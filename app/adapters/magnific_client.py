from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.errors import ThirdPartyError
from app.core.logging import get_logger

logger = get_logger(__name__)

INCOMPATIBLE_MYSTIC_MODELS = {"fluid", "flexible", "super_real", "editorial_portraits"}


class MagnificCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class MagnificTrainingStatus:
    raw_status: str
    phase: str
    progress_percent: int
    provider_lora_id: Optional[str]
    provider_lora_name: Optional[str]
    default_scale: Optional[float]


def normalize_lora_training_status(row: dict[str, Any]) -> MagnificTrainingStatus:
    training = row.get("training") if isinstance(row.get("training"), dict) else {}
    raw = str(training.get("status") or row.get("status") or "unknown").lower()
    phase_map = {
        "created": ("queued", 10),
        "queued": ("queued", 10),
        "in_progress": ("training", 50),
        "processing": ("training", 50),
        "training": ("training", 50),
        "completed": ("ready", 100),
        "succeeded": ("ready", 100),
        "ready": ("ready", 100),
        "failed": ("failed", 0),
        "error": ("failed", 0),
    }
    phase, percent = phase_map.get(raw, ("training", 35))
    return MagnificTrainingStatus(
        raw_status=raw,
        phase=phase,
        progress_percent=percent,
        provider_lora_id=str(row.get("id")) if row.get("id") is not None else None,
        provider_lora_name=str(row.get("name")) if row.get("name") else None,
        default_scale=training.get("defaultScale"),
    )


def build_mystic_character_payload(
    *,
    prompt: str,
    lora_id: str,
    strength: int,
    aspect_ratio: str = "social_story_9_16",
    resolution: str = "2k",
    webhook_url: Optional[str] = None,
    extra_options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    options = dict(extra_options or {})
    if options.get("structure_reference") or options.get("style_reference"):
        raise MagnificCompatibilityError("Mystic LoRA payload cannot include structure_reference or style_reference")
    model = str(options.get("model") or "").strip()
    if model in INCOMPATIBLE_MYSTIC_MODELS:
        raise MagnificCompatibilityError(f"Mystic model {model} silently ignores LoRAs")
    payload: dict[str, Any] = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "styling": {"characters": [{"id": str(lora_id), "strength": int(strength)}]},
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url
    payload.update(options)
    return payload


class MagnificClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        settings = get_settings() if api_key is None else None
        self.api_key = api_key if api_key is not None else settings.magnific_api_key
        self.base_url = (base_url or (settings.magnific_base_url if settings else "https://api.magnific.com")).rstrip("/")
        timeout = timeout_seconds if timeout_seconds is not None else (settings.magnific_timeout_seconds if settings else 60)
        self.http_client = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=15.0, read=float(timeout), write=30.0, pool=None),
            follow_redirects=True,
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ThirdPartyError(message="Magnific API key not configured", details={"provider": "magnific"})
        return {
            "x-magnific-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, correlation_id: str, json_payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            response = self.http_client.request(method, path, headers=self._headers(), json=json_payload)
            if response.status_code >= 400:
                raise ThirdPartyError(
                    message="Magnific API request failed",
                    details={
                        "provider": "magnific",
                        "path": path,
                        "status_code": response.status_code,
                        "body": response.text[:1000],
                        "correlation_id": correlation_id,
                    },
                )
            data = response.json()
            if not isinstance(data, dict):
                raise ThirdPartyError(
                    message="Magnific API returned a non-object response",
                    details={"provider": "magnific", "path": path, "correlation_id": correlation_id},
                )
            return data
        except ThirdPartyError:
            raise
        except httpx.HTTPError as exc:
            raise ThirdPartyError(
                message="Magnific API transport failed",
                details={"provider": "magnific", "path": path, "error": str(exc), "correlation_id": correlation_id},
            ) from exc

    def build_character_training_payload(
        self,
        *,
        name: str,
        quality: str,
        gender: str,
        images: list[str],
        description: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "quality": quality,
            "gender": gender,
            "images": images,
        }
        if description:
            payload["description"] = description
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    def submit_character_training(
        self,
        *,
        name: str,
        quality: str,
        gender: str,
        images: list[str],
        description: Optional[str],
        webhook_url: Optional[str],
        correlation_id: str,
    ) -> dict[str, Any]:
        payload = self.build_character_training_payload(
            name=name,
            quality=quality,
            gender=gender,
            images=images,
            description=description,
            webhook_url=webhook_url,
        )
        data = self._request("POST", "/v1/ai/loras/characters", correlation_id=correlation_id, json_payload=payload)
        task_id = data.get("task_id") or data.get("id") or data.get("lora_id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        logger.info("magnific_character_training_submitted", correlation_id=correlation_id, task_id=data.get("task_id"))
        return data

    def list_loras(self, *, correlation_id: str) -> dict[str, Any]:
        return self._request("GET", "/v1/ai/loras", correlation_id=correlation_id)

    def create_mystic_scene_reference(
        self,
        *,
        prompt: str,
        lora_id: str,
        strength: int,
        correlation_id: str,
        aspect_ratio: str = "social_story_9_16",
        resolution: str = "2k",
        webhook_url: Optional[str] = None,
        extra_options: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        payload = build_mystic_character_payload(
            prompt=prompt,
            lora_id=lora_id,
            strength=strength,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            webhook_url=webhook_url,
            extra_options=extra_options,
        )
        data = self._request("POST", "/v1/ai/mystic", correlation_id=correlation_id, json_payload=payload)
        task_id = data.get("task_id") or data.get("id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        return data

    def get_mystic_task(self, *, task_id: str, correlation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/ai/mystic/{task_id}", correlation_id=correlation_id)


def get_magnific_client() -> MagnificClient:
    return MagnificClient()
