"""Regression tests for Gemini-backed topic discovery."""

import asyncio
import json
import re
from types import SimpleNamespace
import httpx
import pytest

from app.adapters import llm_client as llm_client_module
from app.core.config import Settings
from app.features.topics import agents as topic_agents
from app.features.topics import handlers as topic_handlers
from app.features.topics.topic_validation import detect_metadata_bleed, detect_spoken_copy_issues


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, params=None, json=None, headers=None):
        self.calls.append(("POST", url, params, json, headers))
        return self.responses.pop(0)

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self.responses.pop(0)


class FakeStreamResponse:
    def __init__(self, status_code, lines, text="", error_after_lines=None):
        self.status_code = status_code
        self._lines = list(lines)
        self.text = text or "\n".join(lines)
        self.error_after_lines = error_after_lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        for index, line in enumerate(self._lines, start=1):
            yield line
            if self.error_after_lines is not None and index >= self.error_after_lines:
                raise httpx.ReadTimeout("idle stream timeout")


class FakeStreamingHttpClient(FakeHttpClient):
    def __init__(self, responses, stream_responses):
        super().__init__(responses)
        self.stream_responses = list(stream_responses)

    def stream(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(("STREAM", method, url, params, json, headers, timeout))
        return self.stream_responses.pop(0)


class FakeTopicLLM:
    def __init__(self):
        self.deep_research_prompts = []
        self.json_prompts = []
        self.text_prompts = []

    def generate_gemini_deep_research(self, prompt, system_prompt=None, **kwargs):
        self.deep_research_prompts.append((prompt, system_prompt, kwargs))
        return """
        [
          {
            "topic": "Pflegegrad 2025 prüfen",
            "framework": "PAL",
            "sources": [{"title": "Bundesgesundheitsministerium Pflege", "url": "https://www.bundesgesundheitsministerium.de/themen/pflege.html"}],
            "script": "Kennst du deinen Pflegegrad? So prüfst du 2025 deine Leistungen deutlich schneller.",
            "source_summary": "Das Bundesgesundheitsministerium erklärt, welche Leistungen die Pflegeversicherung umfasst, wie du Anträge stellst und welche Fristen wichtig sind. Gerade bei Pflegegrad-Änderungen lohnt sich ein genauer Blick auf Voraussetzungen, Nachweise und Beratungsangebote. #Pflegegrad #Pflegeversicherung #Rollstuhlalltag",
            "estimated_duration_s": 5,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          },
          {
            "topic": "Hilfsmittel richtig beantragen",
            "framework": "Testimonial",
            "sources": [{"title": "GKV Hilfsmittel", "url": "https://www.gkv-spitzenverband.de/krankenversicherung/hilfsmittel/hilfsmittel.jsp"}],
            "script": "Check dein Hilfsmittelrezept genau, dann vermeidest du Rückfragen und Versorgungslücken im Alltag.",
            "source_summary": "Der GKV-Spitzenverband erläutert, wie Hilfsmittel gelistet sind, welche Nachweise oft nötig werden und warum genaue Produktbeschreibungen den Antrag beschleunigen können. Gerade bei Rollstuhlversorgung hilft dir das, Ärzt:innen und Kostenträger sauber zu koordinieren. #Hilfsmittel #Rollstuhlversorgung #Krankenkasse",
            "estimated_duration_s": 5,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          },
          {
            "topic": "Begleitperson im Nahverkehr",
            "framework": "Transformation",
            "sources": [{"title": "DB Barrierefrei reisen", "url": "https://www.bahn.de/service/individuelle-reise/barrierefrei"}],
            "script": "Weißt du, wann Begleitpersonen gratis mitfahren? Mit Merkzeichen B reist du entspannter.",
            "source_summary": "Die Bahn beschreibt Unterstützungsangebote, Buchungswege und Voraussetzungen für barrierefreies Reisen. Für viele Fahrten lohnt sich der Blick auf Nachweise, Voranmeldung und Servicezeiten, damit du unterwegs weniger Stress hast und Begleitung sicher einplanen kannst. #Begleitperson #BarrierefreiReisen #Nahverkehr",
            "estimated_duration_s": 5,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          }
        ]
        """

    def generate_gemini_json(self, prompt, json_schema, system_prompt=None, **kwargs):
        self.json_prompts.append((prompt, json_schema, system_prompt, kwargs))
        if system_prompt and "PROMPT_1 stage-3" in system_prompt:
            raise AssertionError("Stage 3 must use text generation, not JSON generation")
        return {
            "facts": ["Pflegeleistungen müssen beantragt werden"],
            "source_context": "Pflegeversicherung 2025",
        }

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.text_prompts.append((prompt, system_prompt, kwargs))
        if system_prompt and "audit agent" in system_prompt.lower():
            return json.dumps(
                {
                    "total_score": 88,
                    "status": "pass",
                    "german_nativeness": {"score": 22},
                    "hook_quality": {"score": 22},
                    "prompt_compliance": {"score": 22},
                    "virality_potential": {"score": 22},
                },
                ensure_ascii=False,
            )
        if "PROMPT_1_STAGE3" in prompt or "finalen Scripttext" in prompt:
            return "Kennst du deinen Pflegegrad? Prüfe Unterlagen früher, dann sparst du Rückfragen und Zeit im Alltag."
        return """Problem-Agitieren-Lösung Ads

Schon mal erlebt, dass Anträge ewig dauern? Ich sammle jetzt Unterlagen früher, welche Strategie hilft dir dabei?

Beschreibung

Pflegeleistungen hängen oft an vollständigen Unterlagen, Beratungsstellen und klar dokumentierten Bedarfen. Wenn du Fristen und Nachweise kennst, sparst du dir unnötige Rückfragen und kannst Leistungen schneller absichern. #Pflegegrad #Antragshilfe #Rollstuhlalltag"""


def test_settings_allow_missing_openai_for_topics():
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="anon",
        supabase_service_key="service",
        openai_api_key="",
        google_ai_api_key="veo-key",
        gemini_api_key="gemini-key",
        cloudflare_r2_account_id="acct",
        cloudflare_r2_access_key_id="key",
        cloudflare_r2_secret_access_key="secret",
        cloudflare_r2_bucket_name="bucket",
        cloudflare_r2_public_base_url="https://cdn.example.com",
        cron_secret="cron-secret",
    )

    assert settings.openai_api_key == ""
    assert settings.gemini_api_key == "gemini-key"


def test_generate_gemini_deep_research_polls_until_done(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeHttpClient(
        [
            FakeResponse(200, {"name": "interactions/abc123"}),
            FakeResponse(200, {"name": "interactions/abc123", "status": "RUNNING"}),
            FakeResponse(200, {"name": "interactions/abc123", "status": "DONE", "outputs": [{"text": "research complete"}]}),
        ]
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    client = llm_client_module.LLMClient()
    progress_updates = []

    result = client.generate_gemini_deep_research(
        "Find current wheelchair topics",
        progress_callback=progress_updates.append,
    )

    assert result == "research complete"
    assert fake_gemini.calls[0][0] == "POST"
    assert fake_gemini.calls[1][0] == "GET"
    assert progress_updates[0]["provider_status"] == "SUBMITTED"
    assert any(update["provider_status"] == "RUNNING" for update in progress_updates)
    assert any(update["provider_status"] == "DONE" for update in progress_updates)


def test_generate_gemini_deep_research_streams_thought_summaries(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeStreamingHttpClient(
        [
            FakeResponse(
                200,
                {
                    "name": "interactions/abc123",
                    "status": "DONE",
                    "outputs": [{"text": "research complete"}],
                },
            )
        ],
        [
            FakeStreamResponse(
                200,
                [
                    'id: evt-1',
                    'event: interaction.start',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"}}',
                    "",
                    'id: evt-2',
                    'event: content.delta',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"delta":{"type":"thought_summary","content":{"text":"Planning the research approach."}}}',
                    "",
                    'id: evt-3',
                    'event: content.delta',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"delta":{"type":"text","text":"research complete"}}',
                    "",
                    'id: evt-4',
                    'event: interaction.complete',
                    'data: {"interaction":{"id":"interactions/abc123","status":"completed"}}',
                    "",
                ],
            )
        ],
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    client = llm_client_module.LLMClient()
    progress_updates = []

    result = client.generate_gemini_deep_research(
        "Find current wheelchair topics",
        progress_callback=progress_updates.append,
    )

    assert result == "research complete"
    assert fake_gemini.calls[0][0] == "STREAM"
    assert any(update["provider_status"] == "SUBMITTED" for update in progress_updates)
    assert any(update["detail_message"] == "Planning the research approach." for update in progress_updates)
    assert any(update["provider_status"] == "COMPLETED" for update in progress_updates)


def test_generate_gemini_deep_research_resumes_after_idle_timeout(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeStreamingHttpClient(
        [],
        [
            FakeStreamResponse(
                200,
                [
                    'event: interaction.start',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"event_type":"interaction.start"}',
                    "",
                    'event: interaction.status_update',
                    'data: {"interaction_id":"interactions/abc123","status":"in_progress","event_type":"interaction.status_update"}',
                    "",
                ],
                error_after_lines=6,
            ),
            FakeStreamResponse(
                200,
                [
                    'event: content.delta',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"event_type":"content.delta","delta":{"type":"thought_summary","content":{"text":"Comparing new topic angles against live sources."}}}',
                    "",
                    'event: content.delta',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"event_type":"content.delta","delta":{"type":"text","text":"research complete"}}',
                    "",
                    'event: interaction.complete',
                    'data: {"interaction":{"id":"interactions/abc123","status":"completed"},"event_type":"interaction.complete"}',
                    "",
                ],
            ),
        ],
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _seconds: None)
    client = llm_client_module.LLMClient()
    progress_updates = []

    result = client.generate_gemini_deep_research(
        "Find current wheelchair topics",
        progress_callback=progress_updates.append,
    )

    assert result == "research complete"
    stream_calls = [call for call in fake_gemini.calls if call[0] == "STREAM"]
    assert stream_calls[0][1] == "POST"
    assert stream_calls[1][1] == "GET"
    assert any("still reports the research interaction as in progress" in update["detail_message"] for update in progress_updates)
    assert any("paused event delivery" in update["detail_message"] for update in progress_updates)
    assert any(update["detail_message"] == "Comparing new topic angles against live sources." for update in progress_updates)


def test_generate_gemini_deep_research_falls_back_to_poll_after_repeated_stream_timeouts(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeStreamingHttpClient(
        [
            FakeResponse(200, {"name": "interactions/abc123"}),
            FakeResponse(200, {"name": "interactions/abc123", "status": "DONE", "outputs": [{"text": "research complete"}]}),
        ],
        [
            FakeStreamResponse(
                200,
                [
                    'event: interaction.start',
                    'data: {"interaction":{"id":"interactions/abc123","status":"in_progress"},"event_type":"interaction.start"}',
                    "",
                ],
                error_after_lines=1,
            ),
            FakeStreamResponse(
                200,
                [
                    'event: interaction.status_update',
                    'data: {"interaction_id":"interactions/abc123","status":"in_progress","event_type":"interaction.status_update"}',
                    "",
                ],
                error_after_lines=1,
            ),
            FakeStreamResponse(
                200,
                [
                    'event: interaction.status_update',
                    'data: {"interaction_id":"interactions/abc123","status":"in_progress","event_type":"interaction.status_update"}',
                    "",
                ],
                error_after_lines=1,
            ),
        ],
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _seconds: None)
    client = llm_client_module.LLMClient()
    progress_updates = []

    result = client.generate_gemini_deep_research(
        "Find current wheelchair topics",
        progress_callback=progress_updates.append,
    )

    assert result == "research complete"
    stream_calls = [call for call in fake_gemini.calls if call[0] == "STREAM"]
    get_calls = [call for call in fake_gemini.calls if call[0] == "GET"]
    assert len(stream_calls) == 1
    assert get_calls
    assert any(update["provider_status"] == "DONE" for update in progress_updates)


def test_generate_gemini_deep_research_retries_transient_poll_503(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeHttpClient(
        [
            FakeResponse(200, {"name": "interactions/abc123"}),
            FakeResponse(503, {"error": {"message": "temporarily unavailable"}}),
            FakeResponse(200, {"name": "interactions/abc123", "status": "DONE", "outputs": [{"text": "research complete"}]}),
        ]
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _seconds: None)
    client = llm_client_module.LLMClient()

    result = client.generate_gemini_deep_research("Find current wheelchair topics")

    assert result == "research complete"
    get_calls = [call for call in fake_gemini.calls if call[0] == "GET"]
    assert len(get_calls) == 2


def test_generate_gemini_deep_research_429_submission_surfaces_structured_error(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    fake_openai = FakeHttpClient([])
    fake_gemini = FakeHttpClient(
        [
            FakeResponse(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
        ]
    )
    clients = [fake_openai, fake_gemini]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    client = llm_client_module.LLMClient()

    with pytest.raises(llm_client_module.ThirdPartyError) as exc_info:
        client.generate_gemini_deep_research("Find current wheelchair topics")

    assert exc_info.value.details["status_code"] == 429
    assert exc_info.value.details["agent"] == "deep-research-pro-preview"


def test_to_gemini_response_schema_inlines_local_refs(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
        gemini_image_model="gemini-2.0-flash-preview-image-generation",
        gemini_deep_research_agent="deep-research-pro-preview",
        gemini_topic_timeout_seconds=30,
        gemini_topic_poll_seconds=0,
    )
    clients = [FakeHttpClient([]), FakeHttpClient([])]

    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module.httpx, "Client", lambda *args, **kwargs: clients.pop(0))
    client = llm_client_module.LLMClient()

    cleaned = client._to_gemini_response_schema(
        {
            "type": "object",
            "properties": {
                "source": {"$ref": "#/$defs/source"},
            },
            "$defs": {
                "source": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["title", "url"],
                    "additionalProperties": False,
                }
            },
        }
    )

    assert cleaned == {
        "type": "object",
        "properties": {
            "source": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["title", "url"],
            }
        },
    }


def test_topics_feature_uses_gemini_methods(monkeypatch):
    fake_llm = FakeTopicLLM()

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(topic_agents, "_validate_url_accessible", lambda url, timeout=5.0: True)

    items = topic_agents.generate_topics_research_agent(post_type="value", count=9)
    scripts = topic_agents.generate_dialog_scripts(topic="Pflegegrad 2025 prüfen", scripts_required=1)
    topic = topic_agents.convert_research_item_to_topic(items[0])
    seed = topic_agents.extract_seed_strict_extractor(topic)

    assert len(items) >= 3
    assert fake_llm.deep_research_prompts, "PROMPT_1 should use Gemini Deep Research"
    assert len(fake_llm.deep_research_prompts) == 5, "PROMPT_1 should widen seed-topic coverage for larger runs"
    assert fake_llm.text_prompts, "PROMPT_1 stage 3 should use Gemini text generation"
    assert fake_llm.json_prompts, "Strict extractor or normalizer should use Gemini JSON generation"
    assert scripts.problem_agitate_solution
    assert seed.facts == ["Pflegeleistungen müssen beantragt werden"]


def test_generate_topic_script_candidate_uses_duration_profile_import(monkeypatch):
    class FakeLaneLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            if system_prompt and "audit agent" in system_prompt.lower():
                return json.dumps(
                    {
                        "total_score": 90,
                        "status": "pass",
                        "german_nativeness": {"score": 23},
                        "hook_quality": {"score": 22},
                        "prompt_compliance": {"score": 23},
                        "virality_potential": {"score": 22},
                    },
                    ensure_ascii=False,
                )
            return "Kennst du den Begleitservice im Bahnhof? Mit Voranmeldung reist du entspannter."

    dossier = topic_agents.ResearchDossier(
        cluster_id="oepnv-begleitservice-01",
        topic="Begleitservice im Bahnhof",
        anchor_topic="Barrierefrei reisen",
        seed_topic="Barrierefrei reisen",
        cluster_summary="Das Dossier bündelt Fakten zu Voranmeldung, Servicezeiten und praktischer Hilfe beim Ein- und Ausstieg.",
        framework_candidates=["schritt-fuer-schritt"],
        sources=[{"title": "DB Barrierefrei", "url": "https://example.com/barrierefrei"}],
        source_summary="Die Quelle erklärt, wie du Unterstützung im Bahnhof rechtzeitig anmeldest und welche Hilfe konkret angeboten wird.",
        facts=["Begleitservice muss oft vorab angemeldet werden."],
        angle_options=["Voranmeldung im Alltag"],
        risk_notes=["Verfügbarkeit kann je nach Bahnhof schwanken."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            {
                "lane_key": "begleitservice",
                "lane_family": "value",
                "title": "Begleitservice im Bahnhof clever nutzen",
                "angle": "Voranmeldung und Alltagsnutzen.",
                "priority": 1,
                "framework_candidates": ["schritt-fuer-schritt"],
                "source_summary": "Die Quelle zeigt, wie du Hilfe am Bahnhof planbar und stressärmer nutzt.",
                "facts": ["Viele Hilfen erfordern Vorlauf."],
                "risk_notes": ["Kurzfristige Änderungen können Unterstützung verschieben."],
                "disclaimer": "Keine individuelle Rechts- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [8],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeLaneLLM())
    monkeypatch.setattr(topic_agents, "_validate_url_accessible", lambda url, timeout=5.0: True)

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=8,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    assert item.topic == "Begleitservice im Bahnhof clever nutzen"
    assert 5 <= item.estimated_duration_s <= 6


def test_generate_dialog_scripts_rejects_malformed_output_after_retries(monkeypatch):
    class FakeBrokenDialogLLM:
        def generate_gemini_json(self, prompt, json_schema, system_prompt=None, **kwargs):
            raise topic_agents.ValidationError(message="Broken JSON", details={})

        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return """Problem-Agitieren-Lösung Ads
            Was dir bei Dekubitus niemand klar sagt
            """

    dossier = {
        "topic": "Dekubitus-Dokumentation im Alltag",
        "cluster_summary": "Das Dossier erklärt Risiken, Dokumentation und klare Routinen im Pflegealltag.",
        "source_summary": "Klare Dokumentation, feste Routinen und nachvollziehbare Nachweise senken Stress, Rückfragen und spätere Haftungsrisiken im Alltag.",
        "facts": ["Dokumentation macht Risiken und Maßnahmen nachvollziehbar."],
        "risk_notes": ["Fehlende Nachweise erhöhen den Druck im Schadensfall."],
        "lane_candidate": {
            "title": "Haftungsfalle Dekubitus im Alltag",
        },
    }

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeBrokenDialogLLM())

    with pytest.raises(topic_agents.ValidationError, match="PROMPT_2 generation failed"):
        topic_agents.generate_dialog_scripts(
            topic="Haftungsfalle Dekubitus im Alltag",
            scripts_required=2,
            dossier=dossier,
            profile=topic_agents.get_duration_profile(8),
        )


def test_generate_topic_script_candidate_synthesizes_fallback_on_empty_text(monkeypatch):
    class FakeBrokenPrompt1LLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "   "

    dossier = topic_agents.ResearchDossier(
        cluster_id="teilhabe-beirat-01",
        topic="Behindertenbeirat mit Wirkung",
        anchor_topic="Kommunale Teilhabe",
        seed_topic="Kommunale Teilhabe",
        cluster_summary="Das Dossier erklärt Rechte, Mitsprache und feste Routinen für wirksame Behindertenbeiräte.",
        framework_candidates=["PAL"],
        sources=[{"title": "Kommunale Teilhabe", "url": "https://example.com/teilhabe"}],
        source_summary="Klare Rechte, feste Abläufe und nachvollziehbare Anträge entscheiden darüber, ob kommunale Teilhabe im Alltag tatsächlich Wirkung entfaltet.",
        facts=["Antragsrechte erhöhen den Einfluss in kommunalen Gremien."],
        angle_options=["Rechte sichern"],
        risk_notes=["Ohne klare Rechte bleibt Mitsprache oft symbolisch."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            {
                "lane_key": "beirat",
                "lane_family": "value",
                "title": "Behindertenbeirat mit Wirkung",
                "angle": "Rechte und Routinen.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "Die Quelle zeigt, wie Rechte, Anträge und feste Abläufe kommunale Mitsprache belastbarer machen.",
                "facts": ["Klare Rechte stärken Mitsprache."],
                "risk_notes": ["Ohne Zuständigkeiten versanden Anträge."],
                "disclaimer": "Keine individuelle Rechts- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [8],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeBrokenPrompt1LLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=8,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    assert item.topic == "Behindertenbeirat mit Wirkung"
    assert item.script.endswith(".")
    assert 12 <= len(item.script.split()) <= 15
    assert "für mehr klarheit im alltag" not in item.script.lower()
    assert "damit du sicherer entscheiden kannst" not in item.script.lower()


def test_generate_topic_script_candidate_expands_short_16s_script_to_tier_bounds(monkeypatch):
    class FakeShort16sLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "Exoskelette helfen dir im Alltag sofort."

    dossier = topic_agents.ResearchDossier(
        cluster_id="exo-16s-01",
        topic="Exoskelette im Alltag",
        anchor_topic="Exoskelette im Alltag",
        seed_topic="Exoskelette im Alltag",
        cluster_summary="Dossier mit Einsatzfeldern, Grenzen und Alltagseffekten von Exoskeletten.",
        framework_candidates=["PAL"],
        sources=[{"title": "Quelle Exo", "url": "https://example.com/exo"}],
        source_summary="Exoskelette unterstützen Kraft und Stabilität im Alltag.",
        facts=[
            "Passive Systeme arbeiten ohne Motor und speichern Bewegung in Federmechanik.",
            "Aktive Systeme geben gezielte Unterstützung beim Gehen und Aufstehen.",
        ],
        angle_options=["Alltagseffekt"],
        risk_notes=["Nicht jede Lösung passt für jeden Kontext."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            {
                "lane_key": "exo-16",
                "lane_family": "value",
                "title": "Exoskelette im Alltag",
                "angle": "Praxisnutzen.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "So helfen Exoskelette bei typischen Alltagsbewegungen.",
                "facts": [
                    "Aktive Systeme helfen bei wiederholten Bewegungen in Alltag und Rehabilitation.",
                ],
                "risk_notes": ["Eine individuelle Einordnung bleibt notwendig."],
                "disclaimer": "Keine individuelle Rechts- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [16],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeShort16sLLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=16,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    word_count = len(item.script.split())
    assert 26 <= word_count <= 36


def test_generate_topic_script_candidate_synthesizes_fallback_on_provider_failures(monkeypatch):
    class FakeUnavailablePrompt1LLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            raise topic_agents.ThirdPartyError(
                message="Gemini text generation failed",
                details={"status_code": 503},
            )

    dossier = topic_agents.ResearchDossier(
        cluster_id="pflegegrad-fallback-01",
        topic="Pflegegrad verstehen",
        anchor_topic="Pflegegrad",
        seed_topic="Pflegegrad",
        cluster_summary="Das Dossier bündelt Begutachtung, Leistungsanpassungen und typische Fehler im Antragsprozess.",
        framework_candidates=["PAL"],
        sources=[{"title": "Pflegekasse", "url": "https://example.com/pflege"}],
        source_summary="Die Quelle erklärt Begutachtung, Leistungsanstiege 2025 und typische Fehler bei der Antragstellung.",
        facts=[
            "Pflegeleistungen steigen 2025 um 4,5 Prozent.",
            "Die Begutachtung bewertet Selbstständigkeit in mehreren Lebensbereichen.",
            "Unvollständige Angaben führen oft zu einer zu niedrigen Einstufung.",
        ],
        angle_options=["Leistungen erklären"],
        risk_notes=["Ohne Vorbereitung droht eine zu niedrige Einstufung."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            {
                "lane_key": "pflegegrad-fallback",
                "lane_family": "value",
                "title": "Pflegegrad ohne Fehlstart verstehen",
                "angle": "Leistungen und Begutachtung.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "So ordnest du Begutachtung, Leistungssätze und typische Stolperfallen besser ein.",
                "facts": [
                    "Ab 2025 steigen Geld- und Sachleistungen um 4,5 Prozent.",
                    "Dokumentierte Alltagshilfen verbessern die Einschätzung im Gutachten.",
                ],
                "risk_notes": ["Ohne Nachweise wirkt der Hilfebedarf schnell kleiner."],
                "disclaimer": "Keine individuelle Rechts- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [16],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeUnavailablePrompt1LLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=16,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    assert item.topic == "Pflegegrad ohne Fehlstart verstehen"
    assert str(item.sources[0].url) == "https://example.com/pflege"
    assert 26 <= len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", item.script)) <= 36


def test_generate_topic_script_candidate_rebuilds_when_raw_draft_bleeds_metadata(monkeypatch):
    class FakeMetadataBleedLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "Die Quelle zeigt, wie Rechte, Anträge und feste Abläufe kommunale Mitsprache belastbarer machen."

    dossier = topic_agents.ResearchDossier(
        cluster_id="teilhabe-bleed-01",
        topic="Behindertenbeirat mit Wirkung",
        anchor_topic="Kommunale Teilhabe",
        seed_topic="Kommunale Teilhabe",
        cluster_summary="Das Dossier erklärt Rechte, Mitsprache und feste Routinen für wirksame Behindertenbeiräte.",
        framework_candidates=["PAL"],
        sources=[{"title": "Kommunale Teilhabe", "url": "https://example.com/teilhabe"}],
        source_summary="Klare Rechte, feste Abläufe und nachvollziehbare Anträge entscheiden darüber, ob kommunale Teilhabe im Alltag tatsächlich Wirkung entfaltet.",
        facts=[
            "Antragsrechte erhöhen den Einfluss in kommunalen Gremien.",
            "Feste Zuständigkeiten verhindern, dass Anliegen im Alltag liegen bleiben.",
            "Klare Routinen machen Mitsprache belastbarer und sichtbarer.",
        ],
        angle_options=["Rechte sichern"],
        risk_notes=["Ohne klare Rechte bleibt Mitsprache oft symbolisch."],
        disclaimer="Keine individuelle Rechts- oder Medizinberatung.",
        lane_candidates=[
            {
                "lane_key": "beirat-bleed",
                "lane_family": "value",
                "title": "Behindertenbeirat mit Wirkung",
                "angle": "Rechte und Routinen.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "Die Quelle zeigt, wie Rechte, Anträge und feste Abläufe kommunale Mitsprache belastbarer machen.",
                "facts": ["Klare Rechte stärken Mitsprache."],
                "risk_notes": ["Ohne Zuständigkeiten versanden Anträge."],
                "disclaimer": "Keine individuelle Rechts- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [8],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeMetadataBleedLLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=8,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    assert detect_metadata_bleed(
        item.script,
        source_summary=dossier.lane_candidates[0].source_summary,
        cluster_summary=dossier.cluster_summary,
    ) is None
    assert item.script.endswith(".")
    assert 12 <= len(item.script.split()) <= 15


def test_generate_topic_script_candidate_strips_research_labels_from_long_script(monkeypatch):
    class FakeContaminatedPrompt1LLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return (
                "Fühlst du dich im digitalen Job schnell überfordert? **Demografische Dringlichkeit:** "
                "Schätzungen zufolge leben weltweit etwa 16 Prozent der Menschen mit signifikanter Behinderung. "
                "Mit barrierefreier Software und Assistenztechnik senkst du Stress und gewinnst mehr Kontrolle im Alltag [cite: 1]."
            )

    dossier = topic_agents.ResearchDossier(
        cluster_id="digital-work-32s-01",
        topic="Digitale Arbeit ohne Technostress",
        anchor_topic="Digitale Arbeit ohne Technostress",
        seed_topic="Digitale Arbeit ohne Technostress",
        cluster_summary="Das Dossier bündelt Belastungsfaktoren, Assistenztechnik und barrierefreie Software für gesündere digitale Arbeit.",
        framework_candidates=["PAL"],
        sources=[{"title": "Digitale Arbeit", "url": "https://example.com/digital"}],
        source_summary="**Zentrale Erkenntnisse:** Barrierefreie Tools, Assistenztechnik und klare Routinen senken mentalen Druck im digitalen Arbeitsalltag.",
        facts=[
            "Assistive Software reduziert wiederkehrende Hürden bei Kommunikation, Struktur und Zugriff auf Informationen.",
            "Barrierefreie Systeme senken Fehlerdruck, wenn Tastaturwege, Kontraste und Vorlesefunktionen zuverlässig funktionieren.",
            "Klare Routinen und passende Technik helfen dir, Überforderung im digitalen Arbeitsalltag messbar zu reduzieren.",
        ],
        angle_options=["Technostress senken"],
        risk_notes=["Ohne passende Tools steigt die Belastung im digitalen Arbeitsalltag."],
        disclaimer="Keine medizinische oder rechtliche Beratung.",
        lane_candidates=[
            {
                "lane_key": "digital-work-clean",
                "lane_family": "value",
                "title": "Digitale Arbeit ohne Technostress",
                "angle": "Assistenztechnik und barrierefreie Tools.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "**Demografische Dringlichkeit:** Gute Assistenztechnik entlastet dich im digitalen Alltag spürbar.",
                "facts": [
                    "Assistive Tools sparen Energie, weil sie mühsame Zwischenschritte im Alltag verkürzen.",
                    "Barrierefreie Software macht Arbeitsabläufe planbarer und reduziert Rückfragen im Team.",
                ],
                "risk_notes": ["Ohne passende Werkzeuge bleibt die Belastung oft dauerhaft hoch."],
                "disclaimer": "Keine medizinische oder rechtliche Beratung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [32],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeContaminatedPrompt1LLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=32,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    lowered = item.script.lower()
    assert "demografische dringlichkeit" not in lowered
    assert "zentrale erkenntnisse" not in lowered
    assert "**" not in item.script
    assert "[cite:" not in lowered
    assert 54 <= len(item.script.split()) <= 74
    assert 5 <= len([segment for segment in item.script.split(". ") if segment.strip()]) <= 6


def test_generate_topic_script_candidate_rebuilds_from_facts_when_raw_draft_contains_heading_tail(monkeypatch):
    class FakeContaminatedPrompt1LLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return (
                "Für G zählt, ob du zwei Kilometer in 30 Minuten gehen kannst. "
                "aG erfordert einen mobilitätsbezogenen GdB von 80 und massive Einschränkungen. "
                "Zentrale Erkenntnisse. Januar 2025 von 91 Euro auf 104 Euro pro Jahr."
            )

    dossier = topic_agents.ResearchDossier(
        cluster_id="merkzeichen-32s-01",
        topic="Merkzeichen G und aG sauber unterscheiden",
        anchor_topic="Merkzeichen G und aG",
        seed_topic="Merkzeichen G und aG",
        cluster_summary="Das Dossier bündelt Anspruchsvoraussetzungen, Mobilitätskriterien und finanzielle Folgen rund um G und aG.",
        framework_candidates=["PAL"],
        sources=[{"title": "Merkzeichen", "url": "https://example.com/merkzeichen"}],
        source_summary="Klare Voraussetzungen helfen dir, Mobilitätsgrad, Wegstrecke und Nachweise sauber zu unterscheiden.",
        facts=[
            "Für G zählt vor allem, ob du ortsübliche Wegstrecken nur deutlich eingeschränkt bewältigen kannst.",
            "aG verlangt besonders schwere Mobilitätseinschränkungen ab den ersten Schritten.",
            "Die saubere Unterscheidung hilft dir bei Anträgen, Nachweisen und dem passenden Parkausweis.",
        ],
        angle_options=["Merkzeichen sauber trennen"],
        risk_notes=["Verwechslungen führen schnell zu falschen Erwartungen bei Nachweisen und Ansprüchen."],
        disclaimer="Keine Rechts- oder medizinische Beratung.",
        lane_candidates=[
            {
                "lane_key": "merkzeichen-32",
                "lane_family": "value",
                "title": "Merkzeichen G und aG sauber unterscheiden",
                "angle": "Anspruchsvoraussetzungen und Folgen.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "Die Quellen trennen Voraussetzungen, Wegstrecke und Schweregrad klar voneinander.",
                "facts": [
                    "G und aG betreffen nicht dieselbe Schwelle im Alltag.",
                    "Die richtige Einordnung spart dir Rückfragen bei Antrag und Nachweis.",
                ],
                "risk_notes": ["Falsche Gleichsetzung führt oft zu unnötigem Frust im Verfahren."],
                "disclaimer": "Keine Rechts- oder medizinische Beratung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [32],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeContaminatedPrompt1LLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=32,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    lowered = item.script.lower()
    assert "zentrale erkenntnisse" not in lowered
    assert "januar 2025" not in lowered
    assert 54 <= len(item.script.split()) <= 74


def test_generate_topic_script_candidate_rejects_contaminated_long_fallback_without_clean_facts(monkeypatch):
    class FakeContaminatedPrompt1LLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "**Demografische Dringlichkeit:** Studienlage: zunehmender chronischer."

    dossier = topic_agents.ResearchDossier(
        cluster_id="contaminated-32s-01",
        topic="Digitale Überlastung",
        anchor_topic="Digitale Überlastung",
        seed_topic="Digitale Überlastung",
        cluster_summary="**Zentrale Erkenntnisse:** zunehmender chronischer.",
        framework_candidates=["PAL"],
        sources=[{"title": "Digitale Überlastung", "url": "https://example.com/stress"}],
        source_summary="**Demografische Dringlichkeit:** zunehmender chronischer.",
        facts=["zunehmender chronischer", "aber damit bist"],
        angle_options=["Technostress"],
        risk_notes=["häufig"],
        disclaimer="Keine medizinische oder rechtliche Beratung.",
        lane_candidates=[
            {
                "lane_key": "contaminated-32",
                "lane_family": "value",
                "title": "Digitale Überlastung besser einordnen",
                "angle": "Studienlage.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "**Zentrale Erkenntnisse:** häufig.",
                "facts": ["massiv", "damit bist"],
                "risk_notes": ["zunehmender chronischer"],
                "disclaimer": "Keine medizinische oder rechtliche Beratung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [32],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeContaminatedPrompt1LLM())

    with pytest.raises(topic_agents.ValidationError, match="PROMPT_1 script"):
        topic_agents.generate_topic_script_candidate(
            post_type="value",
            target_length_tier=32,
            dossier=dossier,
            lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
        )


def test_detect_spoken_copy_issues_allows_valid_separable_verb_endings():
    assert detect_spoken_copy_issues(
        "Deine barrierefreie Bahnreise organisierst du einfach: Ruf die MSZ unter 030 65212888 an!"
    ) == []
    assert detect_spoken_copy_issues(
        "Wenn du das Merkzeichen B hast, fährt deine Begleitperson gratis im Nah- und Fernverkehr mit!"
    ) == []


def test_detect_spoken_copy_issues_flags_definition_residue_and_parenthetical_artifacts():
    issues = detect_spoken_copy_issues(
        "Pflege, Überprüfung, Justieren, rechtzeitiger Austausch von Verschleißteilen), eines Kopiergerätes) liegt."
    )
    kinds = {issue["kind"] for issue in issues}
    assert "dangling_parenthetical" in kinds
    assert "definition_residue" in kinds


def test_generate_topic_script_candidate_rebuilds_when_model_returns_definition_residue(monkeypatch):
    class FakeDefinitionResidueLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return (
                "Hast du dir mal Gedanken über deine Serviceverträge gemacht? "
                "Maßnahmen zur Feststellung und Beurteilung des Ist-Zustands einer Einheit, "
                "um potenzielle Mängel und deren Ursachen zu identifizieren."
            )

    dossier = topic_agents.ResearchDossier(
        cluster_id="servicevertrag-16s-01",
        topic="Serviceverträge für Rollstuhlwartung besser verstehen",
        anchor_topic="Serviceverträge für Rollstuhlwartung",
        seed_topic="Serviceverträge für Rollstuhlwartung",
        cluster_summary="Das Dossier trennt Wartung, Reparatur und Leihservice mit Blick auf Alltag und Vertragsfolgen.",
        framework_candidates=["PAL"],
        sources=[{"title": "Servicevertrag", "url": "https://example.com/servicevertrag"}],
        source_summary="Klare Unterschiede bei Wartung, Reparatur und Ersatzservice helfen dir bei Verträgen und Ausfällen.",
        facts=[
            "Wartung verhindert Ausfälle durch Prüfung, Pflege und rechtzeitigen Teiletausch.",
            "Klare Vertragsregeln helfen dir, Ersatzservice und Fristen besser einzuordnen.",
            "Wenn du den Leistungsumfang kennst, reagierst du bei Ausfällen deutlich ruhiger.",
        ],
        angle_options=["Vertragsfolgen im Alltag"],
        risk_notes=["Ohne klare Servicegrenzen bleibt im Ausfall oft zu viel Interpretationsspielraum."],
        disclaimer="Keine Rechts- oder medizinische Beratung.",
        lane_candidates=[
            {
                "lane_key": "servicevertrag-16",
                "lane_family": "value",
                "title": "Serviceverträge für Rollstuhlwartung besser verstehen",
                "angle": "Vertragsfolgen und Ausfallrisiko.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": "So erkennst du schneller, was Wartung, Reparatur und Ersatzservice für dich praktisch bedeuten.",
                "facts": [
                    "Ein klarer Servicevertrag spart dir Rückfragen, wenn dein Rollstuhl kurzfristig ausfällt.",
                ],
                "risk_notes": ["Ohne klaren Vertrag fehlt im Notfall oft die verlässliche Zuständigkeit."],
                "disclaimer": "Keine Rechts- oder medizinische Beratung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [16],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeDefinitionResidueLLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=16,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    lowered = item.script.lower()
    assert "maßnahmen zur feststellung" not in lowered
    assert "ist-zustands" not in lowered
    assert "überlassung einer sache" not in lowered
    assert 26 <= len(item.script.split()) <= 36


def test_generate_topic_script_candidate_clips_long_metadata_summary(monkeypatch):
    class FakeLongSummaryLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "Wenn du Hilfe im Nahverkehr früh anmeldest, vermeidest du Stress, Wartezeit und chaotische Umstiege."

    long_summary = ("Barrierefreiheit im Nahverkehr braucht klare Anmeldung, Hilfe und verlässliche Abläufe. " * 20)[:1100].strip()
    lane_summary = long_summary[:480].strip()
    dossier = topic_agents.ResearchDossier(
        cluster_id="nahverkehr-8s-01",
        topic="Nahverkehrshilfe besser abstimmen",
        anchor_topic="Nahverkehrshilfe",
        seed_topic="Nahverkehrshilfe",
        cluster_summary=long_summary,
        framework_candidates=["PAL"],
        sources=[{"title": "Nahverkehr", "url": "https://example.com/nahverkehr"}],
        source_summary=long_summary,
        facts=[
            "Frühe Anmeldung verhindert Stress am Bahnsteig.",
            "Klare Zuständigkeiten helfen dir beim barrierefreien Einstieg.",
        ],
        angle_options=["Anmeldung und Ablauf"],
        risk_notes=["Ohne Vorlauf fehlt dir oft verlässliche Unterstützung."],
        disclaimer="Keine Rechts- oder medizinische Beratung.",
        lane_candidates=[
            {
                "lane_key": "nahverkehr-8",
                "lane_family": "value",
                "title": "Nahverkehrshilfe besser abstimmen",
                "angle": "Anmeldung und Ablauf klar erklären.",
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": lane_summary,
                "facts": ["Mit früher Meldung planst du Einstieg und Umstieg ruhiger."],
                "risk_notes": ["Zu späte Meldung führt oft zu improvisierten Lösungen."],
                "disclaimer": "Keine Rechts- oder medizinische Beratung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [8],
            }
        ],
    )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeLongSummaryLLM())

    item = topic_agents.generate_topic_script_candidate(
        post_type="value",
        target_length_tier=8,
        dossier=dossier,
        lane_candidate=dossier.lane_candidates[0].model_dump(mode="json"),
    )

    assert len(item.caption) <= 500
    assert len(item.source_summary) <= 500


def test_parse_topic_research_response_accepts_json_followed_by_markdown():
    raw = """{
      "cluster_id": "rehabilitation-guide-01",
      "topic": "Ergotherapie und Physiotherapie im Alltag",
      "anchor_topic": "Therapie im Alltag",
      "seed_topic": "Ergotherapie und Physiotherapie",
      "cluster_summary": "Das Dossier vergleicht Ziele, Verordnungen, Kosten und konkrete Alltagseffekte von Ergo- und Physiotherapie.",
      "framework_candidates": ["PAL", "Testimonial"],
      "sources": [
        {"title": "GKV Heilmittel", "url": "https://example.com/heilmittel"}
      ],
      "source_summary": "Heilmittelrichtlinien, Verordnungen und Praxisbeispiele zeigen, wann Ergo oder Physio im Alltag sinnvoll und finanzierbar ist.",
      "facts": ["Beide Therapieformen brauchen in der Regel eine ärztliche Verordnung."],
      "angle_options": ["Ziele vergleichen", "Kosten erklären"],
      "risk_notes": ["Verordnungen unterscheiden sich je nach Diagnose."],
      "disclaimer": "Keine individuelle Therapie- oder Rechtsberatung.",
      "lane_candidates": [
        {
          "lane_key": "vergleich",
          "lane_family": "value",
          "title": "Ergo oder Physio: Was hilft dir wann?",
          "angle": "Vergleich der Ziele und Alltagseffekte.",
          "priority": 1,
          "framework_candidates": ["PAL"],
          "source_summary": "Die Quellen vergleichen Ziele, typische Verordnungen und den praktischen Nutzen im Alltag.",
          "facts": ["Physiotherapie fokussiert stärker Bewegung und Funktion."],
          "risk_notes": ["Therapiebedarf bleibt individuell."],
          "disclaimer": "Keine individuelle Therapie- oder Rechtsberatung.",
          "lane_overlap_warnings": ["Nicht mit Reha-Sport vermischen."],
          "suggested_length_tiers": [8, 16]
        }
      ]
    }

    **Sources:**
    - https://example.com/heilmittel
    """

    dossier = topic_agents.parse_topic_research_response(raw)

    assert dossier.cluster_id == "rehabilitation-guide-01"
    assert len(dossier.sources) == 1
    assert len(dossier.lane_candidates) >= 3
    assert len({lane.title for lane in dossier.lane_candidates[:3]}) == 3


def test_parse_topic_research_response_expands_lane_fanout_from_angles():
    raw = json.dumps(
        {
            "cluster_id": "opnv-fanout-01",
            "topic": "Barrierefreiheit im ÖPNV",
            "anchor_topic": "ÖPNV Barrierefreiheit",
            "seed_topic": "ÖPNV Barrierefreiheit",
            "cluster_summary": "Das Dossier fasst Infrastruktur, Regeln und Informationszugang im ÖPNV zusammen.",
            "framework_candidates": ["PAL"],
            "sources": [
                {"title": "Quelle A", "url": "https://example.com/a"}
            ],
            "source_summary": "Barrierefreiheit im ÖPNV betrifft Einstieg, Information und Assistenz im Alltag.",
            "facts": [
                "Rampen und Aufzüge beeinflussen die Mobilität direkt.",
                "Informationen müssen auch taktil und akustisch zugänglich sein.",
            ],
            "angle_options": [
                "Einstieg und Fahrzeugtechnik",
                "Informationszugang im Alltag",
                "Assistenz und Begleitservice",
            ],
            "risk_notes": [
                "Ausfälle und Zuständigkeiten verzögern die Umsetzung.",
            ],
            "disclaimer": "Keine individuelle Rechtsberatung.",
            "lane_candidates": [
                {
                    "lane_key": "entry",
                    "lane_family": "infrastructure",
                    "title": "Einstieg und Fahrzeugtechnik",
                    "angle": "Rampen, Aufzüge, Kneeling.",
                    "priority": 1,
                    "framework_candidates": ["PAL"],
                    "source_summary": "Der Einstieg ist die größte Hürde.",
                    "facts": ["Niederflurfahrzeuge helfen beim Einstieg."],
                    "risk_notes": ["Aufzüge fallen aus."],
                    "disclaimer": "Keine individuelle Rechtsberatung.",
                    "lane_overlap_warnings": [],
                    "suggested_length_tiers": [16],
                }
            ],
        },
        ensure_ascii=False,
    )

    dossier = topic_agents.parse_topic_research_response(raw)

    assert len(dossier.lane_candidates) >= 3
    signatures = {lane.lane_key for lane in dossier.lane_candidates[:3]}
    assert len(signatures) == 3


def test_normalize_topic_research_dossier_falls_back_when_normalizer_is_unavailable(monkeypatch):
    class FakeUnavailableNormalizerLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            raise topic_agents.ThirdPartyError(
                message="Gemini generateContent failed",
                details={"status_code": 503},
            )

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: FakeUnavailableNormalizerLLM())

    dossier = topic_agents.normalize_topic_research_dossier(
        seed_topic="Pflegegrad",
        post_type="value",
        target_length_tier=8,
        raw_response=(
            "# Forschungsdossier: Pflegegrad\n\n"
            "- Pflegeleistungen steigen 2025 um 4,5 Prozent.\n"
            "- Die Begutachtung bewertet Selbstständigkeit in mehreren Lebensbereichen.\n"
            "- Widerspruch ist möglich, wenn die Einstufung zu niedrig ausfällt.\n"
        ),
    )

    assert dossier.seed_topic == "Pflegegrad"
    assert dossier.topic
    assert len(dossier.lane_candidates) >= 1


def test_parse_topic_research_response_clips_overlong_disclaimers():
    long_disclaimer = "Hinweis " * 80
    raw = json.dumps(
        {
            "cluster_id": "rehabilitation-guide-02",
            "topic": "Therapie richtig einordnen",
            "anchor_topic": "Therapie",
            "seed_topic": "Therapie",
            "cluster_summary": "Ein Dossier über Grenzen, Nutzen und Organisation von Ergo- und Physiotherapie im Alltag.",
            "framework_candidates": ["PAL"],
            "sources": [
                {"title": "Quelle", "url": "https://example.com/quelle"}
            ],
            "source_summary": "Die Quelle ordnet Therapieziele, Verordnungen und alltagsnahe Erwartungen ein.",
            "facts": ["Therapieziele hängen von Diagnose und Alltag ab."],
            "angle_options": ["Grenzen erklären"],
            "risk_notes": ["Nicht jede Therapie passt für jede Situation."],
            "disclaimer": long_disclaimer,
            "lane_candidates": [
                {
                    "lane_key": "grenzen",
                    "lane_family": "value",
                    "title": "Was Therapie leisten kann und was nicht",
                    "angle": "Abgrenzung realistischer Erwartungen.",
                    "priority": 3,
                    "framework_candidates": ["PAL"],
                    "source_summary": "Die Quelle beschreibt Nutzen, Grenzen und typische Missverständnisse.",
                    "facts": ["Therapie ersetzt keine individuelle Diagnose."],
                    "risk_notes": ["Konkrete Behandlungen müssen ärztlich abgestimmt werden."],
                    "disclaimer": long_disclaimer,
                    "lane_overlap_warnings": [],
                    "suggested_length_tiers": [8],
                }
            ],
        },
        ensure_ascii=False,
    )

    dossier = topic_agents.parse_topic_research_response(raw)

    assert len(dossier.disclaimer) <= 240
    assert len(dossier.lane_candidates[0].disclaimer) <= 200


def test_normalize_topic_research_dossier_rejects_topic_list_contamination():
    contaminated_raw = json.dumps(
        [
            {
                "topic": "Pflegegrad 2025 prüfen",
                "script": "Nicht relevant für den Seed.",
                "source_summary": "Unrelated topic list entry.",
                "facts": ["foo"],
                "risk_notes": ["bar"],
            },
            {
                "topic": "Hilfsmittel richtig beantragen",
                "script": "Another unrelated item.",
                "source_summary": "Still unrelated.",
                "facts": ["baz"],
                "risk_notes": ["qux"],
            },
        ],
        ensure_ascii=False,
    )

    dossier = topic_agents.normalize_topic_research_dossier(
        seed_topic="Dein Rollstuhl ist versichert?",
        post_type="value",
        target_length_tier=8,
        raw_response=contaminated_raw,
    )

    assert dossier.seed_topic == "Dein Rollstuhl ist versichert?"
    assert "Pflegegrad 2025 prüfen" not in dossier.source_summary
    assert dossier.topic == "Dein Rollstuhl ist versichert?"
    assert len(dossier.lane_candidates) >= 1


def test_discover_topics_for_batch_runs_off_event_loop(monkeypatch):
    calls = []

    def fake_sync(batch_id):
        calls.append(batch_id)
        return {"batch_id": batch_id, "posts_created": 1, "state": "S2_SEEDED", "topics": []}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(topic_handlers, "_discover_topics_for_batch_sync", fake_sync)
    monkeypatch.setattr(topic_handlers.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(topic_handlers.discover_topics_for_batch("batch-123"))

    assert calls == ["batch-123"]
    assert result["state"] == "S2_SEEDED"


def test_discover_topics_for_manual_batch_skips_gemini(monkeypatch):
    updated = []

    monkeypatch.setattr(
        topic_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "state": "S1_SETUP",
            "creation_mode": "manual",
            "post_type_counts": {},
            "target_length_tier": 16,
        },
    )
    monkeypatch.setattr(
        topic_handlers,
        "get_posts_by_batch",
        lambda batch_id: [
            {
                "id": "post-1",
                "seed_data": {
                    "manual_draft": True,
                    "script_review_status": "pending",
                },
            }
        ],
    )
    monkeypatch.setattr(
        topic_handlers,
        "update_batch_state",
        lambda batch_id, target_state: updated.append((batch_id, target_state.value)) or {
            "id": batch_id,
            "state": target_state.value,
        },
    )
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", lambda: (_ for _ in ()).throw(AssertionError("Gemini path should not run")))
    monkeypatch.setattr(topic_handlers, "generate_lifestyle_topics", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gemini path should not run")))
    monkeypatch.setattr(topic_handlers, "generate_product_topics", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gemini path should not run")))

    result = asyncio.run(topic_handlers.discover_topics_for_batch("batch-manual"))

    assert result["manual_batch"] is True
    assert result["state"] == "S2_SEEDED"
    assert result["posts_created"] == 1
    assert updated == [("batch-manual", "S2_SEEDED")]


def test_cron_topic_discovery_continues_after_one_batch_failure(monkeypatch):
    from app.features.topics import handlers as topic_handlers
    from app.core.states import BatchState
    from app.features.batches import queries as batch_queries

    batches = [
        {"id": "batch-1", "state": BatchState.S1_SETUP.value},
        {"id": "batch-2", "state": BatchState.S1_SETUP.value},
        {"id": "batch-3", "state": BatchState.S2_SEEDED.value},
    ]

    async def fake_discover_topics_endpoint(request):
        if request.batch_id == "batch-1":
            return topic_handlers.SuccessResponse(data={"batch_id": "batch-1", "posts_created": 1, "state": "S2_SEEDED", "topics": []})
        raise RuntimeError("boom")

    monkeypatch.setattr(topic_handlers, "get_settings", lambda: SimpleNamespace(cron_secret="cron-secret"))
    monkeypatch.setattr(batch_queries, "list_batches", lambda archived=False, limit=100, offset=0: (batches, None))
    monkeypatch.setattr(topic_handlers, "discover_topics_endpoint", fake_discover_topics_endpoint)

    response = asyncio.run(topic_handlers.cron_topic_discovery(authorization="Bearer cron-secret"))

    assert response.data["seeded_batches"] == ["batch-1"]
    assert response.data["failed_batches"][0]["batch_id"] == "batch-2"


def test_cron_topic_discovery_returns_success_when_all_batches_fail(monkeypatch):
    from app.features.topics import handlers as topic_handlers
    from app.core.states import BatchState
    from app.features.batches import queries as batch_queries

    batches = [
        {"id": "batch-1", "state": BatchState.S1_SETUP.value},
        {"id": "batch-2", "state": BatchState.S1_SETUP.value},
    ]

    async def fake_discover_topics_endpoint(request):
        raise RuntimeError("boom")

    monkeypatch.setattr(topic_handlers, "get_settings", lambda: SimpleNamespace(cron_secret="cron-secret"))
    monkeypatch.setattr(batch_queries, "list_batches", lambda archived=False, limit=100, offset=0: (batches, None))
    monkeypatch.setattr(topic_handlers, "discover_topics_endpoint", fake_discover_topics_endpoint)

    response = asyncio.run(topic_handlers.cron_topic_discovery(authorization="Bearer cron-secret"))

    assert response.data["seeded_batches"] == []
    assert {item["batch_id"] for item in response.data["failed_batches"]} == {"batch-1", "batch-2"}


def test_cron_topic_discovery_skips_malformed_batch_rows(monkeypatch):
    from app.features.topics import handlers as topic_handlers
    from app.features.batches import queries as batch_queries

    batches = [
        {"id": "batch-1", "state": "S1_SETUP"},
        {"id": "batch-2"},
        {"state": "S1_SETUP"},
        "not-a-batch-row",
    ]

    async def fake_discover_topics_endpoint(request):
        return topic_handlers.SuccessResponse(data={"batch_id": request.batch_id, "posts_created": 1, "state": "S2_SEEDED", "topics": []})

    monkeypatch.setattr(topic_handlers, "get_settings", lambda: SimpleNamespace(cron_secret="cron-secret"))
    monkeypatch.setattr(batch_queries, "list_batches", lambda archived=False, limit=100, offset=0: (batches, None))
    monkeypatch.setattr(topic_handlers, "discover_topics_endpoint", fake_discover_topics_endpoint)

    response = asyncio.run(topic_handlers.cron_topic_discovery(authorization="Bearer cron-secret"))

    assert response.data["seeded_batches"] == ["batch-1"]
    assert len(response.data["failed_batches"]) == 3
    assert {item["error"] for item in response.data["failed_batches"]} == {"Malformed batch row returned by list_batches"}


def test_discover_topics_endpoint_finalizes_partial_completion(monkeypatch):
    from app.core.states import BatchState

    async def fake_discover_topics_for_batch(batch_id):
        return {
            "batch_id": batch_id,
            "posts_created": 1,
            "state": BatchState.S2_SEEDED.value,
            "topics": [{"title": "Teilweise fertig", "rotation": "Hook", "cta": "CTA"}],
        }

    monkeypatch.setattr(topic_handlers, "discover_topics_for_batch", fake_discover_topics_for_batch)

    response = asyncio.run(
        topic_handlers.discover_topics_endpoint(
            topic_handlers.DiscoverTopicsRequest(batch_id="batch-1", count=10)
        )
    )

    assert response.data["posts_created"] == 1
    assert response.data["state"] == BatchState.S2_SEEDED.value


def test_validate_german_content_allows_peer_support_loan_phrase():
    item = topic_agents.ResearchAgentItem(
        topic="Austausch im Alltag",
        framework="PAL",
        sources=[{"title": "Beispiel", "url": "https://example.com"}],
        script="Weißt du, wie Peer-Support hilft? Andere Betroffene unterstützen dich bei neuen Hürden.",
        source_summary="Peer-Support Gruppen stärken Austausch, Zugehörigkeit und Mut im Alltag. Viele Betroffene erleben dadurch mehr Sicherheit, Orientierung und gegenseitige Hilfe. #Austausch #Mut #Rollstuhlalltag",
        estimated_duration_s=5,
        tone="direkt, freundlich, empowernd, du-Form",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )

    topic_agents.validate_german_content(item)


def test_validate_duration_rejects_dense_compound_script():
    item = topic_agents.ResearchAgentItem(
        topic="Arbeitshilfen im Job",
        framework="PAL",
        sources=[{"title": "Beispiel", "url": "https://example.com"}],
        script="Weißt du eigentlich, dass das Integrationsamt deine kompletten technischen Arbeitshilfen im Job vollständig bezahlt?",
        source_summary="Das Integrationsamt kann technische Hilfen im Beruf finanzieren, wenn sie deine Teilhabe am Arbeitsleben sichern. Wichtig sind Antrag, Zuständigkeit und eine klare Begründung für den Arbeitsplatz. #Integrationsamt #Arbeitshilfe #Teilhabe",
        estimated_duration_s=6,
        tone="direkt, freundlich, empowernd, du-Form",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )

    with pytest.raises(topic_agents.ValidationError, match="too dense for natural Veo speech delivery"):
        topic_agents.validate_duration(item)


def test_parse_prompt2_response_splits_consecutive_one_line_scripts_for_new_hooks():
    raw = """Problem-Agitieren-Lösung Ads
Was viele an barrierefreien Eingängen unterschätzen, merkst du erst, wenn schon eine kleine Stufe alles blockiert.
Von außen wirkt Umsteigen simpel, aber im Alltag kostet mich schlechte Planung oft viel mehr Energie als gedacht.

Beschreibung

Barrierefreiheit scheitert im Alltag oft an kleinen Details wie Schwellen, Türbreiten und fehlenden Alternativen bei Ausfällen. Gerade unterwegs spart dir gute Vorbereitung Stress, Kraft und unnötige Umwege. #Barrierefreiheit #Rollstuhlalltag #Mobilität"""

    scripts = topic_agents.parse_prompt2_response(raw, max_per_category=5)

    assert scripts.problem_agitate_solution == [
        "Was viele an barrierefreien Eingängen unterschätzen, merkst du erst, wenn schon eine kleine Stufe alles blockiert.",
        "Von außen wirkt Umsteigen simpel, aber im Alltag kostet mich schlechte Planung oft viel mehr Energie als gedacht.",
    ]
    assert scripts.testimonial == [scripts.problem_agitate_solution[0]]
    assert scripts.transformation == [scripts.problem_agitate_solution[0]]


def test_generate_topic_script_candidate_applies_shared_quality_gate():
    from app.features.topics.research_runtime import generate_topic_script_candidate

    class FakeLLM:
        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            return "Ab 2025 gibt es endlich Hilfe — und du sparst im Alltag spürbar Stress."

    item = generate_topic_script_candidate(
        post_type="value",
        target_length_tier=8,
        dossier={
            "topic": "MSZ — Hilfe",
            "seed_topic": "MSZ — Hilfe",
            "source_summary": "Ab 2025 gilt die Hilfe am Bahnhof.",
            "facts": ["Ab 2025 gilt die Hilfe am Bahnhof."],
            "risk_notes": [],
            "framework_candidates": ["PAL"],
            "sources": [],
        },
        lane_candidate={
            "title": "MSZ — Hilfe",
            "source_summary": "Ab 2025 gilt die Hilfe am Bahnhof.",
            "facts": ["Ab 2025 gilt die Hilfe am Bahnhof."],
            "risk_notes": [],
            "framework_candidates": ["PAL"],
        },
        llm_factory=lambda: FakeLLM(),
    )

    assert "—" not in item.topic
    assert "—" not in item.script
    assert "—" not in item.caption
    assert "—" not in item.source_summary
    assert "Ab 2025" not in item.script
    assert "Seit 2025" in item.script
    assert 14 <= len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", item.script)) <= 18
