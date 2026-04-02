from pathlib import Path

from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import build_prompt1, build_prompt1_batch, build_prompt2, build_prompt3, build_topic_research_prompt
from app.features.topics.schemas import ProductKnowledgeEntry
from app.features.topics import agents as topic_agents


PROMPT_DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "features" / "topics" / "prompt_data"


def test_prompt_text_files_exist_for_all_duration_tiers():
    expected = {
        "prompt1_8s.txt",
        "prompt1_16s.txt",
        "prompt1_32s.txt",
        "prompt1_batch.txt",
        "prompt1_normalization.txt",
        "prompt2_8s.txt",
        "prompt2_16s.txt",
        "prompt2_32s.txt",
        "prompt3_8s.txt",
        "prompt3_16s.txt",
        "prompt3_32s.txt",
    }
    existing = {path.name for path in PROMPT_DATA_DIR.glob("prompt*.txt")}
    assert expected.issubset(existing)


def test_build_prompt1_uses_32s_text_template():
    prompt = build_prompt1(
        post_type="value",
        desired_topics=1,
        profile=get_duration_profile(32),
        dossier={
            "topic": "Rollstuhltransport im Zug und Flugzeug",
            "seed_topic": "Rollstuhltransport",
            "source_summary": "Klare Regeln fuer Anmeldung, Akku und Schadensmeldung.",
            "facts": ["Akkuregeln und Voranmeldung entscheiden ueber den Ablauf."],
            "risk_notes": ["Fehlende Anmeldung kann Hilfe verzögern."],
            "framework_candidates": ["PAL"],
        },
        lane_candidate={
            "title": "Rollstuhltransport im Zug und Flugzeug",
            "lane_family": "mobilitaet",
            "angle": "Anmeldung und Hilfen vor Abfahrt.",
            "facts": ["Voranmeldung erleichtert Boarding und Umstieg."],
            "risk_notes": ["Kurzfristige Aenderungen kosten oft Zeit."],
            "framework_candidates": ["PAL"],
        },
    )

    assert "VIER natuerliche Sprechbloecke" in prompt
    assert "54-74 Woerter" in prompt
    assert "Lane-Titel:" in prompt
    assert "Nur der Scripttext" in prompt
    assert "Keine Zwischenueberschriften" in prompt
    assert "keine Zitate wie `[cite: 1]`" in prompt
    assert "HOOK-BANK" in prompt
    assert "Fragen" in prompt
    assert "Nur valides JSON-Array" not in prompt
    assert "caption" not in prompt
    assert "source_summary" not in prompt


def test_build_prompt1_batch_keeps_rotation_context():
    prompt = build_prompt1_batch(
        post_type="value",
        desired_topics=2,
        profile=get_duration_profile(16),
        assigned_topics=["Barrierefreie Bahnreisen", "BahnCard bei Schwerbehinderung"],
    )

    assert "ZUFALLS-THEMEN FÜR DIESEN DURCHLAUF:" in prompt
    assert "26-36 Woerter" in prompt
    assert "ZWEI natuerliche Sprechbloecke" in prompt
    assert "Barrierefreie Bahnreisen" in prompt


def test_build_prompt2_uses_32s_text_template():
    prompt = build_prompt2(
        topic="Barrierefreie Bahnreisen",
        scripts_per_category=5,
        profile=get_duration_profile(32),
    )

    assert "32-Sekunden-UGC-Videos" in prompt
    assert "40-66 Wörter" in prompt
    assert "4 Sprechbloecke" in prompt
    assert "core:" not in prompt


def test_build_prompt2_uses_16s_text_template():
    prompt = build_prompt2(
        topic="Barrierefreie Bahnreisen",
        scripts_per_category=5,
        profile=get_duration_profile(16),
    )

    assert "16-Sekunden-UGC-Videos" in prompt
    assert "24-34 Wörter" in prompt
    assert "2 Sprechbloecke" in prompt
    assert "core:" not in prompt


def _sample_product() -> ProductKnowledgeEntry:
    return ProductKnowledgeEntry(
        product_name="VARIO PLUS",
        source_label="PLATTFORMTREPPENLIFT T80",
        aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
        summary="Plattform oder Sitzlift auf derselben Schiene.",
        facts=[
            "Plattform oder Sitzlift auf derselben Schiene",
            "Tragfaehigkeit bis 300 kg",
            "Innen- und Aussenbereich",
        ],
        support_facts=[
            "100% Made in Germany",
            "5 Jahre Gewaehrleistung auf den gesamten Lift",
        ],
    )


def test_build_prompt3_uses_32s_text_template():
    prompt = build_prompt3(product=_sample_product(), profile=get_duration_profile(32))

    assert "32-Sekunden-UGC-Videos" in prompt
    assert "40-66 Woerter" in prompt
    assert "vier natuerlichen Sprechbloecken" in prompt
    assert "Antworte nicht in JSON" in prompt
    assert "LL12" in prompt


def test_parse_prompt1_response_normalizes_trailing_fragment(monkeypatch):
    monkeypatch.setattr(topic_agents, "validate_sources_accessible", lambda item: None)
    profile = get_duration_profile(32)
    raw = """{
      "items": [
        {
          "topic": "Barrierefrei wohnen unterwegs",
          "framework": "Transformation",
          "sources": [{"title": "Quelle", "url": "https://example.com"}],
          "script": "Erster Satz für dich. Zweiter Satz mit mehr Kontext. Abgeschnittener Rest ohne Punkt",
          "source_summary": "Zusätzlicher Kontext zu einer Unterkunfts-Option in Deutschland, die sich auf rollstuhlgerechte Wohnmobile und flexible Selbstversorgung bezieht. #ReisenOhneBarrieren #Rollstuhlurlaub #Camping",
          "estimated_duration_s": 27,
          "tone": "direkt, freundlich, empowernd, du-Form",
          "disclaimer": "Keine Rechts- oder medizinische Beratung."
        }
      ]
    }"""

    batch = topic_agents.parse_prompt1_response(raw, profile=profile)

    assert batch.items[0].script.endswith(".")


def test_parse_prompt1_response_accepts_minimal_stage3_contract(monkeypatch):
    monkeypatch.setattr(topic_agents, "validate_sources_accessible", lambda item: None)
    profile = get_duration_profile(16)
    raw = """{
      "items": [
        {
          "title": "Barrierefreie Arbeitshilfen im Job",
          "script": "Kennst du den Hebel fuer bessere Teilhabe? Gute Arbeitshilfen halten dich im Job und reduzieren Stress.",
          "caption": "Klare Arbeitshilfen machen den Alltag leichter und sichern Teilhabe. #Teilhabe #Arbeit #Barrierefrei"
        }
      ]
    }"""

    batch = topic_agents.parse_prompt1_response(raw, profile=profile)

    assert batch.items[0].topic == "Barrierefreie Arbeitshilfen im Job"
    assert batch.items[0].script.endswith(".")
    assert batch.items[0].caption.startswith("Klare Arbeitshilfen")
    assert batch.items[0].source_summary.startswith("Klare Arbeitshilfen")
    assert batch.items[0].framework == "PAL"
    assert batch.items[0].estimated_duration_s > 0


def test_prompt1_8s_contains_hook_mechanics():
    """8s prompt must contain explicit hook mechanics, not just 'klaren Hook-Start'."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "klaren Hook-Start" not in prompt, "Old vague hook instruction still present"
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt


def test_prompt1_16s_contains_hook_mechanics():
    """16s prompt must contain explicit hook mechanics."""
    prompt = build_prompt1(
        post_type="value", desired_topics=1, profile=get_duration_profile(16),
    )
    assert "klaren Hook" not in prompt, "Old vague hook instruction still present in 16s"
    assert "ZWEI natuerliche Sprechbloecke" in prompt
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt


def test_prompt1_32s_contains_hook_mechanics():
    """32s prompt must contain explicit hook mechanics."""
    prompt = build_prompt1(
        post_type="value", desired_topics=1, profile=get_duration_profile(32),
    )
    assert "klaren Hook" not in prompt, "Old vague hook instruction still present in 32s"
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt


def test_prompt2_hook_prefixes_include_new_families():
    """PROMPT_2 parser must recognize hooks from new bank families."""
    from app.features.topics.response_parsers import parse_prompt2_response

    raw = """## Problem-Agitieren-Lösung Ads
Das sagt dir dein Verkehrsbetrieb nicht, aber die Rampe fehlt seit Monaten.
Dieser Fehler kostet dich als Rollstuhlfahrerin Zeit und Nerven jeden Tag.
Nur 2 Prozent aller Wohnungen sind rollstuhlgerecht in ganz Deutschland.

## Testimonial Ads
Wie kommt man als Rollstuhlfahrerin eigentlich ins Flugzeug ohne fremde Hilfe?
Deutschland 2025 und du kommst trotzdem nicht in den verdammten Bus.
Als Rollstuhlnutzerin kennst du das Gefuehl wenn der Aufzug kaputt ist.

## Transformation Ads
Dein Recht auf Mitfahrt existiert auf dem Papier aber nicht an der Haltestelle.
POV: Du willst einfach nur in den Bus einsteigen und es geht nicht.
Warum ist Barrierefreiheit in Deutschland immer noch so verdammt schwer?
"""
    result = parse_prompt2_response(raw, max_per_category=5)
    assert len(result.problem_agitate_solution) >= 1
    assert len(result.testimonial) >= 1
    assert len(result.transformation) >= 1


def test_prompt1_8s_contains_new_word_range_and_guardrails():
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "16-18 Woerter" in prompt
    assert "Heute ist April 2026" in prompt
    assert "U+2014" in prompt
    assert "HARTE NORMEN" in prompt
    assert "SELF-CHECK VOR DEM ABSCHICKEN" in prompt


def test_prompt1_research_mentions_current_year_context():
    prompt = build_topic_research_prompt(
        seed_topic="BFSG",
        post_type="value",
        target_length_tier=8,
    )
    assert "Heute ist April 2026" in prompt
    assert "Seit 2025" in prompt


def test_hook_bank_examples_no_longer_contain_long_dashes_or_ab_2025():
    hook_bank = (PROMPT_DATA_DIR / "hook_bank.yaml").read_text(encoding="utf-8")
    assert "\u2014" not in hook_bank
    assert "\u2013" not in hook_bank
    assert "\u2015" not in hook_bank
    assert "\u2212" not in hook_bank
    assert "Ab 2025" not in hook_bank


def test_audit_prompt_reflects_new_8s_word_range():
    audit_prompt = (PROMPT_DATA_DIR / "audit_prompt.txt").read_text(encoding="utf-8")
    assert "8s: 14-18" in audit_prompt
