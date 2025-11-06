"""
FLOW-FORGE Configuration Module
Loads and validates environment variables using Pydantic Settings.
Per Constitution § III: Deterministic Execution
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator
from typing import Literal, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Application
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    
    # Supabase
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: str = Field(..., description="Supabase anon key")
    supabase_service_key: str = Field(..., description="Supabase service role key")
    
    # LLM Providers
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_model: str = Field("gpt-4.1-mini", description="Default OpenAI model identifier")
    
    # Video Providers
    veo_api_key: str = Field(default="", description="Veo 3.1 API key")
    sora_api_key: str = Field(default="", description="Sora 2 API key")
    
    # Social Media
    tiktok_client_key: str = Field(default="", description="TikTok client key")
    tiktok_client_secret: str = Field(default="", description="TikTok client secret")
    instagram_access_token: str = Field(default="", description="Instagram access token")
    
    # Cron Security
    cron_secret: str = Field(..., description="Secret for cron endpoint authentication")
    
    @validator("supabase_url")
    def validate_supabase_url(cls, v):
        if not v.startswith("https://"):
            raise ValueError("Supabase URL must start with https://")
        return v.rstrip("/")
    
    @property
    def is_production(self) -> bool:
        return self.environment == "production"
    
    @property
    def is_development(self) -> bool:
        return self.environment == "development"


# Singleton instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
