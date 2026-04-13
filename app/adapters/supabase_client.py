"""
Lippe Lift Studio Supabase Adapter
Singleton client for Supabase database and storage.
Per Constitution § VI: Vanilla-First Implementation
"""

from typing import Optional
from supabase import create_client, Client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _looks_like_jwt(value: str) -> bool:
    token = (value or "").strip()
    return token.count(".") == 2 and all(part for part in token.split("."))


def _resolve_supabase_api_key(*, public_key: str, service_key: str) -> str:
    if _looks_like_jwt(service_key):
        return service_key
    if _looks_like_jwt(public_key):
        logger.warning(
            "supabase_service_key_invalid_fallback_to_public_key",
            service_key_present=bool(service_key),
        )
        return public_key
    return service_key or public_key


def _is_invalid_api_key_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid api key" in message or "double check your supabase" in message


def _probe_supabase_client(client: Client) -> None:
    client.table("batches").select("id").limit(1).execute()


class SupabaseAdapter:
    """Singleton adapter for Supabase client."""
    
    _instance: Optional["SupabaseAdapter"] = None
    _client: Optional[Client] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize Supabase client if not already initialized."""
        if self._client is None:
            settings = get_settings()
            candidates = []
            service_key = (settings.supabase_service_key or "").strip()
            public_key = (settings.supabase_key or "").strip()
            primary_key = _resolve_supabase_api_key(public_key=public_key, service_key=service_key)
            for candidate in (primary_key, public_key, service_key):
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

            last_error: Optional[Exception] = None
            for candidate in candidates:
                try:
                    client = create_client(
                        supabase_url=settings.supabase_url,
                        supabase_key=candidate,
                    )
                    _probe_supabase_client(client)
                    self._client = client
                    logger.info(
                        "supabase_client_initialized",
                        url=settings.supabase_url,
                        api_key_source="service" if candidate == service_key else "public",
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if _is_invalid_api_key_error(exc):
                        logger.warning(
                            "supabase_client_candidate_rejected",
                            api_key_source="service" if candidate == service_key else "public",
                            error=str(exc),
                        )
                        continue
                    raise

            if self._client is None and last_error is not None:
                raise last_error
    
    @property
    def client(self) -> Client:
        """Get Supabase client instance."""
        if self._client is None:
            raise RuntimeError("Supabase client not initialized")
        return self._client
    
    def health_check(self) -> bool:
        """
        Check if Supabase connection is healthy.
        Returns True if connection is working, False otherwise.
        """
        try:
            # Simple query to verify connection
            response = self.client.table("batches").select("id").limit(1).execute()
            return True
        except Exception as e:
            logger.error(
                "supabase_health_check_failed",
                error=str(e)
            )
            return False


def get_supabase() -> SupabaseAdapter:
    """Get Supabase adapter singleton."""
    return SupabaseAdapter()
