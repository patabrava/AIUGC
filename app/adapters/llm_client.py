"""
FLOW-FORGE LLM Client Adapter
Wrapper for OpenAI and Anthropic clients.
Per Constitution § VI: Adapterize Specialists
"""

from typing import Optional, Dict, Any, List
from openai import OpenAI
from anthropic import Anthropic
import json

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.errors import ThirdPartyError, ValidationError

logger = get_logger(__name__)


class LLMClient:
    """Unified LLM client for OpenAI and Anthropic."""
    
    def __init__(self):
        settings = get_settings()
        self.openai_client = OpenAI(api_key=settings.openai_api_key)
        self.anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    
    def generate_openai(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: str = "gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 1000,
        tools: Optional[List[Dict[str, Any]]] = None,
        store: bool = False
    ) -> str:
        """Generate text using OpenAI Responses API."""
        try:
            response = self.openai_client.responses.create(
                model=model,
                input=prompt,
                instructions=system_prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
                tools=tools,
                store=store
            )

            content = response.output_text or ""

            logger.info(
                "openai_generation_success",
                model=model,
                prompt_length=len(prompt),
                response_length=len(content)
            )

            return content

        except Exception as e:
            logger.error(
                "openai_generation_failed",
                error=str(e),
                model=model
            )
            raise ThirdPartyError(
                message="OpenAI API call failed",
                details={"error": str(e), "model": model}
            )
    
    def generate_anthropic(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: str = "claude-3-sonnet-20240229",
        temperature: float = 0.7,
        max_tokens: int = 1000
    ) -> str:
        """
        Generate text using Anthropic Claude.
        Per Constitution § XII: Validate LLM Outputs
        """
        try:
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            if system_prompt:
                kwargs["system"] = system_prompt
            
            response = self.anthropic_client.messages.create(**kwargs)
            
            content = response.content[0].text
            
            logger.info(
                "anthropic_generation_success",
                model=model,
                prompt_length=len(prompt),
                response_length=len(content)
            )
            
            return content
        
        except Exception as e:
            logger.error(
                "anthropic_generation_failed",
                error=str(e),
                model=model
            )
            raise ThirdPartyError(
                message="Anthropic API call failed",
                details={"error": str(e), "model": model}
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
                if provider == "openai":
                    model = model or "gpt-4"
                    response = self.generate_openai(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        model=model,
                        max_tokens=2000,
                        tools=tools,
                        store=False
                    )
                else:
                    model = model or "claude-3-sonnet-20240229"
                    response = self.generate_anthropic(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        model=model
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


# Singleton instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get LLM client singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
