"""Tests for the PROMPT_1 prompt builders."""

import pytest
from app.features.topics.prompts import build_prompt1, build_prompt1_variant, get_hook_bank
from app.features.topics import prompts as _prompts_mod


@pytest.fixture(autouse=True)
def _clear_hook_bank_cache():
    """Clear cached hook bank so tests pick up YAML changes."""
    _prompts_mod._load_hook_bank_payload.cache_clear()
    _prompts_mod.get_hook_bank.cache_clear()
    yield
    _prompts_mod._load_hook_bank_payload.cache_clear()
    _prompts_mod.get_hook_bank.cache_clear()


def test_build_prompt1_variant_includes_hook_bank():
    """Variant prompt includes hook bank section."""
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


def test_build_prompt1_includes_yaml_hook_bank_for_canonical_path():
    """Canonical PROMPT_1 must also inject the YAML hook bank."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "HOOK-BANK" in prompt
    assert "Fragen" in prompt
    assert "Du wirst nicht glauben" in prompt


def test_hook_bank_has_high_engagement_families():
    """Hook bank must include high-engagement families from the overhaul."""
    bank = get_hook_bank()
    family_names = [f["name"] for f in bank["families"]]
    required = [
        "Identitaet und Zugehoerigkeit",
        "Provokation und Faktenkonflikt",
        "Zahlen und Spezifitaet",
        "Absurditaet und Realitaetscheck",
        "Neugier und Alltagsfragen",
        "Fehler und Warnung",
    ]
    for name in required:
        assert name in family_names, f"Missing high-engagement family: {name}"


def test_hook_bank_bans_weak_starters():
    """Weak starters must be in the banned list."""
    bank = get_hook_bank()
    banned = bank["banned_patterns"]
    assert any("Heute erklaere ich" in b for b in banned)
    assert any("In diesem Video" in b for b in banned)
    assert any("Ich moechte euch zeigen" in b for b in banned)


def test_hook_bank_families_have_priority():
    """Every family must declare a priority tier."""
    bank = get_hook_bank()
    for family in bank["families"]:
        assert "priority" in family, f"Family '{family['name']}' missing priority"
        assert family["priority"] in ("high", "medium", "low"), (
            f"Family '{family['name']}' has invalid priority: {family['priority']}"
        )


def test_hook_bank_has_negative_examples():
    """Hook bank must include before/after negative examples."""
    bank = get_hook_bank()
    examples = bank.get("negative_examples", [])
    assert len(examples) >= 3, "Need at least 3 negative examples"
    for ex in examples:
        assert "bad" in ex, "Negative example missing 'bad' key"
        assert "good" in ex, "Negative example missing 'good' key"
        assert "why" in ex, "Negative example missing 'why' key"


def test_format_hook_bank_includes_priority_ordering():
    """High-priority families must appear before low-priority in rendered output."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    high_pos = prompt.find("Provokation und Faktenkonflikt")
    low_pos = prompt.find("Fragen (nur mit Punch")
    assert high_pos != -1, "High-priority family not found in prompt"
    assert low_pos != -1, "Low-priority family not found in prompt"
    assert high_pos < low_pos, "High-priority families must appear before low-priority"


def test_format_hook_bank_includes_negative_examples():
    """Rendered hook bank must include before/after examples."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "SCHLECHT:" in prompt
    assert "GUT:" in prompt
