"""Tests for the expand_topic_variants orchestration."""

from unittest.mock import MagicMock
from types import SimpleNamespace

from app.features.topics.variant_expansion import expand_topic_variants


def test_expand_topic_variants_generates_and_stores(monkeypatch):
    """Generates a variant and calls upsert."""
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"id": "dossier-1", "normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [{"title": "Lane 1", "framework_candidates": ["PAL"]}],
        }}],
    )

    stored = []
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.upsert_topic_script_variants",
        lambda **kw: stored.append(kw) or [],
    )

    mock_llm = MagicMock()
    mock_llm.generate_gemini_json.return_value = [{"topic": "Test", "script": "Test script das ist ein guter deutscher Skript fuer dich heute jetzt.", "caption": "Cap"}]
    mock_llm.generate_gemini_text.return_value = (
        "Problem-Agitieren-Lösung Ads\n\n"
        "Kennst du das Gefühl, wenn der Alltag im Rollstuhl dich mal wieder völlig unerwartet überrascht und du spontan umplanen musst?\n\n"
        "Beschreibung\n\n"
        "Ein ausführliches Test-Skript für Lifestyle-Inhalte mit genug Zeichen und Kontext für die Social-Media-Caption und noch mehr Wörter dazu."
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    mock_item = SimpleNamespace(
        topic="Test",
        script="Test script das ist ein guter deutscher Skript fuer dich heute jetzt.",
        caption="Cap",
        framework="PAL",
        source_summary="",
        estimated_duration_s=5,
        sources=[],
        tone="direkt",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )
    mock_batch = SimpleNamespace(items=[mock_item])
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.parse_prompt1_response",
        lambda raw, profile=None, **kwargs: mock_batch,
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=1,
    )
    assert result["generated"] == 1
    assert len(stored) == 1
    assert stored[0]["topic_research_dossier_id"] == "dossier-1"


def test_expand_topic_variants_expands_16s_script_to_tier_bounds(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"id": "dossier-1", "normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "facts": [
                "Taktile Infos und klare Umstiegswege helfen im Alltag deutlich.",
                "Begleitservice muss oft rechtzeitig vor der Fahrt angemeldet werden.",
            ],
            "risk_notes": ["Kurzfristige Änderungen sind weiterhin möglich."],
            "lane_candidates": [{"title": "Lane 1", "framework_candidates": ["PAL"], "facts": [
                "Stationäre Hilfe braucht Vorlauf und klare Zuständigkeiten.",
                "Bahnsteighilfe kann begrenzt verfügbar sein.",
            ]}],
        }}],
    )

    stored = []
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.upsert_topic_script_variants",
        lambda **kw: stored.append(kw) or [],
    )

    captured_prompts = []

    mock_llm = MagicMock()

    def fake_generate_text(*, prompt, system_prompt=None, **kwargs):
        captured_prompts.append(prompt)
        return "Brauchst du Hilfe am Bahnhof und weißt nicht genau wie du den Begleitservice rechtzeitig anmeldest damit alles klappt und du entspannt reisen kannst ohne Stress?"

    mock_llm.generate_gemini_text = fake_generate_text
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=16,
        count=1,
    )

    assert result["generated"] == 1
    assert len(stored) == 1
    assert len(captured_prompts) >= 1
    script_words = stored[0]["variants"][0]["script"].split()
    assert 24 <= len(script_words) <= 36


def test_expand_topic_variants_skips_exhausted_topic(monkeypatch):
    """Returns 0 generated when all variants are used."""
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [
            {"framework": "PAL", "hook_style": "question"},
            {"framework": "PAL", "hook_style": "bold_claim"},
            {"framework": "Testimonial", "hook_style": "question"},
            {"framework": "Testimonial", "hook_style": "bold_claim"},
        ],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"id": "d1", "normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [],
        }}],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_hook_bank",
        lambda: {"families": [
            {"name": "question", "examples": ["Was wäre wenn..."]},
            {"name": "bold_claim", "examples": ["Die Wahrheit ist..."]},
        ], "banned_patterns": []},
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=5,
    )
    assert result["generated"] == 0
