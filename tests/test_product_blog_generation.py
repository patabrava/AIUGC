"""Product blogs use approved knowledge through the real draft/image pipeline."""

import asyncio
import copy
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.features.blog import blog_runtime as runtime, handlers, queries
from app.features.blog.schemas import BlogContent
from app.features.blog.webflow_client import WebflowClient


ARTICLE = """Name: VARIO PLUS auf unterschiedlichen Treppen
Slug: vario-plus-treppen
Merksatz: Die Einbausituation entscheidet über die passende Ausführung.
Tipp: Lassen Sie Ihre Treppe vor der Auswahl prüfen.
Zusammenfassung:
- Für gerade und kurvige Treppen
- Als Plattformlift oder Sitzlift
- Auch für den Außenbereich
Einleitung:
### Wenn Treppen den Alltag erschweren
Der VARIO PLUS lässt sich an unterschiedliche Treppen anpassen.
Abschnitt 1:
### Gerade und kurvige Treppen
Der Lift kann auf geraden und kurvigen Treppen eingesetzt werden.
Abschnitt 2:
### Plattform oder Sitz
Die Nutzung als Plattformlift oder Sitzlift ist möglich.
Abschnitt 3:
### Die konkrete Einbausituation
Der Einsatz ist im Innenbereich und Außenbereich möglich.
Schluss:
### Die passende Ausführung prüfen
Lassen Sie die konkrete Einbausituation prüfen.
Vorschautext: Welche Einsatzmöglichkeiten bietet der VARIO PLUS?
Meta-Titel: VARIO PLUS für unterschiedliche Treppen
Meta-Beschreibung: Plattformlift oder Sitzlift: Möglichkeiten des VARIO PLUS und die Prüfung Ihrer Einbausituation.
"""


def product_post():
    return {
        "id": "product-post", "post_type": "product", "topic_title": "Überraschend vielseitig unterwegs",
        "blog_enabled": True, "blog_status": "failed", "blog_content": {},
        "blog_scheduled_at": None, "blog_published_at": None, "blog_webflow_item_id": None,
        "seed_data": {"script_review_status": "approved", "product_name": "VARIO PLUS",
                      "strict_seed": {"facts": ["UNAPPROVED FACT"]},
                      "source_urls": ["https://example.com/invented"]},
    }


def test_product_generate_endpoint_saves_draft_and_image_without_dossier_or_cms(monkeypatch):
    row = product_post()
    seed_before = copy.deepcopy(row["seed_data"])
    db = MagicMock()
    db.client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [row]
    monkeypatch.setattr(queries, "get_supabase", lambda: db)
    monkeypatch.setattr(runtime, "seo_catalog_enabled", lambda: False)
    dossier_lookup = MagicMock(side_effect=AssertionError("Product must not require a research dossier"))
    monkeypatch.setattr(runtime, "_lookup_dossier", dossier_lookup)
    cms = MagicMock(side_effect=AssertionError("Draft generation must not publish"))
    monkeypatch.setattr(runtime, "WebflowClient", cms)
    writes, prompts, uploads = [], [], []

    def save(post_id, *, status, blog_content=None):
        assert post_id == row["id"]
        writes.append(status)
        row["blog_status"] = status
        if blog_content is not None:
            row["blog_content"] = blog_content

    png = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(png, format="PNG")

    class LLM:
        def generate_gemini_text(self, **kwargs):
            prompts.append(kwargs)
            return "Photographic editorial cover of a platform lift." if kwargs.get("system_prompt") else ARTICLE

        def generate_gemini_image(self, **kwargs):
            assert kwargs["prompt"] == "Photographic editorial cover of a platform lift."
            return {"image_bytes": png.getvalue(), "model": "test-image-model"}

    class Storage:
        def upload_image(self, **kwargs):
            uploads.append(kwargs)
            return {"url": "https://cdn.example.com/product.webp", "storage_key": "product.webp"}

    monkeypatch.setattr(runtime, "update_blog_status", save)
    monkeypatch.setattr(runtime, "get_llm_client", LLM)
    monkeypatch.setattr(runtime, "get_storage_client", Storage)
    result = asyncio.run(handlers.generate_blog_draft(row["id"]))
    assert result.ok
    content = row["blog_content"]
    BlogContent.model_validate(content)
    assert content["body_html"] and content["preview_image_url"].endswith("product.webp")
    assert content["dossier_id"] == ""
    assert content["sources"] == [{"title": "LIPPE Lift Produktwissen – VARIO PLUS", "url": ""}]
    assert "UNAPPROVED FACT" not in prompts[0]["prompt"]
    assert "example.com/invented" not in prompts[0]["prompt"]
    assert "Tragfähigkeit: 300 kg" in prompts[0]["prompt"]
    assert "Freigegebene interne Produktinformationen" in prompts[0]["prompt"]
    assert "Do not generate the image." in prompts[1]["system_prompt"]
    assert queries.get_blog_preview_source_names(row["id"]) == ["LIPPE Lift Produktwissen – VARIO PLUS"]
    assert WebflowClient(api_token="test", collection_id="test", site_id="test")._render_sources_rich_text(content["sources"]) == "<ul><li>LIPPE Lift Produktwissen – VARIO PLUS</li></ul>"
    assert writes == ["generating", "draft", "draft"]
    assert uploads[0]["content_type"] == "image/webp"
    assert row["seed_data"] == seed_before
    assert row["blog_scheduled_at"] is row["blog_published_at"] is row["blog_webflow_item_id"] is None
    dossier_lookup.assert_not_called()
    cms.assert_not_called()
    db.client.table.return_value.update.assert_not_called()
    assert "post_type" in db.client.table.return_value.select.call_args.args[0]


@pytest.mark.parametrize("name", ["", "Invented Lift", "LL12"])
def test_unknown_missing_or_inactive_product_fails_without_generation(monkeypatch, name):
    row = product_post()
    row["seed_data"]["product_name"] = name
    monkeypatch.setattr(runtime, "_load_post_for_blog", lambda _: row)
    monkeypatch.setattr(runtime, "update_blog_status", lambda *a, **kw: None)
    llm = MagicMock(side_effect=AssertionError("No approved facts, no generation"))
    monkeypatch.setattr(runtime, "get_llm_client", llm)
    result = runtime.generate_blog_draft(row["id"])
    assert result["error"] == "No approved product knowledge found for this post's product."
    llm.assert_not_called()
    with pytest.raises(ValueError, match="No approved product knowledge"):
        runtime._resolve_blog_input(row)


def test_product_type_legacy_fallback_and_authoritative_post_type(monkeypatch):
    row = product_post()
    row.pop("post_type")
    row["seed_data"]["post_type"] = "product"
    payload, dossier_id = runtime._resolve_blog_input(row)
    assert payload["facts"] and dossier_id == ""
    row["post_type"] = "value"
    dossier = {"id": "real-dossier", "normalized_payload": {"topic": "Research topic", "facts": ["Research fact"]}}
    monkeypatch.setattr(runtime, "_lookup_dossier", lambda p: dossier)
    monkeypatch.setattr(runtime, "get_product_knowledge_base", MagicMock(side_effect=AssertionError("Not a product")))
    assert runtime._resolve_blog_input(row) == (dossier["normalized_payload"], "real-dossier")


def test_nonproduct_missing_dossier_keeps_original_failure(monkeypatch):
    monkeypatch.setattr(runtime, "_lookup_dossier", lambda p: None)
    with pytest.raises(ValueError, match="No research dossier found for this topic"):
        runtime._resolve_blog_input({"post_type": "value"})
