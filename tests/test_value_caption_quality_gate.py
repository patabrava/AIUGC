from types import SimpleNamespace

from app.features.topics import captions


class _HookyLLM:
    def generate_gemini_text(self, **_kwargs):
        return (
            "[curiosity]\n"
            "Rollstuhl im Kofferraum: Mach diesen Fehler nicht.\n\n"
            "[personal]\n"
            "Wenn du unterwegs bist, betrifft dich das direkt.\n\n"
            "[provocative]\n"
            "Viele machen es immer noch falsch."
        )


def test_value_caption_marks_review_required_for_thin_legacy_output(monkeypatch):
    monkeypatch.setattr(
        captions,
        "get_settings",
        lambda: SimpleNamespace(
            value_caption_informative_mode=False,
            value_caption_block_on_publish=False,
        ),
    )

    bundle = captions.generate_caption_bundle(
        topic_title="Rollstuhl im Kofferraum",
        post_type="value",
        script="Rollstuhl im Kofferraum sicher verstauen ist leichter, wenn du zwei Punkte beachtest.",
        research_facts=["Der Rollstuhl muss gesichert werden."],
        llm_factory=lambda: _HookyLLM(),
        seed_payload={
            "strict_seed": {"facts": ["Der Rollstuhl muss gesichert werden."]},
            "source": {"url": "https://www.bahn.de/service"},
            "source_urls": [{"url": "https://www.bahn.de/service"}],
        },
    )

    assert bundle["generation_mode"] == "standard_legacy"
    assert bundle["quality_status"] == "fail"
    assert bundle["quality_score"] < 60
    assert bundle["caption_review_required"] is True


def test_value_caption_quality_metadata_stays_non_blocking(monkeypatch):
    monkeypatch.setattr(
        captions,
        "get_settings",
        lambda: SimpleNamespace(
            value_caption_informative_mode=True,
            value_caption_block_on_publish=False,
        ),
    )

    bundle = captions.generate_caption_bundle(
        topic_title="Rollstuhl im Kofferraum",
        post_type="value",
        script="Rollstuhl im Kofferraum sicher verstauen ist leichter, wenn du zwei Punkte beachtest.",
        research_facts=["Der Rollstuhl muss gesichert werden."],
        llm_factory=lambda: _HookyLLM(),
        seed_payload={
            "strict_seed": {
                "facts": [
                    "Der Rollstuhl muss im Kofferraum gegen Verrutschen gesichert werden.",
                    "Lose Gurte werden bei einer Vollbremsung schnell zum Risiko.",
                ]
            },
            "source_summary": "Beim Einladen entstehen oft Schäden, wenn nichts gegen Verrutschen gesichert ist.",
            "source_urls": [{"url": "https://www.bahn.de/service"}],
        },
    )

    assert bundle["generation_mode"] == "fallback_informative"
    assert bundle["quality_status"] == "pass"
    assert bundle["caption_review_required"] is False
    assert "Mehr dazu" in bundle["selected_body"]

