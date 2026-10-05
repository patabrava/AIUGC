"""
Runway Developer API adapter for the experimental Seedance 2.5 evaluation provider.

Server-side only. The request/response contract follows Runway's official OpenAPI
spec (github.com/runwayml/openapi): ``POST /v1/image_to_video`` or the single
unpositioned-reference ``POST /v1/text_to_video`` contract, with
``model="seedance2_5"``, ``GET``/``DELETE /v1/tasks/{id}``, ``GET /v1/organization``,
bearer auth, and ``X-Runway-Version: 2024-11-06``. Runway exposes no idempotency
key, so callers must fence each paid submission durably before calling
``submit_image_to_video`` and must never retry an ambiguous submission.

Errors carry a ``kind`` that tells the caller whether a task could exist:
``not_submitted``/``rejected``/``rate_limited`` prove no task was created;
``ambiguous`` means a paid task may exist. Logs never contain the API key,
prompt text, image payloads, or signed output URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import ipaddress
import re
import time
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.adapters.video_provider_transport import (
    VideoProviderError as RunwayError, VideoTask as RunwayTask,
    VideoOutput as RunwayOutput, SafeVideoOutputDownloader,
    parse_allowed_hosts, host_is_allowed, safe_url_label, validate_output_url,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

RUNWAY_API_VERSION = "2024-11-06"
RUNWAY_DEFAULT_BASE_URL = "https://api.dev.runwayml.com"
SEEDANCE_2_5_MODEL = "seedance2_5"
# Portrait 9:16 ratios accepted by seedance2_5 (480p/720p/1080p variants).
SEEDANCE_2_5_PORTRAIT_RATIOS = {"480p": "480:854", "720p": "720:1280", "1080p": "1080:1920"}
SEEDANCE_2_5_MIN_SECONDS = 4
SEEDANCE_2_5_MAX_SECONDS = 30
PROMPT_TEXT_MAX_CHARS = 15_000
DATA_URI_MAX_CHARS = 5_242_880
HTTPS_PROMPT_IMAGE_MAX_CHARS = 2_048
SEED_MAX = 4_294_967_295
TASK_STATUSES_ACTIVE = frozenset({"PENDING", "THROTTLED", "RUNNING"})
TASK_STATUSES_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_REJECTED_SUBMIT_STATUSES = frozenset({400, 401, 403, 404, 405, 413, 415, 422})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_OUTPUT_REDIRECTS = 3
_MAX_RETRY_AFTER_SECONDS = 300.0


def build_image_data_uri(image_bytes: bytes, mime_type: str) -> str:
    import base64

    normalized = str(mime_type or "").strip().lower()
    if normalized not in {"image/png", "image/jpeg"}:
        raise RunwayError("Runway prompt image must be PNG or JPEG.", kind="rejected", details={"mime_type": normalized})
    if not image_bytes:
        raise RunwayError("Runway prompt image is empty.", kind="rejected")
    return f"data:{normalized};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _parse_retry_after(headers: httpx.Headers) -> Optional[float]:
    raw_ms = headers.get("retry-after-ms")
    if raw_ms:
        try:
            return min(max(float(raw_ms) / 1000.0, 1.0), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (moment - datetime.now(timezone.utc)).total_seconds()
    return min(max(seconds, 1.0), _MAX_RETRY_AFTER_SECONDS)


def _sanitized_error_body(response: httpx.Response) -> Dict[str, Any]:
    """Keep Runway's error string and issue codes/paths; drop echoed values."""
    try:
        payload = response.json()
    except ValueError:
        return {"error": None}
    if not isinstance(payload, dict):
        return {"error": None}
    issues = []
    for issue in payload.get("issues") or []:
        if isinstance(issue, dict):
            issues.append(
                {
                    "code": str(issue.get("code") or "")[:80],
                    "path": [str(part)[:40] for part in (issue.get("path") or [])][:6],
                }
            )
    return {"error": str(payload.get("error") or "")[:300] or None, "issues": issues[:10]}


def _optional_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _credits(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        return _optional_number(value.get("credits"))
    return None


class RunwayClient(SafeVideoOutputDownloader):
    """Stateless REST client. Inject ``http_client`` in tests."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = RUNWAY_DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        output_allowed_hosts: Iterable[str] = (),
        output_max_bytes: int = 150 * 1024 * 1024,
        download_timeout_seconds: float = 180.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise RunwayError("Runway API key is not configured.", kind="configuration")
        parsed_base = urlparse(str(base_url or ""))
        if parsed_base.scheme != "https" or not parsed_base.hostname:
            raise RunwayError("Runway API base URL must use HTTPS.", kind="configuration")
        self._api_key = str(api_key).strip()
        self._base_url = str(base_url).rstrip("/")
        self._timeout = httpx.Timeout(connect=10.0, read=float(timeout_seconds), write=float(timeout_seconds), pool=10.0)
        self._output_allowed_hosts = parse_allowed_hosts(tuple(output_allowed_hosts))
        self._output_max_bytes = int(output_max_bytes)
        self._download_timeout_seconds = float(download_timeout_seconds)
        self._http_client = http_client

    def __repr__(self) -> str:  # never expose the key through debugging output
        return f"RunwayClient(base_url={self._base_url!r})"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Runway-Version": RUNWAY_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> httpx.Response:
        url = f"{self._base_url}{path}"
        if self._http_client is not None:
            return self._http_client.request(method, url, headers=self._headers(), json=json_body, timeout=self._timeout)
        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
            return client.request(method, url, headers=self._headers(), json=json_body)

    def submit_image_to_video(
        self, *, prompt_image: str, prompt_text: str, ratio: str,
        duration_seconds: int, audio: bool, seed: Optional[int], correlation_id: str,
    ) -> Dict[str, Any]:
        """Submit the existing single-frame evaluation contract."""
        return self._submit_video(
            prompt_image=prompt_image, prompt_text=prompt_text, ratio=ratio,
            duration_seconds=duration_seconds, audio=audio, seed=seed,
            correlation_id=correlation_id, reference_only=False,
        )

    def submit_reference_to_video(
        self, *, reference_image: str, prompt_text: str, ratio: str,
        duration_seconds: int, audio: bool, seed: Optional[int], correlation_id: str,
    ) -> Dict[str, Any]:
        """Submit exactly one unpositioned Seedance identity reference, with no keyframes."""
        return self._submit_video(
            prompt_image=reference_image, prompt_text=prompt_text, ratio=ratio,
            duration_seconds=duration_seconds, audio=audio, seed=seed,
            correlation_id=correlation_id, reference_only=True,
        )

    def _submit_video(
        self,
        *,
        prompt_image: str,
        prompt_text: str,
        ratio: str,
        duration_seconds: int,
        audio: bool,
        seed: Optional[int],
        correlation_id: str,
        reference_only: bool = False,
    ) -> Dict[str, Any]:
        """Create one paid seedance2_5 task. Returns ``task_id`` and ``estimated_credits``."""
        if ratio not in SEEDANCE_2_5_PORTRAIT_RATIOS.values():
            raise RunwayError("Unsupported Seedance 2.5 ratio.", kind="rejected", details={"ratio": ratio})
        if isinstance(duration_seconds, bool) or not SEEDANCE_2_5_MIN_SECONDS <= int(duration_seconds) <= SEEDANCE_2_5_MAX_SECONDS:
            raise RunwayError("Unsupported Seedance 2.5 duration.", kind="rejected", details={"duration": duration_seconds})
        text = str(prompt_text or "")
        if not text.strip() or len(text) > PROMPT_TEXT_MAX_CHARS:
            raise RunwayError("Runway promptText must contain 1-15000 characters.", kind="rejected", details={"chars": len(text)})
        if prompt_image.startswith("data:image/"):
            if len(prompt_image) > DATA_URI_MAX_CHARS:
                raise RunwayError("Runway promptImage data URI exceeds the provider limit.", kind="rejected")
        elif prompt_image.startswith("https://"):
            if len(prompt_image) > HTTPS_PROMPT_IMAGE_MAX_CHARS:
                raise RunwayError("Runway promptImage URL exceeds the provider limit.", kind="rejected")
        else:
            raise RunwayError("Runway promptImage must be an HTTPS URL or image data URI.", kind="rejected")
        body: Dict[str, Any] = {
            "model": SEEDANCE_2_5_MODEL,
            "promptImage": prompt_image,
            "promptText": text,
            "ratio": ratio,
            "duration": int(duration_seconds),
            "audio": bool(audio),
        }
        endpoint = "/v1/image_to_video"
        if reference_only:
            body.pop("promptImage")
            body["references"] = [{"uri": prompt_image}]
            endpoint = "/v1/text_to_video"
        if seed is not None:
            if isinstance(seed, bool) or not 0 <= int(seed) <= SEED_MAX:
                raise RunwayError("Runway seed is out of range.", kind="rejected")
            body["seed"] = int(seed)

        logger.info(
            "runway_submit_starting",
            correlation_id=correlation_id,
            model=SEEDANCE_2_5_MODEL,
            ratio=ratio,
            duration_seconds=int(duration_seconds),
            audio=bool(audio),
            prompt_chars=len(text),
            prompt_image_transport="data_uri" if prompt_image.startswith("data:") else "https_url",
        )
        try:
            response = self._request("POST", endpoint, json_body=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            logger.warning("runway_submit_not_sent", correlation_id=correlation_id, error_type=type(exc).__name__)
            raise RunwayError("Runway submission was not sent.", kind="not_submitted") from exc
        except httpx.HTTPError as exc:
            logger.error("runway_submit_ambiguous_transport", correlation_id=correlation_id, error_type=type(exc).__name__)
            raise RunwayError(
                "Runway submission outcome is unknown after a transport failure.",
                kind="ambiguous",
                details={"error_type": type(exc).__name__},
            ) from exc

        status_code = response.status_code
        if status_code == 429:
            retry_after = _parse_retry_after(response.headers)
            logger.warning("runway_submit_rate_limited", correlation_id=correlation_id, retry_after_seconds=retry_after)
            raise RunwayError(
                "Runway rejected the submission with a rate limit.",
                kind="rate_limited",
                status_code=429,
                retry_after_seconds=retry_after,
                details=_sanitized_error_body(response),
            )
        if status_code in _REJECTED_SUBMIT_STATUSES:
            details = _sanitized_error_body(response)
            logger.warning("runway_submit_rejected", correlation_id=correlation_id, status_code=status_code, issues=details.get("issues"))
            raise RunwayError(
                details.get("error") or f"Runway rejected the submission with HTTP {status_code}.",
                kind="rejected",
                status_code=status_code,
                details=details,
            )
        if status_code < 200 or status_code >= 300:
            logger.error("runway_submit_ambiguous_status", correlation_id=correlation_id, status_code=status_code)
            raise RunwayError(
                f"Runway submission outcome is unknown after HTTP {status_code}.",
                kind="ambiguous",
                status_code=status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunwayError("Runway accepted the request but returned an unreadable body.", kind="ambiguous", status_code=status_code) from exc
        task_id = str((payload or {}).get("id") or "").strip() if isinstance(payload, dict) else ""
        if not _TASK_ID_PATTERN.fullmatch(task_id):
            raise RunwayError("Runway accepted the request without a valid task id.", kind="ambiguous", status_code=status_code)
        estimated = _credits(payload.get("estimatedCost"))
        logger.info("runway_submit_accepted", correlation_id=correlation_id, task_id=task_id, estimated_credits=estimated)
        return {"task_id": task_id, "estimated_credits": estimated}

    def get_task(self, task_id: str, *, correlation_id: str) -> RunwayTask:
        if not _TASK_ID_PATTERN.fullmatch(str(task_id or "")):
            raise RunwayError("Runway task id is invalid.", kind="invalid_response")
        try:
            response = self._request("GET", f"/v1/tasks/{task_id}")
        except httpx.HTTPError as exc:
            raise RunwayError("Runway task poll failed in transport.", kind="transport", details={"error_type": type(exc).__name__}) from exc
        status_code = response.status_code
        if status_code == 404:
            raise RunwayError("Runway task does not exist, was deleted, or was cancelled.", kind="not_found", status_code=404)
        if status_code == 429:
            raise RunwayError(
                "Runway task poll was rate limited.",
                kind="rate_limited",
                status_code=429,
                retry_after_seconds=_parse_retry_after(response.headers),
            )
        if status_code >= 400:
            raise RunwayError(f"Runway task poll failed with HTTP {status_code}.", kind="transport", status_code=status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunwayError("Runway task poll returned an unreadable body.", kind="invalid_response") from exc
        if not isinstance(payload, dict) or str(payload.get("id") or "") != task_id:
            raise RunwayError("Runway task poll returned a mismatched task.", kind="invalid_response")
        status = str(payload.get("status") or "").strip().upper()
        outputs = payload.get("output") if isinstance(payload.get("output"), list) else []
        cost = _credits(payload.get("cost"))
        return RunwayTask(
            task_id=task_id,
            status=status,
            progress=_optional_number(payload.get("progress")),
            output_urls=tuple(str(item) for item in outputs if isinstance(item, str) and item.strip()),
            failure=str(payload.get("failure") or "")[:500] or None,
            failure_code=str(payload.get("failureCode") or "")[:120] or None,
            estimated_credits=_credits(payload.get("estimatedCost")),
            cost_credits=int(cost) if cost is not None else None,
        )

    def cancel_task(self, task_id: str, *, correlation_id: str) -> bool:
        """Cancel a pending/running task. A 404 is treated as already gone."""
        if not _TASK_ID_PATTERN.fullmatch(str(task_id or "")):
            return False
        try:
            response = self._request("DELETE", f"/v1/tasks/{task_id}")
        except httpx.HTTPError as exc:
            logger.warning("runway_cancel_transport_failed", correlation_id=correlation_id, task_id=task_id, error_type=type(exc).__name__)
            return False
        cancelled = response.status_code in {204, 404}
        logger.info("runway_cancel_result", correlation_id=correlation_id, task_id=task_id, status_code=response.status_code, cancelled=cancelled)
        return cancelled

    def get_organization(self, *, correlation_id: str) -> Dict[str, Any]:
        """Non-paid canary: verifies credentials and seedance2_5 account access."""
        try:
            response = self._request("GET", "/v1/organization")
        except httpx.HTTPError as exc:
            raise RunwayError("Runway organization probe failed in transport.", kind="transport") from exc
        if response.status_code >= 400:
            raise RunwayError(
                f"Runway organization probe failed with HTTP {response.status_code}.",
                kind="configuration" if response.status_code in {401, 403} else "transport",
                status_code=response.status_code,
            )
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise RunwayError("Runway organization probe returned an unreadable body.", kind="invalid_response") from exc
        if not isinstance(payload, dict):
            raise RunwayError("Runway organization probe returned an unexpected body.", kind="invalid_response")
        tier = payload.get("tier") if isinstance(payload.get("tier"), dict) else {}
        tier_models = tier.get("models") if isinstance(tier.get("models"), dict) else {}
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        usage_models = usage.get("models") if isinstance(usage.get("models"), dict) else {}
        model_tier = tier_models.get(SEEDANCE_2_5_MODEL) if isinstance(tier_models.get(SEEDANCE_2_5_MODEL), dict) else None
        model_usage = usage_models.get(SEEDANCE_2_5_MODEL) if isinstance(usage_models.get(SEEDANCE_2_5_MODEL), dict) else {}
        result = {
            "credit_balance": payload.get("creditBalance"),
            "max_monthly_credit_spend": tier.get("maxMonthlyCreditSpend"),
            "seedance2_5_available": model_tier is not None,
            "seedance2_5_max_concurrent_generations": (model_tier or {}).get("maxConcurrentGenerations"),
            "seedance2_5_max_daily_generations": (model_tier or {}).get("maxDailyGenerations"),
            "seedance2_5_daily_generations": model_usage.get("dailyGenerations"),
        }
        logger.info("runway_organization_probe", correlation_id=correlation_id, **result)
        return result


def get_runway_client(settings: Any = None) -> RunwayClient:
    """Build a client from settings; raises ``RunwayError(kind='configuration')`` when unset."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return RunwayClient(
        api_key=str(getattr(settings, "runway_api_key", "") or ""),
        base_url=str(getattr(settings, "runway_api_base_url", RUNWAY_DEFAULT_BASE_URL) or RUNWAY_DEFAULT_BASE_URL),
        timeout_seconds=float(getattr(settings, "runway_api_timeout_seconds", 30.0)),
        output_allowed_hosts=parse_allowed_hosts(getattr(settings, "runway_output_allowed_hosts", "")),
        output_max_bytes=int(getattr(settings, "runway_output_max_bytes", 150 * 1024 * 1024)),
        download_timeout_seconds=float(getattr(settings, "runway_output_download_timeout_seconds", 180.0)),
    )


__all__ = [
    "DATA_URI_MAX_CHARS",
    "HTTPS_PROMPT_IMAGE_MAX_CHARS",
    "PROMPT_TEXT_MAX_CHARS",
    "RUNWAY_API_VERSION",
    "SEEDANCE_2_5_MODEL",
    "SEEDANCE_2_5_PORTRAIT_RATIOS",
    "TASK_STATUSES_ACTIVE",
    "TASK_STATUSES_TERMINAL",
    "RunwayClient",
    "RunwayError",
    "RunwayOutput",
    "RunwayTask",
    "build_image_data_uri",
    "get_runway_client",
    "host_is_allowed",
    "parse_allowed_hosts",
    "safe_url_label",
    "validate_output_url",
]
