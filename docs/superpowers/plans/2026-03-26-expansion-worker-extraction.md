# Expansion Worker Extraction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract script bank expansion from the video poller into a standalone worker so video polling is never blocked.

**Architecture:** Create `workers/expansion_worker.py` that runs `expand_script_bank()` on a 24-hour cycle. Remove all expansion code from `workers/video_poller.py`.

**Tech Stack:** Python 3.9+ (no new deps)

---

### Task 1: Create expansion worker

**Files:**
- Create: `workers/expansion_worker.py`

- [ ] **Step 1: Create `workers/expansion_worker.py`**

```python
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
```

- [ ] **Step 2: Verify it starts without import errors**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && source .venv/bin/activate && python -c "import workers.expansion_worker; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add workers/expansion_worker.py
git commit -m "feat: add standalone expansion worker"
```

---

### Task 2: Remove expansion from video poller

**Files:**
- Modify: `workers/video_poller.py`

- [ ] **Step 1: Remove expansion constants (lines 61-62)**

Remove these two lines from the constants section:
```python
EXPANSION_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours
EXPANSION_MAX_SCRIPTS_PER_RUN = 30
```

- [ ] **Step 2: Remove `_maybe_expand_script_bank` function (lines 1054-1087)**

Delete the entire function:
```python
def _maybe_expand_script_bank(last_expansion_time: float) -> float:
    ...
```

- [ ] **Step 3: Simplify the `__main__` block**

Replace the current `if __name__ == "__main__":` block (lines 1090-1120) with:

```python
if __name__ == "__main__":
    logger.info(
        "video_poller_started",
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
        poller_identity=_poller_identity(),
    )

    while True:
        try:
            poll_pending_videos()
        except KeyboardInterrupt:
            logger.info("video_poller_stopped_by_user")
            break
        except Exception as e:
            logger.exception("video_poller_unexpected_error", error=str(e))

        time.sleep(POLL_INTERVAL_SECONDS)
```

- [ ] **Step 4: Verify syntax and existing tests pass**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && source .venv/bin/activate && python -c "import ast; ast.parse(open('workers/video_poller.py').read()); print('OK')" && pytest tests/test_veo_client_payload.py tests/test_caption_aligner.py -q`

Expected: `OK` and all tests pass.

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py
git commit -m "refactor: remove expansion from video poller — now in expansion_worker.py"
```
