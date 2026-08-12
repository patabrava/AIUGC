"""Batch-level arm dispatch handler for S7_PUBLISH_PLAN."""

from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from postgrest.exceptions import APIError
from zoneinfo import ZoneInfo

from app.adapters.supabase_client import get_supabase
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.publish.schemas import BatchArmRequest, TikTokPostSettings

log = get_logger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")
DAY_OFFSETS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

router = APIRouter()


def _validate_tiktok_settings_present(posts: List[Dict[str, Any]]) -> None:
    """Reject arming when any TikTok-targeted post lacks complete TikTok settings."""
    missing: List[str] = []
    for post in posts:
        networks = post.get("social_networks") or []
        if "tiktok" not in networks:
            continue
        settings = post.get("tiktok_settings") or {}
        if not settings:
            missing.append(str(post.get("id")))
            continue
        try:
            TikTokPostSettings(**settings)
        except Exception:
            missing.append(str(post.get("id")))
    if missing:
        raise ValidationError(
            "TikTok settings are required for every post that targets TikTok before arming the batch.",
            details={"posts_missing_tiktok_settings": missing},
        )


def _compute_scheduled_at(week_start: str, day: str, time: str) -> str:
    """Compute UTC ISO timestamp from week_start date + day + time in Europe/Berlin."""
    base = datetime.strptime(week_start, "%Y-%m-%d")
    offset = DAY_OFFSETS[day]
    hour, minute = int(time[:2]), int(time[3:])
    local_dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=BERLIN_TZ)
    local_dt += timedelta(days=offset)
    return local_dt.astimezone(ZoneInfo("UTC")).isoformat()


def _arm_batch_dispatch_sync(
    batch_id: str,
    request: BatchArmRequest,
    db: Any = None,
) -> Dict[str, Any]:
    """Validate and arm all posts in a batch for scheduled dispatch."""
    if db is None:
        db = get_supabase().client

    # 1. Validate batch state
    batch_resp = db.table("batches").select("id,state").eq("id", batch_id).execute()
    if not batch_resp.data:
        raise ValidationError(f"Batch {batch_id} not found")
    batch = batch_resp.data[0]
    if batch["state"] != "S7_PUBLISH_PLAN":
        raise ValidationError(f"Batch must be in S7_PUBLISH_PLAN state, got {batch['state']}")

    # 2. Fetch posts for this batch
    posts_resp = (
        db.table("posts")
        .select("id,video_url,batch_id,social_networks,tiktok_settings")
        .eq("batch_id", batch_id)
        .execute()
    )
    posts_by_id = {p["id"]: p for p in posts_resp.data}

    # 3. Validate and schedule each post
    user_tz = ZoneInfo(request.timezone) if request.timezone else BERLIN_TZ
    scheduled_posts = []

    # Pre-validate TikTok settings for every post that will target TikTok after arming.
    posts_for_tiktok_check: List[Dict[str, Any]] = []
    for post_spec in request.posts:
        db_post = posts_by_id.get(post_spec.post_id)
        if not db_post:
            continue
        effective_networks = post_spec.networks_override or request.default_networks
        posts_for_tiktok_check.append(
            {
                "id": post_spec.post_id,
                "social_networks": effective_networks,
                "tiktok_settings": db_post.get("tiktok_settings") or {},
            }
        )
    _validate_tiktok_settings_present(posts_for_tiktok_check)

    for i, post_spec in enumerate(request.posts):
        db_post = posts_by_id.get(post_spec.post_id)
        if not db_post:
            raise ValidationError(f"Post {post_spec.post_id} not found in batch {batch_id}")
        if not db_post.get("video_url"):
            raise ValidationError(f"Post {post_spec.post_id} has no video — cannot arm dispatch")

        # Compute scheduled_at
        if post_spec.time_override:
            local_dt = datetime.strptime(post_spec.time_override, "%Y-%m-%dT%H:%M")
            local_dt = local_dt.replace(tzinfo=user_tz)
            scheduled_at = local_dt.astimezone(ZoneInfo("UTC")).isoformat()
        elif i < len(request.slots):
            slot = request.slots[i]
            scheduled_at = _compute_scheduled_at(request.week_start, slot.day, slot.time)
        else:
            raise ValidationError(
                f"Post {post_spec.post_id} has no time slot (index {i}) and no time_override"
            )

        networks = post_spec.networks_override or request.default_networks

        scheduled_posts.append({
            "post_id": post_spec.post_id,
            "scheduled_at": scheduled_at,
            "networks": networks,
            "publish_caption": post_spec.caption,
        })

    rpc_applied = False
    if hasattr(db, "rpc"):
        try:
            db.rpc(
                "arm_batch_publish_schedule",
                {"p_batch_id": batch_id, "p_schedules": scheduled_posts},
            ).execute()
            rpc_applied = True
        except APIError as exc:
            error_text = str(exc).lower()
            if exc.code not in {"PGRST202", "42883"} and "arm_batch_publish_schedule" not in error_text:
                raise
            log.warning("batch_arm_rpc_unavailable_fallback", batch_id=batch_id)

    if not rpc_applied:
        # Rolling-migration fallback. Remove after every deployment has the RPC.
        for schedule in scheduled_posts:
            db.table("posts").update({
                "scheduled_at": schedule["scheduled_at"],
                "publish_caption": schedule["publish_caption"],
                "social_networks": schedule["networks"],
                "publish_status": "scheduled",
            }).eq("id", schedule["post_id"]).execute()

    log.info("batch_arm_dispatch", batch_id=batch_id, armed_count=len(scheduled_posts))
    return {
        "ok": True,
        "armed_count": len(scheduled_posts),
        "scheduled_posts": [
            {key: value for key, value in schedule.items() if key != "publish_caption"}
            for schedule in scheduled_posts
        ],
    }


async def arm_batch_dispatch(
    batch_id: str,
    request: BatchArmRequest,
    db: Any = None,
) -> Dict[str, Any]:
    """Keep synchronous PostgREST work away from the application event loop."""
    import asyncio

    return await asyncio.to_thread(
        _arm_batch_dispatch_sync,
        batch_id,
        request,
        db,
    )


@router.post("/batches/{batch_id}/arm")
async def handle_arm_batch(batch_id: str, request: BatchArmRequest, http_request: Request):
    """Arm all posts in a batch for scheduled dispatch across all platforms."""
    result = await arm_batch_dispatch(batch_id=batch_id, request=request)
    return result
