from __future__ import annotations

import os
import re
from types import SimpleNamespace

import httpx
import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.topics import handlers, queries
from app.features.topics.semantic_scripts import (
    SemanticScriptResult,
    generate_semantic_script,
    validate_semantic_script,
)


def _seven_take_script() -> str:
    return " ".join(
        (
            "Barrierefreie Wege sparen täglich Kraft, weil klare Zugänge spontane Termine ohne zusätzliche Umwege zuverlässig möglich machen.",
            "Eine früh geprüfte Route zeigt Aufzüge, breite Türen und ruhige Übergänge schon vor der Abfahrt zuverlässig.",
            "Aktuelle Hinweise verhindern Überraschungen, wenn Baustellen oder defekte Aufzüge den bekannten Weg plötzlich spürbar verändern.",
            "Gespeicherte Alternativen geben Sicherheit, falls eine Verbindung ausfällt und kurzfristig eine andere Lösung gebraucht wird.",
            "Verlässliche Kontakte helfen direkt weiter, wenn Informationen fehlen oder Unterstützung vor Ort schnell organisiert werden muss.",
            "Ein kurzer Zugangscheck schützt Energie, damit der eigentliche Termin im Mittelpunkt bleibt und nicht die Anreise.",
            "Teile geprüfte Wege mit anderen, damit gute Lösungen schneller auffindbar werden und gemeinsam weiterhelfen können.",
        )
    )


def _candidate(post_type: str) -> dict:
    common = {
        "title": f"{post_type.title()} Thema für barrierefreie Wege",
        "rotation": "Interner kanonischer 32-Sekunden-Text.",
        "script": "Interner kanonischer 32-Sekunden-Text.",
        "cta": "Speichere dir die geprüfte Route.",
        "spoken_duration": 31.0,
        "target_length_tier": 32,
    }
    if post_type == "value":
        return {
            **common,
            "id": "family-value-32",
            "topic_registry_id": "family-value-32",
            "script_id": "script-value-32",
            "source_urls": [
                {"title": "Verkehrsverbund", "url": "https://example.test/barrierefrei"}
            ],
            "seed_payload": {
                "canonical_topic": common["title"],
                "facts": [
                    "Aufzugstatus und barrierefreie Alternativen lassen sich vor der Abfahrt prüfen."
                ],
            },
        }
    if post_type == "lifestyle":
        return {
            **common,
            "framework": "PAL",
            "dialog_scripts": SimpleNamespace(
                problem_agitate_solution=[common["script"]],
                testimonial=[common["script"]],
                transformation=[common["script"]],
                description="Ein Community-Thema über planbare barrierefreie Wege im Alltag.",
            ),
        }
    return {
        **common,
        "product_name": "Lippe Lift",
        "angle": "Planbare Mobilität",
        "facts": ["Der Lift schafft einen stufenlosen Zugang."],
        "support_facts": ["Die Bedienung ist für den Alltag ausgelegt."],
        "source_summary": "Freigegebene interne Produktkenntnis.",
        "framework": "PAL",
    }


@pytest.mark.parametrize("post_type", ["value", "lifestyle", "product"])
def test_semantic_discovery_generates_each_family_once_from_duration_neutral_inputs(
    monkeypatch,
    post_type,
):
    contract = build_semantic_duration_contract(50)
    candidate = _candidate(post_type)
    batch = {
        "id": f"batch-semantic-{post_type}",
        "state": "S1_SETUP",
        "creation_mode": "semantic_ugc",
        "target_duration_seconds": 50,
        "target_length_tier": None,
        "post_type_counts": {
            family: int(family == post_type)
            for family in ("value", "lifestyle", "product")
        },
        "actor_identity_snapshot": {"name": "Nora"},
    }

    monkeypatch.setattr(handlers, "get_batch_by_id", lambda _batch_id: batch)
    monkeypatch.setattr(handlers, "_batch_has_manual_drafts", lambda _batch_id: False)
    monkeypatch.setattr(handlers, "get_posts_by_batch", lambda _batch_id: [])
    monkeypatch.setattr(handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(handlers, "update_seeding_progress", lambda *args, **kwargs: {})
    monkeypatch.setattr(handlers, "mark_topic_family_used", lambda *args, **kwargs: None)
    monkeypatch.setattr(handlers, "mark_topic_script_used", lambda *args, **kwargs: None)
    monkeypatch.setattr(handlers, "deduplicate_topics", lambda values, *args, **kwargs: values)
    monkeypatch.setattr(handlers, "_build_script_variants", lambda **kwargs: [])
    monkeypatch.setattr(handlers, "upsert_topic_script_variants", lambda **kwargs: [])
    monkeypatch.setattr(
        handlers,
        "store_topic_bank_entry",
        lambda **kwargs: {"id": f"stored-{post_type}", "title": candidate["title"]},
    )

    input_tiers = []

    def fake_list_topic_suggestions(*, target_length_tier, limit, post_type, **_kwargs):
        input_tiers.append(target_length_tier)
        assert target_length_tier == 32
        assert _kwargs["duration_neutral"] is True
        assert _kwargs["check_accessibility"] is False
        return [candidate]

    def fake_generate_family(*, count, target_length_tier):
        input_tiers.append(target_length_tier)
        assert target_length_tier == 32
        return [candidate]

    monkeypatch.setattr(handlers, "list_topic_suggestions", fake_list_topic_suggestions)
    monkeypatch.setattr(handlers, "generate_lifestyle_topics", fake_generate_family)
    monkeypatch.setattr(handlers, "generate_product_topics", fake_generate_family)

    semantic_calls = []

    def fake_generate_semantic_script(**kwargs):
        semantic_calls.append(kwargs)
        return SemanticScriptResult(
            script=_seven_take_script(),
            contract_hash=contract.contract_hash,
            provenance={
                "source": "gemini",
                "post_type": post_type,
                "source_urls": [
                    item["url"] for item in candidate.get("source_urls", [])
                ],
                "research": {"canonical_input_tier": 32},
            },
        )

    monkeypatch.setattr(
        handlers,
        "generate_semantic_script",
        fake_generate_semantic_script,
        raising=False,
    )

    created_posts = []

    def fake_create_post_for_batch(**kwargs):
        created_posts.append(kwargs)
        return {"id": f"post-semantic-{post_type}", "post_type": kwargs["post_type"]}

    monkeypatch.setattr(handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(
        handlers,
        "update_batch_state",
        lambda batch_id, state: {**batch, "state": state.value},
    )

    result = handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 1
    assert input_tiers == [32]
    assert 50 not in input_tiers
    assert len(semantic_calls) == 1
    semantic_call = semantic_calls[0]
    assert semantic_call["requested_duration_seconds"] == 50
    assert semantic_call["post_type"] == post_type
    assert semantic_call["cta"] == candidate["cta"]
    assert semantic_call["facts"] == [candidate["script"]]
    assert "Nora" in semantic_call["actor_context"]

    created = created_posts[0]
    assert created["target_length_tier"] is None
    assert created["topic_rotation"] == _seven_take_script()
    assert created["spoken_duration"] == 50.0
    assert "target_length_tier" not in created["seed_data"]
    assert created["seed_data"]["target_duration_seconds"] == 50
    assert created["seed_data"]["semantic_duration_contract"] == contract.as_dict()
    assert created["seed_data"]["semantic_duration_contract_hash"] == contract.contract_hash
    assert created["seed_data"]["semantic_script_provenance"]["source"] == "gemini"
    assert created["seed_data"]["script_review_status"] == "pending"
    assert created["seed_data"]["semantic_minimum_take_count"] == 7
    assert created["seed_data"]["semantic_planned_take_count"] == 7
    assert len(created["seed_data"]["semantic_planned_beats"]) == 7
    if post_type == "value":
        assert created["seed_data"]["source_urls"] == candidate["source_urls"]


def test_semantic_provider_failure_builds_distinct_fact_aware_fallback():
    class FailingClient:
        def generate_gemini_text(self, **kwargs):
            request = httpx.Request("POST", "https://provider.invalid")
            raise httpx.ConnectError("offline", request=request)

    result = generate_semantic_script(
        post_type="product",
        title="Stufenloser Zugang",
        cta="Prüfe den passenden Zugang.",
        facts=[
            "Der Lift schafft einen stufenlosen Zugang für planbare Wege im Alltag.",
            "Die Bedienung ist für wiederkehrende Abläufe mit klaren Schritten ausgelegt.",
        ],
        requested_duration_seconds=50,
        llm_client=FailingClient(),
        actor_context="Nora spricht ruhig und direkt.",
        research_provenance={"canonical_input_tier": 32},
        source_urls=["https://example.test/product"],
    )

    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=50,
    )
    sentences = [part.strip().casefold() for part in re.split(r"(?<=[.!?])\s+", result.script)]
    assert result.provenance["source"] == "fallback"
    assert validation.planned_take_count == 7
    assert len(sentences) == len(set(sentences)) == 7


def test_semantic_post_insert_removes_internal_canonical_tier(monkeypatch):
    captured = {}

    class Query:
        def insert(self, payload):
            captured.update(payload)
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": "post-semantic", **captured}])

    adapter = SimpleNamespace(
        client=SimpleNamespace(table=lambda table: Query()),
    )
    monkeypatch.setattr(queries, "_get_supabase_adapter", lambda: adapter)

    queries.create_post_for_batch(
        batch_id="batch-semantic",
        post_type="value",
        topic_title="Dauerneutrales Thema",
        topic_rotation=_seven_take_script(),
        topic_cta="Mehr erfahren.",
        spoken_duration=50,
        seed_data={
            "script": _seven_take_script(),
            "target_duration_seconds": 50,
            "target_length_tier": 32,
        },
        target_length_tier=None,
    )

    assert "target_length_tier" not in captured["seed_data"]


def test_semantic_value_retry_resumes_only_missing_persisted_slots(monkeypatch):
    contract = build_semantic_duration_contract(50)
    batch = {
        "id": "batch-semantic-resume-value",
        "brand": "AYRA",
        "state": "S1_SETUP",
        "creation_mode": "semantic_ugc",
        "target_duration_seconds": 50,
        "target_length_tier": None,
        "post_type_counts": {"value": 3, "lifestyle": 0, "product": 0},
        "actor_identity_snapshot": {"name": "Nora"},
    }
    candidates = [
        {
            **_candidate("value"),
            "id": f"family-value-{index}",
            "family_id": f"family-value-{index}",
            "topic_registry_id": f"family-value-{index}",
            "script_id": f"script-value-{index}",
            "family_fingerprint": f"value-family-{index}",
            "title": title,
            "rotation": rotation,
            "script": rotation,
            "seed_payload": {
                "canonical_topic": title,
                "facts": [rotation],
            },
        }
        for index, (title, rotation) in enumerate(
            (
                (
                    "Rollstuhlrampe vor Reiseantritt prüfen",
                    "Die Einstiegshöhe entscheidet, welche mobile Rampe für die geplante Reise passt.",
                ),
                (
                    "Aufzugstatus am Bahnhof speichern",
                    "Ein gespeicherter Aufzugstatus macht kurzfristige Alternativrouten am Bahnhof sichtbar.",
                ),
                (
                    "Begleitservice frühzeitig buchen",
                    "Der gebuchte Begleitservice koordiniert Treffpunkt und Umstieg für die konkrete Verbindung.",
                ),
            ),
            start=1,
        )
    ]
    persisted_posts = []
    provider_calls = []
    fail_family_two = {"enabled": True}
    state_updates = []

    monkeypatch.setattr(handlers, "get_batch_by_id", lambda _batch_id: batch)
    monkeypatch.setattr(handlers, "_batch_has_manual_drafts", lambda _batch_id: False)
    monkeypatch.setattr(handlers, "get_posts_by_batch", lambda _batch_id: list(persisted_posts))
    monkeypatch.setattr(handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(handlers, "update_seeding_progress", lambda *args, **kwargs: {})
    monkeypatch.setattr(handlers, "mark_topic_family_used", lambda *args, **kwargs: None)
    monkeypatch.setattr(handlers, "mark_topic_script_used", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        handlers,
        "_attach_publish_captions",
        lambda *, seed_payload, **kwargs: seed_payload,
    )
    monkeypatch.setattr(
        handlers,
        "list_topic_suggestions",
        lambda **kwargs: list(candidates),
    )

    def fake_generate_semantic_script(**kwargs):
        provider_calls.append(kwargs["title"])
        if fail_family_two["enabled"] and kwargs["title"] == candidates[1]["title"]:
            raise RuntimeError("provider failed on semantic slot value:2")
        return SemanticScriptResult(
            script=_seven_take_script(),
            contract_hash=contract.contract_hash,
            provenance={"source": "gemini", "post_type": "value"},
        )

    def fake_create_post_for_batch(**kwargs):
        row = {
            "id": f"post-{len(persisted_posts) + 1}",
            "batch_id": batch["id"],
            "post_type": kwargs["post_type"],
            "topic_title": kwargs["topic_title"],
            "topic_rotation": kwargs["topic_rotation"],
            "topic_cta": kwargs["topic_cta"],
            "spoken_duration": kwargs["spoken_duration"],
            "seed_data": dict(kwargs["seed_data"]),
        }
        persisted_posts.append(row)
        return row

    monkeypatch.setattr(handlers, "generate_semantic_script", fake_generate_semantic_script)
    monkeypatch.setattr(handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(
        handlers,
        "update_batch_state",
        lambda batch_id, state: state_updates.append(state.value)
        or {**batch, "state": state.value},
    )

    with pytest.raises(RuntimeError, match="value:2"):
        handlers._discover_topics_for_batch_sync(batch["id"])

    assert provider_calls == [candidates[0]["title"], candidates[1]["title"]]
    assert len(persisted_posts) == 1

    fail_family_two["enabled"] = False
    provider_calls.clear()
    result = handlers._discover_topics_for_batch_sync(batch["id"])

    assert provider_calls == [candidates[1]["title"], candidates[2]["title"]]
    assert len(persisted_posts) == 3
    assert [post["seed_data"]["semantic_slot_id"] for post in persisted_posts] == [
        "value:1",
        "value:2",
        "value:3",
    ]
    assert {
        post["seed_data"]["semantic_family_identity"] for post in persisted_posts
    } == {"value-family-1", "value-family-2", "value-family-3"}
    assert state_updates == ["S2_SEEDED"]
    assert result["posts_created"] == 3
    assert result["state"] == "S2_SEEDED"
