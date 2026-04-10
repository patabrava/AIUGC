"""
Lippe Lift Studio Auth Queries
Supabase Auth API interactions and email allowlist logic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings
from app.core.errors import AuthenticationError, RateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)


def is_email_allowed(email: str) -> bool:
    """Check if an email is authorized to log in."""
    settings = get_settings()
    normalized = email.strip().lower()
    domain = normalized.split("@")[-1] if "@" in normalized else ""

    if domain == settings.allowed_email_domain.lower():
        return True

    reviewer_email = settings.reviewer_login_email.strip().lower()
    if reviewer_email and normalized == reviewer_email:
        return True

    explicit = [e.strip().lower() for e in settings.allowed_emails.split(",") if e.strip()]
    return normalized in explicit


def _auth_url() -> str:
    """Build the Supabase Auth (GoTrue) base URL."""
    settings = get_settings()
    return f"{settings.supabase_url}/auth/v1"


def _auth_headers() -> Dict[str, str]:
    """Common headers for Supabase Auth API calls."""
    settings = get_settings()
    return {
        "apikey": settings.supabase_key,
        "Content-Type": "application/json",
    }


def _callback_url() -> str:
    """Build the auth callback URL for magic link redirects."""
    settings = get_settings()
    base = settings.app_url or "http://localhost:8000"
    return f"{base}/auth/callback"


async def send_otp(email: str) -> bool:
    """Send a magic OTP code to the given email via Supabase Auth."""
    settings = get_settings()
    if settings.is_auth_bypassed:
        logger.info("auth_otp_bypassed_local", email=email)
        return True

    redirect_to = _callback_url()
    url = f"{_auth_url()}/otp?redirect_to={redirect_to}"
    payload = {"email": email}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_auth_headers())

    if response.status_code == 429:
        logger.warning("auth_otp_rate_limited", email=email)
        raise RateLimitError("Rate limit exceeded. Please wait before requesting another code.")

    if response.status_code >= 400:
        body = response.json()
        logger.error("auth_otp_send_failed", email=email, status=response.status_code, body=body)
        raise AuthenticationError(
            message=body.get("msg", "Failed to send verification code."),
            details={"status": response.status_code},
        )

    logger.info("auth_otp_sent", email=email)
    return True


async def verify_otp(email: str, token: str) -> Optional[Dict[str, Any]]:
    """Verify an OTP code with Supabase Auth. Returns session dict or None."""
    settings = get_settings()
    if settings.is_auth_bypassed:
        logger.info("auth_otp_bypassed_local_verified", email=email)
        return {
            "access_token": f"local-access-token:{email}",
            "refresh_token": f"local-refresh-token:{email}",
            "user": {"email": email},
        }

    url = f"{_auth_url()}/verify"
    payload = {"email": email, "token": token, "type": "email"}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_auth_headers())

    if response.status_code != 200:
        logger.warning("auth_otp_verify_failed", email=email, status=response.status_code)
        return None

    data = response.json()
    logger.info("auth_otp_verified", email=email)
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "user": data.get("user", {}),
    }


def _reviewer_token_email(token: str) -> Optional[str]:
    if token.startswith("review-access-token:"):
        return token.split(":", 1)[1]
    if token.startswith("review-refresh-token:"):
        return token.split(":", 1)[1]
    return None


async def get_user_from_token(access_token: str) -> Optional[Dict[str, Any]]:
    """Validate an access token and return user info, or None if invalid."""
    settings = get_settings()
    reviewer_email = _reviewer_token_email(access_token)
    if reviewer_email:
        return {"email": reviewer_email}
    if settings.is_auth_bypassed and access_token.startswith("local-access-token:"):
        return {"email": access_token.split(":", 1)[1]}

    url = f"{_auth_url()}/user"
    headers = {**_auth_headers(), "Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)

    if response.status_code != 200:
        return None

    return response.json()


async def refresh_session(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Refresh an expired session. Returns new session dict or None."""
    settings = get_settings()
    reviewer_email = _reviewer_token_email(refresh_token)
    if reviewer_email:
        return {
            "access_token": f"review-access-token:{reviewer_email}",
            "refresh_token": refresh_token,
            "user": {"email": reviewer_email},
        }
    if settings.is_auth_bypassed and refresh_token.startswith("local-refresh-token:"):
        email = refresh_token.split(":", 1)[1]
        return {
            "access_token": f"local-access-token:{email}",
            "refresh_token": refresh_token,
            "user": {"email": email},
        }

    url = f"{_auth_url()}/token?grant_type=refresh_token"
    payload = {"refresh_token": refresh_token}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=_auth_headers())

    if response.status_code != 200:
        logger.warning("auth_session_refresh_failed", status=response.status_code)
        return None

    data = response.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "user": data.get("user", {}),
    }


async def sign_out(access_token: str) -> None:
    """Sign out a user via Supabase Auth."""
    url = f"{_auth_url()}/logout"
    headers = {**_auth_headers(), "Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers)

    logger.info("auth_user_signed_out")
