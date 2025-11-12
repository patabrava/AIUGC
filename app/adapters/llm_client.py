"""
FLOW-FORGE LLM Client Adapter
Wrapper for OpenAI and Anthropic clients.
Per Constitution § VI: Adapterize Specialists
"""

from typing import Optional, Dict, Any, List
import httpx
import json

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.errors import ThirdPartyError, ValidationError

logger = get_logger(__name__)


class LLMClient:
    """Unified LLM client for OpenAI and Anthropic."""
    
    def __init__(self):
        settings = get_settings()
        self.openai_api_key = settings.openai_api_key
        self.default_openai_model = settings.openai_model
        self.http_client = httpx.Client(
            base_url="https://api.openai.com/v1",
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=None),
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "OpenAI-Beta": "tools=v1",
            },
        )
    
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

            response = self.http_client.post(
                "/responses",
                json=payload,
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
                timeout=str(self.http_client.timeout),
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

            response = self.http_client.post("/chat/completions", json=payload)
            
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


# Singleton instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get LLM client singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
