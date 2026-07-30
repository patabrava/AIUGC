"""Single-owner scheduler for due social and blog publishing.

Production web containers deliberately disable in-process schedulers. This
worker is the durable owner that keeps scheduled publishing alive regardless
of web-process restarts or scaling.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.features.blog.handlers import run_scheduled_blog_publish_job
from app.features.publish.handlers import run_scheduled_publish_job

configure_logging()
logger = get_logger(__name__)

POLL_SECONDS = max(5.0, float(os.getenv("PUBLISH_SCHEDULER_POLL_SECONDS", "60")))


async def run_cycle() -> tuple[dict[str, Any], dict[str, Any]]:
    """Run both schedule queues once without letting one starve the other."""
    social_result, blog_result = await asyncio.gather(
        run_scheduled_publish_job(),
        run_scheduled_blog_publish_job(),
        return_exceptions=True,
    )

    if isinstance(social_result, BaseException):
        logger.error("social_publish_scheduler_cycle_failed", error=str(social_result))
        social_result = {"processed": 0, "error": str(social_result)}
    if isinstance(blog_result, BaseException):
        logger.error("blog_publish_scheduler_cycle_failed", error=str(blog_result))
        blog_result = {"processed": 0, "error": str(blog_result)}

    logger.info(
        "publish_scheduler_cycle_complete",
        social=social_result,
        blog=blog_result,
    )
    return social_result, blog_result


async def main() -> None:
    settings = get_settings()
    logger.info(
        "publish_scheduler_started",
        environment=settings.environment,
        poll_seconds=POLL_SECONDS,
    )
    while True:
        await run_cycle()
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("publish_scheduler_stopped")
