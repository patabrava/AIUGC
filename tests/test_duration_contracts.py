from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.errors import ValidationError
from app.core.video_profiles import (
    get_script_duration_bounds,
    validate_script_duration_contract,
)
from app.features.topics.topic_validation import (
    detect_spoken_copy_issues,
    get_prompt1_word_bounds,
    get_prompt2_word_bounds,
    get_prompt3_word_bounds,
    validate_pre_persistence_topic_payload,
)


def _words(count: int) -> str:
    return " ".join(f"wort{i}" for i in range(count)) + "."


@pytest.mark.parametrize(
    "post_type,tier,expected",
    [
        ("value", 8, (14, 18)),
        ("value", 16, (26, 36)),
        ("value", 32, (54, 74)),
        ("lifestyle", 8, (16, 20)),
        ("lifestyle", 16, (20, 34)),
        ("lifestyle", 32, (64, 84)),
        ("product", 8, (16, 20)),
        ("product", 16, (24, 34)),
        ("product", 32, (32, 66)),
    ],
)
def test_canonical_script_duration_bounds(post_type, tier, expected):
    assert get_script_duration_bounds(post_type, tier) == expected


def test_legacy_prompt_bound_wrappers_use_canonical_contract():
    assert get_prompt1_word_bounds(32) == (68, 88)
    assert get_prompt2_word_bounds(32) == (64, 84)
    assert get_prompt3_word_bounds(32) == (32, 66)


def test_32s_lifestyle_24_word_script_is_rejected_by_contract():
    with pytest.raises(ValidationError) as exc:
        validate_script_duration_contract(
            script=_words(24),
            post_type="lifestyle",
            target_length_tier=32,
            row_id="post-incident",
            table="posts",
        )
    assert "24 words" in str(exc.value)
    assert exc.value.details["min_words"] == 64
    assert exc.value.details["max_words"] == 84


def test_32s_lifestyle_40_word_script_is_rejected_by_contract():
    with pytest.raises(ValidationError) as exc:
        validate_script_duration_contract(
            script=_words(40),
            post_type="lifestyle",
            target_length_tier=32,
            row_id="post-underlength",
            table="posts",
        )

    assert "40 words" in str(exc.value)
    assert exc.value.details["min_words"] == 64


def test_32s_lifestyle_64_word_script_passes_contract():
    result = validate_script_duration_contract(
        script=_words(64),
        post_type="lifestyle",
        target_length_tier=32,
        row_id="post-valid",
        table="posts",
    )
    assert result["status"] == "valid"
    assert result["word_count"] == 64


def test_spoken_copy_detector_allows_valid_particle_and_year_endings():
    assert detect_spoken_copy_issues("Probier es mal aus!") == []
    assert detect_spoken_copy_issues("Mehr als 20.000 zufriedene Kunden seit 1985.") == []


def test_value_variant_expansion_synthesizes_contract_safe_fallback_after_invalid_provider_copy(monkeypatch):
    import app.features.topics.variant_expansion as variants

    captured = []

    class FakeLLM:
        def generate_gemini_text(self, **_kwargs):
            return "Kurz."

    monkeypatch.setattr(variants, "get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(variants, "get_existing_variant_pairs", lambda **_kwargs: [])
    monkeypatch.setattr(
        variants,
        "get_topic_research_dossiers",
        lambda **_kwargs: [
            {
                "id": "dossier-1",
                "normalized_payload": {
                    "topic": "Assistive Technik",
                    "facts": ["Digitale Assistenz kann Barrieren im Alltag reduzieren."],
                    "lane_candidates": [
                        {
                            "title": "Digitale Assistenz im Alltag",
                            "framework_candidates": ["PAL"],
                            "facts": ["Digitale Assistenz kann Barrieren im Alltag reduzieren."],
                        }
                    ],
                    "framework_candidates": ["PAL"],
                },
            }
        ],
    )
    monkeypatch.setattr(variants, "_get_hook_style_names", lambda: ["direct"])
    monkeypatch.setattr(
        variants,
        "upsert_topic_script_variants",
        lambda **kwargs: captured.extend(kwargs["variants"]) or kwargs["variants"],
    )

    result = variants.expand_topic_variants(
        topic_registry_id="topic-1",
        title="Digitale Assistenz",
        post_type="value",
        target_length_tier=32,
    )

    assert result["generated"] == 1
    assert captured
    contract = validate_script_duration_contract(
        script=captured[0]["script"],
        post_type="value",
        target_length_tier=32,
    )
    assert contract["status"] == "valid"


def test_value_variant_expansion_does_not_store_script_as_source_summary(monkeypatch):
    import app.features.topics.variant_expansion as variants

    script = (
        "Rollstuhl sicher verstauen schuetzt dich unterwegs, weil lose Teile bei Bremsungen schnell gefaehrlich werden. "
        "Pruefe Gurt, Position und Akku, bevor du losfaehrst."
    )
    captured = []

    class FakeLLM:
        def generate_gemini_text(self, **_kwargs):
            return script

    monkeypatch.setattr(variants, "get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(variants, "get_existing_variant_pairs", lambda **_kwargs: [])
    monkeypatch.setattr(
        variants,
        "get_topic_research_dossiers",
        lambda **_kwargs: [
            {
                "id": "dossier-1",
                "normalized_payload": {
                    "topic": "Rollstuhl sicher verstauen",
                    "source_summary": script,
                    "facts": ["Lose Teile koennen bei Bremsungen gefaehrlich werden."],
                    "lane_candidates": [
                        {
                            "title": "Rollstuhl sicher verstauen",
                            "source_summary": script,
                            "framework_candidates": ["PAL"],
                            "facts": ["Lose Teile koennen bei Bremsungen gefaehrlich werden."],
                        }
                    ],
                    "framework_candidates": ["PAL"],
                },
            }
        ],
    )
    monkeypatch.setattr(variants, "_get_hook_style_names", lambda: ["direct"])
    monkeypatch.setattr(
        variants,
        "upsert_topic_script_variants",
        lambda **kwargs: captured.extend(kwargs["variants"]) or kwargs["variants"],
    )

    result = variants.expand_topic_variants(
        topic_registry_id="topic-1",
        title="Rollstuhl sicher verstauen",
        post_type="value",
        target_length_tier=16,
    )

    assert result["generated"] == 1
    assert validate_script_duration_contract(
        script=captured[0]["script"],
        post_type="value",
        target_length_tier=16,
    )["status"] == "valid"
    assert captured[0]["source_summary"] == ""


def test_pre_persistence_lifestyle_uses_32s_floor_without_padding():
    with pytest.raises(ValidationError) as exc:
        validate_pre_persistence_topic_payload(
            {"title": "Lifestyle", "topic": "Lifestyle", "script": _words(24)},
            target_length_tier=32,
            post_type="lifestyle",
        )
    assert exc.value.details["word_count"] == 24
    assert exc.value.details["min_words"] == 64


@patch("app.features.topics.queries.get_supabase")
def test_create_post_for_batch_rejects_underlength_32s_lifestyle(mock_get_sb):
    fake_client = MagicMock()
    mock_get_sb.return_value = type("SB", (), {"client": fake_client})()

    from app.features.topics.queries import create_post_for_batch

    with pytest.raises(ValidationError) as exc:
        create_post_for_batch(
            batch_id="batch-32",
            post_type="lifestyle",
            topic_title="Lifestyle",
            topic_rotation=_words(24),
            topic_cta="CTA",
            spoken_duration=8,
            seed_data={"script": _words(24), "script_review_status": "approved"},
            target_length_tier=32,
        )

    assert "lifestyle 32s script has 24 words" in str(exc.value)
    fake_client.table.assert_not_called()


@patch("app.features.topics.queries.get_supabase")
def test_create_post_for_batch_allows_empty_manual_draft(mock_get_sb):
    fake_client = MagicMock()
    fake_client.table.return_value.insert.return_value.execute.return_value = SimpleNamespace(
        data=[{"id": "draft-1"}]
    )
    mock_get_sb.return_value = type("SB", (), {"client": fake_client})()

    from app.features.topics.queries import create_post_for_batch

    post = create_post_for_batch(
        batch_id="batch-manual",
        post_type="value",
        topic_title="Manual Draft",
        topic_rotation="",
        topic_cta="",
        spoken_duration=0,
        seed_data={"script": "", "script_review_status": "pending", "manual_draft": True},
        target_length_tier=32,
    )

    assert post["id"] == "draft-1"
    fake_client.table.assert_called_once_with("posts")


def test_topic_suggestion_contract_rejects_stale_passed_bad_row(monkeypatch):
    import app.features.topics.queries as queries

    monkeypatch.setattr(
        queries,
        "get_all_topics_from_registry",
        lambda: [
            {
                "id": "topic-1",
                "title": "Stale Lifestyle",
                "script": _words(24),
                "canonical_topic": "Stale Lifestyle",
                "status": "active",
                "post_type": "lifestyle",
            }
        ],
    )
    monkeypatch.setattr(
        queries,
        "_fetch_topic_script_rows",
        lambda **_kwargs: [
            {
                "id": "script-1",
                "topic_registry_id": "topic-1",
                "title": "Stale Lifestyle",
                "script": _words(24),
                "target_length_tier": 32,
                "post_type": "lifestyle",
                "audit_status": "pass",
                "seed_payload": {"script": _words(24)},
            }
        ],
    )

    assert queries.list_topic_suggestions(target_length_tier=32, post_type="lifestyle") == []


def test_audit_classifies_generated_video_from_bad_script():
    from scripts.audit_duration_contracts import audit_post_row

    row = {
        "id": "post-1",
        "post_type": "lifestyle",
        "seed_data": {"script": _words(24), "target_length_tier": 32},
        "video_prompt_json": {"audio": {"dialogue": _words(24)}},
        "video_metadata": {"target_length_tier": 32},
        "video_url": "https://example.test/video.mp4",
        "video_status": "completed",
    }

    result = audit_post_row(row)

    assert result["status"] == "video_generated_from_bad_script"
    assert result["word_count"] == 24
    assert result["min_words"] == 64


def test_audit_treats_repaired_post_as_nonblocking():
    from scripts.audit_duration_contracts import audit_post_row

    row = {
        "id": "post-1",
        "post_type": "lifestyle",
        "seed_data": {
            "script": _words(24),
            "target_length_tier": 32,
            "duration_contract_status": "needs_repair",
        },
        "video_prompt_json": None,
        "video_metadata": {},
        "video_url": None,
        "video_status": "pending",
    }

    result = audit_post_row(row)

    assert result["status"] == "needs_repair"
    assert result["word_count"] == 24
    assert result["min_words"] == 64


def test_audit_treats_repaired_topic_script_as_nonblocking():
    from scripts.audit_duration_contracts import audit_topic_script_row

    row = {
        "id": "script-1",
        "post_type": "lifestyle",
        "target_length_tier": 32,
        "script": _words(24),
        "audit_status": "needs_repair",
    }

    result = audit_topic_script_row(row)

    assert result["status"] == "needs_repair"
    assert result["word_count"] == 24
    assert result["min_words"] == 64


def test_repair_tool_builds_safe_updates_without_apply():
    from scripts.repair_duration_contracts import build_repair_update

    topic_update = build_repair_update(
        {
            "table": "topic_scripts",
            "row_id": "script-1",
            "status": "underlength",
            "post_type": "lifestyle",
            "target_length_tier": 32,
            "word_count": 24,
            "min_words": 40,
            "max_words": 66,
        }
    )
    assert topic_update["table"] == "topic_scripts"
    assert topic_update["payload"]["audit_status"] == "needs_repair"
    assert "24 words" in topic_update["payload"]["quality_notes"]

    post_update = build_repair_update(
        {
            "table": "posts",
            "row_id": "post-1",
            "status": "underlength",
            "post_type": "lifestyle",
            "target_length_tier": 32,
            "word_count": 24,
            "min_words": 40,
            "max_words": 66,
            "has_video": False,
        }
    )
    assert post_update["table"] == "posts"
    assert post_update["payload"]["video_prompt_json"] is None
    assert post_update["seed_patch"]["script_review_status"] == "pending"
