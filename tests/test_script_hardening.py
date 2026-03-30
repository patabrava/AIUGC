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
