"""
FLOW-FORGE Auth Queries
Supabase Auth API interactions and email allowlist logic.
"""

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def is_email_allowed(email: str) -> bool:
    """Check if an email is authorized to log in."""
    settings = get_settings()
    normalized = email.strip().lower()
    domain = normalized.split("@")[-1] if "@" in normalized else ""

    if domain == settings.allowed_email_domain.lower():
        return True

    explicit = [e.strip().lower() for e in settings.allowed_emails.split(",") if e.strip()]
    return normalized in explicit
