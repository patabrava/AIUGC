from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from app.features.topics import handlers as topic_handlers
from app.features.topics import hub as topic_hub
from app.features.topics.prompts import build_prompt1, build_prompt2
from app.features.topics.seed_builders import build_research_seed_data
from app.features.topics.schemas import (
    ResearchAgentItem,
    ResearchAgentSource,
    ResearchDossier,
    ResearchLaneCandidate,
)
from app.core.errors import ThirdPartyError
from app.core.video_profiles import get_duration_profile


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(topic_handlers.router)
    return TestClient(app)


def test_topics_endpoint_returns_json_for_api_clients(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [
                {"id": "topic-1", "title": "Barrierefreier Bahnalltag", "rotation": "Rotation", "cta": "CTA", "first_seen_at": "2026-03-21T00:00:00Z", "last_used_at": "2026-03-21T00:00:00Z", "use_count": 2},
            ],
            "total_topics": 1,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["topics"][0]["title"] == "Barrierefreier Bahnalltag"


def test_topics_endpoint_returns_html_for_browser_requests(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "Topics Hub" in response.text


def test_topics_hub_uses_fixed_height_desktop_panels(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "lg:h-[80vh]" in response.text
    assert 'id="launch-panel" class="bg-slate-50/50 p-5 sm:p-6 overflow-y-auto lg:h-full min-h-0"' in response.text


def test_topics_hub_script_cards_show_delete_button(monkeypatch):
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(topic_queries, "get_topic_registry_by_id", lambda topic_id: {"id": topic_id, "title": "Test Topic", "post_type": "value"})
    monkeypatch.setattr(
        topic_queries,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [
            {"id": "script-1", "script": "Script one", "target_length_tier": 8, "created_at": "2026-04-01T00:00:00Z", "use_count": 0, "source_urls": []},
            {"id": "script-2", "script": "Script two", "target_length_tier": 8, "created_at": "2026-04-01T00:00:00Z", "use_count": 1, "source_urls": []},
        ],
    )

    client = _build_test_client()
    response = client.get("/topics/topic-1/scripts-drawer", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert 'action="/topics/scripts/script-1/delete"' in response.text
    assert 'action="/topics/scripts/script-2/delete"' not in response.text
    assert "Fresh" in response.text
    assert "Used" in response.text


def test_topics_hub_delete_script_endpoint_refreshes_drawer(monkeypatch):
    from app.features.topics import queries as topic_queries

    deleted = {}

    monkeypatch.setattr(topic_handlers, "delete_topic_script", lambda script_id: deleted.setdefault("script_id", script_id) or "topic-1")
    monkeypatch.setattr(topic_queries, "get_topic_registry_by_id", lambda topic_id: {"id": topic_id, "title": "Test Topic", "post_type": "value"})
    monkeypatch.setattr(
        topic_queries,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )

    client = _build_test_client()
    response = client.post("/topics/scripts/script-1/delete", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert deleted["script_id"] == "script-1"
    assert "No scripts generated yet" in response.text


def test_topics_hub_delete_script_endpoint_blocks_used_scripts(monkeypatch):
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(topic_handlers, "delete_topic_script", lambda script_id: None)
    monkeypatch.setattr(topic_queries, "get_topic_registry_by_id", lambda topic_id: {"id": topic_id, "title": "Test Topic", "post_type": "value"})
    monkeypatch.setattr(topic_queries, "get_topic_scripts_for_registry", lambda topic_id, target_length_tier=None: [])

    client = _build_test_client()
    response = client.post("/topics/scripts/script-used/delete", headers={"accept": "text/html"})

    assert response.status_code == 409
    assert "Used scripts cannot be deleted" in response.text


def test_persist_topic_bank_row_applies_shared_quality_gate(monkeypatch):
    captured = {}

    def fake_store_topic_bank_entry(**kwargs):
        captured.update(kwargs)
        return {
            "id": "topic-1",
            "title": kwargs["title"],
            "topic_research_dossier_id": "dossier-1",
        }

    monkeypatch.setattr(topic_hub, "_build_script_variants", lambda **kwargs: [])
    monkeypatch.setattr(topic_hub, "store_topic_bank_entry", fake_store_topic_bank_entry)
    monkeypatch.setattr(topic_hub, "upsert_topic_script_variants", lambda **kwargs: [])

    topic_hub._persist_topic_bank_row(
        title="MSZ — Hilfe",
        target_length_tier=8,
        research_dossier={
            "topic": "MSZ — Hilfe",
            "source_summary": "Ab 2025 gilt die Hilfe am Bahnhof.",
            "disclaimer": "Keine Rechts- oder Medizinberatung.",
        },
        prompt1_item=SimpleNamespace(
            script="Ab 2025 gibt es endlich Hilfe — und du sparst im Alltag Stress.",
            caption="MSZ — Hilfe am Bahnhof.",
            source_summary="Ab 2025 gilt die Hilfe am Bahnhof.",
            disclaimer="Keine Rechts- oder Medizinberatung.",
        ),
        dialog_scripts=None,
        post_type="value",
        seed_payload={},
        variants=[],
    )

    assert "—" not in captured["title"]
    assert "—" not in captured["topic_script"]
    assert "Ab 2025" not in captured["topic_script"]
    assert "Seit 2025" in captured["topic_script"]


def test_topics_launch_endpoint_returns_json(monkeypatch):
    async def fake_launch_topic_research_run(**kwargs):
        return {
            "run": {"id": "run-1", "status": "running"},
            "topic": {"id": kwargs["topic_registry_id"], "title": "Barrierefreier Bahnalltag"},
            "status_url": "/topics/runs/run-1",
        }

    monkeypatch.setattr(topic_handlers, "launch_topic_research_run", fake_launch_topic_research_run)

    client = _build_test_client()
    response = client.post(
        "/topics/runs",
        data={
            "topic_registry_id": "topic-1",
            "target_length_tier": "16",
            "trigger_source": "hub",
            "post_type": "value",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["run"]["id"] == "run-1"
    assert payload["data"]["status_url"] == "/topics/runs/run-1"


def test_recover_stalled_topic_research_runs_requeues_recent_running_row(monkeypatch):
    from app.features.topics import hub as topic_hub

    run = {
        "id": "run-1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "trigger_source": "hub",
        "target_length_tier": 16,
        "topic_registry_id": "topic-1",
        "post_type": "value",
        "raw_prompt": "",
        "raw_response": "",
        "provider_interaction_id": None,
    }
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=25, status=None: [run])
    monkeypatch.setattr(topic_hub, "get_topic_registry_by_id", lambda topic_registry_id: {"id": topic_registry_id, "title": "Test Topic", "post_type": "value"})

    scheduled = []
    monkeypatch.setattr(
        topic_hub,
        "schedule_topic_research_run",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    recovered = topic_hub.recover_stalled_topic_research_runs(limit=1, max_age_hours=6)

    assert recovered == ["run-1"]
    assert scheduled[0]["run_row"]["id"] == "run-1"
    assert scheduled[0]["reason"] == "startup_recovery"


def test_prompt_builders_include_bank_and_research_context():
    dossier = {
        "cluster_id": "cluster-1",
        "topic": "Mobilitätsservice im Bahnalltag",
        "anchor_topic": "Mobilitätsservice im Bahnalltag",
        "seed_topic": "Mobilitätsservice im Bahnalltag",
        "cluster_summary": "Zusammenfassung",
        "framework_candidates": ["PAL"],
        "sources": [{"title": "DB", "url": "https://example.com/db"}],
        "source_summary": "Zusätzlicher Kontext aus der Quelle.",
        "facts": ["Begleitservice muss vorher gebucht werden."],
        "angle_options": ["Fehler vor der Reise vermeiden"],
        "risk_notes": ["Fristen je Bahnhof beachten."],
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
    }
    lane_candidate = {
        "lane_key": "lane-1",
        "lane_family": "checklist",
        "title": "Mobilitätsservice richtig buchen",
        "angle": "Fristen und Ablauf",
        "framework_candidates": ["PAL"],
        "source_summary": "Lane-spezifischer Kontext.",
        "facts": ["Die Buchung braucht Vorlauf."],
        "risk_notes": ["Kurzfristige Fahrten sind riskant."],
    }

    prompt1 = build_prompt1(
        post_type="value",
        desired_topics=1,
        profile=get_duration_profile(8),
        assigned_topics=["Mobilitätsservice richtig buchen"],
        dossier=dossier,
        lane_candidate=lane_candidate,
    )
    prompt2 = build_prompt2(
        topic="Mobilitätsservice richtig buchen",
        scripts_per_category=1,
        profile=get_duration_profile(8),
        dossier={**dossier, "lane_candidate": lane_candidate},
    )

    assert "DOSSIER-KONTEXT FÜR DIESEN DURCHLAUF:" in prompt1
    assert "HOOK-BANK (verbindlich):" in prompt1
    assert "Lane-Titel: Mobilitätsservice richtig buchen" in prompt1
    assert "RESEARCH-KONTEXT FÜR DIE SKRIPTE:" in prompt2
    assert "HOOK-BANK (verbindlich):" in prompt2


def test_build_research_seed_data_uses_normalized_dossier_facts():
    prompt1_item = ResearchAgentItem(
        topic="Barrierefreier Bahnalltag",
        script="Kennst du die richtige Buchung vor der Fahrt?",
        caption="Kurz gesagt: Vorlauf hilft.",
        framework="PAL",
        sources=[ResearchAgentSource(title="Quelle", url="https://example.com")],
        source_summary="Zusätzlicher Hinweis aus dem Dossier.",
        estimated_duration_s=8,
        tone="direkt, freundlich, empowernd, du-Form",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )

    seed = build_research_seed_data(
        prompt1_item=prompt1_item,
        research_dossier={
            "topic": "Barrierefreier Bahnalltag",
            "facts": ["Fakt aus der Normierung.", "Zweites Faktum."],
            "source_summary": "Normierter Dossier-Kontext.",
        },
    )

    assert seed.facts == ["Fakt aus der Normierung.", "Zweites Faktum."]
    assert seed.source_context == "Normierter Dossier-Kontext."


def test_topics_hub_groups_scripts_by_usage(monkeypatch):
    monkeypatch.setattr(
        topic_hub,
        "get_all_topics_from_registry",
        lambda: [
            {
                "id": "topic-1",
                "title": "Barrierefreier Bahnalltag",
                "script": "Rotation",
                "rotation": "Rotation",
                "cta": "CTA",
                "post_type": "value",
                "target_length_tiers": [16],
            }
        ],
    )
    monkeypatch.setattr(
        topic_hub,
        "get_topic_scripts_for_registry",
        lambda topic_registry_id, target_length_tier=None: [
            {
                "id": "script-1",
                "topic_registry_id": topic_registry_id,
                "title": "Barrierefreier Bahnalltag",
                "script": "Unbenutztes Skript.",
                "target_length_tier": 16,
                "use_count": 0,
                "created_at": "2026-03-21T00:00:00Z",
                "last_used_at": None,
            },
            {
                "id": "script-2",
                "topic_registry_id": topic_registry_id,
                "title": "Barrierefreier Bahnalltag",
                "script": "Genutztes Skript.",
                "target_length_tier": 16,
                "use_count": 3,
                "created_at": "2026-03-20T00:00:00Z",
                "last_used_at": "2026-03-21T08:00:00Z",
            },
        ],
    )
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=12, status=None: [])
    monkeypatch.setattr(topic_hub, "list_topic_suggestions", lambda **kwargs: [])

    payload_all = topic_hub.build_topic_hub_payload(
        SimpleNamespace(
            query_params={"topic_id": "topic-1", "script_usage": "all", "target_length_tier": "16"},
            headers={"accept": "text/html"},
        )
    )
    assert [group["key"] for group in payload_all["selected_script_groups"]] == ["unused", "used"]
    assert payload_all["selected_script_groups"][0]["count"] == 1
    assert payload_all["selected_script_groups"][1]["count"] == 1
    assert payload_all["selected_scripts"][0]["use_count"] == 0

    payload_unused = topic_hub.build_topic_hub_payload(
        SimpleNamespace(
            query_params={"topic_id": "topic-1", "script_usage": "unused", "target_length_tier": "16"},
            headers={"accept": "text/html"},
        )
    )
    assert [group["key"] for group in payload_unused["selected_script_groups"]] == ["unused"]
    assert len(payload_unused["selected_scripts"]) == 1
    assert payload_unused["selected_scripts"][0]["use_count"] == 0


def test_topics_hub_falls_back_to_topic_bank_when_registry_is_empty(monkeypatch):
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(
        topic_hub,
        "get_topic_bank",
        lambda: {
            "topics": [
                "Barrierefreiheit im ÖPNV-Alltag: Einstieg mit Rampe, Rollstuhlplätze, Ansagen, Begleitservice.",
                "Pflegegrad beantragen und Leistungen der Pflegeversicherung 2025 optimal nutzen.",
            ]
        },
    )
    monkeypatch.setattr(topic_hub, "get_topic_scripts_for_registry", lambda *args, **kwargs: [])
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=12, status=None: [])
    monkeypatch.setattr(topic_hub, "list_topic_suggestions", lambda **kwargs: [])

    payload = topic_hub.build_topic_hub_payload(
        SimpleNamespace(
            query_params={},
            headers={"accept": "text/html"},
        )
    )

    assert payload["total_topics"] == 2
    assert [topic["title"] for topic in payload["topics"]] == [
        "Barrierefreiheit im ÖPNV-Alltag: Einstieg mit Rampe, Rollstuhlplätze, Ansagen, Begleitservice.",
        "Pflegegrad beantragen und Leistungen der Pflegeversicherung 2025 optimal nutzen.",
    ]
    assert payload["selected_topic"]["id"].startswith("topic-bank-")
    assert payload["selected_topic"]["source"] == "topic_bank.yaml"


def test_topics_hub_marks_fresh_generated_topics_new_and_sorts_them_first(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(topic_hub, "get_topic_bank", lambda: {"topics": []})
    monkeypatch.setattr(
        topic_hub,
        "get_all_topics_from_registry",
        lambda: [
            {"id": "topic-older", "title": "Older topic", "post_type": "value", "last_harvested_at": (now - timedelta(days=3)).isoformat()},
            {"id": "topic-fresh-2", "title": "Fresh topic B", "post_type": "value", "last_harvested_at": (now - timedelta(hours=2)).isoformat()},
            {"id": "topic-fresh-1", "title": "Fresh topic A", "post_type": "value", "last_harvested_at": (now - timedelta(hours=1)).isoformat()},
        ],
    )
    monkeypatch.setattr(topic_hub, "_fetch_all_script_counts", lambda: {
        "topic-older": 1,
        "topic-fresh-1": 3,
        "topic-fresh-2": 2,
    })
    monkeypatch.setattr(topic_hub, "get_topic_scripts_for_registry", lambda *args, **kwargs: [])
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=12, status=None: [])
    monkeypatch.setattr(topic_hub, "list_topic_suggestions", lambda **kwargs: [])

    payload = topic_hub.build_topic_hub_payload(
        SimpleNamespace(
            query_params={"topic_mode": "generated"},
            headers={"accept": "text/html"},
        )
    )

    assert [topic["id"] for topic in payload["generated_topics"]] == ["topic-fresh-1", "topic-fresh-2", "topic-older"]
    assert [topic["is_new"] for topic in payload["generated_topics"]] == [True, True, False]
    assert [topic["script_count"] for topic in payload["generated_topics"]] == [3, 2, 1]


def test_topics_hub_html_renders_new_badge_for_fresh_generated_topics(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False, "topic_mode": "generated"},
            "topics": [],
            "basic_topics": [],
            "generated_topics": [
                {"id": "topic-1", "title": "Fresh topic", "post_type": "value", "script_count": 4, "is_new": True},
                {"id": "topic-2", "title": "Old topic", "post_type": "value", "script_count": 2, "is_new": False},
            ],
            "total_topics": 2,
            "basic_topic_count": 0,
            "generated_topic_count": 2,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "selected_script_groups": [],
            "script_usage_filter": "all",
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert ">New<" in response.text
    assert response.text.index("Fresh topic") < response.text.index("Old topic")


def test_harvest_topics_to_bank_expands_every_research_lane(monkeypatch):
    stored_entries = []
    updated_runs = []
    variant_counts = {}
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(topic_hub, "create_topic_research_run", lambda **kwargs: {"id": "run-1"})
    monkeypatch.setattr(topic_hub, "update_topic_research_run", lambda run_id, **kwargs: updated_runs.append((run_id, kwargs)))
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])

    dossier = ResearchDossier(
        cluster_id="cluster-1",
        topic="Barrierefreie Bahnreisen",
        anchor_topic="Barrierefreie Bahnreisen",
        seed_topic="Seed Thema",
        cluster_summary="Dichtes Cluster fuer barrierefreie Bahnreisen und relevante Alltagsrechte.",
        framework_candidates=["PAL"],
        sources=[
            ResearchAgentSource(title="Quelle 1", url="https://example.com/1"),
            ResearchAgentSource(title="Quelle 2", url="https://example.com/2"),
            ResearchAgentSource(title="Quelle 3", url="https://example.com/3"),
            ResearchAgentSource(title="Quelle 4", url="https://example.com/4"),
        ],
        source_summary="Die Quellenlage deckt Buchung, Hilfen am Bahnhof und Rechte bei Stoerungen umfassend ab.",
        facts=["Fakt 1", "Fakt 2", "Fakt 3"],
        angle_options=["Angle 1", "Angle 2", "Angle 3", "Angle 4"],
        risk_notes=["Hinweis 1"],
        disclaimer="Keine Rechts- oder medizinische Beratung.",
        lane_candidates=[
            ResearchLaneCandidate(
                lane_key="lane-a",
                lane_family="checklist",
                title="Rampe vorher anmelden",
                angle="Buchungsablauf",
                priority=1,
                framework_candidates=["PAL"],
                source_summary="Lane A mit klaren Bahn- und Buchungsdetails.",
                facts=["Lane A Fakt 1", "Lane A Fakt 2", "Lane A Fakt 3"],
                risk_notes=["Lane A Risiko"],
                disclaimer="Keine Rechts- oder medizinische Beratung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8],
            ),
            ResearchLaneCandidate(
                lane_key="lane-b",
                lane_family="mistakes",
                title="Entschädigung bei Ausfall prüfen",
                angle="Rechte bei Störung",
                priority=2,
                framework_candidates=["Transformation"],
                source_summary="Lane B mit belastbaren Infos zu Rechten bei Stoerungen.",
                facts=["Lane B Fakt 1", "Lane B Fakt 2", "Lane B Fakt 3"],
                risk_notes=["Lane B Risiko"],
                disclaimer="Keine Rechts- oder medizinische Beratung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8],
            ),
        ],
    )
    from app.features.topics import bank_warmup as topic_warmup

    monkeypatch.setattr(topic_warmup, "_generate_research_dossier_raw", lambda **kwargs: "# Forschungsdossier: Barrierefreie Bahnreisen\n\nRaw research prose.")
    monkeypatch.setattr(topic_warmup, "parse_topic_research_response", lambda raw, **kwargs: dossier)
    monkeypatch.setattr(topic_warmup, "touch_topic_registry", lambda *args, **kwargs: {})
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])

    def fake_generate_topic_script_candidate(**kwargs):
        lane = kwargs["lane_candidate"]
        return ResearchAgentItem(
            topic=lane["title"],
            framework=(lane.get("framework_candidates") or ["PAL"])[0],
            sources=[ResearchAgentSource(title="Primärquelle", url="https://example.com/primary")],
            script=f"Kennst du {lane['title'].lower()}? So sparst du Zeit im Bahnalltag heute.",
            source_summary=f"{lane['title']} basiert auf belastbaren Bahnquellen und klaren Prozessdetails. #Bahn #Barrierefrei #Mobilitaet",
            estimated_duration_s=5,
            tone="direkt, freundlich, empowernd, du-Form",
            disclaimer="Keine Rechts- oder medizinische Beratung.",
        )

    monkeypatch.setattr(topic_warmup, "generate_topic_script_candidate", fake_generate_topic_script_candidate)

    def fake_store_topic_bank_entry(**kwargs):
        stored_entries.append(kwargs)
        return {
            "id": f"topic-{len(stored_entries)}",
            "title": kwargs["title"],
            "research_dossier_id": f"dossier-{len(stored_entries)}",
            "topic_research_dossier_id": f"dossier-{len(stored_entries)}",
        }

    monkeypatch.setattr(topic_hub, "store_topic_bank_entry", fake_store_topic_bank_entry)
    variant_calls = []
    monkeypatch.setattr(
        topic_hub,
        "upsert_topic_script_variants",
        lambda **kwargs: (variant_calls.append(kwargs), variant_counts.setdefault(kwargs["title"], len(kwargs["variants"])))[1],
    )
    monkeypatch.setattr(topic_warmup, "get_topic_scripts_for_dossier", lambda *_args, **_kwargs: [{"id": "script-1"}, {"id": "script-2"}, {"id": "script-3"}])
    monkeypatch.setattr(topic_queries, "get_topic_scripts_for_dossier", lambda *_args, **_kwargs: [{"id": "script-1"}, {"id": "script-2"}, {"id": "script-3"}])

    result = topic_hub.harvest_topics_to_bank_sync(
        post_type_counts={"value": 1},
        target_length_tier=8,
        trigger_source="test",
    )

    assert result["stored_by_type"]["value"] == 6
    assert len(result["seed_topics_used"]) == 3
    assert len(set(result["seed_topics_used"])) == 3
    assert len(stored_entries) == 6
    assert {entry["title"] for entry in stored_entries} == {
        "Rampe vorher anmelden",
        "Entschädigung bei Ausfall prüfen",
    }
    assert all(entry["research_payload"]["topic"] == entry["title"] for entry in stored_entries)
    assert all(len(entry["research_payload"]["lane_candidates"]) == 1 for entry in stored_entries)
    assert all(call["topic_research_dossier_id"] for call in variant_calls)
    assert variant_counts["Rampe vorher anmelden"] == 3
    assert variant_counts["Entschädigung bei Ausfall prüfen"] == 3
    assert updated_runs[-1][1]["status"] == "completed"


def test_get_random_topic_returns_least_coverage(monkeypatch):
    """Random topic should return the topic with the fewest scripts."""
    from app.features.topics import hub as topic_hub

    fake_topics = [
        {"id": "t1", "title": "Topic A", "post_type": "value", "rotation": "r", "cta": "c"},
        {"id": "t2", "title": "Topic B", "post_type": "value", "rotation": "r", "cta": "c"},
        {"id": "t3", "title": "Topic C", "post_type": "lifestyle", "rotation": "r", "cta": "c"},
    ]
    script_counts = {"t1": 5, "t2": 0, "t3": 2}

    monkeypatch.setattr(
        topic_hub, "get_all_topics_from_registry", lambda: fake_topics
    )
    monkeypatch.setattr(
        topic_hub,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [{}] * script_counts.get(topic_id, 0),
    )

    result = topic_hub.get_random_topic()
    assert result is not None
    assert result["id"] == "t2"
    assert result["script_count"] == 0


def test_pick_topic_bank_topics_prefers_unseen_and_least_used(monkeypatch):
    from app.features.topics import prompts as topic_prompts
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_prompts,
        "get_topic_seed_catalog",
        lambda: ["Alpha topic", "Beta topic", "Gamma topic", "Delta topic"],
    )
    monkeypatch.setattr(
        topic_queries,
        "get_all_topics_from_registry",
        lambda: [
            {
                "id": "topic-alpha",
                "title": "Alpha topic",
                "canonical_topic": "Alpha topic",
                "family_fingerprint": "alpha topic",
                "post_type": "value",
                "use_count": 5,
                "last_used_at": "2026-03-31T10:00:00+00:00",
                "last_harvested_at": "2026-03-31T09:00:00+00:00",
            },
            {
                "id": "topic-beta",
                "title": "Beta topic",
                "canonical_topic": "Beta topic",
                "family_fingerprint": "beta topic",
                "post_type": "value",
                "use_count": 1,
                "last_used_at": "2026-03-20T10:00:00+00:00",
                "last_harvested_at": "2026-03-20T09:00:00+00:00",
            },
            {
                "id": "topic-gamma",
                "title": "Gamma topic",
                "canonical_topic": "Gamma topic",
                "family_fingerprint": "gamma topic",
                "post_type": "value",
                "use_count": 1,
                "last_used_at": "2026-03-29T10:00:00+00:00",
                "last_harvested_at": "2026-03-29T09:00:00+00:00",
            },
        ],
    )

    ranked = topic_prompts.pick_topic_bank_topics(4, seed=7, post_type="value")
    assert ranked == ["Delta topic", "Beta topic", "Gamma topic", "Alpha topic"]

    ranked_without_delta = topic_prompts.pick_topic_bank_topics(
        3,
        seed=7,
        post_type="value",
        exclude_topics=["Delta topic"],
    )
    assert ranked_without_delta == ["Beta topic", "Gamma topic", "Alpha topic"]


def test_get_random_topic_returns_none_when_no_topics(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    result = topic_hub.get_random_topic()
    assert result is None


def test_fuzzy_match_topic_finds_similar(monkeypatch):
    """Fuzzy match should find a similar existing topic."""
    from app.features.topics import hub as topic_hub

    fake_topics = [
        {"id": "t1", "title": "Hyaluronic Acid Benefits", "post_type": "value", "rotation": "Benefits of hyaluronic acid", "cta": "Try it"},
    ]
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: fake_topics)
    monkeypatch.setattr(
        topic_hub,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )

    result = topic_hub.fuzzy_match_topic("Hyaluronic Acid")
    assert result is not None
    assert result["id"] == "t1"


def test_fuzzy_match_topic_returns_none_for_novel_topic(monkeypatch):
    """Fuzzy match should return None when no similar topic exists."""
    from app.features.topics import hub as topic_hub

    fake_topics = [
        {"id": "t1", "title": "Hyaluronic Acid Benefits", "post_type": "value", "rotation": "Benefits of hyaluronic acid", "cta": "Try it"},
    ]
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: fake_topics)
    monkeypatch.setattr(
        topic_hub,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )

    result = topic_hub.fuzzy_match_topic("Morning Skincare Routine Tips")
    assert result is None


def test_build_launch_hub_payload_sorts_by_script_count(monkeypatch):
    """Launch hub payload should default to the basic topic bank."""
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(topic_hub, "_topic_bank_rows", lambda: [
        {"id": "topic-bank-1", "title": "Basic A", "post_type": "bank", "source": "topic_bank.yaml"},
        {"id": "topic-bank-2", "title": "Basic B", "post_type": "bank", "source": "topic_bank.yaml"},
    ])
    monkeypatch.setattr(
        topic_hub,
        "get_all_topics_from_registry",
        lambda: [
            {"id": "t1", "title": "A", "post_type": "value", "rotation": "r", "cta": "c", "created_at": "2026-01-01"},
            {"id": "t2", "title": "B", "post_type": "value", "rotation": "r", "cta": "c", "created_at": "2026-01-02"},
        ],
    )
    monkeypatch.setattr(topic_hub, "_fetch_all_script_counts", lambda: {"t1": 3, "t2": 0})
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=20, status=None, topic_registry_id=None: [])

    class FakeRequest:
        query_params = {}
        headers = {}

    result = topic_hub.build_launch_hub_payload(FakeRequest())
    assert result["topics"][0]["id"] == "topic-bank-1"
    assert result["topics"][1]["id"] == "topic-bank-2"
    assert result["generated_topics"][0]["id"] == "t2"
    assert result["generated_topics"][0]["script_count"] == 0
    assert result["generated_topics"][1]["id"] == "t1"
    assert result["generated_topics"][1]["script_count"] == 3


def test_build_launch_hub_payload_generated_mode(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(topic_hub, "_topic_bank_rows", lambda: [
        {"id": "topic-bank-1", "title": "Basic A", "post_type": "bank", "source": "topic_bank.yaml"},
    ])
    monkeypatch.setattr(
        topic_hub,
        "get_all_topics_from_registry",
        lambda: [
            {"id": "t1", "title": "Generated A", "post_type": "value", "rotation": "r", "cta": "c", "created_at": "2026-01-01"},
        ],
    )
    monkeypatch.setattr(topic_hub, "_fetch_all_script_counts", lambda: {"t1": 2})
    monkeypatch.setattr(topic_hub, "list_topic_research_runs", lambda limit=20, status=None, topic_registry_id=None: [])

    class FakeRequest:
        query_params = {"topic_mode": "generated"}
        headers = {}

    result = topic_hub.build_launch_hub_payload(FakeRequest())
    assert result["topics"][0]["id"] == "t1"
    assert result["topics"][0]["script_count"] == 2


def test_random_topic_endpoint(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(
        topic_hub,
        "get_random_topic",
        lambda: {"id": "t1", "title": "Random Topic", "post_type": "value", "script_count": 0, "rotation": "r", "cta": "c"},
    )
    client = _build_test_client()
    response = client.get("/topics/random", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Random Topic" in response.text


def test_random_topic_endpoint_empty(monkeypatch):
    import asyncio
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(topic_hub, "get_random_topic", lambda: None)
    scheduled = {}
    captured = {}

    monkeypatch.setattr(
        topic_handlers,
        "_schedule_topic_bank_research_if_empty",
        lambda **kwargs: (scheduled.setdefault("kwargs", kwargs), True)[1],
    )

    class FakeResponse:
        def __init__(self, template_name, context):
            captured["template_name"] = template_name
            captured["context"] = context
            self.headers = {}
            self.status_code = 200
            self.text = "No topics are available right now. A new research run is being started for value at 32s."

    monkeypatch.setattr(topic_handlers.templates, "TemplateResponse", lambda template_name, context: FakeResponse(template_name, context))

    class FakeRequest:
        query_params = {}
        headers = {"HX-Request": "true"}

    response = asyncio.run(topic_handlers.random_topic_endpoint(FakeRequest()))
    assert response.status_code == 200
    assert "No topics" in response.text
    assert scheduled["kwargs"] == {"post_type": "value", "target_length_tier": 32}
    assert "new research run is being started" in response.text
    assert captured["template_name"] == "topics/partials/confirmation_card.html"
    assert captured["context"]["research_triggered"] is True


def test_schedule_topic_bank_research_if_empty_launches_background_warmup(monkeypatch):
    captured = {}

    monkeypatch.setattr(topic_handlers, "count_selectable_topic_families", lambda **kwargs: 0)
    monkeypatch.setattr(topic_handlers, "list_topic_research_runs", lambda limit=25, status=None: [])

    def fake_harvest_topics_to_bank_sync(**kwargs):
        captured["kwargs"] = kwargs

    class FakeThread:
        def __init__(self, target, args=(), daemon=False):
            self.target = target
            self.args = args
            self.daemon = daemon

        def is_alive(self):
            return False

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(topic_handlers, "harvest_topics_to_bank_sync", fake_harvest_topics_to_bank_sync)
    monkeypatch.setattr(topic_handlers, "Thread", FakeThread)
    topic_handlers._TOPIC_BANK_RESEARCH_TASKS.clear()

    triggered = topic_handlers._schedule_topic_bank_research_if_empty(post_type="value", target_length_tier=32)

    assert triggered is True
    assert captured["kwargs"] == {
        "post_type_counts": {"value": 5},
        "target_length_tier": 32,
        "trigger_source": "topic_hub_empty_state",
    }


def test_coverage_warmup_retries_until_audited_coverage_is_ready(monkeypatch):
    from app.features.topics import handlers as topic_handlers
    import workers.audit_worker as audit_worker

    coverage_key = "value:32"
    topic_handlers._COVERAGE_WAITERS[coverage_key] = {"batch-1": 3}
    topic_handlers._COVERAGE_TASKS[coverage_key] = None  # type: ignore[assignment]

    harvest_calls = []
    audit_calls = []
    cleared = []
    progress_updates = []
    state = {"harvests": 0}

    def fake_count_selectable_topic_families(*, post_type, target_length_tier):
        return 3 if state["harvests"] >= 2 else 1

    def fake_harvest_topics_to_bank_sync(**kwargs):
        harvest_calls.append(kwargs)
        state["harvests"] += 1

    monkeypatch.setattr(topic_handlers, "count_selectable_topic_families", fake_count_selectable_topic_families)
    monkeypatch.setattr(topic_handlers, "harvest_topics_to_bank_sync", fake_harvest_topics_to_bank_sync)
    monkeypatch.setattr(audit_worker, "run_audit_cycle", lambda: audit_calls.append("audit"))
    monkeypatch.setattr(topic_handlers, "clear_seeding_progress", lambda batch_id: cleared.append(batch_id))
    monkeypatch.setattr(topic_handlers, "update_seeding_progress", lambda *args, **kwargs: progress_updates.append(kwargs))

    try:
        topic_handlers._run_coverage_warmup_task(coverage_key, "value", 32)
    finally:
        topic_handlers._COVERAGE_WAITERS.pop(coverage_key, None)
        topic_handlers._COVERAGE_TASKS.pop(coverage_key, None)

    assert len(harvest_calls) == 3
    assert len(audit_calls) == 3
    assert harvest_calls[0]["trigger_source"] != harvest_calls[1]["trigger_source"]
    assert harvest_calls[0]["post_type_counts"] == {"value": 5}
    assert harvest_calls[1]["post_type_counts"] == {"value": 6}
    assert harvest_calls[2]["post_type_counts"] == {"value": 7}
    assert cleared == ["batch-1"]
    assert progress_updates == []


def test_match_topic_endpoint_found(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(
        topic_hub,
        "fuzzy_match_topic",
        lambda q, threshold=0.35: {"id": "t1", "title": "Hyaluronic Acid Benefits", "post_type": "value", "script_count": 0, "similarity_score": 0.8},
    )
    client = _build_test_client()
    response = client.get("/topics/match?q=Hyaluronic", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Hyaluronic Acid Benefits" in response.text


def test_match_topic_endpoint_no_match(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(
        topic_hub, "fuzzy_match_topic", lambda q, threshold=0.35: None
    )
    client = _build_test_client()
    response = client.get("/topics/match?q=Something+New", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Something New" in response.text


def test_select_topic_endpoint(monkeypatch):
    from app.features.topics import hub as topic_hub
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_queries,
        "get_topic_registry_by_id",
        lambda tid: {"id": "t1", "title": "Selected Topic", "post_type": "value", "rotation": "r", "cta": "c"},
    )
    monkeypatch.setattr(
        topic_queries,
        "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )
    client = _build_test_client()
    response = client.get("/topics/select/t1", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Selected Topic" in response.text


def test_launch_research_with_new_topic_title(monkeypatch):
    """POST /topics/runs with new_topic_title should create a registry entry and launch."""
    created_topic = {"id": "new-t1", "title": "Brand New Topic", "post_type": "value", "rotation": "Brand New Topic", "cta": "Brand New Topic"}

    monkeypatch.setattr(
        topic_handlers,
        "add_topic_to_registry",
        lambda title, rotation, cta, post_type, **kwargs: created_topic,
    )

    launch_called_with = {}
    async def fake_launch(**kwargs):
        launch_called_with.update(kwargs)
        return {
            "run": {"id": "run-1", "status": "running"},
            "topic": created_topic,
            "status_url": "/topics/runs/run-1",
        }

    monkeypatch.setattr(topic_handlers, "launch_topic_research_run", fake_launch)

    client = _build_test_client()
    response = client.post(
        "/topics/runs",
        data={
            "new_topic_title": "Brand New Topic",
            "trigger_source": "hub",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert launch_called_with["topic_registry_id"] == "new-t1"


def test_pipeline_sync_runs_all_tiers_when_tier_is_none(monkeypatch):
    """When target_length_tier is None, pipeline should run one canonical harvest."""
    from app.features.topics import hub as topic_hub

    harvested_tiers = []
    updated_runs = []

    def fake_harvest(*, seed_topic, post_type, target_length_tier, existing_topics, collected_topics, progress_callback=None):
        harvested_tiers.append(target_length_tier)
        return {
            "seed_topic": seed_topic,
            "post_type": post_type,
            "requested_target_length_tier": target_length_tier,
            "tiers_processed": [8, 16, 32],
            "dossiers_completed": 1,
            "lanes_seen": 1,
            "lanes_persisted": 1,
            "scripts_persisted_by_tier": {"8": 1, "16": 1, "32": 1},
            "duplicate_scripts_skipped": 0,
            "stored_rows": [{"id": "topic-1", "title": "Test Topic"}],
            "stored_topic_ids": ["topic-1"],
            "seed_topics_used": ["Test Topic"],
        }

    monkeypatch.setattr(topic_hub, "_harvest_seed_topic_to_bank", fake_harvest)
    monkeypatch.setattr(
        topic_hub, "get_topic_registry_by_id",
        lambda tid: {"id": tid, "title": "Test Topic", "post_type": "value"},
    )
    monkeypatch.setattr(
        topic_hub, "get_all_topics_from_registry", lambda: [],
    )
    monkeypatch.setattr(
        topic_hub, "update_topic_research_run",
        lambda run_id, **kwargs: updated_runs.append((run_id, kwargs)),
    )

    topic_hub._run_topic_research_pipeline_sync(
        run_id="run-1",
        topic_registry_id="t1",
        target_length_tier=None,
        trigger_source="hub",
        post_type="value",
    )

    assert harvested_tiers == [None]
    assert updated_runs[-1][1]["result_summary"]["tiers_processed"] == [8, 16, 32]
    assert updated_runs[-1][1]["result_summary"]["stored_topic_ids"] == ["topic-1"]


def test_build_launch_hub_payload_includes_active_runs(monkeypatch):
    """Launch hub payload should include active research runs."""
    from app.features.topics import hub as topic_hub
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(
        topic_hub, "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )
    monkeypatch.setattr(
        topic_hub, "list_topic_research_runs",
        lambda limit=20, status=None, topic_registry_id=None: [
            {"id": "run-1", "status": "running", "result_summary": {"topic_title": "Test"}, "created_at": "2026-03-22", "updated_at": "2026-03-22", "target_length_tier": None, "trigger_source": "hub"},
            {"id": "run-2", "status": "completed", "result_summary": {"topic_title": "Done"}, "created_at": "2026-03-22", "updated_at": "2026-03-22", "target_length_tier": 8, "trigger_source": "hub"},
        ],
    )

    class FakeRequest:
        query_params = {}
        headers = {}

    result = topic_hub.build_launch_hub_payload(FakeRequest())
    assert "active_runs" in result
    assert len(result["active_runs"]) == 1
    assert result["active_runs"][0]["id"] == "run-1"


def test_run_status_compact_endpoint(monkeypatch):
    """GET /topics/runs/{id}?compact=1 should return compact status partial."""
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_queries, "get_topic_research_run",
        lambda run_id: {
            "id": "run-1", "status": "completed",
            "result_summary": {"topic_title": "Test Topic", "stored_count": 3, "tiers_processed": [8, 16, 32]},
            "error_message": None, "created_at": "2026-03-22", "updated_at": "2026-03-22",
            "target_length_tier": None, "trigger_source": "hub",
        },
    )
    client = _build_test_client()
    response = client.get("/topics/runs/run-1?compact=1", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Test Topic" in response.text


def test_run_status_card_shows_canonical_warmup(monkeypatch):
    """The expanded run card should surface canonical 8/16/32 coverage."""
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_queries, "get_topic_research_run",
        lambda run_id: {
            "id": "run-1",
            "status": "completed",
            "result_summary": {
                "topic_title": "Test Topic",
                "stored_count": 3,
                "tiers_processed": [8, 16, 32],
                "requested_target_length_tier": 16,
            },
            "error_message": None,
            "created_at": "2026-03-22",
            "updated_at": "2026-03-22",
            "target_length_tier": 16,
            "trigger_source": "hub",
        },
    )

    client = _build_test_client()
    response = client.get("/topics/runs/run-1", headers={"HX-Request": "true", "accept": "text/html"})

    assert response.status_code == 200
    assert "Canonical 8/16/32" in response.text


def test_shared_warmup_falls_back_to_synthesized_dossier(monkeypatch):
    """Provider deep-research failures should still complete via local dossier synthesis."""
    from app.features.topics import bank_warmup
    from app.features.topics import hub as topic_hub

    fallback_dossier = ResearchDossier(
        cluster_id="cluster-1",
        topic="Barrierefreie Wohnung nach DIN 18040",
        anchor_topic="Barrierefreie Wohnung nach DIN 18040",
        seed_topic="Barrierefreie Wohnung nach DIN 18040",
        cluster_summary="Barrierefreie Wohnungen brauchen klare Planung, verlässliche Maße und saubere Abläufe.",
        framework_candidates=["PAL"],
        sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
        source_summary="Die Wohnraumanpassung braucht im Alltag eine gute Abstimmung von Maß, Zugang und Ausstattung.",
        facts=["Wohnraumanpassung muss früh geplant werden.", "Barrierefreiheit hängt an konkreten Maßen."],
        angle_options=["Planung und Maße", "Zugang und Ausstattung"],
        risk_notes=["Zu enge Durchgänge blockieren Nutzung.", "Späte Änderungen verursachen Mehrkosten."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            ResearchLaneCandidate(
                lane_key="lane-1",
                lane_family="sub_angle",
                title="Planung und Maße",
                angle="Wie Maße und Wege die Nutzung bestimmen",
                priority=1,
                framework_candidates=["PAL"],
                source_summary="Planung und Maße entscheiden oft über die praktische Nutzbarkeit.",
                facts=["Planung muss vor dem Umbau stehen."],
                risk_notes=["Späte Anpassungen sind teuer."],
                disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8, 16, 32],
            )
        ],
    )

    monkeypatch.setattr(
        bank_warmup,
        "_generate_research_dossier_raw",
        lambda **kwargs: (_ for _ in ()).throw(ThirdPartyError("Gemini Deep Research polling failed", {"status_code": 500})),
    )
    parse_calls = []
    monkeypatch.setattr(
        bank_warmup,
        "parse_topic_research_response",
        lambda raw, **kwargs: parse_calls.append(raw) or fallback_dossier,
    )
    monkeypatch.setattr(bank_warmup.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bank_warmup,
        "generate_topic_script_candidate",
        lambda **kwargs: ResearchAgentItem(
            topic="Planung und Maße",
            script="Kennst du die richtige Planung fuer barrierefreie Wohnungen?",
            caption="Kurz gesagt: Planung entscheidet.",
            framework="PAL",
            sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
            source_summary="Planung entscheidet im Alltag über Nutzbarkeit.",
            estimated_duration_s=8,
            tone="direkt, freundlich, empowernd, du-Form",
            disclaimer="Keine Rechts- oder medizinische Beratung.",
        ),
    )
    monkeypatch.setattr(bank_warmup, "deduplicate_topics", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        topic_hub,
        "_persist_topic_bank_row",
        lambda **kwargs: {
            "stored_row": {
                "id": "topic-1",
                "title": kwargs["title"],
            },
            "stored_variants": [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}],
        },
    )

    summary = bank_warmup.run_single_seed_topic_warmup(
        seed_topic="Barrierefreie Wohnung nach DIN 18040",
        post_type="value",
        existing_topics=[],
        collected_topics=[],
    )

    assert parse_calls
    assert summary["research_source"] == "synthetic_fallback"
    assert summary["tiers_processed"] == [8, 16, 32]
    assert summary["stored_topic_ids"] == ["topic-1"]
    assert summary["lanes_persisted"] == 1


def test_shared_warmup_filters_near_duplicate_lane_candidates(monkeypatch):
    from app.features.topics import bank_warmup
    from app.features.topics import hub as topic_hub

    dossier = ResearchDossier(
        cluster_id="cluster-2",
        topic="Barrierefreie Arzttermine",
        anchor_topic="Barrierefreie Arzttermine",
        seed_topic="Barrierefreie Arzttermine",
        cluster_summary="Barrierefreie Arzttermine brauchen verlässliche Wege, Rückrufoptionen und klare Abläufe.",
        framework_candidates=["PAL"],
        sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
        source_summary="Formulare, Rückrufwege und Terminabstimmung entscheiden über alltagstaugliche Arzttermine.",
        facts=["Klare Rückrufwege sparen dir unnötige Schleifen.", "Digitale Terminwege müssen erreichbar bleiben."],
        angle_options=["Terminwege", "Rückrufwege"],
        risk_notes=["Ohne erreichbare Wege kippt der ganze Terminablauf."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            ResearchLaneCandidate(
                lane_key="lane-1",
                lane_family="sub_angle",
                title="Barrierefreie Terminwege",
                angle="Wie digitale und telefonische Wege den Arzttermin absichern",
                priority=1,
                framework_candidates=["PAL"],
                source_summary="Digitale und telefonische Wege sichern den Arzttermin im Alltag besser ab.",
                facts=["Klare Wege helfen dir schon vor dem Termin."],
                risk_notes=["Ohne Plan bleiben Termine unnötig fragil."],
                disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8, 16, 32],
            ),
            ResearchLaneCandidate(
                lane_key="lane-2",
                lane_family="sub_angle",
                title="Digitale Terminwege barrierefrei halten",
                angle="Warum erreichbare Formulare und Rückrufe denselben Terminweg absichern",
                priority=2,
                framework_candidates=["PAL"],
                source_summary="Erreichbare Formulare und Rückrufe sichern denselben Terminweg ebenfalls ab.",
                facts=["Erreichbare Wege helfen dir schon vor dem Termin."],
                risk_notes=["Ohne Plan bleiben Termine unnötig fragil."],
                disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8, 16, 32],
            ),
            ResearchLaneCandidate(
                lane_key="lane-3",
                lane_family="sub_angle",
                title="Rückrufoptionen ohne Wartefrust",
                angle="Wie klare Rückrufregeln deinen Termin nach Fehlversuchen retten",
                priority=3,
                framework_candidates=["PAL"],
                source_summary="Klare Rückrufregeln verhindern, dass ein Fehlversuch den ganzen Tag kippt.",
                facts=["Klare Rückrufregeln geben dir wieder Kontrolle."],
                risk_notes=["Ohne Rückrufoption versanden Anliegen schnell."],
                disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8, 16, 32],
            ),
        ],
    )

    monkeypatch.setattr(bank_warmup, "_generate_research_dossier_raw", lambda **kwargs: "raw")
    monkeypatch.setattr(bank_warmup, "parse_topic_research_response", lambda raw, **kwargs: dossier)
    monkeypatch.setattr(bank_warmup, "touch_topic_registry", lambda *args, **kwargs: {})

    seen_lane_keys = []

    def fake_generate_topic_script_candidate(**kwargs):
        lane = kwargs["lane_candidate"]
        seen_lane_keys.append(lane["lane_key"])
        return ResearchAgentItem(
            topic=lane["title"],
            script=f"{lane['title']} hilft dir, Termine, Rückrufe und Wege deutlich ruhiger zu ordnen.",
            caption="Kurze Zusammenfassung.",
            framework="PAL",
            sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
            source_summary=lane["source_summary"],
            estimated_duration_s=8,
            tone="direkt, freundlich, empowernd, du-Form",
            disclaimer="Keine Rechts- oder medizinische Beratung.",
        )

    monkeypatch.setattr(bank_warmup, "generate_topic_script_candidate", fake_generate_topic_script_candidate)
    monkeypatch.setattr(bank_warmup, "deduplicate_topics", lambda topics, *_args, **_kwargs: topics)
    monkeypatch.setattr(bank_warmup, "get_topic_scripts_for_dossier", lambda *_args, **_kwargs: [{"id": "script-1"}])
    monkeypatch.setattr(
        topic_hub,
        "_persist_topic_bank_row",
        lambda **kwargs: {
            "stored_row": {
                "id": f"topic-{kwargs['title']}",
                "title": kwargs["title"],
                "topic_research_dossier_id": f"dossier-{kwargs['title']}",
            },
            "stored_variants": [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}],
        },
    )

    summary = bank_warmup.run_single_seed_topic_warmup(
        seed_topic="Barrierefreie Arzttermine",
        post_type="value",
        existing_topics=[],
        collected_topics=[],
    )

    assert list(dict.fromkeys(seen_lane_keys)) == ["lane-1", "lane-3"]
    assert "lane-2" not in seen_lane_keys
    assert summary["lanes_seen"] == 3
    assert summary["lanes_persisted"] == 2


def test_shared_warmup_forces_one_lane_when_all_candidates_dedupe(monkeypatch):
    from app.features.topics import bank_warmup
    from app.features.topics import hub as topic_hub

    dossier = ResearchDossier(
        cluster_id="cluster-3",
        topic="Barrierefreie Terminwege",
        anchor_topic="Barrierefreie Terminwege",
        seed_topic="Barrierefreie Terminwege",
        cluster_summary="Klare Terminwege helfen im Alltag.",
        framework_candidates=["PAL"],
        sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
        source_summary="Klare Terminwege helfen im Alltag.",
        facts=["Klare Wege helfen dir vor dem Termin."],
        angle_options=["Terminwege"],
        risk_notes=["Ohne klare Wege wird alles zäh."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            ResearchLaneCandidate(
                lane_key="lane-1",
                lane_family="sub_angle",
                title="Barrierefreie Terminwege",
                angle="Wie digitale und telefonische Wege den Arzttermin absichern",
                priority=1,
                framework_candidates=["PAL"],
                source_summary="Digitale und telefonische Wege sichern den Arzttermin im Alltag besser ab.",
                facts=["Klare Wege helfen dir schon vor dem Termin."],
                risk_notes=["Ohne Plan bleiben Termine unnötig fragil."],
                disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
                lane_overlap_warnings=[],
                suggested_length_tiers=[8, 16, 32],
            ),
        ],
    )

    monkeypatch.setattr(bank_warmup, "_generate_research_dossier_raw", lambda **kwargs: "raw")
    monkeypatch.setattr(bank_warmup, "parse_topic_research_response", lambda raw, **kwargs: dossier)
    monkeypatch.setattr(bank_warmup, "touch_topic_registry", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        bank_warmup,
        "generate_topic_script_candidate",
        lambda **kwargs: ResearchAgentItem(
            topic="Barrierefreie Terminwege",
            script="Barrierefreie Terminwege helfen dir, Termine ruhiger zu planen.",
            caption="Kurze Zusammenfassung.",
            framework="PAL",
            sources=[ResearchAgentSource(title="Quelle 1", url="https://example.com")],
            source_summary="Klare Wege helfen dir schon vor dem Termin.",
            estimated_duration_s=8,
            tone="direkt, freundlich, empowernd, du-Form",
            disclaimer="Keine Rechts- oder Medizinberatung.",
        ),
    )
    monkeypatch.setattr(bank_warmup, "deduplicate_topics", lambda *args, **kwargs: [])

    persisted = []

    def fake_persist_topic_bank_row(**kwargs):
        persisted.append(kwargs["title"])
        return {
            "stored_row": {
                "id": f"topic-{len(persisted)}",
                "title": kwargs["title"],
                "topic_research_dossier_id": f"dossier-{len(persisted)}",
            },
            "stored_variants": [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}],
        }

    monkeypatch.setattr(topic_hub, "_persist_topic_bank_row", fake_persist_topic_bank_row)

    summary = bank_warmup.run_single_seed_topic_warmup(
        seed_topic="Barrierefreie Terminwege",
        post_type="value",
        existing_topics=[],
        collected_topics=[],
    )

    assert persisted == ["Barrierefreie Terminwege"]
    assert summary["lanes_persisted"] == 1
    assert summary["stored_topic_ids"] == ["topic-1"]


def test_launch_redirects_and_hub_shows_active_runs(monkeypatch):
    """After launching research, redirecting to /topics should show the active run."""
    from app.features.topics import hub as topic_hub
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(
        topic_hub, "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )
    monkeypatch.setattr(
        topic_hub, "list_topic_research_runs",
        lambda limit=20, status=None, topic_registry_id=None: [
            {"id": "run-1", "status": "running", "result_summary": {"topic_title": "Test"}, "created_at": "2026-03-22", "updated_at": "2026-03-22", "target_length_tier": None, "trigger_source": "hub"},
        ],
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "Running" in response.text


def test_pipeline_passes_progress_callback_to_harvest(monkeypatch):
    """Pipeline should pass progress_callback through to _harvest_seed_topic_to_bank."""
    from app.features.topics import hub as topic_hub

    received_callbacks = []

    def fake_harvest(*, seed_topic, post_type, target_length_tier, existing_topics, collected_topics, progress_callback=None):
        received_callbacks.append(progress_callback)
        return []

    monkeypatch.setattr(topic_hub, "_harvest_seed_topic_to_bank", fake_harvest)
    monkeypatch.setattr(topic_hub, "get_topic_registry_by_id", lambda tid: {"id": tid, "title": "Test", "post_type": "value"})
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_hub, "update_topic_research_run", lambda run_id, **kw: None)

    cb = lambda **kw: None
    topic_hub._run_topic_research_pipeline_sync(
        run_id="run-1",
        topic_registry_id="t1",
        target_length_tier=8,
        trigger_source="hub",
        post_type="value",
        progress_callback=cb,
    )

    assert len(received_callbacks) == 1
    assert received_callbacks[0] is cb


def test_full_launch_flow_pick_from_list(monkeypatch):
    """Full flow: select topic -> confirmation -> launch -> redirect."""
    from app.features.topics import hub as topic_hub
    from app.features.topics import queries as topic_queries
    from app.features.topics import handlers as topic_handlers

    test_topic = {"id": "t1", "title": "Test Topic", "post_type": "value", "rotation": "r", "cta": "c"}

    monkeypatch.setattr(topic_queries, "get_topic_registry_by_id", lambda tid: test_topic)
    monkeypatch.setattr(topic_queries, "get_topic_scripts_for_registry", lambda tid, target_length_tier=None: [])

    # Step 1: Select topic
    client = _build_test_client()
    select_response = client.get("/topics/select/t1", headers={"HX-Request": "true"})
    assert select_response.status_code == 200
    assert "Test Topic" in select_response.text
    assert "Launch Research" in select_response.text

    # Step 2: Launch research
    async def fake_launch(**kwargs):
        return {
            "run": {"id": "run-1", "status": "running"},
            "topic": test_topic,
            "status_url": "/topics/runs/run-1",
        }

    monkeypatch.setattr(topic_handlers, "launch_topic_research_run", fake_launch)

    launch_response = client.post(
        "/topics/runs",
        data={"topic_registry_id": "t1", "post_type": "value", "trigger_source": "hub"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert launch_response.status_code == 303
    assert launch_response.headers["location"] == "/topics"


def test_topic_run_stream_endpoint_returns_sse(monkeypatch):
    """GET /topics/runs/{id}/stream should return SSE content type."""
    from app.features.topics import handlers as topic_handlers

    monkeypatch.setattr(topic_handlers, "get_seeding_events", lambda run_id, last_event_id=None: [
        {"event_id": "1", "event_type": "interaction.complete", "created_at": "2026-03-22", "progress": {"stage": "completed"}}
    ])
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda run_id: {"stage": "completed"})

    client = _build_test_client()
    response = client.get("/topics/runs/run-1/stream")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


def test_scripts_drawer_endpoint_returns_grouped_scripts(monkeypatch):
    """GET /topics/{id}/scripts-drawer should return drawer HTML with scripts grouped by tier."""
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_queries, "get_topic_registry_by_id",
        lambda tid: {"id": "t1", "title": "Test Topic", "post_type": "value", "rotation": "r", "cta": "c"},
    )
    monkeypatch.setattr(
        topic_queries, "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [
            {"id": "s1", "script": "Script one text", "target_length_tier": 8, "source_urls": [{"title": "Src", "url": "https://example.com"}], "primary_source_url": None, "primary_source_title": None, "created_at": "2026-03-20", "use_count": 0},
            {"id": "s2", "script": "Script two text", "target_length_tier": 16, "source_urls": [], "primary_source_url": "https://example.com/alt", "primary_source_title": "Alt Source", "created_at": "2026-03-21", "use_count": 0},
        ],
    )

    client = _build_test_client()
    response = client.get("/topics/t1/scripts-drawer", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Test Topic" in response.text
    assert "Script one text" in response.text
    assert "Script two text" in response.text
    assert "8s" in response.text
    assert "16s" in response.text
    assert "open-scripts-drawer" in response.headers.get("hx-trigger", "")


def test_scripts_drawer_endpoint_empty_scripts(monkeypatch):
    """GET /topics/{id}/scripts-drawer with no scripts should show empty state."""
    from app.features.topics import queries as topic_queries

    monkeypatch.setattr(
        topic_queries, "get_topic_registry_by_id",
        lambda tid: {"id": "t1", "title": "Empty Topic", "post_type": "value", "rotation": "r", "cta": "c"},
    )
    monkeypatch.setattr(
        topic_queries, "get_topic_scripts_for_registry",
        lambda topic_id, target_length_tier=None: [],
    )

    client = _build_test_client()
    response = client.get("/topics/t1/scripts-drawer", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Empty Topic" in response.text
    assert "No scripts generated yet" in response.text
