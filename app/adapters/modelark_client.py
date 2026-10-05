"""Direct BytePlus ModelArk Seedance 2.5 REST adapter; never retries paid POSTs.

Official contract: /api/v3/contents/generations/tasks, Bearer API key,
model dreamina-seedance-2-5-260628. No Runway headers or intermediary.
Money is represented as integer USD microdollars (1 unit = $0.000001).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.adapters.video_provider_transport import (
    SafeVideoOutputDownloader, VideoProviderError, VideoTask, parse_allowed_hosts,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
MODEL = "dreamina-seedance-2-5-260628"
BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
TASK_ID = re.compile(r"^cgt-[A-Za-z0-9_-]{4,123}$")
ASSET_URI = re.compile(r"^asset://[A-Za-z0-9_-]{1,128}$")
DIMENSIONS = {"480p": (480, 854), "720p": (720, 1280)}
RATIOS = {key: f"{w}:{h}" for key, (w, h) in DIMENSIONS.items()}


def cost_units(tokens: int, usd_per_million_tokens: Any = "10.70") -> int:
    if isinstance(tokens, bool) or tokens < 0:
        raise ValueError("Token usage must be a non-negative integer")
    return int((Decimal(tokens) * Decimal(str(usd_per_million_tokens))).to_integral_value(rounding=ROUND_CEILING))


def estimate_cost(resolution: str, duration_seconds: int, settings: Any) -> dict:
    if resolution not in DIMENSIONS or not 4 <= duration_seconds <= 30:
        raise VideoProviderError("Unsupported Seedance 2.5 output specification.", kind="rejected")
    w, h = DIMENSIONS[resolution]
    tokens = int((Decimal(w * h * duration_seconds * 24) / 1024).to_integral_value(rounding=ROUND_CEILING))
    rate = getattr(settings, "modelark_usd_per_million_tokens", Decimal("10.70"))
    expected = cost_units(tokens, rate)
    # Provider output aligns frame dimensions. Reserve 15% headroom, not extra paid work.
    reserved = int((Decimal(expected) * Decimal("1.15")).to_integral_value(rounding=ROUND_CEILING))
    return {"expected_tokens": tokens, "expected_units": expected, "reserved_units": reserved,
            "expected_usd": str(Decimal(expected) / 1_000_000),
            "usd_per_million_tokens": str(rate), "unit": "usd_microdollar"}


class ModelArkClient(SafeVideoOutputDownloader):
    def __init__(self, *, api_key: str, base_url: str = BASE_URL,
                 timeout_seconds: float = 30, output_allowed_hosts=(),
                 output_max_bytes: int = 150 * 1024 * 1024,
                 download_timeout_seconds: float = 180,
                 usd_per_million_tokens: Any = "10.70", http_client: Optional[httpx.Client] = None):
        parsed = urlparse(base_url)
        if not api_key.strip():
            raise VideoProviderError("MODELARK_API_KEY is not configured.", kind="configuration")
        if (parsed.scheme != "https" or parsed.hostname != "ark.ap-southeast.bytepluses.com"
                or parsed.path.rstrip("/") != "/api/v3" or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise VideoProviderError("ModelArk API URL must be the official region endpoint.", kind="configuration")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(float(timeout_seconds), connect=10)
        self._http_client = http_client
        self._output_allowed_hosts = parse_allowed_hosts(output_allowed_hosts)
        self._output_max_bytes = int(output_max_bytes)
        self._download_timeout_seconds = float(download_timeout_seconds)
        self._rate = Decimal(str(usd_per_million_tokens))

    def __repr__(self):
        return f"ModelArkClient(base_url={self._base_url!r}, model={MODEL!r})"

    def _request(self, method, path, body=None):
        owned = self._http_client is None
        client = self._http_client or httpx.Client(follow_redirects=False)
        try:
            return client.request(method, self._base_url + path, json=body,
                                  headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                                  timeout=self._timeout, follow_redirects=False)
        finally:
            if owned:
                client.close()

    @staticmethod
    def _error(response, kind):
        # Upstream messages can echo prompts or URLs; preserve only a structured code.
        try:
            payload = response.json()
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
        except ValueError:
            code = ""
        code = code[:120] if re.fullmatch(r"[A-Za-z0-9_.-]*", code) else "provider_error"
        return VideoProviderError(f"ModelArk returned HTTP {response.status_code}" + (f" ({code})." if code else "."),
                                  kind=kind, status_code=response.status_code,
                                  details={"failure_code": code or None})

    def submit_image_to_video(self, *, prompt_image, prompt_text, ratio, duration_seconds,
                              audio, seed, correlation_id, reference_only=False):
        resolution = next((key for key, value in RATIOS.items() if value == ratio), None)
        if resolution is None or isinstance(duration_seconds, bool) or duration_seconds != 8:
            raise VideoProviderError("ModelArk evaluations require 8 seconds at 480p or 720p portrait.", kind="rejected")
        if not prompt_text.strip() or len(prompt_text) > 15000:
            raise VideoProviderError("The persisted prompt must contain 1–15000 characters.", kind="rejected")
        parsed = urlparse(prompt_image)
        valid_image = (bool(ASSET_URI.fullmatch(prompt_image)) or
                       (parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password) or
                       prompt_image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")))
        if not valid_image or len(prompt_image) > 5_242_880:
            raise VideoProviderError("Invalid ModelArk image input.", kind="rejected")
        if seed is not None and (isinstance(seed, bool) or not 0 <= seed <= 2_147_483_647):
            raise VideoProviderError("ModelArk seed is outside its signed 32-bit range.", kind="rejected")
        body = {"model": MODEL, "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": prompt_image},
             "role": "reference_image" if reference_only else "first_frame"}],
            "resolution": resolution, "ratio": "9:16", "duration": 8,
            "generate_audio": bool(audio), "watermark": False, "output_format": "mp4"}
        if seed is not None:
            body["seed"] = seed
        logger.info("modelark_submit_starting", correlation_id=correlation_id, model=MODEL, resolution=resolution)
        try:
            response = self._request("POST", "/contents/generations/tasks", body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise VideoProviderError("ModelArk request was not sent.", kind="not_submitted") from exc
        except httpx.HTTPError as exc:
            raise VideoProviderError("ModelArk submission outcome is unknown; do not resubmit.", kind="ambiguous") from exc
        if response.status_code == 429:
            raise self._error(response, "rate_limited")
        if response.status_code in {400, 401, 403, 404, 405, 413, 415, 422}:
            raise self._error(response, "rejected")
        if not 200 <= response.status_code < 300:
            raise self._error(response, "ambiguous")
        try:
            payload = response.json()
            task_id = str(payload.get("id", ""))
        except (ValueError, AttributeError) as exc:
            raise VideoProviderError("ModelArk accepted a request without a readable acknowledgement.", kind="ambiguous") from exc
        if not TASK_ID.fullmatch(task_id):
            raise VideoProviderError("ModelArk accepted a request without a valid task id.", kind="ambiguous")
        return {"task_id": task_id, "estimated_credits": None}

    def get_task(self, task_id, *, correlation_id):
        if not TASK_ID.fullmatch(task_id):
            raise VideoProviderError("Invalid ModelArk task id.", kind="invalid_response")
        try:
            response = self._request("GET", f"/contents/generations/tasks/{task_id}")
        except httpx.HTTPError as exc:
            raise VideoProviderError("ModelArk polling transport failed.", kind="transport") from exc
        if response.status_code == 404:
            raise self._error(response, "not_found")
        if response.status_code >= 400:
            raise self._error(response, "rate_limited" if response.status_code == 429 else "transport")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VideoProviderError("Unreadable ModelArk task response.", kind="invalid_response") from exc
        if not isinstance(payload, dict) or payload.get("id") != task_id or payload.get("model", MODEL) != MODEL:
            raise VideoProviderError("ModelArk returned a mismatched task or model.", kind="invalid_response")
        raw_status = payload.get("status")
        status = {"queued": "PENDING", "running": "RUNNING", "succeeded": "SUCCEEDED",
                  "failed": "FAILED", "expired": "FAILED", "cancelled": "CANCELLED"}.get(raw_status, "UNKNOWN")
        usage = payload.get("usage") or {}
        tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        units = cost_units(tokens, self._rate) if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else None
        if status in {"FAILED", "CANCELLED"}:
            units = 0  # BytePlus charges only successful generations.
        error = payload.get("error") or {}
        code = str(error.get("code", ""))[:120] if isinstance(error, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]*", code):
            code = "provider_error"
        content = payload.get("content") or {}
        url = content.get("video_url") if isinstance(content, dict) else None
        return VideoTask(task_id=task_id, status=status, progress=None,
                         output_urls=(url,) if isinstance(url, str) and url else (),
                         failure=f"ModelArk task failed ({code or raw_status})." if status == "FAILED" else None,
                         failure_code=code or None, estimated_credits=None, cost_credits=units,
                         usage_tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else None)

    def cancel_task(self, task_id, *, correlation_id):
        # ModelArk DELETE also deletes task records. It is not a reliable running-task
        # cancellation acknowledgement; retain the durable submission/quota fence.
        try:
            return self.get_task(task_id, correlation_id=correlation_id).status == "CANCELLED"
        except VideoProviderError:
            return False

    def probe_access(self):
        """Non-paid list-tasks probe proves authentication, not model activation."""
        try:
            response = self._request("GET", "/contents/generations/tasks?page_size=1")
        except httpx.HTTPError as exc:
            raise VideoProviderError("ModelArk access probe failed.", kind="transport") from exc
        if response.status_code != 200:
            raise self._error(response, "configuration")
        return {"authenticated": True, "model": MODEL, "model_activation_verified": False}


def get_modelark_client(settings):
    return ModelArkClient(api_key=str(getattr(settings, "modelark_api_key", "") or ""),
        base_url=getattr(settings, "modelark_api_base_url", BASE_URL),
        timeout_seconds=getattr(settings, "modelark_api_timeout_seconds", 30),
        output_allowed_hosts=parse_allowed_hosts(getattr(settings, "modelark_output_allowed_hosts", "")),
        output_max_bytes=getattr(settings, "modelark_output_max_bytes", 150 * 1024 * 1024),
        download_timeout_seconds=getattr(settings, "modelark_output_download_timeout_seconds", 180),
        usd_per_million_tokens=getattr(settings, "modelark_usd_per_million_tokens", "10.70"))
