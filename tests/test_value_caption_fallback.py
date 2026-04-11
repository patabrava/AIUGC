from types import SimpleNamespace

from app.features.topics import captions
from app.features.topics import queries


class _RaisingLLM:
    def generate_gemini_text(self, **_kwargs):
        raise RuntimeError("caption llm exploded")


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        row = dict(self.payload)
        row["id"] = "post-1"
        self.storage[self.table_name].append(row)
        return _FakeResponse([row])


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, table_name):
        return _FakeTable(self.storage, table_name)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


def test_value_caption_uses_deterministic_informative_fallback_on_llm_failure(monkeypatch):
    monkeypatch.setattr(
        captions,
        "get_settings",
        lambda: SimpleNamespace(
            value_caption_informative_mode=True,
            value_caption_block_on_publish=False,
        ),
    )

    payload = {
        "script": "Rollstuhl im Kofferraum sicher verstauen ist leichter, wenn du zwei Punkte beachtest.",
        "source_summary": "Viele Fehler passieren beim Einladen, weil der Rollstuhl nicht sauber gesichert wird.",
        "strict_seed": {
            "facts": [
                "Der Rollstuhl muss im Kofferraum gegen Verrutschen gesichert werden.",
                "Lose Gurte werden bei einer Vollbremsung schnell zum Risiko.",
            ]
        },
    }

    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Rollstuhl im Kofferraum",
        post_type="value",
        llm_factory=lambda: _RaisingLLM(),
    )

    bundle = enriched["caption_bundle"]
    assert bundle["generation_mode"] == "fallback_informative"
    assert bundle["quality_status"] == "pass"
    assert bundle["quality_score"] >= 80
    assert enriched["caption_review_required"] is False
    assert bundle["selected_body"] == enriched["caption"]
    assert "Mehr dazu" in bundle["selected_body"]
    assert bundle["selected_body"].count("\n") >= 4


def test_create_post_for_batch_persists_selected_publish_caption(monkeypatch):
    storage = {"posts": []}
    monkeypatch.setattr(queries, "supabase", _FakeSupabase(storage))

    seed_data = {
        "script": "Rollstuhl im Kofferraum sicher verstauen ist leichter, wenn du zwei Punkte beachtest.",
        "strict_seed": {
            "facts": [
                "Der Rollstuhl muss im Kofferraum gegen Verrutschen gesichert werden.",
                "Lose Gurte werden bei einer Vollbremsung schnell zum Risiko.",
            ]
        },
        "caption_bundle": {
            "selected_body": (
                "Rollstuhl im Kofferraum: Darauf kommt es an.\n"
                "Der Rollstuhl muss im Kofferraum gegen Verrutschen gesichert werden.\n"
                "Lose Gurte werden bei einer Vollbremsung schnell zum Risiko.\n"
                "Kurz gesagt: Sicherung spart Kraft und verhindert Schäden.\n"
                "Mehr dazu im Beitrag.\n"
                "#RollstuhlAlltag #BarriereFreiheit #Alltag"
            ),
            "selected_key": "informative",
            "generation_mode": "fallback_informative",
            "quality_score": 92,
            "quality_status": "pass",
        },
    }

    post = queries.create_post_for_batch(
        batch_id="batch-1",
        post_type="value",
        topic_title="Rollstuhl im Kofferraum",
        topic_rotation="Rollstuhl im Kofferraum sicher verstauen ist leichter, wenn du zwei Punkte beachtest.",
        topic_cta="Speicher dir das.",
        spoken_duration=5.0,
        seed_data=seed_data,
        target_length_tier=16,
    )

    assert post["publish_caption"] == seed_data["caption_bundle"]["selected_body"]
    assert storage["posts"][0]["publish_caption"] == seed_data["caption_bundle"]["selected_body"]

