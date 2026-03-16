"""
FLOW-FORGE Configuration Module
Loads and validates environment variables using Pydantic Settings.
Per Constitution § III: Deterministic Execution
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field, field_validator, model_validator
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
    supabase_key: str = Field(
        ...,
        validation_alias=AliasChoices("SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
        description="Supabase anon key or service role key fallback",
    )
    supabase_service_key: str = Field(
        ...,
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
        description="Supabase service role key",
    )
    
    # LLM Providers
    openai_api_key: str = Field("", description="OpenAI API key (required for Sora video generation)")
    openai_model: str = Field("gpt-4o-mini", description="Default OpenAI model identifier")
    gemini_api_key: str = Field("", description="Gemini API key for topic generation")
    gemini_topic_model: str = Field("gemini-2.5-flash", description="Gemini model for topic generation and repair")
    gemini_deep_research_agent: str = Field(
        "deep-research-pro-preview-12-2025",
        description="Gemini Interactions API agent for Deep Research topic discovery",
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
        description="Polling interval for Gemini Deep Research interactions",
    )
    
    # Video Providers
    google_ai_api_key: str = Field(..., description="Google AI API key for VEO 3.1")
    google_ai_project_id: Optional[str] = Field(None, description="Google Cloud project ID")
    # sora_api_key: str = Field(default="", description="Sora 2 API key")  # Future
    
    # Video Storage
    cloudflare_r2_account_id: str = Field(..., description="Cloudflare account ID for R2")
    cloudflare_r2_access_key_id: str = Field(..., description="Cloudflare R2 access key ID")
    cloudflare_r2_secret_access_key: str = Field(..., description="Cloudflare R2 secret access key")
    cloudflare_r2_bucket_name: str = Field(..., description="Cloudflare R2 bucket name")
    cloudflare_r2_public_base_url: str = Field(..., description="Public base URL for Cloudflare R2 objects")
    cloudflare_r2_region: str = Field("auto", description="Cloudflare R2 region name")
    cloudflare_r2_endpoint_url: Optional[str] = Field(
        default=None,
        description="Optional explicit Cloudflare R2 S3 endpoint URL",
    )
    cloudflare_r2_video_prefix: str = Field(
        default="flow-forge/videos",
        description="Object key prefix for generated videos in Cloudflare R2",
    )
    use_url_based_upload: bool = Field(
        default=False,
        description="Enable source URL ingestion before storing videos in Cloudflare R2"
    )
    
    # Social Media
    tiktok_client_key: str = Field(default="", description="TikTok client key")
    tiktok_client_secret: str = Field(default="", description="TikTok client secret")
    instagram_access_token: str = Field(default="", description="Instagram access token")
    
    # Cron Security
    cron_secret: str = Field(..., description="Secret for cron endpoint authentication")
    
    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, v):
        if not v.startswith("https://"):
            raise ValueError("Supabase URL must start with https://")
        return v.rstrip("/")

    @model_validator(mode="after")
    def validate_supabase_credentials(self) -> "Settings":
        if not self.supabase_key:
            raise ValueError("SUPABASE_KEY, SUPABASE_SERVICE_KEY, or SUPABASE_SERVICE_ROLE_KEY must be set")
        if not self.supabase_service_key:
            raise ValueError("SUPABASE_SERVICE_KEY or SUPABASE_SERVICE_ROLE_KEY must be set")
        return self

    @field_validator("cloudflare_r2_public_base_url")
    @classmethod
    def validate_r2_public_base_url(cls, v):
        if not v.startswith("https://"):
            raise ValueError("Cloudflare R2 public base URL must start with https://")
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
