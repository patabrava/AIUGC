"""Tests for the variant expansion diversity logic."""

from app.features.topics.variant_expansion import (
    pick_next_variant,
    LIFESTYLE_FRAMEWORKS,
    LIFESTYLE_HOOK_STYLES,
)


def test_pick_next_variant_returns_unused_combination():
    """With no existing scripts, picks first framework × first hook."""
    result = pick_next_variant(
        existing_pairs=[],
        available_frameworks=["PAL", "Testimonial", "Transformation"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    framework, hook_style = result
    assert framework in ["PAL", "Testimonial", "Transformation"]
    assert hook_style in ["question", "bold_claim", "story_opener"]


def test_pick_next_variant_skips_used_pairs():
    existing = [("PAL", "question")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim"],
        max_scripts=20,
    )
    assert result is not None
    assert result != ("PAL", "question")


def test_pick_next_variant_exhausted_returns_none():
    existing = [
        ("PAL", "question"),
        ("PAL", "bold_claim"),
        ("Testimonial", "question"),
        ("Testimonial", "bold_claim"),
    ]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim"],
        max_scripts=20,
    )
    assert result is None


def test_pick_next_variant_respects_max_cap():
    existing = [("PAL", "question"), ("PAL", "bold_claim")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=2,
    )
    assert result is None


def test_pick_next_variant_diversity_prefers_underrepresented_framework():
    existing = [("PAL", "question"), ("PAL", "bold_claim")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    framework, _ = result
    assert framework == "Testimonial"


def test_pick_next_variant_diversity_prefers_underrepresented_hook():
    existing = [
        ("PAL", "question"),
        ("Testimonial", "question"),
    ]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    _, hook_style = result
    assert hook_style != "question"


def test_lifestyle_constants_defined():
    assert len(LIFESTYLE_FRAMEWORKS) >= 3
    assert len(LIFESTYLE_HOOK_STYLES) >= 5
