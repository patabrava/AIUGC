from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import io
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.core.errors import StateTransitionError


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")


SCRIPT = " ".join(
    (
        "Wenn jede Treppe plötzlich zum Hindernis wird, verliert dein Alltag schnell an Freiheit, Ruhe und Spontaneität.",
        "Mit einem passenden Treppenlift bewegst du dich zuhause wieder sicher, selbstständig und ohne tägliche Umwege weiter.",
        "Unsere Beratung betrachtet deine Wohnsituation genau und erklärt verständlich, welche Lösung wirklich zu deinem Leben passt.",
        "Dabei bleiben wichtige Details wie Platzbedarf, Bedienung, Komfort und Finanzierung von Anfang an transparent für dich.",
        "Du erhältst keine pauschale Empfehlung, sondern eine ehrliche Einschätzung, die deine persönlichen Prioritäten konsequent vollständig berücksichtigt.",
        "So wird aus einer belastenden Barriere wieder ein Zuhause, in dem du dich selbstverständlich bewegen kannst.",
        "Vereinbare jetzt dein kostenloses Gespräch und finde gemeinsam mit unserem Team den nächsten Schritt für dich.",
    )
)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (90, 160), (120, 90, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeStorage:
    def __init__(self, master: bytes):
        self.master = master
        self.objects = {
            "https://storage/front.png": b"front-reference",
            "https://storage/three-quarter.png": b"three-quarter-reference",
            "https://storage/location.png": b"location-reference",
            "https://storage/master.png": master,
        }
        self.download_calls = []
        self.upload_calls = []

    def download_video(self, *, video_url: str, correlation_id: str):
        self.download_calls.append((video_url, correlation_id))
        return self.objects[video_url]

    def upload_image(self, *, image_bytes: bytes, file_name: str, correlation_id: str, content_type: str):
        self.upload_calls.append(
            {
                "image_bytes": image_bytes,
                "file_name": file_name,
                "correlation_id": correlation_id,
                "content_type": content_type,
            }
        )
        url = f"https://storage/generated/{file_name}"
        self.objects[url] = image_bytes
        return {
            "url": url,
            "storage_key": f"generated/{file_name}",
            "size": len(image_bytes),
            "file_type": content_type,
        }


def _install_repository(monkeypatch):
    from app.features.semantic_videos import handlers

    master = _png_bytes()
    master_hash = sha256(master).hexdigest()
    state = {"run": None, "takes": [], "approvals": []}
    context = {
        "post": {
            "id": "post-1",
            "batch_id": "batch-1",
            "topic_rotation": SCRIPT,
            "seed_data": {"script": SCRIPT, "script_review_status": "approved"},
        },
        "batch": {
            "id": "batch-1",
            "creation_mode": "semantic_ugc",
            "target_duration_seconds": 50,
        },
        "reference": {
            "actor_identity_id": "actor-1",
            "actor": {"name": "AYRA Actor", "character_description": "Immutable actor description."},
            "actor_references": [
                {
                    "role": "actor_front",
                    "storage_uri": "https://storage/front.png",
                    "mime_type": "image/png",
                    "sha256": sha256(b"front-reference").hexdigest(),
                },
                {
                    "role": "actor_three_quarter",
                    "storage_uri": "https://storage/three-quarter.png",
                    "mime_type": "image/png",
                    "sha256": sha256(b"three-quarter-reference").hexdigest(),
                },
            ],
            "location_reference": {
                "role": "location",
                "storage_uri": "https://storage/location.png",
                "mime_type": "image/png",
                "sha256": sha256(b"location-reference").hexdigest(),
            },
            "master": {
                "storage_uri": "https://storage/master.png",
                "mime_type": "image/png",
                "byte_length": len(master),
                "sha256": master_hash,
            },
        },
    }

    state["context"] = context

    def get_run_by_post(_post_id):
        return deepcopy(state["run"])

    def create_run(payload):
        state["run"] = {**deepcopy(payload), "id": "run-1", "revision": 0}
        return deepcopy(state["run"])

    def update_run(run_id, *, expected_revision, updates):
        assert run_id == "run-1"
        assert state["run"]["revision"] == expected_revision
        state["run"].update(deepcopy(updates))
        state["run"]["revision"] += 1
        return deepcopy(state["run"])

    def persist_semantic_video_plan(run_id, *, expected_revision, run_updates, takes):
        assert run_id == "run-1"
        assert state["run"]["revision"] == expected_revision
        state["run"].update(deepcopy(run_updates))
        state["run"]["revision"] += 1
        state["takes"] = [
            {**deepcopy(take), "id": f"take-{index + 1}", "run_id": run_id}
            for index, take in enumerate(takes)
        ]
        return deepcopy(state["run"]), deepcopy(state["takes"])

    def append_approval(payload):
        row = {**deepcopy(payload), "id": f"approval-{len(state['approvals']) + 1}"}
        state["approvals"].append(row)
        return deepcopy(row)

    def append_attempts(run_id, takes):
        appended = []
        for take in takes:
            row = {**deepcopy(take), "id": f"take-{len(state['takes']) + 1}", "run_id": run_id}
            state["takes"].append(row)
            appended.append(row)
        return deepcopy(appended)

    def cancel_pending_takes(run_id):
        changed = []
        for take in state["takes"]:
            if take["run_id"] == run_id and take["submission_state"] not in {"completed", "failed", "qa_failed", "cancelled"}:
                take["submission_state"] = "cancelled"
                changed.append(deepcopy(take))
        return changed

    monkeypatch.setattr(handlers, "load_semantic_video_context", lambda post_id: deepcopy(state["context"]))
    monkeypatch.setattr(handlers, "get_run_by_post", get_run_by_post)
    monkeypatch.setattr(handlers, "create_run", create_run)
    monkeypatch.setattr(handlers, "update_run", update_run)
    monkeypatch.setattr(handlers, "persist_semantic_video_plan", persist_semantic_video_plan)
    monkeypatch.setattr(handlers, "append_approval", append_approval)
    monkeypatch.setattr(handlers, "append_attempts", append_attempts, raising=False)
    monkeypatch.setattr(handlers, "cancel_pending_takes", cancel_pending_takes, raising=False)
    monkeypatch.setattr(handlers, "list_attempts", lambda run_id: deepcopy(state["takes"]))
    monkeypatch.setattr(handlers, "list_approvals", lambda run_id: deepcopy(state["approvals"]))
    fake_storage = _FakeStorage(master)
    monkeypatch.setattr(handlers, "get_storage_client", lambda: fake_storage)
    return handlers, state, fake_storage


def _seed_awaiting_paid_run(state, *, revision=0):
    state["run"] = {
        "id": "run-1",
        "post_id": "post-1",
        "batch_id": "batch-1",
        "revision": revision,
        "stage": "awaiting_paid_approval",
    }


def _create_plan_from_unenriched_candidate_flow(monkeypatch, client, handlers, state, storage):
    state["context"]["reference"].pop("master")
    for reference in [
        *state["context"]["reference"]["actor_references"],
        state["context"]["reference"]["location_reference"],
    ]:
        reference.pop("sha256", None)
        reference.pop("byte_length", None)

    monkeypatch.setattr(
        handlers,
        "generate_shot_frame_candidates",
        lambda **_kwargs: SimpleNamespace(
            prompt_writer_output="Complete prompt writer result.",
            composition_prompt="Complete composition prompt.",
            candidates=[
                SimpleNamespace(
                    index=index,
                    image_bytes=storage.master,
                    mime_type="image/png",
                    provider_model="gemini-3.1-flash-image",
                )
                for index in range(1, 4)
            ],
        ),
        raising=False,
    )
    candidate_response = client.post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3},
    )
    assert candidate_response.status_code == 200, candidate_response.text
    assert all(
        "sha256" in reference and "byte_length" in reference
        for reference in [
            *state["run"]["reference_snapshot"]["actor_references"],
            state["run"]["reference_snapshot"]["location_reference"],
        ]
    )

    master_response = client.post(
        "/semantic-videos/posts/post-1/master-approve",
        json={"candidate_index": 1, "expected_revision": 0},
    )
    assert master_response.status_code == 200, master_response.text

    plan_response = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 1},
    )
    assert plan_response.status_code == 200, plan_response.text
    return plan_response.json()["data"]


def test_initial_approval_uses_persisted_enriched_reference_after_unenriched_candidate_flow(monkeypatch):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    plan = _create_plan_from_unenriched_candidate_flow(
        monkeypatch,
        client,
        handlers,
        state,
        storage,
    )

    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 2},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["stage"] == "generating"


@pytest.mark.parametrize(
    "mutation",
    ["actor_identity", "ordered_source_uris", "script", "duration", "master_bytes"],
)
def test_initial_approval_rejects_every_fresh_source_mutation(monkeypatch, mutation):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    plan = _create_plan_from_unenriched_candidate_flow(
        monkeypatch,
        client,
        handlers,
        state,
        storage,
    )

    if mutation == "actor_identity":
        state["context"]["reference"]["actor_identity_id"] = "actor-2"
    elif mutation == "ordered_source_uris":
        actor_references = state["context"]["reference"]["actor_references"]
        actor_references[0]["storage_uri"], actor_references[1]["storage_uri"] = (
            actor_references[1]["storage_uri"],
            actor_references[0]["storage_uri"],
        )
    elif mutation == "script":
        state["context"]["post"]["seed_data"]["script"] = SCRIPT.replace(
            "transparent",
            "nachvollziehbar",
        )
    elif mutation == "duration":
        state["context"]["batch"]["target_duration_seconds"] = 51
    elif mutation == "master_bytes":
        master_uri = state["run"]["master_snapshot"]["storage_uri"]
        storage.objects[master_uri] = b"mutated-master-bytes"

    approval_count = len(state["approvals"])
    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 2},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "state_transition_error"
    assert len(state["approvals"]) == approval_count
    assert state["run"]["stage"] == "awaiting_paid_approval"


def test_free_plan_endpoint_persists_seven_take_plan_without_provider_calls(monkeypatch):
    _handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    _seed_awaiting_paid_run(state)
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["take_count"] == 7
    assert payload["billable_provider_seconds"] == 56
    assert payload["quota_units"] == 7
    assert payload["estimated_cost_usd"] == "22.40"
    assert payload["plan_hash"] == state["run"]["plan_hash"]
    assert len(state["takes"]) == 7
    assert len(storage.download_calls) == 1


@pytest.mark.parametrize(
    "stage",
    [
        "awaiting_script_approval",
        "awaiting_reference_approval",
        "generating",
        "transcript_qa",
        "identity_qa",
        "voice_qa",
        "retry_approval_required",
        "acoustic_qa",
        "composing",
        "uploading",
        "completed",
        "failed",
    ],
)
def test_plan_endpoint_rejects_every_stage_except_awaiting_paid_approval_without_mutation(
    monkeypatch,
    stage,
):
    _handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["run"] = {"id": "run-1", "post_id": "post-1", "revision": 4, "stage": stage}
    original_run = deepcopy(state["run"])
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 4},
    )

    assert response.status_code == 409, response.text
    assert storage.download_calls == []
    assert state["run"] == original_run
    assert state["takes"] == []


def test_plan_endpoint_requires_an_existing_awaiting_paid_approval_run_without_mutation(monkeypatch):
    _handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    )

    assert response.status_code == 404, response.text
    assert storage.download_calls == []
    assert state["run"] is None
    assert state["takes"] == []


def test_plan_endpoint_persists_run_and_takes_through_one_atomic_query_call(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    state["run"] = {
        "id": "run-1",
        "post_id": "post-1",
        "batch_id": "batch-1",
        "revision": 4,
        "stage": "awaiting_paid_approval",
    }
    atomic_calls = []
    legacy_calls = []

    def persist_plan(run_id, *, expected_revision, run_updates, takes):
        atomic_calls.append(
            {
                "run_id": run_id,
                "expected_revision": expected_revision,
                "run_updates": deepcopy(run_updates),
                "takes": deepcopy(takes),
            }
        )
        state["run"].update(deepcopy(run_updates))
        state["run"]["revision"] = expected_revision + 1
        state["takes"] = [
            {**deepcopy(take), "id": f"take-{index + 1}", "run_id": run_id}
            for index, take in enumerate(takes)
        ]
        return deepcopy(state["run"]), deepcopy(state["takes"])

    def legacy_update(*args, **kwargs):
        legacy_calls.append(("update", args, kwargs))
        state["run"].update(deepcopy(kwargs["updates"]))
        state["run"]["revision"] = kwargs["expected_revision"] + 1
        return deepcopy(state["run"])

    def legacy_replace(run_id, takes):
        legacy_calls.append(("replace", run_id, deepcopy(takes)))
        state["takes"] = [
            {**deepcopy(take), "id": f"take-{index + 1}", "run_id": run_id}
            for index, take in enumerate(takes)
        ]
        return deepcopy(state["takes"])

    monkeypatch.setattr(handlers, "persist_semantic_video_plan", persist_plan, raising=False)
    monkeypatch.setattr(handlers, "update_run", legacy_update)
    monkeypatch.setattr(handlers, "replace_initial_takes", legacy_replace, raising=False)
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 4},
    )

    assert response.status_code == 200, response.text
    assert len(atomic_calls) == 1
    assert atomic_calls[0]["run_id"] == "run-1"
    assert atomic_calls[0]["expected_revision"] == 4
    assert len(atomic_calls[0]["takes"]) == 7
    assert legacy_calls == []


def test_progress_endpoint_reports_persisted_generated_and_verified_counts(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    assert client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).status_code == 200
    state["takes"][0]["submission_state"] = "completed"
    state["takes"][0]["transcript_result"] = {"passed": True}

    response = client.get("/semantic-videos/posts/post-1/progress")

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["stage"] == "awaiting_paid_approval"
    assert payload["total_takes"] == 7
    assert payload["generated_takes"] == 1
    assert payload["verified_takes"] == 1


def test_initial_approval_appends_exact_hash_and_moves_run_to_generating(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]

    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 1, "reason": "Approved test plan"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["stage"] == "generating"
    assert state["run"]["stage"] == "generating"
    assert len(state["approvals"]) == 1
    approval = state["approvals"][0]
    assert approval["approval_type"] == "initial_plan"
    assert approval["contract_hash"] == plan["plan_hash"]
    assert approval["approved_take_indexes"] == list(range(7))
    assert approval["quota_units"] == 7


def test_candidate_endpoint_uses_exact_ordered_references_and_persists_all_bytes(monkeypatch):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            prompt_writer_output="Complete prompt writer result.",
            composition_prompt="Complete composition prompt.",
            candidates=[
                SimpleNamespace(
                    index=index,
                    image_bytes=f"candidate-{index}".encode(),
                    mime_type="image/png",
                    provider_model="gemini-3.1-flash-image",
                )
                for index in range(1, 4)
            ],
        )

    monkeypatch.setattr(handlers, "generate_shot_frame_candidates", fake_generate, raising=False)
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3},
    )

    assert response.status_code == 200, response.text
    assert [reference.role for reference in captured["actor_references"]] == [
        "actor_front",
        "actor_three_quarter",
    ]
    assert [reference.image_bytes for reference in captured["actor_references"]] == [
        b"front-reference",
        b"three-quarter-reference",
    ]
    assert captured["location_reference"].role == "location"
    assert captured["location_reference"].image_bytes == b"location-reference"
    assert len(storage.upload_calls) == 3
    candidates = response.json()["data"]["candidates"]
    assert [candidate["sha256"] for candidate in candidates] == [
        sha256(f"candidate-{index}".encode()).hexdigest() for index in range(1, 4)
    ]
    assert state["run"]["stage"] == "awaiting_reference_approval"
    assert state["run"]["master_snapshot"]["candidates"] == candidates


def test_candidate_endpoint_rejects_missing_reference_readiness_before_provider_call(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"]["actor_references"] = state["context"]["reference"]["actor_references"][:1]
    calls = []
    monkeypatch.setattr(handlers, "generate_shot_frame_candidates", lambda **kwargs: calls.append(kwargs), raising=False)

    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3},
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert calls == []


@pytest.mark.parametrize(
    "stage",
    [
        "awaiting_script_approval",
        "awaiting_paid_approval",
        "generating",
        "transcript_qa",
        "identity_qa",
        "voice_qa",
        "retry_approval_required",
        "acoustic_qa",
        "composing",
        "uploading",
        "completed",
        "failed",
    ],
)
def test_candidate_endpoint_rejects_every_unintended_existing_stage_before_external_calls(
    monkeypatch,
    stage,
):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    state["run"] = {"id": "run-1", "post_id": "post-1", "revision": 4, "stage": stage}
    original_run = deepcopy(state["run"])
    provider_calls = []

    def fake_generate(**kwargs):
        provider_calls.append(kwargs)
        return SimpleNamespace(
            prompt_writer_output="Complete prompt writer result.",
            composition_prompt="Complete composition prompt.",
            candidates=[
                SimpleNamespace(
                    index=index,
                    image_bytes=f"candidate-{index}".encode(),
                    mime_type="image/png",
                    provider_model="gemini-3.1-flash-image",
                )
                for index in range(1, 4)
            ],
        )

    monkeypatch.setattr(handlers, "generate_shot_frame_candidates", fake_generate, raising=False)
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3, "expected_revision": 4},
    )

    assert response.status_code == 409, response.text
    assert provider_calls == []
    assert storage.download_calls == []
    assert storage.upload_calls == []
    assert state["run"] == original_run


@pytest.mark.parametrize("supplied_revision", [None, 3])
def test_candidate_endpoint_rejects_missing_or_stale_existing_revision_before_external_calls(
    monkeypatch,
    supplied_revision,
):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    state["run"] = {
        "id": "run-1",
        "post_id": "post-1",
        "revision": 4,
        "stage": "awaiting_reference_approval",
    }
    original_run = deepcopy(state["run"])
    provider_calls = []
    monkeypatch.setattr(
        handlers,
        "generate_shot_frame_candidates",
        lambda **kwargs: provider_calls.append(kwargs)
        or SimpleNamespace(
            prompt_writer_output="Complete prompt writer result.",
            composition_prompt="Complete composition prompt.",
            candidates=[
                SimpleNamespace(
                    index=index,
                    image_bytes=f"candidate-{index}".encode(),
                    mime_type="image/png",
                    provider_model="gemini-3.1-flash-image",
                )
                for index in range(1, 4)
            ],
        ),
        raising=False,
    )
    payload = {"candidate_count": 3}
    if supplied_revision is not None:
        payload["expected_revision"] = supplied_revision

    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/candidates",
        json=payload,
    )

    assert response.status_code == 409, response.text
    assert provider_calls == []
    assert storage.download_calls == []
    assert storage.upload_calls == []
    assert state["run"] == original_run


def test_master_approval_is_append_only_and_snapshots_selected_candidate(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    monkeypatch.setattr(
        handlers,
        "generate_shot_frame_candidates",
        lambda **_kwargs: SimpleNamespace(
            prompt_writer_output="Complete prompt writer result.",
            composition_prompt="Complete composition prompt.",
            candidates=[
                SimpleNamespace(index=index, image_bytes=f"candidate-{index}".encode(), mime_type="image/png", provider_model="gemini-3.1-flash-image")
                for index in range(1, 4)
            ],
        ),
        raising=False,
    )
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/semantic-videos/posts/post-1/candidates", json={"candidate_count": 3}).status_code == 200

    response = client.post(
        "/semantic-videos/posts/post-1/master-approve",
        json={"candidate_index": 2, "expected_revision": 0, "reason": "Best identity match"},
    )

    assert response.status_code == 200, response.text
    assert state["run"]["master_snapshot"]["approved_candidate_index"] == 2
    assert state["run"]["master_hash"] == sha256(b"candidate-2").hexdigest()
    assert state["run"]["stage"] == "awaiting_paid_approval"
    assert state["approvals"][0]["approval_type"] == "reference"
    assert state["approvals"][0]["contract_hash"] == sha256(b"candidate-2").hexdigest()


def test_initial_approval_rejects_stale_hash_and_changed_script(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]
    stale = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": "0" * 64, "expected_revision": 1},
    )
    assert stale.status_code == 409, stale.text
    assert state["approvals"] == []

    state["context"]["post"]["seed_data"]["script"] = SCRIPT.replace("transparent", "nachvollziehbar")
    changed = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 1},
    )
    assert changed.status_code == 409, changed.text
    assert state["approvals"] == []


def test_retry_approval_targets_only_failed_indexes_and_incremental_cost(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]
    assert client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 1},
    ).status_code == 200
    state["run"]["stage"] = "retry_approval_required"
    for take in state["takes"]:
        take["submission_state"] = "qa_failed" if take["take_index"] in {1, 4} else "completed"

    response = client.post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 2,
            "failed_take_indexes": [1, 4],
            "reason": "Retry exact QA failures",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["approved_take_indexes"] == [1, 4]
    assert payload["approved_provider_seconds"] == 16
    assert payload["quota_units"] == 2
    assert payload["estimated_cost_usd"] == "6.40"
    assert state["approvals"][-1]["approval_type"] == "retry"
    assert [(take["take_index"], take["attempt"]) for take in state["takes"] if take["attempt"] == 2] == [(1, 2), (4, 2)]

    rejected = client.post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 3,
            "failed_take_indexes": [0],
        },
    )
    assert rejected.status_code == 409, rejected.text


def test_cancel_only_accepts_nonterminal_run_and_cancels_pending_takes(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    client.post("/semantic-videos/posts/post-1/plan", json={"expected_revision": 0})
    response = client.post(
        "/semantic-videos/posts/post-1/cancel",
        json={"expected_revision": 1, "reason": "Operator cancelled"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["stage"] == "failed"
    assert state["run"]["failure_envelope"]["code"] == "cancelled"
    assert {take["submission_state"] for take in state["takes"]} == {"cancelled"}

    second = client.post(
        "/semantic-videos/posts/post-1/cancel",
        json={"expected_revision": 2, "reason": "Again"},
    )
    assert second.status_code == 409, second.text


class _QueryResponse:
    def __init__(self, data):
        self.data = deepcopy(data)


class _RecordingQuery:
    def __init__(self, client, call):
        self.client = client
        self.call = call

    def select(self, fields):
        self.call.update(operation="select", fields=fields)
        return self

    def update(self, payload):
        self.call.update(operation="update", payload=deepcopy(payload))
        return self

    def eq(self, key, value):
        self.call.setdefault("filters", []).append(("eq", key, value))
        return self

    def limit(self, value):
        self.call["limit"] = value
        return self

    def execute(self):
        return _QueryResponse(self.client.responses.pop(0))


class _RecordingClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, table_name):
        call = {"kind": "table", "table": table_name}
        self.calls.append(call)
        return _RecordingQuery(self, call)

    def rpc(self, function_name, payload):
        call = {"kind": "rpc", "function": function_name, "payload": deepcopy(payload)}
        self.calls.append(call)
        return _RecordingQuery(self, call)


def test_persist_semantic_video_plan_query_uses_one_rpc_and_returns_exact_contract():
    from app.features.semantic_videos.queries import persist_semantic_video_plan

    run_update = {"stage": "awaiting_paid_approval", "plan_hash": "a" * 64}
    initial_takes = [{"take_index": 0, "attempt": 1, "request_hash": "b" * 64}]
    persisted_run = {
        "id": "00000000-0000-0000-0000-000000000021",
        "revision": 5,
        "stage": "awaiting_paid_approval",
        "plan_hash": "a" * 64,
    }
    persisted_takes = [
        {
            "id": "00000000-0000-0000-0000-000000000031",
            "run_id": persisted_run["id"],
            **initial_takes[0],
        }
    ]
    client = _RecordingClient({"run": persisted_run, "takes": persisted_takes})

    run, takes = persist_semantic_video_plan(
        persisted_run["id"],
        expected_revision=4,
        run_updates=run_update,
        takes=initial_takes,
        client=client,
    )

    assert run == persisted_run
    assert takes == persisted_takes
    assert client.calls == [
        {
            "kind": "rpc",
            "function": "persist_semantic_video_plan",
            "payload": {
                "p_run_id": persisted_run["id"],
                "p_expected_revision": 4,
                "p_run_update": run_update,
                "p_initial_takes": initial_takes,
            },
        }
    ]


def test_query_helpers_persist_intent_operation_and_qa_with_affected_row_validation():
    from app.features.semantic_videos.queries import (
        persist_accepted_operation,
        persist_submission_intent,
        persist_take_qa_artifacts,
    )

    client = _RecordingClient(
        [{"id": "take-1", "submission_state": "intent_persisted"}],
        [{"id": "take-1", "submission_state": "submitted"}],
        [{"id": "take-1", "submission_state": "completed"}],
    )
    persist_submission_intent(
        "take-1",
        expected_state="reserved",
        request_hash="a" * 64,
        intent_at="2026-07-13T10:00:00Z",
        client=client,
    )
    persist_accepted_operation(
        "take-1",
        expected_state="intent_persisted",
        operation_id="operations/accepted-1",
        provider_model="veo-3.1-generate-001",
        client=client,
    )
    persist_take_qa_artifacts(
        "take-1",
        expected_state="submitted",
        submission_state="completed",
        raw_artifact_uri="https://storage/raw.mp4",
        raw_artifact_sha256="b" * 64,
        transcript_result={"passed": True},
        identity_qa_result={"passed": True},
        client=client,
    )

    assert client.calls[0]["payload"]["submission_state"] == "intent_persisted"
    assert ("eq", "request_hash", "a" * 64) in client.calls[0]["filters"]
    assert ("eq", "submission_state", "reserved") in client.calls[0]["filters"]
    assert client.calls[1]["payload"] == {
        "submission_state": "submitted",
        "operation_id": "operations/accepted-1",
        "provider_model": "veo-3.1-generate-001",
    }
    assert client.calls[2]["payload"]["raw_artifact_sha256"] == "b" * 64

    losing_client = _RecordingClient([])
    with pytest.raises(StateTransitionError, match="optimistic"):
        persist_submission_intent(
            "take-1",
            expected_state="reserved",
            request_hash="a" * 64,
            client=losing_client,
        )


def test_query_helpers_cover_get_lease_release_completion_and_failure():
    from app.features.semantic_videos.queries import (
        acquire_run_lease,
        complete_run,
        fail_run,
        get_run,
        release_run_lease,
    )

    client = _RecordingClient(
        [{"id": "run-1", "revision": 2}],
        [{"id": "run-1", "lease_owner": "worker-1", "revision": 3}],
        [{"id": "run-1", "lease_owner": None, "revision": 4}],
        [{"id": "run-1", "stage": "completed", "revision": 5}],
        [{"id": "run-2", "stage": "failed", "revision": 8}],
    )
    assert get_run("run-1", client=client)["revision"] == 2
    claimed = acquire_run_lease(worker_id="worker-1", lease_seconds=45, client=client)
    assert claimed["lease_owner"] == "worker-1"
    released = release_run_lease(
        "run-1",
        worker_id="worker-1",
        expected_revision=3,
        client=client,
    )
    assert released["revision"] == 4
    completed = complete_run(
        "run-1",
        expected_revision=4,
        final_video_uri="https://storage/final.mp4",
        final_video_sha256="c" * 64,
        final_caption_uri="https://storage/final.vtt",
        final_caption_sha256="d" * 64,
        client=client,
    )
    assert completed["stage"] == "completed"
    failed = fail_run(
        "run-2",
        expected_revision=7,
        failure_envelope={"code": "qa_failed", "message": "Identity mismatch"},
        client=client,
    )
    assert failed["stage"] == "failed"

    assert client.calls[1] == {
        "kind": "rpc",
        "function": "claim_semantic_video_run",
        "payload": {"worker_id": "worker-1", "lease_seconds": 45},
    }
    assert ("eq", "lease_owner", "worker-1") in client.calls[2]["filters"]
    assert client.calls[3]["payload"]["stage"] == "completed"
    assert client.calls[4]["payload"]["failure_envelope"]["code"] == "qa_failed"
