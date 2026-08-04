from __future__ import annotations

import os

import pytest
from jinja2 import Environment, FileSystemLoader


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")

from app.features.batches import handlers as batch_handlers  # noqa: E402


def _semantic_batch() -> dict:
    return {
        "id": "batch-semantic",
        "creation_mode": "semantic_ugc",
        "state": "S4_SCRIPTED",
        "target_length_tier": None,
        "target_duration_seconds": 50,
        "video_pipeline_route": "semantic_ugc",
        "meta_connection": {},
        "tiktok_connection": {},
        "posts": [
            {
                "id": "post-1",
                "post_type": "value",
                "topic_title": "Ramp truth",
                "topic_rotation": "",
                "topic_cta": "",
                "spoken_duration": 0,
                "seed_data": {"script_review_status": "approved"},
            }
        ],
    }


def test_semantic_projection_exposes_persisted_approval_and_cost_contract(monkeypatch):
    run = {
        "id": "run-1",
        "revision": 7,
        "stage": "retry_approval_required",
        "requested_duration_seconds": 50,
        "plan_hash": "a" * 64,
        "master_hash": "b" * 64,
        "master_snapshot": {
            "approved_candidate_index": 2,
            "candidates": [
                {"index": 1, "storage_uri": "https://cdn/one.png", "sha256": "1" * 64},
                {"index": 2, "storage_uri": "https://cdn/two.png", "sha256": "2" * 64},
            ],
        },
        "plan_snapshot": {
            "take_count": 7,
            "billable_provider_seconds": 56,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "22.40",
        },
        "artifact_manifest": {"delivery_duration_seconds": 49.8},
    }
    attempts = [
        {
            "take_index": index,
            "attempt": 1,
            "submission_state": "qa_failed" if index in {2, 5} else "completed",
            "provider_duration_seconds": 8,
            "raw_artifact_uri": f"https://cdn.example.com/take-{index}.mp4",
            "raw_artifact_sha256": f"{index}" * 64,
            "transcript_result": {"passed": index != 2},
            "identity_qa_result": {"passed": index != 5},
            "request_contract": {
                "prompt": f"Persisted provider prompt {index}",
                "provider_model": "veo-3.1-generate-001",
            },
        }
        for index in range(7)
    ]
    approvals = [
        {"approval_type": "reference", "contract_hash": "b" * 64},
        {"approval_type": "initial_plan", "contract_hash": "a" * 64},
    ]
    monkeypatch.setattr(batch_handlers.semantic_video_queries, "get_run_by_post", lambda post_id: run)
    monkeypatch.setattr(batch_handlers.semantic_video_queries, "list_attempts", lambda run_id: attempts)
    monkeypatch.setattr(batch_handlers.semantic_video_queries, "list_approvals", lambda run_id: approvals)

    view = batch_handlers._build_batch_detail_view(_semantic_batch())
    semantic = view["semantic_video"]
    item = semantic["posts"][0]

    assert semantic["requested_duration_seconds"] == 50
    assert item["delivery_duration_seconds"] == 49.8
    assert item["master_state"] == "approved"
    assert item["master_hash_is_current"] is True
    assert item["initial_plan_is_approved"] is True
    assert item["take_count"] == 7
    assert item["billable_provider_seconds"] == 56
    assert item["provider_model"] == "veo-3.1-generate-001"
    assert item["price_per_provider_second_usd"] == "0.40"
    assert item["estimated_cost_usd"] == "22.40"
    assert item["generated_takes"] == 7
    assert item["verified_takes"] == 6
    assert item["failed_take_indexes"] == [2, 5]
    assert item["retry_provider_seconds"] == 16
    assert item["retry_estimated_cost_usd"] == "6.40"
    assert item["latest_attempts"][2]["attempt"] == 1
    assert item["provider_prompts"][2]["raw_artifact_uri"] == (
        "https://cdn.example.com/take-2.mp4"
    )

    html = Environment(loader=FileSystemLoader("templates")).get_template(
        "batches/detail/_semantic_video.html"
    ).render(batch=_semantic_batch(), batch_view=view)
    assert "Veo 3.1" in html
    assert "$0.40/s" in html
    assert "$22.40" in html
    assert "video generated · QA needs attention" in html
    assert 'src="https://cdn.example.com/take-2.mp4"' in html
    assert "Raw Veo take" in html
    assert "Captions are added during final delivery." in html
    assert "Open generated take" in html
    assert "Retry only failed takes: 3, 6" in html
    assert "Retry only failed takes: 2, 5" not in html


def test_semantic_projection_reads_delivery_duration_from_worker_manifest(monkeypatch):
    run = {
        "id": "run-completed-16s",
        "revision": 9,
        "stage": "completed",
        "requested_duration_seconds": 16,
        "master_snapshot": {},
        "plan_snapshot": {},
        "artifact_manifest": {
            "pipeline_manifest": {
                "media_qa": {
                    "passed": True,
                    "duration_seconds": 16.0,
                }
            }
        },
    }
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: [],
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {"id": "post-completed-16s", "topic_title": "Exact delivery"}
    )

    assert item["delivery_duration_seconds"] == 16.0
    assert item["provider_model"] == "veo-3.1-generate-001"
    assert item["price_per_provider_second_usd"] == "0.40"


def test_semantic_projection_reads_verified_latest_take_from_pipeline_evidence(
    monkeypatch,
):
    run = {
        "id": "run-completed-8s",
        "revision": 101,
        "stage": "completed",
        "requested_duration_seconds": 8,
        "master_snapshot": {},
        "plan_snapshot": {"take_count": 1},
        "artifact_manifest": {
            "pipeline_manifest": {
                "takes": [
                    {
                        "index": 0,
                        "attempt": 2,
                        "transcript_qa": {"passed": True},
                    }
                ]
            }
        },
    }
    attempts = [
        {
            "take_index": 0,
            "attempt": 1,
            "submission_state": "qa_failed",
            "transcript_result": None,
        },
        {
            "take_index": 0,
            "attempt": 2,
            "submission_state": "completed",
            "transcript_result": None,
        },
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {"id": "post-completed-8s", "topic_title": "Verified delivery"}
    )

    assert item["generated_takes"] == 1
    assert item["verified_takes"] == 1
    assert item["latest_attempts"][0]["attempt"] == 2


def test_semantic_projection_ignores_stale_pipeline_qa_attempt(monkeypatch):
    run = {
        "id": "run-latest-not-verified",
        "revision": 4,
        "stage": "generating",
        "master_snapshot": {},
        "plan_snapshot": {"take_count": 1},
        "artifact_manifest": {
            "pipeline_manifest": {
                "takes": [
                    {
                        "index": 0,
                        "attempt": 1,
                        "transcript_qa": {"passed": True},
                    }
                ]
            }
        },
    }
    attempts = [
        {
            "take_index": 0,
            "attempt": 1,
            "submission_state": "completed",
            "transcript_result": None,
        },
        {
            "take_index": 0,
            "attempt": 2,
            "submission_state": "submitted",
            "transcript_result": None,
        },
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {"id": "post-latest-not-verified", "topic_title": "Pending retry"}
    )

    assert item["verified_takes"] == 0
    assert item["latest_attempts"][0]["attempt"] == 2


@pytest.mark.parametrize("qa_stage", ["identity_qa", "acoustic_qa"])
def test_generated_take_qa_failure_renders_free_resume_instead_of_paid_retry(
    monkeypatch,
    qa_stage,
):
    run = {
        "id": "run-identity-service-failure",
        "revision": 12,
        "stage": "retry_approval_required",
        "requested_duration_seconds": 16,
        "plan_hash": "a" * 64,
        "master_hash": "b" * 64,
        "master_snapshot": {},
        "plan_snapshot": {
            "take_count": 2,
            "billable_provider_seconds": 16,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "6.40",
        },
        "artifact_manifest": {
            "qa_failure": {
                "stage": qa_stage,
                "message": "Existing generated takes require a QA recheck",
                "failure_type": "qa_service_unavailable",
                "retry_mode": "qa_only",
            }
        },
    }
    attempts = [
        {
            "take_index": index,
            "attempt": 1,
            "submission_state": "qa_failed",
            "provider_duration_seconds": 8,
        }
        for index in range(2)
    ]
    approvals = [
        {"approval_type": "reference", "contract_hash": "b" * 64},
        {"approval_type": "initial_plan", "contract_hash": "a" * 64},
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: approvals,
    )

    view = batch_handlers._build_batch_detail_view(_semantic_batch())
    item = view["semantic_video"]["posts"][0]

    assert item["qa_resume_available"] is True
    assert item["qa_resume_stage"] == qa_stage
    assert item["qa_resume_message"] == "Existing generated takes require a QA recheck"

    html = Environment(loader=FileSystemLoader("templates")).get_template(
        "batches/detail/_semantic_video.html"
    ).render(batch=_semantic_batch(), batch_view=view)
    assert 'data-action="resume-qa"' in html
    assert "Continue with generated videos · $0.00" in html
    assert "no new Veo work will be submitted" in html
    assert 'data-action="approve-retry" data-cost-usd="6.40"' not in html


def test_terminal_speech_overlap_renders_localized_paid_retry_instead_of_free_resume(
    monkeypatch,
):
    run = {
        "id": "run-terminal-speech-overlap",
        "revision": 13,
        "stage": "retry_approval_required",
        "requested_duration_seconds": 8,
        "plan_hash": "a" * 64,
        "master_hash": "b" * 64,
        "master_snapshot": {},
        "plan_snapshot": {
            "take_count": 1,
            "billable_provider_seconds": 8,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "3.20",
        },
        "artifact_manifest": {
            "qa_failure": {
                "stage": "acoustic_qa",
                "message": "Advisory terminal protection would cut transcript-safe context.",
                "failure_type": "terminal_tail_speech_overlap",
                "retry_mode": "localized_paid_take",
                "failed_take_indexes": [0],
            }
        },
    }
    attempts = [
        {
            "take_index": 0,
            "attempt": 1,
            "submission_state": "qa_failed",
            "provider_duration_seconds": 8,
        }
    ]
    approvals = [
        {"approval_type": "reference", "contract_hash": "b" * 64},
        {"approval_type": "initial_plan", "contract_hash": "a" * 64},
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: approvals,
    )

    view = batch_handlers._build_batch_detail_view(_semantic_batch())
    item = view["semantic_video"]["posts"][0]

    assert item["qa_resume_available"] is False
    assert item["failed_take_indexes"] == [0]
    assert item["retry_provider_seconds"] == 8
    assert item["retry_estimated_cost_usd"] == "3.20"

    html = Environment(loader=FileSystemLoader("templates")).get_template(
        "batches/detail/_semantic_video.html"
    ).render(batch=_semantic_batch(), batch_view=view)
    assert "Retry only failed takes: 1" in html
    assert 'data-action="approve-retry" data-cost-usd="3.20"' in html
    assert "Continue with generated videos · $0.00" not in html


def test_completed_semantic_panel_renders_run_artifact_urls_without_legacy_prompt(monkeypatch):
    run = {
        "id": "run-completed-artifacts",
        "revision": 11,
        "stage": "completed",
        "requested_duration_seconds": 16,
        "final_video_uri": "https://cdn.example.com/semantic-raw.mp4",
        "final_caption_uri": "https://cdn.example.com/semantic-captioned.mp4",
        "master_snapshot": {},
        "plan_snapshot": {},
        "artifact_manifest": {"delivery_duration_seconds": 16.0},
    }
    post = {
        "id": "post-completed-artifacts",
        "topic_title": "Exact artifact truth",
        "video_prompt_json": None,
        "video_url": "https://cdn.example.com/post-captioned-fallback.mp4",
        "video_metadata": {
            "raw_video_url": "https://cdn.example.com/post-raw-fallback.mp4",
        },
    }
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: [],
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(post)

    assert item["final_video_url"] == "https://cdn.example.com/semantic-raw.mp4"
    assert item["final_caption_url"] == "https://cdn.example.com/semantic-captioned.mp4"

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_semantic_video.html").render(
        batch=_semantic_batch(),
        batch_view={
            "semantic_workflow": {
                "current_step": {"key": "delivery"},
            },
            "semantic_video": {
                "requested_duration_seconds": 16,
                "duration_contract": {},
                "posts": [item],
            }
        },
    )

    assert "Final delivery" in html
    assert 'href="https://cdn.example.com/semantic-raw.mp4"' in html
    assert 'href="https://cdn.example.com/semantic-captioned.mp4"' in html
    assert "post-captioned-fallback.mp4" not in html


def test_semantic_projection_falls_back_to_persisted_post_artifact_urls(monkeypatch):
    run = {
        "id": "run-post-artifact-fallback",
        "revision": 4,
        "stage": "completed",
        "requested_duration_seconds": 16,
        "master_snapshot": {},
        "plan_snapshot": {},
        "artifact_manifest": {},
    }
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: [],
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {
            "id": "post-artifact-fallback",
            "topic_title": "Persisted post artifacts",
            "video_prompt_json": None,
            "video_url": "https://cdn.example.com/post-captioned.mp4",
            "video_metadata": {
                "raw_video_url": "https://cdn.example.com/post-raw.mp4",
                "caption_video_url": "https://cdn.example.com/post-captioned.mp4",
            },
        }
    )

    assert item["final_video_url"] == "https://cdn.example.com/post-raw.mp4"
    assert item["final_caption_url"] == "https://cdn.example.com/post-captioned.mp4"


def test_semantic_projection_rejects_raw_file_mislabeled_as_captioned(monkeypatch):
    raw_url = "https://cdn.example.com/raw.mp4"
    repaired_url = "https://cdn.example.com/repaired-captioned.mp4"
    run = {
        "id": "run-caption-alias",
        "revision": 8,
        "stage": "completed",
        "requested_duration_seconds": 8,
        "final_video_uri": raw_url,
        "final_video_sha256": "a" * 64,
        "final_caption_uri": raw_url,
        "final_caption_sha256": "a" * 64,
        "master_snapshot": {},
        "plan_snapshot": {},
        "artifact_manifest": {},
    }
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: [],
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {
            "id": "post-caption-alias",
            "topic_title": "Caption recovery",
            "video_url": repaired_url,
            "video_metadata": {
                "raw_video_url": raw_url,
                "raw_video_sha256": "a" * 64,
                "caption_video_url": repaired_url,
                "caption_video_sha256": "b" * 64,
            },
        }
    )

    assert item["final_video_url"] == raw_url
    assert item["final_caption_url"] == repaired_url


def test_semantic_projection_exposes_frozen_visual_contract_and_provider_prompts(monkeypatch):
    visual_contract = {
        "version": "semantic_visual_contract_v1",
        "scene_key": "garden_patio_a",
        "scene_description": "An accessible garden patio in soft daylight.",
        "wardrobe_key": "grey_cardigan",
        "wardrobe_description": "light-grey cardigan over a plain white top",
        "wheelchair_description": "the same compact black manual wheelchair",
        "framing_description": "vertical seated medium shot with both wheels visible",
        "location_reference_sha256": "c" * 64,
        "contract_hash": "d" * 64,
    }
    run = {
        "id": "run-visual-truth",
        "revision": 3,
        "stage": "generating",
        "reference_snapshot": {"visual_contract": visual_contract},
        "master_snapshot": {},
        "plan_snapshot": {},
    }
    attempts = [
        {
            "take_index": 0,
            "attempt": 1,
            "submission_state": "completed",
            "request_contract": {
                "prompt": "Continue the approved garden scene and preserve the grey cardigan.",
                "negative_prompt": "standing, walking, different wheelchair",
                "provider_model": "veo-3.1-generate-001",
            },
        },
        {
            "take_index": 1,
            "attempt": 1,
            "submission_state": "planned",
            "request_contract": {"negative_prompt": "music"},
        },
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: [],
    )

    item = batch_handlers._build_semantic_video_post_projection(
        {"id": "post-visual-truth", "topic_title": "Garden transfer"}
    )

    assert item["visual_contract"] == visual_contract
    assert item["provider_prompts"] == [
        {
            "take_index": 0,
            "attempt": 1,
            "submission_state": "completed",
            "provider_model": "veo-3.1-generate-001",
            "prompt": "Continue the approved garden scene and preserve the grey cardigan.",
            "negative_prompt": "standing, walking, different wheelchair",
        }
    ]


def test_legacy_projection_does_not_query_or_render_semantic_workflow(monkeypatch):
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda post_id: (_ for _ in ()).throw(AssertionError("legacy queried semantic data")),
    )
    batch = _semantic_batch()
    batch["creation_mode"] = "automated"
    batch["target_duration_seconds"] = None
    batch["posts"] = []

    view = batch_handlers._build_batch_detail_view(batch)
    assert view["semantic_video"] is None

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail.html").render(
        batch=batch,
        batch_view=view,
        static_version="1",
    )
    assert "semantic-video-workflow" not in html
    assert "semantic_video.js" not in html


@pytest.mark.parametrize("creation_mode", ["semantic_ugc", "manual_semantic_ugc"])
def test_semantic_modes_render_workflow_without_generic_model_selector(
    monkeypatch,
    creation_mode,
):
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda post_id: None,
    )
    batch = _semantic_batch()
    batch["creation_mode"] = creation_mode

    view = batch_handlers._build_batch_detail_view(batch)
    assert view["semantic_video"] is not None
    assert view["video_generation_settings"]["initial_model"] == "veo-3.1-generate-001"

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail.html").render(
        batch=batch,
        batch_view=view,
        static_version="1",
    )
    assert "semantic-video-workflow" in html
    assert "semantic_video.js" in html
    assert "Batch Video Generation Settings" not in html
    assert "Veo 3.1 Fast" not in html
    assert "Veo 3.1 Lite" not in html


def test_semantic_partial_has_accessible_hash_gated_approval_controls():
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_semantic_video.html")
    html = template.render(
        batch=_semantic_batch(),
        batch_view={
            "semantic_workflow": {
                "current_step": {"key": "scene"},
            },
            "semantic_video": {
                "requested_duration_seconds": 50,
                "posts": [
                    {
                        "post_id": "post-1",
                        "topic_title": "Ramp truth",
                        "revision": 7,
                        "stage": "awaiting_paid_approval",
                        "plan_hash": "",
                        "master_state": "approved",
                        "master_hash_is_current": False,
                        "initial_plan_is_approved": False,
                        "actor_references": [
                            {
                                "role": "actor_front",
                                "storage_uri": "https://cdn/front.png",
                            },
                            {
                                "role": "actor_three_quarter",
                                "storage_uri": "https://cdn/three-quarter.png",
                            },
                        ],
                        "has_passed_candidate": True,
                        "candidates": [
                            {
                                "index": 1,
                                "storage_uri": "https://cdn/one.png",
                                "sha256": "1" * 64,
                                "identity_gate_result": {
                                    "passed": True,
                                    "confidence": 0.96,
                                    "blocking_reasons": [],
                                    "observed_differences": [
                                        "Natural expression differs slightly."
                                    ],
                                },
                            }
                        ],
                        "requested_duration_seconds": 50,
                        "delivery_duration_seconds": None,
                        "take_count": 0,
                        "billable_provider_seconds": 0,
                        "estimated_cost_usd": "0.00",
                        "generated_takes": 0,
                        "verified_takes": 0,
                        "failed_take_indexes": [],
                        "retry_provider_seconds": 0,
                        "retry_estimated_cost_usd": "0.00",
                        "visual_contract": {
                            "scene_key": "garden_patio_a",
                            "scene_description": "An accessible garden patio in soft daylight.",
                            "wardrobe_key": "grey_cardigan",
                            "wardrobe_description": "light-grey cardigan over a plain white top",
                            "wheelchair_description": "the same compact black manual wheelchair",
                        },
                        "provider_prompts": [
                            {
                                "take_index": 0,
                                "attempt": 1,
                                "submission_state": "planned",
                                "provider_model": "veo-3.1-generate-001",
                                "prompt": "Continue the approved garden scene and preserve the grey cardigan.",
                                "negative_prompt": "standing, walking, different wheelchair",
                            }
                        ],
                    }
                ],
            }
        },
    )

    assert 'id="semantic-video-workflow"' in html
    assert 'id="semantic-video-post-post-1"' in html
    assert 'aria-live="polite"' in html
    assert "Wheelchair scene plate candidates" in html
    assert 'aria-label="Select wheelchair scene plate candidate 1"' in html
    assert "Original-actor identity review" in html
    assert "Original front reference" in html
    assert "Original three-quarter reference" in html
    assert "Identity verified · 96% evaluator confidence" in html
    assert "not overall picture quality" in html
    assert "Approving confirms that the selected scene plate shows the same actor as both original references" in html
    assert "Regenerate wheelchair scene plates" in html
    assert "Approve selected scene plate and confirm identity" in html
    assert "data-identity-attestation" not in html
    assert "Frozen visual contract" in html
    assert "An accessible garden patio in soft daylight." in html
    assert "light-grey cardigan over a plain white top" in html
    assert "the same compact black manual wheelchair" in html
    assert "Actual provider prompts" not in html
    assert 'data-action="approve-master"' in html
    assert "Continue: Build free Veo plan" not in html
    assert 'data-action="approve-plan"' not in html
    assert "disabled" in html


def test_awaiting_paid_visual_can_be_regenerated_from_live_panel():
    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_semantic_video.html").render(
        batch=_semantic_batch(),
        batch_view={
            "semantic_video": {
                "requested_duration_seconds": 16,
                "posts": [
                    {
                        "post_id": "post-visual-restart",
                        "topic_title": "Changed garden and outfit",
                        "revision": 4,
                        "stage": "awaiting_paid_approval",
                        "plan_hash": "",
                        "master_state": "approved",
                        "master_hash_is_current": False,
                        "initial_plan_is_approved": False,
                        "candidates": [],
                        "requested_duration_seconds": 16,
                        "delivery_duration_seconds": None,
                        "take_count": 0,
                        "billable_provider_seconds": 0,
                        "estimated_cost_usd": "0.00",
                        "generated_takes": 0,
                        "verified_takes": 0,
                        "failed_take_indexes": [],
                        "retry_provider_seconds": 0,
                        "retry_estimated_cost_usd": "0.00",
                        "visual_contract": {},
                        "provider_prompts": [],
                    }
                ],
            }
        },
    )

    button = html.split('data-action="generate-candidates"', 1)[1].split(">", 1)[0]
    assert "disabled" not in button
    assert "Regenerate wheelchair scene plates" in html
    assert "never discards paid take evidence" in html


def test_unapproved_candidate_set_is_labeled_as_regeneration():
    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_semantic_video.html").render(
        batch=_semantic_batch(),
        batch_view={
            "semantic_workflow": {"current_step": {"key": "scene"}},
            "semantic_video": {
                "requested_duration_seconds": 16,
                "posts": [
                    {
                        "post_id": "post-candidate-refresh",
                        "topic_title": "Refresh candidates",
                        "revision": 0,
                        "stage": "awaiting_reference_approval",
                        "script_review_status": "approved",
                        "candidates": [{"index": 1, "storage_uri": "https://cdn/one.png"}],
                        "visual_contract": {},
                        "actor_references": [],
                        "has_passed_candidate": False,
                        "requested_duration_seconds": 16,
                    }
                ],
            },
        },
    )

    assert "Regenerate wheelchair scene plates" in html


@pytest.mark.parametrize("creation_mode", ["semantic_ugc", "manual_semantic_ugc"])
def test_semantic_post_card_omits_unused_legacy_prompt(creation_mode):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_post_card.html")
    html = template.render(
        batch={
            "id": "batch-semantic",
            "state": "S4_SCRIPTED",
            "creation_mode": creation_mode,
            "scene_plan": None,
            "actor_identity_id": "actor-1",
        },
        post={
            "id": "post-1",
            "post_type": "value",
            "topic_title": "Ramp truth",
            "topic_rotation": "Check the ramp angle before committing.",
            "spoken_duration": 6.5,
            "created_at": "2026-07-20T10:00:00+00:00",
            "updated_at": None,
            "seed_data": {
                "script": "Check the ramp angle before committing.",
                "script_review_status": "approved",
            },
            "video_prompt_json": {
                "veo_prompt": "This is the stored legacy prompt draft.",
            },
            "video_status": "pending",
            "video_url": None,
            "video_metadata": {},
            "blog_enabled": False,
            "blog_status": None,
            "review_caption": None,
            "publish_caption": None,
            "caption_source_links": [],
        },
        batch_view={
            "semantic_video": {
                "posts": [
                    {
                        "post_id": "post-1",
                        "stage": "awaiting_paid_approval",
                        "plan_hash": "",
                        "estimated_cost_usd": "0.00",
                    }
                ]
            }
        },
    )

    assert "Legacy Prompt" not in html
    assert "This is the stored legacy prompt draft." not in html
    assert "VEO Prompt (Sent to Provider)" not in html
    assert 'href="#semantic-video-post-post-1"' in html
    assert 'data-semantic-next-action' in html
    assert "Continue: Build free Veo plan" in html


def test_manual_semantic_script_form_exposes_location_and_outfit_overrides():
    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_post_card.html").render(
        batch={
            "id": "batch-semantic-manual",
            "state": "S2_SEEDED",
            "creation_mode": "manual_semantic_ugc",
            "scene_plan": None,
        },
        post={
            "id": "post-semantic-manual",
            "post_type": "value",
            "topic_title": "Accessible patio",
            "topic_rotation": "",
            "spoken_duration": 0,
            "created_at": "2026-07-20T10:00:00+00:00",
            "updated_at": None,
            "seed_data": {
                "manual_draft": True,
                "script": "A complete draft script.",
                "script_review_status": "pending",
                "semantic_scene_key": "garden_patio_a",
                "semantic_wardrobe_description": "navy cotton blouse",
            },
            "video_prompt_json": None,
            "video_status": "pending",
            "video_url": None,
            "video_metadata": {},
            "blog_enabled": False,
            "blog_status": None,
            "review_caption": None,
            "publish_caption": None,
            "caption_source_links": [],
        },
    )

    assert 'name="semantic_scene_key"' in html
    assert '<option value="garden_patio_a" selected>' in html
    assert 'name="semantic_wardrobe_description"' in html
    assert 'value="navy cotton blouse"' in html
    assert "Automatic rotating location" in html
    assert "Leave blank for automatic outfit rotation" in html


@pytest.mark.parametrize("creation_mode", ["semantic_ugc", "manual_semantic_ugc"])
def test_semantic_s2_script_editor_uses_live_16s_duration_contract_guidance(
    monkeypatch,
    creation_mode,
):
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: None,
    )
    batch = _semantic_batch()
    batch["state"] = "S2_SEEDED"
    batch["creation_mode"] = creation_mode
    batch["target_duration_seconds"] = 16
    batch["posts"][0]["seed_data"] = {
        "script": (
            "Ein sicherer Zugang erleichtert deinen Alltag und schafft mehr Ruhe bei jeder "
            "Bewegung. Prüfe deshalb Wege und Rampen frühzeitig und plane genug Platz für "
            "deinen Rollstuhl ein."
        ),
        "script_review_status": "pending",
    }

    view = batch_handlers._build_batch_detail_view(batch)
    contract = view["semantic_video"]["duration_contract"]

    assert contract["requested_duration_seconds"] == 16
    assert contract["minimum_words"] == 32
    assert contract["maximum_words"] == 36
    assert contract["minimum_semantic_blocks"] == 2

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_post_card.html").render(
        batch=batch,
        post=view["visible_posts"][0],
        batch_view=view,
    )

    assert "16s target" in html
    assert "32–36 words" in html
    assert "at least 2 complete semantic statements" in html
    assert 'x-text="wordCount"' in html
    assert 'x-text="completeStatementCount"' in html
    assert 'x-text="contractStatus"' in html
    assert "Words ready" in html
    assert "Statements ready" in html


def test_semantic_controller_confirms_exact_cost_and_polls_progress():
    source = open("static/js/batches/semantic_video.js", encoding="utf-8").read()
    template_source = open(
        "templates/batches/detail/_semantic_video.html", encoding="utf-8"
    ).read()

    assert "/progress" in source
    assert "confirm(" in source
    assert "approveReadyBatchPlans(workflow, button)" in source
    assert "approve-batch-plans" in source
    assert "exact combined cost" in source
    assert "roots.length !== expectedCount" in source
    assert "/semantic-videos/batches/${encodeURIComponent(workflow.dataset.batchId)}/approve" in source
    assert "approvals: approvals.map" in source
    assert "result.approval_count" in source
    assert "data-cost-usd" in source
    assert "generated-takes" in source
    assert "verified-takes" in source
    assert "retry-approve" in source
    assert "qa-resume" in source
    assert "master-approve" in source
    assert "reconcileMasterApproval(root)" in source
    assert "progress.stage !== 'awaiting_reference_approval'" in source
    assert "synchronizePaidAction(root, path, body)" in source
    assert "reconcilePaidAction(root, path)" in source
    assert "['approve', 'retry-approve'].includes(path)" in source
    assert "error.status === 409" in source
    assert "path === 'approve' ? 'awaiting_paid_approval' : 'retry_approval_required'" in source
    assert "expected_revision: Number(progress.revision || 0)" in source
    assert "String(progress.plan_hash || '') !== String(body.plan_hash || '')" in source
    assert "data-identity-attestation" not in source
    assert "candidate_generation_status" in source
    assert "candidate_generation_phase" in source
    assert "showCandidateLoading(root)" in source
    assert "recoverCandidateProgress(root)" in source
    assert "updateCandidateStatus(root, progress)" in source
    assert "Generating 3 scene plates" in source
    assert "Checking scene diversity" in source
    assert "Verifying actor identity" in source
    assert "showPlanLoading(root)" in source
    assert "showPlanError(root, error.message)" in source
    assert "Building production plan" in source
    assert "Plan could not be built" in source
    assert "['takes', 'seconds', 'cost']" in source
    assert "force ? 2000 : 8000" in source
    assert "payload?.message" in source
    assert "startPolling(root, true, false)" in source
    assert "reloadAtWorkflow(root)" in source
    assert "settleCandidateAction(root)" in source
    assert "candidateBaselineRunId" in source
    assert "candidateBaselineRevision" in source
    assert "requestAdvanced" in source
    assert "candidateRoot.dataset.waitingForCandidates === 'true'" in source
    assert "candidateRoot.dataset.candidateGenerationStatus === 'generating'" in source
    assert 'data-run-id="{{ item.run_id or \'\' }}"' in template_source
    assert "#semantic-video-post-" in source
    assert "handleSemanticDeliveryDecision" in source
    assert "'#publish-workflow'" in source
    assert "payload?.data?.batch_advanced" in source
    assert "event.detail?.elt || event.target" in source
    assert "decision.dataset.semanticDeliveryPostId" in source
    assert 'data-semantic-delivery-post-id="{{ item.post_id }}"' in template_source
    assert "hx-on::after-request" not in template_source
    assert "progress.estimated_remaining_seconds" in source
    assert "progress.progress_percent" in source
    assert "updateStatStatus(root, progress)" in source
    assert "generated-status" in source
    assert "verified-status" in source
    assert "generated-spinner" in template_source
    assert "verified-spinner" in template_source
    assert "Generating" in template_source
    assert "Verifying" in template_source
    assert "Typical time remaining" in template_source
    assert 'data-field="progress-spinner"' in template_source
    assert 'data-field="candidate-progress"' in template_source
    assert 'data-field="candidate-spinner"' in template_source
    assert 'data-field="candidate-status-label"' in template_source
    assert 'aria-label="Scene-plate generation progress"' in template_source
    assert 'data-field="plan-progress"' in template_source
    assert 'data-field="plan-spinner"' in template_source
    assert 'data-field="plan-takes-spinner"' in template_source
    assert 'data-field="plan-seconds-spinner"' in template_source
    assert 'data-field="plan-cost-spinner"' in template_source
    assert 'aria-label="Production plan build progress"' in template_source
    assert 'role="progressbar"' in template_source
    assert (
        "semantic_step in ['plan', 'production'] and item.stage == "
        "'retry_approval_required'"
    ) in template_source


def test_plan_step_offers_one_combined_approval_for_all_ready_videos():
    env = Environment(loader=FileSystemLoader("templates"))
    ready_posts = []
    for index in range(3):
        ready_posts.append(
            {
                "post_id": f"post-{index + 1}",
                "topic_title": f"Ready video {index + 1}",
                "revision": index + 2,
                "stage": "awaiting_paid_approval",
                "plan_hash": str(index + 1) * 64,
                "master_state": "approved",
                "master_hash_is_current": True,
                "initial_plan_is_approved": False,
                "requested_duration_seconds": 16,
                "delivery_duration_seconds": None,
                "take_count": 2,
                "billable_provider_seconds": 16,
                "price_per_provider_second_usd": "0.40",
                "estimated_cost_usd": "6.40",
                "generated_takes": 0,
                "verified_takes": 0,
                "failed_take_indexes": [],
                "retry_provider_seconds": 0,
                "retry_estimated_cost_usd": "0.00",
                "provider_prompts": [],
                "candidates": [],
            }
        )

    html = env.get_template("batches/detail/_semantic_video.html").render(
        batch={**_semantic_batch(), "state": "S4_SCRIPTED"},
        batch_view={
            "semantic_workflow": {"current_step": {"key": "plan"}},
            "semantic_video": {
                "requested_duration_seconds": 16,
                "posts": ready_posts,
            },
        },
    )

    assert 'data-action="approve-batch-plans"' in html
    assert 'data-ready-count="3"' in html
    assert 'data-cost-usd="19.20"' in html
    assert 'data-batch-id="batch-semantic"' in html
    assert "Approve &amp; generate all 3 videos · $19.20" in html
    assert html.count('data-action="approve-plan"') == 3


def test_pending_script_keeps_semantic_production_out_of_the_script_step(monkeypatch):
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda post_id: None,
    )
    batch = _semantic_batch()
    batch["state"] = "S2_SEEDED"
    batch["posts"][0]["seed_data"]["script_review_status"] = "pending"

    view = batch_handlers._build_batch_detail_view(batch)
    item = view["semantic_video"]["posts"][0]

    assert item["script_review_status"] == "pending"

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail/_semantic_video.html").render(
        batch=batch,
        batch_view=view,
    )

    assert view["semantic_workflow"]["current_step"]["key"] == "scripts"
    assert 'data-action="generate-candidates"' not in html
    assert "Create and approve the scene" not in html


def test_acoustic_plan_failure_renders_localized_paid_retry_for_legacy_evidence(
    monkeypatch,
):
    run = {
        "id": "run-acoustic-plan-failure",
        "revision": 14,
        "stage": "retry_approval_required",
        "requested_duration_seconds": 16,
        "plan_hash": "a" * 64,
        "master_hash": "b" * 64,
        "master_snapshot": {},
        "plan_snapshot": {
            "take_count": 2,
            "billable_provider_seconds": 16,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "6.40",
        },
        "artifact_manifest": {
            "qa_failure": {
                "stage": "acoustic_qa",
                "message": "Acoustic duration extension exceeds the seam energy limit.",
                "failed_take_indexes": [0, 1],
            },
            "pipeline_manifest": {
                "acoustic_plan_failure": {
                    "recommended_retry_take_indexes": [0, 1],
                }
            },
        },
    }
    attempts = [
        {
            "take_index": index,
            "attempt": 1,
            "submission_state": "qa_failed",
            "provider_duration_seconds": 8,
        }
        for index in range(2)
    ]
    approvals = [
        {"approval_type": "reference", "contract_hash": "b" * 64},
        {"approval_type": "initial_plan", "contract_hash": "a" * 64},
    ]
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda _post_id: run,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_attempts",
        lambda _run_id: attempts,
    )
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "list_approvals",
        lambda _run_id: approvals,
    )

    view = batch_handlers._build_batch_detail_view(_semantic_batch())
    item = view["semantic_video"]["posts"][0]
    assert item["qa_resume_available"] is False
    assert item["retry_provider_seconds"] == 16
    assert item["retry_estimated_cost_usd"] == "6.40"

    html = Environment(loader=FileSystemLoader("templates")).get_template(
        "batches/detail/_semantic_video.html"
    ).render(batch=_semantic_batch(), batch_view=view)
    assert 'data-action="approve-retry" data-cost-usd="6.40"' in html
    assert "Continue with generated videos · $0.00" not in html
