from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from app.main import app
from app.features.blog import queries as blog_queries
from app.features.blog.schemas import BlogContent, BlogSource, build_blog_content_from_llm, normalize_blog_content
from app.features.blog.webflow_client import WebflowClient
from pathlib import Path


def test_blog_content_from_llm_is_webflow_ready():
    content = build_blog_content_from_llm(
        {
            "name": "Treppenlift kaufen: 7 Fehler vermeiden",
            "slug": "treppenlift-kaufen-7-fehler-vermeiden",
            "merksatz": "Nicht der billigste Lift ist automatisch die beste Entscheidung.",
            "tipp": "Vergleichen Sie Angebote erst, nachdem Ihre Wohnsituation sauber aufgenommen wurde.",
            "summary_bullets": [
                "Der guenstigste Preis ist selten die beste Loesung.",
                "Unklare Vertraege verursachen spaeter oft Mehrkosten.",
                "Eine saubere Bedarfsanalyse spart Zeit und Geld.",
            ],
            "intro_heading": "Warum Fehlentscheidungen beim Liftkauf teuer werden",
            "introduction_paragraphs": [
                "Ein Treppenlift ist keine spontane Anschaffung.",
                "Gerade unter Zeitdruck werden wichtige Kriterien uebersehen.",
            ],
            "sections": [
                {
                    "heading": "Fehler 1: Zu frueh Angebote vergleichen",
                    "paragraphs": ["Viele starten direkt mit Preisvergleichen.", "Das blendet die eigentlichen Anforderungen aus."],
                    "bullets": ["Wohnsituation pruefen", "Nutzungshaeufigkeit klaeren"],
                },
                {
                    "heading": "Fehler 2: Vertragsdetails ignorieren",
                    "paragraphs": ["Service und Wartung stehen oft nur im Kleingedruckten."],
                    "bullets": [],
                },
                {
                    "heading": "Fehler 3: Die Zukunft nicht mitdenken",
                    "paragraphs": ["Was heute passt, kann in zwei Jahren unpraktisch sein."],
                    "bullets": [],
                },
            ],
            "conclusion_heading": "Was vor der Unterschrift klar sein muss",
            "conclusion_paragraphs": ["Eine gute Entscheidung entsteht nicht aus Zeitdruck, sondern aus Klarheit."],
            "preview_text": "Viele Lift-Kaeufer achten auf den falschen Vergleichswert. Diese Fehler sollten Sie kennen.",
            "meta_title": "Treppenlift kaufen: 7 Fehler vermeiden",
            "meta_description": "Diese typischen Fehler kosten Lift-Kaeufer spaeter Geld. Worauf Sie vor dem Kauf achten muessen.",
        },
        dossier_id="dossier-123",
        sources=[{"title": "LIPPE Lift", "url": "https://www.example.com/source"}],
    )

    assert content["name"] == "Treppenlift kaufen: 7 Fehler vermeiden"
    assert content["title"] == content["name"]
    assert content["summary_html"].startswith("<h2>")
    assert "<ul>" in content["summary_html"]
    assert "<h2>Fehler 1: Zu frueh Angebote vergleichen</h2>" in content["body_html"]
    assert content["body"] == content["body_html"]
    assert content["reading_time"].endswith("Minuten") or content["reading_time"] == "1 Minute"


def test_blog_content_model_accepts_webflow_contract():
    content = BlogContent(
        schema_version=2,
        name="Barrierefreiheit im OePNV",
        title="Barrierefreiheit im OePNV",
        slug="barrierefreiheit-oepnv",
        merksatz="Rechte helfen nur, wenn Betroffene sie praktisch durchsetzen koennen.",
        tipp="Dokumentieren Sie Hindernisse sofort mit Ort, Zeit und konkreter Auswirkung.",
        summary_title="Das Wichtigste auf einen Blick",
        summary_bullets=["Punkt 1", "Punkt 2", "Punkt 3"],
        summary_html="<h2>Das Wichtigste auf einen Blick</h2><ul><li>Punkt 1</li></ul>",
        intro_heading="Einleitung",
        introduction_paragraphs=["Absatz 1"],
        sections=[],
        conclusion_heading="Schluss",
        conclusion_paragraphs=["Absatz 2"],
        body_html="<h2>Einleitung</h2><p>Absatz 1</p>",
        body="<h2>Einleitung</h2><p>Absatz 1</p>",
        preview_text="Kurzer Vorschautext",
        reading_time="4-5 Minuten",
        meta_title="Barrierefreiheit im OePNV",
        meta_description="Kurze Meta Beschreibung",
        sources=[BlogSource(title="Quelle", url="https://example.com")],
        word_count=120,
        generated_at="2026-03-27T14:00:00Z",
        dossier_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert content.name == content.title
    assert len(content.sources) == 1


def test_normalize_blog_content_upgrades_legacy_payload():
    normalized = normalize_blog_content(
        {
            "title": "Legacy Titel",
            "body": "Erster Absatz.\n\nZweiter Absatz.",
            "slug": "legacy-titel",
            "meta_description": "Legacy Beschreibung",
        }
    )

    assert normalized["name"] == "Legacy Titel"
    assert normalized["title"] == "Legacy Titel"
    assert normalized["body_html"].startswith("<p>")
    assert normalized["summary_html"].startswith("<h2>")
    assert normalized["meta_description"] == "Legacy Beschreibung"


def test_webflow_client_build_blog_field_data_resolves_field_slugs_and_author_option():
    client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
    client._collection_cache = {
        "fields": [
            {"slug": "merksatz", "displayName": "Merksatz"},
            {"slug": "tipp", "displayName": "Tipp"},
            {"slug": "zusammenfassung", "displayName": "Zusammenfassung"},
            {"slug": "inhalt", "displayName": "Inhalt"},
            {"slug": "veroeffentlichungsdatum", "displayName": "Veröffentlichungsdatum"},
            {"slug": "vorschautext", "displayName": "Vorschautext"},
            {"slug": "lesedauer", "displayName": "Lesedauer"},
            {"slug": "vorschaubild", "displayName": "Vorschaubild"},
            {
                "slug": "autor",
                "displayName": "Autor",
                "validations": {"options": [{"id": "opt-1", "name": "Patrick Berg"}]},
            },
            {"slug": "meta-titel", "displayName": "Meta-Titel"},
            {"slug": "meta-beschreibung", "displayName": "Meta-Beschreibung"},
        ]
    }

    field_data = client.build_blog_field_data(
        {
            "name": "Treppenlift kaufen: 7 Fehler vermeiden",
            "slug": "treppenlift-kaufen-7-fehler-vermeiden",
            "merksatz": "Merksatz",
            "tipp": "Tipp",
            "summary_html": "<h2>Das Wichtigste auf einen Blick</h2><ul><li>A</li></ul>",
            "body_html": "<h2>Einleitung</h2><p>Text</p>",
            "preview_text": "Vorschautext",
            "reading_time": "7-8 Minuten",
            "preview_image_url": "https://images.example.com/thumb.jpg",
            "author_name": "Patrick Berg",
            "meta_title": "Meta Titel",
            "meta_description": "Meta Beschreibung",
        },
        publication_date="2026-04-01T09:00:00Z",
    )

    assert field_data["name"] == "Treppenlift kaufen: 7 Fehler vermeiden"
    assert field_data["slug"] == "treppenlift-kaufen-7-fehler-vermeiden"
    assert field_data["zusammenfassung"].startswith("<h2>")
    assert field_data["inhalt"].startswith("<h2>")
    assert field_data["veroeffentlichungsdatum"] == "2026-04-01T09:00:00Z"
    assert field_data["autor"] == "opt-1"
    assert field_data["vorschaubild"]["url"] == "https://images.example.com/thumb.jpg"


def test_webflow_client_create_and_publish_payloads_are_correct():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"id": "wf-item-123"}'
    mock_response.json.return_value = {"id": "wf-item-123"}

    with patch.object(httpx.Client, "request", return_value=mock_response) as mock_request:
        client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
        item_id = client.create_item({"name": "Test Blog", "slug": "test-blog", "inhalt": "<p>Hello</p>"})
        published = client.publish_item(item_id)

    assert item_id == "wf-item-123"
    assert published is True
    assert mock_request.call_args_list[0].args[0] == "POST"
    assert mock_request.call_args_list[0].kwargs["json"]["fieldData"]["name"] == "Test Blog"
    assert mock_request.call_args_list[1].kwargs["json"] == {"itemIds": ["wf-item-123"]}


def test_blog_toggle_endpoint_is_registered():
    client = TestClient(app)
    response = client.put("/blog/posts/nonexistent/blog-toggle", allow_redirects=False)
    assert response.status_code != 404


def test_blog_generate_endpoint_is_registered():
    client = TestClient(app)
    response = client.post("/blog/posts/00000000-0000-0000-0000-000000000000/blog/generate", allow_redirects=False)
    assert response.status_code != 404


def test_blog_content_update_endpoint_rejects_empty():
    client = TestClient(app)
    response = client.put(
        "/blog/posts/00000000-0000-0000-0000-000000000000/blog/content",
        json={},
        allow_redirects=False,
    )
    assert response.status_code in (302, 307, 422, 500)


def test_blog_publish_endpoint_is_registered():
    client = TestClient(app)
    response = client.post("/blog/posts/00000000-0000-0000-0000-000000000000/blog/publish", allow_redirects=False)
    assert response.status_code != 404


def test_blog_prompt_requests_plain_text_output():
    prompt = Path("app/features/topics/prompt_data/blog_post.txt").read_text(encoding="utf-8")
    assert "reiner Text" in prompt
    assert "kein JSON" in prompt
    assert "AUSGABEFORMAT (reiner Text" in prompt


def test_update_blog_status_falls_back_for_legacy_status_constraint(monkeypatch):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.update_calls = []

        def update(self, payload):
            self.update_calls.append(dict(payload))
            self._payload = payload
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            if len(self.update_calls) == 1:
                raise Exception("violates check constraint posts_blog_status_check")
            return _FakeResponse([self._payload])

    table = _FakeTable()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: table)})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    updated = blog_queries.update_blog_status("post-1", status="publishing", scheduled_at="2026-04-01T09:00:00Z")

    assert table.update_calls[0]["blog_status"] == "publishing"
    assert table.update_calls[0]["blog_scheduled_at"] == "2026-04-01T09:00:00Z"
    assert table.update_calls[1]["blog_status"] == "draft"
    assert "blog_scheduled_at" not in table.update_calls[1]
    assert updated["blog_status"] == "draft"
