"""
Lippe Lift Studio Publish Handlers
FastAPI endpoints for S7_PUBLISH_PLAN state management and Meta publishing.
Per Constitution § II: Validated Boundaries
Per Canon § 3.2: S7_PUBLISH_PLAN → S8_COMPLETE transition
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.adapters.supabase_client import get_supabase
from app.core.config import get_settings
from app.core.errors import (
    ErrorCode,
    FlowForgeException,
    NotFoundError,
    RateLimitError,
    SuccessResponse,
    ThirdPartyError,
    ValidationError,
)
from app.core.states import BatchState
from app.features.publish.schemas import (
    BatchPublishPlanRequest,
    BatchPublishPlanResponse,
    ConfirmPublishRequest,
    ConfirmPublishResponse,
    MetaTargetSelectionRequest,
    PostScheduleRequest,
    PostScheduleResponse,
    PublishResult,
    SocialNetwork,
    SuggestTimesRequest,
    SuggestedTime,
    SuggestTimesResponse,
    PostNowRequest,
    UpdatePostScheduleRequest,
)
from app.features.topics.captions import resolve_selected_caption
try:
    from app.features.publish.tiktok import (
        get_tiktok_public_account,
        get_tiktok_publish_state,
        publish_tiktok_direct_for_post,
        refresh_tiktok_post_status,
        upload_tiktok_draft_for_post,
    )
except ModuleNotFoundError:
    def get_tiktok_public_account() -> Dict[str, Any]:
        """Keep Meta/account-hub startup resilient when TikTok code is not deployed yet."""
        return {"status": "unavailable"}

    async def get_tiktok_publish_state() -> Dict[str, Any]:
        return {"status": "unavailable", "publish_ready": False, "draft_ready": False}

    async def publish_tiktok_direct_for_post(
        post_id: str,
        *,
        caption: Optional[str] = None,
        privacy_level: str,
        disable_comment: bool,
        disable_duet: bool,
        disable_stitch: bool,
    ) -> Dict[str, Any]:
        raise ValidationError("TikTok publishing is unavailable in this deployment.")

    async def refresh_tiktok_post_status(post_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def upload_tiktok_draft_for_post(post_id: str, caption: Optional[str] = None) -> Dict[str, Any]:
        raise ValidationError("TikTok publishing is unavailable in this deployment.")

logger = structlog.get_logger()
router = APIRouter(prefix="/publish", tags=["publish"])

from app.features.publish.arm import router as arm_router
router.include_router(arm_router)

META_GRAPH_BASE = "https://graph.facebook.com/v25.0"
META_IG_BASE = "https://graph.instagram.com/v25.0"
META_OAUTH_URL = "https://www.facebook.com/v25.0/dialog/oauth"
META_TIMEOUT_SECONDS = 30.0
INSTAGRAM_POLL_ATTEMPTS = 45
INSTAGRAM_POLL_SECONDS = 2
META_LOGIN_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "business_management",
    "instagram_basic",
    "instagram_content_publish",
]


def _load_json_object(value: Any) -> Dict[str, Any]:
    """Parse JSONB/string fields defensively into dicts."""
    if not value:
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return deepcopy(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _load_string_list(value: Any) -> List[str]:
    """Normalize a TEXT[]/JSON/string value to a string list."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [value]
    return []


def _is_removed_post(post: Dict[str, Any]) -> bool:
    """Respect existing removed/video-excluded semantics in publish planning."""
    seed_data = _load_json_object(post.get("seed_data"))
    return (
        seed_data.get("script_review_status") == "removed"
        or seed_data.get("video_excluded") is True
    )


def _sanitize_meta_connection(meta_connection: Dict[str, Any]) -> Dict[str, Any]:
    """Strip token material before any value is returned to the browser."""
    if not meta_connection:
        return {}

    readiness = _meta_publish_readiness(meta_connection)
    sanitized = deepcopy(meta_connection)
    sanitized.pop("user_access_token", None)
    sanitized.pop("page_access_token", None)

    for page in sanitized.get("available_pages", []) or []:
        if isinstance(page, dict):
            page.pop("access_token", None)

    selected_page = sanitized.get("selected_page")
    if isinstance(selected_page, dict):
        selected_page.pop("access_token", None)

    return {**sanitized, **readiness}


def _meta_publish_readiness(meta_connection: Dict[str, Any]) -> Dict[str, Any]:
    status = str(meta_connection.get("status") or "disconnected")
    selected_page = meta_connection.get("selected_page") or {}
    selected_instagram = meta_connection.get("selected_instagram") or {}
    available_pages = meta_connection.get("available_pages") or []
    publishable_pages = _get_publishable_meta_pages(available_pages)

    readiness_status = "disconnected"
    readiness_reason = "Connect Meta before scheduling Facebook or Instagram."
    publish_ready = False
    if status == "error":
        readiness_status = "connected_not_publishable"
        readiness_reason = str(meta_connection.get("error") or "Reconnect Meta before publishing.")
    elif status == "connected" and selected_page.get("id") and selected_instagram.get("id"):
        readiness_status = "publish_ready"
        readiness_reason = "Facebook and Instagram are ready to publish from this workspace."
        publish_ready = True
    elif status == "connected" and not publishable_pages:
        readiness_status = "missing_instagram_business"
        readiness_reason = "No manageable Facebook Page with a connected Instagram business account is available."
    elif status == "connected" and not selected_page.get("id"):
        readiness_status = "page_selection_required"
        readiness_reason = "Select the Facebook Page and linked Instagram business account for this workspace."
    elif status == "connected" and not selected_instagram.get("id"):
        readiness_status = "missing_instagram_business"
        readiness_reason = "The selected Facebook Page is missing a linked Instagram business account."

    return {
        "publish_ready": publish_ready,
        "readiness_status": readiness_status,
        "readiness_reason": readiness_reason,
        "publishable_page_count": len(publishable_pages),
    }


def _get_page_instagram_account(page: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Meta's page-linked Instagram shape across login variants."""
    instagram_account = page.get("instagram_business_account")
    if isinstance(instagram_account, dict) and instagram_account.get("id"):
        return instagram_account

    connected_account = page.get("connected_instagram_account")
    if isinstance(connected_account, dict) and connected_account.get("id"):
        return connected_account
    return {}


def _get_publishable_meta_pages(available_pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only Pages that can drive Facebook+Instagram publishing."""
    publishable_pages: List[Dict[str, Any]] = []
    for page in available_pages:
        if not isinstance(page, dict) or not page.get("id"):
            continue
        instagram_account = _get_page_instagram_account(page)
        if not instagram_account.get("id"):
            continue
        publishable_pages.append(page)
    return publishable_pages


def _apply_default_meta_target(meta_connection: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-bind the only valid Meta target so existing connections recover without a forced reconnect."""
    if not meta_connection or meta_connection.get("status") != "connected":
        return meta_connection

    selected_page = meta_connection.get("selected_page") or {}
    selected_instagram = meta_connection.get("selected_instagram") or {}
    if selected_page.get("id") and selected_instagram.get("id"):
        return meta_connection

    publishable_pages = _get_publishable_meta_pages(meta_connection.get("available_pages") or [])
    if len(publishable_pages) != 1:
        return meta_connection

    normalized = deepcopy(meta_connection)
    normalized["selected_page"] = deepcopy(publishable_pages[0])
    normalized["selected_instagram"] = deepcopy(_get_page_instagram_account(publishable_pages[0]))
    return normalized


def _require_meta_settings() -> Any:
    """Validate that standard Meta OAuth settings are present before starting the flow."""
    settings = get_settings()
    missing = [
        name
        for name, value in [
            ("META_APP_ID", settings.meta_app_id),
            ("META_APP_SECRET", settings.meta_app_secret),
            ("META_REDIRECT_URI", settings.meta_redirect_uri),
        ]
        if not value
    ]
    if missing:
        raise ValidationError(
            "Meta OAuth is not configured.",
            details={"missing_env": missing},
        )
    return settings


def _build_meta_state(batch_id: str, secret: str, post_id: Optional[str] = None) -> str:
    """Create a signed state token so the callback can return to the right batch and post."""
    payload = json.dumps(
        {
            "batch_id": batch_id,
            "post_id": post_id,
            "issued_at": datetime.utcnow().isoformat(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_meta_state(state: str, secret: str) -> Dict[str, Any]:
    """Verify the signed state token and recover the batch identifier."""
    if "." not in state:
        raise ValidationError("Invalid Meta OAuth state.")

    encoded, signature = state.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValidationError("Invalid Meta OAuth state signature.")

    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        raise ValidationError("Invalid Meta OAuth state payload.")

    if not payload.get("batch_id"):
        raise ValidationError("Meta OAuth state missing batch id.")
    return payload


def _meta_updated_at(meta_connection: Dict[str, Any]) -> datetime:
    """Sort workspace Meta connections by their last meaningful update."""
    raw = meta_connection.get("updated_at") or meta_connection.get("connected_at")
    if not raw:
        return datetime.min
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def _row_updated_at(row: Dict[str, Any]) -> datetime:
    """Sort generic batch rows by their freshest timestamp."""
    raw = row.get("updated_at") or row.get("created_at")
    if not raw:
        return datetime.min
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


async def _meta_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perform a Meta Graph API request with standard error normalization."""
    async with httpx.AsyncClient(timeout=META_TIMEOUT_SECONDS) as client:
        response = await client.request(method, url, params=params, data=data)

    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text}

    error = payload.get("error") if isinstance(payload, dict) else None
    if response.status_code == 429 or (isinstance(error, dict) and error.get("code") == 4):
        raise RateLimitError(
            message="Meta rate limit exceeded.",
            details={"status_code": response.status_code, "error": error or payload},
        )
    if response.is_error:
        raise ThirdPartyError(
            message="Meta request failed.",
            details={"status_code": response.status_code, "error": error or payload, "url": url},
        )

    if not isinstance(payload, dict):
        raise ThirdPartyError(
            message="Meta request returned an unexpected payload.",
            details={"url": url},
        )
    return payload


async def _exchange_code_for_meta_tokens(code: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Exchange an OAuth code and, when possible, upgrade it to a longer-lived token."""
    settings = _require_meta_settings()

    base_token = await _meta_request(
        "GET",
        f"{META_GRAPH_BASE}/oauth/access_token",
        params={
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "redirect_uri": settings.meta_redirect_uri,
            "code": code,
        },
    )

    long_lived = None
    try:
        long_lived = await _meta_request(
            "GET",
            f"{META_GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": base_token["access_token"],
            },
        )
    except FlowForgeException:
        logger.warning("meta_long_lived_token_exchange_failed")

    token_payload = long_lived or base_token
    expires_in = token_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()

    return token_payload, {
        "access_token": token_payload["access_token"],
        "expires_at": expires_at,
    }


async def _fetch_meta_user_and_pages(user_token: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fetch the connected user plus reachable Pages and connected Instagram accounts."""
    page_fields = ",".join(
        [
            "id",
            "name",
            "tasks",
            "access_token",
            "instagram_business_account{id,username}",
            "connected_instagram_account{id,username}",
        ]
    )
    user = await _meta_request(
        "GET",
        f"{META_GRAPH_BASE}/me",
        params={"fields": "id,name", "access_token": user_token},
    )
    pages_payload = await _meta_request(
        "GET",
        f"{META_GRAPH_BASE}/me/accounts",
        params={
            "fields": page_fields,
            "access_token": user_token,
        },
    )

    async def _fetch_assigned_pages() -> Dict[str, Any]:
        return await _meta_request(
            "GET",
            f"{META_GRAPH_BASE}/me/assigned_pages",
            params={
                "fields": page_fields,
                "access_token": user_token,
            },
        )

    pages_sources = [pages_payload]
    try:
        pages_sources.append(await _fetch_assigned_pages())
    except FlowForgeException as exc:
        logger.info(
            "meta_assigned_pages_lookup_failed",
            error=exc.message,
            details=exc.details,
        )

    available_pages: List[Dict[str, Any]] = []
    seen_page_ids: set[str] = set()
    for payload in pages_sources:
        for page in payload.get("data", []) or []:
            if not isinstance(page, dict):
                continue
            page_id = str(page.get("id", "")).strip()
            if not page_id or page_id in seen_page_ids:
                continue
            seen_page_ids.add(page_id)
            instagram_account = _get_page_instagram_account(page)
            available_pages.append(
                {
                    "id": page_id,
                    "name": page.get("name") or "Untitled Page",
                    "tasks": page.get("tasks") or [],
                    "access_token": page.get("access_token", ""),
                    "instagram_business_account": instagram_account,
                    "connected_instagram_account": page.get("connected_instagram_account"),
                }
            )
    return user, available_pages


def _load_batch(batch_id: str, fields: str = "id,state,meta_connection") -> Dict[str, Any]:
    """Fetch a single batch record for publish operations."""
    supabase = get_supabase().client
    response = supabase.table("batches").select(fields).eq("id", batch_id).execute()
    if not response.data:
        raise NotFoundError("Batch not found", details={"batch_id": batch_id})
    return response.data[0]


def _update_batch_meta_connection(batch_id: str, meta_connection: Dict[str, Any]) -> Dict[str, Any]:
    """Persist Meta connection data on the batch record."""
    supabase = get_supabase().client
    response = supabase.table("batches").update(
        {"meta_connection": meta_connection}
    ).eq("id", batch_id).execute()
    if not response.data:
        raise NotFoundError("Batch not found", details={"batch_id": batch_id})
    return response.data[0]


def _list_batch_rows(fields: str = "id,meta_connection") -> List[Dict[str, Any]]:
    """Load batch rows used to resolve or propagate workspace-wide Meta connection state."""
    supabase = get_supabase().client
    response = supabase.table("batches").select(fields).execute()
    return response.data or []


def _get_workspace_meta_connection(preferred_batch_id: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the current workspace Meta connection for any batch."""
    rows = _list_batch_rows("id,meta_connection")
    candidates: List[Tuple[datetime, bool, Dict[str, Any]]] = []
    for row in rows:
        meta_connection = _load_json_object(row.get("meta_connection"))
        if not meta_connection:
            continue
        candidates.append(
            (
                _meta_updated_at(meta_connection),
                row.get("id") == preferred_batch_id,
                meta_connection,
            )
        )

    connected = [
        candidate for candidate in candidates if candidate[2].get("status") == "connected"
    ]
    if connected:
        connected.sort(key=lambda item: (item[1], item[0]), reverse=True)
        return _apply_default_meta_target(deepcopy(connected[0][2]))

    if candidates:
        candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
        return _apply_default_meta_target(deepcopy(candidates[0][2]))
    return {}


def _effective_meta_connection(batch_id: str, batch_meta_connection: Any) -> Dict[str, Any]:
    """Prefer the live workspace Meta connection over stale batch-local state."""
    local = _apply_default_meta_target(_load_json_object(batch_meta_connection))
    if local.get("status") == "connected":
        return local

    workspace = _get_workspace_meta_connection(preferred_batch_id=batch_id)
    if workspace.get("status") == "connected":
        return workspace
    if local:
        return local
    return workspace


def _resolve_meta_connect_batch_id(batch_id: Optional[str]) -> str:
    """Pick a batch id for navbar-triggered Meta login when no batch is provided."""
    if batch_id:
        _load_batch(batch_id, fields="id")
        return batch_id

    rows = _list_batch_rows("id,updated_at,created_at")
    viable_rows = [row for row in rows if row.get("id")]
    if not viable_rows:
        raise ValidationError("Create a batch before connecting Meta.")

    viable_rows.sort(key=_row_updated_at, reverse=True)
    return str(viable_rows[0]["id"])


def _set_workspace_meta_connection(meta_connection: Dict[str, Any], *, source_batch_id: Optional[str] = None) -> None:
    """Propagate one Meta connection across all existing batches."""
    rows = _list_batch_rows("id")
    batch_ids = [row.get("id") for row in rows if row.get("id")]
    if source_batch_id and source_batch_id not in batch_ids:
        batch_ids.append(source_batch_id)

    for batch_id in batch_ids:
        _update_batch_meta_connection(str(batch_id), deepcopy(meta_connection))


def _get_selected_meta_targets(meta_connection: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return the selected Page and Instagram targets from a stored connection."""
    selected_page = meta_connection.get("selected_page") or {}
    selected_instagram = meta_connection.get("selected_instagram") or {}
    return selected_page, selected_instagram


def _ensure_meta_targets_for_networks(networks: List[str], meta_connection: Dict[str, Any]) -> None:
    """Validate that the batch has the selected targets required for a post's network intent."""
    selected_page, selected_instagram = _get_selected_meta_targets(meta_connection)

    if SocialNetwork.FACEBOOK.value in networks:
        if not selected_page.get("id") or not selected_page.get("access_token"):
            raise ValidationError("Facebook Page target is not selected for this batch.")
    if SocialNetwork.INSTAGRAM.value in networks:
        if not selected_instagram.get("id") or not selected_page.get("access_token"):
            raise ValidationError("Instagram target is not selected for this batch.")


def _resolve_video_url(post: Dict[str, Any]) -> str:
    """Prefer captioned video over raw video when available."""
    metadata = _load_json_object(post.get("video_metadata"))
    captioned_url = metadata.get("caption_video_url")
    if captioned_url:
        return captioned_url
    return post.get("video_url") or ""


def _mp4_url_has_front_moov(url: str) -> bool:
    """Return whether a remote MP4 exposes moov before mdat for Instagram ingestion."""
    if not url.startswith(("http://", "https://")):
        return True
    try:
        response = httpx.get(url, headers={"Range": "bytes=0-65535"}, timeout=10.0, follow_redirects=True)
    except Exception as exc:
        logger.warning("instagram_video_faststart_probe_failed", error=str(exc))
        return True

    if response.status_code not in {200, 206}:
        logger.warning("instagram_video_faststart_probe_unexpected_status", status_code=response.status_code)
        return True

    head = response.content
    moov_index = head.find(b"moov")
    mdat_index = head.find(b"mdat")
    return moov_index >= 0 and (mdat_index < 0 or moov_index < mdat_index)


def _resolve_instagram_video_url(post: Dict[str, Any]) -> str:
    """Use the captioned video unless it is an existing non-faststart MP4."""
    video_url = post.get("video_url") or ""
    metadata = _load_json_object(post.get("video_metadata"))
    captioned_url = metadata.get("caption_video_url")
    raw_url = post.get("raw_video_url") or metadata.get("raw_video_url") or metadata.get("source_video_url")
    if captioned_url and raw_url and video_url == captioned_url and not _mp4_url_has_front_moov(captioned_url):
        logger.warning("instagram_captioned_video_not_faststart_using_raw", raw_video_url=raw_url)
        return str(raw_url)
    return str(video_url)


def _default_publish_caption(post: Dict[str, Any]) -> str:
    """Use stored caption when available, otherwise fall back to the generated seed bundle."""
    caption = (post.get("publish_caption") or "").strip()
    if caption:
        return caption

    seed_data = _load_json_object(post.get("seed_data"))
    return resolve_selected_caption(seed_data)


def _value_caption_publish_guard_enabled() -> bool:
    try:
        return bool(get_settings().value_caption_block_on_publish)
    except Exception:
        return False


def _value_caption_requires_review(post: Dict[str, Any]) -> bool:
    seed_data = _load_json_object(post.get("seed_data"))
    caption_bundle = _load_json_object(seed_data.get("caption_bundle"))
    post_type = str(post.get("post_type") or seed_data.get("post_type") or "").strip().lower()
    if post_type != "value":
        return False
    return bool(
        seed_data.get("caption_review_required")
        or caption_bundle.get("caption_review_required")
    )


def _should_block_value_caption_publish(post: Dict[str, Any]) -> bool:
    return _value_caption_publish_guard_enabled() and _value_caption_requires_review(post)


def get_post_schedules(batch_id: str) -> List[Dict[str, Any]]:
    """Get all publish-planning fields for posts in a batch."""
    supabase = get_supabase()
    response = supabase.client.table("posts").select(
        "id, batch_id, topic_title, seed_data, scheduled_at, publish_caption, social_networks, publish_status, platform_ids, publish_results"
    ).eq("batch_id", batch_id).execute()

    schedules: List[Dict[str, Any]] = []
    for row in response.data:
        if _is_removed_post(row):
            continue
        row = dict(row)
        row["publish_caption"] = _default_publish_caption(row)
        row["publish_results"] = _load_json_object(row.get("publish_results"))
        row["platform_ids"] = _load_json_object(row.get("platform_ids"))
        row["social_networks"] = _load_string_list(row.get("social_networks"))
        schedules.append(row)
    return schedules


def update_post_schedule(
    post_id: str,
    *,
    scheduled_at: Optional[datetime] = None,
    social_networks: Optional[List[str]] = None,
    publish_caption: Optional[str] = None,
) -> Dict[str, Any]:
    """Update schedule/caption state for a single post."""
    supabase = get_supabase()

    update_data: Dict[str, Any] = {}
    if scheduled_at is not None:
        update_data["scheduled_at"] = scheduled_at.isoformat()
    if social_networks is not None:
        update_data["social_networks"] = social_networks
    if publish_caption is not None:
        update_data["publish_caption"] = publish_caption.strip()

    if update_data:
        update_data["publish_status"] = "pending"
        update_data["publish_results"] = {}
        update_data["platform_ids"] = {}

        response = supabase.client.table("posts").update(update_data).eq(
            "id", post_id
        ).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
        return response.data[0]

    return {}


@router.get("/meta/connect")
async def connect_meta_account(batch_id: Optional[str] = None, post_id: Optional[str] = None):
    """Start Instagram Login and remember batch/post return context."""
    batch_id = _resolve_meta_connect_batch_id(batch_id)
    settings = _require_meta_settings()
    state = _build_meta_state(batch_id, settings.meta_app_secret, post_id=post_id)
    query = urlencode(
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": settings.meta_redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(META_LOGIN_SCOPES),
        }
    )
    return RedirectResponse(url=f"{META_OAUTH_URL}?{query}", status_code=302)


@router.get("/meta/status", response_model=SuccessResponse)
async def get_meta_status(batch_id: Optional[str] = None):
    """Return the current workspace Meta connection state for shared UI surfaces."""
    if batch_id:
        batch = _load_batch(batch_id)
        meta_connection = _effective_meta_connection(batch_id, batch.get("meta_connection"))
    else:
        meta_connection = _get_workspace_meta_connection()

    sanitized = _sanitize_meta_connection(meta_connection)
    return SuccessResponse(
        data={
            "meta_connection": sanitized,
            "is_connected": sanitized.get("status") == "connected",
            "has_selected_target": bool(sanitized.get("publish_ready")),
        }
    )


@router.get("/accounts/status", response_model=SuccessResponse)
async def get_accounts_status(batch_id: Optional[str] = None):
    """Return the shared account hub status for all publish providers."""
    if batch_id:
        batch = _load_batch(batch_id)
        meta_connection = _effective_meta_connection(batch_id, batch.get("meta_connection"))
    else:
        meta_connection = _get_workspace_meta_connection()

    sanitized_meta = _sanitize_meta_connection(meta_connection)
    tiktok_connection = await get_tiktok_publish_state()

    return SuccessResponse(
        data={
            "meta_connection": sanitized_meta,
            "tiktok_connection": tiktok_connection,
            "providers": {
                "meta": {
                    "connected": sanitized_meta.get("status") == "connected",
                    "publish_ready": bool(sanitized_meta.get("publish_ready")),
                    "needs_attention": sanitized_meta.get("status") == "error",
                    "readiness_status": sanitized_meta.get("readiness_status"),
                    "readiness_reason": sanitized_meta.get("readiness_reason"),
                },
                "tiktok": {
                    "connected": tiktok_connection.get("status") == "connected",
                    "publish_ready": bool(tiktok_connection.get("publish_ready")),
                    "draft_ready": bool(tiktok_connection.get("draft_ready")),
                    "needs_attention": tiktok_connection.get("status") == "reconnect_required",
                    "readiness_status": tiktok_connection.get("readiness_status"),
                    "readiness_reason": tiktok_connection.get("readiness_reason"),
                },
            },
        }
    )


@router.get("/batches/{batch_id}/meta/connect")
async def connect_batch_meta_account(batch_id: str, post_id: Optional[str] = None):
    """Backwards-compatible wrapper for batch-relative Meta connect links."""
    return await connect_meta_account(batch_id=batch_id, post_id=post_id)


@router.get("/meta/callback")
async def meta_oauth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handle the Instagram Login callback and persist the batch connection."""
    settings = _require_meta_settings()
    if not state:
        raise HTTPException(status_code=400, detail="Missing Meta OAuth state")

    state_payload = _decode_meta_state(state, settings.meta_app_secret)
    batch_id = state_payload["batch_id"]
    post_id = state_payload.get("post_id")
    redirect_target = f"/batches/{batch_id}"
    if post_id:
        redirect_target = f"{redirect_target}#post-{post_id}"

    if error:
        _set_workspace_meta_connection(
            {
                "status": "error",
                "error": error,
                "updated_at": datetime.utcnow().isoformat(),
            },
            source_batch_id=batch_id,
        )
        return RedirectResponse(url=redirect_target, status_code=302)

    if not code:
        raise HTTPException(status_code=400, detail="Missing Meta OAuth code")

    try:
        token_payload, token_meta = await _exchange_code_for_meta_tokens(code)
        user, available_pages = await _fetch_meta_user_and_pages(token_meta["access_token"])
        publishable_pages = _get_publishable_meta_pages(available_pages)
        auto_selected_page: Dict[str, Any] = {}
        auto_selected_instagram: Dict[str, Any] = {}
        if len(publishable_pages) == 1:
            auto_selected_page = deepcopy(publishable_pages[0])
            auto_selected_instagram = deepcopy(_get_page_instagram_account(publishable_pages[0]))
        connection = {
            "status": "connected",
            "connected_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "user": {"id": user.get("id"), "name": user.get("name")},
            "user_access_token": token_meta["access_token"],
            "token_expires_at": token_meta["expires_at"],
            "token_scope_source": "instagram_login",
            "available_pages": available_pages,
            "selected_page": auto_selected_page,
            "selected_instagram": auto_selected_instagram,
            "oauth_debug": {
                "token_type": token_payload.get("token_type"),
                "expires_in": token_payload.get("expires_in"),
            },
        }
        _set_workspace_meta_connection(connection, source_batch_id=batch_id)
        logger.info(
            "meta_connection_created",
            batch_id=batch_id,
            user_id=user.get("id"),
            available_pages=len(available_pages),
        )
    except FlowForgeException as exc:
        _set_workspace_meta_connection(
            {
                "status": "error",
                "error": exc.message,
                "details": exc.details,
                "updated_at": datetime.utcnow().isoformat(),
            },
            source_batch_id=batch_id,
        )

    return RedirectResponse(url=redirect_target, status_code=302)


@router.post("/batches/{batch_id}/meta/select-target", response_model=SuccessResponse)
async def select_meta_target(batch_id: str, request: Request):
    """Select the Page/Instagram pair that this batch should use for publishing."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = MetaTargetSelectionRequest.model_validate(await request.json())
    else:
        form = await request.form()
        payload = MetaTargetSelectionRequest(page_id=str(form.get("page_id", "")).strip())

    batch = _load_batch(batch_id)
    meta_connection = _effective_meta_connection(batch_id, batch.get("meta_connection"))
    available_pages = meta_connection.get("available_pages") or []

    selected_page = None
    for page in available_pages:
        if str(page.get("id")) == payload.page_id:
            selected_page = page
            break

    if not selected_page:
        raise HTTPException(status_code=404, detail="Selected Meta Page is not available for this batch")

    instagram_account = _get_page_instagram_account(selected_page)
    if not instagram_account.get("id"):
        raise HTTPException(status_code=422, detail="Selected Page does not have a connected Instagram business account")

    meta_connection["selected_page"] = selected_page
    meta_connection["selected_instagram"] = instagram_account
    meta_connection["updated_at"] = datetime.utcnow().isoformat()
    _set_workspace_meta_connection(meta_connection, source_batch_id=batch_id)

    logger.info(
        "meta_targets_selected",
        batch_id=batch_id,
        page_id=selected_page.get("id"),
        instagram_id=instagram_account.get("id"),
    )
    return SuccessResponse(data={"batch_id": batch_id, "meta_connection": _sanitize_meta_connection(meta_connection)})


@router.post("/batches/{batch_id}/meta/disconnect", response_model=SuccessResponse)
async def disconnect_meta_account(batch_id: str):
    """Clear the stored Meta connection for the whole workspace."""
    _load_batch(batch_id)
    connection = {
        "status": "disconnected",
        "updated_at": datetime.utcnow().isoformat(),
        "available_pages": [],
        "selected_page": {},
        "selected_instagram": {},
    }
    _set_workspace_meta_connection(connection, source_batch_id=batch_id)
    logger.info("meta_connection_cleared", batch_id=batch_id)
    return SuccessResponse(data={"batch_id": batch_id, "meta_connection": connection})


def _load_post(post_id: str, fields: str = "id,batch_id,video_url") -> Dict[str, Any]:
    """Load a single post row for schedule and dispatch validation."""
    supabase = get_supabase().client
    response = supabase.table("posts").select(fields).eq("id", post_id).execute()
    if not response.data:
        raise NotFoundError("Post not found", details={"post_id": post_id})
    return response.data[0]


@router.post("/posts/{post_id}/schedule", response_model=SuccessResponse)
async def schedule_post(post_id: str, request: PostScheduleRequest):
    """Save the publish plan for a single post."""
    if post_id != request.post_id:
        raise HTTPException(status_code=409, detail="Post id mismatch between route and body")

    post = _load_post(post_id)
    if not post.get("video_url"):
        raise HTTPException(status_code=422, detail="Generate the video before saving a publish schedule.")

    batch = _load_batch(post["batch_id"])
    meta_connection = _effective_meta_connection(post["batch_id"], batch.get("meta_connection"))
    _ensure_meta_targets_for_networks([n.value for n in request.social_networks], meta_connection)

    updated_post = update_post_schedule(
        post_id=post_id,
        scheduled_at=request.scheduled_at,
        social_networks=[n.value for n in request.social_networks],
        publish_caption=request.publish_caption,
    )

    logger.info(
        "post_publish_plan_saved",
        post_id=post_id,
        scheduled_at=updated_post.get("scheduled_at"),
        social_networks=updated_post.get("social_networks"),
    )
    return SuccessResponse(
        data={
            "post_id": post_id,
            "scheduled_at": updated_post.get("scheduled_at"),
            "publish_caption": updated_post.get("publish_caption"),
            "social_networks": updated_post.get("social_networks"),
            "publish_status": updated_post.get("publish_status"),
        }
    )


@router.put("/posts/{post_id}/schedule", response_model=SuccessResponse)
async def update_schedule(post_id: str, request: UpdatePostScheduleRequest):
    """Update the publish plan for a single post."""
    social_networks = None
    if request.social_networks is not None:
        social_networks = [n.value for n in request.social_networks]

    post = _load_post(post_id, fields="id,batch_id,video_url,social_networks")
    networks_to_validate = social_networks or _load_string_list(post.get("social_networks"))
    if networks_to_validate:
        if not post.get("video_url"):
            raise HTTPException(status_code=422, detail="Generate the video before saving a publish schedule.")
        batch = _load_batch(post["batch_id"])
        meta_connection = _effective_meta_connection(post["batch_id"], batch.get("meta_connection"))
        _ensure_meta_targets_for_networks(networks_to_validate, meta_connection)

    updated_post = update_post_schedule(
        post_id=post_id,
        scheduled_at=request.scheduled_at,
        social_networks=social_networks,
        publish_caption=request.publish_caption,
    )
    return SuccessResponse(data={"post": updated_post})


@router.post("/batches/{batch_id}/plan", response_model=SuccessResponse)
async def set_batch_publish_plan(batch_id: str, request: BatchPublishPlanRequest):
    """Set publish plan for every active post in the batch."""
    batch = _load_batch(batch_id)
    if batch.get("state") != BatchState.S7_PUBLISH_PLAN.value:
        raise HTTPException(
            status_code=409,
            detail=f"Batch must be in S7_PUBLISH_PLAN state (current: {batch.get('state')})",
        )

    updated_count = 0
    for schedule in request.schedules:
        update_post_schedule(
            post_id=schedule.post_id,
            scheduled_at=schedule.scheduled_at,
            social_networks=[n.value for n in schedule.social_networks],
            publish_caption=schedule.publish_caption,
        )
        updated_count += 1

    logger.info("batch_publish_plan_set", batch_id=batch_id, updated_count=updated_count)
    return SuccessResponse(
        data={
            "batch_id": batch_id,
            "updated_count": updated_count,
            "message": f"Publish plan set for {updated_count} posts",
        }
    )


@router.get("/batches/{batch_id}/plan", response_model=BatchPublishPlanResponse)
async def get_batch_publish_plan(batch_id: str):
    """Return the current publish planning state for the batch."""
    try:
        schedules = get_post_schedules(batch_id)
        scheduled_count = sum(1 for schedule in schedules if schedule.get("scheduled_at"))
        pending_count = len(schedules) - scheduled_count
        return BatchPublishPlanResponse(
            batch_id=batch_id,
            total_posts=len(schedules),
            scheduled_posts=scheduled_count,
            pending_posts=pending_count,
            schedules=[
                PostScheduleResponse(
                    post_id=schedule["id"],
                    topic_title=schedule.get("topic_title", "Untitled"),
                    scheduled_at=schedule.get("scheduled_at"),
                    publish_caption=schedule.get("publish_caption") or "",
                    social_networks=schedule.get("social_networks", []),
                    publish_status=schedule.get("publish_status", "pending"),
                    platform_ids=schedule.get("platform_ids"),
                    publish_results=schedule.get("publish_results", {}),
                )
                for schedule in schedules
            ],
        )
    except Exception as exc:
        logger.error("get_batch_publish_plan_failed", batch_id=batch_id, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/batches/{batch_id}/suggest-times", response_model=SuggestTimesResponse)
async def suggest_publish_times(batch_id: str, request: SuggestTimesRequest):
    """Suggest peak times with the existing deterministic heuristic."""
    try:
        schedules = get_post_schedules(batch_id)
        num_posts = len(schedules)
        peak_hours = [12, 15, 18, 20]

        start_date = request.start_date or (datetime.utcnow() + timedelta(days=1))
        berlin_tz = ZoneInfo(request.timezone)

        suggestions = []
        current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        for index in range(num_posts):
            hour = peak_hours[index % len(peak_hours)]
            local_dt = current_date.replace(hour=hour, minute=0, tzinfo=berlin_tz)
            utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            suggestions.append(
                SuggestedTime(
                    datetime_utc=utc_dt,
                    datetime_local=local_dt.strftime("%Y-%m-%d %H:%M %Z"),
                    reason=f"Peak engagement time ({hour}:00 {request.timezone})",
                )
            )
            if (index + 1) % len(peak_hours) == 0:
                current_date += timedelta(days=1)

        return SuggestTimesResponse(suggestions=suggestions, timezone=request.timezone)
    except Exception as exc:
        logger.error("suggest_publish_times_failed", batch_id=batch_id, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/batches/{batch_id}/confirm", response_model=ConfirmPublishResponse)
async def confirm_publish(
    batch_id: str,
    http_request: Request,
):
    """Arm dispatch for all active posts in the batch without faking any publish result."""
    request = await _resolve_confirm_publish_request(http_request, batch_id)
    if batch_id != request.batch_id:
        raise HTTPException(status_code=409, detail="Batch id mismatch between route and body")

    batch = _load_batch(batch_id)
    if batch.get("state") != BatchState.S7_PUBLISH_PLAN.value:
        raise HTTPException(
            status_code=409,
            detail=f"Batch must be in S7_PUBLISH_PLAN state (current: {batch.get('state')})",
        )

    meta_connection = _effective_meta_connection(batch_id, batch.get("meta_connection"))
    schedules = get_post_schedules(batch_id)
    if not schedules:
        raise HTTPException(status_code=400, detail="No active posts are available for publish scheduling")

    unscheduled: List[str] = []
    for schedule in schedules:
        networks = _load_string_list(schedule.get("social_networks"))
        caption = (schedule.get("publish_caption") or "").strip()
        if not schedule.get("scheduled_at") or not networks or not caption:
            unscheduled.append(schedule["id"])
            continue
        _ensure_meta_targets_for_networks(networks, meta_connection)

    if unscheduled:
        raise HTTPException(
            status_code=400,
            detail=f"{len(unscheduled)} active posts are missing schedule, caption, or network selection",
        )

    supabase = get_supabase().client
    results: List[PublishResult] = []
    for schedule in schedules:
        supabase.table("posts").update(
            {"publish_status": "scheduled", "publish_results": {}, "platform_ids": {}}
        ).eq("id", schedule["id"]).execute()
        results.append(PublishResult(post_id=schedule["id"], success=True))

    logger.info(
        "batch_publish_dispatch_armed",
        batch_id=batch_id,
        scheduled_posts=len(results),
        selected_page=(meta_connection.get("selected_page") or {}).get("id"),
        selected_instagram=(meta_connection.get("selected_instagram") or {}).get("id"),
    )
    return ConfirmPublishResponse(
        batch_id=batch_id,
        total_posts=len(results),
        published_count=0,
        failed_count=0,
        results=results,
    )


def _network_attempt_count(result: Dict[str, Any]) -> int:
    """Count attempts for a network result object."""
    return int(result.get("attempt_count") or 0) + 1


async def _publish_facebook_video(post: Dict[str, Any], meta_connection: Dict[str, Any]) -> str:
    """Publish a video post to the selected Facebook Page."""
    selected_page, _ = _get_selected_meta_targets(meta_connection)
    page_id = selected_page.get("id")
    page_token = selected_page.get("access_token")
    if not page_id or not page_token:
        raise ValidationError("Facebook Page token is unavailable for this batch.")
    if not post.get("video_url"):
        raise ValidationError("Post has no video_url for Facebook publishing.")

    payload = await _meta_request(
        "POST",
        f"{META_GRAPH_BASE}/{page_id}/videos",
        data={
            "file_url": post["video_url"],
            "description": post["publish_caption"],
            "published": "true",
            "access_token": page_token,
        },
    )
    remote_id = payload.get("id")
    if not remote_id:
        raise ThirdPartyError("Facebook did not return a video id.", details={"payload": payload})
    return str(remote_id)


async def _wait_for_instagram_container(container_id: str, page_token: str) -> None:
    """Poll a container until Instagram says it is ready to publish."""
    last_status_code = "UNKNOWN"
    for _ in range(INSTAGRAM_POLL_ATTEMPTS):
        payload = await _meta_request(
            "GET",
            f"{META_GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": page_token},
        )
        status_code = str(payload.get("status_code", "")).upper()
        last_status_code = status_code or "UNKNOWN"
        if status_code in {"FINISHED", "PUBLISHED"}:
            return
        if status_code in {"ERROR", "EXPIRED"}:
            raise ThirdPartyError(
                "Instagram media container failed before publish.",
                details={"container_id": container_id, "status_code": status_code},
            )
        await asyncio.sleep(INSTAGRAM_POLL_SECONDS)

    raise ThirdPartyError(
        "Instagram media container did not become publishable in time.",
        details={
            "container_id": container_id,
            "status_code": last_status_code,
            "poll_attempts": INSTAGRAM_POLL_ATTEMPTS,
            "poll_seconds": INSTAGRAM_POLL_SECONDS,
        },
    )


async def _publish_instagram_reel(post: Dict[str, Any], meta_connection: Dict[str, Any]) -> str:
    """Publish a reel to the selected Instagram business account."""
    selected_page, selected_instagram = _get_selected_meta_targets(meta_connection)
    ig_id = selected_instagram.get("id")
    page_token = selected_page.get("access_token")
    video_url = _resolve_instagram_video_url(post)
    if not ig_id or not page_token:
        raise ValidationError("Instagram target is unavailable for this batch.")
    if not video_url:
        raise ValidationError("Post has no video_url for Instagram publishing.")

    container = await _meta_request(
        "POST",
        f"{META_GRAPH_BASE}/{ig_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": post["publish_caption"],
            "share_to_feed": "true",
            "access_token": page_token,
        },
    )
    container_id = container.get("id")
    if not container_id:
        raise ThirdPartyError("Instagram did not return a media container id.", details={"payload": container})

    await _wait_for_instagram_container(str(container_id), page_token)
    published = await _meta_request(
        "POST",
        f"{META_GRAPH_BASE}/{ig_id}/media_publish",
        data={"creation_id": container_id, "access_token": page_token},
    )
    remote_id = published.get("id")
    if not remote_id:
        raise ThirdPartyError("Instagram did not return a published media id.", details={"payload": published})
    return str(remote_id)


def _derive_publish_status(networks: List[str], publish_results: Dict[str, Any]) -> str:
    """Collapse per-network publish results to the existing post-level publish status."""
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


def _default_tiktok_privacy_level(tiktok_connection: Dict[str, Any]) -> str:
    creator_info = tiktok_connection.get("creator_info") or {}
    privacy_options = [str(item) for item in creator_info.get("privacy_level_options") or [] if item]
    if "SELF_ONLY" in privacy_options:
        return "SELF_ONLY"
    if privacy_options:
        return privacy_options[0]
    return "SELF_ONLY"


def _coerce_publish_confirm(value: Any, *, default: bool = True) -> bool:
    """Parse booleans from JSON/form payloads without depending on transport details."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _tiktok_job_result_status(tiktok_job: Dict[str, Any]) -> str:
    """Map TikTok job/provider completion into the post-level status used by the UI."""
    tiktok_payload = _load_json_object(tiktok_job.get("response_payload_json"))
    provider_status = str(tiktok_payload.get("provider_status") or tiktok_job.get("status") or "").upper()
    if tiktok_job.get("status") == "failed" or provider_status == "FAILED":
        return "failed"
    if provider_status == "SEND_TO_USER_INBOX":
        return "awaiting_user_action"
    if tiktok_job.get("status") == "published" or provider_status == "PUBLISH_COMPLETE":
        return "published"
    return "publishing"


async def _resolve_confirm_publish_request(
    http_request: Request,
    batch_id: str,
) -> ConfirmPublishRequest:
    """Accept both JSON API clients and HTMX form posts for publish arming."""
    content_type = (http_request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await http_request.json()
        if isinstance(payload, dict):
            return ConfirmPublishRequest(
                batch_id=str(payload.get("batch_id") or batch_id),
                confirm=_coerce_publish_confirm(payload.get("confirm"), default=True),
            )

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await http_request.form()
        return ConfirmPublishRequest(
            batch_id=str(form.get("batch_id") or batch_id),
            confirm=_coerce_publish_confirm(form.get("confirm"), default=True),
        )

    return ConfirmPublishRequest(batch_id=batch_id, confirm=True)


async def _reconcile_inflight_tiktok_posts(limit: int = 20) -> List[str]:
    supabase = get_supabase().client
    response = supabase.table("posts").select(
        "id,batch_id,publish_status,publish_results,seed_data"
    ).eq("publish_status", "publishing").limit(limit).execute()
    touched_batches: List[str] = []
    for row in response.data or []:
        if _is_removed_post(row):
            continue
        tiktok_result = _load_json_object(_load_json_object(row.get("publish_results")).get("tiktok"))
        if not tiktok_result:
            continue
        if str(tiktok_result.get("provider_status") or "").upper() in {"PUBLISH_COMPLETE", "FAILED"}:
            continue
        try:
            refreshed = await refresh_tiktok_post_status(str(row["id"]))
            if refreshed:
                touched_batches.append(str(row["batch_id"]))
        except FlowForgeException as exc:
            logger.warning("tiktok_publish_status_refresh_failed", post_id=row.get("id"), error=exc.message)
        except Exception as exc:
            logger.warning("tiktok_publish_status_refresh_failed", post_id=row.get("id"), error=str(exc))
    return touched_batches


def _terminal_batch_post(post: Dict[str, Any]) -> bool:
    """Determine whether an active post is in a terminal publish state for batch completion."""
    if _is_removed_post(post):
        return True

    networks = _load_string_list(post.get("social_networks"))
    if not networks:
        return True
    return str(post.get("publish_status")) in {"published", "failed"}


def _reconcile_completed_batches(batch_ids: List[str]) -> None:
    """Advance batches to S8_COMPLETE once all active scheduled posts are terminal."""
    if not batch_ids:
        return

    supabase = get_supabase().client
    for batch_id in set(batch_ids):
        batch = _load_batch(batch_id, fields="id,state")
        if batch.get("state") != BatchState.S7_PUBLISH_PLAN.value:
            continue

        posts_response = supabase.table("posts").select(
            "id, seed_data, social_networks, publish_status"
        ).eq("batch_id", batch_id).execute()
        posts = posts_response.data or []
        active_posts = [post for post in posts if not _is_removed_post(post)]
        if active_posts and all(_terminal_batch_post(post) for post in active_posts):
            supabase.table("batches").update(
                {"state": BatchState.S8_COMPLETE.value, "updated_at": datetime.utcnow().isoformat()}
            ).eq("id", batch_id).execute()
            logger.info("batch_meta_publish_completed", batch_id=batch_id, posts=len(active_posts))


async def dispatch_due_posts(limit: int = 10, *, trigger: str = "scheduler") -> Dict[str, Any]:
    """Dispatch due posts and persist per-network outcomes."""
    inflight_batches = await _reconcile_inflight_tiktok_posts()
    now = datetime.utcnow().isoformat()
    supabase = get_supabase().client
    due_response = supabase.table("posts").select(
        "id, batch_id, post_type, video_url, video_metadata, seed_data, scheduled_at, publish_caption, social_networks, publish_status, publish_results, platform_ids"
    ).eq("publish_status", "scheduled").lte("scheduled_at", now).order("scheduled_at").limit(limit).execute()

    due_posts = due_response.data or []
    processed = 0
    published = 0
    failed = 0
    touched_batches: List[str] = list(inflight_batches)

    for due_post in due_posts:
        if _is_removed_post(due_post):
            continue

        if _should_block_value_caption_publish(due_post):
            supabase.table("posts").update(
                {
                    "publish_results": {
                        **_load_json_object(due_post.get("publish_results")),
                        "dispatch": {
                            "status": "blocked",
                            "error_code": ErrorCode.VALIDATION_ERROR.value,
                            "error_message": "Value caption requires review before publish dispatch.",
                            "details": {"caption_review_required": True},
                            "last_attempt_at": datetime.utcnow().isoformat(),
                        },
                    },
                }
            ).eq("id", due_post["id"]).execute()
            processed += 1
            failed += 1
            continue

        claim = supabase.table("posts").update({"publish_status": "publishing"}).eq(
            "id", due_post["id"]
        ).eq("publish_status", "scheduled").execute()
        if not claim.data:
            continue

        post = dict(claim.data[0])
        post["publish_caption"] = _default_publish_caption(post)
        post["raw_video_url"] = post.get("video_url") or ""
        post["video_url"] = _resolve_video_url(post)
        post["social_networks"] = _load_string_list(post.get("social_networks"))
        publish_results = _load_json_object(post.get("publish_results"))
        platform_ids = _load_json_object(post.get("platform_ids"))
        batch = _load_batch(post["batch_id"], fields="id,meta_connection,state")
        meta_connection = _effective_meta_connection(post["batch_id"], batch.get("meta_connection"))
        tiktok_connection = await get_tiktok_publish_state()

        try:
            _ensure_meta_targets_for_networks(post["social_networks"], meta_connection)

            for network in post["social_networks"]:
                existing = _load_json_object(publish_results.get(network))
                if existing.get("status") == "published":
                    continue

                attempt_count = _network_attempt_count(existing)
                try:
                    if network == SocialNetwork.FACEBOOK.value:
                        remote_id = await _publish_facebook_video(post, meta_connection)
                    elif network == SocialNetwork.INSTAGRAM.value:
                        remote_id = await _publish_instagram_reel(post, meta_connection)
                    elif network == SocialNetwork.TIKTOK.value:
                        tiktok_job = await upload_tiktok_draft_for_post(
                            post["id"],
                            caption=post["publish_caption"],
                        )
                        tiktok_payload = _load_json_object(tiktok_job.get("response_payload_json"))
                        provider_post_ids = tiktok_payload.get("publicaly_available_post_id") or []
                        remote_id = str(provider_post_ids[0]) if provider_post_ids else str(tiktok_job.get("tiktok_publish_id") or tiktok_job.get("id"))
                        provider_status = str(tiktok_payload.get("provider_status") or tiktok_job.get("status") or "").upper()
                        publish_results[network] = {
                            "status": _tiktok_job_result_status(tiktok_job),
                            "post_mode": "draft",
                            "provider_status": provider_status,
                            "publish_id": tiktok_job.get("tiktok_publish_id"),
                            "remote_id": remote_id,
                            "post_id": str(provider_post_ids[0]) if provider_post_ids else None,
                            "fail_reason": tiktok_payload.get("fail_reason"),
                            "error_message": tiktok_job.get("error_message") or "",
                            "published_at": datetime.utcnow().isoformat() if _tiktok_job_result_status(tiktok_job) == "published" else None,
                            "last_attempt_at": datetime.utcnow().isoformat(),
                            "attempt_count": attempt_count,
                        }
                        if _tiktok_job_result_status(tiktok_job) == "published" and provider_post_ids:
                            platform_ids[network] = str(provider_post_ids[0])
                        continue
                    else:
                        raise ValidationError(
                            f"{network} publishing is not supported by this publish slice.",
                            details={"network": network},
                        )

                    publish_results[network] = {
                        "status": "published",
                        "remote_id": remote_id,
                        "published_at": datetime.utcnow().isoformat(),
                        "last_attempt_at": datetime.utcnow().isoformat(),
                        "attempt_count": attempt_count,
                    }
                    platform_ids[network] = remote_id
                except FlowForgeException as exc:
                    publish_results[network] = {
                        "status": "failed",
                        "error_code": exc.code.value,
                        "error_message": exc.message,
                        "details": exc.details,
                        "last_attempt_at": datetime.utcnow().isoformat(),
                        "attempt_count": attempt_count,
                    }
                except Exception as exc:
                    publish_results[network] = {
                        "status": "failed",
                        "error_code": ErrorCode.INTERNAL_ERROR.value,
                        "error_message": str(exc),
                        "last_attempt_at": datetime.utcnow().isoformat(),
                        "attempt_count": attempt_count,
                    }

            overall_status = _derive_publish_status(post["social_networks"], publish_results)
            supabase.table("posts").update(
                {
                    "publish_status": overall_status,
                    "publish_results": publish_results,
                    "platform_ids": platform_ids,
                }
            ).eq("id", post["id"]).execute()

            processed += 1
            published += int(overall_status == "published")
            failed += int(overall_status == "failed")
            touched_batches.append(post["batch_id"])
            logger.info(
                "meta_due_post_dispatched",
                trigger=trigger,
                post_id=post["id"],
                batch_id=post["batch_id"],
                publish_status=overall_status,
            )
        except FlowForgeException as exc:
            supabase.table("posts").update(
                {
                    "publish_status": "failed",
                    "publish_results": {
                        **publish_results,
                        "dispatch": {
                            "status": "failed",
                            "error_code": exc.code.value,
                            "error_message": exc.message,
                            "details": exc.details,
                            "last_attempt_at": datetime.utcnow().isoformat(),
                        },
                    },
                }
            ).eq("id", post["id"]).execute()
            processed += 1
            failed += 1
            touched_batches.append(post["batch_id"])

    _reconcile_completed_batches(touched_batches)
    return {
        "processed": processed,
        "published": published,
        "failed": failed,
        "trigger": trigger,
        "checked_at": datetime.utcnow().isoformat(),
    }


async def run_scheduled_publish_job() -> Dict[str, Any]:
    """Entry point used by the in-process scheduler in app lifespan."""
    try:
        result = await dispatch_due_posts(trigger="apscheduler")
        logger.info("meta_publish_scheduler_tick", **result)
        return result
    except Exception as exc:
        logger.exception("meta_publish_scheduler_failed", error=str(exc))
        return {"processed": 0, "published": 0, "failed": 0, "error": str(exc)}


@router.post("/cron/dispatch", response_model=SuccessResponse)
async def cron_dispatch_publish(request: Request):
    """Cron-compatible endpoint for dispatching due Meta posts."""
    settings = get_settings()
    authorization = request.headers.get("authorization")
    if not authorization or authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await dispatch_due_posts(trigger="cron")
    return SuccessResponse(data=result)


async def publish_post_now(post_id: str, social_networks: List[str], *, publish_caption: str | None = None) -> Dict[str, Any]:
    """Immediately dispatch a single post to its selected networks.

    Guards:
    - Post must exist with publish_status in ('draft', 'scheduled')
    - Post's batch must be in S7_PUBLISH_PLAN
    """
    supabase = get_supabase().client

    # 1. Load post and validate status
    post_resp = supabase.table("posts").select(
        "id, batch_id, post_type, video_url, video_metadata, seed_data, scheduled_at, publish_caption, social_networks, publish_status, publish_results, platform_ids"
    ).eq("id", post_id).execute()
    if not post_resp.data:
        raise NotFoundError(f"Post {post_id} not found")
    post = post_resp.data[0]

    if _is_removed_post(post):
        raise ValidationError("Post has been removed", details={"post_id": post_id})

    if post["publish_status"] not in ("pending", "draft", "scheduled"):
        raise ValidationError(
            f"Post must be in pending, draft, or scheduled status, got {post['publish_status']}",
            details={"publish_status": post["publish_status"]},
        )

    if _should_block_value_caption_publish(post):
        raise ValidationError(
            "Value caption requires review before publish dispatch.",
            details={"post_id": post_id, "caption_review_required": True},
        )

    # 2. Validate batch state
    batch = _load_batch(post["batch_id"], fields="id,state,meta_connection")
    if batch.get("state") != BatchState.S7_PUBLISH_PLAN.value:
        raise ValidationError(
            f"Batch must be in S7_PUBLISH_PLAN state, got {batch.get('state')}",
            details={"batch_state": batch.get("state")},
        )

    # 3. Validate networks selected
    if not social_networks:
        raise ValidationError(
            "No social networks selected. Please select at least one network (IG, FB, or TT) before publishing.",
            details={"social_networks": social_networks},
        )

    # 4. Validate Meta connection for Meta networks
    meta_networks = [n for n in social_networks if n in (SocialNetwork.FACEBOOK.value, SocialNetwork.INSTAGRAM.value)]
    if meta_networks:
        meta_connection = _effective_meta_connection(post["batch_id"], batch.get("meta_connection"))
        selected_page = (meta_connection.get("selected_page") or {})
        if not selected_page.get("id") or not selected_page.get("access_token"):
            raise ValidationError(
                "Facebook/Instagram is not connected. Please connect a Meta account before publishing.",
                details={"missing": "meta_connection", "networks": meta_networks},
            )

    # 5. Optimistic lock
    claim = supabase.table("posts").update({"publish_status": "publishing"}).eq(
        "id", post_id
    ).eq("publish_status", post["publish_status"]).execute()
    if not claim.data:
        raise ValidationError("Post status changed concurrently, please retry")

    post = dict(claim.data[0])
    post["publish_caption"] = publish_caption if publish_caption else _default_publish_caption(post)
    post["raw_video_url"] = post.get("video_url") or ""
    post["video_url"] = _resolve_video_url(post)
    post["social_networks"] = social_networks
    publish_results = _load_json_object(post.get("publish_results"))
    platform_ids = _load_json_object(post.get("platform_ids"))
    try:
        meta_connection = _effective_meta_connection(post["batch_id"], batch.get("meta_connection"))
        tiktok_connection = await get_tiktok_publish_state()

        # 4. Dispatch to each network (reuse existing per-network functions)
        meta_networks = [n for n in social_networks if n in (SocialNetwork.FACEBOOK.value, SocialNetwork.INSTAGRAM.value)]
        if meta_networks:
            _ensure_meta_targets_for_networks(meta_networks, meta_connection)

        for network in social_networks:
            attempt_count = _network_attempt_count(_load_json_object(publish_results.get(network)))
            try:
                if network == SocialNetwork.FACEBOOK.value:
                    remote_id = await _publish_facebook_video(post, meta_connection)
                elif network == SocialNetwork.INSTAGRAM.value:
                    remote_id = await _publish_instagram_reel(post, meta_connection)
                elif network == SocialNetwork.TIKTOK.value:
                    tiktok_job = await upload_tiktok_draft_for_post(
                        post["id"],
                        caption=post["publish_caption"],
                    )
                    tiktok_payload = _load_json_object(tiktok_job.get("response_payload_json"))
                    provider_post_ids = tiktok_payload.get("publicaly_available_post_id") or []
                    remote_id = str(provider_post_ids[0]) if provider_post_ids else str(tiktok_job.get("tiktok_publish_id") or tiktok_job.get("id"))
                    provider_status = str(tiktok_payload.get("provider_status") or tiktok_job.get("status") or "").upper()
                    post_mode = "draft"
                    publish_results[network] = {
                        "status": _tiktok_job_result_status(tiktok_job),
                        "post_mode": post_mode,
                        "provider_status": provider_status,
                        "publish_id": tiktok_job.get("tiktok_publish_id"),
                        "remote_id": remote_id,
                        "post_id": str(provider_post_ids[0]) if provider_post_ids else None,
                        "fail_reason": tiktok_payload.get("fail_reason"),
                        "error_message": tiktok_job.get("error_message") or "",
                        "published_at": datetime.utcnow().isoformat() if _tiktok_job_result_status(tiktok_job) == "published" else None,
                        "last_attempt_at": datetime.utcnow().isoformat(),
                        "attempt_count": attempt_count,
                    }
                    if _tiktok_job_result_status(tiktok_job) == "published" and provider_post_ids:
                        platform_ids[network] = str(provider_post_ids[0])
                    continue
                else:
                    raise ValidationError(f"{network} publishing is not supported.")

                publish_results[network] = {
                    "status": "published",
                    "remote_id": remote_id,
                    "published_at": datetime.utcnow().isoformat(),
                    "last_attempt_at": datetime.utcnow().isoformat(),
                    "attempt_count": attempt_count,
                }
                platform_ids[network] = remote_id
            except FlowForgeException as exc:
                publish_results[network] = {
                    "status": "failed",
                    "error_code": exc.code.value,
                    "error_message": exc.message,
                    "details": exc.details,
                    "last_attempt_at": datetime.utcnow().isoformat(),
                    "attempt_count": attempt_count,
                }
            except Exception as exc:
                publish_results[network] = {
                    "status": "failed",
                    "error_code": ErrorCode.INTERNAL_ERROR.value,
                    "error_message": str(exc),
                    "last_attempt_at": datetime.utcnow().isoformat(),
                    "attempt_count": attempt_count,
                }
    except FlowForgeException as exc:
        supabase.table("posts").update({
            "publish_status": "failed",
            "publish_results": {
                **publish_results,
                "dispatch": {
                    "status": "failed",
                    "error_code": exc.code.value,
                    "error_message": exc.message,
                    "details": exc.details,
                    "last_attempt_at": datetime.utcnow().isoformat(),
                },
            },
        }).eq("id", post_id).execute()
        _reconcile_completed_batches([post["batch_id"]])
        raise

    overall_status = _derive_publish_status(social_networks, publish_results)
    supabase.table("posts").update({
        "publish_status": overall_status,
        "publish_results": publish_results,
        "platform_ids": platform_ids,
    }).eq("id", post_id).execute()

    # Check batch completion
    _reconcile_completed_batches([post["batch_id"]])

    logger.info("post_now_dispatched", post_id=post_id, publish_status=overall_status)
    return {
        "post_id": post_id,
        "publish_status": overall_status,
        "publish_results": publish_results,
        "platform_ids": platform_ids,
    }


@router.post("/posts/{post_id}/now", response_model=SuccessResponse)
async def handle_publish_post_now(post_id: str, request: PostNowRequest):
    """Immediately publish a single post to selected networks."""
    result = await publish_post_now(
        post_id,
        [n.value for n in request.social_networks],
        publish_caption=request.publish_caption,
    )
    return SuccessResponse(data=result)
