# tests/test_blog_feature.py
from __future__ import annotations

import os

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

from app.features.blog.schemas import BlogContent, BlogSource


def test_blog_content_valid():
    content = BlogContent(
        title="Warum dein Sparkonto dich arm macht",
        body="Dein Sparkonto frisst dein Geld. " * 50,
        slug="sparkonto-geld-verlieren",
        meta_description="Deutsche Sparer verlieren jährlich Milliarden.",
        sources=[BlogSource(title="Bundesbank.de", url="https://bundesbank.de/stats")],
        word_count=742,
        generated_at="2026-03-27T14:00:00Z",
        dossier_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert content.title == "Warum dein Sparkonto dich arm macht"
    assert len(content.sources) == 1
    assert content.word_count == 742


def test_blog_content_rejects_empty_title():
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        BlogContent(
            title="",
            body="Some body text " * 50,
            slug="test-slug",
            meta_description="Test description",
            sources=[],
            word_count=100,
            generated_at="2026-03-27T14:00:00Z",
            dossier_id="550e8400-e29b-41d4-a716-446655440000",
        )


def test_blog_content_rejects_empty_body():
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        BlogContent(
            title="Valid Title",
            body="",
            slug="test-slug",
            meta_description="Test description",
            sources=[],
            word_count=0,
            generated_at="2026-03-27T14:00:00Z",
            dossier_id="550e8400-e29b-41d4-a716-446655440000",
        )


from unittest.mock import patch, MagicMock


def _mock_supabase_post(post_data):
    """Helper: create a mock supabase client that returns given post data."""
    mock_response = MagicMock()
    mock_response.data = [post_data]

    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.execute.return_value = mock_response
    mock_table.update.return_value = mock_table

    mock_client = MagicMock()
    mock_client.client.table.return_value = mock_table

    return mock_client


def test_toggle_blog_enabled_on():
    from app.features.blog.queries import toggle_blog_enabled

    post_data = {
        "id": "post-1",
        "blog_enabled": True,
        "blog_status": "pending",
        "seed_data": {"script_review_status": "approved"},
    }
    mock_sb = _mock_supabase_post(post_data)

    with patch("app.features.blog.queries.get_supabase", return_value=mock_sb):
        result = toggle_blog_enabled("post-1", enabled=True)

    assert result["blog_enabled"] is True
    assert result["blog_status"] == "pending"


def test_webflow_client_create_item_sends_correct_payload():
    import httpx
    from unittest.mock import patch, MagicMock
    from app.features.blog.webflow_client import WebflowClient

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "wf-item-123", "fieldData": {"name": "Test"}}

    with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
        client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
        item_id = client.create_item({
            "name": "Test Blog",
            "slug": "test-blog",
            "post-body": "<p>Hello</p>",
            "meta-description": "Test",
        })

    assert item_id == "wf-item-123"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or (call_kwargs[1].get("json") if len(call_kwargs) > 1 else None)
    assert payload is not None
    assert "fieldData" in payload


def test_blog_runtime_builds_prompt_from_dossier():
    from app.features.blog.blog_runtime import _build_blog_prompt

    dossier_payload = {
        "topic": "Inflation und Sparkonten",
        "cluster_summary": "Deutsche Sparer verlieren real Geld durch Niedrigzinsen.",
        "facts": ["Inflation 3.8%", "Sparzins 0.5%"],
        "angle_options": ["Kaufkraftverlust", "Alternativen zum Sparen"],
        "sources": [{"title": "Bundesbank.de", "url": "https://bundesbank.de"}],
        "source_summary": "Bundesbank-Daten zeigen realen Verlust.",
        "risk_notes": ["Keine Anlageberatung"],
        "disclaimer": "Dieser Artikel stellt keine Finanzberatung dar.",
    }

    prompt = _build_blog_prompt(dossier_payload)

    assert "Inflation und Sparkonten" in prompt
    assert "Bundesbank.de" in prompt
    assert "500–800 Wörter" in prompt


def test_blog_runtime_parses_valid_llm_response():
    from app.features.blog.blog_runtime import _parse_blog_response

    raw_response = '{"title": "Test Titel", "body": "Ein Absatz. Noch ein Absatz.", "slug": "test-titel", "meta_description": "Kurze Beschreibung"}'

    result = _parse_blog_response(raw_response, dossier_id="dossier-123")

    assert result["title"] == "Test Titel"
    assert result["slug"] == "test-titel"
    assert result["dossier_id"] == "dossier-123"
    assert result["word_count"] == 5


def test_blog_runtime_handles_invalid_llm_response():
    from app.features.blog.blog_runtime import _parse_blog_response

    result = _parse_blog_response("This is not JSON at all", dossier_id="dossier-123")

    assert result.get("error") is not None


from fastapi.testclient import TestClient
from app.main import app


def test_blog_toggle_endpoint_is_registered():
    """Verify the blog toggle endpoint exists (not 404)."""
    client = TestClient(app)
    response = client.put("/blog/posts/nonexistent/blog-toggle")
    # Should not be 404 (route not found) — 500 is expected with test Supabase credentials
    assert response.status_code != 404
