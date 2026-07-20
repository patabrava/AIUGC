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
    assert 'aria-live="polite"' in html
    assert "Wheelchair scene plate candidates" in html
    assert 'aria-label="Select wheelchair scene plate candidate 1"' in html
    assert "Regenerate wheelchair scene plates" in html
    assert "Approve selected scene plate" in html
    assert "Frozen visual contract" in html
    assert "An accessible garden patio in soft daylight." in html
    assert "light-grey cardigan over a plain white top" in html
    assert "the same compact black manual wheelchair" in html
    assert "Actual provider prompts" in html
    assert "Continue the approved garden scene and preserve the grey cardigan." in html
    assert "standing, walking, different wheelchair" in html
    assert 'data-action="approve-master"' in html
    assert 'data-action="approve-plan"' in html
    assert 'data-action="approve-retry"' in html
    assert 'data-cost-usd="0.00"' in html
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


@pytest.mark.parametrize("creation_mode", ["semantic_ugc", "manual_semantic_ugc"])
def test_semantic_post_card_labels_stored_legacy_prompt_as_not_sent(creation_mode):
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
    )

    assert "Legacy Prompt Draft (not sent by Semantic pipeline)" in html
    assert "VEO Prompt (Sent to Provider)" not in html


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

    assert "/progress" in source
    assert "confirm(" in source
    assert "data-cost-usd" in source
    assert "generated-takes" in source
    assert "verified-takes" in source
    assert "retry-approve" in source
    assert "master-approve" in source
