"""Tests for the variant-specific prompt builder."""

from app.features.topics.prompts import build_prompt1_variant


def test_build_prompt1_variant_includes_hook_bank():
    """Variant prompt includes hook bank section (unlike build_prompt1)."""
    prompt = build_prompt1_variant(
        post_type="value",
        desired_topics=1,
        forced_framework="Testimonial",
        forced_hook_style="question",
    )
    assert "HOOK-BANK" in prompt or "Hook" in prompt.lower()


def test_build_prompt1_variant_includes_forced_constraints():
    """Variant prompt includes the forced framework and hook style."""
    prompt = build_prompt1_variant(
        post_type="value",
        desired_topics=1,
        forced_framework="Testimonial",
        forced_hook_style="bold_claim",
    )
    assert "Testimonial" in prompt
    assert "bold_claim" in prompt


def test_build_prompt1_original_unchanged():
    """Original build_prompt1 still has empty hook_bank_section."""
    from app.features.topics.prompts import build_prompt1
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "HOOK-BANK" not in prompt
