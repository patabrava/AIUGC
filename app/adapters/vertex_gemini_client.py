"""Vertex AI Gemini REST adapter for text, JSON, image, and grounded research."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from copy import deepcopy
from typing import Any, Dict, Optional

import google.auth
import google.auth.exceptions
import httpx
from google.auth.transport.requests import Request

from app.core.config import get_settings, resolve_google_application_credentials_path
from app.core.errors import ThirdPartyError, ValidationError
from app.core.german import restore_german_umlauts, restore_german_umlauts_in_json
from app.core.logging import get_logger

logger = get_logger(__name__)

# Cap on simultaneous in-flight Vertex requests across the process.
# Prevents HTTP/2 stream collisions that crash the shared connection
# under bursts (observed as RemoteProtocolError / LocalProtocolError).
_VERTEX_INFLIGHT_LIMIT = int(os.environ.get("VERTEX_INFLIGHT_LIMIT", "4"))
_VERTEX_REQUEST_SEMAPHORE = threading.Semaphore(_VERTEX_INFLIGHT_LIMIT)


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
    ) -> str:
        target_model = model or self._settings.vertex_gemini_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_budget=thinking_budget,
        )
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_text",
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
    ) -> Dict[str, Any]:
        target_model = model or self._settings.vertex_gemini_image_model
        payload = self._build_generate_content_payload(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        payload.setdefault("generationConfig", {})
        payload["generationConfig"]["responseModalities"] = ["IMAGE"]
        payload["generationConfig"]["imageConfig"] = {"aspectRatio": "1:1", "imageSize": "1K"}
        data = self._post_generate_content(
            model=target_model,
            location=self._settings.vertex_ai_location,
            payload=payload,
            log_event="vertex_gemini_generate_image",
        )
        image_payload = self._extract_image_bytes(data)
        return {
            "image_bytes": image_payload["bytes"],
            "mime_type": image_payload["mime_type"],
            "model": target_model,
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
    ) -> Dict[str, Any]:
        self._ensure_configured()
        url = self._build_generate_content_url(model=model, location=location)
        last_exc: Optional[Exception] = None
        max_attempts = 4
        for attempt in range(max_attempts):
            with _VERTEX_REQUEST_SEMAPHORE:
                client = self._http_client
                client_generation = self._http_client_generation
                try:
                    response = client.post(
                        url,
                        headers=self._build_headers(include_json=True),
                        json=payload,
                    )
                    break
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
            # Outside the semaphore: rebuild the client (one thread wins;
            # others piggy-back on the rebuilt instance), then back off.
            self._recycle_http_client(client_generation)
            if attempt < max_attempts - 1:
                time.sleep(0.5 * (2 ** attempt))  # 0.5, 1.0, 2.0 s
                continue
            raise ThirdPartyError(
                message="Vertex Gemini generateContent failed (transport)",
                details={
                    "error_class": type(last_exc).__name__,
                    "error": str(last_exc)[:300],
                    "model": model,
                    "location": location,
                    "attempts": max_attempts,
                },
            ) from last_exc
        if response.status_code >= 400:
            logger.error(
                f"{log_event}_http_error",
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
                },
            )
        logger.info(log_event, model=model, location=location)
        return response.json()

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
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": self._merge_prompts(system_prompt, prompt)}],
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

    def _get_credentials(self):
        with self._credentials_lock:
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
                self._credentials.refresh(Request())
            return self._credentials

    def _build_headers(self, include_json: bool = False) -> Dict[str, str]:
        creds = self._get_credentials()
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
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline_data.get("data")
            if encoded:
                return {
                    "bytes": base64.b64decode(encoded),
                    "mime_type": inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png",
                }
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
