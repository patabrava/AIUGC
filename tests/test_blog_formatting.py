"""Regression coverage for Markdown leaking through stored blog representations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.features.blog.schemas import normalize_blog_content, render_body_html
from app.features.blog.webflow_client import WebflowClient


@pytest.mark.parametrize("body_key", ["body", "body_html"])
@pytest.mark.parametrize("as_html", [False, True])
def test_legacy_bodies_render_bold_and_normalize_idempotently(body_key, as_html):
    text = "**Wichtig:** Nutzer*innen vergleichen. **Kosten** prüfen."
    body = f"<h2>**Vergleich**</h2><p>{text}</p>" if as_html else text
    content = normalize_blog_content({"title": "**Liftvergleich**", body_key: body})

    assert "<strong>Wichtig:</strong> Nutzer*innen vergleichen." in content["body_html"]
    assert "<strong>Kosten</strong> prüfen." in content["body_html"]
    if as_html:
        assert "<h2>Vergleich</h2>" in content["body_html"]
    for field in ("body_html", "summary_html", "merksatz", "tipp", "preview_text", "name"):
        assert "**" not in content[field]
    assert content["body"] == content["body_html"]
    assert normalize_blog_content(content) == content


def test_stored_html_preserves_links_entities_and_escaped_markup():
    content = normalize_blog_content({
        "name": "Vergleich",
        "body_html": '<p>**A &amp; B**: **&lt;script&gt;alert(1)&lt;/script&gt;**</p>'
                     '<p><a href="https://example.com/?a=1&amp;b=2" title="A > B">**Details**</a>'
                     ' für Nutzer*innen, ** offen.</p>',
        "summary_html": '<h2>**Überblick**</h2><ul><li>**Preis:** 5 &lt; 10</li></ul>',
    })

    assert "<strong>A &amp; B</strong>" in content["body_html"]
    assert "<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>" in content["body_html"]
    assert '<a href="https://example.com/?a=1&amp;b=2" title="A > B"><strong>Details</strong></a>' in content["body_html"]
    assert "Nutzer*innen,  offen." in content["body_html"]
    assert content["summary_html"] == '<h2>Überblick</h2><ul><li><strong>Preis:</strong> 5 &lt; 10</li></ul>'
    assert normalize_blog_content(content) == content


def test_stored_html_does_not_rewrite_attribute_values():
    body = '<p><a href="https://example.com/**/" title="**literal**">**Link**</a></p>'
    content = normalize_blog_content({"name": "Titel", "body_html": body})
    assert content["body_html"] == '<p><a href="https://example.com/**/" title="**literal**"><strong>Link</strong></a></p>'


def test_introduction_only_draft_uses_structured_renderer():
    content = normalize_blog_content({
        "name": "Titel",
        "introduction_paragraphs": ["**Aktuell:** Nutzer*innen vergleichen."],
        "body_html": "<p>Veralteter Text</p>",
    })
    assert content["body_html"] == "<p><strong>Aktuell:</strong> Nutzer*innen vergleichen.</p>"


def test_unverified_link_token_cannot_bypass_bold_renderer():
    body = render_body_html(intro_heading=None,
                            introduction_paragraphs=["[[LINK:unknown|**Details**]] für Nutzer*innen"],
                            sections=[], conclusion_heading=None, conclusion_paragraphs=[])
    assert body == "<p>[[LINK:unknown|<strong>Details</strong>]] für Nutzer*innen</p>"
    assert "<a " not in body


@pytest.fixture
def webflow_client():
    client = WebflowClient("test-token", "test-collection", "test-site")
    client._collection_cache = {"fields": [
        {"slug": slug} for slug in (
            "merksatz", "tipp", "zusammenfassung", "inhalt", "veroeffentlichungsdatum",
            "vorschautext", "lesedauer", "vorschaubild", "autor", "meta-titel", "meta-beschreibung", "quellen",
        )
    ]}
    yield client
    client.http_client.close()


def test_webflow_mapping_normalizes_raw_drafts(webflow_client):
    fields = webflow_client.build_blog_field_data({
        "name": "**Titel**", "body_html": "<p>**Text** für Nutzer*innen.</p>",
        "summary_html": "<ul><li>**Merken**</li></ul>", "meta_title": "**SEO Titel**",
        "merksatz": "**Wichtig**", "tipp": "**Prüfen**", "preview_text": "**Vorschau**",
        "meta_description": "**Beschreibung**", "reading_time": "**1 Minute**", "slug": "**titel**",
        "sources": [{"title": "**Quelle**", "url": "https://example.com/source"}],
    }, publication_date="2026-10-24T12:00:00Z")
    assert fields["inhalt"] == "<p><strong>Text</strong> für Nutzer*innen.</p>"
    assert fields["zusammenfassung"] == "<ul><li><strong>Merken</strong></li></ul>"
    for key in ("name", "meta-titel", "merksatz", "tipp", "vorschautext", "meta-beschreibung", "lesedauer", "slug", "quellen"):
        assert "**" not in fields[key]


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("existing_item", [None, "existing-item"])
def test_publication_sends_repaired_content_and_persists_it(monkeypatch, webflow_client, scheduled, existing_item):
    from app.features.blog import blog_runtime

    post = {"id": "post-1", "blog_enabled": True, "blog_webflow_item_id": existing_item,
            "blog_content": {"name": "Titel", "body_html": "<p>**Wichtig:** vergleichen.</p>",
                             "summary_html": "<ul><li>**Merken**</li></ul>"}}
    monkeypatch.setattr(blog_runtime, "_load_post_for_blog", lambda _: post)
    monkeypatch.setattr(blog_runtime, "get_settings", lambda: SimpleNamespace(
        webflow_api_token="test-token", webflow_collection_id="test-collection", webflow_site_id="test-site"))
    monkeypatch.setattr(blog_runtime, "WebflowClient", lambda **_: webflow_client)
    requests = []

    def request(method, path, *, json=None):
        requests.append((method, path, json))
        return {"id": existing_item or "new-item"}

    monkeypatch.setattr(webflow_client, "_request", request)
    updates = []
    monkeypatch.setattr(blog_runtime, "update_blog_status", lambda post_id, **kwargs: updates.append(kwargs))
    if scheduled:
        monkeypatch.setattr(blog_runtime, "get_due_scheduled_blog_posts", lambda limit: [
            {"id": "post-1", "blog_scheduled_at": "2026-10-24T12:00:00Z"}])
        table = MagicMock()
        table.update.return_value = table
        table.eq.return_value = table
        table.execute.return_value = SimpleNamespace(data=[{"id": "post-1"}])
        monkeypatch.setattr(blog_runtime, "get_supabase", lambda: SimpleNamespace(
            client=SimpleNamespace(table=lambda _: table)))
        result = asyncio.run(blog_runtime.dispatch_due_blog_posts(trigger="test"))
        assert result["published"] == 1
        assert result["failed"] == 0
    else:
        blog_runtime.publish_blog_post("post-1")

    payload = requests[0][2]["fieldData"]
    assert requests[0][0] == ("PATCH" if existing_item else "POST")
    assert payload["inhalt"] == "<p><strong>Wichtig:</strong> vergleichen.</p>"
    assert payload["zusammenfassung"] == "<ul><li><strong>Merken</strong></li></ul>"
    assert requests[1][1].endswith("/items/publish")
    persisted = updates[-1]["blog_content"]
    assert persisted["body_html"] == payload["inhalt"]
    assert persisted["summary_html"] == payload["zusammenfassung"]
    assert updates[-1]["status"] == "published"
