"""
FLOW-FORGE LLM Client Adapter
Wrapper for OpenAI and Gemini clients.
Per Constitution § VI: Adapterize Specialists
"""

from typing import Optional, Dict, Any, List, Iterator, Tuple
import httpx
import json
import time
from copy import deepcopy

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.errors import ThirdPartyError, ValidationError

logger = get_logger(__name__)


class LLMClient:
    """Unified LLM client for OpenAI and Gemini."""
    
    def __init__(self):
        settings = get_settings()
        self.openai_api_key = settings.openai_api_key
        self.default_openai_model = settings.openai_model
        self.gemini_api_key = settings.gemini_api_key
        self.default_gemini_model = settings.gemini_topic_model
        self.gemini_deep_research_agent = settings.gemini_deep_research_agent
        self.gemini_topic_timeout_seconds = settings.gemini_topic_timeout_seconds
        self.gemini_topic_poll_seconds = settings.gemini_topic_poll_seconds
        self.openai_http_client = httpx.Client(
            base_url="https://api.openai.com/v1",
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=None),
            follow_redirects=True,
        )
        self.gemini_http_client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=None),
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def _openai_headers(self) -> Dict[str, str]:
        if not self.openai_api_key:
            raise ThirdPartyError(
                message="OpenAI API key not configured",
                details={"provider": "openai"},
            )
        return {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "OpenAI-Beta": "tools=v1",
        }

    def _gemini_params(self) -> Dict[str, str]:
        if not self.gemini_api_key:
            raise ThirdPartyError(
                message="Gemini API key not configured",
                details={"provider": "gemini"},
            )
        return {"key": self.gemini_api_key}

    def _gemini_stream_params(self, **extra: Any) -> Dict[str, str]:
        params = self._gemini_params()
        params["alt"] = "sse"
        for key, value in extra.items():
            if value is None:
                continue
            if isinstance(value, bool):
                params[key] = "true" if value else "false"
            else:
                params[key] = str(value)
        return params
    
    def generate_openai(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        store: bool = False,
        text_format: Optional[Dict[str, Any]] = None,
        input_override: Optional[List[Dict[str, Any]]] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        tool_choice: Optional[Any] = None,
        include: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate text using OpenAI Responses API."""
        try:
            target_model = model or self.default_openai_model

            payload: Dict[str, Any] = {
                "model": target_model,
                "store": store,
            }

            if system_prompt:
                payload["instructions"] = system_prompt
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_output_tokens"] = max_tokens
            if tools:
                payload["tools"] = tools
            if text_format is not None:
                if tools and any(tool.get("type") == "web_search" for tool in tools):
                    logger.warning(
                        "openai_text_format_unsupported_with_web_search",
                        requested_format=text_format,
                    )
                    text_format = None
                if text_format is not None:
                    payload.setdefault("text", {})["format"] = text_format
            if reasoning is not None:
                payload["reasoning"] = reasoning
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if include is not None:
                payload["include"] = include
            if metadata is not None:
                payload["metadata"] = metadata

            # Responses API expects input as message array
            # Note: system_prompt goes in 'instructions' field, NOT as a system role message
            if input_override is not None:
                payload["input"] = input_override
            else:
                # Always use user role for the prompt content
                payload["input"] = [{"role": "user", "content": prompt}]

            logger.debug(
                "openai_request",
                model=target_model,
                instructions_present=bool(system_prompt),
                tools_configured=bool(tools),
                prompt_length=len(prompt),
                prompt_preview=prompt[:400],
                metadata=metadata or {},
            )

            response = self.openai_http_client.post(
                "/responses",
                json=payload,
                headers=self._openai_headers(),
            )

            if response.status_code >= 400:
                logger.error(
                    "openai_generation_http_error",
                    status_code=response.status_code,
                    response_text=response.text,
                )
                raise ThirdPartyError(
                    message="OpenAI API call failed",
                    details={
                        "status_code": response.status_code,
                        "body": response.text,
                        "model": target_model,
                    }
                )

            data = response.json()
            content = self._extract_output_text(data)

            logger.info(
                "openai_generation_success",
                model=target_model,
                prompt_length=len(prompt),
                response_length=len(content)
            )
            logger.debug(
                "openai_response",
                model=target_model,
                response_preview=content[:400],
                response_full_length=len(content),
                raw_response=data,
            )

            return content

        except ThirdPartyError:
            raise
        except httpx.TimeoutException as exc:
            logger.error(
                "openai_generation_timeout",
                model=target_model,
                timeout=str(self.openai_http_client.timeout),
                error=str(exc)
            )
            raise ThirdPartyError(
                message="OpenAI API call timed out",
                details={"error": str(exc), "model": target_model}
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "openai_generation_http_exception",
                model=target_model,
                error=str(exc)
            )
            raise ThirdPartyError(
                message="OpenAI API transport failed",
                details={"error": str(exc), "model": target_model}
            ) from exc
        except Exception as e:
            logger.error(
                "openai_generation_failed",
                error=str(e),
                model=target_model
            )
            raise ThirdPartyError(
                message="OpenAI API call failed",
                details={"error": str(e), "model": target_model}
            )
    
    def generate_structured(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        include: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate structured JSON output using OpenAI's json_schema format.
        This works with web_search tools (unlike json_object format).
        Per Constitution § XII: Schema-first validation.
        
        Args:
            json_schema: JSON schema dict with "name", "strict", and "schema" keys
        
        Returns:
            Parsed JSON dict matching the schema
        """
        try:
            target_model = model or self.default_openai_model

            payload: Dict[str, Any] = {
                "model": target_model,
                "store": False,
            }

            if system_prompt:
                payload["instructions"] = system_prompt
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_output_tokens"] = max_tokens
            if tools:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if include is not None:
                payload["include"] = include
            if metadata is not None:
                payload["metadata"] = metadata

            # Use json_schema format (works with web_search!)
            # Unpack the json_schema dict directly into format (per OpenAI docs)
            payload.setdefault("text", {})["format"] = {
                "type": "json_schema",
                "name": json_schema["name"],
                "schema": json_schema["schema"],
                "strict": json_schema.get("strict", True),
            }

            payload["input"] = [{"role": "user", "content": prompt}]

            logger.debug(
                "openai_structured_request",
                model=target_model,
                schema_name=json_schema.get("name"),
                tools_configured=bool(tools),
                prompt_length=len(prompt),
            )

            response = self.openai_http_client.post("/responses", json=payload, headers=self._openai_headers())

            if response.status_code >= 400:
                logger.error(
                    "openai_structured_http_error",
                    status_code=response.status_code,
                    response_text=response.text,
                )
                raise ThirdPartyError(
                    message="OpenAI Structured Outputs API call failed",
                    details={
                        "status_code": response.status_code,
                        "body": response.text,
                        "model": target_model,
                    }
                )

            data = response.json()
            content = self._extract_output_text(data)

            # Parse the JSON response (guaranteed valid by schema)
            parsed = json.loads(content)

            logger.info(
                "openai_structured_success",
                model=target_model,
                schema_name=json_schema.get("name"),
                prompt_length=len(prompt),
                response_length=len(content)
            )

            return parsed

        except ThirdPartyError:
            raise
        except json.JSONDecodeError as exc:
            # This should never happen with structured outputs, but log if it does
            logger.error(
                "openai_structured_json_parse_failed",
                error=str(exc),
                content_preview=content[:500] if 'content' in locals() else None
            )
            raise ValidationError(
                message="Structured output produced invalid JSON",
                details={"error": str(exc)}
            ) from exc
        except Exception as e:
            logger.error(
                "openai_structured_failed",
                error=str(e),
                model=target_model
            )
            raise ThirdPartyError(
                message="OpenAI Structured Outputs API call failed",
                details={"error": str(e), "model": target_model}
            )
    
    def generate_chat(self, prompt: str, system_prompt: Optional[str] = None, max_tokens: Optional[int] = None) -> str:
        """Generate text using OpenAI Chat Completions API (for non-web-search requests)."""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.default_openai_model,
                "messages": messages,
            }

            if max_tokens:
                payload["max_tokens"] = max_tokens

            logger.debug(
                "openai_chat_request",
                model=self.default_openai_model,
                system_present=bool(system_prompt),
                prompt_length=len(prompt),
                prompt_preview=prompt[:400],
            )

            response = self.openai_http_client.post("/chat/completions", json=payload, headers=self._openai_headers())
            
            if response.status_code >= 400:
                logger.error(
                    "openai_chat_http_error",
                    status_code=response.status_code,
                    response_text=response.text,
                )
                raise ThirdPartyError(
                    message="OpenAI Chat API call failed",
                    details={
                        "status_code": response.status_code,
                        "body": response.text,
                    }
                )
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]

            logger.info(
                "openai_chat_success",
                model=self.default_openai_model,
                prompt_length=len(prompt),
                response_length=len(content)
            )
            logger.debug(
                "openai_chat_response",
                model=self.default_openai_model,
                response_preview=content[:400],
                response_full_length=len(content),
                raw_response=data,
            )

            return content
            
        except ThirdPartyError:
            raise
        except Exception as e:
            logger.error("openai_chat_failed", error=str(e))
            raise ThirdPartyError(
                message="OpenAI Chat API call failed",
                details={"error": str(e)}
            )
    
    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        provider: str = "openai",
        model: Optional[str] = None,
        max_retries: int = 3,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate JSON output with validation and retries.
        Per Constitution § XII: Retry with feedback
        """
        for attempt in range(max_retries):
            try:
                model = model or self.default_openai_model
                response = self.generate_openai(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    max_tokens=2000,
                    tools=tools,
                    store=False
                )
                
                # Parse JSON
                parsed = json.loads(response)
                
                logger.info(
                    "json_generation_success",
                    provider=provider,
                    attempt=attempt + 1
                )
                
                return parsed
            
            except json.JSONDecodeError as e:
                logger.warning(
                    "json_parse_failed",
                    attempt=attempt + 1,
                    error=str(e),
                    response=response[:200] if 'response' in locals() else None
                )
                
                if attempt == max_retries - 1:
                    raise ValidationError(
                        message="Failed to generate valid JSON after retries",
                        details={"attempts": max_retries, "error": str(e)}
                    )
                
                # Add feedback to prompt for retry
                prompt += f"\n\nPrevious attempt failed with error: {str(e)}. Please ensure the response is valid JSON."
            
            except Exception as e:
                logger.error(
                    "json_generation_error",
                    attempt=attempt + 1,
                    error=str(e)
                )
                
                if attempt == max_retries - 1:
                    raise
        
        raise ValidationError(
            message="Failed to generate JSON",
            details={"max_retries": max_retries}
        )

    def generate_gemini_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Generate plain text using Gemini generateContent."""
        target_model = model or self.default_gemini_model
        full_prompt = self._merge_prompts(system_prompt, prompt)
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": full_prompt}],
                }
            ]
        }
        if max_tokens is not None or temperature is not None:
            payload["generationConfig"] = {}
            if max_tokens is not None:
                payload["generationConfig"]["maxOutputTokens"] = max_tokens
            if temperature is not None:
                payload["generationConfig"]["temperature"] = temperature

        logger.debug(
            "gemini_generate_text_request",
            model=target_model,
            prompt_length=len(full_prompt),
            prompt_preview=full_prompt[:400],
        )

        try:
            response = self.gemini_http_client.post(
                f"/models/{target_model}:generateContent",
                params=self._gemini_params(),
                json=payload,
            )
            if response.status_code >= 400:
                logger.error(
                    "gemini_generate_text_http_error",
                    status_code=response.status_code,
                    response_text=response.text,
                    model=target_model,
                )
                raise ThirdPartyError(
                    message="Gemini generateContent failed",
                    details={"status_code": response.status_code, "body": response.text, "model": target_model},
                )

            data = response.json()
            content = self._extract_gemini_candidate_text(data)
            logger.info(
                "gemini_generate_text_success",
                model=target_model,
                response_length=len(content),
            )
            return content
        except ThirdPartyError:
            raise
        except Exception as exc:
            logger.error("gemini_generate_text_failed", model=target_model, error=str(exc))
            raise ThirdPartyError(
                message="Gemini generateContent failed",
                details={"error": str(exc), "model": target_model},
            ) from exc

    def generate_gemini_json(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Generate structured JSON using Gemini responseSchema."""
        target_model = model or self.default_gemini_model
        full_prompt = self._merge_prompts(system_prompt, prompt)
        schema_payload = self._to_gemini_response_schema(json_schema.get("schema", json_schema))
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": full_prompt}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema_payload,
            },
        }
        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if temperature is not None:
            payload["generationConfig"]["temperature"] = temperature

        logger.debug(
            "gemini_generate_json_request",
            model=target_model,
            prompt_length=len(full_prompt),
            schema_keys=list(schema_payload.keys()) if isinstance(schema_payload, dict) else None,
        )

        try:
            response = self.gemini_http_client.post(
                f"/models/{target_model}:generateContent",
                params=self._gemini_params(),
                json=payload,
            )
            if response.status_code >= 400:
                logger.error(
                    "gemini_generate_json_http_error",
                    status_code=response.status_code,
                    response_text=response.text,
                    model=target_model,
                )
                raise ThirdPartyError(
                    message="Gemini structured generation failed",
                    details={"status_code": response.status_code, "body": response.text, "model": target_model},
                )

            data = response.json()
            content = self._extract_gemini_candidate_text(data)
            parsed = json.loads(content)
            logger.info(
                "gemini_generate_json_success",
                model=target_model,
                response_length=len(content),
            )
            return parsed
        except ThirdPartyError:
            raise
        except json.JSONDecodeError as exc:
            logger.error("gemini_generate_json_parse_failed", error=str(exc), model=target_model)
            raise ValidationError(
                message="Gemini structured output produced invalid JSON",
                details={"error": str(exc), "model": target_model},
            ) from exc
        except Exception as exc:
            logger.error("gemini_generate_json_failed", model=target_model, error=str(exc))
            raise ThirdPartyError(
                message="Gemini structured generation failed",
                details={"error": str(exc), "model": target_model},
            ) from exc

    def generate_gemini_deep_research(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        agent: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Any] = None,
    ) -> str:
        """Run Gemini Deep Research via the Interactions API and return final text."""
        target_agent = agent or self.gemini_deep_research_agent
        effective_timeout = timeout_seconds or self.gemini_topic_timeout_seconds
        effective_poll = poll_interval_seconds or self.gemini_topic_poll_seconds
        full_prompt = self._merge_prompts(system_prompt, prompt)
        payload: Dict[str, Any] = {
            "input": full_prompt,
            "agent": target_agent,
            "background": True,
            "store": True,
        }

        logger.info(
            "gemini_deep_research_submit",
            agent=target_agent,
            timeout_seconds=effective_timeout,
            poll_interval_seconds=effective_poll,
            metadata_present=bool(metadata),
        )

        try:
            if progress_callback and hasattr(self.gemini_http_client, "stream"):
                streamed = self._generate_gemini_deep_research_streamed(
                    payload=payload,
                    agent=target_agent,
                    timeout_seconds=effective_timeout,
                    poll_interval_seconds=effective_poll,
                    progress_callback=progress_callback,
                )
                if streamed is not None:
                    return streamed

            return self._generate_gemini_deep_research_polled(
                payload=payload,
                agent=target_agent,
                timeout_seconds=effective_timeout,
                poll_interval_seconds=effective_poll,
                progress_callback=progress_callback,
            )
        except ThirdPartyError:
            raise
        except Exception as exc:
            logger.error("gemini_deep_research_failed", agent=target_agent, error=str(exc))
            raise ThirdPartyError(
                message="Gemini Deep Research failed",
                details={"error": str(exc), "agent": target_agent},
            ) from exc

    def _generate_gemini_deep_research_polled(
        self,
        *,
        payload: Dict[str, Any],
        agent: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
        progress_callback: Optional[Any],
    ) -> str:
        try:
            submit_response = self.gemini_http_client.post(
                "/interactions",
                params=self._gemini_params(),
                json=payload,
            )
            if submit_response.status_code >= 400:
                logger.error(
                    "gemini_deep_research_submit_http_error",
                    status_code=submit_response.status_code,
                    response_text=submit_response.text,
                    agent=target_agent,
                )
                raise ThirdPartyError(
                    message="Gemini Deep Research submission failed",
                    details={
                        "status_code": submit_response.status_code,
                        "body": submit_response.text,
                        "agent": agent,
                    },
                )

            interaction = submit_response.json()
            interaction_id = interaction.get("id") or interaction.get("name")
            if not interaction_id:
                raise ThirdPartyError(
                    message="Gemini Deep Research did not return an interaction id",
                    details={"agent": agent, "response": interaction},
                )
            logger.info(
                "gemini_deep_research_submitted",
                interaction_id=interaction_id,
                agent=agent,
            )
            if progress_callback:
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_status": "SUBMITTED",
                        "detail_message": "Gemini accepted the research task and is preparing the first planning step.",
                    }
                )

            deadline = time.monotonic() + timeout_seconds
            started_at = time.monotonic()
            last_status: Optional[str] = None
            consecutive_retryable_poll_errors = 0
            max_retryable_poll_errors = 5
            while time.monotonic() < deadline:
                poll_response = self.gemini_http_client.get(
                    f"/{interaction_id}" if str(interaction_id).startswith("interactions/") else f"/interactions/{interaction_id}",
                    params=self._gemini_params(),
                )
                if poll_response.status_code >= 400:
                    if poll_response.status_code in {429, 500, 502, 503, 504}:
                        consecutive_retryable_poll_errors += 1
                        logger.warning(
                            "gemini_deep_research_poll_retryable_http_error",
                            status_code=poll_response.status_code,
                            response_text=poll_response.text,
                            interaction_id=interaction_id,
                            consecutive_errors=consecutive_retryable_poll_errors,
                            max_retryable_errors=max_retryable_poll_errors,
                        )
                        if consecutive_retryable_poll_errors >= max_retryable_poll_errors:
                            raise ThirdPartyError(
                                message="Gemini Deep Research polling failed",
                                details={
                                    "status_code": poll_response.status_code,
                                    "body": poll_response.text,
                                    "interaction_id": interaction_id,
                                    "consecutive_errors": consecutive_retryable_poll_errors,
                                },
                            )
                        if progress_callback:
                            progress_callback(
                                {
                                    "provider_interaction_id": interaction_id,
                                    "provider_status": f"HTTP_{poll_response.status_code}",
                                    "detail_message": (
                                        f"Gemini returned a temporary {poll_response.status_code} response. "
                                        f"Retry {consecutive_retryable_poll_errors} is running automatically."
                                    ),
                                    "retry_message": (
                                        f"Gemini {poll_response.status_code} while polling. Waiting before reconnect."
                                    ),
                                    "is_retrying": True,
                                }
                            )
                        backoff_seconds = min(max(poll_interval_seconds, 1) * consecutive_retryable_poll_errors, 15)
                        time.sleep(backoff_seconds)
                        continue

                    logger.error(
                        "gemini_deep_research_poll_http_error",
                        status_code=poll_response.status_code,
                        response_text=poll_response.text,
                        interaction_id=interaction_id,
                    )
                    raise ThirdPartyError(
                        message="Gemini Deep Research polling failed",
                        details={
                            "status_code": poll_response.status_code,
                            "body": poll_response.text,
                            "interaction_id": interaction_id,
                        },
                    )

                poll_data = poll_response.json()
                consecutive_retryable_poll_errors = 0
                status = (poll_data.get("state") or poll_data.get("status") or "").upper()
                elapsed_seconds = round(time.monotonic() - started_at, 1)
                if status != last_status:
                    logger.info(
                        "gemini_deep_research_status",
                        interaction_id=interaction_id,
                        status=status or "UNKNOWN",
                        elapsed_seconds=elapsed_seconds,
                        done=bool(poll_data.get("done")),
                    )
                    last_status = status
                if progress_callback:
                    status_label = status or "RUNNING"
                    progress_callback(
                        {
                            "provider_interaction_id": interaction_id,
                            "provider_status": status_label,
                            "detail_message": (
                                f"Gemini research is {status_label.lower()} after {elapsed_seconds}s. "
                                "Planning, searching, reading, and drafting are still in progress."
                            ),
                            "retry_message": None,
                            "is_retrying": False,
                        }
                    )
                if status in {"DONE", "COMPLETED", "SUCCEEDED"}:
                    content = self._extract_gemini_interaction_text(poll_data)
                    logger.info(
                        "gemini_deep_research_success",
                        interaction_id=interaction_id,
                        status=status,
                        elapsed_seconds=elapsed_seconds,
                        content_characters=len(content),
                    )
                    if progress_callback:
                        progress_callback(
                            {
                                "provider_interaction_id": interaction_id,
                                "provider_status": status,
                                "detail_message": (
                                    f"Gemini finished the research interaction after {elapsed_seconds}s and returned the final draft."
                                ),
                                "retry_message": None,
                                "is_retrying": False,
                            }
                        )
                    return content
                if status in {"FAILED", "CANCELLED", "ERROR"}:
                    if progress_callback:
                        progress_callback(
                            {
                                "provider_interaction_id": interaction_id,
                                "provider_status": status,
                                "detail_message": (
                                    f"Gemini ended the research interaction with status {status.lower()}."
                                ),
                                "retry_message": None,
                                "is_retrying": False,
                            }
                        )
                    raise ThirdPartyError(
                        message="Gemini Deep Research failed",
                        details={"interaction_id": interaction_id, "status": status, "response": poll_data},
                    )

                time.sleep(poll_interval_seconds)

            raise ThirdPartyError(
                message="Gemini Deep Research timed out",
                details={"interaction_id": interaction_id, "timeout_seconds": timeout_seconds},
            )
        except ThirdPartyError:
            raise
        except Exception as exc:
            logger.error("gemini_deep_research_polling_failed", agent=agent, error=str(exc))
            raise

    def _generate_gemini_deep_research_streamed(
        self,
        *,
        payload: Dict[str, Any],
        agent: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
        progress_callback: Any,
    ) -> Optional[str]:
        deadline = time.monotonic() + timeout_seconds
        started_at = time.monotonic()
        interaction_id: Optional[str] = None
        last_event_id: Optional[str] = None
        last_summary: Optional[str] = None
        last_status_bucket: Optional[int] = None
        final_text_parts: List[str] = []
        is_complete = False
        resume_attempt = 0
        idle_stream_timeout_seconds = max(8, min(max(poll_interval_seconds, 1) * 2, 20))
        max_stream_resume_failures = 3

        def handle_stream_event(event: Dict[str, Any]) -> None:
            nonlocal interaction_id, last_event_id, last_summary, last_status_bucket, is_complete

            event_type = str(event.get("event_type") or "").strip()
            event_id = event.get("event_id")
            if event_id:
                last_event_id = str(event_id)

            interaction = event.get("interaction") or {}
            interaction_id = str(
                interaction.get("id")
                or event.get("interaction_id")
                or interaction_id
                or ""
            ) or interaction_id
            provider_status = str(
                interaction.get("status")
                or event.get("status")
                or event.get("state")
                or ("COMPLETED" if event_type == "interaction.complete" else "RUNNING")
            ).upper()
            elapsed_seconds = round(time.monotonic() - started_at, 1)

            if event_type == "interaction.start":
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_event_id": last_event_id,
                        "provider_status": "SUBMITTED",
                        "detail_message": "Gemini opened the Deep Research session and started planning the work.",
                        "is_retrying": False,
                        "retry_message": None,
                    }
                )
                return

            if event_type == "interaction.status_update":
                bucket = int(elapsed_seconds // 20)
                if bucket != last_status_bucket:
                    last_status_bucket = bucket
                    status_text = provider_status.lower().replace("_", " ")
                    progress_callback(
                        {
                            "provider_interaction_id": interaction_id,
                            "provider_event_id": last_event_id,
                            "provider_status": provider_status or "IN_PROGRESS",
                            "detail_message": (
                                f"Gemini still reports the research interaction as {status_text} after {int(elapsed_seconds)}s."
                            ),
                            "is_retrying": False,
                            "retry_message": None,
                        }
                    )
                return

            if event_type == "content.delta":
                delta = event.get("delta") or {}
                delta_type = delta.get("type")
                if delta_type == "thought_summary":
                    text = ((delta.get("content") or {}).get("text") or "").strip()
                    if text and text != last_summary:
                        last_summary = text
                        progress_callback(
                            {
                                "provider_interaction_id": interaction_id,
                                "provider_event_id": last_event_id,
                                "provider_status": provider_status or "RUNNING",
                                "detail_message": text,
                                "is_retrying": False,
                                "retry_message": None,
                            }
                        )
                    return
                if delta_type == "text":
                    text = (delta.get("text") or "").strip()
                    if text:
                        final_text_parts.append(text)
                    return

            if event_type == "interaction.complete":
                is_complete = True
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_event_id": last_event_id,
                        "provider_status": "COMPLETED",
                        "detail_message": f"Gemini finished the research interaction after {elapsed_seconds}s.",
                        "is_retrying": False,
                        "retry_message": None,
                    }
                )
                return

            if event_type in {"interaction.failed", "error"}:
                is_complete = True
                raise ThirdPartyError(
                    message="Gemini Deep Research failed",
                    details={
                        "interaction_id": interaction_id,
                        "event_id": last_event_id,
                        "event_type": event_type,
                        "response": event,
                    },
                )

        try:
            with self.gemini_http_client.stream(
                "POST",
                "/interactions",
                params=self._gemini_stream_params(stream=True),
                json={
                    **payload,
                    "stream": True,
                    "agent_config": {
                        "type": "deep-research",
                        "thinking_summaries": "auto",
                    },
                },
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(connect=15.0, read=idle_stream_timeout_seconds, write=60.0, pool=None),
            ) as response:
                if response.status_code >= 400:
                    logger.warning(
                        "gemini_deep_research_stream_submit_http_error",
                        status_code=response.status_code,
                        response_text=response.text,
                        agent=agent,
                    )
                    return None
                for event in self._iter_gemini_sse_events(response):
                    handle_stream_event(event)
                    if is_complete:
                        break
        except ThirdPartyError:
            raise
        except Exception as exc:
            logger.warning(
                "gemini_deep_research_stream_interrupted",
                agent=agent,
                interaction_id=interaction_id,
                last_event_id=last_event_id,
                error=str(exc),
            )
            if not interaction_id:
                return None
            progress_callback(
                {
                    "provider_interaction_id": interaction_id,
                    "provider_event_id": last_event_id,
                    "provider_status": "IN_PROGRESS",
                    "detail_message": (
                        f"Gemini kept the research run open but paused event delivery after {int(time.monotonic() - started_at)}s. Reopening the stream."
                    ),
                    "is_retrying": False,
                    "retry_message": None,
                }
            )

        while not is_complete and interaction_id and time.monotonic() < deadline:
            resume_attempt += 1
            time.sleep(min(max(poll_interval_seconds, 1), 2))
            try:
                with self.gemini_http_client.stream(
                    "GET",
                    f"/{interaction_id}" if str(interaction_id).startswith("interactions/") else f"/interactions/{interaction_id}",
                    params=self._gemini_stream_params(stream=True, last_event_id=last_event_id),
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(connect=15.0, read=idle_stream_timeout_seconds, write=60.0, pool=None),
                ) as response:
                    if response.status_code >= 400:
                        if response.status_code in {429, 500, 502, 503, 504}:
                            progress_callback(
                                {
                                    "provider_interaction_id": interaction_id,
                                    "provider_event_id": last_event_id,
                                    "provider_status": f"HTTP_{response.status_code}",
                                    "detail_message": (
                                        f"Gemini returned a temporary {response.status_code} while resuming the research stream."
                                    ),
                                    "is_retrying": True,
                                    "retry_message": "Waiting briefly before reopening the Deep Research stream.",
                                }
                            )
                            continue
                        raise ThirdPartyError(
                            message="Gemini Deep Research stream resume failed",
                            details={
                                "status_code": response.status_code,
                                "body": response.text,
                                "interaction_id": interaction_id,
                                "last_event_id": last_event_id,
                            },
                        )
                    for event in self._iter_gemini_sse_events(response):
                        handle_stream_event(event)
                        if is_complete:
                            break
            except ThirdPartyError:
                raise
            except Exception as exc:
                logger.warning(
                    "gemini_deep_research_stream_resume_failed",
                    interaction_id=interaction_id,
                    last_event_id=last_event_id,
                    attempt=resume_attempt,
                    error=str(exc),
                )
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_event_id": last_event_id,
                        "provider_status": "IN_PROGRESS",
                        "detail_message": (
                            f"Gemini kept the research run open but paused event delivery after {int(time.monotonic() - started_at)}s. Reopening the stream."
                        ),
                        "is_retrying": False,
                        "retry_message": None,
                    }
                )
                if resume_attempt >= max_stream_resume_failures:
                    logger.info(
                        "gemini_deep_research_stream_fallback_to_polling",
                        interaction_id=interaction_id,
                        last_event_id=last_event_id,
                        resume_attempt=resume_attempt,
                        message="Exceeded stream resume retries; switching to status polling."
                    )
                    break

        if not is_complete and interaction_id:
            poll_result = self._generate_gemini_deep_research_poll_result(
                interaction_id=interaction_id,
                deadline=deadline,
                started_at=started_at,
                poll_interval_seconds=poll_interval_seconds,
                progress_callback=progress_callback,
            )
            if poll_result:
                return poll_result

        if final_text_parts:
            return "".join(final_text_parts).strip()

        if interaction_id:
            final_snapshot = self.gemini_http_client.get(
                f"/{interaction_id}" if str(interaction_id).startswith("interactions/") else f"/interactions/{interaction_id}",
                params=self._gemini_params(),
            )
            if final_snapshot.status_code < 400:
                return self._extract_gemini_interaction_text(final_snapshot.json())

        return None

    def _generate_gemini_deep_research_poll_result(
        self,
        *,
        interaction_id: str,
        deadline: float,
        started_at: float,
        poll_interval_seconds: int,
        progress_callback: Any,
    ) -> Optional[str]:
        last_status: Optional[str] = None
        consecutive_retryable_poll_errors = 0
        max_retryable_poll_errors = 5

        while time.monotonic() < deadline:
            try:
                poll_response = self.gemini_http_client.get(
                    f"/{interaction_id}" if str(interaction_id).startswith("interactions/") else f"/interactions/{interaction_id}",
                    params=self._gemini_params(),
                )
            except httpx.TransportError as exc:
                consecutive_retryable_poll_errors += 1
                logger.warning(
                    "gemini_deep_research_poll_transport_retry",
                    interaction_id=interaction_id,
                    consecutive_errors=consecutive_retryable_poll_errors,
                    error=str(exc),
                )
                if consecutive_retryable_poll_errors >= max_retryable_poll_errors:
                    raise ThirdPartyError(
                        message="Gemini Deep Research polling failed",
                        details={
                            "interaction_id": interaction_id,
                            "consecutive_errors": consecutive_retryable_poll_errors,
                            "error": str(exc),
                        },
                    ) from exc
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_status": "TRANSPORT_RETRY",
                        "detail_message": "Gemini polling hit a temporary transport failure.",
                        "retry_message": "Retrying the Deep Research poll after a temporary network failure.",
                        "is_retrying": True,
                    }
                )
                backoff_seconds = min(max(poll_interval_seconds, 1) * consecutive_retryable_poll_errors, 15)
                time.sleep(backoff_seconds)
                continue
            if poll_response.status_code >= 400:
                if poll_response.status_code in {429, 500, 502, 503, 504}:
                    consecutive_retryable_poll_errors += 1
                    if consecutive_retryable_poll_errors >= max_retryable_poll_errors:
                        raise ThirdPartyError(
                            message="Gemini Deep Research polling failed",
                            details={
                                "status_code": poll_response.status_code,
                                "body": poll_response.text,
                                "interaction_id": interaction_id,
                                "consecutive_errors": consecutive_retryable_poll_errors,
                            },
                        )
                    progress_callback(
                        {
                            "provider_interaction_id": interaction_id,
                            "provider_status": f"HTTP_{poll_response.status_code}",
                            "detail_message": (
                                f"Gemini returned a temporary {poll_response.status_code} while resuming the research run."
                            ),
                            "retry_message": "Retrying the Deep Research poll after a temporary provider response.",
                            "is_retrying": True,
                        }
                    )
                    backoff_seconds = min(max(poll_interval_seconds, 1) * consecutive_retryable_poll_errors, 15)
                    time.sleep(backoff_seconds)
                    continue
                raise ThirdPartyError(
                    message="Gemini Deep Research polling failed",
                    details={
                        "status_code": poll_response.status_code,
                        "body": poll_response.text,
                        "interaction_id": interaction_id,
                    },
                )

            poll_data = poll_response.json()
            consecutive_retryable_poll_errors = 0
            status = (poll_data.get("state") or poll_data.get("status") or "").upper()
            elapsed_seconds = round(time.monotonic() - started_at, 1)
            if status != last_status:
                logger.info(
                    "gemini_deep_research_resume_status",
                    interaction_id=interaction_id,
                    status=status or "UNKNOWN",
                    elapsed_seconds=elapsed_seconds,
                    done=bool(poll_data.get("done")),
                )
                last_status = status
            if status in {"DONE", "COMPLETED", "SUCCEEDED"}:
                progress_callback(
                    {
                        "provider_interaction_id": interaction_id,
                        "provider_status": status,
                        "detail_message": (
                            f"Gemini finished the research interaction after {elapsed_seconds}s and returned the final draft."
                        ),
                        "retry_message": None,
                        "is_retrying": False,
                    }
                )
                return self._extract_gemini_interaction_text(poll_data)
            if status in {"FAILED", "CANCELLED", "ERROR"}:
                raise ThirdPartyError(
                    message="Gemini Deep Research failed",
                    details={"interaction_id": interaction_id, "status": status, "response": poll_data},
                )
            time.sleep(poll_interval_seconds)

        raise ThirdPartyError(
            message="Gemini Deep Research timed out",
            details={"interaction_id": interaction_id, "timeout_seconds": round(deadline - started_at, 1)},
        )

    def _iter_gemini_sse_events(self, response: Any) -> Iterator[Dict[str, Any]]:
        event_id: Optional[str] = None
        event_type: Optional[str] = None
        data_lines: List[str] = []

        def flush_event() -> Optional[Dict[str, Any]]:
            nonlocal event_id, event_type, data_lines
            if not data_lines:
                event_id = None
                event_type = None
                return None
            payload_text = "\n".join(data_lines).strip()
            data_lines = []
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                logger.warning(
                    "gemini_deep_research_stream_event_parse_failed",
                    event_id=event_id,
                    event_type=event_type,
                    payload_preview=payload_text[:400],
                )
                event_id = None
                event_type = None
                return None
            if event_id and "event_id" not in payload:
                payload["event_id"] = event_id
            if event_type and "event_type" not in payload:
                payload["event_type"] = event_type
            event_id = None
            event_type = None
            return payload

        for raw_line in response.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
            if not line:
                event = flush_event()
                if event is not None:
                    yield event
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value.lstrip(" ")
            if field == "id":
                event_id = value
            elif field == "event":
                event_type = value
            elif field == "data":
                data_lines.append(value)

        event = flush_event()
        if event is not None:
            yield event

    def _extract_output_text(self, data: Dict[str, Any]) -> str:
        """Extract concatenated output text from Responses API payload."""
        if not data:
            return ""

        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()

        collected: List[str] = []

        for item in data.get("output", []) or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                    collected.append(part["text"])

        if not collected and isinstance(data.get("content"), list):
            for part in data.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                    collected.append(part["text"])

        return "".join(collected).strip()

    def _extract_gemini_candidate_text(self, data: Dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        collected: List[str] = []
        for candidate in candidates:
            content = candidate.get("content") or {}
            for part in content.get("parts", []) or []:
                text = part.get("text")
                if text:
                    collected.append(text)
        if collected:
            return "".join(collected).strip()
        raise ThirdPartyError(message="Gemini response missing text output", details={"response": data})

    def _extract_gemini_interaction_text(self, data: Dict[str, Any]) -> str:
        outputs = data.get("outputs") or data.get("output") or []
        if isinstance(outputs, dict):
            outputs = [outputs]

        collected: List[str] = []
        for output in outputs:
            if not isinstance(output, dict):
                continue
            text = output.get("text")
            if text:
                collected.append(text)
            content = output.get("content") or {}
            for part in content.get("parts", []) or []:
                part_text = part.get("text")
                if part_text:
                    collected.append(part_text)

        if collected:
            return "\n".join(collected).strip()
        raise ThirdPartyError(message="Gemini interaction missing text output", details={"response": data})

    def _merge_prompts(self, system_prompt: Optional[str], prompt: str) -> str:
        if system_prompt:
            return f"{system_prompt.strip()}\n\nUSER TASK:\n{prompt.strip()}"
        return prompt.strip()

    def _to_gemini_response_schema(self, schema: Any) -> Any:
        """Remove JSON Schema fields unsupported by Gemini responseSchema."""
        if isinstance(schema, dict):
            cleaned = {}
            for key, value in schema.items():
                if key in {"additionalProperties", "strict", "name", "$schema"}:
                    continue
                cleaned[key] = self._to_gemini_response_schema(value)
            return cleaned
        if isinstance(schema, list):
            return [self._to_gemini_response_schema(item) for item in schema]
        return deepcopy(schema)


# Singleton instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get LLM client singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
