"""Vertex AI Gemini REST adapter for text, JSON, image, and grounded research."""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import json
import math
import os
import threading
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

import google.auth
import google.auth.exceptions
import httpx
from google.auth.transport.requests import Request

from app.core.config import get_settings, resolve_google_application_credentials_path
from app.core.errors import ThirdPartyError, ValidationError
from app.core.german import restore_german_umlauts, restore_german_umlauts_in_json
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_GEMINI_INLINE_MEDIA_BYTES = 12 * 1024 * 1024

# Cap on simultaneous in-flight Vertex requests across the process.
# Prevents HTTP/2 stream collisions that crash the shared connection
# under bursts (observed as RemoteProtocolError / LocalProtocolError).
_VERTEX_INFLIGHT_LIMIT = int(os.environ.get("VERTEX_INFLIGHT_LIMIT", "4"))
_VERTEX_REQUEST_SEMAPHORE = threading.Semaphore(_VERTEX_INFLIGHT_LIMIT)
_VERTEX_GENERATE_CONTENT_MAX_ATTEMPTS = 4
_VERTEX_RETRY_BASE_SECONDS = 0.5
_VERTEX_RETRY_MAX_SECONDS = 8.0
_VERTEX_TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_VERTEX_CAPACITY_WAIT_SECONDS = 15.0


class _DeadlineBoundAuthRequest:
    """Clamp every Google-auth transport call to one absolute deadline."""

    def __init__(self, deadline_at: float):
        self._deadline_at = float(deadline_at)
        self._request = Request()

    def __call__(self, *args, timeout=120, **kwargs):
        remaining = self._deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Google credential refresh exceeded its absolute deadline")
        try:
            requested_timeout = float(timeout)
        except (TypeError, ValueError):
            requested_timeout = remaining
        return self._request(
            *args,
            timeout=max(0.05, min(requested_timeout, remaining)),
            **kwargs,
        )


def _vertex_capacity_budget_seconds(timeout_seconds: float) -> float:
    return min(5.0, max(0.25, float(timeout_seconds) * 0.08))


def _vertex_http_timeout(total_seconds: float) -> httpx.Timeout:
    """Allocate a total call budget across independent httpx phase limits."""
    total = max(1.0, float(total_seconds))
    capacity = _vertex_capacity_budget_seconds(total)
    pool = min(1.0, max(0.1, total * 0.02))
    network = max(0.3, total - capacity - pool)
    connect = min(10.0, max(0.1, network * 0.12))
    write = min(15.0, max(0.1, network * 0.18))
    read = max(0.1, network - connect - write)
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


async def _async_vertex_post_with_deadline(
    *,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> httpx.Response:
    async with httpx.AsyncClient(
        http2=False,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        timeout=_vertex_http_timeout(timeout_seconds),
    ) as client:
        return await asyncio.wait_for(
            client.post(url, headers=headers, json=payload),
            timeout=max(0.1, float(timeout_seconds)),
        )


def _vertex_post_with_deadline(
    *,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> httpx.Response:
    """Run one request under a cancellable absolute wall-clock deadline."""
    try:
        return asyncio.run(
            _async_vertex_post_with_deadline(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        )
    except TimeoutError as exc:
        raise httpx.TimeoutException(
            "Vertex Gemini request exceeded its absolute deadline"
        ) from exc


@contextmanager
def _vertex_request_slot(*, timeout_seconds: Optional[float] = None):
    """Bound queue wait for deadline-aware requests before network I/O starts."""
    if timeout_seconds is None:
        acquired = _VERTEX_REQUEST_SEMAPHORE.acquire()
    else:
        acquired = _VERTEX_REQUEST_SEMAPHORE.acquire(
            timeout=min(
                _VERTEX_CAPACITY_WAIT_SECONDS,
                _vertex_capacity_budget_seconds(float(timeout_seconds)),
            )
        )
    if not acquired:
        raise ThirdPartyError(
            message="Vertex Gemini request capacity wait timed out",
            details={
                "reason_code": "provider_capacity_timeout",
                "status_code": 503,
            },
        )
    try:
        yield
    finally:
        _VERTEX_REQUEST_SEMAPHORE.release()


def _vertex_retry_delay_seconds(*, attempt: int, response: Optional[Any] = None) -> float:
    delay = min(
        _VERTEX_RETRY_MAX_SECONDS,
        _VERTEX_RETRY_BASE_SECONDS * (2 ** attempt),
    )
    if response is None:
        return delay
    retry_after = str(response.headers.get("Retry-After") or "").strip()
    if not retry_after:
        return delay
    try:
        retry_after_seconds = float(retry_after)
    except (TypeError, ValueError):
        return delay
    if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
        return delay
    return min(
        _VERTEX_RETRY_MAX_SECONDS,
        max(delay, retry_after_seconds),
    )


class VertexGeminiClient:
    """Singleton adapter for Gemini models through Vertex AI."""

    _instance: Optional["VertexGeminiClient"] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        with self._instance_lock:
            if getattr(self, "_initialized", False):
                return
            self._settings = get_settings()
            self._http_client_lock = threading.Lock()
            self._credentials_lock = threading.Lock()
            self._http_client = self._build_http_client()
            self._http_client_generation = 0
            self._credentials = None
            self._initialized = True
            logger.info("vertex_gemini_client_initialized")

    @staticmethod
    def _build_http_client() -> "httpx.Client":
        # Force HTTP/1.1: HTTP/2 stream-state corruption (StreamIDTooLowError,
        # last_stream_id, KeyError on stream tracker) was the dominant
        # concurrency failure under bursts. HTTP/1.1 has no shared-stream
        # state, only a connection pool, which httpx handles cleanly.
        return httpx.Client(
            http2=False,
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=None),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    def _recycle_http_client(self, observed_generation: int) -> None:
        """Drop the shared httpx.Client after a connection-level error.

        Vertex's HTTP/2 server caps streams per connection (~30) and tears
        the connection down once that's hit. All in-flight requests on the
        dying connection fail with RemoteProtocolError. Rebuilding the
        client forces a fresh TCP handshake on the next call.
        """
        with self._http_client_lock:
            if observed_generation != self._http_client_generation:
                # Another thread already recycled — reuse its new client.
                return
            old = self._http_client
            self._http_client = self._build_http_client()
            self._http_client_generation += 1
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("vertex_gemini_http_client_recycled", generation=self._http_client_generation)

    def generate_text(
        self,
        *,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        thinking_budget: Optional[int] = None,
        input_images: Optional[List[Dict[str, Any]]] = None,
        input_media: Optional[List[Dict[str, Any]]] = None,
        location: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        provider_max_attempts: Optional[int] = None,
    ) -> str:
        target_model = model or self._settings.vertex_gemini_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_budget=thinking_budget,
            input_images=input_images,
            input_media=input_media,
        )
        data = self._post_generate_content(
            model=target_model,
            location=location or self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_text",
            timeout_seconds=timeout_seconds,
            max_attempts=provider_max_attempts,
        )
        return restore_german_umlauts(self._extract_candidate_text(data))

    def generate_json(
        self,
        *,
        prompt: str,
        json_schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        target_model = model or self._settings.vertex_gemini_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        payload.setdefault("generationConfig", {})
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = self._to_vertex_response_schema(
            json_schema.get("schema", json_schema)
        )
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_json",
        )
        content = self._extract_candidate_text(data)
        try:
            return restore_german_umlauts_in_json(json.loads(content))
        except json.JSONDecodeError as exc:
            raise ValidationError(
                message="Vertex Gemini structured output produced invalid JSON",
                details={"error": str(exc), "model": target_model},
            ) from exc

    def generate_image(
        self,
        *,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        aspect_ratio: str = "1:1",
        image_size: str = "1K",
        input_images: Optional[List[Dict[str, Any]]] = None,
        provider_max_attempts: Optional[int] = None,
        provider_timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        target_model = model or self._settings.vertex_gemini_image_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            input_images=input_images,
        )
        payload.setdefault("generationConfig", {})
        payload["generationConfig"]["responseModalities"] = ["IMAGE"]
        payload["generationConfig"]["imageConfig"] = {
            "aspectRatio": aspect_ratio,
            "imageSize": image_size,
        }
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_gemini_image_location or self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_image",
            max_attempts=provider_max_attempts,
            timeout_seconds=provider_timeout_seconds,
        )
        image_payload = self._extract_image_bytes(data)
        return {
            "image_bytes": image_payload["bytes"],
            "mime_type": image_payload["mime_type"],
            "model": target_model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "raw_response": data,
        }

    def generate_images(
        self,
        *,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        aspect_ratio: str = "1:1",
        image_size: str = "1K",
        input_images: Optional[List[Dict[str, Any]]] = None,
        provider_max_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate every inline image returned by one Gemini request."""
        target_model = model or self._settings.vertex_gemini_image_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            input_images=input_images,
        )
        payload.setdefault("generationConfig", {})
        payload["generationConfig"]["responseModalities"] = ["IMAGE"]
        payload["generationConfig"]["imageConfig"] = {
            "aspectRatio": aspect_ratio,
            "imageSize": image_size,
        }
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_gemini_image_location or self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_images",
            max_attempts=provider_max_attempts,
        )
        image_payloads = self._extract_images_bytes(data)
        return {
            "images": [
                {
                    "image_bytes": image_payload["bytes"],
                    "mime_type": image_payload["mime_type"],
                }
                for image_payload in image_payloads
            ],
            "model": target_model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "raw_response": data,
        }

    def generate_grounded_research(
        self,
        *,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run grounded research via Google Search tool.

        Returns a dict with:
          - ``text``: concatenated text parts from the response.
          - ``grounding_chunks``: list of ``{"uri", "title"}`` extracted from
            ``candidates[0].groundingMetadata.groundingChunks[].web``. May be empty.
        """
        target_model = model or self._settings.vertex_grounded_research_model
        research_prompt = self._merge_prompts(
            system_prompt,
            "\n".join(
                [
                    prompt.strip(),
                    "",
                    "Research using current public web information.",
                    "Return a detailed cited German research dossier in plain text.",
                    "If a fact is uncertain or unavailable, say so explicitly instead of inventing it.",
                ]
            ),
        )
        payload = self._build_generate_content_payload(
            prompt=research_prompt,
            system_prompt=None,
            max_tokens=max_tokens,
            temperature=1.0 if temperature is None else temperature,
        )
        payload["tools"] = [{"googleSearch": {}}]
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_grounded_research_location,
            payload=payload,
            log_event="vertex_gemini_grounded_research",
        )
        return {
            "text": self._extract_candidate_text(data),
            "grounding_chunks": self._extract_grounding_chunks(data),
        }

    def _extract_grounding_chunks(self, data: Dict[str, Any]) -> list:
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        metadata = (candidates[0] or {}).get("groundingMetadata") or {}
        chunks = metadata.get("groundingChunks") or []
        results = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web") or {}
            uri = str(web.get("uri") or "").strip()
            if not uri:
                continue
            results.append(
                {
                    "uri": uri,
                    "title": str(web.get("title") or "").strip(),
                }
            )
        return results

    def _post_generate_content(
        self,
        *,
        model: str,
        location: str,
        payload: Dict[str, Any],
        log_event: str,
        max_attempts: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._ensure_configured()
        url = self._build_generate_content_url(model=model, location=location)
        last_exc: Optional[Exception] = None
        attempt_limit = (
            _VERTEX_GENERATE_CONTENT_MAX_ATTEMPTS
            if max_attempts is None
            else max(1, int(max_attempts))
        )
        for attempt in range(attempt_limit):
            response = None
            absolute_deadline = (
                time.monotonic() + float(timeout_seconds)
                if timeout_seconds is not None
                else None
            )
            with _vertex_request_slot(timeout_seconds=timeout_seconds):
                client = self._http_client
                client_generation = self._http_client_generation
                try:
                    headers = self._build_headers(
                        include_json=True,
                        deadline_at=absolute_deadline,
                    )
                    if absolute_deadline is not None:
                        remaining = absolute_deadline - time.monotonic()
                        if remaining <= 0.1:
                            raise httpx.TimeoutException(
                                "Vertex Gemini capacity wait exhausted the request deadline"
                            )
                        response = _vertex_post_with_deadline(
                            url=url,
                            headers=headers,
                            payload=payload,
                            timeout_seconds=remaining,
                        )
                    else:
                        response = client.post(
                            url,
                            headers=headers,
                            json=payload,
                        )
                # Catch every transport-layer error (HTTP/2 stream errors,
                # connection drops, socket-level ReadError, h2's KeyError on
                # its stream tracker) and trigger a recycle + retry.
                except (httpx.HTTPError, KeyError, ConnectionError, OSError) as exc:
                    last_exc = exc
                    logger.warning(
                        f"{log_event}_transport_error",
                        attempt=attempt,
                        error_class=type(exc).__name__,
                        error=str(exc)[:200],
                        model=model,
                    )

            if response is None:
                # Outside the semaphore: rebuild the client (one thread wins;
                # others piggy-back on the rebuilt instance), then back off.
                if timeout_seconds is None:
                    self._recycle_http_client(client_generation)
                if attempt < attempt_limit - 1:
                    time.sleep(_vertex_retry_delay_seconds(attempt=attempt))
                    continue
                raise ThirdPartyError(
                    message="Vertex Gemini generateContent failed (transport)",
                    details={
                        "error_class": type(last_exc).__name__,
                        "error": str(last_exc)[:300],
                        "model": model,
                        "location": location,
                        "attempts": attempt_limit,
                    },
                ) from last_exc

            if response.status_code < 400:
                logger.info(log_event, model=model, location=location)
                return response.json()

            is_transient = (
                response.status_code in _VERTEX_TRANSIENT_HTTP_STATUS_CODES
            )
            if (
                is_transient
                and attempt < attempt_limit - 1
            ):
                delay_seconds = _vertex_retry_delay_seconds(
                    attempt=attempt,
                    response=response,
                )
                logger.warning(
                    f"{log_event}_http_retry",
                    attempt=attempt + 1,
                    max_attempts=attempt_limit,
                    status_code=response.status_code,
                    delay_seconds=delay_seconds,
                    model=model,
                    location=location,
                )
                time.sleep(delay_seconds)
                continue

            logger.error(
                f"{log_event}_http_error",
                attempts=attempt + 1,
                status_code=response.status_code,
                response_text=response.text,
                model=model,
                location=location,
            )
            raise ThirdPartyError(
                message="Vertex Gemini generateContent failed",
                details={
                    "status_code": response.status_code,
                    "body": response.text,
                    "model": model,
                    "location": location,
                    "attempts": attempt + 1,
                },
            )

        raise AssertionError("Vertex Gemini generateContent retry loop exhausted unexpectedly.")

    def _build_generate_content_url(self, *, model: str, location: str) -> str:
        project = self._settings.vertex_ai_project_id
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        return (
            f"https://{host}/v1"
            f"/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent"
        )

    def _build_generate_content_payload(
        self,
        *,
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: Optional[int],
        temperature: Optional[float],
        thinking_budget: Optional[int] = None,
        input_images: Optional[List[Dict[str, Any]]] = None,
        input_media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        parts: List[Dict[str, Any]] = [{"text": self._merge_prompts(system_prompt, prompt)}]
        for index, image in enumerate(input_images or [], start=1):
            mime_type = str(image.get("mime_type") or "").strip()
            image_bytes = image.get("image_bytes")
            if not mime_type.startswith("image/") or not isinstance(image_bytes, bytes) or not image_bytes:
                raise ValidationError(
                    "Gemini input images require non-empty bytes and an image MIME type.",
                    {"image_index": index, "mime_type": mime_type},
                )
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        inline_media_bytes = 0
        for index, media in enumerate(input_media or [], start=1):
            mime_type = str(media.get("mime_type") or "").strip()
            media_bytes = media.get("media_bytes")
            if (
                not mime_type.startswith(("audio/", "video/"))
                or not isinstance(media_bytes, bytes)
                or not media_bytes
            ):
                raise ValidationError(
                    "Gemini input media require non-empty bytes and an audio or video MIME type.",
                    {"media_index": index, "mime_type": mime_type},
                )
            inline_media_bytes += len(media_bytes)
            if inline_media_bytes > _MAX_GEMINI_INLINE_MEDIA_BYTES:
                raise ValidationError(
                    "Gemini inline media payload size exceeds the safe request limit.",
                    {
                        "media_index": index,
                        "inline_media_bytes": inline_media_bytes,
                        "maximum_bytes": _MAX_GEMINI_INLINE_MEDIA_BYTES,
                    },
                )
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(media_bytes).decode("ascii"),
                    }
                }
            )
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ]
        }
        if max_tokens is not None or temperature is not None or thinking_budget is not None:
            payload["generationConfig"] = {}
        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if temperature is not None:
            payload["generationConfig"]["temperature"] = temperature
        if thinking_budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        return payload

    def _ensure_configured(self) -> None:
        if not self._settings.vertex_ai_enabled:
            raise ValidationError(
                "Vertex AI is not enabled for this environment.",
                {"vertex_ai_enabled": self._settings.vertex_ai_enabled},
            )
        if not self._settings.vertex_ai_project_id:
            raise ValidationError(
                "Vertex AI project ID is required.",
                {"vertex_ai_project_id": self._settings.vertex_ai_project_id},
            )

    def _load_or_refresh_credentials(self, *, deadline_at: Optional[float] = None):
        auth_request = (
            _DeadlineBoundAuthRequest(deadline_at)
            if deadline_at is not None
            else Request()
        )
        if self._credentials is None:
            adc_path = resolve_google_application_credentials_path(self._settings)
            if adc_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path
            project_id = self._settings.vertex_ai_project_id.strip()
            if project_id:
                if not os.getenv("GOOGLE_CLOUD_PROJECT"):
                    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
                if not os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT"):
                    os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project_id
            try:
                self._credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    request=auth_request,
                    quota_project_id=project_id or None,
                )
            except google.auth.exceptions.DefaultCredentialsError as exc:
                raise ValidationError(
                    "No Google Cloud Application Default Credentials found. "
                    "Run `gcloud auth application-default login` or set GOOGLE_APPLICATION_CREDENTIALS.",
                    {"error": str(exc)},
                ) from exc
            if project_id and hasattr(self._credentials, "with_quota_project"):
                self._credentials = self._credentials.with_quota_project(project_id)
        if self._credentials.expired or not self._credentials.token:
            self._credentials.refresh(auth_request)
        return self._credentials

    def _get_credentials(self, *, deadline_at: Optional[float] = None):
        if deadline_at is None:
            with self._credentials_lock:
                return self._load_or_refresh_credentials()

        remaining = float(deadline_at) - time.monotonic()
        if remaining <= 0 or not self._credentials_lock.acquire(timeout=remaining):
            raise httpx.TimeoutException(
                "Vertex credential lock exceeded its absolute deadline"
            )

        result: Dict[str, Any] = {}
        completed = threading.Event()

        def load_credentials() -> None:
            try:
                result["credentials"] = self._load_or_refresh_credentials(
                    deadline_at=deadline_at
                )
            except BaseException as exc:  # Propagate worker failures to the request thread.
                result["error"] = exc
            finally:
                self._credentials_lock.release()
                completed.set()

        remaining = float(deadline_at) - time.monotonic()
        if remaining <= 0:
            self._credentials_lock.release()
            raise httpx.TimeoutException(
                "Vertex credential refresh exceeded its absolute deadline"
            )
        threading.Thread(
            target=load_credentials,
            name="vertex-credential-refresh",
            daemon=True,
        ).start()
        if not completed.wait(timeout=remaining):
            raise httpx.TimeoutException(
                "Vertex credential refresh exceeded its absolute deadline"
            )
        if "error" in result:
            raise result["error"]
        return result["credentials"]

    def _build_headers(
        self,
        include_json: bool = False,
        *,
        deadline_at: Optional[float] = None,
    ) -> Dict[str, str]:
        creds = self._get_credentials(deadline_at=deadline_at)
        headers = {"Authorization": f"Bearer {creds.token}"}
        quota_project_id = getattr(creds, "quota_project_id", None) or self._settings.vertex_ai_project_id
        if quota_project_id:
            headers["x-goog-user-project"] = quota_project_id
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers

    def _extract_candidate_text(self, data: Dict[str, Any]) -> str:
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        texts = [str(part.get("text")) for part in parts if isinstance(part, dict) and part.get("text")]
        if texts:
            return "\n".join(texts).strip()
        raise ThirdPartyError(message="Vertex Gemini response missing text", details={"response": data})

    def _extract_image_bytes(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._extract_images_bytes(data)[0]

    def _extract_images_bytes(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        images: List[Dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline_data.get("data")
            if encoded:
                images.append(
                    {
                        "bytes": base64.b64decode(encoded),
                        "mime_type": inline_data.get("mimeType")
                        or inline_data.get("mime_type")
                        or "image/png",
                    }
                )
        if images:
            return images
        raise ThirdPartyError(message="Vertex Gemini response missing image data", details={"response": data})

    def _merge_prompts(self, system_prompt: Optional[str], prompt: str) -> str:
        if system_prompt:
            return f"{system_prompt.strip()}\n\nUSER TASK:\n{prompt.strip()}"
        return prompt.strip()

    def _resolve_vertex_schema_ref(self, ref: str, root_schema: Any) -> Any:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return None

        node: Any = root_schema
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return deepcopy(node)

    def _to_vertex_response_schema(self, schema: Any, *, root_schema: Any = None) -> Any:
        if root_schema is None:
            root_schema = schema
        if isinstance(schema, dict):
            current = dict(schema)
            ref = current.get("$ref")
            if ref:
                resolved = self._resolve_vertex_schema_ref(str(ref), root_schema)
                current.pop("$ref", None)
                if resolved is not None:
                    if isinstance(resolved, dict):
                        resolved.update(current)
                    current = resolved

            cleaned = {}
            for key, value in current.items():
                if key in {"additionalProperties", "strict", "name", "$schema", "$defs", "definitions"}:
                    continue
                cleaned[key] = self._to_vertex_response_schema(value, root_schema=root_schema)
            return cleaned
        if isinstance(schema, list):
            return [self._to_vertex_response_schema(item, root_schema=root_schema) for item in schema]
        return deepcopy(schema)


_vertex_gemini_client: Optional[VertexGeminiClient] = None
_vertex_gemini_client_lock = threading.Lock()


def get_vertex_gemini_client() -> VertexGeminiClient:
    """Get Vertex Gemini client singleton."""
    global _vertex_gemini_client
    if _vertex_gemini_client is None:
        with _vertex_gemini_client_lock:
            if _vertex_gemini_client is None:
                _vertex_gemini_client = VertexGeminiClient()
    return _vertex_gemini_client
