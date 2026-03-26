"""Script bank expansion worker — runs daily to fill the script bank.

Extracted from video_poller.py so video polling is never blocked by
long-running Gemini calls during expansion.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

EXPANSION_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours
MAX_SCRIPTS_PER_RUN = 30


def run_expansion() -> None:
    """Run one cycle of script bank expansion."""
    from app.features.topics.variant_expansion import expand_script_bank

    logger.info(
        "script_bank_expansion_starting",
        max_scripts=MAX_SCRIPTS_PER_RUN,
    )
    result = expand_script_bank(
        max_scripts_per_cron_run=MAX_SCRIPTS_PER_RUN,
    )
    logger.info(
        "script_bank_expansion_complete",
        total_generated=result["total_generated"],
        topics_processed=result["topics_processed"],
    )


def main() -> None:
    settings = get_settings()
    logger.info(
        "expansion_worker_started",
        interval_hours=EXPANSION_INTERVAL_SECONDS / 3600,
        max_scripts=MAX_SCRIPTS_PER_RUN,
        environment=settings.environment,
    )

    while True:
        try:
            run_expansion()
        except KeyboardInterrupt:
            logger.info("expansion_worker_stopped_by_user")
            break
        except Exception:
            logger.exception("expansion_worker_error")

        logger.info(
            "expansion_worker_sleeping",
            next_run_in_hours=EXPANSION_INTERVAL_SECONDS / 3600,
        )
        try:
            time.sleep(EXPANSION_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("expansion_worker_stopped_by_user")
            break


if __name__ == "__main__":
    main()
