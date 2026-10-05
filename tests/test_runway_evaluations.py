"""Runway Seedance 2.5 evaluation slice: pricing, gates, source verification, submit fence, and polling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
import re
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.adapters.runway_client import RunwayError, RunwayOutput, RunwayTask, build_image_data_uri
from app.core.errors import FlowForgeException, StateTransitionError
from app.features.runway_evaluations import handlers, service
from app.features.shot_production.shot_deck import derive_shot_deck

POST_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
BATCH_ID = "33333333-3333-4333-8333-333333333333"
EVALUATION_ID = "44444444-4444-4444-8444-444444444444"
LEASE_TOKEN = "55555555-5555-4555-8555-555555555555"
TASK_ID = "task_0123456789abcdef"
OPERATOR = "ops@example.test"
FINGERPRINT = "f" * 64
GENERATION_HASH = "a" * 64
VISUAL_HASH = "b" * 64
PROMPT = "Eine Frau in ihrer Küche erklärt ruhig, wie der Treppenlift ihr hilft."
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (width, height), color)
    for x in range(0, width, 7):
        for y in range(0, height, 5):
            image.putpixel((x, y), (x % 255, y % 255, (x + y) % 255))
    image.save(output, format="PNG")
    return output.getvalue()


def _settings(**overrides):
    values = {
        "runway_evaluation_enabled": True,
        "runway_api_key": "rw-test-key",
        "runway_output_allowed_hosts": "*.runway-output.test",
        "runway_evaluation_operator_emails": f"{OPERATOR}, second@example.test",
        "reviewer_login_email": "reviewer@example.test",
        "runway_evaluation_allowed_resolutions": "720p,1080p",
        "runway_seedance_credits_per_second_480p": 20,
        "runway_seedance_credits_per_second_720p": 30,
        "runway_seedance_credits_per_second_1080p": 68,
        "runway_seedance_minimum_credits": 80,
        "runway_usd_per_credit": Decimal("0.01"),
        "runway_evaluation_max_credits_per_run": 600,
        "runway_evaluation_daily_credit_limit": 2000,
        "runway_evaluation_max_active": 1,
        "runway_evaluation_min_submit_interval_seconds": 30,
        "runway_poll_interval_seconds": 10,
        "runway_poll_max_age_seconds": 7200,
        "environment": "Production",
        "app_url": "https://studio.example.test",
        "app_host": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


CONTRACTS = SimpleNamespace(
    build_actor_reference_fingerprint=lambda rows: FINGERPRINT,
    validate_scene_plate_generation_contract=lambda value, *, actor_reference_fingerprint: {
        "contract_hash": GENERATION_HASH,
        "model": "scene-plate-model",
    },
    validate_visual_contract=lambda value: {"contract_hash": VISUAL_HASH},
    validate_approved_scene_plate_identity=lambda master, **kwargs: None,
)


class FakeStorage:
    image_prefix = "Studio/images"
    object_prefix = "Studio/videos"

    def __init__(self, objects):
        self.objects = dict(objects)
        self.uploads = []
        self.verify_result = {"passed": True, "failure_reasons": []}

    def download_video(self, *, video_url, correlation_id, timeout_seconds=None):
        return self.objects[video_url]

    def upload_video(self, *, video_bytes, file_name, correlation_id=None, content_type="video/mp4", object_key=None):
        self.uploads.append({"object_key": object_key, "content_type": content_type, "size": len(video_bytes)})
        url = f"https://r2.example.test/{object_key}"
        self.objects[url] = video_bytes
        return {"storage_key": object_key, "url": url, "sha256": sha256(video_bytes).hexdigest()}

    def verify_video_upload(self, *, storage_key, expected_size, expected_sha256):
        return self.verify_result


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.create_result = None
        self.mark_error = None
        self.ack_error = None
        self.claimed = []
        self.active = 0
        self.moderated_request = None
        self.moderation_lookup = None

    def find_moderated_request(self, **kwargs):
        self.moderation_lookup = kwargs
        return self.moderated_request

    def create(self, payload, **limits):
        self.calls.append(("create", payload, limits))
        return self.create_result or {"allowed": True, "evaluation": {"id": EVALUATION_ID, **payload}}

    def mark_submitting(self, evaluation_id):
        self.calls.append(("mark_submitting", evaluation_id))
        if self.mark_error:
            raise self.mark_error
        return {"id": evaluation_id, "status": "submitting"}

    def acknowledge(self, evaluation_id, *, task_id, provider_estimated_credits, cancel_reason=None):
        self.calls.append(("acknowledge", evaluation_id, task_id, provider_estimated_credits, cancel_reason))
        if self.ack_error:
            raise self.ack_error
        return {
            "id": evaluation_id,
            "status": "cancelled" if cancel_reason else "submitted",
            "task_id": task_id,
            "lease_token": None,
            "reservation_key": "runway_seedance_2_5:hidden",
        }

    def fail_before_submit(self, evaluation_id, *, code, message, details):
        self.calls.append(("fail_before_submit", evaluation_id, code))
        return {}

    def mark_submission_unknown(self, evaluation_id, *, code, message, details):
        self.calls.append(("mark_submission_unknown", evaluation_id, code))
        return {}

    def reconcile_stale(self, **kwargs):
        self.calls.append(("reconcile_stale", kwargs))
        return {"released": 0, "submission_unknown": 0}

    def claim(self, **kwargs):
        self.calls.append(("claim", kwargs))
        return self.claimed

    def record_poll(self, evaluation_id, **kwargs):
        self.calls.append(("record_poll", evaluation_id, kwargs))
        return {}

    def complete(self, evaluation_id, **kwargs):
        self.calls.append(("complete", evaluation_id, kwargs))
        return {}

    def fail_after_submit(self, evaluation_id, **kwargs):
        self.calls.append(("fail_after_submit", evaluation_id, kwargs))
        return {}

    def list_for_post(self, post_id, limit=20):
        return [{"id": EVALUATION_ID, "post_id": post_id, "status": "submitted", "lease_token": LEASE_TOKEN}]

    def count_active(self):
        return self.active

    def names(self):
        return [call[0] for call in self.calls]

    def last(self, name):
        return [call for call in self.calls if call[0] == name][-1]


class FakeRunway:
    def __init__(self):
        self.submissions = []
        self.submit_error = None
        self.submit_result = {"task_id": TASK_ID, "estimated_credits": 240.0}
        self.task = None
        self.task_error = None
        self.download_error = None
        self.output = None
        self.cancelled = []

    def submit_image_to_video(self, **kwargs):
        self.submissions.append(kwargs)
        if self.submit_error:
            raise self.submit_error
        return self.submit_result

    def get_task(self, task_id, *, correlation_id):
        if self.task_error:
            raise self.task_error
        return self.task

    def cancel_task(self, task_id, *, correlation_id):
        self.cancelled.append(task_id)
        return True

    def download_output(self, url, *, correlation_id):
        if self.download_error:
            raise self.download_error
        return self.output


def _scenario(*, creation_mode="semantic_ugc", take_duration=8, veo_resolution="720p"):
    master = _png(90, 160, (200, 180, 150))
    master_sha = sha256(master).hexdigest()
    front = _png(16, 16, (10, 20, 30))
    three_quarter = _png(16, 16, (40, 50, 60))
    deck = derive_shot_deck(approved_master_bytes=master, expected_sha256=master_sha, mime_type="image/png", shot_count=2)
    storage = FakeStorage(
        {
            "https://r2.example.test/front.png": front,
            "https://r2.example.test/three_quarter.png": three_quarter,
            "https://r2.example.test/master.png": master,
        }
    )
    run = {
        "id": RUN_ID,
        "post_id": POST_ID,
        "batch_id": BATCH_ID,
        "stage": "completed",
        "script_hash": "c" * 64,
        "resolution": veo_resolution,
        "master_hash": master_sha,
        "master_snapshot": {
            "storage_uri": "https://r2.example.test/master.png",
            "sha256": master_sha,
            "byte_length": len(master),
            "mime_type": "image/png",
            "generation_contract_hash": GENERATION_HASH,
            "visual_contract_hash": VISUAL_HASH,
            "actor_reference_fingerprint": FINGERPRINT,
            "provider_model": "scene-plate-model",
        },
        "reference_snapshot": {
            "actor_references": [
                {
                    "role": "actor_front",
                    "storage_uri": "https://r2.example.test/front.png",
                    "sha256": sha256(front).hexdigest(),
                    "byte_length": len(front),
                    "mime_type": "image/png",
                },
                {
                    "role": "actor_three_quarter",
                    "storage_uri": "https://r2.example.test/three_quarter.png",
                    "sha256": sha256(three_quarter).hexdigest(),
                    "byte_length": len(three_quarter),
                    "mime_type": "image/png",
                },
            ],
            "actor_reference_fingerprint": FINGERPRINT,
            "scene_plate_generation_contract": {},
            "visual_contract": {},
        },
        "plan_snapshot": {
            "generation_contract_hash": GENERATION_HASH,
            "actor_reference_fingerprint": FINGERPRINT,
            "visual_contract_hash": VISUAL_HASH,
        },
    }

    def take(index, attempt):
        return {
            "id": f"6666666{index}-6666-4666-8666-66666666666{attempt}",
            "take_index": index,
            "attempt": attempt,
            "beat_text": f"Beat {index}",
            "provider_duration_seconds": take_duration,
            "provider_model": "veo-3.1-generate-001",
            "seed": 1234,
            "shot_transform": {"output_sha256": deck[index].output_sha256},
            "request_contract": {
                "prompt": f"{PROMPT} Take {index}.{attempt}",
                "negative_prompt": "blurry, extra fingers",
                "provider_model": "veo-3.1-generate-001",
                "resolution": veo_resolution,
                "aspect_ratio": "9:16",
                "provider_duration_seconds": take_duration,
                "seed": 1234,
                "shot_sha256": deck[index].output_sha256,
            },
            "request_hash": "d" * 64,
            "submission_state": "completed",
            "raw_artifact_uri": f"https://r2.example.test/veo-{index}.mp4",
            "raw_artifact_sha256": "e" * 64,
        }

    takes = [take(0, 1), take(1, 1), take(1, 2)]
    context = service.EvaluationContext(
        post={"id": POST_ID, "seed_data": {"script_review_status": "approved"}},
        batch={"id": BATCH_ID, "creation_mode": creation_mode},
        run=run,
        takes=takes,
    )
    return SimpleNamespace(context=context, storage=storage, deck=deck, run=run, takes=takes, master=master)


def _deps(scenario, *, settings=None, repository=None, runway=None, probe=None, recovery=None):
    repository = repository or FakeRepository()
    runway = runway or FakeRunway()
    recovery_records = recovery if recovery is not None else []
    return SimpleNamespace(
        deps=service.EvaluationDependencies(
            settings=settings or _settings(),
            repository=repository,
            storage_factory=lambda: scenario.storage,
            runway_client_factory=lambda settings: runway,
            load_context=lambda post_id: scenario.context,
            budget_snapshot=lambda limit: {"daily_remaining_units": 2000, "daily_committed_units": 0, "frozen": False},
            probe_media=probe or (lambda content, **kwargs: {"duration_seconds": 8.0, "has_audio": True, "width": 720, "height": 1280}),
            recovery_writer=recovery_records.append,
            contracts=CONTRACTS,
            now=lambda: NOW,
        ),
        repository=repository,
        runway=runway,
        recovery=recovery_records,
    )


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("resolution", "credits", "usd"), [("480p", 160, "1.60"), ("720p", 240, "2.40"), ("1080p", 544, "5.44")])
def test_estimate_uses_configured_output_rates(resolution, credits, usd):
    estimate = service.estimate_credits(resolution=resolution, duration_seconds=8, settings=_settings())
    assert estimate.credits == credits
    assert str(estimate.usd) == usd


def test_estimate_applies_minimum_credits():
    estimate = service.estimate_credits(
        resolution="480p",
        duration_seconds=8,
        settings=_settings(runway_seedance_credits_per_second_480p=5),
    )
    assert estimate.credits == 80


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _codes(result):
    return {reason["code"] for reason in result["reasons"]}


def test_semantic_take_is_eligible_with_matched_veo_resolution():
    scenario = _scenario()
    result = service.evaluate_eligibility(
        context=scenario.context, take_index=1, resolution=None, settings=_settings(), operator_email=OPERATOR,
        active_count=0, budget_snapshot={"daily_remaining_units": 2000},
    )
    assert result["eligible"] is True, result["reasons"]
    assert result["request"]["resolution"] == "720p"
    assert result["request"]["ratio"] == "720:1280"
    assert result["estimate"]["credits"] == 240
    assert result["take_attempt"] == 2
    assert result["evaluation_only"] is True
    assert PROMPT not in json.dumps(result)


def test_manual_semantic_mode_is_eligible():
    scenario = _scenario(creation_mode="manual_semantic_ugc")
    result = service.evaluate_eligibility(context=scenario.context, take_index=0, resolution=None, settings=_settings(), operator_email=OPERATOR)
    assert result["eligible"] is True, result["reasons"]


@pytest.mark.parametrize(
    ("settings_overrides", "operator", "code"),
    [
        ({"runway_evaluation_enabled": False}, OPERATOR, "feature_flag_disabled"),
        ({"runway_api_key": ""}, OPERATOR, "credentials_missing"),
        ({"runway_output_allowed_hosts": ""}, OPERATOR, "output_hosts_not_configured"),
        ({}, "someone@example.test", "operator_not_authorized"),
        ({}, None, "operator_not_authorized"),
        ({"runway_evaluation_operator_emails": "reviewer@example.test"}, "reviewer@example.test", "operator_not_authorized"),
    ],
)
def test_configuration_gates_fail_closed(settings_overrides, operator, code):
    scenario = _scenario()
    result = service.evaluate_eligibility(
        context=scenario.context, take_index=0, resolution=None, settings=_settings(**settings_overrides), operator_email=operator
    )
    assert result["eligible"] is False
    assert code in _codes(result)


def test_operator_match_is_case_insensitive():
    assert service.operator_authorized("OPS@Example.Test", _settings()) is True


@pytest.mark.parametrize(
    ("scenario_kwargs", "take_index", "resolution", "settings_overrides", "code"),
    [
        ({"creation_mode": "automated"}, 0, None, {}, "unsupported_creation_mode"),
        ({}, 5, None, {}, "take_not_found"),
        ({"take_duration": 4}, 0, None, {}, "unsupported_take_duration"),
        ({}, 0, "480p", {}, "unsupported_resolution"),
        ({"veo_resolution": "4k"}, 0, None, {}, "unsupported_resolution"),
        ({}, 0, "1080p", {"runway_evaluation_max_credits_per_run": 500}, "estimate_exceeds_run_cap"),
    ],
)
def test_eligibility_reasons(scenario_kwargs, take_index, resolution, settings_overrides, code):
    scenario = _scenario(**scenario_kwargs)
    result = service.evaluate_eligibility(
        context=scenario.context, take_index=take_index, resolution=resolution, settings=_settings(**settings_overrides), operator_email=OPERATOR
    )
    assert result["eligible"] is False
    assert code in _codes(result)


def test_removed_post_and_missing_run_are_blocked():
    scenario = _scenario()
    scenario.context.post["seed_data"] = {"script_review_status": "removed"}
    assert "post_removed" in _codes(
        service.evaluate_eligibility(context=scenario.context, take_index=0, resolution=None, settings=_settings(), operator_email=OPERATOR)
    )
    scenario.context.run = None
    assert "semantic_run_missing" in _codes(
        service.evaluate_eligibility(context=scenario.context, take_index=0, resolution=None, settings=_settings(), operator_email=OPERATOR)
    )


def test_budget_and_concurrency_are_reported():
    scenario = _scenario()
    result = service.evaluate_eligibility(
        context=scenario.context, take_index=0, resolution=None, settings=_settings(), operator_email=OPERATOR,
        active_count=1, budget_snapshot={"daily_remaining_units": 100},
    )
    assert {"daily_budget_exhausted", "concurrency_limit_reached"} <= _codes(result)


def test_select_take_handles_index_zero_and_latest_attempt():
    takes = [
        {"take_index": 0, "attempt": 1, "request_contract": {}},
        {"take_index": 0, "attempt": 3, "request_contract": {}},
        {"take_index": 0, "attempt": 4, "request_contract": None},
    ]
    assert service.select_take(takes, 0)["attempt"] == 3
    assert service.select_take(takes, 1) is None


# ---------------------------------------------------------------------------
# Source verification and request contract
# ---------------------------------------------------------------------------


def test_source_verification_returns_exact_veo_shot_frame():
    scenario = _scenario()
    take = service.select_take(scenario.takes, 1)
    source = service.verify_semantic_take_source(run=scenario.run, take=take, storage=scenario.storage, correlation_id="t", contracts=CONTRACTS)
    assert source.image_bytes == scenario.deck[1].image_bytes
    assert source.sha256 == scenario.deck[1].output_sha256
    assert source.provenance["veo_request_hash"] == "d" * 64
    assert source.provenance["take_attempt"] == 2
    assert source.provenance["approved_master_sha256"] == scenario.run["master_hash"]


@pytest.mark.parametrize("tamper", ["actor_bytes", "actor_order", "master_bytes", "shot_hash", "plan_contract"])
def test_source_verification_rejects_any_changed_authority(tamper):
    scenario = _scenario()
    take = dict(service.select_take(scenario.takes, 1))
    if tamper == "actor_bytes":
        scenario.storage.objects["https://r2.example.test/front.png"] = b"changed"
    elif tamper == "actor_order":
        refs = scenario.run["reference_snapshot"]["actor_references"]
        refs.reverse()
    elif tamper == "master_bytes":
        scenario.storage.objects["https://r2.example.test/master.png"] = _png(90, 160, (1, 2, 3))
    elif tamper == "shot_hash":
        take["shot_transform"] = {"output_sha256": "0" * 64}
    elif tamper == "plan_contract":
        scenario.run["plan_snapshot"]["visual_contract_hash"] = "9" * 64
    with pytest.raises(StateTransitionError):
        service.verify_semantic_take_source(run=scenario.run, take=take, storage=scenario.storage, correlation_id="t", contracts=CONTRACTS)


def test_prompt_image_inlines_small_frames():
    scenario = _scenario()
    source = service.verify_semantic_take_source(
        run=scenario.run, take=service.select_take(scenario.takes, 0), storage=scenario.storage, correlation_id="t", contracts=CONTRACTS
    )
    image, meta = service.build_prompt_image(source=source, storage_factory=lambda: scenario.storage, correlation_id="t")
    assert image == build_image_data_uri(source.image_bytes, "image/png")
    assert meta == {"transport": "data_uri"}
    assert scenario.storage.uploads == []


def test_prompt_image_falls_back_to_content_addressed_storage_url(monkeypatch):
    monkeypatch.setattr(service, "DATA_URI_MAX_CHARS", 16)
    scenario = _scenario()
    source = service.verify_semantic_take_source(
        run=scenario.run, take=service.select_take(scenario.takes, 0), storage=scenario.storage, correlation_id="t", contracts=CONTRACTS
    )
    image, meta = service.build_prompt_image(source=source, storage_factory=lambda: scenario.storage, correlation_id="t")
    expected_key = f"Studio/images/runway-evaluations/sources/{source.sha256}.png"
    assert meta["transport"] == "https_storage_url"
    assert meta["storage_key"] == expected_key
    assert image == f"https://r2.example.test/{expected_key}"
    assert scenario.storage.objects[image] == source.image_bytes


# ---------------------------------------------------------------------------
# Submission fence
# ---------------------------------------------------------------------------


def _submit(harness, **overrides):
    params = {
        "post_id": POST_ID,
        "take_index": 1,
        "resolution": None,
        "confirm_estimated_credits": 240,
        "operator_email": "OPS@example.test",
        "correlation_id": "corr-1",
        "deps": harness.deps,
    }
    params.update(overrides)
    return service.submit_evaluation(**params)


def test_submit_reserves_marks_intent_then_acknowledges_once():
    scenario = _scenario()
    harness = _deps(scenario)
    result = _submit(harness)

    assert harness.repository.names() == ["create", "mark_submitting", "acknowledge"]
    _, payload, limits = harness.repository.calls[0]
    assert limits == {"daily_credit_limit": 2000, "max_active": 1, "min_submit_interval_seconds": 30}
    assert payload["reservation_key"].startswith("runway_seedance_2_5:evaluation:")
    assert payload["requested_by"] == "ops@example.test"
    assert payload["estimated_credits"] == 240
    assert payload["requested_ratio"] == "720:1280"
    assert payload["take_attempt"] == 2
    assert payload["poller_environment"] == "production"
    assert payload["poller_scope"] == "studio.example.test"
    assert payload["request_contract"]["prompt_image"]["sha256"] == scenario.deck[1].output_sha256
    assert payload["request_contract"]["negative_prompt_dropped"] is True
    assert payload["request_hash"] == service._canonical_hash(payload["request_contract"])
    assert PROMPT not in json.dumps(payload), "prompt text must never be persisted in evaluation rows"

    assert len(harness.runway.submissions) == 1
    submission = harness.runway.submissions[0]
    assert submission["prompt_text"] == scenario.takes[2]["request_contract"]["prompt"]
    assert submission["prompt_image"] == build_image_data_uri(scenario.deck[1].image_bytes, "image/png")
    assert submission["ratio"] == "720:1280"
    assert submission["duration_seconds"] == 8
    assert submission["audio"] is True
    assert submission["seed"] == 1234
    assert harness.repository.last("acknowledge")[2:] == (TASK_ID, 240.0, None)
    assert "lease_token" not in result and "reservation_key" not in result
    assert result["task_id"] == TASK_ID


@pytest.mark.parametrize("resolution", ["480p", "720p", "1080p"])
def test_known_moderated_inputs_block_preview_and_submit_at_every_resolution(resolution):
    scenario = _scenario()
    harness = _deps(scenario, settings=_settings(runway_evaluation_allowed_resolutions="480p,720p,1080p"))
    harness.repository.moderated_request = {
        "id": EVALUATION_ID,
        "error_details": {"failure_code": "INPUT_PREPROCESSING.SAFETY.THIRD_PARTY"},
    }
    preview = service.preview_evaluation(
        post_id=POST_ID, take_index=0, resolution=resolution,
        operator_email=OPERATOR, deps=harness.deps,
    )
    assert preview["eligible"] is False
    assert "previous_provider_moderation" in [reason["code"] for reason in preview["reasons"]]
    contract = service.select_take(scenario.takes, 0)["request_contract"]
    assert harness.repository.moderation_lookup == {
        "post_id": POST_ID,
        "prompt_sha256": sha256(contract["prompt"].encode()).hexdigest(),
        "shot_sha256": contract["shot_sha256"],
    }
    with pytest.raises(FlowForgeException) as caught:
        service.submit_evaluation(
            post_id=POST_ID, take_index=0, resolution=resolution,
            confirm_estimated_credits=preview["estimate"]["credits"],
            operator_email=OPERATOR, correlation_id="moderation-regression", deps=harness.deps,
        )
    assert caught.value.status_code == 409
    assert caught.value.details["blocked_before_submit"] is True
    assert harness.repository.calls == []
    assert harness.runway.submissions == []
    assert scenario.storage.uploads == []


def test_confirmed_estimate_mismatch_blocks_before_reservation():
    harness = _deps(_scenario())
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness, confirm_estimated_credits=200)
    assert caught.value.status_code == 409
    assert caught.value.details["blocked_before_submit"] is True
    assert harness.repository.calls == []
    assert harness.runway.submissions == []


def test_ineligible_submit_never_reserves_or_calls_runway():
    harness = _deps(_scenario(), settings=_settings(runway_evaluation_enabled=False))
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == 403
    assert harness.repository.calls == []
    assert harness.runway.submissions == []


def test_database_admission_block_returns_429_without_provider_call():
    harness = _deps(_scenario())
    harness.repository.create_result = {"allowed": False, "reason": "submission_paced", "retry_after_seconds": 12}
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == 429
    assert caught.value.details["retry_after_seconds"] == 12
    assert harness.repository.names() == ["create"]
    assert harness.runway.submissions == []


@pytest.mark.parametrize(("kind", "status_code"), [("rejected", 422), ("not_submitted", 503), ("rate_limited", 429)])
def test_provable_no_task_failures_release_the_reservation(kind, status_code):
    harness = _deps(_scenario())
    harness.runway.submit_error = RunwayError("no task", kind=kind, status_code=400 if kind == "rejected" else None)
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == status_code
    assert caught.value.details["no_task_created"] is True
    assert harness.repository.names() == ["create", "mark_submitting", "fail_before_submit"]
    assert harness.repository.last("fail_before_submit")[2] == f"runway_{kind}"


def test_ambiguous_submission_blocks_and_is_never_retried():
    harness = _deps(_scenario())
    harness.runway.submit_error = RunwayError("unknown", kind="ambiguous", status_code=502)
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == 502
    assert caught.value.details["submission_unknown"] is True
    assert "will not be resubmitted" in caught.value.message
    assert harness.repository.names() == ["create", "mark_submitting", "mark_submission_unknown"]
    assert len(harness.runway.submissions) == 1


def test_unexpected_submit_exception_is_treated_as_ambiguous():
    harness = _deps(_scenario())
    harness.runway.submit_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _submit(harness)
    assert harness.repository.names()[-1] == "mark_submission_unknown"


def test_intent_write_failure_releases_without_calling_runway():
    harness = _deps(_scenario())
    harness.repository.mark_error = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        _submit(harness)
    assert harness.repository.names() == ["create", "mark_submitting", "fail_before_submit"]
    assert harness.runway.submissions == []


def test_acknowledgement_failure_writes_recovery_record_with_task_id():
    harness = _deps(_scenario())
    harness.repository.ack_error = RuntimeError("db write lost")
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == 500
    assert caught.value.details["task_id"] == TASK_ID
    assert harness.recovery == [
        {"evaluation_id": EVALUATION_ID, "post_id": POST_ID, "task_id": TASK_ID, "provider": "runway", "correlation_id": "corr-1"}
    ]


def test_provider_estimate_above_reservation_cancels_accepted_task():
    harness = _deps(_scenario())
    harness.runway.submit_result = {"task_id": TASK_ID, "estimated_credits": 300.0}
    with pytest.raises(FlowForgeException) as caught:
        _submit(harness)
    assert caught.value.status_code == 409
    assert harness.runway.cancelled == [TASK_ID]
    assert harness.repository.last("acknowledge")[4] == "provider_estimate_exceeds_reservation"


def test_preview_survives_budget_read_failure():
    scenario = _scenario()
    harness = _deps(scenario)
    harness.deps.budget_snapshot = lambda limit: (_ for _ in ()).throw(RuntimeError("rpc down"))
    result = service.preview_evaluation(post_id=POST_ID, take_index=0, resolution=None, operator_email=OPERATOR, deps=harness.deps)
    assert result["eligible"] is True
    assert result["estimate"]["usd"] == "2.40"


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def _claimed(**overrides):
    row = {
        "id": EVALUATION_ID,
        "lease_token": LEASE_TOKEN,
        "task_id": TASK_ID,
        "status": "submitted",
        "submitted_at": (NOW - timedelta(minutes=3)).isoformat(),
        "requested_ratio": "720:1280",
        "requested_duration_seconds": 8,
        "poll_error_count": 0,
        "last_provider_status": None,
    }
    row.update(overrides)
    return row


def _task(status, **overrides):
    values = {
        "task_id": TASK_ID,
        "status": status,
        "progress": 0.4,
        "output_urls": (),
        "failure": None,
        "failure_code": None,
        "estimated_credits": None,
        "cost_credits": None,
    }
    values.update(overrides)
    return RunwayTask(**values)


def _poll(harness, row=None):
    harness.repository.claimed = [row or _claimed()]
    return service.poll_runway_evaluations(worker_id="poller-1", deps=harness.deps)


def test_poll_skips_without_credentials_and_scopes_claims():
    harness = _deps(_scenario(), settings=_settings(runway_api_key=""))
    assert service.poll_runway_evaluations(worker_id="poller-1", deps=harness.deps)["skipped"] == "credentials_missing"
    assert harness.repository.calls == []

    harness = _deps(_scenario())
    assert service.poll_runway_evaluations(worker_id="poller-1", deps=harness.deps) == {"claimed": 0}
    claim = harness.repository.last("claim")[1]
    assert claim["environment"] == "production"
    assert claim["scope"] == "studio.example.test"
    assert harness.repository.names()[0] == "reconcile_stale"


def test_poll_records_active_status():
    harness = _deps(_scenario())
    harness.runway.task = _task("RUNNING")
    assert _poll(harness)["outcomes"] == ["processing"]
    _, _, poll = harness.repository.last("record_poll")
    assert poll["provider_status"] == "RUNNING"
    assert poll["progress"] == 0.4
    assert poll["next_poll_at"] == NOW + timedelta(seconds=10)
    assert poll.get("poll_error") is None


@pytest.mark.parametrize(
    ("error", "expected_delay"),
    [
        (RunwayError("slow", kind="rate_limited", status_code=429, retry_after_seconds=30), 30),
        (RunwayError("down", kind="transport", status_code=503), 20),
    ],
)
def test_poll_transient_errors_back_off(error, expected_delay):
    harness = _deps(_scenario())
    harness.runway.task_error = error
    assert _poll(harness)["outcomes"] == ["retry_scheduled"]
    _, _, poll = harness.repository.last("record_poll")
    assert poll["next_poll_at"] == NOW + timedelta(seconds=expected_delay)
    assert poll["poll_error"]["kind"] == error.kind


@pytest.mark.parametrize(
    ("task", "error", "final_status", "code"),
    [
        (None, RunwayError("gone", kind="not_found", status_code=404), "failed", "provider_task_missing"),
        (None, RunwayError("bad", kind="invalid_response"), "failed", "provider_response_invalid"),
        (_task("FAILED", failure="moderation", failure_code="SAFETY", cost_credits=0), None, "failed", "provider_failed"),
        (_task("CANCELLED"), None, "cancelled", "provider_cancelled"),
        (_task("WEIRD"), None, "failed", "unknown_provider_status"),
        (_task("SUCCEEDED"), None, "failed", "provider_output_missing"),
    ],
)
def test_poll_terminal_failures(task, error, final_status, code):
    harness = _deps(_scenario())
    harness.runway.task = task
    harness.runway.task_error = error
    assert _poll(harness)["outcomes"] == [final_status]
    _, _, failure = harness.repository.last("fail_after_submit")
    assert failure["final_status"] == final_status
    assert failure["code"] == code
    assert failure["lease_token"] == LEASE_TOKEN


def test_third_party_moderation_preserves_zero_cost_without_resubmission():
    scenario = _scenario()
    harness = _deps(scenario)
    harness.runway.task = _task(
        "FAILED",
        failure="Your request was blocked by this model provider's content moderation system.",
        failure_code="INPUT_PREPROCESSING.SAFETY.THIRD_PARTY",
        cost_credits=0,
    )
    assert _poll(harness)["outcomes"] == ["failed"]
    failure = harness.repository.last("fail_after_submit")[2]
    assert failure["details"]["failure_code"] == "INPUT_PREPROCESSING.SAFETY.THIRD_PARTY"
    assert failure["actual_credits"] == 0
    assert "record_poll" not in harness.repository.names()
    assert "complete" not in harness.repository.names()
    assert harness.runway.submissions == []
    assert scenario.storage.uploads == []


def test_poll_timeout_cancels_task():
    harness = _deps(_scenario())
    row = _claimed(submitted_at=(NOW - timedelta(hours=3)).isoformat())
    assert _poll(harness, row)["outcomes"] == ["failed"]
    assert harness.runway.cancelled == [TASK_ID]
    assert harness.repository.last("fail_after_submit")[2]["code"] == "poll_timeout"


def test_poll_success_downloads_validates_uploads_and_completes():
    scenario = _scenario()
    harness = _deps(scenario)
    content = b"\x00\x00\x00\x18ftypmp42" + b"v" * 64
    harness.runway.task = _task("SUCCEEDED", output_urls=("https://cdn.runway-output.test/out.mp4",), cost_credits=240)
    harness.runway.output = RunwayOutput(content=content, content_type="video/mp4", host="cdn.runway-output.test", redirects=0)

    assert _poll(harness)["outcomes"] == ["completed"]
    digest = sha256(content).hexdigest()
    expected_key = f"Studio/videos/runway-evaluations/{EVALUATION_ID}/{digest[:16]}.mp4"
    assert scenario.storage.uploads == [{"object_key": expected_key, "content_type": "video/mp4", "size": len(content)}]
    _, _, completion = harness.repository.last("complete")
    assert completion["lease_token"] == LEASE_TOKEN
    assert completion["actual_credits"] == 240
    assert completion["output"]["storage_key"] == expected_key
    assert completion["output"]["sha256"] == digest
    assert completion["output"]["url"].startswith("https://")
    assert completion["output"]["probe"]["source_host"] == "cdn.runway-output.test"


@pytest.mark.parametrize(
    ("download_error", "outcome"),
    [
        (RunwayError("expired", kind="output_expired", status_code=403), "retry_scheduled"),
        (RunwayError("flaky", kind="transport"), "retry_scheduled"),
        (RunwayError("unsafe", kind="unsafe_output"), "failed"),
        (RunwayError("html", kind="invalid_media"), "failed"),
    ],
)
def test_poll_download_failures(download_error, outcome):
    harness = _deps(_scenario())
    harness.runway.task = _task("SUCCEEDED", output_urls=("https://cdn.runway-output.test/out.mp4",))
    harness.runway.download_error = download_error
    assert _poll(harness)["outcomes"] == [outcome]


def test_poll_media_validation_failure_fails_evaluation():
    def probe(content, **kwargs):
        raise service.MediaValidationError("media_missing_audio", "no audio")

    harness = _deps(_scenario(), probe=probe)
    harness.runway.task = _task("SUCCEEDED", output_urls=("https://cdn.runway-output.test/out.mp4",))
    harness.runway.output = RunwayOutput(content=b"x", content_type="video/mp4", host="cdn.runway-output.test", redirects=0)
    assert _poll(harness)["outcomes"] == ["failed"]
    assert harness.repository.last("fail_after_submit")[2]["code"] == "media_missing_audio"


def test_poll_storage_verification_failure_retries_without_completing():
    scenario = _scenario()
    scenario.storage.verify_result = {"passed": False, "failure_reasons": ["sha256_mismatch"]}
    harness = _deps(scenario)
    harness.runway.task = _task("SUCCEEDED", output_urls=("https://cdn.runway-output.test/out.mp4",))
    harness.runway.output = RunwayOutput(content=b"x", content_type="video/mp4", host="cdn.runway-output.test", redirects=0)
    assert _poll(harness)["outcomes"] == ["retry_scheduled"]
    assert "complete" not in harness.repository.names()


def test_poll_isolates_per_row_exceptions():
    harness = _deps(_scenario())
    harness.runway.task_error = ValueError("unexpected")
    assert _poll(harness)["outcomes"] == ["error"]


def test_video_poller_sweep_is_throttled_and_never_breaks_veo_loop(monkeypatch):
    try:
        from workers import video_poller
    except Exception as exc:  # noqa: BLE001 - the sandbox may block google.auth imports
        pytest.skip(f"workers.video_poller unavailable here: {type(exc).__name__}")
    calls = []
    monkeypatch.setattr(video_poller, "get_settings", lambda: _settings())
    monkeypatch.setattr(video_poller, "_poller_identity", lambda: "poller-x")
    monkeypatch.setattr(video_poller, "_last_runway_evaluation_sweep_at", 0.0)
    monkeypatch.setattr(video_poller, "_runway_evaluation_sweep_idle", True)
    monkeypatch.setattr(service, "poll_seedance_evaluations", lambda **kwargs: calls.append(kwargs) or {"claimed": 1})

    video_poller._poll_runway_evaluations()
    video_poller._poll_runway_evaluations()
    assert calls == [{"worker_id": "poller-x"}]
    assert video_poller._runway_evaluation_sweep_idle is False

    def explode(**kwargs):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(service, "poll_seedance_evaluations", explode)
    monkeypatch.setattr(video_poller, "_last_runway_evaluation_sweep_at", 0.0)
    video_poller._poll_runway_evaluations()
    assert video_poller._runway_evaluation_sweep_idle is True

    monkeypatch.setattr(video_poller, "get_settings", lambda: _settings(runway_api_key=""))
    monkeypatch.setattr(service, "poll_seedance_evaluations", lambda **kwargs: calls.append(kwargs) or {"claimed": 0})
    monkeypatch.setattr(video_poller, "_last_runway_evaluation_sweep_at", 0.0)
    video_poller._poll_runway_evaluations()
    assert len(calls) == 1


def test_poller_scope_matches_video_poller_fallbacks():
    assert service.poller_scope(_settings(app_url="studio.example.test/path")) == "studio.example.test"
    assert service.poller_scope(_settings(app_url="", app_host="Worker.Local")) == "worker.local"
    assert service.poller_scope(_settings(app_url="", app_host="", environment="")) == "development"


# ---------------------------------------------------------------------------
# Media probe (real ffmpeg when available)
# ---------------------------------------------------------------------------

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


def _render(tmp_path: Path, *, size: str, seconds: float, audio: bool) -> bytes:
    target = tmp_path / f"clip-{size}-{seconds}-{audio}.mp4"
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={size}:d={seconds}:r=10"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    command += ["-c:v", "mpeg4", "-t", str(seconds), str(target)]
    subprocess.run(command, check=True, capture_output=True)
    return target.read_bytes()


@needs_ffmpeg
def test_probe_accepts_matching_clip(tmp_path):
    probe = service.probe_mp4(_render(tmp_path, size="72x128", seconds=8, audio=True), expected_duration_seconds=8, expected_ratio="72:128", expect_audio=True)
    assert probe["has_audio"] is True
    assert probe["duration_seconds"] == pytest.approx(8, abs=0.75)


@needs_ffmpeg
@pytest.mark.parametrize(
    ("size", "seconds", "audio", "ratio", "code"),
    [
        ("72x128", 8, False, "72:128", "media_missing_audio"),
        ("72x128", 4, True, "72:128", "media_duration_mismatch"),
        ("128x72", 8, True, "72:128", "media_resolution_mismatch"),
        ("72x128", 8, True, "720:1280", "media_resolution_mismatch"),
    ],
)
def test_probe_rejects_mismatched_clips(tmp_path, size, seconds, audio, ratio, code):
    with pytest.raises(service.MediaValidationError) as caught:
        service.probe_mp4(_render(tmp_path, size=size, seconds=seconds, audio=audio), expected_duration_seconds=8, expected_ratio=ratio, expect_audio=True)
    assert caught.value.code == code


def test_probe_rejects_non_mp4_and_missing_ffprobe():
    with pytest.raises(service.MediaValidationError) as not_mp4:
        service.probe_mp4(b"<html>not a video</html>", expected_duration_seconds=8, expected_ratio="720:1280", expect_audio=True)
    assert not_mp4.value.code == "media_not_mp4"
    with pytest.raises(service.MediaValidationError) as missing:
        service.probe_mp4(
            b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32,
            expected_duration_seconds=8,
            expected_ratio="720:1280",
            expect_audio=True,
            ffprobe_bin="/nonexistent/ffprobe",
        )
    assert missing.value.code == "media_probe_unavailable"


# ---------------------------------------------------------------------------
# Isolation and HTTP surface
# ---------------------------------------------------------------------------


def test_service_never_writes_tables_directly():
    source = Path(service.__file__).read_text()
    assert re.findall(r"\)\s*\.(?:insert|upsert|update|delete)\(", source) == [], "writes must go through RPCs"
    assert set(re.findall(r"\.table\(\"([a-z_]+)\"\)", source)) == {"posts", "runway_video_evaluations"}
    for forbidden in ('"video_status"', '"video_metadata"', '"video_url"', "semantic_video_takes", "caption_"):
        assert forbidden not in source, forbidden


def _app(monkeypatch, *, user_email=OPERATOR):
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user_email = user_email
        request.state.correlation_id = "corr-http"
        return await call_next(request)

    @app.exception_handler(FlowForgeException)
    async def handle(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "details": exc.details})

    app.include_router(handlers.router)
    app.include_router(handlers.seedance_router)
    return TestClient(app)


def test_http_submit_requires_confirmation_and_forwards_operator(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "submit_evaluation", lambda **kwargs: calls.append(kwargs) or {"id": EVALUATION_ID})
    client = _app(monkeypatch, user_email="Ops@Example.test")

    assert client.post(f"/runway-evaluations/posts/{POST_ID}", json={"take_index": 0}).status_code == 422
    assert client.post(f"/runway-evaluations/posts/{POST_ID}", json={"take_index": 0, "confirm_estimated_credits": 240, "audio": False}).status_code == 422
    assert client.post("/runway-evaluations/posts/not-a-uuid", json={"confirm_estimated_credits": 240}).status_code == 422

    response = client.post(f"/runway-evaluations/posts/{POST_ID}", json={"take_index": 1, "resolution": "1080p", "confirm_estimated_credits": 544})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"id": EVALUATION_ID}}
    assert calls == [
        {
            "post_id": POST_ID,
            "take_index": 1,
            "resolution": "1080p",
            "confirm_estimated_credits": 544,
            "operator_email": "ops@example.test",
            "correlation_id": "corr-http",
        }
    ]


def test_http_preview_and_list(monkeypatch):
    monkeypatch.setattr(service, "preview_evaluation", lambda **kwargs: {"eligible": False, "kwargs": kwargs})
    monkeypatch.setattr(service, "list_evaluations", lambda **kwargs: [{"id": EVALUATION_ID}])
    client = _app(monkeypatch)
    preview = client.get(f"/runway-evaluations/posts/{POST_ID}/preview", params={"take_index": 2, "resolution": "720p"}).json()
    assert preview["data"]["kwargs"] == {"post_id": POST_ID, "take_index": 2, "resolution": "720p", "operator_email": OPERATOR}
    assert client.get(f"/runway-evaluations/posts/{POST_ID}/preview", params={"resolution": "4k"}).status_code == 422
    listed = client.get(f"/runway-evaluations/posts/{POST_ID}").json()
    assert listed["data"] == {"post_id": POST_ID, "evaluations": [{"id": EVALUATION_ID}]}


def test_serialized_rows_hide_lease_and_reservation():
    harness = _deps(_scenario())
    rows = service.list_evaluations(post_id=POST_ID, deps=harness.deps)
    assert rows[0]["id"] == EVALUATION_ID
    assert "lease_token" not in rows[0]
    assert "reservation_key" not in rows[0]

# The existing verified Semantic source path now hosts direct ModelArk evaluations.
def _modelark_settings(**overrides):
    return _settings(seedance_evaluation_provider="modelark", modelark_api_key="ark-test-key",
        modelark_evaluation_enabled=True, modelark_evaluation_operator_emails=OPERATOR,
        modelark_output_allowed_hosts="*.byteplus-output.test", modelark_evaluation_allowed_resolutions="480p,720p",
        modelark_evaluation_max_credits_per_run=2_000_000, modelark_evaluation_daily_credit_limit=10_000_000,
        modelark_evaluation_max_active=1, modelark_evaluation_min_submit_interval_seconds=30,
        modelark_poll_interval_seconds=10, modelark_poll_max_age_seconds=7200, **overrides)


def test_modelark_uses_separate_admission_identity_and_money_units():
    harness = _deps(_scenario(), settings=_modelark_settings())
    harness.deps.budget_snapshot=lambda limit:{"daily_remaining_units":10_000_000}
    preview=service.preview_evaluation(post_id=POST_ID,take_index=0,resolution="480p",operator_email=OPERATOR,deps=harness.deps)
    assert preview["eligible"] and preview["provider"] == "modelark"
    assert preview["provider_model"] == "dreamina-seedance-2-5-260628"
    assert preview["estimate"]["unit"] == "usd_microdollar"
    assert preview["estimate"]["expected_usd"] == "0.822402"
    _submit(harness, resolution="480p", confirm_estimated_credits=preview["estimate"]["credits"])
    payload=harness.repository.last("create")[1]
    assert payload["provider"] == "modelark" and payload["quota_provider"] == "modelark_seedance_2_5"
    assert payload["reservation_key"].startswith("modelark_seedance_2_5:")
    assert payload["request_contract"]["endpoint"] == "/contents/generations/tasks"
    assert payload["request_contract"]["pricing"]["usd_per_million_tokens"] == "10.70"


def test_modelark_refactor_preserves_upstream_moderation_fence():
    harness=_deps(_scenario(),settings=_modelark_settings())
    harness.repository.moderated_request={"id":EVALUATION_ID,"error_details":{"failure_code":"INPUT_PREPROCESSING.SAFETY.THIRD_PARTY"}}
    with pytest.raises(FlowForgeException) as caught:_submit(harness,resolution="480p",confirm_estimated_credits=945763)
    assert caught.value.status_code == 409
    assert not harness.runway.submissions and not harness.repository.calls


def test_wrong_host_poll_fails_before_any_provider_request():
    harness=_deps(_scenario(),settings=_modelark_settings())
    with pytest.raises(StateTransitionError):
        service.process_claimed_evaluation(_claimed(provider="runway"),client=harness.runway,deps=harness.deps)
    assert not harness.runway.submissions


def test_direct_route_accepts_explicit_budget_units(monkeypatch):
    calls=[]
    monkeypatch.setattr(service,"submit_evaluation",lambda **kwargs:calls.append(kwargs) or {"id":EVALUATION_ID})
    client=_app(monkeypatch)
    response=client.post(f"/seedance-evaluations/posts/{POST_ID}",json={"resolution":"480p","confirm_estimated_units":945763})
    assert response.status_code==200
    assert calls[0]["confirm_estimated_credits"]==945763
    assert calls[0]["operator_email"]==OPERATOR
    assert client.post(f"/seedance-evaluations/posts/{POST_ID}",json={"resolution":"480p"}).status_code==422


def test_trusted_asset_requires_exact_source_checksum():
    source=service.VerifiedSource(image_bytes=b"image",mime_type="image/png",sha256="a"*64,byte_length=5,provenance={})
    settings=_modelark_settings(modelark_reference_asset_uri="asset://approved",modelark_reference_asset_sha256="b"*64)
    with pytest.raises(StateTransitionError):
        service.build_prompt_image(source=source,settings=settings,storage_factory=lambda:pytest.fail("No upload"),correlation_id="test")
    settings.modelark_reference_asset_sha256=source.sha256
    uri,metadata=service.build_prompt_image(source=source,settings=settings,storage_factory=lambda:pytest.fail("No upload"),correlation_id="test")
    assert uri=="asset://approved" and metadata["transport"]=="modelark_trusted_asset"
