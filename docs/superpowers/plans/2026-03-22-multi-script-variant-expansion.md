# Multi-Script Variant Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate multiple unique script variants per topic using a framework × hook_style matrix, supporting both value and lifestyle post types from a unified script bank.

**Architecture:** Stateless diversity query — no new tables. A `pick_next_variant()` function queries existing scripts, finds unused (framework, hook_style) combinations, and generates the most diverse next variant. Two isolated generation paths: `build_prompt1_variant()` for value (PROMPT_1 + hook bank), `generate_dialog_scripts_variant()` for lifestyle (PROMPT_2). A daily cron and hub trigger fill the bank over time.

**Tech Stack:** Python/FastAPI, Supabase Postgres, Gemini LLM, YAML hook bank

**Spec:** `docs/superpowers/specs/2026-03-22-multi-script-variant-expansion-design.md`

---

### Task 1: Database Migration — Backfill and Enforce framework/hook_style

**Files:**
- Create: `supabase/migrations/016_enforce_framework_hook_style.sql`

This migration must be applied to Supabase before any code changes. It backfills NULL values and adds constraints.

- [ ] **Step 1: Write the migration SQL**

```sql
-- Backfill existing NULL values with identifiable defaults.
-- NOTE: Some existing rows have hook_style set to bucket names like
-- 'problem-agitate-solution', 'testimonial', 'transformation' (from
-- _build_script_variants in hub.py). These are NOT real hook styles
-- but they won't collide with the hook bank vocabulary used by variant
-- expansion, so they're safe to leave as-is. They identify legacy rows.
UPDATE public.topic_scripts
SET framework = 'PAL'
WHERE framework IS NULL;

UPDATE public.topic_scripts
SET hook_style = 'default'
WHERE hook_style IS NULL;

-- Enforce NOT NULL
ALTER TABLE public.topic_scripts
ALTER COLUMN framework SET NOT NULL,
ALTER COLUMN hook_style SET NOT NULL;

-- Add unique constraint for variant expansion dedup
-- (separate from existing unique on topic_registry_id, target_length_tier, script)
CREATE UNIQUE INDEX IF NOT EXISTS topic_scripts_variant_unique_idx
ON public.topic_scripts (topic_registry_id, target_length_tier, post_type, framework, hook_style);
```

- [ ] **Step 2: Apply the migration**

Run via Supabase MCP or dashboard. Verify with:
```sql
SELECT COUNT(*) FROM public.topic_scripts WHERE framework IS NULL OR hook_style IS NULL;
-- Expected: 0
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/016_enforce_framework_hook_style.sql
git commit -m "feat: enforce NOT NULL on framework/hook_style, add variant unique index"
```

---

### Task 2: Update Existing Insert Paths to Always Provide framework/hook_style

**Files:**
- Modify: `app/features/topics/queries.py:645-670` (upsert_topic_script_variants payload)
- Modify: `app/features/topics/hub.py:339-348` (_build_value_dialog_scripts_from_prompt1)
- Modify: `app/features/topics/hub.py:383-422` (_persist_topic_bank_row and _build_script_variants)
- Test: `tests/test_topics_hub.py`

The goal is to ensure every code path that inserts into `topic_scripts` always provides non-NULL `framework` and `hook_style` values, so the NOT NULL constraint never fails.

- [ ] **Step 1: Read the _build_script_variants function in hub.py to understand where variant dicts are constructed**

Find all places in `hub.py` where variant dicts are built with `framework` and `hook_style` keys. The key function is `_build_script_variants()` — read it fully.

Also read the fallback insert paths in `handlers.py` around lines 1016-1034 where lifestyle fallback variants are built.

- [ ] **Step 2: Update _build_script_variants in hub.py to always set framework and hook_style**

In `_build_script_variants()`, ensure every variant dict has:
- `"framework"`: pulled from `prompt1_item.framework` or defaulting to `"PAL"`
- `"hook_style"`: pulled from `prompt1_item.hook_style` or defaulting to `"default"`

Similarly update the fallback variant construction in `handlers.py` (lifestyle fallback around line 1016) to always provide these values.

- [ ] **Step 3: Add a safety fallback in upsert_topic_script_variants**

In `queries.py` line 654, change:
```python
"hook_style": variant.get("hook_style"),
"framework": variant.get("framework"),
```
To:
```python
"hook_style": variant.get("hook_style") or "default",
"framework": variant.get("framework") or "PAL",
```

This is a safety net — callers should provide values, but this prevents a NULL insert crash.

- [ ] **Step 4: Run existing tests to verify nothing breaks**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
python -m pytest tests/test_topics_hub.py tests/test_topic_pipeline.py -v
```
Expected: All existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/queries.py app/features/topics/hub.py app/features/topics/handlers.py
git commit -m "fix: ensure framework/hook_style are always populated on topic_scripts inserts"
```

---

### Task 3: Implement pick_next_variant() — Core Diversity Logic

**Files:**
- Create: `app/features/topics/variant_expansion.py`
- Create: `tests/test_variant_expansion.py`

This is the pure function that determines which (framework, hook_style) combination to generate next. No LLM calls, no DB writes — just logic.

- [ ] **Step 1: Write failing tests for pick_next_variant**

Create `tests/test_variant_expansion.py`:

```python
"""Tests for the variant expansion diversity logic."""

from app.features.topics.variant_expansion import (
    pick_next_variant,
    LIFESTYLE_FRAMEWORKS,
    LIFESTYLE_HOOK_STYLES,
)


def test_pick_next_variant_returns_unused_combination():
    """With no existing scripts, picks first framework × first hook."""
    result = pick_next_variant(
        existing_pairs=[],
        available_frameworks=["PAL", "Testimonial", "Transformation"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    framework, hook_style = result
    assert framework in ["PAL", "Testimonial", "Transformation"]
    assert hook_style in ["question", "bold_claim", "story_opener"]


def test_pick_next_variant_skips_used_pairs():
    """Used pairs are excluded from selection."""
    existing = [("PAL", "question")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim"],
        max_scripts=20,
    )
    assert result is not None
    assert result != ("PAL", "question")


def test_pick_next_variant_exhausted_returns_none():
    """When all combinations are used, returns None."""
    existing = [
        ("PAL", "question"),
        ("PAL", "bold_claim"),
        ("Testimonial", "question"),
        ("Testimonial", "bold_claim"),
    ]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim"],
        max_scripts=20,
    )
    assert result is None


def test_pick_next_variant_respects_max_cap():
    """Returns None when existing count reaches max_scripts."""
    existing = [("PAL", "question"), ("PAL", "bold_claim")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=2,
    )
    assert result is None


def test_pick_next_variant_diversity_prefers_underrepresented_framework():
    """If PAL has 2 scripts and Testimonial has 0, pick Testimonial."""
    existing = [("PAL", "question"), ("PAL", "bold_claim")]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    framework, _ = result
    assert framework == "Testimonial"


def test_pick_next_variant_diversity_prefers_underrepresented_hook():
    """Within a framework, pick the least-used hook style."""
    existing = [
        ("PAL", "question"),
        ("Testimonial", "question"),
    ]
    result = pick_next_variant(
        existing_pairs=existing,
        available_frameworks=["PAL", "Testimonial"],
        available_hook_styles=["question", "bold_claim", "story_opener"],
        max_scripts=20,
    )
    assert result is not None
    _, hook_style = result
    assert hook_style != "question"


def test_lifestyle_constants_defined():
    """Lifestyle frameworks and hook styles are available."""
    assert len(LIFESTYLE_FRAMEWORKS) >= 3
    assert len(LIFESTYLE_HOOK_STYLES) >= 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_variant_expansion.py -v
```
Expected: ImportError — module does not exist yet.

- [ ] **Step 3: Implement pick_next_variant in variant_expansion.py**

Create `app/features/topics/variant_expansion.py`:

```python
"""
Multi-script variant expansion.

Generates multiple script variants per topic using a framework × hook_style
diversity matrix. Stateless — queries existing scripts to determine what's
missing, then picks the most diverse next combination.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

# Lifestyle-specific constants
LIFESTYLE_FRAMEWORKS = ["PAL", "Testimonial", "Transformation"]
LIFESTYLE_HOOK_STYLES = [
    "personal_story",
    "daily_tip",
    "community_moment",
    "challenge",
    "humor",
]

# Default config
DEFAULT_MAX_SCRIPTS_PER_TOPIC = 20
DEFAULT_MAX_SCRIPTS_PER_CRON_RUN = 30


def pick_next_variant(
    *,
    existing_pairs: List[Tuple[str, str]],
    available_frameworks: List[str],
    available_hook_styles: List[str],
    max_scripts: int = DEFAULT_MAX_SCRIPTS_PER_TOPIC,
) -> Optional[Tuple[str, str]]:
    """Pick the most diverse unused (framework, hook_style) combination.

    Returns None if all combinations are exhausted or max_scripts is reached.
    """
    if len(existing_pairs) >= max_scripts:
        return None

    used_set = set(existing_pairs)
    all_combos = [
        (fw, hs)
        for fw in available_frameworks
        for hs in available_hook_styles
        if (fw, hs) not in used_set
    ]
    if not all_combos:
        return None

    # Count how many scripts each framework and hook_style already have
    fw_counts = Counter(fw for fw, _ in existing_pairs)
    hs_counts = Counter(hs for _, hs in existing_pairs)

    # Sort by: least-used framework first, then least-used hook_style
    all_combos.sort(key=lambda pair: (fw_counts.get(pair[0], 0), hs_counts.get(pair[1], 0)))

    return all_combos[0]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_variant_expansion.py -v
```
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/variant_expansion.py tests/test_variant_expansion.py
git commit -m "feat: add pick_next_variant diversity logic for variant expansion"
```

---

### Task 4: Implement build_prompt1_variant() — Value Script Prompt Builder

**Files:**
- Modify: `app/features/topics/prompts.py` (add new function only)
- Create: `tests/test_prompt1_variant.py`

New prompt builder that extends the existing template with hook bank context and forced framework/hook constraints. Does NOT modify `build_prompt1()`.

- [ ] **Step 1: Read the existing build_prompt1 and _format_hook_bank_section functions**

Read `app/features/topics/prompts.py` lines 355-383 (`build_prompt1`) and lines 295-312 (`_format_hook_bank_section`) to understand the template and format keys.

Also read one of the prompt template files (e.g., `app/features/topics/prompt_data/prompt1_8s.txt`) to see what `{hook_bank_section}` looks like in the template.

- [ ] **Step 2: Write a failing test for build_prompt1_variant**

Create `tests/test_prompt1_variant.py`:

```python
"""Tests for the variant-specific prompt builder."""

from app.features.topics.prompts import build_prompt1_variant
from app.features.topics.schemas import ResearchDossier


def test_build_prompt1_variant_includes_hook_bank():
    """Variant prompt includes hook bank section (unlike build_prompt1)."""
    prompt = build_prompt1_variant(
        post_type="value",
        desired_topics=1,
        forced_framework="Testimonial",
        forced_hook_style="question",
    )
    assert "HOOK-BANK" in prompt or "Hook" in prompt.lower()


def test_build_prompt1_variant_includes_forced_constraints():
    """Variant prompt includes the forced framework and hook style."""
    prompt = build_prompt1_variant(
        post_type="value",
        desired_topics=1,
        forced_framework="Testimonial",
        forced_hook_style="bold_claim",
    )
    assert "Testimonial" in prompt
    assert "bold_claim" in prompt


def test_build_prompt1_original_unchanged():
    """Original build_prompt1 still has empty hook_bank_section."""
    from app.features.topics.prompts import build_prompt1
    prompt = build_prompt1(desired_topics=1)
    assert "HOOK-BANK" not in prompt
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_prompt1_variant.py -v
```
Expected: ImportError — `build_prompt1_variant` does not exist.

- [ ] **Step 4: Implement build_prompt1_variant in prompts.py**

Add this function after the existing `build_prompt1` (around line 384). Do NOT modify `build_prompt1`.

```python
def build_prompt1_variant(
    post_type: str,
    desired_topics: int = 1,
    profile: Optional[DurationProfile] = None,
    dossier: ResearchDossier | Dict[str, Any] | None = None,
    lane_candidate: Optional[Dict[str, Any]] = None,
    *,
    forced_framework: str,
    forced_hook_style: str,
) -> str:
    """Render a variant PROMPT_1 stage-3 prompt with hook bank and forced constraints.

    Unlike build_prompt1(), this injects the hook bank and forces a specific
    framework + hook_style. Used only by the variant expansion system.
    """
    profile = profile or get_duration_profile(8)
    prompt_path = PROMPT_DATA_DIR / f"prompt1_{profile.target_length_tier}s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        template = fp.read().strip()
    research_context_section = _format_prompt1_research_context(dossier, lane_candidate)
    hook_bank_section = _format_hook_bank_section()

    # Append framework/hook constraints to the hook bank section
    constraint_block = (
        f"\n\nPFLICHT-VORGABEN FÜR DIESES SKRIPT:\n"
        f"- Framework: {forced_framework}\n"
        f"- Hook-Stil: {forced_hook_style}\n"
        f"Halte dich strikt an dieses Framework und diesen Hook-Stil."
    )
    hook_bank_section = (hook_bank_section + constraint_block).strip()

    return template.format(
        desired_topics=desired_topics,
        research_context_section=research_context_section,
        hook_bank_section=hook_bank_section,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_prompt1_variant.py -v
```
Expected: All 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/prompts.py tests/test_prompt1_variant.py
git commit -m "feat: add build_prompt1_variant with hook bank and forced constraints"
```

---

### Task 5: Implement generate_dialog_scripts_variant() — Lifestyle Script Generator

**Files:**
- Modify: `app/features/topics/variant_expansion.py` (add function)
- Create: `tests/test_lifestyle_variant.py`

New wrapper that calls the LLM with PROMPT_2 but forces a specific framework/hook_style. Does NOT modify `generate_dialog_scripts()`.

- [ ] **Step 1: Read the existing generate_dialog_scripts**

The public API is in `app/features/topics/agents.py` (thin wrapper), implementation is in `app/features/topics/research_runtime.py`. Read both to understand inputs, LLM call, and response parsing:
```bash
grep -n "def generate_dialog_scripts" app/features/topics/agents.py app/features/topics/research_runtime.py
```
Also read `build_prompt2()` in prompts.py to understand the template.

- [ ] **Step 2: Write a failing test**

Create `tests/test_lifestyle_variant.py`:

```python
"""Tests for lifestyle variant generation."""

from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from app.features.topics.variant_expansion import generate_dialog_scripts_variant


def test_generate_dialog_scripts_variant_includes_constraints(monkeypatch):
    """The variant prompt includes forced framework and hook style."""
    captured_prompt = {}

    def mock_generate(*, prompt, system_prompt=None, **kwargs):
        captured_prompt["value"] = prompt
        return '{"problem_agitate_solution": ["Test script"], "testimonial": ["Test"], "transformation": ["Test"], "description": "Test"}'

    mock_llm = MagicMock()
    mock_llm.generate_gemini_json = mock_generate

    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    result = generate_dialog_scripts_variant(
        topic="Test topic",
        forced_framework="Testimonial",
        forced_hook_style="personal_story",
    )
    assert "Testimonial" in captured_prompt["value"]
    assert "personal_story" in captured_prompt["value"]
    assert result is not None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_lifestyle_variant.py -v
```
Expected: ImportError — function does not exist.

- [ ] **Step 4: Implement generate_dialog_scripts_variant**

Add to `app/features/topics/variant_expansion.py`:

```python
from app.adapters.llm_client import get_llm_client
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import build_prompt2
from app.features.topics.response_parsers import parse_prompt2_response


def generate_dialog_scripts_variant(
    *,
    topic: str,
    forced_framework: str,
    forced_hook_style: str,
    target_length_tier: int = 8,
    dossier: dict | None = None,
):
    """Generate lifestyle dialog scripts constrained to a specific framework and hook style.

    Wraps PROMPT_2 with additional constraints. Does not modify the
    existing generate_dialog_scripts() function.
    """
    profile = get_duration_profile(target_length_tier)
    base_prompt = build_prompt2(
        topic=topic,
        scripts_per_category=1,
        profile=profile,
        dossier=dossier,
    )

    constraint_block = (
        f"\n\nPFLICHT-VORGABEN FÜR DIESES SKRIPT:\n"
        f"- Framework: {forced_framework}\n"
        f"- Hook-Stil: {forced_hook_style}\n"
        f"Halte dich strikt an dieses Framework und diesen Hook-Stil.\n"
    )
    constrained_prompt = base_prompt + constraint_block

    llm = get_llm_client()
    raw_response = llm.generate_gemini_json(
        prompt=constrained_prompt,
        system_prompt="You are a German UGC script writer. Return valid JSON only.",
    )

    return parse_prompt2_response(raw_response)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_lifestyle_variant.py -v
```
Expected: Pass.

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/variant_expansion.py tests/test_lifestyle_variant.py
git commit -m "feat: add generate_dialog_scripts_variant for lifestyle expansion"
```

---

### Task 6: Implement expand_topic_variants() — Orchestration Function

**Files:**
- Modify: `app/features/topics/variant_expansion.py` (add function)
- Modify: `app/features/topics/queries.py` (add query for existing pairs + dossier loader)
- Create: `tests/test_expand_topic_variants.py`

This is the main orchestration function that ties everything together: loads a topic, determines the next variant, generates the script, and stores it.

- [ ] **Step 1: Add query function to get existing (framework, hook_style) pairs**

Add to `app/features/topics/queries.py`:

```python
def get_existing_variant_pairs(
    *,
    topic_registry_id: str,
    target_length_tier: int,
    post_type: str,
) -> List[Dict[str, Any]]:
    """Return existing (framework, hook_style) pairs for a topic/tier/post_type."""
    supabase = get_supabase()
    response = (
        supabase.client.table("topic_scripts")
        .select("framework, hook_style")
        .eq("topic_registry_id", topic_registry_id)
        .eq("target_length_tier", target_length_tier)
        .eq("post_type", post_type)
        .execute()
    )
    return response.data or []
```

- [ ] **Step 2: Write failing test for expand_topic_variants**

Create `tests/test_expand_topic_variants.py`. This is an integration-level test with mocked DB and LLM:

```python
"""Tests for the expand_topic_variants orchestration."""

from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from app.features.topics.variant_expansion import expand_topic_variants


def test_expand_topic_variants_generates_and_stores(monkeypatch):
    """Generates a variant and calls upsert."""
    # Mock DB queries
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [],  # No existing scripts
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [{"title": "Lane 1", "framework_candidates": ["PAL"]}],
        }}],
    )

    stored = []
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.upsert_topic_script_variants",
        lambda **kw: stored.append(kw) or [],
    )

    # Mock LLM (variant expansion calls LLM directly, not generate_topic_script_candidate)
    mock_llm = MagicMock()
    mock_llm.generate_gemini_json.return_value = '[{"title": "Test", "script": "Test script", "caption": "Cap"}]'
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    mock_item = SimpleNamespace(
        topic="Test", script="Test script", caption="Cap",
        hook="hook", cta="cta", framework="PAL",
        source_summary="", estimated_duration_s=5,
        hook_style="question", lane_key="", lane_family="",
        cluster_id="", anchor_topic="", disclaimer="",
        primary_source_url="", primary_source_title="",
        source_urls=[], seed_payload={},
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.parse_prompt1_response",
        lambda raw, expected_count=1: [mock_item],
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=1,
    )
    assert result["generated"] == 1
    assert len(stored) == 1


def test_expand_topic_variants_skips_exhausted_topic(monkeypatch):
    """Returns 0 generated when all variants are used."""
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [
            {"framework": "PAL", "hook_style": "question"},
            {"framework": "PAL", "hook_style": "bold_claim"},
            {"framework": "Testimonial", "hook_style": "question"},
            {"framework": "Testimonial", "hook_style": "bold_claim"},
        ],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [],
        }}],
    )
    # Mock hook bank to only have 2 styles
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_hook_bank",
        lambda: {"families": [
            {"name": "question", "examples": ["Was wäre wenn..."]},
            {"name": "bold_claim", "examples": ["Die Wahrheit ist..."]},
        ], "banned_patterns": []},
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=5,
    )
    assert result["generated"] == 0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_expand_topic_variants.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement expand_topic_variants**

Add to `app/features/topics/variant_expansion.py`:

```python
import time
from typing import Any, Dict

from app.features.topics.queries import (
    get_existing_variant_pairs,
    get_topic_research_dossiers,
    upsert_topic_script_variants,
)
from app.adapters.llm_client import get_llm_client
from app.features.topics.prompts import build_prompt1_variant, get_hook_bank
from app.features.topics.response_parsers import parse_prompt1_response


def _get_hook_style_names() -> List[str]:
    """Extract hook family names from the hook bank YAML."""
    payload = get_hook_bank()
    families = list(payload.get("families") or [])
    return [str(f.get("name") or "").strip() for f in families if f.get("name")]


def _pick_lane_for_framework(
    lane_candidates: List[Dict[str, Any]],
    target_framework: str,
    existing_pairs: List[Tuple[str, str]],
) -> Dict[str, Any]:
    """Pick the lane whose framework_candidates contains the target framework."""
    matching = [
        lc for lc in lane_candidates
        if target_framework in (lc.get("framework_candidates") or [])
    ]
    if not matching:
        return lane_candidates[0] if lane_candidates else {}
    # Among matching lanes, pick the one with fewest existing scripts
    # (simple heuristic: use the first match for now)
    return matching[0]


def expand_topic_variants(
    *,
    topic_registry_id: str,
    title: str,
    post_type: str,
    target_length_tier: int,
    count: int = 1,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Generate up to `count` new script variants for a topic.

    Returns a summary dict with generated count and details.
    """
    existing_rows = get_existing_variant_pairs(
        topic_registry_id=topic_registry_id,
        target_length_tier=target_length_tier,
        post_type=post_type,
    )
    existing_pairs = [
        (row["framework"], row["hook_style"]) for row in existing_rows
    ]

    # Determine available frameworks and hook styles
    if post_type == "value":
        dossiers = get_topic_research_dossiers(topic_registry_id=topic_registry_id)
        dossier_payload = (dossiers[0].get("normalized_payload") or {}) if dossiers else {}
        available_frameworks = list(dossier_payload.get("framework_candidates") or ["PAL", "Testimonial", "Transformation"])
        available_hook_styles = _get_hook_style_names() or ["default"]
        lane_candidates = list(dossier_payload.get("lane_candidates") or [])
    else:
        dossier_payload = {}
        available_frameworks = LIFESTYLE_FRAMEWORKS
        available_hook_styles = LIFESTYLE_HOOK_STYLES
        lane_candidates = []

    generated = 0
    details = []

    for _ in range(count):
        variant = pick_next_variant(
            existing_pairs=existing_pairs,
            available_frameworks=available_frameworks,
            available_hook_styles=available_hook_styles,
            max_scripts=DEFAULT_MAX_SCRIPTS_PER_TOPIC,
        )
        if variant is None:
            logger.info("variant_expansion_exhausted", topic_registry_id=topic_registry_id)
            break

        framework, hook_style = variant

        if dry_run:
            details.append({"framework": framework, "hook_style": hook_style, "dry_run": True})
            existing_pairs.append(variant)
            generated += 1
            continue

        try:
            if post_type == "value":
                lane = _pick_lane_for_framework(lane_candidates, framework, existing_pairs)
                # Call LLM directly with variant prompt — bypasses
                # generate_topic_script_candidate to keep research_runtime.py untouched
                variant_prompt = build_prompt1_variant(
                    post_type=post_type,
                    desired_topics=1,
                    dossier=dossier_payload,
                    lane_candidate=lane,
                    forced_framework=framework,
                    forced_hook_style=hook_style,
                    profile=get_duration_profile(target_length_tier),
                )
                llm = get_llm_client()
                raw = llm.generate_gemini_json(
                    prompt=variant_prompt,
                    system_prompt="You are the Flow Forge PROMPT_1 stage-3 script agent. Return only valid JSON. Keep all output fully in German.",
                )
                parsed = parse_prompt1_response(raw, expected_count=1)
                prompt1_item = parsed[0] if parsed else None
                if not prompt1_item:
                    logger.warning("variant_expansion_parse_failed", framework=framework, hook_style=hook_style)
                    continue
                script_text = str(prompt1_item.script or "").strip()
                variant_data = {
                    "script": script_text,
                    "framework": framework,
                    "hook_style": hook_style,
                    "bucket": framework.lower(),
                    "estimated_duration_s": getattr(prompt1_item, "estimated_duration_s", None),
                    "lane_key": getattr(prompt1_item, "lane_key", None) or lane.get("lane_key"),
                    "lane_family": getattr(prompt1_item, "lane_family", None) or lane.get("lane_family"),
                    "cluster_id": getattr(prompt1_item, "cluster_id", None),
                    "anchor_topic": getattr(prompt1_item, "anchor_topic", None),
                    "seed_payload": {},
                }
            else:
                dialog_scripts = generate_dialog_scripts_variant(
                    topic=title,
                    forced_framework=framework,
                    forced_hook_style=hook_style,
                    target_length_tier=target_length_tier,
                )
                script_text = str(
                    (dialog_scripts.problem_agitate_solution or [""])[0]
                ).strip()
                variant_data = {
                    "script": script_text,
                    "framework": framework,
                    "hook_style": hook_style,
                    "bucket": framework.lower(),
                    "seed_payload": {},
                }

            if not script_text:
                logger.warning("variant_expansion_empty_script", framework=framework, hook_style=hook_style)
                continue

            # Get dossier ID for FK if available
            dossier_id = dossiers[0].get("id") if (post_type == "value" and dossiers) else None
            upsert_topic_script_variants(
                topic_registry_id=topic_registry_id,
                title=title,
                post_type=post_type,
                target_length_tier=target_length_tier,
                topic_research_dossier_id=dossier_id,
                variants=[variant_data],
            )

            existing_pairs.append(variant)
            generated += 1
            details.append({"framework": framework, "hook_style": hook_style, "script": script_text[:80]})
            logger.info(
                "variant_expansion_generated",
                topic_registry_id=topic_registry_id,
                framework=framework,
                hook_style=hook_style,
            )

        except Exception as exc:
            logger.warning(
                "variant_expansion_failed",
                topic_registry_id=topic_registry_id,
                framework=framework,
                hook_style=hook_style,
                error=str(exc),
            )
            # Skip and continue to next variant
            continue

    return {
        "topic_registry_id": topic_registry_id,
        "post_type": post_type,
        "target_length_tier": target_length_tier,
        "generated": generated,
        "total_existing": len(existing_rows) + generated,
        "details": details,
    }
```

**Note:** The value path calls the LLM directly via `get_llm_client()` + `parse_prompt1_response()` instead of going through `generate_topic_script_candidate()`. This keeps `research_runtime.py` completely untouched (isolation guarantee).

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_expand_topic_variants.py -v
```
Expected: Pass.

- [ ] **Step 6: Also run all existing tests to verify no regressions**

```bash
python -m pytest tests/ -v --timeout=30
```
Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/features/topics/variant_expansion.py app/features/topics/queries.py tests/test_expand_topic_variants.py
git commit -m "feat: add expand_topic_variants orchestration for variant generation"
```

---

### Task 7: Implement expand_script_bank() — Cron Entry Point

**Files:**
- Modify: `app/features/topics/variant_expansion.py` (add cron function)
- Create: `tests/test_expand_script_bank.py`

The cron entry point that iterates all topics and fills the bank.

- [ ] **Step 1: Write failing test**

Create `tests/test_expand_script_bank.py`:

```python
"""Tests for the cron-level expand_script_bank function."""

from app.features.topics.variant_expansion import expand_script_bank


def test_expand_script_bank_respects_max_per_run(monkeypatch):
    """Stops after max_scripts_per_cron_run."""
    topics = [
        {"id": f"topic-{i}", "title": f"Topic {i}", "post_type": "value"}
        for i in range(10)
    ]
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_all_topics_from_registry",
        lambda: topics,
    )

    call_count = {"n": 0}
    def mock_expand(**kw):
        call_count["n"] += 1
        return {"generated": 1, "total_existing": call_count["n"], "details": []}

    monkeypatch.setattr(
        "app.features.topics.variant_expansion.expand_topic_variants",
        mock_expand,
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_scripts_for_registry",
        lambda tid: [],
    )

    result = expand_script_bank(
        max_scripts_per_cron_run=3,
        target_length_tiers=[8],
    )
    assert result["total_generated"] <= 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_expand_script_bank.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement expand_script_bank**

Add to `app/features/topics/variant_expansion.py`:

```python
from app.features.topics.queries import (
    get_all_topics_from_registry,
    get_topic_scripts_for_registry,
)


ALL_TIERS = [8, 16, 32]


def expand_script_bank(
    *,
    max_scripts_per_cron_run: int = DEFAULT_MAX_SCRIPTS_PER_CRON_RUN,
    target_length_tiers: List[int] | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Cron entry point: fill the script bank across all topics and tiers.

    Ranks topics by gap (fewest scripts first) and generates variants
    until the per-run cap is reached. Iterates all tiers for each topic.
    """
    tiers = target_length_tiers or ALL_TIERS
    topics = get_all_topics_from_registry()

    # Score each topic by how many scripts it has (fewer = higher priority)
    scored = []
    for topic in topics:
        scripts = get_topic_scripts_for_registry(topic["id"])
        scored.append((len(scripts), topic))
    scored.sort(key=lambda pair: pair[0])

    total_generated = 0
    topic_results = []

    for script_count, topic in scored:
        if total_generated >= max_scripts_per_cron_run:
            break

        post_type = topic.get("post_type") or "value"

        for tier in tiers:
            if total_generated >= max_scripts_per_cron_run:
                break
            remaining_budget = max_scripts_per_cron_run - total_generated

            result = expand_topic_variants(
                topic_registry_id=topic["id"],
                title=topic.get("title") or "",
                post_type=post_type,
                target_length_tier=tier,
                count=min(remaining_budget, 3),  # generate up to 3 per topic/tier per run
                dry_run=dry_run,
            )

            total_generated += result["generated"]
            if result["generated"] > 0:
                topic_results.append({
                    "topic_id": topic["id"],
                    "title": topic.get("title"),
                    "tier": tier,
                    "generated": result["generated"],
                    "total": result["total_existing"],
                })

            logger.info(
                "expand_script_bank_topic",
                topic_id=topic["id"],
                title=topic.get("title"),
                tier=tier,
                generated=result["generated"],
                total=result["total_existing"],
                dry_run=dry_run,
            )

    summary = {
        "total_generated": total_generated,
        "topics_processed": len(topic_results),
        "topic_results": topic_results,
        "dry_run": dry_run,
    }
    logger.info("expand_script_bank_complete", **summary)
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_expand_script_bank.py -v
```
Expected: Pass.

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/variant_expansion.py tests/test_expand_script_bank.py
git commit -m "feat: add expand_script_bank cron entry point"
```

---

### Task 8: Live CLI Test Script

**Files:**
- Create: `scripts/test_variant_expansion.py`

A runnable script that hits real Gemini + Supabase with terminal output for quality inspection.

- [ ] **Step 1: Write the CLI test script**

Create `scripts/test_variant_expansion.py`:

```python
#!/usr/bin/env python3
"""Live end-to-end test for variant expansion.

Usage:
    python scripts/test_variant_expansion.py              # generate 1 variant per path
    python scripts/test_variant_expansion.py --dry-run    # show what would be generated
    python scripts/test_variant_expansion.py --count 3    # generate 3 variants
"""

import argparse
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.features.topics.queries import (
    get_all_topics_from_registry,
    get_topic_scripts_for_registry,
)
from app.features.topics.variant_expansion import (
    expand_topic_variants,
    pick_next_variant,
    _get_hook_style_names,
    LIFESTYLE_FRAMEWORKS,
    LIFESTYLE_HOOK_STYLES,
    DEFAULT_MAX_SCRIPTS_PER_TOPIC,
)


def print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="Live variant expansion test")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--count", type=int, default=1, help="Variants per topic")
    parser.add_argument("--tier", type=int, default=8, help="Target length tier")
    parser.add_argument("--post-type", choices=["value", "lifestyle"], help="Filter by post type")
    args = parser.parse_args()

    print_header("VARIANT EXPANSION LIVE TEST")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE (will write to DB)'}")
    print(f"Count: {args.count} variants per topic")
    print(f"Tier: {args.tier}s")

    topics = get_all_topics_from_registry()
    if not topics:
        print("ERROR: No topics in registry. Run topic seeding first.")
        sys.exit(1)

    print(f"\nFound {len(topics)} topics in registry")

    # Filter by post type if specified
    if args.post_type:
        topics = [t for t in topics if t.get("post_type") == args.post_type]
        print(f"Filtered to {len(topics)} {args.post_type} topics")

    # Pick first topic of each type for testing
    value_topic = next((t for t in topics if t.get("post_type") == "value"), None)
    lifestyle_topic = next((t for t in topics if t.get("post_type") == "lifestyle"), None)

    results = []

    for label, topic in [("VALUE", value_topic), ("LIFESTYLE", lifestyle_topic)]:
        if topic is None:
            print(f"\n--- Skipping {label}: no topics of this type ---")
            continue

        print_header(f"{label} PATH: {topic['title']}")

        existing_scripts = get_topic_scripts_for_registry(topic["id"])
        print(f"Existing scripts: {len(existing_scripts)}")
        for s in existing_scripts:
            print(f"  - [{s.get('framework', '?')}/{s.get('hook_style', '?')}] {str(s.get('script', ''))[:60]}...")

        result = expand_topic_variants(
            topic_registry_id=topic["id"],
            title=topic["title"],
            post_type=topic.get("post_type") or "value",
            target_length_tier=args.tier,
            count=args.count,
            dry_run=args.dry_run,
        )

        print(f"\nGenerated: {result['generated']}")
        print(f"Total now: {result['total_existing']}")
        for detail in result.get("details", []):
            print(f"\n  Framework: {detail['framework']}")
            print(f"  Hook:      {detail['hook_style']}")
            if "script" in detail:
                print(f"  Script:    {detail['script']}")
            if detail.get("dry_run"):
                print(f"  (dry run — not stored)")

        results.append({"label": label, "topic": topic["title"], **result})

    # Summary table
    print_header("SUMMARY")
    print(f"{'Path':<12} {'Topic':<30} {'Generated':<10} {'Total':<10} {'Remaining'}")
    print("-" * 80)
    for r in results:
        remaining = DEFAULT_MAX_SCRIPTS_PER_TOPIC - r["total_existing"]
        print(f"{r['label']:<12} {r['topic'][:28]:<30} {r['generated']:<10} {r['total_existing']:<10} {remaining}")

    print(f"\nDone. {'(DRY RUN — no changes made)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test in dry-run mode first**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
python scripts/test_variant_expansion.py --dry-run
```
Expected: Shows topics, existing scripts, and what variants would be generated. No DB writes.

- [ ] **Step 3: Test live with 1 variant**

```bash
python scripts/test_variant_expansion.py --count 1
```
Expected: Generates 1 new variant for value and/or lifestyle topic. Shows the script text and framework/hook in terminal. Verify the script reads well and is in German.

- [ ] **Step 4: Verify stored data in DB**

```bash
python -c "
from app.features.topics.queries import get_all_topics_from_registry, get_topic_scripts_for_registry
topics = get_all_topics_from_registry()
for t in topics[:3]:
    scripts = get_topic_scripts_for_registry(t['id'])
    print(f'{t[\"title\"]}: {len(scripts)} scripts')
    for s in scripts:
        print(f'  [{s.get(\"framework\")}/{s.get(\"hook_style\")}]')
"
```

- [ ] **Step 5: Commit**

```bash
git add scripts/test_variant_expansion.py
git commit -m "feat: add live CLI test for variant expansion"
```

---

### Task 9: Hub Integration — Expand Variants Trigger

**Files:**
- Modify: `app/features/topics/handlers.py` (add endpoint)
- Create: `tests/test_expand_variants_endpoint.py`

Add the "Expand variants" trigger to the topic hub.

- [ ] **Step 1: Read the existing hub handler routes**

Read `app/features/topics/handlers.py` to find the existing topic hub routes and understand the pattern for adding a new endpoint.

- [ ] **Step 2: Write a failing test for the endpoint**

Create `tests/test_expand_variants_endpoint.py`:

```python
"""Tests for the expand-variants endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.features.topics import handlers as topic_handlers


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(topic_handlers.router)
    return TestClient(app)


def test_expand_variants_endpoint_returns_json(monkeypatch):
    """POST /topics/expand-variants returns a JSON summary."""
    monkeypatch.setattr(
        "app.features.topics.handlers.expand_topic_variants",
        lambda **kw: {"generated": 1, "total_existing": 5, "details": []},
    )
    client = _build_test_client()
    response = client.post(
        "/topics/expand-variants",
        json={"topic_registry_id": "topic-1", "count": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] == 1


def test_expand_variants_endpoint_requires_topic_id(monkeypatch):
    """POST without topic_registry_id returns 422."""
    client = _build_test_client()
    response = client.post("/topics/expand-variants", json={})
    assert response.status_code == 422
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_expand_variants_endpoint.py -v
```
Expected: 404 or AttributeError — endpoint doesn't exist yet.

- [ ] **Step 4: Add the expand_variants endpoint**

Add a POST endpoint to `handlers.py` that:
- Accepts `topic_registry_id`, `count` (default 3), `target_length_tier` (default 8)
- Calls `expand_topic_variants()` from `variant_expansion.py`
- Returns the result summary as JSON

Follow the existing handler pattern in the file.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_expand_variants_endpoint.py -v
```
Expected: Pass.

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/handlers.py tests/test_expand_variants_endpoint.py
git commit -m "feat: add expand-variants endpoint to topic hub"
```

---

### Task 10: Run Full Regression + Live E2E Verification

**Files:** No new files — verification only.

- [ ] **Step 1: Run all unit and integration tests**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
python -m pytest tests/ -v --timeout=60
```
Expected: All tests pass, including new variant expansion tests and existing regression tests.

- [ ] **Step 2: Run live CLI test for both paths**

```bash
python scripts/test_variant_expansion.py --count 2
```
Expected: Generates 2 variants each for value and lifestyle. Terminal shows script text, framework, hook_style. Scripts are in German and read naturally.

- [ ] **Step 3: Verify the existing batch seeding still works**

Trigger a batch seeding for both value and lifestyle posts through the UI or API. Confirm:
- Value posts are seeded correctly (PROMPT_1 pipeline unchanged)
- Lifestyle posts are seeded correctly
- No errors in logs
- Generated posts have `framework` and `hook_style` populated

- [ ] **Step 4: Verify the existing hub still works**

Navigate to the topic hub in the browser. Confirm:
- Topics display correctly
- Scripts for each topic are listed
- The new "Expand variants" trigger works
- No UI errors

- [ ] **Step 5: Final commit if any cleanup was needed**

```bash
git status  # review what changed
git add <specific files that were cleaned up>
git commit -m "chore: final cleanup after variant expansion integration testing"
```
