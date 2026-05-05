# Vertex Gemini Provider Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Gemini text, JSON, image, and research workloads through Vertex AI by default, keeping the consumer Gemini API only as an explicitly enabled legacy fallback for native Deep Research until Google exposes a documented Vertex Interactions endpoint.

**Architecture:** Add a focused Vertex Gemini REST adapter next to the existing video-only Vertex adapter, then keep `LLMClient.generate_gemini_*` as the stable public facade for existing topic and blog code. Text, JSON, and image calls go to Vertex `generateContent`; research calls use Vertex Gemini with Google Search grounding as the default replacement because official docs still document native Deep Research only on Gemini API Interactions, not Vertex.

**Tech Stack:** Python 3.11-compatible FastAPI monolith, `httpx==0.27.2`, existing `google.auth` ADC flow, Pydantic Settings, pytest.

**Locality Budget:** `{files: 8, LOC/file: app/adapters/vertex_gemini_client.py <= 360, app/adapters/llm_client.py net <= 900, config/tests/docs <= 260 each, deps: 0}`

---

## Web Research Findings

- Native Deep Research is documented as Gemini API Interactions only. The official Deep Research docs say it is exclusively available through the Interactions API and cannot be accessed through `generate_content`: https://ai.google.dev/gemini-api/docs/deep-research
- The official Google developer blog says Deep Research is available with a Gemini API key from Google AI Studio and that Google is working to bring it to Vertex AI for enterprises: https://blog.google/innovation-and-ai/technology/developers-tools/deep-research-agent-gemini-api/
- Vertex AI supports Gemini `generateContent` and `streamGenerateContent`: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
- Vertex AI supports structured JSON output with `responseMimeType` and `responseSchema`: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output
- Vertex AI supports Grounding with Google Search for Gemini models, which is the closest documented Vertex-native replacement for this app's Deep Research topic discovery path today: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-google-search

## Scope Check

This is one subsystem: Gemini provider routing. Video generation is already on `app/adapters/vertex_ai_client.py` and is not refactored here except for sharing config/auth conventions. The plan does not rewrite topic parsing, prompt templates, batch state transitions, or Veo polling.

## File Structure

- Create `app/adapters/vertex_gemini_client.py`: Vertex Gemini text/JSON/image/grounded-research REST adapter using existing ADC auth semantics.
- Modify `app/adapters/llm_client.py`: keep public `generate_gemini_*` methods and delegate to Vertex or legacy Gemini API based on config.
- Modify `app/core/config.py`: add provider switches, fallback flags, Vertex model defaults, and clearer startup fingerprint fields.
- Modify `app/main.py`: log active Gemini provider instead of only key presence.
- Modify `README.md`: replace Gemini API key as the default path with Vertex setup and call out Deep Research limitation.
- Modify `.env.example`: document the new provider/fallback variables.
- Modify `tests/test_vertex_ai_config.py`: cover new provider settings.
- Create `tests/test_vertex_gemini_client.py`: unit coverage for Vertex URLs, auth headers, payloads, schema payloads, image extraction, and grounded research payloads.
- Modify `tests/test_topics_gemini_flow.py`: preserve legacy Interactions tests under explicit fallback and add routing tests for Vertex-grounded research.

---

### Task 1: Config switches and startup fingerprint

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Test: `tests/test_vertex_ai_config.py`

- [ ] **Step 1: Write failing config tests**

Append these tests to `tests/test_vertex_ai_config.py`:

```python
def test_gemini_provider_defaults_to_vertex_without_legacy_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(tmp_path)

    settings = Settings()

    assert settings.gemini_provider == "vertex"
    assert settings.gemini_api_fallback_enabled is False
    assert settings.gemini_deep_research_provider == "vertex_grounded"
    assert settings.vertex_gemini_model == "gemini-2.5-flash"
    assert settings.vertex_gemini_image_model == "gemini-3.1-flash-image-preview"
    assert settings.vertex_grounded_research_model == "gemini-2.5-pro"
    assert settings.vertex_grounded_research_location == "global"


def test_gemini_provider_accepts_legacy_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(
        tmp_path,
        [
            "GEMINI_PROVIDER=gemini_api",
            "GEMINI_API_FALLBACK_ENABLED=true",
            "GEMINI_DEEP_RESEARCH_PROVIDER=gemini_api",
            "VERTEX_GEMINI_MODEL=gemini-2.5-pro",
            "VERTEX_GROUNDED_RESEARCH_LOCATION=us-central1",
        ],
    )

    settings = Settings()

    assert settings.gemini_provider == "gemini_api"
    assert settings.gemini_api_fallback_enabled is True
    assert settings.gemini_deep_research_provider == "gemini_api"
    assert settings.vertex_gemini_model == "gemini-2.5-pro"
    assert settings.vertex_grounded_research_location == "us-central1"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m pytest tests/test_vertex_ai_config.py -k "gemini_provider" -q
```

Expected: FAIL with `AttributeError` or Pydantic validation errors for missing settings.

- [ ] **Step 3: Add settings fields**

In `app/core/config.py`, replace the Gemini settings block at `# LLM Providers` with this expanded block while preserving existing field names used by callers:

```python
    # LLM Providers
    openai_api_key: str = Field("", description="OpenAI API key (required for Sora video generation)")
    openai_model: str = Field("gpt-4o-mini", description="Default OpenAI model identifier")
    gemini_provider: Literal["vertex", "gemini_api"] = Field(
        "vertex",
        validation_alias=AliasChoices("GEMINI_PROVIDER"),
        description="Primary Gemini transport. Use vertex by default; gemini_api is legacy fallback only.",
    )
    gemini_api_fallback_enabled: bool = Field(
        False,
        validation_alias=AliasChoices("GEMINI_API_FALLBACK_ENABLED"),
        description="Allow the consumer Gemini API key path for explicitly unsupported Vertex features.",
    )
    gemini_deep_research_provider: Literal["vertex_grounded", "gemini_api"] = Field(
        "vertex_grounded",
        validation_alias=AliasChoices("GEMINI_DEEP_RESEARCH_PROVIDER"),
        description="Research transport. vertex_grounded uses Vertex Gemini with Google Search grounding.",
    )
    gemini_api_key: str = Field(
        "",
        validation_alias=AliasChoices("gemini_api_key", "GEMINI_API_KEY"),
        description="Legacy Gemini API key. Only used when GEMINI_API_FALLBACK_ENABLED=true.",
    )
    gemini_topic_model: str = Field("gemini-2.5-flash", description="Legacy Gemini API model for fallback topic generation")
    gemini_image_model: str = Field(
        "gemini-3.1-flash-image-preview",
        description="Legacy Gemini API image model for fallback blog previews",
    )
    gemini_deep_research_agent: str = Field(
        "deep-research-preview-04-2026",
        description="Legacy Gemini Interactions API agent for Deep Research fallback",
    )
    vertex_gemini_model: str = Field(
        "gemini-2.5-flash",
        validation_alias=AliasChoices("VERTEX_GEMINI_MODEL"),
        description="Vertex Gemini model for text and JSON generation",
    )
    vertex_gemini_image_model: str = Field(
        "gemini-3.1-flash-image-preview",
        validation_alias=AliasChoices("VERTEX_GEMINI_IMAGE_MODEL"),
        description="Vertex Gemini image generation model",
    )
    vertex_grounded_research_model: str = Field(
        "gemini-2.5-pro",
        validation_alias=AliasChoices("VERTEX_GROUNDED_RESEARCH_MODEL"),
        description="Vertex Gemini model used for grounded research replacement",
    )
    vertex_grounded_research_location: str = Field(
        "global",
        validation_alias=AliasChoices("VERTEX_GROUNDED_RESEARCH_LOCATION"),
        description="Vertex location for grounded Search requests. Google examples use global.",
    )
    gemini_topic_timeout_seconds: int = Field(
        600,
        ge=30,
        le=1800,
        description="Maximum time to wait for Gemini topic requests",
    )
    gemini_topic_poll_seconds: int = Field(
        5,
        ge=1,
        le=30,
        description="Polling interval for legacy Gemini Deep Research interactions",
    )
```

- [ ] **Step 4: Update fingerprint and startup log**

Replace `google_ai_context_fingerprint` in `app/core/config.py` with:

```python
def google_ai_context_fingerprint(settings: Optional[Settings] = None) -> dict[str, Any]:
    """Summarize the active Google AI context for startup logging."""
    resolved = settings or get_settings()
    return {
        "gemini_provider": resolved.gemini_provider,
        "gemini_deep_research_provider": resolved.gemini_deep_research_provider,
        "gemini_api_fallback_enabled": resolved.gemini_api_fallback_enabled,
        "gemini_api_key_fingerprint": fingerprint_secret(resolved.gemini_api_key),
        "gemini_api_key_present": bool(resolved.gemini_api_key),
        "vertex_ai_project_id": resolved.vertex_ai_project_id or "unset",
        "vertex_ai_location": resolved.vertex_ai_location,
        "vertex_grounded_research_location": resolved.vertex_grounded_research_location,
        "google_application_credentials_configured": bool(resolve_google_application_credentials_path(resolved)),
    }
```

In `app/main.py`, replace the `gemini_api_key_alignment_verified` log block with:

```python
    logger.info(
        "gemini_provider_alignment_verified",
        gemini_provider=settings.gemini_provider,
        gemini_deep_research_provider=settings.gemini_deep_research_provider,
        gemini_api_fallback_enabled=settings.gemini_api_fallback_enabled,
    )
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_vertex_ai_config.py -k "gemini_provider" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/main.py tests/test_vertex_ai_config.py
git commit -m "config: add vertex gemini provider switches"
```

---

### Task 2: Vertex Gemini REST adapter

**Files:**
- Create: `app/adapters/vertex_gemini_client.py`
- Test: `tests/test_vertex_gemini_client.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_vertex_gemini_client.py` with:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.vertex_gemini_client import VertexGeminiClient


def _settings():
    return SimpleNamespace(
        vertex_ai_enabled=True,
        vertex_ai_project_id="test-project",
        vertex_ai_location="us-central1",
        vertex_gemini_model="gemini-2.5-flash",
        vertex_gemini_image_model="gemini-3.1-flash-image-preview",
        vertex_grounded_research_model="gemini-2.5-pro",
        vertex_grounded_research_location="global",
        google_application_credentials="",
        google_application_credentials_json="",
    )


def _client_with_response(response_json):
    VertexGeminiClient._instance = None
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = str(response_json)
    mock_response.json.return_value = response_json
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    mock_credentials = MagicMock()
    mock_credentials.token = "token-123"
    mock_credentials.expired = False
    mock_credentials.quota_project_id = "test-project"
    with patch("app.adapters.vertex_gemini_client.get_settings", return_value=_settings()), \
        patch("app.adapters.vertex_gemini_client.google.auth.default", return_value=(mock_credentials, None)), \
        patch("app.adapters.vertex_gemini_client.Request"), \
        patch("app.adapters.vertex_gemini_client.httpx.Client", return_value=mock_http):
        client = VertexGeminiClient()
    return client, mock_http


def test_generate_text_posts_vertex_generate_content_payload():
    client, mock_http = _client_with_response(
        {"candidates": [{"content": {"parts": [{"text": "Hallo Welt"}]}}]}
    )

    result = client.generate_text(prompt="Sag hallo", system_prompt="Deutsch.", max_tokens=32, temperature=0.2)

    assert result == "Hallo Welt"
    url = mock_http.post.call_args.kwargs["url"] if "url" in mock_http.post.call_args.kwargs else mock_http.post.call_args.args[0]
    payload = mock_http.post.call_args.kwargs["json"]
    headers = mock_http.post.call_args.kwargs["headers"]
    assert "us-central1-aiplatform.googleapis.com" in url
    assert "/publishers/google/models/gemini-2.5-flash:generateContent" in url
    assert headers["Authorization"] == "Bearer token-123"
    assert payload["generationConfig"]["maxOutputTokens"] == 32
    assert payload["generationConfig"]["temperature"] == 0.2
    assert payload["contents"][0]["parts"][0]["text"].startswith("Deutsch.")


def test_generate_json_uses_response_schema():
    client, mock_http = _client_with_response(
        {"candidates": [{"content": {"parts": [{"text": "{\"ok\": true}"}]}}]}
    )

    result = client.generate_json(
        prompt="Return JSON",
        json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    payload = mock_http.post.call_args.kwargs["json"]
    assert result == {"ok": True}
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseSchema"]["properties"]["ok"]["type"] == "boolean"


def test_generate_image_extracts_inline_data():
    client, mock_http = _client_with_response(
        {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}}]}}]}
    )

    result = client.generate_image(prompt="Cover")

    payload = mock_http.post.call_args.kwargs["json"]
    assert result["image_bytes"] == b"hello"
    assert result["mime_type"] == "image/png"
    assert payload["generationConfig"]["responseModalities"] == ["IMAGE"]


def test_generate_grounded_research_uses_global_location_and_search_tool():
    client, mock_http = _client_with_response(
        {"candidates": [{"content": {"parts": [{"text": "Research report"}]}}]}
    )

    result = client.generate_grounded_research(prompt="Research accessibility news", system_prompt="German only.")

    url = mock_http.post.call_args.kwargs["url"] if "url" in mock_http.post.call_args.kwargs else mock_http.post.call_args.args[0]
    payload = mock_http.post.call_args.kwargs["json"]
    assert result == "Research report"
    assert "global-aiplatform.googleapis.com" in url
    assert "/publishers/google/models/gemini-2.5-pro:generateContent" in url
    assert payload["tools"][0]["googleSearch"] == {}
    assert "German only." in payload["contents"][0]["parts"][0]["text"]
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m pytest tests/test_vertex_gemini_client.py -q
```

Expected: FAIL because `app.adapters.vertex_gemini_client` does not exist.

- [ ] **Step 3: Create the adapter**

Create `app/adapters/vertex_gemini_client.py` with this implementation:

```python
"""Vertex AI Gemini REST adapter for text, JSON, image, and grounded research."""

from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional

import google.auth
import httpx
from google.auth.transport.requests import Request

from app.core.config import get_settings, resolve_google_application_credentials_path
from app.core.errors import ThirdPartyError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)


class VertexGeminiClient:
    """Singleton adapter for Gemini models through Vertex AI."""

    _instance: Optional["VertexGeminiClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._settings = get_settings()
        self._http_client = httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=None),
            follow_redirects=True,
        )
        self._credentials = None
        self._initialized = True
        logger.info("vertex_gemini_client_initialized")

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
        return self._extract_candidate_text(data)

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
            return json.loads(content)
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
    ) -> str:
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
        return self._extract_candidate_text(data)

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
        response = self._http_client.post(url, headers=self._build_headers(include_json=True), json=payload)
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
                details={"status_code": response.status_code, "body": response.text, "model": model, "location": location},
            )
        logger.info(log_event, model=model, location=location)
        return response.json()

    def _build_generate_content_url(self, *, model: str, location: str) -> str:
        project = self._settings.vertex_ai_project_id
        return (
            f"https://{location}-aiplatform.googleapis.com/v1"
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
            raise ValidationError("Vertex AI is not enabled for this environment.", {"vertex_ai_enabled": False})
        if not self._settings.vertex_ai_project_id:
            raise ValidationError("Vertex AI project ID is required.", {"vertex_ai_project_id": ""})

    def _get_credentials(self):
        if self._credentials is None:
            adc_path = resolve_google_application_credentials_path(self._settings)
            if adc_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path
            project_id = self._settings.vertex_ai_project_id.strip()
            if project_id and not os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT"):
                os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project_id
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                quota_project_id=project_id or None,
            )
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

    def _to_vertex_response_schema(self, schema: Any, *, root_schema: Any = None) -> Any:
        if root_schema is None:
            root_schema = schema
        if isinstance(schema, dict):
            current = dict(schema)
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


def get_vertex_gemini_client() -> VertexGeminiClient:
    global _vertex_gemini_client
    if _vertex_gemini_client is None:
        _vertex_gemini_client = VertexGeminiClient()
    return _vertex_gemini_client
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
python3 -m pytest tests/test_vertex_gemini_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/vertex_gemini_client.py tests/test_vertex_gemini_client.py
git commit -m "feat: add vertex gemini rest adapter"
```

---

### Task 3: Route `LLMClient` Gemini facade through Vertex

**Files:**
- Modify: `app/adapters/llm_client.py`
- Test: `tests/test_vertex_gemini_client.py`

- [ ] **Step 1: Add failing routing tests**

Append to `tests/test_vertex_gemini_client.py`:

```python
def test_llm_client_routes_text_json_and_image_to_vertex(monkeypatch):
    from app.adapters import llm_client as llm_client_module

    calls = []

    class FakeVertex:
        def generate_text(self, **kwargs):
            calls.append(("text", kwargs))
            return "vertex text"

        def generate_json(self, **kwargs):
            calls.append(("json", kwargs))
            return {"source": "vertex"}

        def generate_image(self, **kwargs):
            calls.append(("image", kwargs))
            return {"image_bytes": b"img", "mime_type": "image/png", "model": "vertex", "raw_response": {}}

    settings = SimpleNamespace(
        openai_api_key="",
        openai_model="gpt-4o-mini",
        gemini_provider="vertex",
        gemini_api_fallback_enabled=False,
        gemini_api_key="",
        gemini_topic_model="legacy-model",
        gemini_image_model="legacy-image",
        gemini_deep_research_agent="deep-research-preview-04-2026",
        gemini_topic_timeout_seconds=600,
        gemini_topic_poll_seconds=5,
        vertex_ai_enabled=True,
        vertex_ai_project_id="test-project",
        vertex_ai_location="us-central1",
        vertex_gemini_model="gemini-2.5-flash",
        vertex_gemini_image_model="gemini-3.1-flash-image-preview",
        vertex_grounded_research_model="gemini-2.5-pro",
        vertex_grounded_research_location="global",
        gemini_deep_research_provider="vertex_grounded",
    )
    monkeypatch.setattr(llm_client_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client_module, "get_vertex_gemini_client", lambda: FakeVertex())
    client = llm_client_module.LLMClient()

    assert client.generate_gemini_text("hello") == "vertex text"
    assert client.generate_gemini_json("json", {"type": "object"}) == {"source": "vertex"}
    assert client.generate_gemini_image("cover")["mime_type"] == "image/png"
    assert [name for name, _kwargs in calls] == ["text", "json", "image"]
```

- [ ] **Step 2: Run the failing routing test**

Run:

```bash
python3 -m pytest tests/test_vertex_gemini_client.py::test_llm_client_routes_text_json_and_image_to_vertex -q
```

Expected: FAIL because `llm_client.py` does not import or call `get_vertex_gemini_client`.

- [ ] **Step 3: Wire Vertex into `LLMClient`**

In `app/adapters/llm_client.py`, add this import near the other adapter imports:

```python
from app.adapters.vertex_gemini_client import get_vertex_gemini_client
```

In `LLMClient.__init__`, after Gemini setting assignments, add:

```python
        self.gemini_provider = settings.gemini_provider
        self.gemini_api_fallback_enabled = settings.gemini_api_fallback_enabled
        self.gemini_deep_research_provider = settings.gemini_deep_research_provider
```

At the start of `generate_gemini_text`, before `target_model = model or self.default_gemini_model`, insert:

```python
        if self.gemini_provider == "vertex":
            return get_vertex_gemini_client().generate_text(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_budget=thinking_budget,
            )
```

At the start of `generate_gemini_json`, before `target_model = model or self.default_gemini_model`, insert:

```python
        if self.gemini_provider == "vertex":
            return get_vertex_gemini_client().generate_json(
                prompt=prompt,
                json_schema=json_schema,
                system_prompt=system_prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
```

At the start of `generate_gemini_image`, before `target_model = self._resolve_gemini_image_model(model)`, insert:

```python
        if self.gemini_provider == "vertex":
            return get_vertex_gemini_client().generate_image(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
```

- [ ] **Step 4: Run routing test**

Run:

```bash
python3 -m pytest tests/test_vertex_gemini_client.py::test_llm_client_routes_text_json_and_image_to_vertex -q
```

Expected: PASS.

- [ ] **Step 5: Run existing Gemini flow tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py -k "not deep_research" -q
```

Expected: PASS. If a test constructs fake settings without the new fields, add those fields to that test's `SimpleNamespace` exactly as in the routing test.

- [ ] **Step 6: Commit**

```bash
git add app/adapters/llm_client.py tests/test_vertex_gemini_client.py tests/test_topics_gemini_flow.py
git commit -m "feat: route gemini facade through vertex"
```

---

### Task 4: Replace Deep Research default with Vertex grounded research

**Files:**
- Modify: `app/adapters/llm_client.py`
- Modify: `tests/test_topics_gemini_flow.py`

- [ ] **Step 1: Add failing Vertex research routing test**

Append to `tests/test_topics_gemini_flow.py`:

```python
def test_generate_gemini_deep_research_routes_to_vertex_grounded_by_default(monkeypatch):
    calls = []

    class FakeVertex:
        def generate_grounded_research(self, **kwargs):
            calls.append(kwargs)
            return "Vertex grounded report"

    fake_settings = type(
        "Settings",
        (),
        {
            "openai_api_key": "",
            "openai_model": "gpt-4o-mini",
            "gemini_provider": "vertex",
            "gemini_api_fallback_enabled": False,
            "gemini_deep_research_provider": "vertex_grounded",
            "gemini_api_key": "",
            "gemini_topic_model": "gemini-2.5-flash",
            "gemini_image_model": "gemini-3.1-flash-image-preview",
            "gemini_deep_research_agent": "deep-research-preview-04-2026",
            "gemini_topic_timeout_seconds": 600,
            "gemini_topic_poll_seconds": 5,
        },
    )()
    monkeypatch.setattr(llm_client_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(llm_client_module, "get_vertex_gemini_client", lambda: FakeVertex())

    client = llm_client_module.LLMClient()
    result = client.generate_gemini_deep_research(
        "Research current accessibility topics",
        system_prompt="Return German only.",
        progress_callback=lambda _event: None,
    )

    assert result == "Vertex grounded report"
    assert calls[0]["prompt"] == "Research current accessibility topics"
    assert calls[0]["system_prompt"] == "Return German only."
```

- [ ] **Step 2: Run the failing research routing test**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py::test_generate_gemini_deep_research_routes_to_vertex_grounded_by_default -q
```

Expected: FAIL because `generate_gemini_deep_research` still uses `/interactions`.

- [ ] **Step 3: Add Vertex grounded branch**

At the start of `generate_gemini_deep_research` in `app/adapters/llm_client.py`, before `target_agent = agent or self.gemini_deep_research_agent`, insert:

```python
        if self.gemini_deep_research_provider == "vertex_grounded":
            if progress_callback:
                progress_callback(
                    {
                        "provider_status": "SUBMITTED",
                        "detail_message": "Vertex Gemini accepted the grounded research request.",
                        "is_retrying": False,
                        "retry_message": None,
                    }
                )
            result = get_vertex_gemini_client().generate_grounded_research(
                prompt=prompt,
                system_prompt=system_prompt,
                model=None,
                max_tokens=None,
                temperature=1.0,
            )
            if progress_callback:
                progress_callback(
                    {
                        "provider_status": "COMPLETED",
                        "detail_message": "Vertex Gemini returned the grounded research dossier.",
                        "is_retrying": False,
                        "retry_message": None,
                    }
                )
            return result
```

- [ ] **Step 4: Guard legacy Deep Research behind explicit fallback**

Still in `generate_gemini_deep_research`, immediately after the Vertex branch, insert:

```python
        if not self.gemini_api_fallback_enabled:
            raise ThirdPartyError(
                message="Native Gemini Deep Research requires the legacy Gemini API fallback path.",
                details={
                    "provider": "gemini_api",
                    "required_env": "GEMINI_API_FALLBACK_ENABLED=true",
                    "reason": "Google documents Deep Research Interactions for Gemini API, not Vertex AI.",
                },
            )
```

- [ ] **Step 5: Run research tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py -k "deep_research" -q
```

Expected: the new Vertex routing test passes. Existing legacy Interactions tests may fail until their fake settings include `gemini_api_fallback_enabled=True` and `gemini_deep_research_provider="gemini_api"`.

- [ ] **Step 6: Update legacy test settings**

For each existing Deep Research test in `tests/test_topics_gemini_flow.py` that builds `fake_settings`, add these attributes:

```python
        gemini_provider="gemini_api",
        gemini_api_fallback_enabled=True,
        gemini_deep_research_provider="gemini_api",
```

- [ ] **Step 7: Re-run research tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py -k "deep_research" -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/adapters/llm_client.py tests/test_topics_gemini_flow.py
git commit -m "feat: use vertex grounded research by default"
```

---

### Task 5: Environment docs and examples

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/vertex-gemini-provider.md`

- [ ] **Step 1: Update `.env.example`**

Replace the Gemini provider area with:

```bash
# Gemini through Vertex AI by default.
GEMINI_PROVIDER=vertex
GEMINI_DEEP_RESEARCH_PROVIDER=vertex_grounded
GEMINI_API_FALLBACK_ENABLED=false

# Vertex AI Gemini.
VERTEX_AI_ENABLED=true
VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83
VERTEX_AI_LOCATION=us-central1
VERTEX_GEMINI_MODEL=gemini-2.5-flash
VERTEX_GEMINI_IMAGE_MODEL=gemini-3.1-flash-image-preview
VERTEX_GROUNDED_RESEARCH_MODEL=gemini-2.5-pro
VERTEX_GROUNDED_RESEARCH_LOCATION=global

# Local development only when using ADC from a file.
# GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json

# Legacy Gemini API fallback. Keep disabled unless native Deep Research Interactions are required.
# GEMINI_API_FALLBACK_ENABLED=true
# GEMINI_PROVIDER=gemini_api
# GEMINI_DEEP_RESEARCH_PROVIDER=gemini_api
# GEMINI_API_KEY=your-ai-studio-key
```

- [ ] **Step 2: Update README environment section**

In `README.md`, replace the bullet `GEMINI_API_KEY: Gemini API key for topic research and generation` with:

```markdown
- `GEMINI_PROVIDER=vertex`: Default Gemini transport through Vertex AI
- `VERTEX_AI_PROJECT_ID`: Google Cloud project that owns billing and Vertex access
- `VERTEX_AI_LOCATION`: Vertex location for standard Gemini calls, default `us-central1`
- `VERTEX_GROUNDED_RESEARCH_LOCATION`: Vertex location for Google Search grounded research, default `global`
- `GEMINI_API_KEY`: Legacy fallback only when `GEMINI_API_FALLBACK_ENABLED=true`
```

- [ ] **Step 3: Create provider documentation**

Create `docs/vertex-gemini-provider.md`:

````markdown
# Vertex Gemini Provider

This app uses Vertex AI as the default Gemini transport.

## Default production mode

Use:

```bash
GEMINI_PROVIDER=vertex
GEMINI_DEEP_RESEARCH_PROVIDER=vertex_grounded
GEMINI_API_FALLBACK_ENABLED=false
VERTEX_AI_ENABLED=true
VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83
VERTEX_AI_LOCATION=us-central1
VERTEX_GROUNDED_RESEARCH_LOCATION=global
```

This routes text generation, structured JSON, image generation, and topic research through Google Cloud IAM and Vertex billing.

## Deep Research status

Google's official Deep Research documentation currently describes native Deep Research as Gemini API Interactions only. It also states that Deep Research cannot be accessed through `generate_content`. Until Google publishes a Vertex Interactions endpoint, this app uses Vertex Gemini with Google Search grounding for research workloads.

## Legacy fallback

Only enable this when the exact Gemini Deep Research Interactions agent is required:

```bash
GEMINI_API_FALLBACK_ENABLED=true
GEMINI_PROVIDER=gemini_api
GEMINI_DEEP_RESEARCH_PROVIDER=gemini_api
GEMINI_API_KEY=...
```

Do not enable the fallback in production if the goal is to consume the $1,000 Vertex credit balance.

## Verification

Run:

```bash
python3 -m pytest tests/test_vertex_ai_config.py tests/test_vertex_gemini_client.py tests/test_topics_gemini_flow.py -k "gemini or vertex or deep_research" -q
```

For a live smoke test, run a small topic generation request and confirm startup logs show:

```text
gemini_provider=vertex
gemini_deep_research_provider=vertex_grounded
gemini_api_fallback_enabled=false
```
````

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md docs/vertex-gemini-provider.md
git commit -m "docs: document vertex gemini provider"
```

---

### Task 6: Final regression and live readiness checks

**Files:**
- No code changes unless a test exposes a local issue.

- [ ] **Step 1: Run focused provider tests**

Run:

```bash
python3 -m pytest tests/test_vertex_ai_config.py tests/test_vertex_gemini_client.py tests/test_topics_gemini_flow.py -k "gemini or vertex or deep_research" -q
```

Expected: PASS.

- [ ] **Step 2: Run topic and blog LLM regression tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py tests/test_blog_feature.py tests/test_audit_agent.py tests/test_audit_json_retry.py -q
```

Expected: PASS.

- [ ] **Step 3: Run import smoke**

Run:

```bash
python3 - <<'PY'
from app.adapters.llm_client import LLMClient
from app.adapters.vertex_gemini_client import VertexGeminiClient
from app.core.config import Settings
print("imports-ok")
PY
```

Expected output:

```text
imports-ok
```

- [ ] **Step 4: Optional live Vertex smoke test**

Only run after ADC or production workload identity is configured:

```bash
APP_ENV_FILE=.env python3 - <<'PY'
from app.adapters.vertex_gemini_client import get_vertex_gemini_client
client = get_vertex_gemini_client()
print(client.generate_text(prompt="Return exactly: vertex-ok", max_tokens=8, temperature=0))
PY
```

Expected: output contains `vertex-ok`.

- [ ] **Step 5: Commit any test-only repairs**

If Step 1 through Step 3 required small compatibility repairs, commit them:

```bash
git add app/adapters app/core tests README.md docs .env.example
git commit -m "test: verify vertex gemini provider migration"
```

If no repairs were needed, skip the commit.

---

## Deployment Notes

- Production should set `GEMINI_API_FALLBACK_ENABLED=false` to ensure spend goes through Vertex credits.
- Production must set `VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83`, `VERTEX_AI_LOCATION=us-central1`, and `VERTEX_GROUNDED_RESEARCH_LOCATION=global`.
- Local development can use `gcloud auth application-default login` or `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json`.
- Do not add service account JSON files to the repo.
- If Google later publishes a Vertex Interactions endpoint for native Deep Research, add it as a third provider value, `GEMINI_DEEP_RESEARCH_PROVIDER=vertex_interactions`, and keep the current `vertex_grounded` path as a cheaper fallback.

## Self-Review

- Spec coverage: Text completions, JSON structured output, image generation, research replacement, legacy fallback, docs, config, tests, and live smoke checks are covered.
- Deep Research limitation: The plan does not claim native Vertex Deep Research exists because current official docs do not document it. It implements the Vertex-native replacement available today: Gemini with Google Search grounding.
- Placeholder scan: No unresolved-marker wording remains.
- Type consistency: Provider values are consistently `vertex`, `gemini_api`, `vertex_grounded`; model fields are consistently `vertex_gemini_model`, `vertex_gemini_image_model`, `vertex_grounded_research_model`, and `vertex_grounded_research_location`.
