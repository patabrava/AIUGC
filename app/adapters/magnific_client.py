from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.errors import ThirdPartyError
from app.core.logging import get_logger

logger = get_logger(__name__)

LORA_UNSAFE_MYSTIC_OPTION_KEYS = {"structure_reference", "style_reference", "model"}
PROTECTED_MYSTIC_PAYLOAD_KEYS = {"prompt", "resolution", "aspect_ratio", "styling", "webhook_url", "fixed_generation"}


class MagnificCompatibilityError(ValueError):
    pass


def _unwrap_data_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    if not isinstance(nested, dict):
        return dict(data)
    normalized = dict(nested)
    for key, value in data.items():
        if key != "data" and key not in normalized:
            normalized[key] = value
    return normalized


def list_lora_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    lora = response.get("lora")
    if isinstance(lora, dict):
        normalized = dict(lora)
        if response.get("task_id") and not normalized.get("task_id"):
            normalized["task_id"] = response.get("task_id")
        return [normalized]

    data = response.get("data")
    if isinstance(data, list):
        rows: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            rows.extend(list_lora_rows(row) if isinstance(row.get("lora"), dict) else [row])
        return rows
    if not isinstance(data, dict):
        return []

    rows: list[dict[str, Any]] = []
    for value in data.values():
        if isinstance(value, list):
            for row in value:
                if not isinstance(row, dict):
                    continue
                rows.extend(list_lora_rows(row) if isinstance(row.get("lora"), dict) else [row])
    return rows


@dataclass
class MagnificTrainingStatus:
    raw_status: Optional[str] = None
    phase: Optional[str] = None
    progress_percent: Optional[int] = None
    provider_lora_id: Optional[str] = None
    provider_lora_name: Optional[str] = None
    default_scale: Optional[float] = None
    provider_training_task_id: Optional[str] = None
    training_status: Optional[str] = None
    training_phase: Optional[str] = None
    training_progress_percent: Optional[int] = None
    training_error: Optional[str] = None

    def __post_init__(self) -> None:
        status = self.raw_status or self.training_status or "unknown"
        phase = self.phase or self.training_phase or status
        progress = self.progress_percent
        if progress is None:
            progress = self.training_progress_percent if self.training_progress_percent is not None else 0
        self.raw_status = status
        self.training_status = self.training_status or status
        self.phase = phase
        self.training_phase = self.training_phase or phase
        self.progress_percent = int(progress)
        self.training_progress_percent = self.training_progress_percent if self.training_progress_percent is not None else int(progress)


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
        provider_training_task_id=str(row.get("task_id") or row.get("training_task_id") or "") or None,
        provider_lora_id=str(row.get("id")) if row.get("id") is not None else None,
        provider_lora_name=str(row.get("name")) if row.get("name") else None,
        default_scale=training.get("defaultScale"),
    )


def _normalize_mystic_style_loras(style_loras: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in style_loras or []:
        if not isinstance(row, dict):
            raise MagnificCompatibilityError("Mystic style LoRA rows must be objects")
        name = str(row.get("name") or "").strip()
        if not name:
            raise MagnificCompatibilityError("Mystic style LoRA row requires a name")
        strength = row.get("strength", 100)
        if isinstance(strength, bool):
            raise MagnificCompatibilityError("Mystic style LoRA strength must be an integer")
        if isinstance(strength, int):
            strength_int = strength
        elif isinstance(strength, str) and strength.strip().lstrip("-").isdigit():
            strength_int = int(strength.strip())
        else:
            raise MagnificCompatibilityError("Mystic style LoRA strength must be an integer")
        if strength_int < 0 or strength_int > 200:
            raise MagnificCompatibilityError("Mystic style LoRA strength must be between 0 and 200")
        normalized.append({"name": name, "strength": strength_int})
    return normalized


def build_mystic_character_payload(
    *,
    prompt: str,
    lora_id: str,
    strength: int,
    aspect_ratio: str = "social_story_9_16",
    resolution: str = "2k",
    fixed_generation: Optional[bool] = None,
    webhook_url: Optional[str] = None,
    extra_options: Optional[dict[str, Any]] = None,
    style_loras: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    options = dict(extra_options or {})
    unsafe_keys = sorted(set(options) & LORA_UNSAFE_MYSTIC_OPTION_KEYS)
    if unsafe_keys:
        raise MagnificCompatibilityError(f"Mystic LoRA payload cannot include {', '.join(unsafe_keys)}")
    protected_keys = sorted(set(options) & PROTECTED_MYSTIC_PAYLOAD_KEYS)
    if protected_keys:
        raise MagnificCompatibilityError(f"Mystic LoRA payload cannot override {', '.join(protected_keys)}")
    styling: dict[str, Any] = {"characters": [{"id": str(lora_id), "strength": int(strength)}]}
    normalized_styles = _normalize_mystic_style_loras(style_loras)
    if normalized_styles:
        styling["styles"] = normalized_styles
    payload: dict[str, Any] = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "styling": styling,
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url
    if fixed_generation is not None:
        payload["fixed_generation"] = fixed_generation
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
        data = _unwrap_data_payload(
            self._request("POST", "/v1/ai/loras/characters", correlation_id=correlation_id, json_payload=payload)
        )
        task_id = data.get("task_id") or data.get("id") or data.get("lora_id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        logger.info("magnific_character_training_submitted", correlation_id=correlation_id, task_id=data.get("task_id"))
        return data

    def build_style_training_payload(
        self,
        *,
        name: str,
        quality: str,
        images: list[str],
        description: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        cleaned_images = [str(url).strip() for url in images if str(url or "").strip()]
        if len(cleaned_images) < 6 or len(cleaned_images) > 20:
            raise MagnificCompatibilityError("Magnific style LoRA training requires 6 to 20 image URLs")
        payload: dict[str, Any] = {
            "name": name.strip(),
            "quality": quality,
            "images": cleaned_images,
        }
        if description:
            payload["description"] = description
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    def submit_style_training(
        self,
        *,
        name: str,
        quality: str,
        images: list[str],
        description: Optional[str],
        webhook_url: Optional[str],
        correlation_id: str,
    ) -> dict[str, Any]:
        payload = self.build_style_training_payload(
            name=name,
            quality=quality,
            images=images,
            description=description,
            webhook_url=webhook_url,
        )
        data = _unwrap_data_payload(
            self._request("POST", "/v1/ai/loras/styles", correlation_id=correlation_id, json_payload=payload)
        )
        task_id = data.get("task_id") or data.get("id") or data.get("lora_id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        logger.info("magnific_style_training_submitted", correlation_id=correlation_id, task_id=data.get("task_id"))
        return data

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
        data = self.submit_character_training(
            name=name,
            quality=quality,
            gender=gender,
            images=[url.strip() for url in image_urls if str(url or "").strip()],
            description=description,
            webhook_url=webhook_url,
            correlation_id=correlation_id,
        )
        raw_status = str(data.get("status") or "queued").lower()
        phase, percent = {
            "created": ("queued", 10),
            "queued": ("queued", 10),
            "pending": ("queued", 10),
            "in_progress": ("training", 50),
            "processing": ("training", 50),
            "training": ("training", 50),
            "completed": ("ready", 100),
            "succeeded": ("ready", 100),
            "ready": ("ready", 100),
            "failed": ("failed", 0),
            "error": ("failed", 0),
        }.get(raw_status, ("training", 35))
        return MagnificTrainingStatus(
            raw_status=raw_status,
            phase=phase,
            progress_percent=percent,
            provider_training_task_id=str(data.get("task_id") or data.get("training_task_id") or "") or None,
            provider_lora_id=str(data.get("lora_id") or data.get("id") or "") or None,
            provider_lora_name=str(data.get("name") or "") or None,
            training_error=str(data.get("error") or "") or None,
        )

    def list_loras(self, *, correlation_id: str) -> dict[str, Any]:
        return self._request("GET", "/v1/ai/loras", correlation_id=correlation_id)

    def poll_character_lora_status(
        self,
        *,
        provider_training_task_id: Optional[str] = None,
        provider_lora_id: Optional[str] = None,
        correlation_id: str,
    ) -> Optional[MagnificTrainingStatus]:
        for row in list_lora_rows(self.list_loras(correlation_id=correlation_id)):
            row_task_id = str(row.get("task_id") or row.get("training_task_id") or "")
            row_lora_id = str(row.get("id") or row.get("lora_id") or "")
            if provider_training_task_id and row_task_id == provider_training_task_id:
                return normalize_lora_training_status(row)
            if provider_lora_id and row_lora_id == provider_lora_id:
                return normalize_lora_training_status(row)
        return None

    def create_mystic_scene_reference(
        self,
        *,
        prompt: str,
        lora_id: str,
        strength: int,
        correlation_id: str,
        aspect_ratio: str = "social_story_9_16",
        resolution: str = "2k",
        fixed_generation: Optional[bool] = None,
        webhook_url: Optional[str] = None,
        extra_options: Optional[dict[str, Any]] = None,
        style_loras: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        payload = build_mystic_character_payload(
            prompt=prompt,
            lora_id=lora_id,
            strength=strength,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            fixed_generation=fixed_generation,
            webhook_url=webhook_url,
            extra_options=extra_options,
            style_loras=style_loras,
        )
        data = _unwrap_data_payload(
            self._request("POST", "/v1/ai/mystic", correlation_id=correlation_id, json_payload=payload)
        )
        task_id = data.get("task_id") or data.get("id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        data["_request_payload"] = payload
        return data

    def create_image_to_prompt_task(
        self,
        *,
        image: str,
        correlation_id: str,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"image": image}
        if webhook_url:
            payload["webhook_url"] = webhook_url
        data = _unwrap_data_payload(
            self._request("POST", "/v1/ai/image-to-prompt", correlation_id=correlation_id, json_payload=payload)
        )
        task_id = data.get("task_id") or data.get("id")
        if task_id is not None:
            data["task_id"] = str(task_id)
        return data

    def get_image_to_prompt_task(self, *, task_id: str, correlation_id: str) -> dict[str, Any]:
        return _unwrap_data_payload(
            self._request("GET", f"/v1/ai/image-to-prompt/{task_id}", correlation_id=correlation_id)
        )

    def get_mystic_task(self, *, task_id: str, correlation_id: str) -> dict[str, Any]:
        return _unwrap_data_payload(
            self._request("GET", f"/v1/ai/mystic/{task_id}", correlation_id=correlation_id)
        )


def get_magnific_client() -> MagnificClient:
    return MagnificClient()
