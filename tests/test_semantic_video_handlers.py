from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import io
import os
from types import SimpleNamespace
from threading import Barrier, Lock

from fastapi.testclient import TestClient
from PIL import Image
from postgrest.exceptions import APIError
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


def test_retry_guidance_names_a_missing_first_word_for_paid_regeneration():
    from app.features.semantic_videos.handlers import _retry_guidance_text

    guidance = _retry_guidance_text(
        {
            "guidance": "Regenerate only the failed semantic beat and correct transcript QA.",
            "qa_failure": {"stage": "transcript_qa"},
            "pipeline_manifest": {
                "takes": [
                    {
                        "transcript_qa": {
                            "passed": False,
                            "expected_words": ["kopfsteinpflaster", "zwingt"],
                            "actual_words": ["steinpflaster", "zwingt"],
                            "failure_reasons": ["missing_first_word"],
                        }
                    }
                ]
            },
        }
    )

    assert "Start with the complete first word 'kopfsteinpflaster'" in guidance
    assert "Do not omit or clip its opening syllable" in guidance


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
    state = {
        "run": None,
        "takes": [],
        "approvals": [],
        "candidate_reservation": None,
        "reservation_lock": Lock(),
    }
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
            "scene_description": "Bright actor-free living room beside a window.",
            "wardrobe_description": "Blue cotton blouse with a simple round neckline.",
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

    def reserve_candidate_generation(
        post_id,
        *,
        expected_revision,
        run_create,
        reservation_owner,
        reservation_token,
        reservation_seconds,
    ):
        assert post_id == "post-1"
        assert reservation_owner
        assert reservation_seconds > 0
        with state["reservation_lock"]:
            if state["candidate_reservation"] is not None:
                raise StateTransitionError("Semantic video candidate reservation is active.")
            if state["run"] is None:
                if expected_revision is not None:
                    raise StateTransitionError("Semantic video candidate revision conflict.")
                state["run"] = {**deepcopy(run_create), "id": "run-1", "revision": 0}
            else:
                if state["run"]["revision"] != expected_revision:
                    raise StateTransitionError("Semantic video candidate revision conflict.")
                state["run"]["revision"] += 1
            state["candidate_reservation"] = reservation_token
            state["run"].update(
                {
                    "candidate_reservation_owner": reservation_owner,
                    "candidate_reservation_token": reservation_token,
                }
            )
            return deepcopy(state["run"])

    def finalize_candidate_generation(
        run_id,
        *,
        reserved_revision,
        reservation_token,
        run_updates,
    ):
        assert run_id == "run-1"
        with state["reservation_lock"]:
            assert state["candidate_reservation"] == reservation_token
            assert state["run"]["revision"] == reserved_revision
            state["run"].update(deepcopy(run_updates))
            return deepcopy(state["run"])

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

    def approve_master_transition(
        run_id,
        *,
        expected_revision,
        candidate_index,
        approved_by,
        reason,
    ):
        if state["run"]["revision"] != expected_revision:
            raise StateTransitionError("Semantic video master approval revision conflict.")
        candidates = state["run"]["master_snapshot"].get("candidates") or []
        selected = next(
            (deepcopy(row) for row in candidates if int(row["index"]) == candidate_index),
            None,
        )
        if selected is None:
            raise StateTransitionError("Semantic video master candidate conflict.")
        approved_snapshot = {
            **selected,
            "candidates": deepcopy(candidates),
            "prompt_writer_system_prompt": state["run"]["master_snapshot"].get(
                "prompt_writer_system_prompt"
            ),
            "prompt_writer_system_prompt_sha256": state["run"]["master_snapshot"].get(
                "prompt_writer_system_prompt_sha256"
            ),
            "prompt_writer_output": state["run"]["master_snapshot"].get(
                "prompt_writer_output"
            ),
            "composition_prompt": state["run"]["master_snapshot"].get(
                "composition_prompt"
            ),
            "approved_candidate_index": candidate_index,
            "approved_by": approved_by,
        }
        approval = append_approval(
            {
                "run_id": run_id,
                "approval_type": "reference",
                "run_revision": expected_revision,
                "contract_hash": selected["sha256"],
                "approved_take_indexes": [],
                "approved_provider_seconds": 0,
                "quota_units": 0,
                "estimated_cost_usd": "0.00",
                "approved_by": approved_by,
                "reason": reason,
            }
        )
        updated = update_run(
            run_id,
            expected_revision=expected_revision,
            updates={
                "master_snapshot": approved_snapshot,
                "master_hash": selected["sha256"],
                "stage": "awaiting_paid_approval",
                "plan_snapshot": None,
                "plan_hash": None,
                "estimated_cost_usd": None,
                "failure_envelope": None,
            },
        )
        return updated, approval

    def approve_initial_plan_transition(
        run_id,
        *,
        expected_revision,
        plan_hash,
        approved_by,
        reason,
    ):
        if (
            state["run"]["revision"] != expected_revision
            or state["run"]["stage"] != "awaiting_paid_approval"
            or state["run"].get("plan_hash") != plan_hash
        ):
            raise StateTransitionError("Semantic video initial approval conflict.")
        initial_takes = [take for take in state["takes"] if int(take.get("attempt") or 1) == 1]
        plan = state["run"]["plan_snapshot"]
        approval = append_approval(
            {
                "run_id": run_id,
                "approval_type": "initial_plan",
                "run_revision": expected_revision,
                "contract_hash": plan_hash,
                "approved_take_indexes": [int(take["take_index"]) for take in initial_takes],
                "approved_provider_seconds": int(plan["billable_provider_seconds"]),
                "quota_units": int(plan["quota_units"]),
                "estimated_cost_usd": str(plan["estimated_cost_usd"]),
                "approved_by": approved_by,
                "reason": reason,
            }
        )
        updated = update_run(
            run_id,
            expected_revision=expected_revision,
            updates={"stage": "generating", "failure_envelope": None},
        )
        return updated, approval

    def approve_retry_transition(
        run_id,
        *,
        expected_revision,
        plan_hash,
        retry_takes,
        contract_hash,
        approved_by,
        reason,
    ):
        if (
            state["run"]["revision"] != expected_revision
            or state["run"]["stage"] != "retry_approval_required"
            or state["run"].get("plan_hash") != plan_hash
        ):
            raise StateTransitionError("Semantic video retry approval conflict.")
        provider_seconds = sum(int(take["provider_duration_seconds"]) for take in retry_takes)
        price = Decimal(str(state["run"]["plan_snapshot"]["price_per_provider_second_usd"]))
        cost = format((price * Decimal(provider_seconds)).quantize(Decimal("0.01")), ".2f")
        approval = append_approval(
            {
                "run_id": run_id,
                "approval_type": "retry",
                "run_revision": expected_revision,
                "contract_hash": contract_hash,
                "approved_take_indexes": [int(take["take_index"]) for take in retry_takes],
                "approved_provider_seconds": provider_seconds,
                "quota_units": len(retry_takes),
                "estimated_cost_usd": cost,
                "approved_by": approved_by,
                "reason": reason,
            }
        )
        persisted = append_attempts(run_id, retry_takes)
        updated = update_run(
            run_id,
            expected_revision=expected_revision,
            updates={"stage": "generating", "failure_envelope": None},
        )
        return updated, approval, persisted

    def cancel_run_transition(
        run_id,
        *,
        expected_revision,
        cancelled_by,
        reason,
        correlation_id,
    ):
        if state["run"]["revision"] != expected_revision:
            raise StateTransitionError("Semantic video cancellation revision conflict.")
        latest = {}
        for take in state["takes"]:
            index = int(take["take_index"])
            if index not in latest or int(take.get("attempt") or 1) > int(latest[index].get("attempt") or 1):
                latest[index] = take
        if any(
            take["submission_state"] in {"intent_persisted", "submitted", "submission_unknown"}
            for take in latest.values()
        ):
            raise StateTransitionError("Semantic video cancellation has paid work in flight.")
        cancelled = 0
        for take in state["takes"]:
            if take["submission_state"] in {"planned", "reserved"}:
                take["submission_state"] = "cancelled"
                cancelled += 1
        updated = update_run(
            run_id,
            expected_revision=expected_revision,
            updates={
                "stage": "failed",
                "failure_envelope": {
                    "code": "cancelled",
                    "message": reason,
                    "cancelled_by": cancelled_by,
                    "correlation_id": correlation_id,
                },
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
        return updated, cancelled

    monkeypatch.setattr(handlers, "load_semantic_video_context", lambda post_id: deepcopy(state["context"]))
    monkeypatch.setattr(handlers, "get_run_by_post", get_run_by_post)
    monkeypatch.setattr(handlers, "update_run", update_run, raising=False)
    monkeypatch.setattr(handlers, "persist_semantic_video_plan", persist_semantic_video_plan)
    monkeypatch.setattr(
        handlers,
        "reserve_candidate_generation",
        reserve_candidate_generation,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "finalize_candidate_generation",
        finalize_candidate_generation,
        raising=False,
    )
    monkeypatch.setattr(handlers, "append_approval", append_approval, raising=False)
    monkeypatch.setattr(handlers, "append_attempts", append_attempts, raising=False)
    monkeypatch.setattr(handlers, "cancel_pending_takes", cancel_pending_takes, raising=False)
    monkeypatch.setattr(
        handlers,
        "approve_master_transition",
        approve_master_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "approve_initial_plan_transition",
        approve_initial_plan_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "approve_retry_transition",
        approve_retry_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "cancel_run_transition",
        cancel_run_transition,
        raising=False,
    )
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


def test_plan_http_contract_rejects_caller_controlled_price(monkeypatch):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)

    response = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={
            "expected_revision": 0,
            "price_per_provider_second_usd": "0.01",
        },
    )

    assert response.status_code == 422, response.text
    assert state["takes"] == []


def test_plan_uses_only_positive_server_configured_price(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    monkeypatch.setattr(
        handlers,
        "get_settings",
        lambda: SimpleNamespace(semantic_ugc_veo_price_per_provider_second_usd=Decimal("0.73")),
        raising=False,
    )
    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)

    response = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["price_per_provider_second_usd"] == "0.73"


@pytest.mark.parametrize("configured_price", [Decimal("0"), Decimal("-0.01")])
def test_plan_rejects_nonpositive_server_price_before_persistence(
    monkeypatch,
    configured_price,
):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    monkeypatch.setattr(
        handlers,
        "get_settings",
        lambda: SimpleNamespace(
            semantic_ugc_veo_price_per_provider_second_usd=configured_price
        ),
        raising=False,
    )
    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)

    response = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    )

    assert response.status_code == 422, response.text
    assert state["takes"] == []


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
    ("role", "storage_uri", "mutated_bytes"),
    [
        ("actor_front", "https://storage/front.png", b"mutated-front-reference"),
        ("location", "https://storage/location.png", b"mutated-location-reference"),
    ],
    ids=["actor-front-same-uri", "location-same-uri"],
)
def test_initial_approval_rejects_same_uri_reference_byte_replacement(
    monkeypatch,
    role,
    storage_uri,
    mutated_bytes,
):
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
    storage.objects[storage_uri] = mutated_bytes
    storage.download_calls.clear()
    approval_count = len(state["approvals"])

    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 2},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "state_transition_error"
    assert any(call[0] == storage_uri for call in storage.download_calls), role
    assert len(state["approvals"]) == approval_count
    assert state["run"]["stage"] == "awaiting_paid_approval"


@pytest.mark.parametrize(
    "mutation",
    [
        "actor_identity",
        "ordered_source_uris",
        "script",
        "duration",
        "invalid_duration",
        "master_bytes",
    ],
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
    elif mutation == "invalid_duration":
        state["context"]["batch"]["target_duration_seconds"] = 7
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
    monkeypatch.setattr(handlers, "update_run", legacy_update, raising=False)
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


@pytest.mark.parametrize("candidate_count", [1, 2, 4])
def test_candidate_endpoint_requires_exactly_three_candidates_before_provider_call(
    monkeypatch,
    candidate_count,
):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    provider_calls = []
    monkeypatch.setattr(
        handlers,
        "generate_shot_frame_candidates",
        lambda **kwargs: provider_calls.append(kwargs),
        raising=False,
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": candidate_count},
    )

    assert response.status_code == 422, response.text
    assert provider_calls == []


def test_candidate_endpoint_uses_exact_ordered_references_and_persists_all_bytes(monkeypatch):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    state["context"]["reference"]["actor"].pop("character_description", None)
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
    assert "character_description" not in captured
    assert len(storage.upload_calls) == 3
    candidates = response.json()["data"]["candidates"]
    assert [candidate["sha256"] for candidate in candidates] == [
        sha256(f"candidate-{index}".encode()).hexdigest() for index in range(1, 4)
    ]
    assert state["run"]["stage"] == "awaiting_reference_approval"
    assert state["run"]["master_snapshot"]["candidates"] == candidates


def test_candidate_endpoint_returns_the_finalized_persisted_candidate_contract(monkeypatch):
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

    def finalize_candidate_generation(
        run_id,
        *,
        reserved_revision,
        reservation_token,
        run_updates,
    ):
        assert run_id == "run-1"
        assert reserved_revision == state["run"]["revision"]
        assert reservation_token == state["candidate_reservation"]
        state["run"].update(deepcopy(run_updates))
        state["run"]["master_snapshot"]["candidates"][0]["storage_uri"] = (
            "https://storage/canonicalized-candidate-1.png"
        )
        return deepcopy(state["run"])

    monkeypatch.setattr(
        handlers,
        "finalize_candidate_generation",
        finalize_candidate_generation,
        raising=False,
    )
    response = TestClient(app, base_url="http://localhost").post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3},
    )

    assert response.status_code == 200, response.text
    response_candidates = response.json()["data"]["candidates"]
    assert response_candidates == state["run"]["master_snapshot"]["candidates"]
    assert response_candidates[0]["storage_uri"] == (
        "https://storage/canonicalized-candidate-1.png"
    )


def test_failed_candidate_attempt_cannot_trigger_a_second_paid_generation(monkeypatch):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    state["context"]["reference"].pop("master")
    provider_calls = []

    def fail_after_provider_invocation(**kwargs):
        provider_calls.append(kwargs)
        raise RuntimeError("simulated provider result uncertainty")

    monkeypatch.setattr(
        handlers,
        "generate_shot_frame_candidates",
        fail_after_provider_invocation,
        raising=False,
    )
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)

    first = client.post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3},
    )
    second = client.post(
        "/semantic-videos/posts/post-1/candidates",
        json={"candidate_count": 3, "expected_revision": 0},
    )

    assert first.status_code == 500, first.text
    assert second.status_code == 409, second.text
    assert len(provider_calls) == 1
    assert storage.upload_calls == []
    assert state["run"]["candidate_reservation_token"] == state["candidate_reservation"]


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


@pytest.mark.parametrize("flow", ["initial", "refresh"])
def test_candidate_generation_reserves_before_concurrent_provider_effects(monkeypatch, flow):
    handlers, state, storage = _install_repository(monkeypatch)
    from app.main import app

    if flow == "refresh":
        state["run"] = {
            "id": "run-1",
            "post_id": "post-1",
            "revision": 5,
            "stage": "awaiting_reference_approval",
        }
    barrier = Barrier(2)
    provider_calls = []

    def load_context(_post_id):
        barrier.wait(timeout=5)
        return deepcopy(state["context"])

    def generate(**_kwargs):
        provider_calls.append(flow)
        return SimpleNamespace(
            prompt_writer_output="Prompt writer output.",
            composition_prompt="Composition prompt.",
            candidates=[
                SimpleNamespace(
                    index=index,
                    image_bytes=f"{flow}-candidate-{index}".encode(),
                    mime_type="image/png",
                    provider_model="gemini-3.1-flash-image",
                )
                for index in range(1, 4)
            ],
        )

    monkeypatch.setattr(handlers, "load_semantic_video_context", load_context)
    monkeypatch.setattr(handlers, "generate_shot_frame_candidates", generate, raising=False)
    payload = {"candidate_count": 3}
    if flow == "refresh":
        payload["expected_revision"] = 5

    def submit():
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        return client.post("/semantic-videos/posts/post-1/candidates", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: submit(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(provider_calls) == 1
    assert len(storage.upload_calls) == 3


def test_master_approval_is_append_only_and_snapshots_selected_candidate(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app
    from app.features.shot_frames.service import load_raw_camera_system_prompt

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
    system_prompt = load_raw_camera_system_prompt()
    candidate_master = state["run"]["master_snapshot"]
    assert candidate_master["prompt_writer_system_prompt"] == system_prompt
    assert candidate_master["prompt_writer_system_prompt_sha256"] == sha256(
        system_prompt.encode("utf-8")
    ).hexdigest()
    assert candidate_master["prompt_writer_output"] == "Complete prompt writer result."
    assert candidate_master["composition_prompt"] == "Complete composition prompt."

    response = client.post(
        "/semantic-videos/posts/post-1/master-approve",
        json={"candidate_index": 2, "expected_revision": 0, "reason": "Best identity match"},
    )

    assert response.status_code == 200, response.text
    assert state["run"]["master_snapshot"]["approved_candidate_index"] == 2
    assert state["run"]["master_hash"] == sha256(b"candidate-2").hexdigest()
    assert state["run"]["stage"] == "awaiting_paid_approval"
    assert state["run"]["master_snapshot"]["prompt_writer_system_prompt"] == system_prompt
    assert state["run"]["master_snapshot"]["prompt_writer_system_prompt_sha256"] == sha256(
        system_prompt.encode("utf-8")
    ).hexdigest()
    assert state["run"]["master_snapshot"]["prompt_writer_output"] == (
        "Complete prompt writer result."
    )
    assert state["run"]["master_snapshot"]["composition_prompt"] == (
        "Complete composition prompt."
    )
    assert state["approvals"][0]["approval_type"] == "reference"
    assert state["approvals"][0]["contract_hash"] == sha256(b"candidate-2").hexdigest()


def test_master_approval_uses_one_atomic_transition(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    candidate = {
        "index": 2,
        "storage_uri": "https://storage/generated/candidate-2.png",
        "mime_type": "image/png",
        "byte_length": len(b"candidate-2"),
        "sha256": sha256(b"candidate-2").hexdigest(),
        "provider_model": "gemini-3.1-flash-image",
    }
    state["run"] = {
        "id": "run-1",
        "revision": 4,
        "stage": "awaiting_reference_approval",
        "master_snapshot": {"candidates": [candidate]},
    }
    calls = []

    def approve_master_transition(
        run_id,
        *,
        expected_revision,
        candidate_index,
        approved_by,
        reason,
    ):
        calls.append((run_id, expected_revision, candidate_index, approved_by, reason))
        approved = {
            **candidate,
            "candidates": [candidate],
            "approved_candidate_index": candidate_index,
            "approved_by": approved_by,
        }
        state["run"].update(
            {
                "revision": expected_revision + 1,
                "stage": "awaiting_paid_approval",
                "master_snapshot": approved,
                "master_hash": candidate["sha256"],
            }
        )
        approval = {
            "id": "approval-atomic-master",
            "contract_hash": candidate["sha256"],
        }
        state["approvals"].append(approval)
        return deepcopy(state["run"]), deepcopy(approval)

    monkeypatch.setattr(
        handlers,
        "approve_master_transition",
        approve_master_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "append_approval",
        lambda *_args, **_kwargs: pytest.fail("legacy approval insert called"),
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "update_run",
        lambda *_args, **_kwargs: pytest.fail("legacy run update called"),
        raising=False,
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).post(
        "/semantic-videos/posts/post-1/master-approve",
        json={
            "candidate_index": 2,
            "expected_revision": 4,
            "reason": "Best identity match",
        },
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert state["run"]["stage"] == "awaiting_paid_approval"
    assert len(state["approvals"]) == 1


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


@pytest.mark.parametrize(
    "mutation",
    [
        "actor_description",
        "scene_description",
        "wardrobe_description",
        "actor_mime",
        "actor_declared_hash",
        "actor_declared_byte_length",
        "location_mime",
        "location_declared_hash",
        "location_declared_byte_length",
        "master_mime",
        "master_declared_hash",
        "master_declared_byte_length",
    ],
)
def test_initial_approval_rejects_authoritative_reference_metadata_changes(
    monkeypatch,
    mutation,
):
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
    actor = state["context"]["reference"]["actor"]
    actor_row = state["context"]["reference"]["actor_references"][0]
    location = state["context"]["reference"]["location_reference"]
    master = deepcopy(state["run"]["master_snapshot"])
    state["context"]["reference"]["master"] = master

    if mutation == "actor_description":
        actor["character_description"] = "A changed actor description."
    elif mutation == "scene_description":
        state["context"]["reference"]["scene_description"] = "A changed location description."
    elif mutation == "wardrobe_description":
        state["context"]["reference"]["wardrobe_description"] = "A changed wardrobe description."
    elif mutation == "actor_mime":
        actor_row["mime_type"] = "image/jpeg"
    elif mutation == "actor_declared_hash":
        actor_row["sha256"] = "0" * 64
    elif mutation == "actor_declared_byte_length":
        actor_row["byte_length"] = 999
    elif mutation == "location_mime":
        location["mime_type"] = "image/jpeg"
    elif mutation == "location_declared_hash":
        location["sha256"] = "0" * 64
    elif mutation == "location_declared_byte_length":
        location["byte_length"] = 999
    elif mutation == "master_mime":
        master["mime_type"] = "image/jpeg"
    elif mutation == "master_declared_hash":
        master["sha256"] = "0" * 64
    elif mutation == "master_declared_byte_length":
        master["byte_length"] = 999

    approval_count = len(state["approvals"])
    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 2},
    )

    assert response.status_code == 409, response.text
    assert len(state["approvals"]) == approval_count
    assert state["run"]["stage"] == "awaiting_paid_approval"


def test_initial_approval_uses_one_atomic_transition(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]
    calls = []

    def approve_initial_plan_transition(
        run_id,
        *,
        expected_revision,
        plan_hash,
        approved_by,
        reason,
    ):
        calls.append((run_id, expected_revision, plan_hash, approved_by, reason))
        state["run"].update({"revision": expected_revision + 1, "stage": "generating"})
        approval = {
            "id": "approval-atomic-initial",
            "contract_hash": plan_hash,
            "approved_take_indexes": list(range(7)),
            "approved_provider_seconds": 56,
            "quota_units": 7,
            "estimated_cost_usd": "22.40",
        }
        state["approvals"].append(approval)
        return deepcopy(state["run"]), deepcopy(approval)

    monkeypatch.setattr(
        handlers,
        "approve_initial_plan_transition",
        approve_initial_plan_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "append_approval",
        lambda *_args, **_kwargs: pytest.fail("legacy approval insert called"),
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "update_run",
        lambda *_args, **_kwargs: pytest.fail("legacy run update called"),
        raising=False,
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 1},
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert state["run"]["stage"] == "generating"
    assert len(state["approvals"]) == 1


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing_contract_hash", None),
        ("contract_hash", "f" * 64),
        ("approved_take_indexes", [0]),
        ("approved_provider_seconds", 8),
        ("quota_units", 1),
        ("estimated_cost_usd", "3.20"),
    ],
)
def test_initial_approval_rejects_incomplete_or_mismatched_persisted_contract(
    monkeypatch,
    mutation,
    value,
):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]

    def approve_initial_plan_transition(
        run_id,
        *,
        expected_revision,
        plan_hash,
        approved_by,
        reason,
    ):
        del run_id, approved_by, reason
        approval = {
            "id": "approval-untrusted-response",
            "contract_hash": plan_hash,
            "approved_take_indexes": [take["take_index"] for take in plan["takes"]],
            "approved_provider_seconds": plan["billable_provider_seconds"],
            "quota_units": plan["quota_units"],
            "estimated_cost_usd": plan["estimated_cost_usd"],
        }
        if mutation == "missing_contract_hash":
            approval.pop("contract_hash")
        else:
            approval[mutation] = value
        updated = {
            **state["run"],
            "revision": expected_revision + 1,
            "stage": "generating",
        }
        return updated, approval

    monkeypatch.setattr(
        handlers,
        "approve_initial_plan_transition",
        approve_initial_plan_transition,
        raising=False,
    )

    response = client.post(
        "/semantic-videos/posts/post-1/approve",
        json={"plan_hash": plan["plan_hash"], "expected_revision": 1},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "state_transition_error"


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
        if take["take_index"] in {1, 4}:
            take["retry_guidance"] = {
                "guidance": f"Correct QA issue for take {take['take_index']}."
            }

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


def test_retry_approval_recovers_provider_internal_failure_without_qa_guidance(monkeypatch):
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
        take["submission_state"] = "failed" if take["take_index"] == 0 else "completed"
        if take["take_index"] == 0:
            take["retry_guidance"] = None
            take["submission_error"] = {
                "code": "provider_operation_failed",
                "details": {"code": 13, "message": "Internal error. Please try again later."},
            }

    response = client.post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 2,
            "failed_take_indexes": [0],
            "reason": "Retry provider-internal failure",
        },
    )

    assert response.status_code == 200, response.text
    retry = max(
        (take for take in state["takes"] if take["take_index"] == 0),
        key=lambda take: int(take["attempt"]),
    )
    assert retry["attempt"] == 2
    assert retry["retry_guidance"]["source"] == "provider_internal_failure"
    assert "provider operation failed internally" in retry["request_contract"]["prompt"]


def test_retry_approval_revalidates_full_plan_sources(monkeypatch):
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
        take["submission_state"] = "qa_failed" if take["take_index"] == 1 else "completed"
        if take["take_index"] == 1:
            take["retry_guidance"] = {"guidance": "Hold eye contact through the line."}
    state["context"]["reference"]["actor"]["character_description"] = "Changed actor identity."
    approval_count = len(state["approvals"])

    response = client.post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 2,
            "failed_take_indexes": [1],
        },
    )

    assert response.status_code == 409, response.text
    assert len(state["approvals"]) == approval_count
    assert not any(int(take.get("attempt") or 1) > 1 for take in state["takes"])


def test_retry_approval_changes_prompt_seed_and_hashes_once(monkeypatch):
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
        take["submission_state"] = "qa_failed" if take["take_index"] == 1 else "completed"
        if take["take_index"] == 1:
            take["retry_guidance"] = {"guidance": "Hold eye contact through the line."}
    previous = deepcopy(next(take for take in state["takes"] if take["take_index"] == 1))

    response = client.post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 2,
            "failed_take_indexes": [1],
        },
    )

    assert response.status_code == 200, response.text
    retry = max(
        (take for take in state["takes"] if take["take_index"] == 1),
        key=lambda take: int(take["attempt"]),
    )
    guidance = "Hold eye contact through the line."
    retry_prompt = retry["request_contract"]["prompt"]
    assert retry["beat_text"] == previous["beat_text"]
    assert retry_prompt.count(guidance) == 1
    assert retry["seed"] == int(previous["seed"]) + 1000
    assert retry["prompt_hash"] == sha256(retry_prompt.encode("utf-8")).hexdigest()
    assert retry["prompt_hash"] != previous["prompt_hash"]
    assert retry["request_hash"] != previous["request_hash"]


def test_retry_approval_uses_one_atomic_transition(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    plan = client.post(
        "/semantic-videos/posts/post-1/plan",
        json={"expected_revision": 0},
    ).json()["data"]
    state["run"]["stage"] = "retry_approval_required"
    for take in state["takes"]:
        take["submission_state"] = "qa_failed" if take["take_index"] == 1 else "completed"
        if take["take_index"] == 1:
            take["retry_guidance"] = {"guidance": "Hold eye contact through the line."}
    calls = []

    def approve_retry_transition(
        run_id,
        *,
        expected_revision,
        plan_hash,
        retry_takes,
        contract_hash,
        approved_by,
        reason,
    ):
        calls.append((run_id, expected_revision, plan_hash, deepcopy(retry_takes)))
        persisted = [
            {**deepcopy(take), "id": f"retry-{index}", "run_id": run_id}
            for index, take in enumerate(retry_takes, start=1)
        ]
        state["takes"].extend(persisted)
        state["run"].update({"revision": expected_revision + 1, "stage": "generating"})
        approval = {
            "id": "approval-atomic-retry",
            "contract_hash": contract_hash,
            "approved_take_indexes": [take["take_index"] for take in retry_takes],
            "approved_provider_seconds": sum(
                take["provider_duration_seconds"] for take in retry_takes
            ),
            "quota_units": len(retry_takes),
            "estimated_cost_usd": "3.2000",
        }
        state["approvals"].append(approval)
        return deepcopy(state["run"]), deepcopy(approval), deepcopy(persisted)

    monkeypatch.setattr(
        handlers,
        "approve_retry_transition",
        approve_retry_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "append_approval",
        lambda *_args, **_kwargs: pytest.fail("legacy approval insert called"),
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "append_attempts",
        lambda *_args, **_kwargs: pytest.fail("legacy retry insert called"),
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "update_run",
        lambda *_args, **_kwargs: pytest.fail("legacy run update called"),
        raising=False,
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).post(
        "/semantic-videos/posts/post-1/retry-approve",
        json={
            "plan_hash": plan["plan_hash"],
            "expected_revision": 1,
            "failed_take_indexes": [1],
        },
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert state["run"]["stage"] == "generating"
    assert len(state["approvals"]) == 1
    assert response.json()["data"]["estimated_cost_usd"] == "3.2000"


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


@pytest.mark.parametrize(
    "unsafe_state",
    ["intent_persisted", "submitted", "submission_unknown"],
)
def test_cancel_rejects_paid_in_flight_current_take_without_partial_mutation(
    monkeypatch,
    unsafe_state,
):
    _handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    client.post("/semantic-videos/posts/post-1/plan", json={"expected_revision": 0})
    state["takes"][0]["submission_state"] = unsafe_state
    original_takes = deepcopy(state["takes"])
    original_run = deepcopy(state["run"])

    response = client.post(
        "/semantic-videos/posts/post-1/cancel",
        json={"expected_revision": 1, "reason": "Operator cancelled"},
    )

    assert response.status_code == 409, response.text
    assert state["takes"] == original_takes
    assert state["run"] == original_run


def test_cancel_uses_one_atomic_transition(monkeypatch):
    handlers, state, _storage = _install_repository(monkeypatch)
    from app.main import app

    client = TestClient(app, base_url="http://localhost")
    _seed_awaiting_paid_run(state)
    client.post("/semantic-videos/posts/post-1/plan", json={"expected_revision": 0})
    calls = []

    def cancel_run_transition(
        run_id,
        *,
        expected_revision,
        cancelled_by,
        reason,
        correlation_id,
    ):
        calls.append((run_id, expected_revision, cancelled_by, reason, correlation_id))
        cancelled = 0
        for take in state["takes"]:
            if take["submission_state"] in {"planned", "reserved"}:
                take["submission_state"] = "cancelled"
                cancelled += 1
        state["run"].update(
            {
                "revision": expected_revision + 1,
                "stage": "failed",
                "failure_envelope": {
                    "code": "cancelled",
                    "message": reason,
                    "cancelled_by": cancelled_by,
                    "correlation_id": correlation_id,
                },
            }
        )
        return deepcopy(state["run"]), cancelled

    monkeypatch.setattr(
        handlers,
        "cancel_run_transition",
        cancel_run_transition,
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "cancel_pending_takes",
        lambda *_args, **_kwargs: pytest.fail("legacy take cancellation called"),
        raising=False,
    )
    monkeypatch.setattr(
        handlers,
        "update_run",
        lambda *_args, **_kwargs: pytest.fail("legacy run update called"),
        raising=False,
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).post(
        "/semantic-videos/posts/post-1/cancel",
        json={"expected_revision": 1, "reason": "Operator cancelled"},
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert state["run"]["stage"] == "failed"
    assert {take["submission_state"] for take in state["takes"]} == {"cancelled"}


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
        response = self.client.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _QueryResponse(response)


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

    conflict = APIError(
        {
            "code": "40001",
            "details": None,
            "hint": None,
            "message": "semantic_video_conflict: plan revision mismatch",
        }
    )
    with pytest.raises(StateTransitionError, match="plan persistence"):
        persist_semantic_video_plan(
            persisted_run["id"],
            expected_revision=4,
            run_updates=run_update,
            takes=initial_takes,
            client=_RecordingClient(conflict),
        )

    unexpected = APIError(
        {
            "code": "P0001",
            "details": None,
            "hint": None,
            "message": "semantic_video_conflict: injected plan failure",
        }
    )
    with pytest.raises(APIError, match="injected plan failure"):
        persist_semantic_video_plan(
            persisted_run["id"],
            expected_revision=4,
            run_updates=run_update,
            takes=initial_takes,
            client=_RecordingClient(unexpected),
        )


def test_candidate_reservation_queries_use_atomic_rpcs_and_map_only_conflicts():
    from app.features.semantic_videos.queries import (
        finalize_candidate_generation,
        reserve_candidate_generation,
    )

    reserved = {
        "id": "run-1",
        "revision": 4,
        "candidate_reservation_token": "token-1",
    }
    finalized = {
        "id": "run-1",
        "revision": 4,
        "candidate_reservation_token": "token-1",
    }
    client = _RecordingClient(reserved, finalized)

    assert reserve_candidate_generation(
        "post-1",
        expected_revision=3,
        run_create={"post_id": "post-1"},
        reservation_owner="operator@example.com",
        reservation_token="token-1",
        reservation_seconds=300,
        client=client,
    ) == reserved
    assert finalize_candidate_generation(
        "run-1",
        reserved_revision=4,
        reservation_token="token-1",
        run_updates={"master_snapshot": {"candidates": [1, 2, 3]}},
        client=client,
    ) == finalized
    assert client.calls == [
        {
            "kind": "rpc",
            "function": "reserve_semantic_video_candidates",
            "payload": {
                "p_post_id": "post-1",
                "p_expected_revision": 3,
                "p_run_create": {"post_id": "post-1"},
                "p_reservation_owner": "operator@example.com",
                "p_reservation_token": "token-1",
                "p_reservation_seconds": 300,
            },
        },
        {
            "kind": "rpc",
            "function": "finalize_semantic_video_candidates",
            "payload": {
                "p_run_id": "run-1",
                "p_reserved_revision": 4,
                "p_reservation_token": "token-1",
                "p_run_update": {"master_snapshot": {"candidates": [1, 2, 3]}},
            },
        },
    ]

    conflict = APIError(
        {
            "code": "40001",
            "details": None,
            "hint": None,
            "message": "semantic_video_conflict: candidate reservation is active",
        }
    )
    with pytest.raises(StateTransitionError, match="reservation"):
        reserve_candidate_generation(
            "post-1",
            expected_revision=None,
            run_create={"post_id": "post-1"},
            reservation_owner="operator@example.com",
            reservation_token="token-2",
            reservation_seconds=300,
            client=_RecordingClient(conflict),
        )

    unexpected = APIError(
        {
            "code": "P0001",
            "details": None,
            "hint": None,
            "message": "unexpected persistence failure",
        }
    )
    with pytest.raises(APIError, match="unexpected persistence failure"):
        reserve_candidate_generation(
            "post-1",
            expected_revision=None,
            run_create={"post_id": "post-1"},
            reservation_owner="operator@example.com",
            reservation_token="token-3",
            reservation_seconds=300,
            client=_RecordingClient(unexpected),
        )


def test_approval_retry_and_cancel_queries_use_transactional_rpcs_and_map_only_conflicts():
    from app.features.semantic_videos.queries import (
        approve_initial_plan_transition,
        approve_master_transition,
        approve_retry_transition,
        cancel_run_transition,
    )

    master_result = {
        "run": {"id": "run-1", "revision": 2, "stage": "awaiting_paid_approval"},
        "approval": {"id": "approval-master", "contract_hash": "master-hash"},
    }
    initial_result = {
        "run": {"id": "run-1", "revision": 3, "stage": "generating"},
        "approval": {"id": "approval-initial", "contract_hash": "plan-hash"},
    }
    retry_result = {
        "run": {"id": "run-1", "revision": 4, "stage": "generating"},
        "approval": {"id": "approval-retry", "contract_hash": "retry-hash"},
        "takes": [{"id": "retry-1", "take_index": 1, "attempt": 2}],
    }
    cancel_result = {
        "run": {"id": "run-1", "revision": 5, "stage": "failed"},
        "cancelled_take_count": 2,
    }
    client = _RecordingClient(master_result, initial_result, retry_result, cancel_result)

    master_run, master_approval = approve_master_transition(
        "run-1",
        expected_revision=1,
        candidate_index=2,
        approved_by="operator@example.com",
        reason="Best match",
        client=client,
    )
    initial_run, initial_approval = approve_initial_plan_transition(
        "run-1",
        expected_revision=2,
        plan_hash="plan-hash",
        approved_by="operator@example.com",
        reason=None,
        client=client,
    )
    retry_run, retry_approval, retry_takes = approve_retry_transition(
        "run-1",
        expected_revision=3,
        plan_hash="plan-hash",
        retry_takes=[{"take_index": 1, "attempt": 2, "request_hash": "request-2"}],
        contract_hash="retry-hash",
        approved_by="operator@example.com",
        reason="QA correction",
        client=client,
    )
    cancelled_run, cancelled_count = cancel_run_transition(
        "run-1",
        expected_revision=4,
        cancelled_by="operator@example.com",
        reason="Operator cancelled",
        correlation_id="corr-1",
        client=client,
    )

    assert (master_run, master_approval) == (
        master_result["run"],
        master_result["approval"],
    )
    assert (initial_run, initial_approval) == (
        initial_result["run"],
        initial_result["approval"],
    )
    assert (retry_run, retry_approval, retry_takes) == (
        retry_result["run"],
        retry_result["approval"],
        retry_result["takes"],
    )
    assert (cancelled_run, cancelled_count) == (cancel_result["run"], 2)
    assert [call["function"] for call in client.calls] == [
        "approve_semantic_video_master",
        "approve_semantic_video_initial_plan",
        "approve_semantic_video_retry",
        "cancel_semantic_video_run",
    ]

    conflict = APIError(
        {
            "code": "40001",
            "details": None,
            "hint": None,
            "message": "semantic_video_conflict: revision mismatch",
        }
    )
    with pytest.raises(StateTransitionError, match="master approval"):
        approve_master_transition(
            "run-1",
            expected_revision=1,
            candidate_index=2,
            approved_by="operator@example.com",
            reason=None,
            client=_RecordingClient(conflict),
        )

    unexpected = APIError(
        {
            "code": "P0001",
            "details": None,
            "hint": None,
            "message": "injected database failure",
        }
    )
    with pytest.raises(APIError, match="injected database failure"):
        cancel_run_transition(
            "run-1",
            expected_revision=4,
            cancelled_by="operator@example.com",
            reason="Operator cancelled",
            correlation_id="corr-1",
            client=_RecordingClient(unexpected),
        )

    misleading_message = APIError(
        {
            "code": "P0001",
            "details": None,
            "hint": None,
            "message": "semantic_video_conflict: injected database failure",
        }
    )
    with pytest.raises(APIError, match="injected database failure"):
        approve_initial_plan_transition(
            "run-1",
            expected_revision=2,
            plan_hash="plan-hash",
            approved_by="operator@example.com",
            reason=None,
            client=_RecordingClient(misleading_message),
        )


def test_query_helpers_use_only_lease_fenced_paid_worker_rpcs():
    from app.features.semantic_videos.queries import (
        persist_worker_accepted_operation,
        persist_worker_completed_take,
        persist_worker_submission_intent,
        reserve_paid_submission,
    )

    client = _RecordingClient(
        {"id": "take-1", "submission_state": "reserved"},
        {"id": "take-1", "submission_state": "intent_persisted"},
        {"id": "take-1", "submission_state": "submitted"},
        {"id": "take-1", "submission_state": "completed"},
    )
    fence = {
        "run_id": "run-1",
        "take_id": "take-1",
        "worker_id": "worker-1",
        "lease_token": "00000000-0000-0000-0000-000000000001",
        "client": client,
    }
    reserve_paid_submission(**fence)
    persist_worker_submission_intent(**fence, request_hash="a" * 64)
    persist_worker_accepted_operation(
        **fence,
        operation_id="operations/accepted-1",
        provider_model="veo-3.1-generate-001",
    )
    persist_worker_completed_take(
        **fence,
        provider_video_uri="gs://bucket/raw.mp4",
        raw_artifact_uri="https://storage/raw.mp4",
        raw_artifact_sha256="b" * 64,
    )

    assert [call["function"] for call in client.calls] == [
        "reserve_semantic_video_submission",
        "persist_semantic_video_submission_intent",
        "persist_semantic_video_accepted_operation",
        "persist_semantic_video_completed_take",
    ]
    assert all(call["payload"]["p_lease_token"] == fence["lease_token"] for call in client.calls)
    assert client.calls[3]["payload"]["p_raw_artifact_sha256"] == "b" * 64


def test_query_helpers_cover_fenced_claim_stage_retry_release_and_completion():
    from app.features.semantic_videos.queries import (
        acquire_run_lease,
        advance_worker_stage,
        complete_worker_run,
        release_worker_lease,
        require_worker_retry_approval,
    )

    client = _RecordingClient(
        [{"id": "run-1", "lease_owner": "worker-1", "lease_token": "lease-1"}],
        {"id": "run-1", "stage": "identity_qa"},
        {"id": "run-1", "stage": "retry_approval_required"},
        {"id": "run-1", "lease_owner": None},
        {
            "run": {"id": "run-2", "stage": "completed"},
            "post_id": "post-2",
            "video_status": "caption_completed",
        },
    )
    claimed = acquire_run_lease(
        run_id="run-1",
        worker_id="worker-1",
        lease_seconds=45,
        client=client,
    )
    assert claimed["lease_owner"] == "worker-1"
    advanced = advance_worker_stage(
        run_id="run-1",
        worker_id="worker-1",
        lease_token="lease-1",
        expected_stage="transcript_qa",
        next_stage="identity_qa",
        artifacts={"transcript": {"passed": True}},
        client=client,
    )
    assert advanced["stage"] == "identity_qa"
    retry = require_worker_retry_approval(
        run_id="run-1",
        worker_id="worker-1",
        lease_token="lease-1",
        expected_stage="identity_qa",
        failed_take_indexes=[0],
        evidence={"identity": {"score": 0.42}},
        client=client,
    )
    assert retry["stage"] == "retry_approval_required"
    released = release_worker_lease(
        run_id="run-1",
        worker_id="worker-1",
        lease_token="lease-1",
        client=client,
    )
    assert released["lease_owner"] is None
    completed = complete_worker_run(
        run_id="run-2",
        worker_id="worker-1",
        lease_token="lease-2",
        final_video_uri="https://storage/final.mp4",
        final_video_sha256="c" * 64,
        final_caption_uri="https://storage/final-captioned.mp4",
        final_caption_sha256="d" * 64,
        artifact_manifest={"delivery": {"passed": True}},
        client=client,
    )
    assert completed["run"]["stage"] == "completed"

    assert client.calls[0] == {
        "kind": "rpc",
        "function": "claim_semantic_video_run",
        "payload": {
            "worker_id": "worker-1",
            "lease_seconds": 45,
            "requested_run_id": "run-1",
        },
    }
    assert client.calls[1]["function"] == "advance_semantic_video_stage"
    assert client.calls[2]["function"] == "require_semantic_video_retry_approval"
    assert client.calls[3]["function"] == "release_semantic_video_lease"
    assert client.calls[4]["function"] == "complete_semantic_video_run"
