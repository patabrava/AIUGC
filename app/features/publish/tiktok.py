"""
TikTok publish integration.
Implements sandbox OAuth and draft upload without disturbing the Meta scheduling flow.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.adapters.supabase_client import get_supabase
from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    ErrorCode,
    FlowForgeException,
    NotFoundError,
    RateLimitError,
    SuccessResponse,
    ThirdPartyError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.states import BatchState
from app.features.publish.schemas import (
    TikTokAccountResponse,
    TikTokPublishRequest,
    TikTokPublishJobResponse,
    TikTokUploadDraftRequest,
)
from app.features.topics.captions import resolve_selected_caption
from app.features.publish.tiktok_crypto import (
    build_code_challenge,
    build_signed_state,
    decode_signed_state,
    generate_code_verifier,
    redact_secret_payload,
)

logger = get_logger(__name__)
router = APIRouter(tags=["tiktok"])

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_API_BASE = "https://open.tiktokapis.com"
TIKTOK_TIMEOUT_SECONDS = 60.0
DEFAULT_SCOPE = "user.info.basic,video.upload,video.publish"
DEFAULT_PRIVACY_LEVEL = "SELF_ONLY"
MAX_SINGLE_CHUNK_BYTES = 64 * 1024 * 1024
MIN_CHUNK_BYTES = 5 * 1024 * 1024
MAX_FINAL_CHUNK_BYTES = 128 * 1024 * 1024
TIKTOK_STATUS_POLL_ATTEMPTS = 15
TIKTOK_STATUS_POLL_SECONDS = 2.0


def _load_json_object(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _coerce_supabase_rows(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _state_secret() -> str:
    settings = get_settings()
    if not settings.token_encryption_key:
        raise ValidationError("TikTok token encryption key is not configured.")
    return settings.token_encryption_key


def _require_tiktok_settings() -> Any:
    settings = get_settings()
    missing = [
        name
        for name, value in [
            ("TIKTOK_CLIENT_KEY", settings.tiktok_client_key),
            ("TIKTOK_CLIENT_SECRET", settings.tiktok_client_secret),
            ("TIKTOK_REDIRECT_URI", settings.tiktok_redirect_uri),
            ("TOKEN_ENCRYPTION_KEY", settings.token_encryption_key),
            ("APP_URL", settings.app_url),
            ("PRIVACY_POLICY_URL", settings.privacy_policy_url),
            ("TERMS_URL", settings.terms_url),
            ("TIKTOK_SANDBOX_ACCOUNT", settings.tiktok_sandbox_account),
        ]
        if not value
    ]
    if missing:
        raise ValidationError(
            "TikTok sandbox integration is not configured.",
            details={"missing_env": missing},
        )
    return settings


def _sanitize_connected_account(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {"status": "disconnected"}

    sanitized = dict(row)
    sanitized.pop("access_token", None)
    sanitized.pop("refresh_token", None)
    status = "connected"
    expires_at = sanitized.get("access_token_expires_at")
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                status = "reconnect_required"
        except ValueError:
            status = "reconnect_required"
    sanitized["status"] = status
    return sanitized


def _scope_set(scope: str) -> set[str]:
    return {part.strip() for part in str(scope or "").split(",") if part.strip()}


def _sanitize_creator_info(value: Dict[str, Any]) -> Dict[str, Any]:
    if not value:
        return {}
    privacy_options = [str(item) for item in value.get("privacy_level_options") or [] if item]
    return {
        "creator_username": value.get("creator_username"),
        "creator_nickname": value.get("creator_nickname"),
        "privacy_level_options": privacy_options,
        "comment_disabled": bool(value.get("comment_disabled")),
        "duet_disabled": bool(value.get("duet_disabled")),
        "stitch_disabled": bool(value.get("stitch_disabled")),
        "max_video_post_duration_sec": value.get("max_video_post_duration_sec"),
    }


def _derive_tiktok_readiness(account: Dict[str, Any], creator_info: Optional[Dict[str, Any]] = None, *, creator_error: Optional[str] = None) -> Dict[str, Any]:
    status = str(account.get("status") or "disconnected")
    environment = str(account.get("environment") or "").lower()
    scopes = _scope_set(str(account.get("scope") or ""))
    draft_ready = status == "connected" and "video.upload" in scopes
    publish_scope_ready = status == "connected" and "video.publish" in scopes
    creator_ready = bool(creator_info and (creator_info.get("privacy_level_options") or []))
    publish_ready = publish_scope_ready and creator_ready

    readiness_status = "disconnected"
    readiness_reason = "Connect TikTok before posting."
    if status == "reconnect_required":
        readiness_status = "reconnect_required"
        readiness_reason = "Reconnect TikTok before posting."
    elif status == "connected" and not publish_scope_ready:
        readiness_status = "connected_not_publishable"
        readiness_reason = "This TikTok login can upload drafts but cannot direct-post until video.publish is granted."
    elif status == "connected" and creator_error:
        readiness_status = "connected_not_publishable"
        readiness_reason = creator_error
    elif status == "connected" and not creator_ready:
        readiness_status = "connected_not_publishable"
        readiness_reason = "TikTok creator settings are unavailable. Refresh the connection before posting."
    elif status == "connected" and environment == "sandbox" and draft_ready:
        readiness_status = "draft_ready"
        readiness_reason = "TikTok sandbox mode only supports draft upload. Use Upload Draft to TikTok for testing."
        publish_ready = False
    elif publish_ready:
        readiness_status = "publish_ready"
        readiness_reason = "This TikTok login can post directly from FLOW-FORGE."
    elif draft_ready:
        readiness_status = "draft_ready"
        readiness_reason = "This TikTok login can upload drafts."

    return {
        **account,
        "scope_flags": {
            "video_upload": "video.upload" in scopes,
            "video_publish": "video.publish" in scopes,
        },
        "creator_info": creator_info or {},
        "draft_ready": draft_ready,
        "publish_ready": publish_ready,
        "readiness_status": readiness_status,
        "readiness_reason": readiness_reason,
    }


def get_tiktok_public_account() -> Dict[str, Any]:
    """Return the latest connected TikTok account without token material."""
    try:
        settings = get_settings()
        response = (
            get_supabase()
            .client.table("connected_accounts")
            .select("*")
            .eq("platform", "tiktok")
            .eq("environment", settings.tiktok_environment)
            .order("updated_at")
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception:
        return {"status": "disconnected"}
    if not rows:
        return {"status": "disconnected"}
    return _sanitize_connected_account(rows[-1])


async def _query_creator_info(access_token: str) -> Dict[str, Any]:
    payload = await _tiktok_request(
        "POST",
        "/v2/post/publish/creator_info/query/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_body={},
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return _sanitize_creator_info(data if isinstance(data, dict) else {})


async def get_tiktok_publish_state() -> Dict[str, Any]:
    account = get_tiktok_public_account()
    if account.get("status") != "connected":
        return _derive_tiktok_readiness(account)

    try:
        secret_account = _load_tiktok_account_secret()
    except AuthenticationError as exc:
        return _derive_tiktok_readiness(
            {**account, "status": "reconnect_required"},
            creator_error=exc.message,
        )
    except ValidationError as exc:
        return _derive_tiktok_readiness(account, creator_error=exc.message)

    try:
        creator_info = await _query_creator_info(secret_account["access_token_plain"])
        return _derive_tiktok_readiness(account, creator_info)
    except AuthenticationError as exc:
        return _derive_tiktok_readiness(
            {**account, "status": "reconnect_required"},
            creator_error=exc.message,
        )
    except FlowForgeException as exc:
        return _derive_tiktok_readiness(account, creator_error=exc.message)


def _load_tiktok_account_secret() -> Dict[str, Any]:
    settings = _require_tiktok_settings()
    response = get_supabase().client.rpc(
        "get_tiktok_connected_account_secret",
        {
            "p_environment": settings.tiktok_environment,
            "p_encryption_key": settings.token_encryption_key,
        },
    ).execute()
    rows = _coerce_supabase_rows(response.data)
    if not rows:
        raise AuthenticationError("No TikTok sandbox account is connected.")

    account = dict(rows[0])
    expires_at = account.get("access_token_expires_at")
    if expires_at:
        expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        if expires_dt <= datetime.now(timezone.utc):
            raise AuthenticationError("TikTok access token expired. Reconnect the sandbox account.")
    return account


async def _tiktok_request(
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = path if path.startswith("http") else f"{TIKTOK_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIKTOK_TIMEOUT_SECONDS) as client:
        response = await client.request(method, url, headers=headers, params=params, data=data, json=json_body)

    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text}

    error = payload.get("error") if isinstance(payload, dict) else None
    error_description = payload.get("error_description") if isinstance(payload, dict) else None
    log_id = payload.get("log_id") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        error_code = str(error.get("code") or error.get("error") or "")
        error_message = str(error.get("message") or error_description or payload.get("message") or response.text or "TikTok request failed")
    elif isinstance(error, str):
        error_code = error
        error_message = str(error_description or payload.get("message") or response.text or "TikTok request failed")
    else:
        error_code = ""
        error_message = str(payload.get("message") or response.text or "TikTok request failed")

    if response.status_code == 429 or error_code == "rate_limit_exceeded":
        raise RateLimitError(
            message="TikTok rate limit exceeded.",
            details={"status_code": response.status_code, "error": redact_secret_payload(error or payload), "log_id": log_id},
        )

    if response.status_code == 401 or error_code in {"access_token_invalid", "scope_not_authorized"}:
        raise AuthenticationError(
            message="TikTok access token is invalid or missing required scope.",
            details={"status_code": response.status_code, "error": redact_secret_payload(error or payload), "log_id": log_id},
        )

    private_post_restriction = "unaudited_client_can_only_post_to_private_accounts"
    response_text = str(response.text or "")
    payload_text = json.dumps(payload, ensure_ascii=False)
    if response.status_code == 403 and "/v2/post/publish/" in url:
        raise ValidationError(
            "TikTok direct posting is blocked for this account until the creator account is private or the API client is audited. Use draft upload for this deployment.",
            details={
                "status_code": response.status_code,
                "error": redact_secret_payload(error or payload),
                "url": url,
                "log_id": log_id,
            },
        )
    if response.status_code == 403 and (
        error_code == private_post_restriction
        or private_post_restriction in payload_text
        or "content-sharing-guidelines" in response_text
        or "private accounts" in payload_text.lower()
    ):
        raise ValidationError(
            "TikTok direct posting is blocked for this account until the creator account is private or the API client is audited. Use draft upload for this deployment.",
            details={
                "status_code": response.status_code,
                "error": redact_secret_payload(error or payload),
                "url": url,
                "log_id": log_id,
            },
        )

    if response.is_error or (error and error_code and error_code != "ok"):
        raise ThirdPartyError(
            message=error_message,
            details={"status_code": response.status_code, "error": redact_secret_payload(error or payload), "url": url, "log_id": log_id},
        )

    return payload if isinstance(payload, dict) else {}


async def _exchange_code_for_tokens(code: str, code_verifier: str) -> Dict[str, Any]:
    settings = _require_tiktok_settings()
    payload = await _tiktok_request(
        "POST",
        "/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.tiktok_redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    return payload.get("data") if isinstance(payload.get("data"), dict) else payload


async def _fetch_user_profile(access_token: str) -> Dict[str, Any]:
    payload = await _tiktok_request(
        "GET",
        "/v2/user/info/",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"fields": "open_id,display_name,avatar_url"},
    )
    return ((payload.get("data") or {}).get("user")) or {}


def _upsert_connected_account(
    *,
    open_id: str,
    display_name: str,
    avatar_url: str,
    access_token: str,
    refresh_token: str,
    access_token_expires_at: Optional[str],
    refresh_token_expires_at: Optional[str],
    scope: str,
) -> Dict[str, Any]:
    settings = _require_tiktok_settings()
    response = get_supabase().client.rpc(
        "upsert_tiktok_connected_account",
        {
            "p_user_id": None,
            "p_open_id": open_id,
            "p_display_name": display_name,
            "p_avatar_url": avatar_url,
            "p_access_token_plain": access_token,
            "p_refresh_token_plain": refresh_token or "",
            "p_access_token_expires_at": access_token_expires_at,
            "p_refresh_token_expires_at": refresh_token_expires_at,
            "p_scope": scope,
            "p_environment": settings.tiktok_environment,
            "p_encryption_key": settings.token_encryption_key,
        },
    ).execute()
    rows = _coerce_supabase_rows(response.data)
    if not rows:
        raise ThirdPartyError("TikTok account persistence failed.")
    return rows[0]


def _storage_key_from_url(video_url: str) -> str:
    parsed = urlparse(video_url)
    return parsed.path.lstrip("/")


def _upsert_media_asset(
    *,
    source_url: str,
    storage_key: str,
    mime_type: str,
    file_size: int,
    duration_seconds: Optional[float],
    status: str,
) -> Dict[str, Any]:
    client = get_supabase().client
    existing = client.table("media_assets").select("*").eq("source_url", source_url).limit(1).execute().data or []
    payload = {
        "user_id": None,
        "source_url": source_url,
        "storage_key": storage_key,
        "mime_type": mime_type,
        "file_size": file_size,
        "duration_seconds": duration_seconds,
        "status": status,
    }
    if existing:
        response = client.table("media_assets").update(payload).eq("id", existing[0]["id"]).execute()
    else:
        response = client.table("media_assets").insert(payload).execute()
    rows = response.data or []
    if not rows:
        raise ThirdPartyError("Media asset persistence failed.")
    return dict(rows[0])


def _create_publish_job(
    *,
    connected_account_id: str,
    media_asset_id: str,
    caption: str,
    post_mode: str,
    request_payload_json: Dict[str, Any],
) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    response = get_supabase().client.table("publish_jobs").insert(
        {
            "user_id": None,
            "connected_account_id": connected_account_id,
            "platform": "tiktok",
            "media_asset_id": media_asset_id,
            "caption": caption,
            "post_mode": post_mode,
            "status": "uploading",
            "request_payload_json": request_payload_json,
            "response_payload_json": {},
            "error_message": "",
            "created_at": now,
            "updated_at": now,
        }
    ).execute()
    rows = response.data or []
    if not rows:
        raise ThirdPartyError("TikTok publish job creation failed.")
    return dict(rows[0])


def _update_publish_job(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = get_supabase().client.table("publish_jobs").update(
        {**payload, "updated_at": datetime.utcnow().isoformat()}
    ).eq("id", job_id).execute()
    rows = response.data or []
    if not rows:
        raise NotFoundError("TikTok publish job not found.", details={"job_id": job_id})
    return dict(rows[0])


def _load_string_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [value]
    return []


def _derive_post_publish_status(networks: List[str], publish_results: Dict[str, Any]) -> str:
    if not networks:
        return "pending"
    statuses = [str((publish_results.get(network) or {}).get("status", "pending")) for network in networks]
    if all(status == "published" for status in statuses):
        return "published"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status in {"publishing", "processing", "awaiting_user_action"} for status in statuses):
        return "publishing"
    if any(status == "scheduled" for status in statuses):
        return "scheduled"
    return "pending"


def _map_tiktok_result_status(provider_status: str, post_mode: str) -> str:
    normalized = str(provider_status or "").upper()
    if normalized == "PUBLISH_COMPLETE":
        return "published"
    if normalized == "FAILED":
        return "failed"
    if post_mode == "draft" and normalized == "SEND_TO_USER_INBOX":
        return "awaiting_user_action"
    return "publishing"


def _map_tiktok_publish_job_status(provider_status: str, post_mode: str) -> str:
    normalized = str(provider_status or "").upper()
    if normalized == "FAILED":
        return "failed"
    if normalized in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}:
        return "submitted"
    if post_mode in {"draft", "direct"}:
        return "submitted"
    return "created"


def _update_post_tiktok_result(
    post: Dict[str, Any],
    job: Dict[str, Any],
    *,
    provider_status: str,
    post_mode: str,
    provider_post_id: Optional[str] = None,
    fail_reason: str = "",
    error_message: str = "",
) -> None:
    publish_results = _load_json_object(post.get("publish_results"))
    platform_ids = _load_json_object(post.get("platform_ids"))
    local_status = _map_tiktok_result_status(provider_status, post_mode)
    result = {
        "status": local_status,
        "post_mode": post_mode,
        "provider_status": provider_status,
        "publish_job_id": job["id"],
        "publish_id": job.get("tiktok_publish_id"),
        "remote_id": provider_post_id or job.get("tiktok_publish_id"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if provider_post_id:
        result["post_id"] = provider_post_id
    if fail_reason:
        result["fail_reason"] = fail_reason
    if error_message:
        result["error_message"] = error_message

    publish_results["tiktok"] = result
    if provider_post_id:
        platform_ids["tiktok"] = provider_post_id

    payload = {"publish_results": publish_results, "platform_ids": platform_ids}
    next_status = _derive_post_publish_status(_load_string_list(post.get("social_networks")), publish_results)
    if next_status != (post.get("publish_status") or "pending"):
        payload["publish_status"] = next_status

    get_supabase().client.table("posts").update(
        payload
    ).eq("id", post["id"]).execute()


async def _download_video_bytes(video_url: str) -> Tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=TIKTOK_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(video_url)
    if response.is_error:
        raise ThirdPartyError(
            "Video download for TikTok upload failed.",
            details={"status_code": response.status_code, "video_url": video_url},
        )
    content_type = response.headers.get("content-type", "video/mp4").split(";")[0].strip() or "video/mp4"
    return response.content, content_type


def _calculate_upload_plan(video_size: int) -> Tuple[int, int]:
    if video_size <= 0:
        raise ValidationError("TikTok upload requires a non-empty video file.")
    if video_size <= MAX_SINGLE_CHUNK_BYTES:
        return video_size, 1

    chunk_size = MAX_SINGLE_CHUNK_BYTES
    full_chunks, remainder = divmod(video_size, chunk_size)
    if remainder == 0:
        return chunk_size, full_chunks
    if remainder >= MIN_CHUNK_BYTES:
        return chunk_size, full_chunks + 1
    if full_chunks < 1:
        return video_size, 1
    if chunk_size + remainder > MAX_FINAL_CHUNK_BYTES:
        raise ValidationError("TikTok upload video is too large for the sandbox file-upload flow.")
    return chunk_size, full_chunks


async def _initialize_inbox_video_upload(access_token: str, video_size: int) -> Dict[str, Any]:
    chunk_size, total_chunk_count = _calculate_upload_plan(video_size)
    payload = await _tiktok_request(
        "POST",
        "/v2/post/publish/inbox/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_body={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            }
        },
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not data.get("upload_url") or not data.get("publish_id"):
        raise ThirdPartyError("TikTok upload init did not return publish_id and upload_url.", details=redact_secret_payload(data))
    data["chunk_size"] = chunk_size
    data["total_chunk_count"] = total_chunk_count
    return data


def _build_tiktok_post_info(
    *,
    caption: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
) -> Dict[str, Any]:
    title = " ".join(str(caption or "").split())[:150].strip() or "Posted from FLOW-FORGE"
    return {
        "title": title,
        "privacy_level": privacy_level,
        "disable_comment": disable_comment,
        "disable_duet": disable_duet,
        "disable_stitch": disable_stitch,
        "video_cover_timestamp_ms": 1000,
    }


async def _initialize_direct_post(
    access_token: str,
    *,
    video_size: int,
    caption: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
) -> Dict[str, Any]:
    chunk_size, total_chunk_count = _calculate_upload_plan(video_size)
    payload = await _tiktok_request(
        "POST",
        "/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_body={
            "post_info": _build_tiktok_post_info(
                caption=caption,
                privacy_level=privacy_level,
                disable_comment=disable_comment,
                disable_duet=disable_duet,
                disable_stitch=disable_stitch,
            ),
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        },
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not data.get("upload_url") or not data.get("publish_id"):
        raise ThirdPartyError("TikTok direct post init did not return publish_id and upload_url.", details=redact_secret_payload(data))
    data["chunk_size"] = chunk_size
    data["total_chunk_count"] = total_chunk_count
    return data


async def _fetch_publish_status(access_token: str, publish_id: str) -> Dict[str, Any]:
    payload = await _tiktok_request(
        "POST",
        "/v2/post/publish/status/fetch/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_body={"publish_id": publish_id},
    )
    return payload.get("data") if isinstance(payload.get("data"), dict) else {}


async def _poll_publish_status(access_token: str, publish_id: str, *, post_mode: str) -> Dict[str, Any]:
    latest: Dict[str, Any] = {}
    for _ in range(TIKTOK_STATUS_POLL_ATTEMPTS):
        latest = await _fetch_publish_status(access_token, publish_id)
        provider_status = str(latest.get("status") or "").upper()
        if provider_status in {"FAILED", "PUBLISH_COMPLETE"}:
            return latest
        if post_mode == "draft" and provider_status == "SEND_TO_USER_INBOX":
            return latest
        await asyncio.sleep(TIKTOK_STATUS_POLL_SECONDS)
    return latest


def _validate_creator_info_for_direct_post(
    creator_info: Dict[str, Any],
    *,
    privacy_level: str,
    duration_seconds: Optional[float],
) -> None:
    privacy_options = [str(item) for item in creator_info.get("privacy_level_options") or [] if item]
    if not privacy_options:
        raise ValidationError("TikTok creator settings are unavailable. Reconnect TikTok before posting.")
    if privacy_level not in privacy_options:
        raise ValidationError("Selected TikTok privacy level is not allowed for this account.", details={"privacy_level": privacy_level, "privacy_level_options": privacy_options})
    max_duration = creator_info.get("max_video_post_duration_sec")
    if max_duration is not None and duration_seconds is not None and float(duration_seconds) > float(max_duration):
        raise ValidationError(
            "This generated video is longer than TikTok allows for this account.",
            details={"duration_seconds": duration_seconds, "max_video_post_duration_sec": max_duration},
        )


def _is_tiktok_private_post_restriction(exc: Exception) -> bool:
    if not isinstance(exc, ThirdPartyError):
        return False
    details = exc.details if isinstance(exc.details, dict) else {}
    error = details.get("error") if isinstance(details.get("error"), dict) else {}
    return (
        int(details.get("status_code") or 0) == 403
        and str(error.get("code") or "").strip() == "unaudited_client_can_only_post_to_private_accounts"
    )


async def _upload_video_chunks(upload_url: str, video_bytes: bytes, content_type: str, chunk_size: int, total_chunk_count: int) -> None:
    async with httpx.AsyncClient(timeout=TIKTOK_TIMEOUT_SECONDS) as client:
        total_size = len(video_bytes)
        for index in range(total_chunk_count):
            start = index * chunk_size
            end = min(total_size, start + chunk_size)
            chunk = video_bytes[start:end]
            response = await client.put(
                upload_url,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end - 1}/{total_size}",
                },
                content=chunk,
            )
            if response.is_error:
                raise ThirdPartyError(
                    "TikTok chunk upload failed.",
                    details={
                        "status_code": response.status_code,
                        "chunk_index": index,
                        "response_text": response.text[:500],
                    },
                )


def _load_post_for_tiktok(post_id: str, *, mode: str) -> Dict[str, Any]:
    response = get_supabase().client.table("posts").select(
        "id,batch_id,topic_title,seed_data,video_url,video_metadata,publish_caption,publish_results,platform_ids,social_networks,publish_status"
    ).eq("id", post_id).execute()
    rows = response.data or []
    if not rows:
        raise NotFoundError("Post not found.", details={"post_id": post_id})
    post = dict(rows[0])
    seed_data = _load_json_object(post.get("seed_data"))
    if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
        raise ValidationError("Removed posts cannot be uploaded to TikTok.", details={"post_id": post_id})
    if not post.get("video_url"):
        raise ValidationError("Post has no generated video for TikTok upload.", details={"post_id": post_id})

    batch = get_supabase().client.table("batches").select("id,state").eq("id", post["batch_id"]).execute().data or []
    if not batch:
        raise NotFoundError("Batch not found for TikTok upload.", details={"post_id": post_id})
    batch_state = str(batch[0].get("state") or "")
    if mode == "draft":
        allowed_states = {BatchState.S7_PUBLISH_PLAN.value, BatchState.S8_COMPLETE.value}
        error_message = "TikTok draft upload is only available in S7_PUBLISH_PLAN or S8_COMPLETE."
    else:
        allowed_states = {BatchState.S7_PUBLISH_PLAN.value, BatchState.S8_COMPLETE.value}
        error_message = "TikTok direct posting is only available in S7_PUBLISH_PLAN or S8_COMPLETE."
    if batch_state not in allowed_states:
        raise ValidationError(
            error_message,
            details={"batch_id": post["batch_id"], "state": batch_state, "mode": mode},
        )
    return post


@router.get("/api/auth/tiktok/start")
async def start_tiktok_oauth(batch_id: Optional[str] = None):
    """Start TikTok OAuth and redirect the browser to Login Kit."""
    settings = _require_tiktok_settings()
    code_verifier = generate_code_verifier()
    state = build_signed_state(_state_secret(), batch_id=batch_id, code_verifier=code_verifier)
    query = urlencode(
        {
            "client_key": settings.tiktok_client_key,
            "redirect_uri": settings.tiktok_redirect_uri,
            "response_type": "code",
            "scope": DEFAULT_SCOPE,
            "state": state,
            "code_challenge": build_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    logger.info("tiktok_oauth_started", batch_id=batch_id, environment=settings.tiktok_environment)
    return RedirectResponse(url=f"{TIKTOK_AUTH_URL}?{query}", status_code=302)


@router.get("/api/auth/tiktok/callback")
@router.get("/publish/tiktok/callback")
async def tiktok_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Handle the TikTok OAuth callback and persist the connected sandbox account."""
    _require_tiktok_settings()
    if not state:
        raise ValidationError("Missing TikTok OAuth state.")

    state_payload = decode_signed_state(state, _state_secret())
    batch_id = state_payload.get("batch_id")
    redirect_target = f"/batches/{batch_id}" if batch_id else "/batches"

    if error:
        logger.error(
            "tiktok_oauth_callback_failed",
            batch_id=batch_id,
            error=error,
            error_description=error_description,
        )
        return RedirectResponse(url=redirect_target, status_code=302)

    if not code:
        raise ValidationError("Missing TikTok OAuth code.")

    token_payload = await _exchange_code_for_tokens(code, state_payload["code_verifier"])
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise ThirdPartyError("TikTok token exchange did not return an access token.", details=redact_secret_payload(token_payload))

    profile = await _fetch_user_profile(access_token)
    open_id = str(profile.get("open_id") or token_payload.get("open_id") or "").strip()
    if not open_id:
        raise ThirdPartyError("TikTok user profile did not return open_id.", details=redact_secret_payload(profile))

    expires_in = int(token_payload.get("expires_in") or 0)
    refresh_expires_in = int(token_payload.get("refresh_expires_in") or 0)
    access_token_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat() if expires_in > 0 else None
    refresh_token_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=refresh_expires_in)
    ).isoformat() if refresh_expires_in > 0 else None

    account = _upsert_connected_account(
        open_id=open_id,
        display_name=str(profile.get("display_name") or "TikTok Account"),
        avatar_url=str(profile.get("avatar_url") or ""),
        access_token=access_token,
        refresh_token=str(token_payload.get("refresh_token") or ""),
        access_token_expires_at=access_token_expires_at,
        refresh_token_expires_at=refresh_token_expires_at,
        scope=str(token_payload.get("scope") or DEFAULT_SCOPE),
    )
    logger.info(
        "tiktok_account_connected",
        batch_id=batch_id,
        open_id=open_id,
        environment=account.get("environment"),
    )
    return RedirectResponse(url=redirect_target, status_code=302)


@router.post("/api/auth/tiktok/disconnect", response_model=SuccessResponse)
async def disconnect_tiktok_account():
    """Remove the connected TikTok account from the workspace."""
    settings = _require_tiktok_settings()
    response = (
        get_supabase()
        .client.table("connected_accounts")
        .delete()
        .eq("platform", "tiktok")
        .eq("environment", settings.tiktok_environment)
        .execute()
    )
    logger.info("tiktok_account_disconnected", environment=settings.tiktok_environment)
    return SuccessResponse(data={"status": "disconnected", "deleted": len(response.data or [])})


@router.get("/api/tiktok/account", response_model=SuccessResponse)
async def get_tiktok_account():
    """Return the current TikTok sandbox account without token material."""
    return SuccessResponse(data=TikTokAccountResponse(**(await get_tiktok_publish_state())).model_dump())


@router.post("/api/tiktok/upload-draft", response_model=SuccessResponse)
async def upload_tiktok_draft(request: TikTokUploadDraftRequest):
    """Upload a generated FLOW-FORGE video as a TikTok draft."""
    job = await upload_tiktok_draft_for_post(request.post_id, caption=request.caption)
    return SuccessResponse(data=TikTokPublishJobResponse(**_sanitize_publish_job(job)).model_dump())


@router.post("/api/tiktok/publish", response_model=SuccessResponse)
async def publish_tiktok_direct(request: TikTokPublishRequest):
    """Direct-post a generated FLOW-FORGE video to TikTok."""
    job = await publish_tiktok_direct_for_post(
        request.post_id,
        caption=request.caption,
        privacy_level=request.privacy_level,
        disable_comment=request.disable_comment,
        disable_duet=request.disable_duet,
        disable_stitch=request.disable_stitch,
    )
    return SuccessResponse(data=TikTokPublishJobResponse(**_sanitize_publish_job(job)).model_dump())


async def upload_tiktok_draft_for_post(post_id: str, caption: Optional[str] = None) -> Dict[str, Any]:
    """Upload a generated FLOW-FORGE video as a TikTok draft."""
    return await _publish_tiktok_post(
        post_id,
        caption=caption,
        mode="draft",
        privacy_level=None,
        disable_comment=False,
        disable_duet=False,
        disable_stitch=False,
    )


async def publish_tiktok_direct_for_post(
    post_id: str,
    *,
    caption: Optional[str] = None,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
) -> Dict[str, Any]:
    """Direct-post a generated FLOW-FORGE video to TikTok."""
    settings = _require_tiktok_settings()
    if settings.tiktok_environment == "sandbox":
        raise ValidationError(
            "TikTok sandbox mode is draft-only. Use Upload Draft to TikTok for testing, or switch to a production TikTok app for direct posting.",
            details={"post_id": post_id, "environment": settings.tiktok_environment, "mode": "direct"},
        )
    return await _publish_tiktok_post(
        post_id,
        caption=caption,
        mode="direct",
        privacy_level=privacy_level,
        disable_comment=disable_comment,
        disable_duet=disable_duet,
        disable_stitch=disable_stitch,
    )


async def _publish_tiktok_post(
    post_id: str,
    *,
    caption: Optional[str],
    mode: str,
    privacy_level: Optional[str],
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
) -> Dict[str, Any]:
    post = _load_post_for_tiktok(post_id, mode=mode)
    account = _load_tiktok_account_secret()
    video_bytes, content_type = await _download_video_bytes(str(post["video_url"]))
    video_size = len(video_bytes)

    video_metadata = _load_json_object(post.get("video_metadata"))
    duration_seconds = video_metadata.get("duration_seconds") or video_metadata.get("requested_seconds")
    media_asset = _upsert_media_asset(
        source_url=str(post["video_url"]),
        storage_key=_storage_key_from_url(str(post["video_url"])),
        mime_type=content_type,
        file_size=video_size,
        duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
        status="ready",
    )

    seed_data = _load_json_object(post.get("seed_data"))
    resolved_caption = (caption or post.get("publish_caption") or resolve_selected_caption(seed_data) or post.get("topic_title") or "").strip()
    request_payload: Dict[str, Any] = {
        "post_id": post["id"],
        "caption": resolved_caption,
        "post_mode": mode,
    }
    creator_info: Dict[str, Any] = {}
    if mode == "draft":
        request_payload["source_info"] = {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
        }
    job = _create_publish_job(
        connected_account_id=str(account["id"]),
        media_asset_id=str(media_asset["id"]),
        caption=resolved_caption,
        post_mode=mode,
        request_payload_json=request_payload,
    )
    try:
        if mode == "direct":
            creator_info = await _query_creator_info(account["access_token_plain"])
            _validate_creator_info_for_direct_post(
                creator_info,
                privacy_level=str(privacy_level or DEFAULT_PRIVACY_LEVEL),
                duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
            )
            init_payload = await _initialize_direct_post(
                account["access_token_plain"],
                video_size=video_size,
                caption=resolved_caption,
                privacy_level=str(privacy_level or DEFAULT_PRIVACY_LEVEL),
                disable_comment=disable_comment,
                disable_duet=disable_duet,
                disable_stitch=disable_stitch,
            )
            request_payload = {
                **request_payload,
                "post_info": _build_tiktok_post_info(
                    caption=resolved_caption,
                    privacy_level=str(privacy_level or DEFAULT_PRIVACY_LEVEL),
                    disable_comment=disable_comment,
                    disable_duet=disable_duet,
                    disable_stitch=disable_stitch,
                ),
                "creator_info": creator_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": init_payload["chunk_size"],
                    "total_chunk_count": init_payload["total_chunk_count"],
                },
            }
        else:
            init_payload = await _initialize_inbox_video_upload(account["access_token_plain"], video_size)
            request_payload = {
                **request_payload,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": init_payload["chunk_size"],
                    "total_chunk_count": init_payload["total_chunk_count"],
                },
            }
        if mode == "direct":
            _update_publish_job(
                str(job["id"]),
                {"request_payload_json": redact_secret_payload(request_payload)},
            )
        await _upload_video_chunks(
            str(init_payload["upload_url"]),
            video_bytes,
            content_type,
            int(init_payload["chunk_size"]),
            int(init_payload["total_chunk_count"]),
        )
        status_payload = await _poll_publish_status(
            account["access_token_plain"],
            str(init_payload["publish_id"]),
            post_mode=mode,
        )
        provider_status = str(status_payload.get("status") or "PROCESSING_UPLOAD").upper()
        fail_reason = str(status_payload.get("fail_reason") or "")
        provider_post_ids = status_payload.get("publicaly_available_post_id") or []
        provider_post_id = str(provider_post_ids[0]) if provider_post_ids else None
        local_job_status = _map_tiktok_publish_job_status(provider_status, mode)
        local_result_status = _map_tiktok_result_status(provider_status, mode)
        updated_job = _update_publish_job(
            str(job["id"]),
            {
                "status": local_job_status,
                "tiktok_publish_id": init_payload.get("publish_id"),
                "response_payload_json": redact_secret_payload(
                    {
                        "publish_id": init_payload.get("publish_id"),
                        "chunk_size": init_payload.get("chunk_size"),
                        "total_chunk_count": init_payload.get("total_chunk_count"),
                        "provider_status": provider_status,
                        "fail_reason": fail_reason,
                        "publicaly_available_post_id": provider_post_ids,
                    }
                ),
                "error_message": fail_reason,
                "published_at": datetime.utcnow().isoformat() if local_result_status == "published" else None,
            },
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status=provider_status,
            post_mode=mode,
            provider_post_id=provider_post_id,
            fail_reason=fail_reason,
            error_message=fail_reason,
        )
        logger.info(
            "tiktok_publish_submitted",
            post_id=post["id"],
            publish_job_id=updated_job["id"],
            publish_id=updated_job.get("tiktok_publish_id"),
            post_mode=mode,
            provider_status=provider_status,
        )
        return updated_job
    except ThirdPartyError as exc:
        error_message = exc.message if hasattr(exc, "message") else str(exc)
        mapped_error: Optional[ValidationError] = None
        if mode == "direct" and _is_tiktok_private_post_restriction(exc):
            error_message = (
                "TikTok direct posting is blocked for this account until the creator account is private or the API client is audited. "
                "Use draft upload for this deployment."
            )
            mapped_error = ValidationError(
                error_message,
                details={
                    "post_id": post["id"],
                    "mode": mode,
                    "provider_error": redact_secret_payload(exc.details if isinstance(exc.details, dict) else {}),
                },
            )
        updated_job = _update_publish_job(
            str(job["id"]),
            {
                "status": "failed",
                "response_payload_json": {},
                "error_message": error_message,
            },
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status="FAILED",
            post_mode=mode,
            fail_reason=error_message,
            error_message=error_message,
        )
        raise mapped_error or exc
    except Exception as exc:
        error_message = exc.message if isinstance(exc, (ThirdPartyError, AuthenticationError, ValidationError)) else str(exc)
        updated_job = _update_publish_job(
            str(job["id"]),
            {
                "status": "failed",
                "response_payload_json": {},
                "error_message": error_message,
            },
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status="FAILED",
            post_mode=mode,
            fail_reason=error_message,
            error_message=error_message,
        )
        raise


async def refresh_tiktok_post_status(post_id: str) -> Optional[Dict[str, Any]]:
    """Refresh the TikTok provider status for an existing publish job."""
    post = _load_post_for_tiktok(post_id, mode="direct")
    existing = _load_json_object((_load_json_object(post.get("publish_results"))).get("tiktok"))
    publish_id = str(existing.get("publish_id") or existing.get("remote_id") or "").strip()
    if not publish_id:
        return None

    account = _load_tiktok_account_secret()
    response = get_supabase().client.table("publish_jobs").select("*").eq("tiktok_publish_id", publish_id).limit(1).execute()
    rows = response.data or []
    if not rows:
        return None

    job = dict(rows[0])
    status_payload = await _fetch_publish_status(account["access_token_plain"], publish_id)
    provider_status = str(status_payload.get("status") or existing.get("provider_status") or "").upper()
    fail_reason = str(status_payload.get("fail_reason") or "")
    provider_post_ids = status_payload.get("publicaly_available_post_id") or []
    provider_post_id = str(provider_post_ids[0]) if provider_post_ids else None
    local_job_status = _map_tiktok_publish_job_status(provider_status, str(job.get("post_mode") or "draft"))
    local_result_status = _map_tiktok_result_status(provider_status, str(job.get("post_mode") or "draft"))

    updated_job = _update_publish_job(
        str(job["id"]),
        {
            "status": local_job_status,
            "response_payload_json": redact_secret_payload(status_payload),
            "error_message": fail_reason,
            "published_at": datetime.utcnow().isoformat() if local_result_status == "published" else None,
        },
    )
    _update_post_tiktok_result(
        post,
        updated_job,
        provider_status=provider_status,
        post_mode=str(updated_job.get("post_mode") or "draft"),
        provider_post_id=provider_post_id,
        fail_reason=fail_reason,
        error_message=fail_reason,
    )
    return updated_job


def _sanitize_publish_job(row: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(row)
    sanitized["request_payload_json"] = redact_secret_payload(_load_json_object(sanitized.get("request_payload_json")))
    sanitized["response_payload_json"] = redact_secret_payload(_load_json_object(sanitized.get("response_payload_json")))
    return sanitized


@router.get("/api/tiktok/publish-jobs/{job_id}", response_model=SuccessResponse)
async def get_tiktok_publish_job(job_id: str):
    """Return a persisted TikTok publish job by id."""
    response = get_supabase().client.table("publish_jobs").select("*").eq("id", job_id).execute()
    rows = response.data or []
    if not rows:
        raise NotFoundError("TikTok publish job not found.", details={"job_id": job_id})
    return SuccessResponse(data=TikTokPublishJobResponse(**_sanitize_publish_job(dict(rows[0]))).model_dump())
