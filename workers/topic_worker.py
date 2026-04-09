"""Unified topic worker.

Owns both discovery and audit timing so the deployed topic container can
drain pending scripts and expand coverage without a second topic service.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from workers.audit_worker import run_audit_cycle
from workers.topic_researcher import (
    RESEARCH_INTERVAL_SECONDS as DEFAULT_RESEARCH_INTERVAL_SECONDS,
    POLL_INTERVAL_SECONDS as DEFAULT_POLL_INTERVAL_SECONDS,
    _get_active_cron_timestamp,
    _get_last_run_timestamp,
    _reconcile_stale_running_cron_run,
    run_discovery_cycle,
)

configure_logging()
logger = get_logger(__name__)

AUDIT_INTERVAL_SECONDS = int(os.getenv("TOPIC_AUDIT_INTERVAL_SECONDS", "60"))
RESEARCH_INTERVAL_SECONDS = int(
    os.getenv("TOPIC_RESEARCH_INTERVAL_SECONDS", str(DEFAULT_RESEARCH_INTERVAL_SECONDS))
)
POLL_INTERVAL_SECONDS = int(
    os.getenv("TOPIC_WORKER_POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS))
)


def _resolve_startup_research_timestamp() -> float:
    _reconcile_stale_running_cron_run()
    last_run = _get_last_run_timestamp()
    active_run = _get_active_cron_timestamp()
    return max(last_run, active_run)


def _is_same_utc_day(first_timestamp: float, second_timestamp: float) -> bool:
    first_day = datetime.fromtimestamp(first_timestamp, tz=timezone.utc).date()
    second_day = datetime.fromtimestamp(second_timestamp, tz=timezone.utc).date()
    return first_day == second_day


def _maybe_run_audit(now: float, last_audit_run: float) -> float:
    if (now - last_audit_run) < AUDIT_INTERVAL_SECONDS:
        return last_audit_run

    logger.info(
        "topic_worker_running_audit_cycle",
        interval_seconds=AUDIT_INTERVAL_SECONDS,
    )
    run_audit_cycle()
    return now


def _maybe_run_research(now: float, last_research_run: float) -> float:
    if last_research_run and _is_same_utc_day(now, last_research_run):
        return last_research_run

    logger.info(
        "topic_worker_running_discovery_cycle",
        interval_seconds=RESEARCH_INTERVAL_SECONDS,
    )
    run_discovery_cycle(audit_after_discovery=False)
    return now


def run_topic_worker_tick(
    *,
    last_audit_run: float,
    last_research_run: float,
    now: Optional[float] = None,
) -> Tuple[float, float]:
    """Run one worker tick and return the updated timestamps."""
    current_time = time.time() if now is None else now
    _reconcile_stale_running_cron_run()

    try:
        last_audit_run = _maybe_run_audit(current_time, last_audit_run)
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.exception("topic_worker_audit_cycle_failed")

    try:
        last_research_run = _maybe_run_research(current_time, last_research_run)
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.exception("topic_worker_discovery_cycle_failed")

    return last_audit_run, last_research_run


def main() -> None:
    settings = get_settings()
    last_research_run = _resolve_startup_research_timestamp()
    last_audit_run = 0.0

    logger.info(
        "topic_worker_started",
        environment=settings.environment,
        audit_interval_seconds=AUDIT_INTERVAL_SECONDS,
        research_interval_seconds=RESEARCH_INTERVAL_SECONDS,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
        last_research_run=last_research_run,
    )

    while True:
        try:
            last_audit_run, last_research_run = run_topic_worker_tick(
                last_audit_run=last_audit_run,
                last_research_run=last_research_run,
            )
        except KeyboardInterrupt:
            logger.info("topic_worker_stopped_by_user")
            break
        except Exception:
            logger.exception("topic_worker_error")

        logger.info(
            "topic_worker_sleeping",
            next_run_in_seconds=POLL_INTERVAL_SECONDS,
        )
        try:
            time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("topic_worker_stopped_by_user")
            break


if __name__ == "__main__":
    main()
