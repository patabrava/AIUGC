from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch
import asyncio

import base64
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import FlowForgeException, ThirdPartyError, ValidationError

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from app.main import app
from app.adapters.llm_client import LLMClient
from app.features.blog import handlers as blog_handlers
from app.features.blog import blog_runtime
from app.features.blog import queries as blog_queries
from app.features.blog.schemas import BlogContent, BlogSource, build_blog_content_from_llm, normalize_blog_content
from app.features.blog.webflow_client import WebflowClient
from pathlib import Path


def _make_png_bytes() -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGBA", (8, 8), (18, 52, 86, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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


def test_gemini_image_generation_maps_nanobanana_alias():
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(b"png-bytes").decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ]
    }

    with patch.object(client.gemini_http_client, "post", return_value=mock_response) as mock_post:
        result = client.generate_gemini_image(prompt="Quadratisches Coverbild", model="nanobanana-2")

    assert result["model"] == "gemini-2.5-flash-image"
    assert result["image_bytes"] == b"png-bytes"
    call = mock_post.call_args
    assert call.args[0] == "/models/gemini-2.5-flash-image:generateContent"
    assert call.kwargs["json"]["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert call.kwargs["json"]["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"


def test_gemini_image_generation_maps_stale_31_preview_alias_to_current_model():
    client = LLMClient()

    assert client._resolve_gemini_image_model("gemini-3.1-flash-image-preview") == "gemini-3.1-flash-image"


def test_gemini_image_generation_maps_nanobananapro_alias():
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(b"png-bytes").decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ]
    }

    with patch.object(client.gemini_http_client, "post", return_value=mock_response) as mock_post:
        result = client.generate_gemini_image(prompt="Quadratisches Coverbild", model="nanobananapro")

    assert result["model"] == "gemini-3-pro-image-preview"
    assert result["image_bytes"] == b"png-bytes"
    call = mock_post.call_args
    assert call.args[0] == "/models/gemini-3-pro-image-preview:generateContent"
    assert call.kwargs["json"]["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert call.kwargs["json"]["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"


def test_gemini_image_generation_accepts_ordered_reference_images():
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(b"png-bytes").decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ]
    }

    with patch.object(client.gemini_http_client, "post", return_value=mock_response) as mock_post:
        client.generate_gemini_image(
            prompt="Compose the actor in the room.",
            model="nanobananapro",
            input_images=[
                {"mime_type": "image/png", "image_bytes": b"actor-front"},
                {"mime_type": "image/jpeg", "image_bytes": b"actor-three-quarter"},
                {"mime_type": "image/png", "image_bytes": b"location"},
            ],
        )

    parts = mock_post.call_args.kwargs["json"]["contents"][0]["parts"]
    assert [part.get("inlineData", {}).get("mimeType") for part in parts[1:]] == [
        "image/png",
        "image/jpeg",
        "image/png",
    ]
    assert [base64.b64decode(part["inlineData"]["data"]) for part in parts[1:]] == [
        b"actor-front",
        b"actor-three-quarter",
        b"location",
    ]


def test_gemini_text_generation_accepts_ordered_input_images():
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "accepted"}]}}]
    }

    with patch.object(client.gemini_http_client, "post", return_value=mock_response) as mock_post:
        assert client.generate_gemini_text(
            prompt="Compare Image 1 with Image 2.",
            input_images=[
                {"mime_type": "image/png", "image_bytes": b"approved-master"},
                {"mime_type": "image/jpeg", "image_bytes": b"contact-sheet"},
            ],
        ) == "accepted"

    parts = mock_post.call_args.kwargs["json"]["contents"][0]["parts"]
    assert parts == [
        {"text": "Compare Image 1 with Image 2."},
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"approved-master").decode("ascii"),
            }
        },
        {
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(b"contact-sheet").decode("ascii"),
            }
        },
    ]


@pytest.mark.parametrize(
    "invalid_image",
    [
        {"mime_type": "application/octet-stream", "image_bytes": b"master"},
        {"mime_type": "image/png", "image_bytes": b""},
    ],
)
def test_gemini_text_generation_rejects_invalid_input_images(invalid_image):
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True

    with pytest.raises(ValidationError, match="input images"):
        client.generate_gemini_text(
            prompt="Compare Image 1 with Image 2.",
            input_images=[invalid_image],
        )


def test_blog_content_from_llm_requires_real_intro_and_closing():
    with pytest.raises(PydanticValidationError):
        build_blog_content_from_llm(
            {
                "name": "Unvollstaendiger Blog",
                "slug": "unvollstaendiger-blog",
                "merksatz": "Merksatz",
                "tipp": "Tipp",
                "summary_bullets": ["Eins", "Zwei", "Drei"],
                "intro_heading": "Einleitung",
                "introduction_paragraphs": [],
                "sections": [
                    {"heading": "A", "paragraphs": ["Text"], "bullets": []},
                    {"heading": "B", "paragraphs": ["Text"], "bullets": []},
                    {"heading": "C", "paragraphs": ["Text"], "bullets": []},
                ],
                "conclusion_heading": "Schluss",
                "conclusion_paragraphs": [],
                "preview_text": "Kurztext",
                "meta_title": "Meta Titel",
                "meta_description": "Meta Beschreibung",
            },
            dossier_id="dossier-123",
        )


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
            {"slug": "hauptbild", "displayName": "Hauptbild"},
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
            "preview_image_url": "https://images.example.com/Lippe Lift/thumb.jpg",
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
    assert field_data["vorschaubild"]["url"] == "https://images.example.com/Lippe%20Lift/thumb.jpg"
    assert field_data["hauptbild"]["url"] == "https://images.example.com/Lippe%20Lift/thumb.jpg"


def test_webflow_client_build_blog_field_data_maps_sources_to_rich_text_field():
    client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
    client._collection_cache = {
        "fields": [
            {"slug": "merksatz", "displayName": "Merksatz"},
            {"slug": "tipp", "displayName": "Tipp"},
            {"slug": "zusammenfassung", "displayName": "Zusammenfassung"},
            {"slug": "inhalt", "displayName": "Inhalt"},
            {"slug": "quellen", "displayName": "Quellen"},
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
            "name": "Blog mit Quellen",
            "slug": "blog-mit-quellen",
            "merksatz": "Merksatz",
            "tipp": "Tipp",
            "summary_html": "<h2>Zusammenfassung</h2>",
            "body_html": "<p>Text</p>",
            "preview_text": "Vorschautext",
            "reading_time": "1 Minute",
            "author_name": "Patrick Berg",
            "meta_title": "Meta Titel",
            "meta_description": "Meta Beschreibung",
            "sources": [
                {"title": "Quelle A", "url": "https://example.com/a?x=1&y=2"},
                {"title": "Quelle B", "url": "https://example.com/b"},
            ],
        },
        publication_date="2026-04-01T09:00:00Z",
    )

    assert field_data["quellen"] == (
        "<ul>"
        '<li><a href="https://example.com/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">Quelle A</a></li>'
        '<li><a href="https://example.com/b" target="_blank" rel="noopener noreferrer">Quelle B</a></li>'
        "</ul>"
    )


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
    assert mock_request.call_args_list[0].args[1].endswith("?skipInvalidFiles=false")
    assert mock_request.call_args_list[0].kwargs["json"]["fieldData"]["name"] == "Test Blog"
    assert mock_request.call_args_list[1].kwargs["json"] == {"itemIds": ["wf-item-123"]}


def test_webflow_client_assert_item_has_images_rejects_skipped_image_import():
    client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
    client.get_item = MagicMock(return_value={"fieldData": {"vorschaubild": None}})

    with pytest.raises(Exception, match="missing expected image fields"):
        client.assert_item_has_images("wf-item-123", ["vorschaubild"])


def test_delete_blog_post_publishes_site_after_webflow_delete(monkeypatch):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.delete_calls = []
            self.publish_calls = 0
            self.unpublish_calls = []

        def delete_item(self, item_id):
            self.delete_calls.append(item_id)

        def list_live_items(self, *, slug=None, limit=100, offset=0):
            return [{"id": "wf-1", "cmsLocaleId": "loc-1", "slug": slug}]

        def unpublish_live_items(self, items):
            self.unpublish_calls.append(items)
            return True

        def publish_site(self):
            self.publish_calls += 1

    fake_client = _FakeClient()
    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "published",
        "blog_webflow_item_id": "wf-1",
        "blog_content": {"name": "Title", "slug": "title", "preview_image_url": "https://example.com/image.png"},
    })
    monkeypatch.setattr(blog_runtime, "WebflowClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(blog_runtime, "get_settings", lambda: MagicMock(webflow_api_token="t", webflow_collection_id="c", webflow_site_id="s"))
    monkeypatch.setattr(blog_runtime, "update_blog_status", lambda *args, **kwargs: {"ok": True})

    result = blog_runtime.delete_blog_post("post-1")

    assert result["deleted_webflow_item"] is True
    assert result["site_published"] is True
    assert fake_client.unpublish_calls == [[{"id": "wf-1", "cmsLocaleId": "loc-1", "slug": "title"}]]
    assert fake_client.delete_calls == ["wf-1"]
    assert fake_client.publish_calls == 1


def test_delete_blog_post_skips_site_publish_errors(monkeypatch):
    class _FakeClient:
        def list_live_items(self, *, slug=None, limit=100, offset=0):
            return [{"id": "wf-1", "cmsLocaleId": "loc-1", "slug": slug}]

        def unpublish_live_items(self, items):
            self.unpublished = items
            return True

        def delete_item(self, item_id):
            self.deleted = item_id

        def publish_site(self):
            raise ThirdPartyError(message="missing scopes", details={"status": 403})

    fake_client = _FakeClient()
    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "published",
        "blog_webflow_item_id": "wf-1",
        "blog_content": {"name": "Title", "slug": "title", "preview_image_url": "https://example.com/image.png"},
    })
    monkeypatch.setattr(blog_runtime, "WebflowClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(blog_runtime, "get_settings", lambda: MagicMock(webflow_api_token="t", webflow_collection_id="c", webflow_site_id="s"))
    monkeypatch.setattr(blog_runtime, "update_blog_status", lambda *args, **kwargs: {"ok": True})

    result = blog_runtime.delete_blog_post("post-1")

    assert result["deleted_webflow_item"] is True
    assert result["site_published"] is False
    assert fake_client.unpublished == [{"id": "wf-1", "cmsLocaleId": "loc-1", "slug": "title"}]


def test_webflow_client_unpublish_live_items_uses_bulk_endpoint():
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_response.text = ""

    with patch.object(httpx.Client, "request", return_value=mock_response) as mock_request:
        client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
        result = client.unpublish_live_items([{"id": "wf-item-123", "cmsLocaleId": "loc-1"}])

    assert result is True
    assert mock_request.call_args.args[0] == "DELETE"
    assert mock_request.call_args.args[1] == "/collections/col-1/items/live"
    assert mock_request.call_args.kwargs["json"] == {"items": [{"id": "wf-item-123", "cmsLocaleId": "loc-1"}]}


def test_gemini_image_generation_decodes_inline_image_bytes():
    client = LLMClient()
    client.gemini_provider = "gemini_api"
    client.gemini_api_fallback_enabled = True
    png_bytes = b"\x89PNG\r\n\x1a\nimage-bytes"
    encoded = base64.b64encode(png_bytes).decode("ascii")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"candidates":[{"content":{"parts":[{"inlineData":{"mimeType":"image/png","data":"%s"}}]}}]}' % encoded
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/png", "data": encoded}},
                    ]
                }
            }
        ]
    }

    with patch.object(client.gemini_http_client, "post", return_value=mock_response) as mock_post:
        result = client.generate_gemini_image("Create a blog cover image")

    assert result["image_bytes"] == png_bytes
    assert result["mime_type"] == "image/png"
    assert mock_post.call_args.kwargs["params"]["key"]


def test_generate_blog_image_converts_provider_png_to_webp_before_upload(monkeypatch):
    png_bytes = _make_png_bytes()
    captured_upload = {}
    update_calls = []

    fake_post = {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "draft",
        "blog_content": {
            "name": "Title",
            "slug": "title",
            "body_html": "<p>Text</p>",
            "image_prompt": "Quadratisches Coverbild",
            "preview_image_url": None,
        },
    }

    class _FakeLLM:
        def generate_gemini_image(self, **kwargs):
            assert kwargs["prompt"] == "Quadratisches Coverbild"
            return {
                "image_bytes": png_bytes,
                "mime_type": "image/png",
                "model": "gemini-2.5-flash-image",
            }

    class _FakeStorage:
        def upload_image(self, **kwargs):
            captured_upload.update(kwargs)
            return {
                "url": f"https://cdn.example.com/blog/{kwargs['file_name']}",
                "storage_key": f"Lippe Lift Studio/images/{kwargs['file_name']}",
            }

    def _fake_update_blog_status(post_id, **kwargs):
        update_calls.append((post_id, kwargs))
        return {"id": post_id, **kwargs}

    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: fake_post)
    monkeypatch.setattr(
        blog_runtime,
        "_lookup_dossier",
        lambda _post: {"id": "dossier-1", "normalized_payload": {"topic": "Title"}},
    )
    monkeypatch.setattr(blog_runtime, "get_llm_client", lambda: _FakeLLM())
    monkeypatch.setattr(blog_runtime, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(blog_runtime, "update_blog_status", _fake_update_blog_status)

    result = blog_runtime.generate_blog_image("post-1")

    assert captured_upload["file_name"] == "title.webp"
    assert captured_upload["content_type"] == "image/webp"
    assert captured_upload["image_bytes"].startswith(b"RIFF")
    assert captured_upload["image_bytes"][8:12] == b"WEBP"
    assert result["preview_image_url"] == "https://cdn.example.com/blog/title.webp"
    assert result["storage_key"] == "Lippe Lift Studio/images/title.webp"
    assert update_calls[0][0] == "post-1"
    assert update_calls[0][1]["blog_content"]["preview_image_url"] == "https://cdn.example.com/blog/title.webp"


def test_generate_blog_image_uploads_webflow_required_dimensions(monkeypatch):
    png_buffer = io.BytesIO()
    Image.new("RGB", (1024, 1024), (24, 94, 160)).save(png_buffer, format="PNG")
    captured_upload = {}

    fake_post = {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "draft",
        "blog_content": {
            "name": "Title",
            "slug": "title",
            "body_html": "<p>Text</p>",
            "image_prompt": "Landscape cover image",
            "preview_image_url": None,
        },
    }

    class _FakeLLM:
        def generate_gemini_image(self, **_kwargs):
            return {
                "image_bytes": png_buffer.getvalue(),
                "mime_type": "image/png",
                "model": "gemini-2.5-flash-image",
            }

    class _FakeStorage:
        def upload_image(self, **kwargs):
            captured_upload.update(kwargs)
            return {
                "url": f"https://cdn.example.com/blog/{kwargs['file_name']}",
                "storage_key": f"Lippe Lift Studio/images/{kwargs['file_name']}",
            }

    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: fake_post)
    monkeypatch.setattr(
        blog_runtime,
        "_lookup_dossier",
        lambda _post: {"id": "dossier-1", "normalized_payload": {"topic": "Title"}},
    )
    monkeypatch.setattr(blog_runtime, "get_llm_client", lambda: _FakeLLM())
    monkeypatch.setattr(blog_runtime, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(blog_runtime, "update_blog_status", lambda post_id, **kwargs: {"id": post_id, **kwargs})

    blog_runtime.generate_blog_image("post-1")

    with Image.open(io.BytesIO(captured_upload["image_bytes"])) as uploaded:
        assert uploaded.format == "WEBP"
        assert uploaded.size == (1150, 850)


def test_blog_toggle_endpoint_is_registered():
    client = TestClient(app)
    response = client.put("/blog/posts/nonexistent/blog-toggle", allow_redirects=False)
    assert response.status_code != 404


def test_blog_generate_endpoint_is_registered():
    client = TestClient(app)
    response = client.post("/blog/posts/00000000-0000-0000-0000-000000000000/blog/generate", allow_redirects=False)
    assert response.status_code != 404


def test_blog_image_generate_endpoint_is_registered():
    client = TestClient(app)
    response = client.post("/blog/posts/00000000-0000-0000-0000-000000000000/blog/image/generate", allow_redirects=False)
    assert response.status_code != 404


def test_blog_generate_endpoint_auto_generates_preview_image(monkeypatch):
    draft_calls = []
    image_calls = []

    def _fake_generate_blog_draft(post_id):
        draft_calls.append(post_id)
        return {
            "name": "Titel",
            "slug": "titel",
            "body_html": "<p>Text</p>",
            "image_prompt": "Quadratisches Coverbild",
            "preview_image_url": None,
        }

    def _fake_generate_blog_image(post_id, *, image_prompt=None):
        image_calls.append((post_id, image_prompt))
        return {
            "post_id": post_id,
            "image_prompt": image_prompt,
            "preview_image_url": "https://cdn.example.com/blog/titel.png",
            "image_model": "gemini-2.5-flash-image",
        }

    monkeypatch.setattr("app.features.blog.blog_runtime.generate_blog_draft", _fake_generate_blog_draft)
    monkeypatch.setattr("app.features.blog.blog_runtime.generate_blog_image", _fake_generate_blog_image)

    response = asyncio.run(blog_handlers.generate_blog_draft("post-1"))

    payload = response.model_dump()
    assert payload["data"]["preview_image_url"] == "https://cdn.example.com/blog/titel.png"
    assert payload["data"]["image_model"] == "gemini-2.5-flash-image"
    assert draft_calls == ["post-1"]
    assert image_calls == [("post-1", "Quadratisches Coverbild")]


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
    assert "Bildprompt:" in prompt


def test_update_blog_status_fails_closed_for_scheduled_legacy_status_constraint(monkeypatch):
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

    with pytest.raises(FlowForgeException, match="Failed to persist scheduled blog status"):
        blog_queries.update_blog_status("post-1", status="publishing", scheduled_at="2026-04-01T09:00:00Z")

    assert table.update_calls[0]["blog_status"] == "publishing"
    assert table.update_calls[0]["blog_scheduled_at"] == "2026-04-01T09:00:00Z"
    assert len(table.update_calls) == 1


def test_update_blog_status_preserves_manual_publishing_legacy_fallback(monkeypatch):
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

    updated = blog_queries.update_blog_status("post-1", status="publishing")

    assert table.update_calls[0]["blog_status"] == "publishing"
    assert table.update_calls[1]["blog_status"] == "draft"
    assert "blog_scheduled_at" not in table.update_calls[1]
    assert updated["blog_status"] == "draft"


def test_update_blog_status_preserves_clear_schedule_legacy_fallback(monkeypatch):
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
                raise Exception("column posts.blog_scheduled_at does not exist")
            return _FakeResponse([self._payload])

    table = _FakeTable()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: table)})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    updated = blog_queries.update_blog_status("post-1", status="published", clear_scheduled_at=True)

    assert table.update_calls[0] == {"blog_status": "published", "blog_scheduled_at": None}
    assert table.update_calls[1] == {"blog_status": "published"}
    assert updated["blog_status"] == "published"


def test_publish_blog_post_requires_accepted_image_when_prompt_exists(monkeypatch):
    class _FakePost(dict):
        pass

    fake_post = _FakePost(
        id="post-1",
        blog_enabled=True,
        blog_status="draft",
        blog_webflow_item_id="wf-1",
        blog_content={
            "name": "Title",
            "slug": "title",
            "merksatz": "Merksatz",
            "tipp": "Tipp",
            "summary_html": "<h2>Summary</h2><ul><li>A</li></ul>",
            "body_html": "<h2>Body</h2><p>Text</p>",
            "preview_text": "Preview",
            "reading_time": "1 Minute",
            "meta_title": "Meta",
            "meta_description": "Meta desc",
            "image_prompt": "Prompt exists",
            "preview_image_url": None,
        },
    )

    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: fake_post)

    with pytest.raises(ValueError, match="Generate and accept a preview image"):
        blog_runtime.publish_blog_post("post-1")


def test_generate_blog_draft_retries_until_contract_is_complete(monkeypatch):
    fake_post = {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "pending",
        "blog_scheduled_at": None,
        "seed_data": {"script_review_status": "approved"},
        "topic_title": "Barrierefreie Arzttermine",
    }
    fake_dossier = {
        "id": "dossier-1",
        "normalized_payload": {
            "topic": "Barrierefreie Arzttermine",
            "cluster_summary": "So funktionieren bessere Terminwege.",
            "source_summary": "Ein klarer Leitfaden fuer Terminwege.",
            "facts": [
                "Viele Betroffene verlieren Zeit durch unklare Rueckrufprozesse.",
                "Klare digitale und telefonische Wege reduzieren Reibung.",
                "Transparente Schritte bauen Unsicherheit ab.",
            ],
            "angle_options": [
                "Wo Terminwege heute scheitern",
                "Wie gute Prozesse aussehen",
                "Welche Schritte sofort helfen",
            ],
            "sources": [{"title": "Quelle", "url": "https://example.com"}],
            "risk_notes": [],
            "disclaimer": "",
        },
    }

    invalid_text = """Name: Barrierefreie Arzttermine ohne Warteschleife
Slug: barrierefreie-arzttermine-ohne-warteschleife
Merksatz: Gute Terminwege beginnen bei klaren Kontaktpunkten.
Tipp: Frage sofort nach dem naechsten konkreten Schritt.
Zusammenfassung:
- Unklare Rueckrufe kosten Zeit
- Gute Prozesse schaffen Sicherheit
- Klare Schritte helfen sofort
Einleitung:
### Warum Terminwege oft scheitern
Abschnitt 1:
### Wo heute Reibung entsteht
Viele Wege bleiben unklar.
Abschnitt 2:
### Was gute Prozesse besser machen
Klare Ablaeufe helfen.
Abschnitt 3:
### Was Betroffene sofort tun koennen
Dokumentation hilft.
Schluss:
### Was jetzt wichtig ist
Vorschautext: Gute Terminwege reduzieren Stress und Wartezeit.
Meta-Titel: Gute Terminwege beim Arzt
Meta-Beschreibung: So werden Arzttermine fuer Betroffene klarer und verlaesslicher.
"""
    valid_text = """Name: Barrierefreie Arzttermine ohne Warteschleife
Slug: barrierefreie-arzttermine-ohne-warteschleife
Merksatz: Gute Terminwege beginnen bei klaren Kontaktpunkten.
Tipp: Frage sofort nach dem naechsten konkreten Schritt.
Bildprompt: Quadratisches Coverbild zu barrierefreien Arztterminen, freundlich, realistisch, ohne Text.
Zusammenfassung:
- Unklare Rueckrufe kosten Zeit
- Gute Prozesse schaffen Sicherheit
- Klare Schritte helfen sofort
Einleitung:
### Warum Terminwege oft scheitern
Wer einen Arzttermin organisieren muss, braucht schnelle Klarheit statt neuer Schleifen.
Gerade bei Rueckrufen oder Formularen wird aus einem kleinen Schritt schnell ein grosser Aufwand.
Abschnitt 1:
### Wo heute Reibung entsteht
Viele Praxen haben keine klar erkennbare Reihenfolge fuer Rueckruf, Terminwahl und Rueckmeldung.
Dadurch wissen Betroffene oft nicht, ob sie warten, erneut anrufen oder Unterlagen nachreichen sollen.
Abschnitt 2:
### Wie gute Prozesse Sicherheit geben
Ein guter Terminweg nennt den naechsten Schritt, den richtigen Kanal und den erwartbaren Zeitraum.
So entsteht Verlaesslichkeit, weil niemand raten muss, was als Naechstes passiert.
Abschnitt 3:
### Welche Schritte sofort helfen
Notiere Kontaktzeit, Anliegen und zugesagte Rueckmeldung direkt nach jedem Gespraech.
Damit kannst du nachfassen, ohne wieder bei null zu beginnen.
Schluss:
### Was am Ende wirklich entlastet
Barrierefreie Terminwege sind nicht kompliziert, wenn Kontaktpunkte, Wartezeiten und Rueckmeldungen klar benannt werden.
Genau diese Klarheit nimmt Unsicherheit aus dem Prozess und macht den Alltag planbarer.
Vorschautext: Gute Terminwege reduzieren Stress und Wartezeit. Diese Struktur schafft mehr Verlaesslichkeit im Alltag.
Meta-Titel: Gute Terminwege beim Arzt
Meta-Beschreibung: So werden Arzttermine fuer Betroffene klarer und verlaesslicher.
"""

    class _FakeLLM:
        def __init__(self):
            self.calls = 0

        def generate_gemini_text(self, **_kwargs):
            self.calls += 1
            return invalid_text if self.calls == 1 else valid_text

    update_calls = []

    def _fake_update_blog_status(post_id, **kwargs):
        update_calls.append((post_id, kwargs))
        return {"id": post_id, **kwargs}

    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _post_id: fake_post)
    monkeypatch.setattr(blog_runtime, "_lookup_dossier", lambda _post: fake_dossier)
    monkeypatch.setattr(blog_runtime, "get_llm_client", lambda: _FakeLLM())
    monkeypatch.setattr(blog_runtime, "update_blog_status", _fake_update_blog_status)

    content = blog_runtime.generate_blog_draft("post-1")

    assert content["name"] == "Barrierefreie Arzttermine ohne Warteschleife"
    assert content["intro_heading"] == "Warum Terminwege oft scheitern"
    assert content["conclusion_heading"] == "Was am Ende wirklich entlastet"
    assert len(content["introduction_paragraphs"]) == 2
    assert len(update_calls) == 2
    assert update_calls[0][1]["status"] == "generating"
    assert update_calls[1][1]["status"] == "draft"


def test_generate_all_blog_drafts_fills_missing_preview_images(monkeypatch):
    generated = []
    imaged = []

    monkeypatch.setattr(
        "app.features.blog.handlers.get_blog_enabled_posts",
        lambda _batch_id: [
            {
                "id": "post-1",
                "seed_data": {"script_review_status": "approved"},
                "blog_status": "pending",
                "blog_content": {},
            },
            {
                "id": "post-2",
                "seed_data": {"script_review_status": "approved"},
                "blog_status": "draft",
                "blog_content": {
                    "name": "Bestehender Draft",
                    "slug": "bestehender-draft",
                    "body_html": "<p>Text</p>",
                    "image_prompt": "Bild fuer bestehenden Draft",
                    "preview_image_url": None,
                },
            },
        ],
    )

    def _fake_generate_blog_draft(post_id):
        generated.append(post_id)
        return {
            "name": f"Draft {post_id}",
            "slug": f"draft-{post_id}",
            "body_html": "<p>Text</p>",
            "image_prompt": f"Bild fuer {post_id}",
            "preview_image_url": None,
        }

    def _fake_generate_blog_image(post_id, *, image_prompt=None):
        imaged.append((post_id, image_prompt))
        return {
            "post_id": post_id,
            "image_prompt": image_prompt,
            "preview_image_url": f"https://cdn.example.com/{post_id}.png",
            "image_model": "gemini-2.5-flash-image",
        }

    monkeypatch.setattr("app.features.blog.blog_runtime.generate_blog_draft", _fake_generate_blog_draft)
    monkeypatch.setattr("app.features.blog.blog_runtime.generate_blog_image", _fake_generate_blog_image)

    response = asyncio.run(blog_handlers.generate_all_blog_drafts("batch-1"))

    payload = response.model_dump()
    assert generated == ["post-1"]
    assert imaged == [
        ("post-1", "Bild fuer post-1"),
        ("post-2", "Bild fuer bestehenden Draft"),
    ]
    assert payload["data"]["results"] == [
        {"post_id": "post-1", "status": "draft", "image_generated": True},
        {"post_id": "post-2", "status": "draft", "image_generated": True},
    ]
