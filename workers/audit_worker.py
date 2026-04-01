"""Audit Worker — promotes persisted pending scripts into selectable coverage."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import configure_logging, get_logger
from app.adapters.llm_client import get_llm_client
from app.features.topics.audit import audit_batch
from app.features.topics.queries import get_unaudited_scripts, update_script_quality

configure_logging()
logger = get_logger(__name__)

AUDIT_INTERVAL_SECONDS = int(os.getenv("TOPIC_AUDIT_INTERVAL_SECONDS", "60"))
MAX_SCRIPTS_PER_RUN = 50


def run_audit_cycle() -> None:
    """Run one audit cycle: fetch unaudited scripts, evaluate, write results."""
    rows = get_unaudited_scripts(limit=MAX_SCRIPTS_PER_RUN)
    if not rows:
        logger.info("audit_cycle_no_pending_scripts")
        return

    logger.info("audit_cycle_starting", pending_count=len(rows))
    llm = get_llm_client()
    results = audit_batch(rows, llm=llm)

    for result in results:
        update_script_quality(
            script_id=result.script_id,
            quality_score=result.total_score,
            quality_notes=result.quality_notes,
            audit_status=result.status,
        )

    pass_count = sum(1 for r in results if r.status == "pass")
    repair_count = sum(1 for r in results if r.status == "needs_repair")
    reject_count = sum(1 for r in results if r.status == "reject")

    logger.info(
        "audit_cycle_complete",
        total=len(results),
        passed=pass_count,
        needs_repair=repair_count,
        rejected=reject_count,
    )


def main() -> None:
    logger.info(
        "audit_worker_started",
        interval_hours=AUDIT_INTERVAL_SECONDS / 3600,
        max_scripts=MAX_SCRIPTS_PER_RUN,
    )

    while True:
        try:
            run_audit_cycle()
        except KeyboardInterrupt:
            logger.info("audit_worker_stopped_by_user")
            break
        except Exception:
            logger.exception("audit_worker_error")

        logger.info(
            "audit_worker_sleeping",
            next_run_in_hours=AUDIT_INTERVAL_SECONDS / 3600,
        )
        try:
            time.sleep(AUDIT_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("audit_worker_stopped_by_user")
            break


if __name__ == "__main__":
    main()
