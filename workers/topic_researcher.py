"""
Topic Research Worker
Discovers and deep-researches new topics daily.
Runs as a separate Docker service alongside the video poller.
"""

import time
import sys
import os
from datetime import datetime, timezone
from dateutil.parser import isoparse
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import configure_logging, get_logger
from app.core.config import get_settings
from app.features.topics.queries import (
    create_cron_run,
    update_cron_run,
    get_latest_cron_run,
    get_all_topics_from_registry,
    list_topic_research_runs,
)
from workers.topic_seed_selector import select_seeds

configure_logging()
logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────
RESEARCH_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours
MAX_TOPICS_PER_RUN = 5
POLL_INTERVAL_SECONDS = 60
TARGET_TIERS = [8, 16, 32]
POST_TYPE = "value"
NICHE = os.environ.get("CRON_RESEARCH_NICHE", "Schwerbehinderung, Treppenlifte, Barrierefreiheit")
CRON_STALE_AFTER_SECONDS = 15 * 60

# Gemini rate limit backoff
BACKOFF_DELAYS = [30, 60, 120]


def _get_last_run_timestamp() -> float:
    """Get timestamp of last completed cron run from DB. Returns 0.0 if none."""
    latest = get_latest_cron_run(status="completed")
    if not latest or not latest.get("completed_at"):
        return 0.0
    try:
        dt = isoparse(latest["completed_at"])
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _get_active_cron_timestamp() -> float:
    """Get timestamp for any active cron wrapper so startup won't double-launch."""
    latest = get_latest_cron_run(status="running")
    if not latest:
        return 0.0
    for field in ("updated_at", "created_at", "started_at"):
        ts = _parse_utc_timestamp(latest.get(field))
        if ts is not None:
            return ts.timestamp()
    return 0.0


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    try:
        parsed = isoparse(str(value or ""))
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _harvest_seed_topic_to_bank(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    existing_topics: List[Dict[str, Any]],
    collected_topics: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Wrapper around hub._harvest_seed_topic_to_bank for the worker context."""
    from app.features.topics.hub import _harvest_seed_topic_to_bank as harvest
    return harvest(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        existing_topics=existing_topics,
        collected_topics=collected_topics,
    )


def _normalize_stored_rows(result: Any) -> List[Dict[str, Any]]:
    """Normalize harvest outputs into a flat list of row dictionaries."""
    if not result:
        return []

    rows: Any
    if isinstance(result, dict):
        rows = result.get("stored_rows") or result.get("rows") or result.get("data") or []
    elif isinstance(result, list):
        rows = result
    else:
        return []

    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []

    normalized_rows: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(row)
    return normalized_rows


def _heartbeat_cron_run(
    *,
    run_id: str,
    topics_completed: int,
    topics_failed: int,
    topic_ids: List[str],
    details: List[Dict[str, Any]],
) -> None:
    update_cron_run(
        run_id,
        status="running",
        topics_completed=topics_completed,
        topics_failed=topics_failed,
        topic_ids=topic_ids,
        details={"per_topic": details},
    )


def _reconcile_stale_running_cron_run(max_age_seconds: int = CRON_STALE_AFTER_SECONDS) -> Optional[Dict[str, Any]]:
    """Close a stale running wrapper when the worker lost its terminal update."""
    latest = get_latest_cron_run(status="running")
    if not latest:
        return None

    started_at = _parse_utc_timestamp(latest.get("started_at"))
    updated_at = _parse_utc_timestamp(latest.get("updated_at") or latest.get("started_at"))
    if started_at is None or updated_at is None:
        return None

    running_children = [
        row
        for row in list_topic_research_runs(limit=50, status="running")
        if (created_at := _parse_utc_timestamp(row.get("created_at"))) is not None and created_at >= started_at
    ]
    if running_children:
        return None

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_seconds < max_age_seconds:
        return None

    topics_requested = int(latest.get("topics_requested") or 0)
    topics_completed = int(latest.get("topics_completed") or 0)
    topics_failed = int(latest.get("topics_failed") or 0)

    completed_children = [
        row
        for row in list_topic_research_runs(limit=50, status="completed")
        if (created_at := _parse_utc_timestamp(row.get("created_at"))) is not None and created_at >= started_at
    ]
    failed_children = [
        row
        for row in list_topic_research_runs(limit=50, status="failed")
        if (created_at := _parse_utc_timestamp(row.get("created_at"))) is not None and created_at >= started_at
    ]
    details = {
        "recovered": True,
        "reason": "stale_running_wrapper",
        "stale_after_seconds": max_age_seconds,
        "completed_children": len(completed_children),
        "failed_children": len(failed_children),
        "last_updated_at": latest.get("updated_at") or latest.get("started_at"),
    }
    effective_topics_completed = topics_completed if topics_completed else len(completed_children)
    effective_topics_failed = topics_failed if topics_failed else len(failed_children)
    final_status = "completed" if topics_requested and effective_topics_completed >= topics_requested and effective_topics_failed == 0 else "failed"
    topic_ids = [
        str(row.get("topic_registry_id") or (row.get("result_summary") or {}).get("topic_registry_id") or "").strip()
        for row in completed_children
    ]
    topic_ids = [topic_id for topic_id in topic_ids if topic_id]
    return update_cron_run(
        latest["id"],
        status=final_status,
        topics_completed=effective_topics_completed,
        topics_failed=effective_topics_failed,
        topic_ids=topic_ids or None,
        details=details,
        error_message=None if final_status == "completed" else "Recovered stale running cron run after inactivity",
    )


def _research_single_topic(
    seed_topic: str,
    post_type: str,
    tiers: List[int],
) -> Optional[List[Dict[str, Any]]]:
    """Research a single topic across all tiers. Returns stored rows or None on failure."""
    existing_topics = get_all_topics_from_registry()
    collected: List[Dict[str, Any]] = []

    for tier in tiers:
        for attempt in range(len(BACKOFF_DELAYS) + 1):
            try:
                rows = _harvest_seed_topic_to_bank(
                    seed_topic=seed_topic,
                    post_type=post_type,
                    target_length_tier=tier,
                    existing_topics=existing_topics,
                    collected_topics=collected,
                )
                normalized_rows = _normalize_stored_rows(rows)
                collected.extend(normalized_rows)
                logger.info(
                    "topic_research_topic_completed",
                    seed_topic=seed_topic,
                    tier=tier,
                    rows_stored=len(normalized_rows),
                )
                break
            except Exception as exc:
                error_msg = str(exc)
                is_rate_limit = "429" in error_msg or "rate" in error_msg.lower()
                if is_rate_limit and attempt < len(BACKOFF_DELAYS):
                    delay = BACKOFF_DELAYS[attempt]
                    logger.warning(
                        "topic_research_rate_limit",
                        seed_topic=seed_topic,
                        tier=tier,
                        attempt=attempt + 1,
                        backoff_seconds=delay,
                    )
                    time.sleep(delay)
                    continue
                logger.exception(
                    "topic_research_topic_failed",
                    seed_topic=seed_topic,
                    tier=tier,
                    error=error_msg,
                )
                break  # Skip remaining tiers but keep partial results

    return collected if collected else None


def run_discovery_cycle():
    """Execute one full discovery cycle: select seeds, research them, track in DB."""
    run_id: Optional[str] = None
    topics_completed = 0
    topics_failed = 0
    topic_ids: List[str] = []
    details: List[Dict[str, Any]] = []
    seeds: List[str] = []

    try:
        seeds, source = select_seeds(max_topics=MAX_TOPICS_PER_RUN, niche=NICHE)

        logger.info(
            "topic_research_seed_selection",
            seed_count=len(seeds),
            source=source,
            seeds=seeds,
        )

        run_record = create_cron_run(
            topics_requested=len(seeds),
            seed_source=source,
        )
        run_id = run_record["id"]

        for seed in seeds:
            logger.info("topic_research_topic_started", seed_topic=seed)
            result = _research_single_topic(
                seed_topic=seed,
                post_type=POST_TYPE,
                tiers=TARGET_TIERS,
            )
            topic_rows = _normalize_stored_rows(result)
            if topic_rows:
                topics_completed += 1
                for row in topic_rows:
                    topic_id = str(row.get("id") or "").strip()
                    if topic_id:
                        topic_ids.append(topic_id)
                details.append({"seed": seed, "status": "completed", "rows": len(topic_rows)})
            else:
                topics_failed += 1
                details.append({"seed": seed, "status": "failed"})

            _heartbeat_cron_run(
                run_id=run_id,
                topics_completed=topics_completed,
                topics_failed=topics_failed,
                topic_ids=topic_ids,
                details=details,
            )

        final_status = "completed" if topics_failed < len(seeds) else "failed"
        error_msg = None
        if topics_failed == len(seeds) and len(seeds) > 0:
            error_msg = f"All {topics_failed} topics failed"

        update_cron_run(
            run_id,
            status=final_status,
            topics_completed=topics_completed,
            topics_failed=topics_failed,
            topic_ids=topic_ids,
            details={"per_topic": details},
            error_message=error_msg,
        )

        if topics_completed > 0:
            try:
                from workers.audit_worker import run_audit_cycle
                logger.info("topic_research_triggering_audit")
                run_audit_cycle()
            except Exception:
                logger.exception("topic_research_audit_trigger_failed")

        logger.info(
            "topic_research_cron_complete",
            run_id=run_id,
            topics_completed=topics_completed,
            topics_failed=topics_failed,
            topic_ids=topic_ids,
        )
    except Exception as exc:
        logger.exception("topic_research_cron_failed", error=str(exc), run_id=run_id)
        if run_id is not None:
            try:
                update_cron_run(
                    run_id,
                    status="failed",
                    topics_completed=topics_completed,
                    topics_failed=max(topics_failed, 1 if seeds else 0),
                    topic_ids=topic_ids,
                    details={"per_topic": details},
                    error_message=str(exc),
                )
            except Exception:
                logger.exception("topic_research_cron_failure_mark_failed", run_id=run_id)


if __name__ == "__main__":
    logger.info(
        "topic_researcher_started",
        interval_hours=RESEARCH_INTERVAL_SECONDS / 3600,
        max_topics=MAX_TOPICS_PER_RUN,
        tiers=TARGET_TIERS,
    )

    _last_run = _get_last_run_timestamp()
    _active_run = _get_active_cron_timestamp()
    if _active_run > _last_run:
        _last_run = _active_run
    logger.info("topic_researcher_last_run_recovered", last_run=_last_run)

    while True:
        try:
            _reconcile_stale_running_cron_run()
            now = time.time()
            if (now - _last_run) >= RESEARCH_INTERVAL_SECONDS:
                logger.info("topic_research_cron_starting")
                run_discovery_cycle()
                _last_run = time.time()
        except KeyboardInterrupt:
            logger.info("topic_researcher_stopped_by_user")
            break
        except Exception as exc:
            logger.exception("topic_research_cron_failed", error=str(exc))

        time.sleep(POLL_INTERVAL_SECONDS)
