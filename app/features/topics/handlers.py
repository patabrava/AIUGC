"""
FLOW-FORGE Topics Handlers
FastAPI route handlers for topic discovery.
Per Constitution § V: Locality & Vertical Slices
"""

import asyncio
from datetime import datetime, timedelta, timezone
from threading import RLock
from collections import Counter
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Header
from typing import Optional, Dict, Any, List

from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse
)
from app.features.topics.agents import (
    generate_topics_research_agent,
    generate_dialog_scripts,
    extract_seed_strict_extractor,
    convert_research_item_to_topic,
    build_seed_payload,
    generate_lifestyle_topics,
    build_lifestyle_seed_payload,
)
from app.features.topics.deduplication import deduplicate_topics
from app.features.topics.queries import (
    get_all_topics_from_registry,
    add_topic_to_registry,
    create_post_for_batch,
    get_posts_by_batch,
)
from app.features.batches.queries import get_batch_by_id, update_batch_state, list_batches
from app.core.states import BatchState
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError
from app.core.logging import get_logger
from app.core.config import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/topics", tags=["topics"])
_SEEDING_PROGRESS: Dict[str, Dict[str, Any]] = {}
_SEEDING_EVENTS: Dict[str, List[Dict[str, Any]]] = {}
_SEEDING_EVENT_COUNTERS: Dict[str, int] = {}
_SEEDING_PROGRESS_LOCK = RLock()
_DISCOVERY_TASKS: Dict[str, asyncio.Task] = {}
_PROGRESS_TTL_SECONDS = 45


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        if not task:
            return False
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
    if is_batch_discovery_active(batch_id):
        logger.info("batch_autoseed_already_active", batch_id=batch_id, reason=reason)
        return False

    batch = get_batch_by_id(batch_id)
    if batch["state"] != BatchState.S1_SETUP.value:
        logger.info(
            "batch_autoseed_skipped_non_setup",
            batch_id=batch_id,
            state=batch["state"],
            reason=reason,
        )
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

    expected_posts = sum(post_type_counts.values())
    all_generated_topics: List[Dict[str, Any]] = []
    created_posts = []

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

        # PIPELINE DISPATCH: lifestyle uses PROMPT_2 only, value/product use PROMPT_1+PROMPT_2
        if post_type == "lifestyle":
            # Lifestyle pipeline: PROMPT_2 direct (no web research), but still dedupe
            required_topics = count
            collected_candidates: List[Dict[str, Any]] = []
            attempts = 0
            max_attempts = 5
            dedupe_reference = existing_topics + all_generated_topics

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
                    count=request_count
                )

                unique_candidates = deduplicate_topics(
                    lifestyle_topics,
                    dedupe_reference,
                    threshold=0.35,
                )

                for candidate in unique_candidates:
                    if len(collected_candidates) >= required_topics:
                        break
                    collected_candidates.append(candidate)
                    dedupe_reference.append(
                        {
                            "title": candidate["title"],
                            "rotation": candidate["rotation"],
                            "cta": candidate["cta"],
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

                add_topic_to_registry(
                    title=topic_data["title"],
                    rotation=topic_data["rotation"],
                    cta=topic_data["cta"]
                )

                post = create_post_for_batch(
                    batch_id=batch_id,
                    post_type=post_type,
                    topic_title=topic_data["title"],
                    topic_rotation=topic_data["rotation"],
                    topic_cta=topic_data["cta"],
                    spoken_duration=float(topic_data["spoken_duration"]),
                    seed_data=seed_payload
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
                }
                all_generated_topics.append(dedup_topic_record)
        else:
            # Value/Product pipeline: PROMPT_1 research + PROMPT_2 scripts with retries for unique topics
            required_topics = count
            collected_candidates: List[Dict[str, Any]] = []
            attempts = 0
            max_attempts = 5
            dedupe_reference = existing_topics + all_generated_topics

            def progress_callback(update: Dict[str, Any]) -> None:
                provider_status = str(update.get("provider_status") or "").upper()
                stage = "retry_wait" if update.get("is_retrying") else "researching"
                stage_label = (
                    "Retrying the Gemini research interaction"
                    if update.get("is_retrying")
                    else "Gemini deep research is running"
                )
                if provider_status in {"DONE", "COMPLETED", "SUCCEEDED"}:
                    stage_label = "Gemini deep research finished"

                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage=stage,
                    stage_label=stage_label,
                    detail_message=update.get("detail_message") or f"Gemini is still researching {post_type} topics.",
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts + 1,
                    max_attempts=max_attempts,
                    is_retrying=bool(update.get("is_retrying")),
                    retry_message=update.get("retry_message"),
                    provider_interaction_id=update.get("provider_interaction_id"),
                    provider_status=provider_status or None,
                )

            while len(collected_candidates) < required_topics and attempts < max_attempts:
                remaining_topics = required_topics - len(collected_candidates)
                request_count = remaining_topics if attempts == 0 else min(required_topics, remaining_topics + 2)
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="researching",
                    stage_label="Researching current source-backed topics",
                    detail_message=(
                        f"Fetching {request_count} fresh {post_type} topic candidates from the research model."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts + 1,
                    max_attempts=max_attempts,
                    is_retrying=attempts > 0,
                    retry_message=(
                        "Retrying with extra candidates because earlier results overlapped."
                        if attempts > 0
                        else None
                    ),
                )
                items = generate_topics_research_agent(
                    post_type=post_type,
                    count=request_count,
                    progress_callback=progress_callback,
                )

                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="collecting",
                    stage_label="Collecting distinct topic candidates",
                    detail_message=(
                        f"Comparing new {post_type} findings against the registry and current batch to keep topics distinct."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts + 1,
                    max_attempts=max_attempts,
                    is_retrying=False,
                    retry_message=None,
                )
                topic_data = [convert_research_item_to_topic(item) for item in items]
                candidate_dicts: List[Dict[str, Any]] = []

                for idx, data in enumerate(topic_data):
                    candidate_dicts.append(
                        {
                            "title": data.title,
                            "rotation": data.rotation,
                            "cta": data.cta,
                            "spoken_duration": float(data.spoken_duration),
                            "__payload": {
                                "topic_model": data,
                                "original_item": items[idx],
                            },
                        }
                    )

                unique_candidates = deduplicate_topics(
                    candidate_dicts,
                    dedupe_reference,
                    threshold=0.35,
                )

                for candidate in unique_candidates:
                    if len(collected_candidates) >= required_topics:
                        break
                    collected_candidates.append(candidate)
                    dedupe_reference.append(
                        {
                            "title": candidate["title"],
                            "rotation": candidate["rotation"],
                            "cta": candidate["cta"],
                            "spoken_duration": candidate["spoken_duration"],
                        }
                    )

                attempts += 1

                logger.info(
                    "topic_candidate_collection_progress",
                    batch_id=batch_id,
                    post_type=post_type,
                    attempt=attempts,
                    requested=request_count,
                    remaining=max(required_topics - len(collected_candidates), 0),
                    collected=len(collected_candidates),
                    required=required_topics,
                )

                remaining_after = max(required_topics - len(collected_candidates), 0)
                should_retry = remaining_after > 0 and attempts < max_attempts
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="retry_wait" if should_retry else "collecting",
                    stage_label=(
                        "Requesting another research pass"
                        if should_retry
                        else "Candidate collection complete"
                    ),
                    detail_message=(
                        f"Collected {len(collected_candidates)} of {required_topics} distinct {post_type} topics so far."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts,
                    max_attempts=max_attempts,
                    is_retrying=should_retry,
                    retry_message=(
                        "Still working. Duplicates were filtered out, so another pass is running."
                        if should_retry
                        else None
                    ),
                )

            for candidate in collected_candidates[:required_topics]:
                payload = candidate["__payload"]
                topic_model = payload["topic_model"]
                original_item = payload["original_item"]
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="writing_posts",
                    stage_label="Writing posts from approved topic candidates",
                    detail_message=(
                        f"Building scripts and seed payloads for the collected {post_type} topics."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts,
                    max_attempts=max_attempts,
                    is_retrying=False,
                    retry_message=None,
                )
                dialog_scripts = generate_dialog_scripts(
                    topic=original_item.topic,
                )
                seed = extract_seed_strict_extractor(topic_model)

                seed_payload = build_seed_payload(
                    original_item,
                    strict_seed=seed,
                    dialog_scripts=dialog_scripts,
                )

                add_topic_to_registry(
                    title=topic_model.title,
                    rotation=topic_model.rotation,
                    cta=topic_model.cta,
                )

                post = create_post_for_batch(
                    batch_id=batch_id,
                    post_type=post_type,
                    topic_title=topic_model.title,
                    topic_rotation=topic_model.rotation,
                    topic_cta=topic_model.cta,
                    spoken_duration=float(topic_model.spoken_duration),
                    seed_data=seed_payload,
                )

                created_posts.append(post)
                update_seeding_progress(
                    batch_id,
                    state=batch["state"],
                    stage="writing_posts",
                    stage_label="Writing posts from approved topic candidates",
                    detail_message=(
                        f"{len(created_posts)} of {expected_posts} posts are now ready for script review."
                    ),
                    posts_created=len(created_posts),
                    expected_posts=expected_posts,
                    current_post_type=post_type,
                    attempt=attempts,
                    max_attempts=max_attempts,
                    is_retrying=False,
                    retry_message=None,
                )
                dedup_topic_record = {
                    "title": topic_model.title,
                    "rotation": topic_model.rotation,
                    "cta": topic_model.cta,
                    "spoken_duration": float(topic_model.spoken_duration),
                }
                all_generated_topics.append(dedup_topic_record)

    if not created_posts:
        logger.warning(
            "topic_discovery_no_posts_after_dedup",
            batch_id=batch_id,
            requested_counts=post_type_counts
        )

        lifestyle_topics = generate_lifestyle_topics(
            count=1
        )

        if lifestyle_topics:
            update_seeding_progress(
                batch_id,
                state=batch["state"],
                stage="writing_posts",
                stage_label="Recovering with a fallback topic",
                detail_message="No unique researched posts survived filtering, so a fallback post is being created.",
                posts_created=len(created_posts),
                expected_posts=expected_posts,
                current_post_type="lifestyle",
                attempt=None,
                max_attempts=None,
                is_retrying=False,
                retry_message=None,
            )
            fallback_topic = lifestyle_topics[0]
            dialog_scripts = fallback_topic["dialog_scripts"]

            add_topic_to_registry(
                title=fallback_topic["title"],
                rotation=fallback_topic["rotation"],
                cta=fallback_topic["cta"]
            )

            seed_payload = build_lifestyle_seed_payload(
                topic_data=fallback_topic,
                dialog_scripts=dialog_scripts
            )

            fallback_post = create_post_for_batch(
                batch_id=batch_id,
                post_type="lifestyle",
                topic_title=fallback_topic["title"],
                topic_rotation=fallback_topic["rotation"],
                topic_cta=fallback_topic["cta"],
                spoken_duration=float(fallback_topic["spoken_duration"]),
                seed_data=seed_payload
            )

            created_posts.append(fallback_post)
            update_seeding_progress(
                batch_id,
                state=batch["state"],
                stage="writing_posts",
                stage_label="Recovered with a fallback topic",
                detail_message=f"{len(created_posts)} of {expected_posts} posts are now ready for script review.",
                posts_created=len(created_posts),
                expected_posts=expected_posts,
                current_post_type="lifestyle",
                attempt=None,
                max_attempts=None,
                is_retrying=False,
                retry_message=None,
            )
            dedup_topic_record = {
                "title": fallback_topic["title"],
                "rotation": fallback_topic["rotation"],
                "cta": fallback_topic["cta"],
                "spoken_duration": float(fallback_topic["spoken_duration"]),
            }
            all_generated_topics.append(dedup_topic_record)

            logger.info(
                "topic_discovery_fallback_created",
                batch_id=batch_id,
                topic_title=fallback_topic["title"],
                post_type="lifestyle"
            )
        else:
            logger.error(
                "topic_discovery_fallback_failed",
                batch_id=batch_id
            )

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


@router.get("", response_model=SuccessResponse)
async def list_topics_endpoint(limit: int = 50, offset: int = 0):
    """List topics from registry."""
    try:
        topics = get_all_topics_from_registry()
        
        # Apply pagination
        paginated = topics[offset:offset + limit]
        
        topic_responses = [
            TopicResponse(
                id=t["id"],
                title=t["title"],
                rotation=t["rotation"],
                cta=t["cta"],
                first_seen_at=t["first_seen_at"],
                last_used_at=t["last_used_at"],
                use_count=t["use_count"]
            )
            for t in paginated
        ]
        
        return SuccessResponse(
            data=TopicListResponse(topics=topic_responses, total=len(topics))
        )
    
    except Exception as e:
        logger.exception("list_topics_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list topics"
        )


@router.post("/cron/discover", response_model=SuccessResponse)
async def cron_topic_discovery(
    authorization: Optional[str] = Header(None)
):
    """
    Vercel Cron endpoint for automated topic discovery.
    Runs every 6 hours to discover topics for batches in S1_SETUP.
    Per Implementation Guide: Vercel Cron
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
