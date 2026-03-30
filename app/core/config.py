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
    supabase_url: str = Field("", description="Supabase project URL")
    supabase_key: str = Field(
        "",
        validation_alias=AliasChoices("SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
        description="Supabase anon key or service role key",
    )
    supabase_service_key: str = Field(
        "",
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
    google_ai_api_key: str = Field("", description="Google AI API key for VEO 3.1")
    google_ai_project_id: Optional[str] = Field(None, description="Google Cloud project ID")
    deepgram_api_key: str = Field("", description="Deepgram API key for speech-to-text captioning")
    # sora_api_key: str = Field(default="", description="Sora 2 API key")  # Future
    
    # Video Storage
    cloudflare_r2_account_id: str = Field("", description="Cloudflare account ID for R2")
    cloudflare_r2_access_key_id: str = Field("", description="Cloudflare R2 access key ID")
    cloudflare_r2_secret_access_key: str = Field("", description="Cloudflare R2 secret access key")
    cloudflare_r2_bucket_name: str = Field("", description="Cloudflare R2 bucket name")
    cloudflare_r2_public_base_url: str = Field("", description="Public base URL for Cloudflare R2 objects")
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
    video_poller_enable_script_bank_expansion: bool = Field(
        default=True,
        description="Enable the daily topic script-bank expansion inside the video poller worker",
    )
    video_poller_identity: str = Field(
        default="",
        description="Optional stable identity label for the video poller worker instance",
    )
    
    # Social Media
    tiktok_client_key: str = Field(default="", description="TikTok client key")
    tiktok_client_secret: str = Field(default="", description="TikTok client secret")
    tiktok_redirect_uri: str = Field(default="", description="TikTok OAuth callback URL")
    tiktok_environment: Literal["sandbox", "production"] = Field(
        default="sandbox",
        description="TikTok environment for app integrations",
    )
    tiktok_sandbox_account: str = Field(default="", description="Authorized TikTok sandbox account handle")
    instagram_access_token: str = Field(default="", description="Instagram access token")
    # Meta must be provided by the deployment environment; do not fall back to a stale app id/secret.
    meta_app_id: str = Field(default="", description="Meta app ID for Instagram Login")
    meta_app_secret: str = Field(default="", description="Meta app secret")
    meta_redirect_uri: str = Field(default="", description="OAuth callback URL for Meta login")
    app_url: str = Field(default="", description="Public application base URL")
    privacy_policy_url: str = Field(default="", description="Privacy policy URL")
    terms_url: str = Field(default="", description="Terms URL")
    token_encryption_key: str = Field(default="", description="Secret for provider token encryption at rest")

    # Webflow
    webflow_api_token: str = Field("", description="Webflow site-level API token")
    webflow_collection_id: str = Field("", description="Webflow CMS collection ID for blog posts")
    webflow_site_id: str = Field("", description="Webflow site ID for publish triggers")

    # Cron Security
    cron_secret: str = Field("", description="Secret for cron endpoint authentication")

    # Auth
    allowed_email_domain: str = Field("lippelift.de", description="Email domain allowed for login")
    allowed_emails: str = Field("caposk817@gmail.com", description="Comma-separated list of explicitly allowed emails")
    session_cookie_name: str = Field("ff_session", description="Name of the session cookie")
    session_max_age: int = Field(2592000, description="Session cookie max age in seconds (default 30 days)")
    
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
        if self.environment == "production" and not self.app_url:
            raise ValueError("APP_URL must be set in production for host validation")
        return self

    @field_validator("cloudflare_r2_public_base_url")
    @classmethod
    def validate_r2_public_base_url(cls, v):
        if not v.startswith("https://"):
            raise ValueError("Cloudflare R2 public base URL must start with https://")
        return v.rstrip("/")

    @field_validator("app_url", "privacy_policy_url", "terms_url", "tiktok_redirect_uri")
    @classmethod
    def validate_optional_urls(cls, v):
        if not v:
            return v
        if not (v.startswith("https://") or v.startswith("http://localhost")):
            raise ValueError("Integration URLs must start with https:// or use localhost for local callbacks")
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
