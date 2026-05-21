"""
Magnific API adapter for actor LoRA training and status polling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

_TRAINING_ENDPOINT = "/ai/loras/characters"
_LORA_LIST_ENDPOINT = "/ai/loras"

_STATUS_TO_PROGRESS = {
    "queued": 10,
    "pending": 10,
    "training": 55,
    "processing": 55,
    "ready": 100,
    "completed": 100,
    "failed": 0,
    "error": 0,
}


@dataclass(frozen=True)
class MagnificTrainingStatus:
    provider_training_task_id: Optional[str]
    provider_lora_id: Optional[str]
    provider_lora_name: Optional[str]
    training_status: str
    training_phase: str
    training_progress_percent: int
    training_error: Optional[str] = None


class MagnificClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._http = httpx.Client(timeout=60.0, follow_redirects=True)

    def _ensure_configured(self) -> None:
        if not str(self._settings.magnific_api_key or "").strip():
            raise ValidationError(
                "MAGNIFIC_API_KEY is required to train Actor Identity LoRAs.",
                {"provider": "magnific"},
            )

    def _headers(self) -> dict[str, str]:
        return {
            "x-magnific-api-key": self._settings.magnific_api_key.strip(),
            "accept": "application/json",
            "content-type": "application/json",
        }

    def train_character_lora(
        self,
        *,
        name: str,
        quality: str,
        gender: str,
        image_urls: list[str],
        correlation_id: str,
        description: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> MagnificTrainingStatus:
        self._ensure_configured()
        payload: dict[str, Any] = {
            "name": name.strip(),
            "quality": quality.strip(),
            "gender": gender.strip(),
            "images": [url.strip() for url in image_urls if str(url or "").strip()],
        }
        if description and description.strip():
            payload["description"] = description.strip()
        if webhook_url and webhook_url.strip():
            payload["webhook_url"] = webhook_url.strip()

        response = self._http.post(
            f"{self._settings.magnific_base_url.rstrip('/')}{_TRAINING_ENDPOINT}",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        status = _normalize_training_status(data)
        logger.info(
            "magnific_character_lora_training_submitted",
            correlation_id=correlation_id,
            provider_training_task_id=status.provider_training_task_id,
            provider_lora_id=status.provider_lora_id,
            training_status=status.training_status,
        )
        return status

    def poll_character_lora_status(
        self,
        *,
        provider_training_task_id: Optional[str] = None,
        provider_lora_id: Optional[str] = None,
        correlation_id: str,
    ) -> Optional[MagnificTrainingStatus]:
        self._ensure_configured()
        response = self._http.get(
            f"{self._settings.magnific_base_url.rstrip('/')}{_LORA_LIST_ENDPOINT}",
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        items = _extract_lora_items(data)
        match = None
        for item in items:
            if provider_training_task_id and str(item.get("training_task_id") or item.get("provider_training_task_id") or "").strip() == provider_training_task_id:
                match = item
                break
            if provider_lora_id and str(item.get("id") or item.get("lora_id") or "").strip() == provider_lora_id:
                match = item
                break
        if match is None:
            return None

        status = _normalize_training_status(match)
        logger.info(
            "magnific_character_lora_status_polled",
            correlation_id=correlation_id,
            provider_training_task_id=status.provider_training_task_id,
            provider_lora_id=status.provider_lora_id,
            training_status=status.training_status,
            training_progress_percent=status.training_progress_percent,
        )
        return status


def _extract_lora_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "loras", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_training_status(payload: Any) -> MagnificTrainingStatus:
    if not isinstance(payload, dict):
        return MagnificTrainingStatus(
            provider_training_task_id=None,
            provider_lora_id=None,
            provider_lora_name=None,
            training_status="queued",
            training_phase="queued",
            training_progress_percent=10,
        )

    training = payload.get("training") if isinstance(payload.get("training"), dict) else {}
    provider_training_task_id = str(
        payload.get("training_task_id")
        or payload.get("provider_training_task_id")
        or training.get("task_id")
        or training.get("id")
        or payload.get("task_id")
        or ""
    ).strip() or None
    provider_lora_id = str(payload.get("id") or payload.get("lora_id") or training.get("lora_id") or "").strip() or None
    provider_lora_name = str(payload.get("name") or payload.get("lora_name") or "").strip() or None
    raw_status = str(
        training.get("status")
        or payload.get("training_status")
        or payload.get("status")
        or "queued"
    ).strip().lower()
    training_status = raw_status or "queued"
    training_phase = str(payload.get("training_phase") or training_status or "queued").strip() or "queued"
    progress = payload.get("training_progress_percent")
    if progress is None:
        progress = payload.get("progress_percent")
    if progress is None:
        progress = _STATUS_TO_PROGRESS.get(training_status, 0)
    try:
        training_progress_percent = max(0, min(100, int(progress)))
    except (TypeError, ValueError):
        training_progress_percent = _STATUS_TO_PROGRESS.get(training_status, 0)
    training_error = payload.get("training_error") or payload.get("error")
    if isinstance(training_error, dict):
        training_error = training_error.get("message") or training_error.get("error")
    training_error = str(training_error).strip() if training_error else None

    return MagnificTrainingStatus(
        provider_training_task_id=provider_training_task_id,
        provider_lora_id=provider_lora_id,
        provider_lora_name=provider_lora_name,
        training_status=training_status,
        training_phase=training_phase,
        training_progress_percent=training_progress_percent,
        training_error=training_error,
    )


def get_magnific_client() -> MagnificClient:
    return MagnificClient()
