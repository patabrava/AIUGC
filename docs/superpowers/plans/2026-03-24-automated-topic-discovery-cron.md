# Automated Topic Discovery Cron — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Docker worker service that automatically discovers and deep-researches new topics daily, filling the topic bank independently of batches.

**Architecture:** New `workers/topic_researcher.py` with a sleep-loop pattern (matching `video_poller.py`). Uses the existing 3-stage pipeline from `hub.py` / `research_runtime.py`. Tracks runs in a new `topic_research_cron_runs` Supabase table. Exposes a `GET /topics/cron-status` health endpoint.

**Tech Stack:** Python, FastAPI, Supabase (PostgreSQL), Gemini API, Docker Compose

**Spec:** `docs/superpowers/specs/2026-03-24-automated-topic-discovery-cron-design.md`

---

### Task 1: Create `topic_research_cron_runs` Supabase Table

**Files:**
- Supabase migration (via MCP `apply_migration`)

- [ ] **Step 1: Apply the migration**

Use the Supabase MCP to apply a migration named `create_topic_research_cron_runs`:

```sql
CREATE TABLE IF NOT EXISTS topic_research_cron_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    topics_requested INT NOT NULL DEFAULT 0,
    topics_completed INT NOT NULL DEFAULT 0,
    topics_failed INT NOT NULL DEFAULT 0,
    seed_source TEXT CHECK (seed_source IN ('yaml_bank', 'llm_generated', 'mixed')),
    topic_ids JSONB DEFAULT '[]'::jsonb,
    error_message TEXT,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_topic_research_cron_runs_status ON topic_research_cron_runs (status);
CREATE INDEX idx_topic_research_cron_runs_started_at ON topic_research_cron_runs (started_at DESC);
```

- [ ] **Step 2: Verify the table exists**

Use the Supabase MCP `list_tables` to confirm `topic_research_cron_runs` appears.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: create topic_research_cron_runs tracking table"
```

---

### Task 2: Add CRUD Functions for Cron Run Tracking

**Files:**
- Modify: `app/features/topics/queries.py` (append to end of file)
- Test: `tests/test_topic_researcher_queries.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_topic_researcher_queries.py`:

```python
"""Tests for topic_research_cron_runs CRUD functions."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import MagicMock, patch


def _mock_supabase():
    mock = MagicMock()
    mock.client.table.return_value = mock.client.table
    mock.client.table.insert.return_value = mock.client.table
    mock.client.table.update.return_value = mock.client.table
    mock.client.table.select.return_value = mock.client.table
    mock.client.table.eq.return_value = mock.client.table
    mock.client.table.order.return_value = mock.client.table
    mock.client.table.limit.return_value = mock.client.table
    mock.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "started_at": "2026-03-24T00:00:00Z",
        "completed_at": None,
        "status": "running",
        "topics_requested": 5,
        "topics_completed": 0,
        "topics_failed": 0,
        "seed_source": "yaml_bank",
        "topic_ids": [],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    return mock


@patch("app.features.topics.queries.get_supabase")
def test_create_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import create_cron_run
    result = create_cron_run(topics_requested=5, seed_source="yaml_bank")
    assert result["status"] == "running"
    assert result["topics_requested"] == 5


@patch("app.features.topics.queries.get_supabase")
def test_update_cron_run(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "status": "completed",
        "topics_completed": 4,
        "topics_failed": 1,
        "completed_at": "2026-03-24T00:10:00Z",
        "topics_requested": 5,
        "started_at": "2026-03-24T00:00:00Z",
        "seed_source": "yaml_bank",
        "topic_ids": ["t1", "t2", "t3", "t4"],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import update_cron_run
    result = update_cron_run(
        run_id="run-1",
        status="completed",
        topics_completed=4,
        topics_failed=1,
        topic_ids=["t1", "t2", "t3", "t4"],
    )
    assert result["status"] == "completed"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is not None
    assert result["id"] == "run-1"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run_empty(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is None


@patch("app.features.topics.queries.get_supabase")
def test_get_cron_run_stats(mock_get_sb):
    mock_sb = _mock_supabase()
    # Mock for count query
    mock_sb.client.table.execute.return_value = MagicMock(data=[
        {"status": "completed", "topics_completed": 4},
        {"status": "completed", "topics_completed": 3},
    ])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_cron_run_stats
    result = get_cron_run_stats()
    assert "total_runs" in result
    assert "total_topics_researched" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher_queries.py -v`
Expected: FAIL with `ImportError` — functions don't exist yet

- [ ] **Step 3: Implement CRUD functions**

Append to `app/features/topics/queries.py`:

```python
# ── Cron Run Tracking ────────────────────────────────────────────

def create_cron_run(
    *,
    topics_requested: int,
    seed_source: str,
) -> Dict[str, Any]:
    """Create a new cron run record with status='running'."""
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").insert({
        "topics_requested": topics_requested,
        "seed_source": seed_source,
        "status": "running",
    }).execute()
    return result.data[0]


def update_cron_run(
    run_id: str,
    *,
    status: str,
    topics_completed: int = 0,
    topics_failed: int = 0,
    topic_ids: Optional[List[str]] = None,
    error_message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update a cron run record on completion or failure."""
    from datetime import datetime, timezone
    payload: Dict[str, Any] = {"status": status}
    if status in ("completed", "failed"):
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["topics_completed"] = topics_completed
    payload["topics_failed"] = topics_failed
    if topic_ids is not None:
        payload["topic_ids"] = topic_ids
    if error_message is not None:
        payload["error_message"] = error_message
    if details is not None:
        payload["details"] = details
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").update(
        payload
    ).eq("id", run_id).execute()
    return result.data[0]


def get_latest_cron_run(*, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get the most recent cron run, optionally filtered by status."""
    sb = get_supabase()
    query = sb.client.table("topic_research_cron_runs").select("*").order(
        "started_at", desc=True
    ).limit(1)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data[0] if result.data else None


def get_cron_run_stats() -> Dict[str, Any]:
    """Get aggregate stats across all cron runs."""
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").select(
        "status,topics_completed"
    ).execute()
    rows = result.data or []
    total_runs = len(rows)
    total_topics = sum(r.get("topics_completed", 0) for r in rows)
    return {"total_runs": total_runs, "total_topics_researched": total_topics}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher_queries.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/queries.py tests/test_topic_researcher_queries.py
git commit -m "feat: add CRUD functions for topic_research_cron_runs table"
```

---

### Task 3: Add Seed Topic Selection Logic

**Files:**
- Create: `workers/topic_seed_selector.py`
- Test: `tests/test_topic_seed_selector.py` (create)

This module handles Phase 1 (YAML bank) and Phase 2 (LLM-generated) seed selection.

- [ ] **Step 1: Write failing tests**

Create `tests/test_topic_seed_selector.py`:

```python
"""Tests for topic seed selection logic."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock
import pytest


def test_load_seed_topics_from_yaml():
    """Loads and flattens topic_bank.yaml categories."""
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert isinstance(topics, list)
    assert len(topics) > 0
    assert all(isinstance(t, str) for t in topics)


def test_load_seed_topics_missing_file(tmp_path, monkeypatch):
    """Returns empty list when YAML file is missing."""
    monkeypatch.setattr(
        "workers.topic_seed_selector.TOPIC_BANK_PATH",
        str(tmp_path / "nonexistent.yaml"),
    )
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert topics == []


def test_load_seed_topics_malformed_yaml(tmp_path, monkeypatch):
    """Returns empty list on malformed YAML."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("{{{{not yaml")
    monkeypatch.setattr(
        "workers.topic_seed_selector.TOPIC_BANK_PATH",
        str(bad_yaml),
    )
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert topics == []


@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_filter_unresearched_seeds(mock_get_topics):
    """Filters out seeds that already have registry entries."""
    mock_get_topics.return_value = [
        {"title": "Topic A", "id": "1"},
        {"title": "Topic B", "id": "2"},
    ]
    from workers.topic_seed_selector import filter_unresearched_seeds
    seeds = ["Topic A", "Topic C", "Topic D"]
    result = filter_unresearched_seeds(seeds)
    # Topic A already in registry, should be excluded
    assert "Topic A" not in result
    assert "Topic C" in result
    assert "Topic D" in result


@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_select_seeds_phase1_sufficient(mock_get_topics):
    """When YAML bank has enough unresearched seeds, uses only Phase 1."""
    mock_get_topics.return_value = []  # No existing topics
    from workers.topic_seed_selector import select_seeds
    with patch("workers.topic_seed_selector.load_seed_topics_from_yaml") as mock_yaml:
        mock_yaml.return_value = [f"Seed {i}" for i in range(20)]
        seeds, source = select_seeds(max_topics=5)
    assert len(seeds) == 5
    assert source == "yaml_bank"


@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_select_seeds_phase2_fallback(mock_get_topics):
    """When YAML bank is empty, falls back to LLM generation."""
    mock_get_topics.return_value = [
        {"title": f"Seed {i}", "id": str(i)} for i in range(20)
    ]
    from workers.topic_seed_selector import select_seeds
    with patch("workers.topic_seed_selector.load_seed_topics_from_yaml") as mock_yaml:
        mock_yaml.return_value = [f"Seed {i}" for i in range(20)]  # All already researched
        with patch("workers.topic_seed_selector._generate_llm_seeds") as mock_llm:
            mock_llm.return_value = ["New LLM Topic 1", "New LLM Topic 2"]
            seeds, source = select_seeds(max_topics=2)
    assert source in ("llm_generated", "mixed")
    assert len(seeds) <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_seed_selector.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement seed selector**

Create `workers/topic_seed_selector.py`:

```python
"""
Topic Seed Selector
Determines which topics to research next: Phase 1 (YAML bank) then Phase 2 (LLM-generated).
"""
import os
import sys
from typing import List, Tuple

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import get_logger
from app.features.topics.queries import get_all_topics_from_registry
from app.features.topics.deduplication import tokenize, jaccard_similarity

logger = get_logger(__name__)

TOPIC_BANK_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "features",
    "topics",
    "prompt_data",
    "topic_bank.yaml",
)

DEFAULT_NICHE = "Schwerbehinderung, Treppenlifte, Barrierefreiheit"


def load_seed_topics_from_yaml() -> List[str]:
    """Load and flatten all seed topics from topic_bank.yaml.

    Returns empty list if file is missing or malformed.
    """
    try:
        with open(TOPIC_BANK_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("topic_bank_yaml_missing", path=TOPIC_BANK_PATH)
        return []
    except yaml.YAMLError as exc:
        logger.warning("topic_bank_yaml_malformed", path=TOPIC_BANK_PATH, error=str(exc))
        return []

    if not isinstance(data, dict):
        return []

    # Prefer flat "topics" list if present
    if "topics" in data and isinstance(data["topics"], list):
        return [str(t) for t in data["topics"] if t]

    # Otherwise flatten categories
    topics = []
    for category in data.get("categories", []):
        for topic in category.get("topics", []):
            if topic:
                topics.append(str(topic))
    return topics


def filter_unresearched_seeds(seed_topics: List[str]) -> List[str]:
    """Filter out seeds that already exist in topic_registry (by title similarity)."""
    existing = get_all_topics_from_registry()
    existing_titles = {t["title"].lower().strip() for t in existing if t.get("title")}

    unresearched = []
    for seed in seed_topics:
        seed_lower = seed.lower().strip()
        # Exact match check
        if seed_lower in existing_titles:
            continue
        # Fuzzy match: Jaccard > 0.7 means too similar
        is_dup = False
        seed_tokens = tokenize(seed)
        for title in existing_titles:
            if jaccard_similarity(seed_tokens, tokenize(title)) > 0.7:
                is_dup = True
                break
        if not is_dup:
            unresearched.append(seed)
    return unresearched


def _generate_llm_seeds(
    existing_titles: List[str],
    count: int,
    niche: str,
) -> List[str]:
    """Generate new seed topic ideas via Gemini."""
    from app.adapters.llm_client import get_llm_client

    llm = get_llm_client()
    existing_str = "\n".join(f"- {t}" for t in existing_titles[:100])
    prompt = (
        f"Du bist ein Content-Stratege für den Bereich: {niche}.\n\n"
        f"Hier sind Themen, die wir bereits behandelt haben:\n{existing_str}\n\n"
        f"Generiere genau {count} NEUE, einzigartige Content-Themen für kurze Social-Media-Videos "
        f"(8-32 Sekunden). Jedes Thema soll ein konkretes Problem, einen Tipp oder eine "
        f"überraschende Tatsache für Menschen mit Behinderung / Rollstuhlfahrer behandeln.\n\n"
        f"Antworte NUR mit einer nummerierten Liste, ein Thema pro Zeile. "
        f"Keine Erklärungen, keine Einleitungen."
    )
    response = llm.generate_gemini_text(
        prompt=prompt,
        model_name=None,  # Uses default gemini_topic_model
    )
    lines = [
        line.strip().lstrip("0123456789.)- ").strip()
        for line in response.strip().split("\n")
        if line.strip()
    ]
    return [l for l in lines if len(l) > 10][:count]


def select_seeds(
    max_topics: int = 5,
    niche: str = DEFAULT_NICHE,
) -> Tuple[List[str], str]:
    """Select seed topics to research.

    Returns (seeds, source) where source is 'yaml_bank', 'llm_generated', or 'mixed'.
    """
    # Phase 1: YAML bank
    yaml_seeds = load_seed_topics_from_yaml()
    unresearched = filter_unresearched_seeds(yaml_seeds)

    if len(unresearched) >= max_topics:
        return unresearched[:max_topics], "yaml_bank"

    # Phase 2: Top up with LLM-generated seeds
    seeds = list(unresearched)
    remaining = max_topics - len(seeds)
    llm_added = 0

    if remaining > 0:
        existing = get_all_topics_from_registry()
        existing_titles = [t["title"] for t in existing if t.get("title")]
        llm_seeds = _generate_llm_seeds(existing_titles, remaining, niche)
        # Deduplicate LLM seeds against existing
        llm_filtered = filter_unresearched_seeds(llm_seeds)
        seeds.extend(llm_filtered[:remaining])
        llm_added = len(llm_filtered[:remaining])

    if llm_added == 0:
        source = "yaml_bank"
    elif len(unresearched) == 0:
        source = "llm_generated"
    else:
        source = "mixed"
    return seeds[:max_topics], source
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_seed_selector.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add workers/topic_seed_selector.py tests/test_topic_seed_selector.py
git commit -m "feat: add topic seed selector with YAML bank + LLM fallback"
```

---

### Task 4: Create the Topic Researcher Worker

**Files:**
- Create: `workers/topic_researcher.py`
- Test: `tests/test_topic_researcher.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_topic_researcher.py`:

```python
"""Tests for the topic researcher worker."""
import os
import time
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock


def test_should_run_no_previous_runs():
    """Should run immediately if no previous cron runs in DB."""
    with patch("workers.topic_researcher.get_latest_cron_run", return_value=None):
        from workers.topic_researcher import _get_last_run_timestamp
        ts = _get_last_run_timestamp()
    assert ts == 0.0


def test_should_run_recent_run():
    """Should not run if last completed run was recent."""
    from workers.topic_researcher import _get_last_run_timestamp, RESEARCH_INTERVAL_SECONDS
    recent_time = "2026-03-24T12:00:00+00:00"
    with patch("workers.topic_researcher.get_latest_cron_run", return_value={
        "completed_at": recent_time,
        "status": "completed",
    }):
        ts = _get_last_run_timestamp()
    assert ts > 0.0


def test_research_single_topic_success():
    """Successfully researches a single topic through the 3-stage pipeline."""
    from workers.topic_researcher import _research_single_topic

    with patch("workers.topic_researcher._harvest_seed_topic_to_bank") as mock_harvest, \
         patch("workers.topic_researcher.get_all_topics_from_registry", return_value=[]):
        mock_harvest.return_value = [{"id": "topic-1", "title": "Test Topic"}]
        result = _research_single_topic(
            seed_topic="Test Seed",
            post_type="value",
            tiers=[8, 16, 32],
        )
    assert result is not None
    assert len(result) > 0


def test_research_single_topic_failure():
    """Handles failure gracefully, returns None."""
    from workers.topic_researcher import _research_single_topic

    with patch("workers.topic_researcher._harvest_seed_topic_to_bank", side_effect=Exception("Gemini error")), \
         patch("workers.topic_researcher.get_all_topics_from_registry", return_value=[]):
        result = _research_single_topic(
            seed_topic="Bad Seed",
            post_type="value",
            tiers=[8, 16, 32],
        )
    # Returns None because all tiers failed (break on first tier failure, no collected results)
    assert result is None


def test_run_discovery_cycle():
    """Full cycle: selects seeds, researches them, tracks in DB."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update, \
         patch("workers.topic_researcher._research_single_topic") as mock_research:

        mock_select.return_value = (["Topic A", "Topic B"], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_research.return_value = [{"id": "t-1", "title": "Topic A"}]
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_create.assert_called_once()
    assert mock_research.call_count == 2
    mock_update.assert_called_once()
    update_kwargs = mock_update.call_args
    assert update_kwargs[1]["status"] == "completed"


def test_run_discovery_cycle_no_seeds():
    """When no seeds available, creates run with 0 topics."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update:

        mock_select.return_value = ([], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_update.assert_called_once()
    assert mock_update.call_args[1]["topics_completed"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement the worker**

Create `workers/topic_researcher.py`:

```python
"""
Topic Research Worker
Discovers and deep-researches new topics daily.
Runs as a separate Docker service alongside the video poller.
Per Design Spec: docs/superpowers/specs/2026-03-24-automated-topic-discovery-cron-design.md
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
            # Don't update _last_run so it retries

        time.sleep(POLL_INTERVAL_SECONDS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add workers/topic_researcher.py tests/test_topic_researcher.py
git commit -m "feat: add topic researcher worker with daily discovery cycle"
```

---

### Task 5: Add Health / Status Endpoint

**Files:**
- Modify: `app/features/topics/handlers.py` (add endpoint near existing cron endpoint ~line 1507)
- Test: `tests/test_topic_researcher_status.py` (create)

- [ ] **Step 1: Write failing test**

Create `tests/test_topic_researcher_status.py`:

```python
"""Tests for the topic researcher cron status endpoint."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_with_data(mock_sb, mock_stats, mock_latest):
    mock_sb.return_value = MagicMock()
    mock_sb.return_value.health_check.return_value = True
    mock_latest.return_value = {
        "id": "run-1",
        "started_at": "2026-03-23T06:00:00+00:00",
        "completed_at": "2026-03-23T06:12:34+00:00",
        "status": "completed",
        "topics_completed": 4,
        "topics_failed": 1,
        "seed_source": "yaml_bank",
    }
    mock_stats.return_value = {"total_runs": 12, "total_topics_researched": 47}

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/topics/cron-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"]["status"] == "completed"
    assert data["total_runs"] == 12


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_no_runs(mock_sb, mock_stats, mock_latest):
    mock_sb.return_value = MagicMock()
    mock_sb.return_value.health_check.return_value = True
    mock_latest.return_value = None
    mock_stats.return_value = {"total_runs": 0, "total_topics_researched": 0}

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/topics/cron-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"] is None
    assert data["total_runs"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher_status.py -v`
Expected: FAIL — endpoint doesn't exist (404)

- [ ] **Step 3: Implement the endpoint**

Add to `app/features/topics/handlers.py` after the existing `/cron/discover` endpoint (after line ~1507):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher_status.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/handlers.py tests/test_topic_researcher_status.py
git commit -m "feat: add GET /topics/cron-status health endpoint"
```

---

### Task 6: Add Docker Service

**Files:**
- Modify: `docker-compose.yml` (add `topic-researcher` service)
- Modify: `docker-compose.yaml` (add `topic-researcher` service for the simpler compose file)

- [ ] **Step 1: Add `CRON_RESEARCH_NICHE` to the env anchor in `docker-compose.yml`**

Add to the `x-flow-forge-env` block (after the `CRON_SECRET` line):

```yaml
  CRON_RESEARCH_NICHE: ${CRON_RESEARCH_NICHE:-Schwerbehinderung, Treppenlifte, Barrierefreiheit}
```

- [ ] **Step 2: Add service to `docker-compose.yml`**

Add after the `worker` service block (after line 81):

```yaml

  topic-researcher:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["python", "workers/topic_researcher.py"]
    environment: *flow-forge-env
    restart: unless-stopped
    depends_on:
      web:
        condition: service_healthy
```

- [ ] **Step 3: Add service to `docker-compose.yaml`**

Add after the `worker` service block (after line 42):

```yaml

  topic-researcher:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["python", "workers/topic_researcher.py"]
    env_file:
      - .env
    restart: unless-stopped
    depends_on:
      web:
        condition: service_healthy
```

- [ ] **Step 4: Verify compose file is valid**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && docker compose -f docker-compose.yml config --quiet`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.yaml
git commit -m "feat: add topic-researcher Docker service"
```

---

### Task 7: Add `python-dateutil` Dependency (if needed)

**Files:**
- Check: `requirements.txt`

- [ ] **Step 1: Check if `python-dateutil` is already a dependency**

Run: `grep -i dateutil /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/requirements.txt`

If not present:

- [ ] **Step 2: Add to requirements.txt**

Append `python-dateutil>=2.8.0` to `requirements.txt`.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add python-dateutil dependency for topic researcher"
```

---

### Task 8: Update Documentation

**Files:**
- Create: `docs/cron-topic-researcher.md`

- [ ] **Step 1: Create the doc**

Create `docs/cron-topic-researcher.md` following the same format as `docs/cron-script-expansion.md`. Include:
- What it does
- Where it runs (Docker `topic-researcher` service)
- How it works (the loop + phases)
- Timing table (constants)
- First run behavior (DB-based recovery)
- Topic selection phases
- Error handling
- Log events table
- Files table
- Manual trigger info (reference the Hub)
- Health endpoint (`GET /topics/cron-status`)

- [ ] **Step 2: Commit**

```bash
git add docs/cron-topic-researcher.md
git commit -m "docs: add topic researcher cron documentation"
```

---

### Task 9: Run Full Test Suite

- [ ] **Step 1: Run all new tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_topic_researcher_queries.py tests/test_topic_seed_selector.py tests/test_topic_researcher.py tests/test_topic_researcher_status.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run existing tests to check for regressions**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -v --timeout=30 -x`
Expected: No regressions — existing tests still pass

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: address test regressions from topic researcher"
```
