# app/features/blog/webflow_client.py
"""
FLOW-FORGE Webflow Client Adapter
Thin wrapper for Webflow CMS API v2.
Per Constitution § VI: Adapterize Specialists
"""

from typing import Any, Dict

import httpx

from app.core.logging import get_logger
from app.core.errors import ThirdPartyError

logger = get_logger(__name__)

WEBFLOW_API_BASE = "https://api.webflow.com/v2"


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

    def create_item(self, field_data: Dict[str, Any]) -> str:
        """Create a new CMS collection item. Returns the Webflow item ID."""
        payload = {"fieldData": field_data}
        logger.info("webflow_create_item", collection_id=self.collection_id, slug=field_data.get("slug"))

        response = self.http_client.post(
            f"/collections/{self.collection_id}/items",
            json=payload,
        )

        if response.status_code >= 400:
            logger.error("webflow_create_item_error", status=response.status_code, body=response.text)
            raise ThirdPartyError(
                message=f"Webflow create item failed: {response.status_code}",
                details={"status": response.status_code, "response": response.text[:500]},
            )

        data = response.json()
        item_id = data.get("id", "")
        logger.info("webflow_item_created", item_id=item_id)
        return item_id

    def update_item(self, item_id: str, field_data: Dict[str, Any]) -> str:
        """Update an existing CMS collection item. Returns the item ID."""
        payload = {"fieldData": field_data}
        logger.info("webflow_update_item", item_id=item_id)

        response = self.http_client.patch(
            f"/collections/{self.collection_id}/items/{item_id}",
            json=payload,
        )

        if response.status_code >= 400:
            logger.error("webflow_update_item_error", status=response.status_code, body=response.text)
            raise ThirdPartyError(
                message=f"Webflow update item failed: {response.status_code}",
                details={"status": response.status_code, "response": response.text[:500]},
            )

        return item_id

    def publish_site(self) -> bool:
        """Trigger a site publish so staged CMS items go live."""
        logger.info("webflow_publish_site", site_id=self.site_id)

        response = self.http_client.post(
            f"/sites/{self.site_id}/publish",
            json={"publishToWebflowSubdomain": True},
        )

        if response.status_code >= 400:
            logger.error("webflow_publish_error", status=response.status_code, body=response.text)
            raise ThirdPartyError(
                message=f"Webflow publish failed: {response.status_code}",
                details={"status": response.status_code, "response": response.text[:500]},
            )

        logger.info("webflow_site_published")
        return True
