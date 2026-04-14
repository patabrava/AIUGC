"""
Lippe Lift Studio Configuration Module
Loads and validates environment variables using Pydantic Settings.
Per Constitution § III: Deterministic Execution
"""

import hashlib
import os
import tempfile
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field, field_validator, model_validator
from typing import Any, Literal, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore"
    )

    def __init__(self, **values: Any):
        values.setdefault("_env_file", os.getenv("APP_ENV_FILE", ".env"))
        super().__init__(**values)
    
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
    gemini_api_key: str = Field(
        "",
        validation_alias=AliasChoices("gemini_api_key", "GEMINI_API_KEY", "GEMINI_API_KEY"),
        description="Canonical Gemini API key for topic generation and VEO requests",
    )
    gemini_topic_model: str = Field("gemini-2.5-flash", description="Gemini model for topic generation and repair")
    gemini_image_model: str = Field(
        "gemini-3.1-flash-image-preview",
        description="Gemini image generation model for blog previews (Nano Banana 2 preview)",
    )
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
    google_ai_project_id: Optional[str] = Field(None, description="Google Cloud project ID")
    google_application_credentials: str = Field(
        "",
        description="Optional explicit path to Google Application Default Credentials JSON",
    )
    google_application_credentials_json: str = Field(
        "",
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS_JSON"),
        description="Optional raw ADC JSON for environments that inject credentials as a secret value",
    )
    vertex_ai_project_id: str = Field("", description="Google Cloud project ID for Vertex AI video generation")
    vertex_ai_location: str = Field("us-central1", description="Vertex AI region for video generation")
    vertex_ai_enabled: bool = Field(default=False, description="Enable the explicit Vertex AI provider path")
    vertex_ai_output_gcs_uri: str = Field(
        "",
        description="Optional GCS URI prefix for Vertex AI video outputs",
    )
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
        default="Lippe Lift Studio/videos",
        description="Object key prefix for generated videos in Cloudflare R2",
    )
    cloudflare_r2_image_prefix: str = Field(
        default="Lippe Lift Studio/images",
        description="Object key prefix for generated images in Cloudflare R2",
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
    veo_daily_generation_limit: int = Field(
        default=10,
        ge=1,
        description="Maximum Veo generations allowed per Pacific day for this project ledger",
    )
    veo_minute_generation_limit: int = Field(
        default=2,
        ge=1,
        description="Maximum Veo generations allowed per UTC minute for this project ledger",
    )
    veo_quota_soft_buffer: int = Field(
        default=0,
        ge=0,
        description="Optional safety buffer subtracted from the configured Veo daily limit",
    )
    veo_enable_efficient_long_route: bool = Field(
        default=True,
        description="Use the 8s-base long-route profile by default for the 16s and 32s Veo tiers; disable to fall back to legacy 4s-base 32s routing",
    )
    veo_quota_freeze_on_unexpected_429: bool = Field(
        default=True,
        description="Freeze further Veo submits until next Pacific reset after an unexpected provider 429",
    )
    veo_disable_local_quota_guard: bool = Field(
        default=False,
        description="Bypass local Veo quota freezes and ledger checks for development multi-key testing",
    )
    veo_disable_all_quota_controls: bool = Field(
        default=False,
        description="Bypass all Veo quota guards, freezes, and reservations for controlled testing",
    )
    veo_use_reference_image: bool = Field(
        default=False,
        validation_alias=AliasChoices("VEO_USE_REFERENCE_IMAGE"),
        description="Attach the global first-frame reference image for Veo and Vertex video submissions",
    )
    veo_quota_project_scope: str = Field(
        default="default-gemini-project",
        description="Operator label for the Google project whose Veo quota is being guarded",
    )

    # Caption reliability
    value_caption_informative_mode: bool = Field(
        default=True,
        description="Prefer informative captions for value posts and fall back deterministically when LLM output is weak",
    )
    value_caption_block_on_publish: bool = Field(
        default=False,
        description="Block publish dispatch for value posts whose captions still require review",
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
    reviewer_login_email: str = Field(
        "",
        description="Optional fixed reviewer account email for passwordless review-only login",
    )
    reviewer_login_token: str = Field(
        "",
        description="Secret token that unlocks the reviewer login route",
    )
    session_cookie_name: str = Field("ff_session", description="Name of the session cookie")
    session_max_age: int = Field(2592000, description="Session cookie max age in seconds (default 30 days)")
    auth_otp_code_length: int = Field(8, ge=6, le=10, description="Expected Supabase email OTP length")
    bypass_auth_in_development: bool = Field(
        False,
        description="Skip end-user auth in development so local test runs do not depend on Supabase rate limits",
    )
    
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

    @property
    def is_auth_bypassed(self) -> bool:
        return self.is_development and self.bypass_auth_in_development


# Singleton compatibility hook retained for tests and older imports.
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Load settings from the current environment."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def fingerprint_secret(value: str, *, prefix_length: int = 12) -> str:
    """Return a stable fingerprint for a secret without exposing the secret."""
    if not value:
        return "unset"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:prefix_length]


def google_ai_context_fingerprint(settings: Optional[Settings] = None) -> dict[str, Any]:
    """Summarize the active Google AI context for startup logging."""
    resolved = settings or get_settings()
    return {
        "gemini_api_key_fingerprint": fingerprint_secret(resolved.gemini_api_key),
        "gemini_api_key_present": bool(resolved.gemini_api_key),
        "google_ai_project_id": resolved.google_ai_project_id or "unset",
        "google_application_credentials_configured": bool(resolve_google_application_credentials_path(resolved)),
    }


def resolve_google_application_credentials_path(settings: Optional[Settings] = None) -> Optional[str]:
    """Return a usable ADC path when one is configured or present in the standard Cloud SDK location."""
    resolved = settings or get_settings()

    explicit = (getattr(resolved, "google_application_credentials", "") or "").strip() or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if explicit and Path(explicit).expanduser().is_file():
        return str(Path(explicit).expanduser())

    inline_json = (
        (getattr(resolved, "google_application_credentials_json", "") or "").strip()
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    )
    if not inline_json and explicit.startswith("{"):
        inline_json = explicit

    if inline_json:
        materialized_path = _materialize_google_adc_json(inline_json)
        if materialized_path:
            return materialized_path

    default_adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if default_adc.is_file():
        return str(default_adc)

    return None


def _materialize_google_adc_json(raw_json: str) -> Optional[str]:
    payload = (raw_json or "").strip()
    if not payload:
        return None

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    target = Path(tempfile.gettempdir()) / f"aiugc-google-adc-{digest}.json"
    if not target.exists():
        target.write_text(payload, encoding="utf-8")
        target.chmod(0o600)
    return str(target)
