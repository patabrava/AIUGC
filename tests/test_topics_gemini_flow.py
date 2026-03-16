"""Regression tests for Gemini-backed topic discovery."""

import asyncio
from types import SimpleNamespace
import httpx

from app.adapters import llm_client as llm_client_module
from app.core.config import Settings
from app.features.topics import agents as topic_agents
from app.features.topics import handlers as topic_handlers


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
            "script": "Kennst du deinen Pflegegrad schon genau? So prüfst du 2025 schneller, welche Leistungen dir wirklich konkret zustehen.",
            "source_summary": "Das Bundesgesundheitsministerium erklärt, welche Leistungen die Pflegeversicherung umfasst, wie du Anträge stellst und welche Fristen wichtig sind. Gerade bei Pflegegrad-Änderungen lohnt sich ein genauer Blick auf Voraussetzungen, Nachweise und Beratungsangebote. #Pflegegrad #Pflegeversicherung #Rollstuhlalltag",
            "estimated_duration_s": 7,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          },
          {
            "topic": "Hilfsmittel richtig beantragen",
            "framework": "Testimonial",
            "sources": [{"title": "GKV Hilfsmittel", "url": "https://www.gkv-spitzenverband.de/krankenversicherung/hilfsmittel/hilfsmittel.jsp"}],
            "script": "Check mal dein Hilfsmittelrezept genau, so vermeidest du Rückfragen und kommst schneller an passende Versorgung für deinen Alltag.",
            "source_summary": "Der GKV-Spitzenverband erläutert, wie Hilfsmittel gelistet sind, welche Nachweise oft nötig werden und warum genaue Produktbeschreibungen den Antrag beschleunigen können. Gerade bei Rollstuhlversorgung hilft dir das, Ärzt:innen und Kostenträger sauber zu koordinieren. #Hilfsmittel #Rollstuhlversorgung #Krankenkasse",
            "estimated_duration_s": 8,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          },
          {
            "topic": "Begleitperson im Nahverkehr",
            "framework": "Transformation",
            "sources": [{"title": "DB Barrierefrei reisen", "url": "https://www.bahn.de/service/individuelle-reise/barrierefrei"}],
            "script": "Weißt du, wann deine Begleitperson gratis mitfährt? Mit Merkzeichen B nutzt du viele Fahrten deutlich entspannter.",
            "source_summary": "Die Bahn beschreibt Unterstützungsangebote, Buchungswege und Voraussetzungen für barrierefreies Reisen. Für viele Fahrten lohnt sich der Blick auf Nachweise, Voranmeldung und Servicezeiten, damit du unterwegs weniger Stress hast und Begleitung sicher einplanen kannst. #Begleitperson #BarrierefreiReisen #Nahverkehr",
            "estimated_duration_s": 8,
            "tone": "direkt, freundlich, empowernd, du-Form",
            "disclaimer": "Keine Rechts- oder medizinische Beratung."
          }
        ]
        """

    def generate_gemini_json(self, prompt, json_schema, system_prompt=None, **kwargs):
        self.json_prompts.append((prompt, json_schema, system_prompt, kwargs))
        return {
            "facts": ["Pflegeleistungen müssen beantragt werden"],
            "source_context": "Pflegeversicherung 2025",
        }

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.text_prompts.append((prompt, system_prompt, kwargs))
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


def test_generate_gemini_deep_research_retries_transient_poll_503(monkeypatch):
    fake_settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
        gemini_topic_model="gemini-2.5-flash",
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


def test_topics_feature_uses_gemini_methods(monkeypatch):
    fake_llm = FakeTopicLLM()

    monkeypatch.setattr(topic_agents, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(topic_agents, "_validate_url_accessible", lambda url, timeout=5.0: True)

    items = topic_agents.generate_topics_research_agent(post_type="value", count=3)
    scripts = topic_agents.generate_dialog_scripts(topic="Pflegegrad 2025 prüfen", scripts_required=1)
    topic = topic_agents.convert_research_item_to_topic(items[0])
    seed = topic_agents.extract_seed_strict_extractor(topic)

    assert len(items) == 3
    assert fake_llm.deep_research_prompts, "PROMPT_1 should use Gemini Deep Research"
    assert len(fake_llm.deep_research_prompts) == 1, "PROMPT_1 should use one deep research request per post type batch"
    assert fake_llm.text_prompts, "PROMPT_2 should use Gemini text generation"
    assert fake_llm.json_prompts, "Strict extractor or normalizer should use Gemini JSON generation"
    assert scripts.problem_agitate_solution
    assert seed.facts == ["Pflegeleistungen müssen beantragt werden"]


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


def test_validate_german_content_allows_peer_support_loan_phrase():
    item = topic_agents.ResearchAgentItem(
        topic="Austausch im Alltag",
        framework="PAL",
        sources=[{"title": "Beispiel", "url": "https://example.com"}],
        script="Weißt du, wie Peer-Support hilft? Andere Betroffene unterstützen dich bei Frust und neuen Hürden wirklich auf Augenhöhe.",
        source_summary="Peer-Support Gruppen stärken Austausch, Zugehörigkeit und Mut im Alltag. Viele Betroffene erleben dadurch mehr Sicherheit, Orientierung und gegenseitige Hilfe. #Austausch #Mut #Rollstuhlalltag",
        estimated_duration_s=8,
        tone="direkt, freundlich, empowernd, du-Form",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )

    topic_agents.validate_german_content(item)


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
