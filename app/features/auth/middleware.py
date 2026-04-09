"""
Lippe Lift Studio Auth Middleware
Session cookie management and route protection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Paths that do not require authentication
PUBLIC_PATH_PREFIXES = ("/auth/", "/health", "/static/", "/tiktok", "/topics/cron")
PUBLIC_PATHS_EXACT = ("/health",)


def _is_local_request(request: Request) -> bool:
    forwarded_host = request.headers.get("x-forwarded-host", "")
    raw_host = forwarded_host.split(",", 1)[0].strip() or request.headers.get("host", "")
    host = raw_host.split(",", 1)[0].strip()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    else:
        host = host.split(":", 1)[0]
    normalized_host = host.lower()
    return normalized_host in {"127.0.0.1", "::1", "localhost"} or normalized_host.endswith(".localhost")


def should_bypass_auth(request: Request) -> bool:
    """Only bypass auth for real local development hosts."""
    settings = get_settings()
    return settings.is_auth_bypassed or (settings.is_development and _is_local_request(request))


def encode_session_cookie(data: Dict[str, Any], secret: str) -> str:
    """Encode and HMAC-sign a session payload for cookie storage."""
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def decode_session_cookie(value: str, secret: str) -> Optional[Dict[str, Any]]:
    """Verify HMAC signature and decode session payload. Returns None on failure."""
    if "." not in value:
        return None

    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None


def is_public_path(path: str) -> bool:
    """Check if a request path is exempt from authentication."""
    if path in PUBLIC_PATHS_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


async def require_auth(request: Request) -> Optional[RedirectResponse]:
    """
    Auth middleware. Returns None if authenticated (sets request.state.user_email),
    or a RedirectResponse to login if not.
    """
    path = request.url.path
    if is_public_path(path):
        return None

    settings = get_settings()
    if should_bypass_auth(request):
        request.state.user_email = "local-dev@lippelift.de"
        return None

    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return RedirectResponse(url="/auth/login", status_code=302)

    session = decode_session_cookie(cookie_value, settings.token_encryption_key)
    if not session or "access_token" not in session:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Validate token with Supabase
    from app.features.auth.queries import get_user_from_token, refresh_session

    user = await get_user_from_token(session["access_token"])
    if user:
        request.state.user_email = user["email"]
        return None

    # Token expired — try refresh
    if "refresh_token" in session:
        new_session = await refresh_session(session["refresh_token"])
        if new_session:
            request.state.user_email = new_session["user"]["email"]
            request.state.new_session = new_session  # Handler will update cookie
            return None

    return RedirectResponse(url="/auth/login", status_code=302)
