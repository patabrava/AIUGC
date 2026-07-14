from __future__ import annotations

import os

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
            "transcript_result": {"passed": index != 2},
            "identity_qa_result": {"passed": index != 5},
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
    assert item["estimated_cost_usd"] == "22.40"
    assert item["generated_takes"] == 7
    assert item["verified_takes"] == 6
    assert item["failed_take_indexes"] == [2, 5]
    assert item["retry_provider_seconds"] == 16
    assert item["retry_estimated_cost_usd"] == "6.40"
    assert item["latest_attempts"][2]["attempt"] == 1


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


def test_manual_semantic_projection_and_template_render_semantic_workflow(monkeypatch):
    monkeypatch.setattr(
        batch_handlers.semantic_video_queries,
        "get_run_by_post",
        lambda post_id: None,
    )
    batch = _semantic_batch()
    batch["creation_mode"] = "manual_semantic_ugc"

    view = batch_handlers._build_batch_detail_view(batch)
    assert view["semantic_video"] is not None

    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("batches/detail.html").render(
        batch=batch,
        batch_view=view,
        static_version="1",
    )
    assert "semantic-video-workflow" in html
    assert "semantic_video.js" in html


def test_semantic_partial_has_accessible_hash_gated_approval_controls():
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_semantic_video.html")
    html = template.render(
        batch=_semantic_batch(),
        batch_view={
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
                        "candidates": [
                            {"index": 1, "storage_uri": "https://cdn/one.png", "sha256": "1" * 64}
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
                    }
                ],
            }
        },
    )

    assert 'id="semantic-video-workflow"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-label="Select master candidate 1"' in html
    assert 'data-action="approve-master"' in html
    assert 'data-action="approve-plan"' in html
    assert 'data-action="approve-retry"' in html
    assert 'data-cost-usd="0.00"' in html
    assert "disabled" in html


def test_semantic_controller_confirms_exact_cost_and_polls_progress():
    source = open("static/js/batches/semantic_video.js", encoding="utf-8").read()

    assert "/progress" in source
    assert "confirm(" in source
    assert "data-cost-usd" in source
    assert "generated-takes" in source
    assert "verified-takes" in source
    assert "retry-approve" in source
    assert "master-approve" in source
