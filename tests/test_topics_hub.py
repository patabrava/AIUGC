from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from app.features.topics import handlers as topic_handlers
from app.features.topics import hub as topic_hub
from app.features.topics.prompts import build_prompt1, build_prompt2
from app.features.topics.schemas import (
    ResearchAgentItem,
    ResearchAgentSource,
    ResearchDossier,
    ResearchLaneCandidate,
    SeedData,
)
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
    assert "HOOK-BANK (verbindlich):" not in prompt1
    assert "Lane-Titel: Mobilitätsservice richtig buchen" in prompt1
    assert "RESEARCH-KONTEXT FÜR DIE SKRIPTE:" in prompt2
    assert "HOOK-BANK (verbindlich):" in prompt2


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


def test_harvest_topics_to_bank_expands_every_research_lane(monkeypatch):
    stored_entries = []
    updated_runs = []
    variant_counts = {}

    monkeypatch.setattr(topic_hub, "create_topic_research_run", lambda **kwargs: {"id": "run-1"})
    monkeypatch.setattr(topic_hub, "update_topic_research_run", lambda run_id, **kwargs: updated_runs.append((run_id, kwargs)))
    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_hub, "pick_topic_bank_topics", lambda count, seed=None: ["Seed Thema"][:count] or ["Seed Thema"])

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
    monkeypatch.setattr(topic_hub, "generate_topic_research_dossier", lambda **kwargs: dossier)

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

    monkeypatch.setattr(topic_hub, "generate_topic_script_candidate", fake_generate_topic_script_candidate)
    def fail_if_prompt2_called(**kwargs):
        raise AssertionError(f"PROMPT_2 path should not run for value posts: {kwargs}")

    monkeypatch.setattr(topic_hub, "generate_dialog_scripts", fail_if_prompt2_called)
    monkeypatch.setattr(topic_hub, "extract_seed_strict_extractor", lambda topic_data: SeedData(facts=[topic_data.title], source_context="Kontext"))

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

    result = topic_hub.harvest_topics_to_bank_sync(
        post_type_counts={"value": 1},
        target_length_tier=8,
        trigger_source="test",
    )

    assert result["stored_by_type"]["value"] == 2
    assert [entry["title"] for entry in stored_entries] == [
        "Rampe vorher anmelden",
        "Entschädigung bei Ausfall prüfen",
    ]
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


def test_get_random_topic_returns_none_when_no_topics(monkeypatch):
    from app.features.topics import hub as topic_hub

    monkeypatch.setattr(topic_hub, "get_all_topics_from_registry", lambda: [])
    result = topic_hub.get_random_topic()
    assert result is None
