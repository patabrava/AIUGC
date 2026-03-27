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
