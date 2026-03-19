from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import build_prompt1, build_prompt2
from app.features.topics import agents as topic_agents


PROMPT_DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "features" / "topics" / "prompt_data"


def test_prompt_text_files_exist_for_all_duration_tiers():
    expected = {
        "prompt1_8s.txt",
        "prompt1_16s.txt",
        "prompt1_32s.txt",
        "prompt2_8s.txt",
        "prompt2_16s.txt",
        "prompt2_32s.txt",
    }
    existing = {path.name for path in PROMPT_DATA_DIR.glob("prompt*.txt")}
    assert expected.issubset(existing)


def test_build_prompt1_uses_32s_text_template():
    prompt = build_prompt1(
        post_type="value",
        desired_topics=1,
        profile=get_duration_profile(32),
        assigned_topics=["Rollstuhltransport im Zug und Flugzeug"],
    )

    assert "3-4 concise sentences" in prompt
    assert "54-74 words" in prompt
    assert "24-28 Sekunden" in prompt
    assert "ZUFALLS-THEMEN FÜR DIESEN DURCHLAUF:" in prompt
    assert "core:" not in prompt


def test_build_prompt2_uses_16s_text_template():
    prompt = build_prompt2(
        topic="Barrierefreie Bahnreisen",
        scripts_per_category=5,
        profile=get_duration_profile(16),
    )

    assert "16-Sekunden-UGC-Videos" in prompt
    assert "24-34 Wörter" in prompt
    assert "1-2 concise sentences" in prompt
    assert "core:" not in prompt


def test_parse_prompt1_response_rejects_trailing_fragment(monkeypatch):
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

    with pytest.raises(ValidationError) as exc_info:
        topic_agents.parse_prompt1_response(raw, profile=profile)

    assert "incomplete fragment" in exc_info.value.message.lower()
