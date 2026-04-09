"""
Lippe Lift Studio Webflow Client Adapter
Thin wrapper for Webflow CMS API v2.
Per Constitution § VI: Adapterize Specialists
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.core.errors import ThirdPartyError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

WEBFLOW_API_BASE = "https://api.webflow.com/v2"

_FIELD_CANDIDATES = {
    "merksatz": ("merksatz",),
    "tipp": ("tipp", "tip", "quote"),
    "summary": ("zusammenfassung", "summary"),
    "content": ("inhalt", "content", "post-body", "body"),
    "publish_date": ("veroeffentlichungsdatum", "veroffentlichungsdatum", "veröffentlichungsdatum", "publishdate", "publisheddate", "date"),
    "preview_text": ("vorschautext", "previewtext", "excerpt", "summarytext"),
    "reading_time": ("lesedauer", "readingtime", "readtime"),
    "preview_image": ("vorschaubild", "previewimage", "thumbnail", "image", "mainimage"),
    "author": ("autor", "author"),
    "meta_title": ("metatitel", "meta-title", "metatitle", "seotitle"),
    "meta_description": ("metabeschreibung", "meta-description", "metadescription", "seodescription"),
}


class WebflowClient:
    """Webflow CMS API client for creating/updating blog post items."""

    def __init__(self, api_token: str, collection_id: str, site_id: str):
        if not api_token:
            raise ThirdPartyError(
                message="Webflow API token not configured",
                details={"provider": "webflow"},
            )
        self.collection_id = collection_id
        self.site_id = site_id
        self.http_client = httpx.Client(
            base_url=WEBFLOW_API_BASE,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=None),
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._collection_cache: Optional[Dict[str, Any]] = None

    def _request(self, method: str, path: str, *, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.http_client.request(method, path, json=json)
        if response.status_code >= 400:
            logger.error(
                "webflow_request_error",
                method=method,
                path=path,
                status=response.status_code,
                body=response.text,
            )
            raise ThirdPartyError(
                message=f"Webflow request failed: {response.status_code}",
                details={"status": response.status_code, "path": path, "response": response.text[:1000]},
            )
        if not response.text:
            return {}
        return response.json()

    def get_collection_details(self) -> Dict[str, Any]:
        """Fetch the configured collection schema once per client instance."""
        if self._collection_cache is None:
            logger.info("webflow_get_collection", collection_id=self.collection_id)
            self._collection_cache = self._request("GET", f"/collections/{self.collection_id}")
        return self._collection_cache

    def get_collection_fields(self) -> List[Dict[str, Any]]:
        """Return collection fields from the Webflow collection response."""
        data = self.get_collection_details()
        if isinstance(data.get("fields"), list):
            return data["fields"]
        collection = data.get("collection")
        if isinstance(collection, dict) and isinstance(collection.get("fields"), list):
            return collection["fields"]
        raise ThirdPartyError(
            message="Webflow collection response did not include fields",
            details={"collection_id": self.collection_id, "response_keys": list(data.keys())},
        )

    def create_item(self, field_data: Dict[str, Any]) -> str:
        """Create a new staged CMS collection item. Returns the Webflow item ID."""
        payload = {"fieldData": field_data}
        logger.info("webflow_create_item", collection_id=self.collection_id, slug=field_data.get("slug"))
        data = self._request("POST", f"/collections/{self.collection_id}/items", json=payload)
        item_id = self._extract_item_id(data)
        logger.info("webflow_item_created", item_id=item_id)
        return item_id

    def update_item(self, item_id: str, field_data: Dict[str, Any]) -> str:
        """Update an existing staged CMS item. Returns the item ID."""
        payload = {"fieldData": field_data}
        logger.info("webflow_update_item", item_id=item_id)
        self._request("PATCH", f"/collections/{self.collection_id}/items/{item_id}", json=payload)
        return item_id

    def publish_item(self, item_id: str) -> bool:
        """Publish a staged item live via the collection publish endpoint."""
        logger.info("webflow_publish_item", collection_id=self.collection_id, item_id=item_id)
        self._request("POST", f"/collections/{self.collection_id}/items/publish", json={"itemIds": [item_id]})
        return True

    def delete_item(self, item_id: str) -> bool:
        """Delete an existing CMS item from the collection."""
        logger.info("webflow_delete_item", collection_id=self.collection_id, item_id=item_id)
        self._request("DELETE", f"/collections/{self.collection_id}/items/{item_id}")
        return True

    def publish_site(self) -> bool:
        """Trigger a site publish. Kept for compatibility with earlier rollout paths."""
        logger.info("webflow_publish_site", site_id=self.site_id)
        self._request(
            "POST",
            f"/sites/{self.site_id}/publish",
            json={"publishToWebflowSubdomain": True},
        )
        return True

    def build_blog_field_data(self, blog_content: Dict[str, Any], *, publication_date: str) -> Dict[str, Any]:
        """Map normalized blog JSON into the configured Webflow collection schema."""
        fields = self.get_collection_fields()
        field_data: Dict[str, Any] = {
            "name": blog_content.get("name", ""),
            "slug": blog_content.get("slug", ""),
        }

        required_fields = {
            "merksatz": self._require_field(fields, "merksatz"),
            "tipp": self._require_field(fields, "tipp"),
            "summary": self._require_field(fields, "summary"),
            "content": self._require_field(fields, "content"),
            "publish_date": self._require_field(fields, "publish_date"),
            "preview_text": self._require_field(fields, "preview_text"),
            "reading_time": self._require_field(fields, "reading_time"),
            "preview_image": self._require_field(fields, "preview_image"),
            "author": self._require_field(fields, "author"),
            "meta_title": self._require_field(fields, "meta_title"),
            "meta_description": self._require_field(fields, "meta_description"),
        }

        field_data[self._field_slug(required_fields["merksatz"])] = blog_content.get("merksatz", "")
        field_data[self._field_slug(required_fields["tipp"])] = blog_content.get("tipp", "")
        field_data[self._field_slug(required_fields["summary"])] = blog_content.get("summary_html", "")
        field_data[self._field_slug(required_fields["content"])] = blog_content.get("body_html", "")
        field_data[self._field_slug(required_fields["publish_date"])] = publication_date
        field_data[self._field_slug(required_fields["preview_text"])] = blog_content.get("preview_text", "")
        field_data[self._field_slug(required_fields["reading_time"])] = blog_content.get("reading_time", "")
        field_data[self._field_slug(required_fields["meta_title"])] = blog_content.get("meta_title", "")
        field_data[self._field_slug(required_fields["meta_description"])] = blog_content.get("meta_description", "")

        preview_image_url = (blog_content.get("preview_image_url") or "").strip()
        if preview_image_url:
            field_data[self._field_slug(required_fields["preview_image"])] = {
                "url": preview_image_url,
                "alt": blog_content.get("name", "Blog image"),
            }

        author_value = self._resolve_option_value(required_fields["author"], blog_content.get("author_name"))
        if author_value:
            field_data[self._field_slug(required_fields["author"])] = author_value

        return field_data

    def _field_slug(self, field: Dict[str, Any]) -> str:
        slug = field.get("slug")
        if not slug:
            raise ThirdPartyError(
                message="Webflow field missing slug",
                details={"field": field},
            )
        return str(slug)

    def _normalize_key(self, value: Any) -> str:
        ascii_text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())

    def _find_field(self, fields: Sequence[Dict[str, Any]], alias_key: str) -> Optional[Dict[str, Any]]:
        candidates = _FIELD_CANDIDATES.get(alias_key, ())
        normalized_candidates = {self._normalize_key(candidate) for candidate in candidates}
        for field in fields:
            probe_values = {
                self._normalize_key(field.get("slug")),
                self._normalize_key(field.get("displayName")),
                self._normalize_key(field.get("name")),
            }
            if probe_values & normalized_candidates:
                return field
        return None

    def _require_field(self, fields: Sequence[Dict[str, Any]], alias_key: str) -> Dict[str, Any]:
        field = self._find_field(fields, alias_key)
        if field is None:
            raise ValidationError(
                message=f"Webflow collection is missing the expected field for {alias_key}",
                details={
                    "collection_id": self.collection_id,
                    "alias_key": alias_key,
                    "candidates": list(_FIELD_CANDIDATES.get(alias_key, ())),
                },
            )
        return field

    def _resolve_option_value(self, field: Dict[str, Any], label: Optional[str]) -> Optional[str]:
        options = self._extract_options(field)
        if not options:
            return None

        normalized_label = self._normalize_key(label)
        if not normalized_label:
            if len(options) == 1:
                return str(options[0].get("id") or options[0].get("value") or "")
            return None

        for option in options:
            option_label = option.get("name") or option.get("displayName") or option.get("label") or option.get("value")
            if self._normalize_key(option_label) == normalized_label:
                return str(option.get("id") or option.get("value") or "")

        raise ValidationError(
            message="Author value does not match any Webflow option",
            details={
                "field": self._field_slug(field),
                "requested": label,
                "available": [option.get("name") or option.get("displayName") or option.get("label") for option in options],
            },
        )

    def _extract_options(self, field: Dict[str, Any]) -> List[Dict[str, Any]]:
        stack: List[Any] = [field]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                options = node.get("options")
                if isinstance(options, list) and options and isinstance(options[0], dict):
                    return options
                stack.extend(node.values())
            elif isinstance(node, list):
                if node and all(isinstance(item, dict) for item in node):
                    if any(("id" in item or "value" in item) and ("name" in item or "displayName" in item or "label" in item) for item in node):
                        return list(node)
                stack.extend(node)
        return []

    def _extract_item_id(self, payload: Dict[str, Any]) -> str:
        item_id = payload.get("id") or payload.get("itemId")
        if item_id:
            return str(item_id)
        items = payload.get("items")
        if isinstance(items, list) and items:
            first_item = items[0]
            if isinstance(first_item, dict) and first_item.get("id"):
                return str(first_item["id"])
        raise ThirdPartyError(
            message="Webflow item response did not contain an item id",
            details={"collection_id": self.collection_id, "response": payload},
        )
