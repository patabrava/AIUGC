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
                collected.extend(rows)
                logger.info(
                    "topic_research_topic_completed",
                    seed_topic=seed_topic,
                    tier=tier,
                    rows_stored=len(rows),
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

    topics_completed = 0
    topics_failed = 0
    topic_ids: List[str] = []
    details: List[Dict[str, Any]] = []

    for seed in seeds:
        logger.info("topic_research_topic_started", seed_topic=seed)
        result = _research_single_topic(
            seed_topic=seed,
            post_type=POST_TYPE,
            tiers=TARGET_TIERS,
        )
        if result:
            topics_completed += 1
            for row in result:
                if row.get("id"):
                    topic_ids.append(row["id"])
            details.append({"seed": seed, "status": "completed", "rows": len(result)})
        else:
            topics_failed += 1
            details.append({"seed": seed, "status": "failed"})

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

    # Audit newly persisted scripts immediately
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


if __name__ == "__main__":
    logger.info(
        "topic_researcher_started",
        interval_hours=RESEARCH_INTERVAL_SECONDS / 3600,
        max_topics=MAX_TOPICS_PER_RUN,
        tiers=TARGET_TIERS,
    )

    _last_run = _get_last_run_timestamp()
    logger.info("topic_researcher_last_run_recovered", last_run=_last_run)

    while True:
        try:
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
