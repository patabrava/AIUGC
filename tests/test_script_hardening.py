"""Tests for script contamination hardening."""

from app.features.topics.topic_validation import _clean_fact_pool


def test_clean_fact_pool_strips_label_fragments():
    """Facts with research labels are rejected."""
    facts = [
        "Zentrale Erkenntnisse: Barrierefreiheit fehlt.",
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "Zentrale Erkenntnisse" not in result[0]
    assert "20 Prozent" in result[0]


def test_clean_fact_pool_strips_citation_residue():
    """Facts with citations are rejected."""
    facts = [
        "Barrierefreiheit ist gesetzlich vorgeschrieben [cite: 1].",
        "Die KfW foerdert barrierefreie Umbauten.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "KfW" in result[0]


def test_clean_fact_pool_rejects_short_fragments():
    """Fragments under 4 words are dropped."""
    facts = [
        "Nur zwei Prozent.",
        "Nur zwei Prozent aller Wohnungen sind rollstuhlgerecht.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "rollstuhlgerecht" in result[0]


def test_clean_fact_pool_deduplicates():
    """Duplicate facts are removed."""
    facts = [
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1


def test_clean_fact_pool_handles_none_and_empty():
    """None values and empty strings are silently dropped."""
    facts = [None, "", "  ", "Gute Arbeitshilfen halten dich im Job."]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "Arbeitshilfen" in result[0]


def test_prompt_research_context_sanitizes_facts():
    """Facts injected into the prompt must be sanitized."""
    from app.features.topics.prompts import _format_prompt1_research_context

    dossier = {
        "topic": "Barrierefreiheit",
        "seed_topic": "OEPNV",
        "source_summary": "Zentrale Erkenntnisse: Das PBefG fordert Barrierefreiheit.",
        "framework_candidates": ["PAL"],
    }
    lane = {
        "title": "Haltestellen",
        "facts": ["Leitende Zusammenfassung: Nur 20% barrierefrei.", "Rampen fehlen oft."],
        "risk_notes": ["Demografische Dringlichkeit: Bedarf steigt."],
        "framework_candidates": ["PAL"],
    }
    context = _format_prompt1_research_context(dossier, lane)
    assert "Zentrale Erkenntnisse" not in context
    assert "Leitende Zusammenfassung" not in context
    assert "Demografische Dringlichkeit" not in context
    assert "Rampen fehlen" in context


from app.features.topics.topic_validation import detect_metadata_bleed


def test_detect_metadata_bleed_catches_verbatim_summary():
    """Script containing 6+ consecutive words from summary is flagged."""
    script = "Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022."
    summary = "Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022 fuer den gesamten OEPNV."
    result = detect_metadata_bleed(script, source_summary=summary)
    assert result is not None
    assert result["kind"] == "metadata_bleed"


def test_detect_metadata_bleed_allows_partial_overlap():
    """Script sharing fewer than 6 consecutive words is OK."""
    script = "Dein Recht auf Mitfahrt existiert nur auf dem Papier."
    summary = "Das PBefG forderte vollstaendige Barrierefreiheit bis 2022."
    result = detect_metadata_bleed(script, source_summary=summary)
    assert result is None


def test_detect_metadata_bleed_checks_cluster_summary():
    """Cluster summary is also checked for bleed."""
    script = "Barrierefreiheit im OEPNV bleibt eine gesellschaftliche Herausforderung fuer alle Beteiligten."
    cluster = "Barrierefreiheit im OEPNV bleibt eine gesellschaftliche Herausforderung fuer alle Beteiligten und Verkehrsbetriebe."
    result = detect_metadata_bleed(script, cluster_summary=cluster)
    assert result is not None


def test_detect_metadata_bleed_empty_inputs():
    """Empty or None inputs return None."""
    assert detect_metadata_bleed("", source_summary="Foo bar baz.") is None
    assert detect_metadata_bleed("Script text.", source_summary="") is None
    assert detect_metadata_bleed("Script text.") is None
