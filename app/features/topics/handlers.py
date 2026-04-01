"""
FLOW-FORGE Topics Handlers
FastAPI route handlers for topic discovery.
Per Constitution § V: Locality & Vertical Slices
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from threading import RLock, Thread
from collections import Counter
from types import SimpleNamespace
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request, Header, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse,
    TopicResearchRunRequest,
)
from app.features.topics.agents import (
    generate_lifestyle_topics,
    build_lifestyle_seed_payload,
)
from app.features.topics.captions import attach_caption_bundle
from app.features.topics.deduplication import deduplicate_topics
from app.features.topics.variant_expansion import expand_topic_variants
from app.features.topics.seed_builders import build_research_seed_data
from app.features.topics.topic_validation import classify_script_overlap
from app.features.topics.queries import (
    get_all_topics_from_registry,
    add_topic_to_registry,
    count_selectable_topic_families,
    delete_topic_script,
    create_post_for_batch,
    get_posts_by_batch,
    list_topic_suggestions,
    mark_topic_script_used,
    get_topic_registry_by_id,
    get_topic_scripts_for_registry,
    store_topic_bank_entry,
    upsert_topic_script_variants,
)
from app.features.batches.queries import get_batch_by_id, update_batch_state, list_batches
from app.core.states import BatchState
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError
from app.core.logging import get_logger
from app.core.config import get_settings
from app.features.topics.hub import (
    _build_script_variants,
    _wants_html,
    build_launch_hub_payload,
    build_topic_hub_payload,
    fuzzy_match_topic,
    get_random_topic,
    harvest_topics_to_bank_sync,
    launch_topic_research_run,
    parse_topic_filters,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/topics", tags=["topics"])
templates = Jinja2Templates(directory="templates")
_SEEDING_PROGRESS: Dict[str, Dict[str, Any]] = {}
_SEEDING_EVENTS: Dict[str, List[Dict[str, Any]]] = {}
_SEEDING_EVENT_COUNTERS: Dict[str, int] = {}
_SEEDING_PROGRESS_LOCK = RLock()
_DISCOVERY_TASKS: Dict[str, asyncio.Task] = {}
_PROGRESS_TTL_SECONDS = 45
_WARMUP_SEED_TOPIC_COUNT = 3
_COVERAGE_TASKS: Dict[str, Thread] = {}
_COVERAGE_WAITERS: Dict[str, Dict[str, int]] = {}


def _attach_publish_captions(
    *,
    topic_title: str,
    post_type: str,
    seed_payload: Dict[str, Any],
    script_fallback: str = "",
    canonical_topic: str = "",
) -> Dict[str, Any]:
    return attach_caption_bundle(
        seed_payload,
        topic_title=topic_title,
        post_type=post_type,
        script_fallback=script_fallback,
        canonical_topic=canonical_topic or None,
    )


def _topic_family_signature(topic: Dict[str, Any]) -> str:
    payload = topic.get("seed_payload") if isinstance(topic, dict) else None
    canonical_topic = ""
    if isinstance(payload, dict):
        canonical_topic = str(payload.get("canonical_topic") or "").strip()
    if not canonical_topic:
        canonical_topic = str(topic.get("canonical_topic") or topic.get("title") or topic.get("script") or "").strip()
    normalized = " ".join(
        token for token in re.sub(r"[^\w\s]", " ", canonical_topic.lower()).split() if token
    )
    return normalized


def _unique_topic_suggestions(
    suggestions: List[Dict[str, Any]],
    limit: int,
    *,
    existing_topics: Optional[List[Dict[str, Any]]] = None,
    semantic_threshold: float = 0.35,
) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen_topic_families: set[str] = set()
    for suggestion in suggestions:
        family_signature = _topic_family_signature(suggestion)
        if family_signature and family_signature in seen_topic_families:
            continue
        if family_signature:
            seen_topic_families.add(family_signature)
        unique.append(suggestion)
        if len(unique) >= limit:
            break
    if existing_topics is None:
        existing_topics = []
    semantic_candidates: List[Dict[str, Any]] = []
    for suggestion in unique:
        seed_payload = suggestion.get("seed_payload") if isinstance(suggestion, dict) else None
        canonical_topic = ""
        if isinstance(seed_payload, dict):
            canonical_topic = str(seed_payload.get("canonical_topic") or "").strip()
        rotation = str(
            suggestion.get("rotation")
            or canonical_topic
            or suggestion.get("script")
            or suggestion.get("title")
            or ""
        ).strip()
        cta = str(suggestion.get("cta") or rotation or suggestion.get("title") or "").strip()
        semantic_candidates.append(
            {
                **suggestion,
                "rotation": rotation,
                "cta": cta,
                "script": str(suggestion.get("script") or rotation or suggestion.get("title") or "").strip(),
            }
        )
    filtered = deduplicate_topics(semantic_candidates, existing_topics, threshold=semantic_threshold)
    return filtered[:limit]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coverage_gap_key(post_type: str, target_length_tier: Optional[int]) -> str:
    return f"{post_type}:{int(target_length_tier or 8)}"


def _create_post_from_suggestion(
    *,
    batch_id: str,
    post_type: str,
    suggestion: Dict[str, Any],
    target_length_tier: Optional[int],
) -> Dict[str, Any]:
    topic_title = str(suggestion.get("title") or "").strip()
    topic_rotation = str(suggestion.get("rotation") or suggestion.get("script") or topic_title).strip()
    topic_cta = str(suggestion.get("cta") or topic_rotation).strip()
    seed_payload = dict(suggestion.get("seed_payload") or {"facts": [topic_rotation]})
    canonical_topic = str(
        seed_payload.get("canonical_topic")
        or suggestion.get("canonical_topic")
        or topic_title
    ).strip()
    if canonical_topic:
        seed_payload["canonical_topic"] = canonical_topic
    family_id = suggestion.get("family_id") or suggestion.get("topic_registry_id")
    family_fingerprint = suggestion.get("family_fingerprint") or _topic_family_signature(suggestion)
    if family_id:
        seed_payload["family_id"] = family_id
    if family_fingerprint:
        seed_payload["family_fingerprint"] = family_fingerprint
    seed_payload = _attach_publish_captions(
        topic_title=topic_title,
        post_type=post_type,
        seed_payload=seed_payload,
        script_fallback=topic_rotation,
        canonical_topic=canonical_topic or topic_title,
    )
    add_topic_to_registry(
        title=topic_title,
        script=topic_rotation,
        post_type=post_type,
        canonical_topic=canonical_topic or topic_title,
    )
    mark_topic_script_used(script_id=suggestion.get("script_id"))
    return create_post_for_batch(
        batch_id=batch_id,
        post_type=post_type,
        topic_title=topic_title,
        topic_rotation=topic_rotation,
        topic_cta=topic_cta,
        spoken_duration=float(suggestion.get("spoken_duration") or 5),
        seed_data=seed_payload,
        target_length_tier=target_length_tier,
    )


def _run_coverage_warmup_task(coverage_key: str, post_type: str, target_length_tier: int) -> None:
    try:
        requested_count = max(_WARMUP_SEED_TOPIC_COUNT, max(_COVERAGE_WAITERS.get(coverage_key, {}).values(), default=0))
        harvest_topics_to_bank_sync(
            post_type_counts={post_type: requested_count},
            target_length_tier=target_length_tier,
            trigger_source="batch_coverage_warmup",
        )
        from workers.audit_worker import run_audit_cycle

        run_audit_cycle()
    except Exception as exc:
        logger.exception(
            "batch_coverage_warmup_failed",
            coverage_key=coverage_key,
            post_type=post_type,
            target_length_tier=target_length_tier,
            error=str(exc),
        )
    finally:
        coverage_count = count_selectable_topic_families(
            post_type=post_type,
            target_length_tier=target_length_tier,
        )
        with _SEEDING_PROGRESS_LOCK:
            waiters = dict(_COVERAGE_WAITERS.pop(coverage_key, {}))
            _COVERAGE_TASKS.pop(coverage_key, None)

        for batch_id, required_count in waiters.items():
            if coverage_count >= required_count:
                clear_seeding_progress(batch_id)
                continue
            update_seeding_progress(
                batch_id,
                stage="coverage_pending",
                stage_label="Waiting for audited family coverage",
                detail_message=(
                    f"Background warm-up finished with {coverage_count} audited {post_type} families at {target_length_tier}s. "
                    "More audited coverage is still needed before this batch can seed."
                ),
                current_post_type=post_type,
                attempt=None,
                max_attempts=None,
                is_retrying=False,
                retry_message=None,
            )


def _schedule_coverage_warmup(
    *,
    batch_id: str,
    post_type: str,
    target_length_tier: Optional[int],
    required_count: int,
) -> None:
    coverage_key = _coverage_gap_key(post_type, target_length_tier)
    resolved_tier = int(target_length_tier or 8)
    with _SEEDING_PROGRESS_LOCK:
        waiters = _COVERAGE_WAITERS.setdefault(coverage_key, {})
        waiters[batch_id] = max(required_count, int(waiters.get(batch_id) or 0))
        task = _COVERAGE_TASKS.get(coverage_key)
        if task and task.is_alive():
            return
        thread = Thread(
            target=_run_coverage_warmup_task,
            args=(coverage_key, post_type, resolved_tier),
            daemon=True,
        )
        _COVERAGE_TASKS[coverage_key] = thread
        thread.start()


def _next_event_id_locked(batch_id: str) -> str:
    current = _SEEDING_EVENT_COUNTERS.get(batch_id, 0) + 1
    _SEEDING_EVENT_COUNTERS[batch_id] = current
    return str(current)


def _append_event_locked(batch_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    event = {
        "event_id": _next_event_id_locked(batch_id),
        "event_type": event_type,
        "created_at": _utc_now_iso(),
        **payload,
    }
    events = _SEEDING_EVENTS.setdefault(batch_id, [])
    events.append(event)
    if len(events) > 80:
        del events[:-80]
    return dict(event)


def start_seeding_interaction(batch_id: str, brand: str, expected_posts: int) -> Dict[str, Any]:
    """Initialize a resumable interaction id and seed event log for a new batch."""
    with _SEEDING_PROGRESS_LOCK:
        current = dict(_SEEDING_PROGRESS.get(batch_id) or {})
        if current.get("interaction_id"):
            return dict(current)

        interaction_id = f"seed_{uuid4().hex[:12]}"
        current.update(
            {
                "brand": brand,
                "expected_posts": expected_posts,
                "posts_created": 0,
                "state": BatchState.S1_SETUP.value,
                "stage": "booting",
                "stage_label": "Preparing topic generation",
                "detail_message": "Opening the batch and starting the research session.",
                "retry_message": None,
                "is_retrying": False,
                "interaction_id": interaction_id,
                "status": "in_progress",
                "last_updated_at": _utc_now_iso(),
            }
        )
        _SEEDING_PROGRESS[batch_id] = current
        _SEEDING_EVENTS[batch_id] = []
        _SEEDING_EVENT_COUNTERS[batch_id] = 0
        _append_event_locked(
            batch_id,
            "interaction.start",
            {
                "interaction": {
                    "id": interaction_id,
                    "status": "in_progress",
                },
                "summary": f"Research session started for {brand}.",
                "progress": dict(current),
            },
        )
        return dict(current)


def update_seeding_progress(batch_id: str, **progress: Any) -> Dict[str, Any]:
    """Persist the latest seeding progress snapshot and emit resumable feed events."""
    with _SEEDING_PROGRESS_LOCK:
        current = dict(_SEEDING_PROGRESS.get(batch_id) or {})
        if not current.get("interaction_id"):
            current = start_seeding_interaction(
                batch_id=batch_id,
                brand=progress.get("brand") or current.get("brand") or batch_id,
                expected_posts=int(progress.get("expected_posts") or current.get("expected_posts") or 0),
            )
            current = dict(_SEEDING_PROGRESS.get(batch_id) or current)

        previous = dict(current)
        current.update(progress)
        current["last_updated_at"] = _utc_now_iso()
        current["status"] = (
            "completed"
            if current.get("stage") == "completed"
            else "failed"
            if current.get("stage") == "failed"
            else "reconnecting"
            if current.get("stage") == "retry_wait"
            else "in_progress"
        )
        _SEEDING_PROGRESS[batch_id] = current

        progress_changed = any(
            previous.get(key) != current.get(key)
            for key in (
                "stage",
                "stage_label",
                "detail_message",
                "posts_created",
                "expected_posts",
                "current_post_type",
                "attempt",
                "max_attempts",
                "is_retrying",
                "retry_message",
                "state",
                "status",
            )
        )
        if progress_changed:
            _append_event_locked(
                batch_id,
                "progress.update",
                {
                    "interaction": {
                        "id": current["interaction_id"],
                        "status": current["status"],
                    },
                    "progress": dict(current),
                },
            )

            if previous.get("detail_message") != current.get("detail_message") and current.get("detail_message"):
                _append_event_locked(
                    batch_id,
                    "content.delta",
                    {
                        "interaction": {
                            "id": current["interaction_id"],
                            "status": current["status"],
                        },
                        "delta": {
                            "type": "thought_summary",
                            "content": {"text": current["detail_message"]},
                        },
                        "stage": current.get("stage"),
                    },
                )

            if current.get("posts_created", 0) > previous.get("posts_created", 0):
                _append_event_locked(
                    batch_id,
                    "progress.post_created",
                    {
                        "interaction": {
                            "id": current["interaction_id"],
                            "status": current["status"],
                        },
                        "summary": (
                            f"{current['posts_created']} of {current.get('expected_posts', 0)} posts ready for review."
                        ),
                        "progress": dict(current),
                    },
                )

            if current.get("stage") == "completed" and previous.get("stage") != "completed":
                _append_event_locked(
                    batch_id,
                    "interaction.complete",
                    {
                        "interaction": {
                            "id": current["interaction_id"],
                            "status": "completed",
                        },
                        "summary": "Research complete",
                        "progress": dict(current),
                    },
                )

            if current.get("stage") == "failed" and previous.get("stage") != "failed":
                _append_event_locked(
                    batch_id,
                    "interaction.failed",
                    {
                        "interaction": {
                            "id": current["interaction_id"],
                            "status": "failed",
                        },
                        "summary": current.get("detail_message") or "Research failed",
                        "progress": dict(current),
                    },
                )

        return dict(current)


def get_seeding_progress(batch_id: str) -> Optional[Dict[str, Any]]:
    """Return current seeding progress if it has not expired."""
    with _SEEDING_PROGRESS_LOCK:
        progress = _SEEDING_PROGRESS.get(batch_id)
        if not progress:
            return None

        stage = progress.get("stage")
        if stage in {"completed", "failed"}:
            try:
                updated_at = datetime.fromisoformat(progress["last_updated_at"])
            except (KeyError, TypeError, ValueError):
                _SEEDING_PROGRESS.pop(batch_id, None)
                return None
            age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
            if age_seconds > _PROGRESS_TTL_SECONDS:
                _SEEDING_PROGRESS.pop(batch_id, None)
                return None

        return dict(progress)


def get_seeding_events(batch_id: str, last_event_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return feed events after the provided event id."""
    with _SEEDING_PROGRESS_LOCK:
        events = list(_SEEDING_EVENTS.get(batch_id) or [])

    if not last_event_id:
        return events

    try:
        last_seen = int(last_event_id)
    except (TypeError, ValueError):
        return events

    return [event for event in events if int(event["event_id"]) > last_seen]


def clear_seeding_progress(batch_id: str) -> None:
    with _SEEDING_PROGRESS_LOCK:
        _SEEDING_PROGRESS.pop(batch_id, None)
        _SEEDING_EVENTS.pop(batch_id, None)
        _SEEDING_EVENT_COUNTERS.pop(batch_id, None)


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mark_discovery_task(batch_id: str, task: asyncio.Task) -> None:
    with _SEEDING_PROGRESS_LOCK:
        _DISCOVERY_TASKS[batch_id] = task


def _clear_discovery_task(batch_id: str, task: asyncio.Task) -> None:
    with _SEEDING_PROGRESS_LOCK:
        current = _DISCOVERY_TASKS.get(batch_id)
        if current is task:
            _DISCOVERY_TASKS.pop(batch_id, None)


def is_batch_discovery_active(batch_id: str) -> bool:
    with _SEEDING_PROGRESS_LOCK:
        task = _DISCOVERY_TASKS.get(batch_id)
        if task is None and batch_id not in _DISCOVERY_TASKS:
            return False
        if task is None:
            return True  # sentinel set by schedule_batch_discovery
        if task.done():
            _DISCOVERY_TASKS.pop(batch_id, None)
            return False
        return True


async def _run_batch_discovery_task(batch_id: str) -> None:
    task = asyncio.current_task()
    if task is not None:
        _mark_discovery_task(batch_id, task)
    try:
        result = await discover_topics_for_batch(batch_id)
        logger.info(
            "batch_autoseed_complete",
            batch_id=batch_id,
            posts_created=result["posts_created"],
            new_state=result["state"],
        )
    except FlowForgeException as exc:
        update_seeding_progress(
            batch_id,
            stage="failed",
            stage_label="Topic generation stopped",
            detail_message=exc.message,
            is_retrying=False,
            retry_message=None,
        )
        logger.error(
            "batch_autoseed_failed",
            batch_id=batch_id,
            error=exc.message,
            details=exc.details,
        )
    except Exception as exc:
        update_seeding_progress(
            batch_id,
            stage="failed",
            stage_label="Topic generation stopped",
            detail_message="The seeding run failed before script review could start.",
            is_retrying=False,
            retry_message=None,
        )
        logger.exception(
            "batch_autoseed_unexpected_error",
            batch_id=batch_id,
            error=str(exc),
        )
    finally:
        if task is not None:
            _clear_discovery_task(batch_id, task)


def schedule_batch_discovery(batch_id: str, *, reason: str) -> bool:
    with _SEEDING_PROGRESS_LOCK:
        task = _DISCOVERY_TASKS.get(batch_id)
        if task and not task.done():
            logger.info("batch_autoseed_already_active", batch_id=batch_id, reason=reason)
            return False
        # Mark a sentinel so concurrent callers see the slot as taken
        _DISCOVERY_TASKS[batch_id] = None  # type: ignore[assignment]

    batch = get_batch_by_id(batch_id)
    if batch["state"] != BatchState.S1_SETUP.value:
        logger.info(
            "batch_autoseed_skipped_non_setup",
            batch_id=batch_id,
            state=batch["state"],
            reason=reason,
        )
        with _SEEDING_PROGRESS_LOCK:
            if _DISCOVERY_TASKS.get(batch_id) is None:
                _DISCOVERY_TASKS.pop(batch_id, None)
        return False

    task = asyncio.create_task(_run_batch_discovery_task(batch_id))
    _mark_discovery_task(batch_id, task)
    logger.info("batch_autoseed_scheduled", batch_id=batch_id, reason=reason)
    return True


def recover_stalled_batches(limit: int = 1, max_age_hours: int = 6) -> List[str]:
    recovered: List[str] = []
    batches, _ = list_batches(archived=False, limit=max(limit * 10, 25), offset=0)
    newest_allowed_created_at = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for batch in batches:
        if len(recovered) >= limit:
            break
        if batch["state"] != BatchState.S1_SETUP.value:
            continue
        batch_id = batch["id"]
        created_at = _parse_utc_timestamp(batch.get("created_at"))
        if created_at is None or created_at < newest_allowed_created_at:
            continue
        if get_posts_by_batch(batch_id):
            continue
        progress = get_seeding_progress(batch_id)
        if progress and progress.get("stage") not in {"failed", "completed"}:
            continue
        if schedule_batch_discovery(batch_id, reason="startup_recovery"):
            recovered.append(batch_id)
    return recovered


def has_required_family_coverage(batch: Dict[str, Any]) -> bool:
    """Return True when every research-backed post type can seed from audited families."""
    post_type_counts = batch.get("post_type_counts") or {}
    target_length_tier = int(batch.get("target_length_tier") or 8)

    for post_type, count in post_type_counts.items():
        requested_count = int(count or 0)
        if post_type == "lifestyle" or requested_count <= 0:
            continue
        available_count = count_selectable_topic_families(
            post_type=post_type,
            target_length_tier=target_length_tier,
        )
        if available_count < requested_count:
            return False
    return True


def _discover_topics_for_batch_sync(batch_id: str) -> Dict[str, Any]:
    """Synchronous topic discovery workflow executed off the request event loop."""
    batch = get_batch_by_id(batch_id)

    if batch["state"] != BatchState.S1_SETUP.value:
        raise ValidationError(
            message="Batch must be in S1_SETUP state for topic discovery",
            details={"current_state": batch["state"], "required_state": "S1_SETUP"}
        )

    post_type_counts = batch["post_type_counts"]
    existing_topics = get_all_topics_from_registry()
    target_length_tier = batch.get("target_length_tier")
    resolved_target_tier = int(target_length_tier or 8)

    expected_posts = sum(post_type_counts.values())
    all_generated_topics: List[Dict[str, Any]] = []
    created_posts = []
    preselected_suggestions: Dict[str, List[Dict[str, Any]]] = {}

    update_seeding_progress(
        batch_id,
        state=batch["state"],
        stage="booting",
        stage_label="Preparing topic discovery",
        detail_message="Opening the research run and reading the batch mix before generating any posts.",
        posts_created=0,
        expected_posts=expected_posts,
        current_post_type=None,
        attempt=None,
        max_attempts=None,
        is_retrying=False,
        retry_message=None,
    )

    for post_type, count in post_type_counts.items():
        if count == 0 or post_type == "lifestyle":
            continue
        stored_suggestions = list_topic_suggestions(
            target_length_tier=resolved_target_tier,
            limit=max(count * 3, count),
            post_type=post_type,
        )
        unique_stored_suggestions = _unique_topic_suggestions(
            stored_suggestions,
            count,
            existing_topics=all_generated_topics,
        )
        preselected_suggestions[post_type] = unique_stored_suggestions
        if len(unique_stored_suggestions) >= count:
            continue

        available_count = len(unique_stored_suggestions)
        update_seeding_progress(
            batch_id,
            state=batch["state"],
            stage="coverage_pending",
            stage_label="Waiting for audited family coverage",
            detail_message=(
                f"Only {available_count} audited {post_type} families are ready at {resolved_target_tier}s. "
                "Background topic-bank warm-up and audit promotion were queued."
            ),
            posts_created=0,
            expected_posts=expected_posts,
            current_post_type=post_type,
            attempt=None,
            max_attempts=None,
            is_retrying=False,
            retry_message=None,
        )
        _schedule_coverage_warmup(
            batch_id=batch_id,
            post_type=post_type,
            target_length_tier=resolved_target_tier,
            required_count=count,
        )
        logger.info(
            "topic_discovery_coverage_pending",
            batch_id=batch_id,
            post_type=post_type,
            target_length_tier=resolved_target_tier,
            required_count=count,
            available_count=available_count,
        )
        return {
            "batch_id": batch_id,
            "posts_created": 0,
            "state": batch["state"],
            "topics": [],
            "coverage_pending": True,
        }

    for post_type, count in post_type_counts.items():
        if count == 0:
            continue

        update_seeding_progress(
            batch_id,
            state=batch["state"],
            stage="researching" if post_type != "lifestyle" else "writing_posts",
            stage_label=(
                "Researching current source-backed topics"
                if post_type != "lifestyle"
                else "Drafting lifestyle concepts"
            ),
            detail_message=(
                f"Working on {count} {post_type} posts and preparing the first pass of usable concepts."
            ),
            posts_created=len(created_posts),
            expected_posts=expected_posts,
            current_post_type=post_type,
            attempt=0 if post_type != "lifestyle" else None,
            max_attempts=5 if post_type != "lifestyle" else None,
            is_retrying=False,
            retry_message=None,
        )

        logger.info(
            "generating_topics",
            batch_id=batch_id,
            post_type=post_type,
            count=count
        )

        # PIPELINE DISPATCH: lifestyle uses PROMPT_2 direct; value/product use Deep Research via PROMPT_1.
        if post_type == "lifestyle":
            # Lifestyle pipeline: PROMPT_2 direct (no web research), but still dedupe
            required_topics = count
            collected_candidates: List[Dict[str, Any]] = []
            attempts = 0
            max_attempts = 5
            dedupe_reference = existing_topics + all_generated_topics
            existing_titles = {
                str(topic.get("title") or "").strip().lower()
                for topic in dedupe_reference
                if isinstance(topic, dict) and str(topic.get("title") or "").strip()
            }
            existing_scripts = [
                str(topic.get("script") or topic.get("rotation") or "").strip()
                for topic in dedupe_reference
                if isinstance(topic, dict) and str(topic.get("script") or topic.get("rotation") or "").strip()
            ]

            while len(collected_candidates) < required_topics and attempts < max_attempts:
                remaining_topics = required_topics - len(collected_candidates)
                request_count = remaining_topics if attempts == 0 else min(required_topics, remaining_topics + 2)

                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="collecting",
                    stage_label="Collecting distinct lifestyle concepts",
                    detail_message=(
                        f"Generating {request_count} lifestyle candidates and comparing them against the registry and current batch."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts + 1,
                    max_attempts=max_attempts,
                    is_retrying=attempts > 0,
                    retry_message=(
                        "Retrying lifestyle generation because earlier concepts overlapped."
                        if attempts > 0
                        else None
                    ),
                )

                lifestyle_topics = generate_lifestyle_topics(
                    count=request_count,
                    target_length_tier=resolved_target_tier,
                )

                unique_candidates = deduplicate_topics(
                    lifestyle_topics,
                    dedupe_reference,
                    threshold=0.35,
                )

                for candidate in unique_candidates:
                    if len(collected_candidates) >= required_topics:
                        break
                    candidate_title = str(candidate.get("title") or "").strip().lower()
                    candidate_script = str(candidate.get("script") or candidate.get("rotation") or "").strip()
                    if candidate_title and candidate_title in existing_titles:
                        continue
                    overlap_reason = next(
                        (
                            reason
                            for existing_script in existing_scripts
                            for reason in [classify_script_overlap(candidate_script, existing_script)]
                            if reason
                        ),
                        "",
                    )
                    if overlap_reason:
                        logger.info(
                            "lifestyle_candidate_script_overlap_filtered",
                            batch_id=batch_id,
                            title=candidate.get("title"),
                            reason=overlap_reason,
                        )
                        continue
                    collected_candidates.append(candidate)
                    if candidate_title:
                        existing_titles.add(candidate_title)
                    if candidate_script:
                        existing_scripts.append(candidate_script)
                    dedupe_reference.append(
                        {
                            "title": candidate["title"],
                            "rotation": candidate["rotation"],
                            "cta": candidate["cta"],
                            "script": candidate_script,
                            "spoken_duration": float(candidate["spoken_duration"]),
                        }
                    )

                attempts += 1
                remaining_after = max(required_topics - len(collected_candidates), 0)
                should_retry = remaining_after > 0 and attempts < max_attempts
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="retry_wait" if should_retry else "collecting",
                    stage_label=(
                        "Requesting another lifestyle pass"
                        if should_retry
                        else "Lifestyle candidate collection complete"
                    ),
                    detail_message=(
                        f"Collected {len(collected_candidates)} of {required_topics} distinct lifestyle topics so far."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts,
                    max_attempts=max_attempts,
                    is_retrying=should_retry,
                    retry_message=(
                        "Still working. Duplicate lifestyle concepts were filtered out, so another pass is running."
                        if should_retry
                        else None
                    ),
                )

            for topic_data in collected_candidates[:required_topics]:
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="writing_posts",
                    stage_label="Writing posts from generated concepts",
                    detail_message=(
                        f"Turning {post_type} concepts into post records and seed payloads."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=None,
                    max_attempts=None,
                    is_retrying=False,
                    retry_message=None,
                )
                dialog_scripts = topic_data["dialog_scripts"]
                seed_payload = build_lifestyle_seed_payload(
                    topic_data=topic_data,
                    dialog_scripts=dialog_scripts
                )
                seed_payload["canonical_topic"] = str(seed_payload.get("canonical_topic") or topic_data["title"]).strip()
                seed_payload = _attach_publish_captions(
                    topic_title=topic_data["title"],
                    post_type=post_type,
                    seed_payload=seed_payload,
                    script_fallback=topic_data["rotation"],
                    canonical_topic=str(seed_payload.get("canonical_topic") or topic_data["title"]),
                )

                stored_row = store_topic_bank_entry(
                    title=topic_data["title"],
                    topic_script=topic_data["rotation"],
                    post_type=post_type,
                    target_length_tier=resolved_target_tier,
                    research_payload={},
                    origin_kind="provider",
                )
                seed_payload["family_id"] = stored_row["id"]
                seed_payload["family_fingerprint"] = stored_row.get("family_fingerprint") or _topic_family_signature(
                    {"title": topic_data["title"], "seed_payload": seed_payload}
                )

                variants = _build_script_variants(
                    topic_title=topic_data["title"],
                    post_type=post_type,
                    target_length_tier=resolved_target_tier,
                    research_dossier={},
                    prompt1_item=SimpleNamespace(
                        script=topic_data["rotation"],
                        caption=seed_payload.get("caption", ""),
                    ),
                    dialog_scripts=dialog_scripts,
                    seed_payload=seed_payload,
                )
                upsert_topic_script_variants(
                    topic_registry_id=stored_row["id"],
                    title=stored_row["title"],
                    post_type=post_type,
                    target_length_tier=resolved_target_tier,
                    topic_research_dossier_id=None,
                    variants=variants,
                    origin_kind="provider",
                )

                post = create_post_for_batch(
                    batch_id=batch_id,
                    post_type=post_type,
                    topic_title=topic_data["title"],
                    topic_rotation=topic_data["rotation"],
                    topic_cta=topic_data["cta"],
                    spoken_duration=float(topic_data["spoken_duration"]),
                    seed_data=seed_payload,
                    target_length_tier=resolved_target_tier,
                )

                created_posts.append(post)
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="writing_posts",
                    stage_label="Writing posts from generated concepts",
                    detail_message=(
                        f"{len(created_posts)} of {expected_posts} posts are now ready for script review."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=None,
                    max_attempts=None,
                    is_retrying=False,
                    retry_message=None,
                )
                dedup_topic_record: Dict[str, Any] = {
                    "title": topic_data["title"],
                    "rotation": topic_data["rotation"],
                    "cta": topic_data["cta"],
                    "spoken_duration": float(topic_data["spoken_duration"]),
                    "seed_payload": {"canonical_topic": seed_payload["canonical_topic"]},
                }
                all_generated_topics.append(dedup_topic_record)
        else:
            update_seeding_progress(
                batch_id,
                state=batch["state"],
                stage="writing_posts",
                stage_label="Reusing audited family coverage",
                detail_message=(
                    f"Using audited {post_type} family coverage from the topic bank without running live research."
                ),
                posts_created=len(created_posts),
                expected_posts=expected_posts,
                current_post_type=post_type,
                attempt=None,
                max_attempts=None,
                is_retrying=False,
                retry_message=None,
            )
            required_topics = count
            unique_stored_suggestions = preselected_suggestions.get(post_type, [])
            for suggestion in unique_stored_suggestions[:required_topics]:
                post = _create_post_from_suggestion(
                    batch_id=batch_id,
                    post_type=post_type,
                    suggestion=suggestion,
                    target_length_tier=resolved_target_tier,
                )
                created_posts.append(post)
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="writing_posts",
                    stage_label="Reusing audited family coverage",
                    detail_message=(
                        f"{len(created_posts)} of {expected_posts} posts are now ready for script review."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=None,
                    max_attempts=None,
                    is_retrying=False,
                    retry_message=None,
                )
                dedup_topic_record = {
                    "title": suggestion["title"],
                    "rotation": suggestion.get("rotation") or suggestion.get("script") or suggestion["title"],
                    "cta": suggestion.get("cta") or suggestion.get("rotation") or suggestion.get("script") or suggestion["title"],
                    "spoken_duration": float(suggestion.get("spoken_duration") or 5),
                    "seed_payload": {"canonical_topic": suggestion.get("canonical_topic") or suggestion["title"]},
                }
                all_generated_topics.append(dedup_topic_record)
                existing_topics.append(dedup_topic_record)

    created_counts = Counter(post.get("post_type") for post in created_posts)
    missing_post_types = {
        post_type: {
            "requested": requested_count,
            "created": created_counts.get(post_type, 0),
        }
        for post_type, requested_count in post_type_counts.items()
        if requested_count > created_counts.get(post_type, 0)
    }

    if missing_post_types:
        raise ValidationError(
            message="Topic discovery did not create all requested post types.",
            details={
                "batch_id": batch_id,
                "requested_counts": post_type_counts,
                "created_counts": dict(created_counts),
                "missing_post_types": missing_post_types,
            },
        )

    update_seeding_progress(
        batch_id,
        state=batch["state"],
        stage="finalizing",
        stage_label="Finalizing batch state",
        detail_message="Finishing post creation and moving the batch into script review.",
        posts_created=len(created_posts),
        expected_posts=expected_posts,
        current_post_type=None,
        attempt=None,
        max_attempts=None,
        is_retrying=False,
        retry_message=None,
    )
    updated_batch = update_batch_state(batch_id, BatchState.S2_SEEDED)
    update_seeding_progress(
        batch_id,
        state=updated_batch["state"],
        stage="completed",
        stage_label="Topic generation complete",
        detail_message=(
            f"{len(created_posts)} posts are ready. Opening script review next."
        ),
        posts_created=len(created_posts),
        expected_posts=expected_posts,
        current_post_type=None,
        attempt=None,
        max_attempts=None,
        is_retrying=False,
        retry_message=None,
    )

    logger.info(
        "topic_discovery_complete",
        batch_id=batch_id,
        posts_created=len(created_posts),
        new_state=updated_batch["state"]
    )

    return {
        "batch_id": batch_id,
        "posts_created": len(created_posts),
        "state": updated_batch["state"],
        "topics": all_generated_topics
    }


async def discover_topics_for_batch(batch_id: str) -> Dict[str, Any]:
    """Core topic discovery workflow reusable outside HTTP context."""
    return await asyncio.to_thread(_discover_topics_for_batch_sync, batch_id)


@router.post("/discover", response_model=SuccessResponse)
async def discover_topics_endpoint(request: DiscoverTopicsRequest):
    """
    Discover topics for a batch and create posts.
    Transitions batch from S1_SETUP to S2_SEEDED.
    Per Canon § 3.2: S1_SETUP → S2_SEEDED
    """
    try:
        result = await discover_topics_for_batch(request.batch_id)

        return SuccessResponse(data=result)

    except FlowForgeException:
        raise
    except ValidationError as exc:
        logger.error(
            "topic_discovery_validation_error",
            batch_id=request.batch_id,
            message=exc.message,
            details=exc.details,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "code": "validation_error",
                "message": exc.message,
                "details": exc.details,
            },
        )
    except Exception as e:
        logger.exception(
            "topic_discovery_failed",
            batch_id=request.batch_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to discover topics"
        )


@router.get("/random")
async def random_topic_endpoint(request: Request):
    """Return the topic with the fewest scripts as an HTMX partial."""
    from app.features.topics import hub as _hub
    topic = _hub.get_random_topic()
    if topic is None:
        return templates.TemplateResponse(
            "topics/partials/confirmation_card.html",
            {"request": request, "topic": None},
        )
    return templates.TemplateResponse(
        "topics/partials/confirmation_card.html",
        {"request": request, "topic": topic},
    )


@router.get("/match")
async def match_topic_endpoint(request: Request):
    """Fuzzy match a query string against existing topics."""
    from app.features.topics import hub as _hub
    query = str(request.query_params.get("q") or "").strip()
    templates = Jinja2Templates(directory="templates")
    if not query:
        return templates.TemplateResponse(
            "topics/partials/launch_panel.html",
            {"request": request},
        )
    match = _hub.fuzzy_match_topic(query)
    if match:
        return templates.TemplateResponse(
            "topics/partials/fuzzy_match.html",
            {"request": request, "match": match, "query": query},
        )
    return templates.TemplateResponse(
        "topics/partials/confirmation_card.html",
        {"request": request, "topic": {"title": query, "post_type": None, "script_count": 0, "is_new": True}},
    )


@router.get("/{topic_id}/scripts-drawer")
async def scripts_drawer_endpoint(request: Request, topic_id: str):
    """Return drawer content with tier-grouped scripts for a topic."""
    from app.features.topics.queries import get_topic_registry_by_id as _get_topic, get_topic_scripts_for_registry as _get_scripts

    topic = _get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    scripts = _get_scripts(topic_id)
    response = _render_scripts_drawer(request=request, topic=topic, scripts=scripts)
    response.headers["HX-Trigger"] = "open-scripts-drawer"
    return response


def _render_scripts_drawer(*, request: Request, topic: Dict[str, Any], scripts: List[Dict[str, Any]]):
    tier_groups: Dict[int, List[Dict[str, Any]]] = {}
    for script in scripts:
        tier = int(script.get("target_length_tier", 0) or 0)
        tier_groups.setdefault(tier, []).append(script)
    for tier_scripts in tier_groups.values():
        tier_scripts.sort(key=lambda s: s.get("created_at", ""), reverse=True)

    grouped: List[Dict[str, Any]] = []
    for tier in [8, 16, 32]:
        if tier in tier_groups:
            grouped.append({"tier": tier, "scripts": tier_groups.pop(tier)})
    for tier, tier_scripts in sorted(tier_groups.items()):
        if tier_scripts:
            grouped.append({"tier": tier, "scripts": tier_scripts})

    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse(
        "topics/partials/scripts_drawer.html",
        {
            "request": request,
            "topic": {**topic, "script_count": len(scripts)},
            "grouped_scripts": grouped,
            "total_scripts": len(scripts),
        },
    )


@router.post("/scripts/{script_id}/delete")
async def delete_topic_script_endpoint(request: Request, script_id: str):
    """Delete a topic script and refresh the drawer UI."""
    topic_id = delete_topic_script(script_id=script_id)
    if not topic_id:
        raise HTTPException(status_code=409, detail="Used scripts cannot be deleted")
    from app.features.topics.queries import get_topic_registry_by_id as _get_topic, get_topic_scripts_for_registry as _get_scripts

    topic = _get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    scripts = _get_scripts(topic_id)
    response = _render_scripts_drawer(request=request, topic=topic, scripts=scripts)
    response.headers["HX-Trigger"] = "open-scripts-drawer"
    return response


@router.get("/select/{topic_id}")
async def select_topic_endpoint(request: Request, topic_id: str):
    """Return a confirmation card for the selected topic."""
    from app.features.topics.queries import get_topic_registry_by_id, get_topic_scripts_for_registry
    topic = get_topic_registry_by_id(topic_id)
    scripts = get_topic_scripts_for_registry(topic_id)
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse(
        "topics/partials/confirmation_card.html",
        {"request": request, "topic": {**topic, "script_count": len(scripts)}},
    )


@router.get("")
async def list_topics_endpoint(request: Request):
    """Render the topics hub or return the legacy JSON API payload."""
    try:
        if _wants_html(request):
            payload = build_topic_hub_payload(request)
            templates = Jinja2Templates(directory="templates")
            return templates.TemplateResponse(
                "topics/hub.html",
                {"request": request, **payload},
            )

        payload = build_topic_hub_payload(request)
        topic_responses = [
            TopicResponse(
                id=topic["id"],
                title=topic["title"],
                rotation=topic["rotation"],
                cta=topic["cta"],
                first_seen_at=topic.get("first_seen_at") or topic.get("created_at") or _utc_now_iso(),
                last_used_at=topic.get("last_used_at") or topic.get("last_harvested_at") or topic.get("updated_at") or _utc_now_iso(),
                use_count=int(topic.get("use_count") or 0),
            )
            for topic in payload["topics"]
        ]
        return SuccessResponse(
            data=TopicListResponse(topics=topic_responses, total=len(topic_responses)).model_dump(mode="json")
        )

    except Exception as e:
        logger.exception("list_topics_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list topics",
        )


@router.post("/runs")
async def launch_topic_research_endpoint(request: Request):
    """Launch a durable topic research run for the selected topic."""
    try:
        payload: Dict[str, Any]
        script_usage = "all"
        if "application/json" in request.headers.get("content-type", ""):
            payload = await request.json()
            script_usage = str(payload.get("script_usage") or "all").strip().lower()
        else:
            form = await request.form()
            script_usage = str(form.get("script_usage") or "all").strip().lower()
            new_topic_title = str(form.get("new_topic_title") or "").strip()
            payload = {
                "topic_registry_id": str(form.get("topic_registry_id") or "").strip(),
                "target_length_tier": form.get("target_length_tier"),
                "trigger_source": str(form.get("trigger_source") or "manual").strip() or "manual",
                "post_type": str(form.get("post_type") or "").strip() or None,
            }

            # If a new topic title is provided and no registry ID, create a minimal registry entry
            if new_topic_title and not payload["topic_registry_id"]:
                new_topic = add_topic_to_registry(
                    title=new_topic_title,
                    rotation=new_topic_title,
                    cta=new_topic_title,
                    post_type=payload["post_type"] or "value",
                )
                payload["topic_registry_id"] = new_topic["id"]

        launch_request = TopicResearchRunRequest.model_validate(payload)
        result = await launch_topic_research_run(
            topic_registry_id=launch_request.topic_registry_id,
            target_length_tier=launch_request.target_length_tier,
            trigger_source=launch_request.trigger_source,
            post_type=launch_request.post_type,
        )

        if _wants_html(request):
            redirect_url = "/topics"
            response = RedirectResponse(
                url=redirect_url,
                status_code=status.HTTP_303_SEE_OTHER,
            )
            response.headers["HX-Trigger"] = "topic-research-launched"
            return response

        return SuccessResponse(
            data={
                "run": result["run"],
                "topic": result["topic"],
                "status_url": result["status_url"],
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "code": "validation_error",
                "message": exc.message,
                "details": exc.details,
            },
        )


@router.get("/runs/{run_id}/stream")
async def stream_topic_run_progress(request: Request, run_id: str, last_event_id: Optional[str] = None):
    """Stream live research progress events via SSE."""
    import json as _json

    async def event_stream():
        last_seen = last_event_id or request.headers.get("last-event-id")
        while True:
            if await request.is_disconnected():
                break
            events = get_seeding_events(run_id, last_seen)
            if events:
                for event in events:
                    payload = _json.dumps(event)
                    yield f"id: {event['event_id']}\ndata: {payload}\n\n"
                    last_seen = event["event_id"]
                terminal = events[-1]["event_type"]
                if terminal in {"interaction.complete", "interaction.failed"}:
                    break
            else:
                progress = get_seeding_progress(run_id)
                if progress and progress.get("stage") in {"completed", "failed"}:
                    break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
async def get_topic_research_run_endpoint(request: Request, run_id: str):
    """Return a durable research run as JSON or an HTML fragment."""
    try:
        from app.features.topics.queries import get_topic_research_run

        run = get_topic_research_run(run_id)
        if _wants_html(request):
            from fastapi.templating import Jinja2Templates

            templates = Jinja2Templates(directory="templates")
            compact = str(request.query_params.get("compact") or "").strip()
            if compact == "1":
                return templates.TemplateResponse(
                    "topics/partials/run_status_compact.html",
                    {"request": request, "run": run},
                )
            return templates.TemplateResponse(
                "topics/partials/run_card.html",
                {
                    "request": request,
                    "run": run,
                },
            )
        return SuccessResponse(data=run)
    except Exception as exc:
        logger.exception("topic_research_run_fetch_failed", run_id=run_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )


@router.post("/cron/discover", response_model=SuccessResponse)
async def cron_topic_discovery(
    authorization: Optional[str] = Header(None)
):
    """
    Hostinger cron endpoint for automated topic discovery.
    Runs once per day to discover topics for batches in S1_SETUP.
    Per Implementation Guide: Hostinger worker cron
    """
    settings = get_settings()
    
    # Verify cron secret
    if not authorization or authorization != f"Bearer {settings.cron_secret}":
        logger.warning("cron_unauthorized_access", auth_header=authorization)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )
    
    try:
        from app.features.batches.queries import list_batches

        batches, _ = list_batches(archived=False, limit=100, offset=0)
        seeded = []
        for batch in batches:
            if batch["state"] != BatchState.S1_SETUP.value:
                continue
            request_payload = DiscoverTopicsRequest(batch_id=batch["id"], count=10)
            seeded.append(batch["id"])
            await discover_topics_endpoint(request_payload)

        logger.info(
            "cron_topic_discovery_triggered",
            seeded_batches=seeded
        )
        return SuccessResponse(
            data={
                "message": "Cron job executed successfully",
                "seeded_batches": seeded,
            }
        )
    
    except Exception as e:
        logger.exception("cron_topic_discovery_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cron job failed"
        )


# ── Cron Status ─────────────────────────────────────────────────


@router.get("/cron-status", response_model=SuccessResponse)
async def cron_status():
    """
    Health/status endpoint for the automated topic discovery cron.
    Returns the latest run info and aggregate stats.
    """
    from app.features.topics.queries import get_latest_cron_run, get_cron_run_stats

    latest = get_latest_cron_run()
    stats = get_cron_run_stats()

    last_run = None
    next_expected = None
    if latest:
        last_run = {
            "id": latest.get("id"),
            "started_at": latest.get("started_at"),
            "completed_at": latest.get("completed_at"),
            "status": latest.get("status"),
            "topics_completed": latest.get("topics_completed", 0),
            "topics_failed": latest.get("topics_failed", 0),
            "seed_source": latest.get("seed_source"),
        }
        if latest.get("completed_at"):
            try:
                from dateutil.parser import isoparse
                from datetime import timedelta
                completed = isoparse(latest["completed_at"])
                next_expected = (completed + timedelta(hours=24)).isoformat()
            except (ValueError, TypeError):
                pass

    return SuccessResponse(
        data={
            "last_run": last_run,
            "next_expected_run": next_expected,
            **stats,
        }
    )


# ── Expand Variants ─────────────────────────────────────────────


class ExpandVariantsRequest(BaseModel):
    topic_registry_id: str
    count: int = 3
    target_length_tier: int = 8


@router.post("/expand-variants")
async def expand_variants_endpoint(body: ExpandVariantsRequest):
    """Generate additional script variants for a topic."""
    topic = get_topic_registry_by_id(body.topic_registry_id)
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic {body.topic_registry_id} not found",
        )
    result = expand_topic_variants(
        topic_registry_id=body.topic_registry_id,
        title=topic["title"],
        post_type=topic["post_type"],
        target_length_tier=body.target_length_tier,
        count=body.count,
    )
    return result
