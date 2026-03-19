"""
TikTok crypto and state helpers.
Keeps OAuth state signing and redaction local to the TikTok provider slice.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.errors import ValidationError

STATE_TTL_MINUTES = 15
REDACT_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "code_verifier",
    "upload_url",
}


def generate_code_verifier() -> str:
    """Generate a PKCE code verifier that is safe for TikTok OAuth."""
    return secrets.token_urlsafe(48)


def build_code_challenge(code_verifier: str) -> str:
    """Create the S256 code challenge for the PKCE flow."""
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def build_signed_state(secret: str, *, batch_id: Optional[str], code_verifier: str) -> str:
    """Encode and sign the OAuth state payload."""
    payload = json.dumps(
        {
            "batch_id": batch_id,
            "code_verifier": code_verifier,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def decode_signed_state(state: str, secret: str) -> Dict[str, Any]:
    """Verify the OAuth state signature and reject stale payloads."""
    if "." not in state:
        raise ValidationError("Invalid TikTok OAuth state.")

    encoded, signature = state.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValidationError("Invalid TikTok OAuth state signature.")

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid TikTok OAuth state payload.") from exc

    code_verifier = str(payload.get("code_verifier") or "").strip()
    issued_at_raw = str(payload.get("issued_at") or "").strip()
    if not code_verifier or not issued_at_raw:
        raise ValidationError("TikTok OAuth state is missing required fields.")

    try:
        issued_at = datetime.fromisoformat(issued_at_raw)
    except ValueError as exc:
        raise ValidationError("Invalid TikTok OAuth state timestamp.") from exc

    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) - issued_at > timedelta(minutes=STATE_TTL_MINUTES):
        raise ValidationError("TikTok OAuth state expired.")

    return {
        "batch_id": payload.get("batch_id"),
        "code_verifier": code_verifier,
        "issued_at": issued_at.isoformat(),
    }


def redact_secret_payload(value: Any) -> Any:
    """Remove secrets from nested payloads before logging or returning them."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in REDACT_KEYS or "token" in key.lower() or "secret" in key.lower():
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_secret_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_secret_payload(item) for item in value]
    return value
