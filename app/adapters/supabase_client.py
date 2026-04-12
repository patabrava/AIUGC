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
            self._client = create_client(
                supabase_url=settings.supabase_url,
                supabase_key=_resolve_supabase_api_key(
                    public_key=settings.supabase_key,
                    service_key=settings.supabase_service_key,
                ),
            )
            logger.info(
                "supabase_client_initialized",
                url=settings.supabase_url
            )
    
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
