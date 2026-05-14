# Duration Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent underlength scripts from being persisted or submitted as longer video tiers, audit existing rows, repair or quarantine bad rows, and prove live generation keeps value, lifestyle, and product scripts inside the correct length envelopes.

**Architecture:** Add one canonical script duration contract in `app/core/video_profiles.py`, then route topic validation, topic persistence, video submission, audit tooling, repair tooling, and live stress tests through that contract. The spend gate validates the effective dialogue that will be submitted to the provider, not only `seed_data.script`.

**Tech Stack:** FastAPI app code, Supabase/PostgREST adapter, existing topic generation modules, `pytest`, standard-library `argparse`/`csv`/`json`, no new dependencies.

---

## Context Zero

**Observed root cause:** `app/features/topics/topic_validation.py` currently allows `PROMPT2_DIALOG_WORD_BOUNDS[32] = (8, 66)`, while `app/core/video_profiles.py` defines 32s lifestyle/PROMPT_2 as `40-66`. This permits 8s/16s-sized lifestyle scripts to pass as 32s scripts.

**Known local environment issue:** A direct Python import probe failed because the local global interpreter has `pydantic_settings` expecting `pydantic._internal`. Execution should use the project environment. If the project environment is missing, repair the environment before code work with:

```powershell
python -m pip install -r requirements.txt
```

Expected import smoke:

```powershell
python - <<'PY'
from app.features.topics.topic_validation import get_prompt2_word_bounds
print(get_prompt2_word_bounds(32))
PY
```

Expected after Task 1: `(40, 66)`.

## Locality Envelope

Files: target 8 modified, 2 new scripts, 2 new/modified test files.

LOC/file:
- `app/core/video_profiles.py`: add <= 90 LOC.
- `app/features/topics/topic_validation.py`: add/modify <= 120 LOC.
- `app/features/topics/queries.py`: add/modify <= 120 LOC.
- `app/features/videos/handlers.py`: add/modify <= 170 LOC.
- `scripts/audit_duration_contracts.py`: new <= 250 LOC.
- `scripts/repair_duration_contracts.py`: new <= 250 LOC.
- `tests/test_duration_contracts.py`: new <= 260 LOC.
- `tests/test_video_quota_guard.py`: modify <= 120 LOC.
- `tests/stress_test_script_generation.py`: modify <= 220 LOC.

Deps: 0 new dependencies.

## File Structure

- `app/core/video_profiles.py`: canonical duration-tier and post-type word bounds.
- `app/features/topics/topic_validation.py`: compatibility wrappers, pre-persistence validator, spoken text utilities.
- `app/features/topics/queries.py`: post creation gate, stale bank-row selector gate, topic script persistence enforcement.
- `app/features/videos/handlers.py`: pre-video spend gate and script validation metadata.
- `scripts/audit_duration_contracts.py`: read-only live database audit, JSON/CSV output.
- `scripts/repair_duration_contracts.py`: dry-run-by-default repair/quarantine tool consuming audit JSON.
- `tests/test_duration_contracts.py`: canonical contract, persistence, selector, and audit helper regressions.
- `tests/test_video_quota_guard.py`: provider-call abort regression for bad 32s scripts.
- `tests/stress_test_script_generation.py`: live stress script extended to value/lifestyle/product and strict bounds.

## Canonical Bounds

The implementation must enforce these exact word bounds:

| Post Type | 8s | 16s | 32s |
|---|---:|---:|---:|
| value | 14-18 | 26-36 | 54-74 |
| lifestyle | 16-20 | 20-34 | 40-66 |
| product | 16-20 | 24-34 | 32-66 |

For video routing, 32s remains the UI/requested tier. Provider target stays `provider_target_seconds=29`, with `veo_base_seconds=8` and `veo_extension_hops=3`.

---

### Task 1: Canonical Script Duration Contract

**Files:**
- Modify: `app/core/video_profiles.py`
- Modify: `app/features/topics/topic_validation.py`
- Create: `tests/test_duration_contracts.py`

- [ ] **Step 1: Write failing canonical contract tests**

Add this file:

```python
# tests/test_duration_contracts.py
from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.core.video_profiles import (
    get_script_duration_bounds,
    validate_script_duration_contract,
)
from app.features.topics.topic_validation import (
    get_prompt1_word_bounds,
    get_prompt2_word_bounds,
    get_prompt3_word_bounds,
    validate_pre_persistence_topic_payload,
)


def _words(count: int) -> str:
    return " ".join(f"wort{i}" for i in range(count)) + "."


@pytest.mark.parametrize(
    "post_type,tier,expected",
    [
        ("value", 8, (14, 18)),
        ("value", 16, (26, 36)),
        ("value", 32, (54, 74)),
        ("lifestyle", 8, (16, 20)),
        ("lifestyle", 16, (20, 34)),
        ("lifestyle", 32, (40, 66)),
        ("product", 8, (16, 20)),
        ("product", 16, (24, 34)),
        ("product", 32, (32, 66)),
    ],
)
def test_canonical_script_duration_bounds(post_type, tier, expected):
    assert get_script_duration_bounds(post_type, tier) == expected


def test_legacy_prompt_bound_wrappers_use_canonical_contract():
    assert get_prompt1_word_bounds(32) == (54, 74)
    assert get_prompt2_word_bounds(32) == (40, 66)
    assert get_prompt3_word_bounds(32) == (32, 66)


def test_32s_lifestyle_24_word_script_is_rejected_by_contract():
    with pytest.raises(ValidationError) as exc:
        validate_script_duration_contract(
            script=_words(24),
            post_type="lifestyle",
            target_length_tier=32,
            row_id="post-incident",
            table="posts",
        )
    assert "24 words" in str(exc.value)
    assert exc.value.details["min_words"] == 40
    assert exc.value.details["max_words"] == 66


def test_32s_lifestyle_40_word_script_passes_contract():
    result = validate_script_duration_contract(
        script=_words(40),
        post_type="lifestyle",
        target_length_tier=32,
        row_id="post-valid",
        table="posts",
    )
    assert result["status"] == "valid"
    assert result["word_count"] == 40


def test_pre_persistence_lifestyle_uses_32s_floor():
    with pytest.raises(ValidationError) as exc:
        validate_pre_persistence_topic_payload(
            {"title": "Lifestyle", "topic": "Lifestyle", "script": _words(24)},
            target_length_tier=32,
            post_type="lifestyle",
        )
    assert exc.value.details["word_count"] == 24
    assert exc.value.details["min_words"] == 40
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
pytest tests/test_duration_contracts.py -v
```

Expected: failures because `get_script_duration_bounds` and `validate_script_duration_contract` do not exist, and because the current PROMPT_2 32s lower bound is still 8.

- [ ] **Step 3: Add canonical helpers**

In `app/core/video_profiles.py`, add after `SUPPORTED_TARGET_LENGTH_TIERS`:

```python
SCRIPT_WORD_BOUNDS = {
    "value": {
        8: (14, 18),
        16: (26, 36),
        32: (54, 74),
    },
    "lifestyle": {
        8: (16, 20),
        16: (20, 34),
        32: (40, 66),
    },
    "product": {
        8: (16, 20),
        16: (24, 34),
        32: (32, 66),
    },
}


def normalize_post_type(value: Optional[str]) -> str:
    post_type = str(value or "value").strip().lower()
    if post_type not in SCRIPT_WORD_BOUNDS:
        return "value"
    return post_type


def get_script_duration_bounds(post_type: Optional[str], target_length_tier: Optional[int]) -> tuple[int, int]:
    resolved_post_type = normalize_post_type(post_type)
    tier = normalize_target_length_tier(target_length_tier)
    try:
        return SCRIPT_WORD_BOUNDS[resolved_post_type][tier]
    except KeyError as exc:
        raise ValueError(f"Unsupported script duration contract: {resolved_post_type} {tier}s") from exc
```

Then import `Any` and `Dict` at the top of `app/core/video_profiles.py`:

```python
from typing import Any, Dict, Optional
```

Add this helper after `get_script_duration_bounds`:

```python
def estimate_duration_from_word_count(word_count: int) -> int:
    if word_count <= 0:
        return 0
    return max(1, int(round(word_count / 2.6)))
```

- [ ] **Step 4: Wire topic validation to the canonical contract**

In `app/features/topics/topic_validation.py`, add imports:

```python
from app.core.video_profiles import (
    estimate_duration_from_word_count,
    get_script_duration_bounds,
)
```

Replace the current bounds dictionaries with:

```python
PROMPT2_DIALOG_WORD_BOUNDS = {
    8: get_script_duration_bounds("lifestyle", 8),
    16: get_script_duration_bounds("lifestyle", 16),
    32: get_script_duration_bounds("lifestyle", 32),
}

PROMPT3_PRODUCT_WORD_BOUNDS = {
    8: get_script_duration_bounds("product", 8),
    16: get_script_duration_bounds("product", 16),
    32: get_script_duration_bounds("product", 32),
}

PROMPT1_WORD_BOUNDS = {
    8: get_script_duration_bounds("value", 8),
    16: get_script_duration_bounds("value", 16),
    32: get_script_duration_bounds("value", 32),
}
```

Add this public validation helper below `_script_word_count`:

```python
def resolve_effective_script_text(seed_data: Dict[str, Any], video_prompt: Optional[Dict[str, Any]] = None) -> str:
    prompt_audio = {}
    if isinstance(video_prompt, dict):
        prompt_audio = video_prompt.get("audio") or {}
        if not isinstance(prompt_audio, dict):
            prompt_audio = {}
    return str(
        prompt_audio.get("dialogue")
        or (seed_data or {}).get("script")
        or (seed_data or {}).get("dialog_script")
        or ""
    ).strip()


def validate_script_duration_contract(
    *,
    script: Any,
    post_type: Optional[str],
    target_length_tier: Optional[int],
    row_id: Optional[str] = None,
    table: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_tier = int(target_length_tier or 8)
    min_words, max_words = get_script_duration_bounds(post_type, resolved_tier)
    text = str(script or "").strip()
    word_count = _script_word_count(text)
    estimated_duration_s = estimate_duration_from_word_count(word_count)
    if not text:
        status = "missing_script"
    elif word_count < min_words:
        status = "underlength"
    elif word_count > max_words:
        status = "overlength"
    else:
        status = "valid"
    result = {
        "table": table,
        "row_id": row_id,
        "post_type": str(post_type or "value").strip().lower() or "value",
        "target_length_tier": resolved_tier,
        "word_count": word_count,
        "min_words": min_words,
        "max_words": max_words,
        "estimated_duration_s": estimated_duration_s,
        "status": status,
    }
    if status != "valid":
        raise ValidationError(
            f"{table or 'row'} {row_id or '<unknown>'} {result['post_type']} {resolved_tier}s script has "
            f"{word_count} words; expected {min_words}-{max_words}.",
            details=result,
        )
    return result
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```powershell
pytest tests/test_duration_contracts.py -v
```

Expected: all tests in `tests/test_duration_contracts.py` pass.

- [ ] **Step 6: Commit**

```powershell
git add app/core/video_profiles.py app/features/topics/topic_validation.py tests/test_duration_contracts.py
git commit -m "fix: centralize script duration contract"
```

---

### Task 2: Persistence And Bank Selection Gates

**Files:**
- Modify: `app/features/topics/queries.py`
- Modify: `tests/test_duration_contracts.py`

- [ ] **Step 1: Add failing tests for post creation and stale bank rows**

Append to `tests/test_duration_contracts.py`:

```python
from unittest.mock import MagicMock, patch


@patch("app.features.topics.queries.get_supabase")
def test_create_post_for_batch_rejects_underlength_32s_lifestyle(mock_get_sb):
    fake_client = MagicMock()
    mock_get_sb.return_value = type("SB", (), {"client": fake_client})()

    from app.features.topics.queries import create_post_for_batch

    with pytest.raises(ValidationError) as exc:
        create_post_for_batch(
            batch_id="batch-32",
            post_type="lifestyle",
            topic_title="Lifestyle",
            topic_rotation=_words(24),
            topic_cta="CTA",
            spoken_duration=8,
            seed_data={"script": _words(24), "script_review_status": "approved"},
            target_length_tier=32,
        )

    assert "lifestyle 32s script has 24 words" in str(exc.value)
    fake_client.table.assert_not_called()


def test_topic_suggestion_contract_rejects_stale_passed_bad_row(monkeypatch):
    import app.features.topics.queries as queries

    monkeypatch.setattr(
        queries,
        "get_all_topics_from_registry",
        lambda: [
            {
                "id": "topic-1",
                "title": "Stale Lifestyle",
                "script": _words(24),
                "canonical_topic": "Stale Lifestyle",
                "status": "active",
                "post_type": "lifestyle",
            }
        ],
    )
    monkeypatch.setattr(
        queries,
        "_fetch_topic_script_rows",
        lambda **_kwargs: [
            {
                "id": "script-1",
                "topic_registry_id": "topic-1",
                "title": "Stale Lifestyle",
                "script": _words(24),
                "target_length_tier": 32,
                "post_type": "lifestyle",
                "audit_status": "pass",
                "seed_payload": {"script": _words(24)},
            }
        ],
    )

    assert queries.list_topic_suggestions(target_length_tier=32, post_type="lifestyle") == []
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
pytest tests/test_duration_contracts.py::test_create_post_for_batch_rejects_underlength_32s_lifestyle tests/test_duration_contracts.py::test_topic_suggestion_contract_rejects_stale_passed_bad_row -v
```

Expected: the first test inserts instead of rejecting, and the second returns the stale suggestion.

- [ ] **Step 3: Add post creation validation**

In `app/features/topics/queries.py`, extend the import from `topic_validation`:

```python
    resolve_effective_script_text,
    validate_script_duration_contract,
```

In `create_post_for_batch(...)`, immediately after the `target_length_tier` injection, add:

```python
    if target_length_tier is not None:
        effective_script = resolve_effective_script_text(resolved_seed_data)
        if not effective_script:
            effective_script = topic_rotation
        contract = validate_script_duration_contract(
            script=effective_script,
            post_type=post_type,
            target_length_tier=target_length_tier,
            row_id=None,
            table="posts",
        )
        resolved_seed_data["script_duration_contract"] = contract
```

- [ ] **Step 4: Add stale bank-row selector validation**

In `list_topic_suggestions(...)`, after:

```python
            if normalized_script.get("audit_status") != "pass":
                continue
```

add:

```python
            try:
                validate_script_duration_contract(
                    script=normalized_script.get("script"),
                    post_type=normalized_script.get("post_type") or post_type,
                    target_length_tier=normalized_script.get("target_length_tier") or target_length_tier,
                    row_id=normalized_script.get("id"),
                    table="topic_scripts",
                )
            except ValidationError as exc:
                logger.warning(
                    "topic_script_duration_contract_rejected_at_selection",
                    script_id=normalized_script.get("id"),
                    topic_registry_id=topic_registry_id,
                    post_type=normalized_script.get("post_type") or post_type,
                    target_length_tier=normalized_script.get("target_length_tier") or target_length_tier,
                    details=getattr(exc, "details", {}),
                )
                continue
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```powershell
pytest tests/test_duration_contracts.py -v
```

Expected: all tests in `tests/test_duration_contracts.py` pass.

- [ ] **Step 6: Commit**

```powershell
git add app/features/topics/queries.py tests/test_duration_contracts.py
git commit -m "fix: reject duration-mismatched scripts before persistence"
```

---

### Task 3: Pre-Video Spend Gate

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_video_quota_guard.py`

- [ ] **Step 1: Add failing provider-call abort test**

Append to `tests/test_video_quota_guard.py`:

```python
def test_generate_all_videos_aborts_before_provider_for_underlength_32s_lifestyle(monkeypatch):
    posts = [
        {
            "id": "bad-lifestyle",
            "batch_id": "batch-32",
            "post_type": "lifestyle",
            "video_prompt_json": {"audio": {"dialogue": " ".join(["wort"] * 24) + "."}},
            "seed_data": {
                "script": " ".join(["wort"] * 24) + ".",
                "target_length_tier": 32,
                "script_review_status": "approved",
            },
            "video_status": "pending",
            "video_metadata": {},
        }
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    captured_calls = []

    def _fake_submit(**kwargs):
        captured_calls.append(kwargs)
        return {"operation_id": "operations/should-not-run", "status": "submitted"}

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "target_length_tier": 32,
            "post_type_counts": {"value": 0, "lifestyle": 1, "product": 0},
        },
    )
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=32)

    with pytest.raises(Exception) as exc:
        asyncio.run(generate_all_videos("batch-32", request))

    assert "lifestyle 32s script has 24 words" in str(exc.value)
    assert captured_calls == []
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
pytest tests/test_video_quota_guard.py::test_generate_all_videos_aborts_before_provider_for_underlength_32s_lifestyle -v
```

Expected: provider call is captured because the spend gate does not exist yet.

- [ ] **Step 3: Add video spend validation helpers**

In `app/features/videos/handlers.py`, import:

```python
from app.features.topics.topic_validation import (
    resolve_effective_script_text,
    validate_script_duration_contract,
)
```

Add helper near `_normalize_seed_data(...)`:

```python
def _validate_post_duration_contract_for_video(
    *,
    post: Dict[str, Any],
    batch: Dict[str, Any],
    video_prompt: Dict[str, Any],
) -> Dict[str, Any]:
    seed_data = _normalize_seed_data(post.get("seed_data"))
    batch_tier = batch.get("target_length_tier")
    seed_tier = seed_data.get("target_length_tier")
    if batch_tier is not None and seed_tier is not None and int(seed_tier) != int(batch_tier):
        raise ValidationError(
            f"Post {post.get('id')} target tier mismatch: seed_data.target_length_tier={seed_tier}, batch.target_length_tier={batch_tier}.",
            {"post_id": post.get("id"), "seed_target_length_tier": seed_tier, "batch_target_length_tier": batch_tier},
        )
    target_tier = int(batch_tier or seed_tier or 8)
    script = resolve_effective_script_text(seed_data, video_prompt)
    return validate_script_duration_contract(
        script=script,
        post_type=post.get("post_type"),
        target_length_tier=target_tier,
        row_id=post.get("id"),
        table="posts",
    )
```

Add batch mix helper near it:

```python
def _validate_batch_post_type_mix_for_video(posts: List[Dict[str, Any]], batch: Dict[str, Any]) -> None:
    expected = dict(batch.get("post_type_counts") or {})
    if not expected:
        return
    active_posts = [
        post for post in posts
        if not (_normalize_seed_data(post.get("seed_data")).get("script_review_status") == "removed"
                or _normalize_seed_data(post.get("seed_data")).get("video_excluded") is True)
    ]
    actual: Dict[str, int] = {}
    for post in active_posts:
        post_type = str(post.get("post_type") or "value").strip().lower()
        actual[post_type] = actual.get(post_type, 0) + 1
    normalized_expected = {str(k): int(v or 0) for k, v in expected.items() if int(v or 0) > 0}
    if actual != normalized_expected:
        raise ValidationError(
            "Batch post type mix does not match batch.post_type_counts.",
            {"batch_id": batch.get("id"), "expected": normalized_expected, "actual": actual},
        )
```

- [ ] **Step 4: Call the gate before provider submission**

In `generate_video(...)`, after `_load_or_build_video_prompt(...)` and before `_resolve_video_submission_plan(...)`, add:

```python
        script_contract = _validate_post_duration_contract_for_video(
            post=post,
            batch=batch,
            video_prompt=video_prompt,
        )
```

Then after `_build_submission_metadata(...)`, add:

```python
        submission_metadata["script_duration_contract"] = script_contract
```

In `generate_all_videos(...)`, after fetching `posts` and `batch`, add:

```python
        _validate_batch_post_type_mix_for_video(posts, batch)
```

Inside the loop, after `_load_or_build_video_prompt(...)` succeeds and before prompt construction, add:

```python
            script_contract = _validate_post_duration_contract_for_video(
                post=post,
                batch=batch,
                video_prompt=video_prompt,
            )
```

Add it to `prepared_submissions`:

```python
                    "script_contract": script_contract,
```

Then before metadata update in the submission loop, add:

```python
                submission_metadata["script_duration_contract"] = item["script_contract"]
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_video_quota_guard.py::test_generate_all_videos_aborts_before_provider_for_underlength_32s_lifestyle tests/test_video_quota_guard.py::test_generate_all_videos_routes_32s_vertex_submission_through_duration_profile -v
```

Expected: both pass. The existing 32s route test may need its fixture script expanded to 40+ words because it now exercises a real 32s contract.

- [ ] **Step 6: Run full video duration/quota slice**

Run:

```powershell
pytest tests/test_video_quota_guard.py tests/test_video_duration_routing.py -v
```

Expected: pass. If older tests intentionally use short scripts for route-only assertions, update only their fixture scripts, not the production gate.

- [ ] **Step 7: Commit**

```powershell
git add app/features/videos/handlers.py tests/test_video_quota_guard.py
git commit -m "fix: block duration-mismatched scripts before video spend"
```

---

### Task 4: Read-Only Duration Audit Script

**Files:**
- Create: `scripts/audit_duration_contracts.py`
- Modify: `tests/test_duration_contracts.py`

- [ ] **Step 1: Add failing audit helper test**

Append to `tests/test_duration_contracts.py`:

```python
def test_audit_classifies_generated_video_from_bad_script():
    from scripts.audit_duration_contracts import audit_post_row

    row = {
        "id": "post-1",
        "post_type": "lifestyle",
        "seed_data": {"script": _words(24), "target_length_tier": 32},
        "video_prompt_json": {"audio": {"dialogue": _words(24)}},
        "video_metadata": {"target_length_tier": 32},
        "video_url": "https://example.test/video.mp4",
        "video_status": "completed",
    }

    result = audit_post_row(row)

    assert result["status"] == "video_generated_from_bad_script"
    assert result["word_count"] == 24
    assert result["min_words"] == 40
    assert result["max_words"] == 66
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
pytest tests/test_duration_contracts.py::test_audit_classifies_generated_video_from_bad_script -v
```

Expected: import fails because `scripts/audit_duration_contracts.py` does not exist.

- [ ] **Step 3: Create the read-only audit script**

Create `scripts/audit_duration_contracts.py`:

```python
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402
from app.core.video_profiles import estimate_duration_from_word_count, get_script_duration_bounds  # noqa: E402
from app.features.topics.topic_validation import _script_word_count, resolve_effective_script_text  # noqa: E402


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _status(*, script: str, post_type: str, target_length_tier: Any, has_video: bool) -> Dict[str, Any]:
    if target_length_tier in (None, ""):
        return {"target_length_tier": None, "status": "missing_tier", "word_count": 0, "min_words": None, "max_words": None}
    tier = int(target_length_tier)
    min_words, max_words = get_script_duration_bounds(post_type, tier)
    word_count = _script_word_count(script)
    if not script.strip():
        status = "missing_script"
    elif word_count < min_words:
        status = "underlength"
    elif word_count > max_words:
        status = "overlength"
    else:
        status = "valid"
    if has_video and status in {"underlength", "overlength", "missing_script"}:
        status = "video_generated_from_bad_script"
    return {
        "target_length_tier": tier,
        "status": status,
        "word_count": word_count,
        "min_words": min_words,
        "max_words": max_words,
        "estimated_duration_s": estimate_duration_from_word_count(word_count),
    }


def audit_post_row(row: Dict[str, Any]) -> Dict[str, Any]:
    seed_data = _as_dict(row.get("seed_data"))
    video_prompt = _as_dict(row.get("video_prompt_json"))
    metadata = _as_dict(row.get("video_metadata"))
    tier = seed_data.get("target_length_tier") or row.get("target_length_tier") or metadata.get("target_length_tier")
    script = resolve_effective_script_text(seed_data, video_prompt)
    has_video = bool(row.get("video_url") or row.get("video_operation_id") or str(row.get("video_status") or "") in {"completed", "submitted", "processing", "extended_submitted", "extended_processing"})
    base = _status(script=script, post_type=row.get("post_type") or "value", target_length_tier=tier, has_video=has_video)
    base.update(
        {
            "table": "posts",
            "row_id": row.get("id"),
            "post_type": row.get("post_type") or "value",
            "script_preview": script[:180],
            "has_video": has_video,
            "video_status": row.get("video_status"),
        }
    )
    return base


def audit_topic_script_row(row: Dict[str, Any]) -> Dict[str, Any]:
    script = str(row.get("script") or "").strip()
    base = _status(
        script=script,
        post_type=row.get("post_type") or "value",
        target_length_tier=row.get("target_length_tier"),
        has_video=False,
    )
    base.update(
        {
            "table": "topic_scripts",
            "row_id": row.get("id"),
            "post_type": row.get("post_type") or "value",
            "script_preview": script[:180],
            "audit_status": row.get("audit_status"),
        }
    )
    return base


def _fetch_all(table: str, fields: str, page_size: int = 1000) -> Iterable[Dict[str, Any]]:
    sb = get_supabase().client
    offset = 0
    while True:
        response = sb.table(table).select(fields).range(offset, offset + page_size - 1).execute()
        rows = response.data or []
        for row in rows:
            yield row
        if len(rows) < page_size:
            break
        offset += page_size


def _write_json(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "table", "row_id", "post_type", "target_length_tier", "word_count",
        "min_words", "max_words", "estimated_duration_s", "status",
        "has_video", "video_status", "audit_status", "script_preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="output/audits")
    parser.add_argument("--format", choices=["json", "csv", "both"], default="both")
    args = parser.parse_args()

    rows: List[Dict[str, Any]] = []
    for row in _fetch_all("posts", "id,post_type,seed_data,video_prompt_json,video_metadata,video_url,video_operation_id,video_status"):
        rows.append(audit_post_row(row))
    for row in _fetch_all("topic_scripts", "id,post_type,target_length_tier,script,audit_status"):
        rows.append(audit_topic_script_row(row))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    if args.format in {"json", "both"}:
        _write_json(output_dir / f"duration-contract-audit-{stamp}.json", rows)
    if args.format in {"csv", "both"}:
        _write_csv(output_dir / f"duration-contract-audit-{stamp}.csv", rows)

    blocking = [row for row in rows if row["status"] != "valid"]
    print(f"audited={len(rows)} blocking={len(blocking)}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused audit test**

Run:

```powershell
pytest tests/test_duration_contracts.py::test_audit_classifies_generated_video_from_bad_script -v
```

Expected: pass.

- [ ] **Step 5: Run audit dry mode locally against configured database**

Run:

```powershell
python scripts/audit_duration_contracts.py --output-dir output/audits --format both
```

Expected: command writes `output/audits/duration-contract-audit-YYYYMMDD.json` and `.csv`, prints `audited=N blocking=M`, and exits `1` if mismatches exist.

- [ ] **Step 6: Commit**

```powershell
git add scripts/audit_duration_contracts.py tests/test_duration_contracts.py
git commit -m "feat: add duration contract audit script"
```

---

### Task 5: Approved Repair/Quarantine Tool

**Files:**
- Create: `scripts/repair_duration_contracts.py`
- Modify: `tests/test_duration_contracts.py`

- [ ] **Step 1: Add failing repair planning test**

Append to `tests/test_duration_contracts.py`:

```python
def test_repair_tool_builds_safe_updates_without_apply():
    from scripts.repair_duration_contracts import build_repair_update

    topic_update = build_repair_update(
        {
            "table": "topic_scripts",
            "row_id": "script-1",
            "status": "underlength",
            "post_type": "lifestyle",
            "target_length_tier": 32,
            "word_count": 24,
            "min_words": 40,
            "max_words": 66,
        }
    )
    assert topic_update["table"] == "topic_scripts"
    assert topic_update["payload"]["audit_status"] == "needs_repair"
    assert "24 words" in topic_update["payload"]["quality_notes"]

    post_update = build_repair_update(
        {
            "table": "posts",
            "row_id": "post-1",
            "status": "underlength",
            "post_type": "lifestyle",
            "target_length_tier": 32,
            "word_count": 24,
            "min_words": 40,
            "max_words": 66,
            "has_video": False,
        }
    )
    assert post_update["table"] == "posts"
    assert post_update["payload"]["video_prompt_json"] is None
    assert post_update["seed_patch"]["script_review_status"] == "pending"
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
pytest tests/test_duration_contracts.py::test_repair_tool_builds_safe_updates_without_apply -v
```

Expected: import fails because `scripts/repair_duration_contracts.py` does not exist.

- [ ] **Step 3: Create repair script**

Create `scripts/repair_duration_contracts.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402


BLOCKING_STATUSES = {"underlength", "overlength", "missing_script", "missing_tier", "video_generated_from_bad_script"}


def _note(row: Dict[str, Any]) -> str:
    return (
        f"duration_contract_failure: {row.get('post_type')} {row.get('target_length_tier')}s "
        f"has {row.get('word_count')} words; expected {row.get('min_words')}-{row.get('max_words')}; "
        f"status={row.get('status')}"
    )


def build_repair_update(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if row.get("status") not in BLOCKING_STATUSES:
        return None
    table = row.get("table")
    row_id = row.get("row_id")
    if not table or not row_id:
        return None
    note = _note(row)
    if table == "topic_scripts":
        return {
            "table": "topic_scripts",
            "row_id": row_id,
            "payload": {
                "audit_status": "needs_repair",
                "quality_notes": note,
            },
        }
    if table == "posts":
        seed_patch = {
            "script_review_status": "pending",
            "duration_contract_status": "needs_repair",
            "duration_contract_note": note,
        }
        metadata_patch = {
            "duration_contract_status": "needs_repair",
            "duration_contract_note": note,
            "duration_contract_checked_at": datetime.now(timezone.utc).isoformat(),
        }
        payload: Dict[str, Any] = {}
        if not row.get("has_video"):
            payload["video_prompt_json"] = None
        return {
            "table": "posts",
            "row_id": row_id,
            "payload": payload,
            "seed_patch": seed_patch,
            "metadata_patch": metadata_patch,
        }
    return None


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") if isinstance(data, dict) else data
    return [row for row in rows if isinstance(row, dict)]


def _apply_update(update: Dict[str, Any]) -> None:
    sb = get_supabase().client
    table = update["table"]
    row_id = update["row_id"]
    payload = dict(update.get("payload") or {})
    if table == "posts":
        current = sb.table("posts").select("seed_data,video_metadata").eq("id", row_id).limit(1).execute().data[0]
        seed_data = current.get("seed_data") or {}
        metadata = current.get("video_metadata") or {}
        if not isinstance(seed_data, dict):
            seed_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        seed_data.update(update.get("seed_patch") or {})
        metadata.update(update.get("metadata_patch") or {})
        payload["seed_data"] = seed_data
        payload["video_metadata"] = metadata
    sb.table(table).update(payload).eq("id", row_id).execute()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    updates = [update for row in _load_rows(Path(args.audit_json)) for update in [build_repair_update(row)] if update]
    output_path = Path(args.output) if args.output else Path(args.audit_json).with_name("duration-contract-repair-plan.json")
    output_path.write_text(json.dumps({"updates": updates}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"repair_updates={len(updates)} plan={output_path}")

    if args.apply:
        for update in updates:
            _apply_update(update)
        print(f"applied={len(updates)}")
    else:
        print("dry_run=true; pass --apply only after reviewing the repair plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused repair test**

Run:

```powershell
pytest tests/test_duration_contracts.py::test_repair_tool_builds_safe_updates_without_apply -v
```

Expected: pass.

- [ ] **Step 5: Generate repair plan from audit output**

Run:

```powershell
python scripts/repair_duration_contracts.py output/audits/duration-contract-audit-YYYYMMDD.json
```

Expected: writes `output/audits/duration-contract-repair-plan.json` and does not mutate the database.

- [ ] **Step 6: Apply only after manual approval**

Run only after reviewing the repair plan:

```powershell
python scripts/repair_duration_contracts.py output/audits/duration-contract-audit-YYYYMMDD.json --apply
```

Expected: topic scripts with blocking mismatches move to `audit_status='needs_repair'`; posts get `seed_data.duration_contract_status='needs_repair'`; ungenerated bad posts also clear `video_prompt_json`.

- [ ] **Step 7: Commit**

```powershell
git add scripts/repair_duration_contracts.py tests/test_duration_contracts.py
git commit -m "feat: add approved duration contract repair tool"
```

---

### Task 6: Live Stress Testing For Value, Lifestyle, Product

**Files:**
- Modify: `tests/stress_test_script_generation.py`

- [ ] **Step 1: Add strict post-type aware validation**

Modify imports near the top:

```python
from app.core.video_profiles import get_duration_profile, get_script_duration_bounds
from app.features.topics.agents import generate_lifestyle_topics, generate_product_topics
```

Change `validate_script(...)` signature:

```python
def validate_script(
    script_text: str,
    tier: int,
    post_type: str,
    source_summary: str = "",
) -> Tuple[List[str], List[str]]:
```

Replace the prompt1-only word bounds:

```python
    min_w, max_w = get_script_duration_bounds(post_type, tier)
    if word_count < min_w:
        blocking.append(f"word_count_low ({word_count}<{min_w})")
    elif word_count > max_w:
        blocking.append(f"word_count_high ({word_count}>{max_w})")
```

Keep sentence-count drift as soft for live stress, because the user request is specifically correct script lengths.

- [ ] **Step 2: Add generation paths for all post types**

Replace `run_one_shot(...)` internals with this branch before value registry selection:

```python
    if post_type == "lifestyle":
        generated = generate_lifestyle_topics(count=1, target_length_tier=tier)
        if not generated:
            raise RuntimeError("lifestyle generator emitted no topics")
        item = generated[0]
        script_text = str(item.get("script") or item.get("rotation") or "")
        source_summary = str(item.get("description") or "")
        framework = str(item.get("framework") or "PAL")
        hook_style = "live_lifestyle"
    elif post_type == "product":
        generated = generate_product_topics(count=1, target_length_tier=tier)
        if not generated:
            raise RuntimeError("product generator emitted no topics")
        item = generated[0]
        script_text = str(item.get("script") or item.get("rotation") or "")
        source_summary = str(item.get("source_summary") or "")
        framework = str(item.get("framework") or "PAL")
        hook_style = "live_product"
    else:
        topic = random.choice(topics)
        topic_id = topic["id"]
        title = topic.get("title") or ""
        result = _call_expand_with_transport_retry(
            topic_registry_id=topic_id,
            title=title,
            post_type=post_type,
            target_length_tier=tier,
        )
        captured = list(getattr(_capture_box, "variants", []) or [])
        if not captured:
            return ShotResult(
                shot_id=shot_id,
                phase=phase,
                tier=tier,
                post_type=post_type,
                topic_id=topic_id,
                topic_title=title[:80],
                framework="",
                hook_style="",
                started_at=started_at,
                duration_s=time.time() - started_at,
                success=False,
                strict_pass=False,
                error=f"no_variant_emitted (generated={result.get('generated', 0)})",
            )
        v = captured[-1]
        script_text = str(v.get("script") or "")
        framework = str(v.get("framework") or "")
        hook_style = str(v.get("hook_style") or "")
        source_summary = str(v.get("source_summary") or "")
```

Then call:

```python
        blocking, soft = validate_script(script_text, tier, post_type, source_summary=source_summary)
```

- [ ] **Step 3: Add CLI controls**

Add arguments:

```python
    parser.add_argument("--post-types", default="value,lifestyle,product")
    parser.add_argument("--min-usable-pct", type=float, default=100.0)
    parser.add_argument("--min-strict-pct", type=float, default=100.0)
```

After `smoke_tiers`, add:

```python
    post_types = [p.strip() for p in args.post_types.split(",") if p.strip()]
```

Replace job construction in smoke, concurrency, and heavy so every tier uses every selected post type:

```python
for tier in smoke_tiers:
    for post_type in post_types:
        r = run_one_shot(phase="smoke", tier=tier, post_type=post_type, topics=topic_pool)
```

For concurrency/heavy jobs:

```python
for tier in (8, 16, 32):
    for post_type in post_types:
        jobs.extend([(tier, post_type)] * n)
```

Replace final return:

```python
    return 0 if pct >= args.min_usable_pct and strict_pct >= args.min_strict_pct else 2
```

- [ ] **Step 4: Run smoke live stress**

Run with real env:

```powershell
python tests/stress_test_script_generation.py --phases smoke --smoke-tiers 8,16,32 --post-types value,lifestyle,product --output-dir output/stress/duration-contract --seed 20260514 --min-usable-pct 100 --min-strict-pct 100
```

Expected: exits `0`, writes JSONL and Markdown under `output/stress/duration-contract`, and reports strict-spec `9/9 (100.0%)`.

- [ ] **Step 5: Run concurrency live stress**

Run:

```powershell
python tests/stress_test_script_generation.py --phases concurrency --concurrency-parallel 2 --post-types value,lifestyle,product --output-dir output/stress/duration-contract --seed 20260514 --min-usable-pct 100 --min-strict-pct 100
```

Expected: exits `0`, strict-spec `18/18 (100.0%)`.

- [ ] **Step 6: Run heavy live stress**

Run:

```powershell
python tests/stress_test_script_generation.py --phases heavy --heavy-parallel 3 --heavy-rounds 2 --post-types value,lifestyle,product --output-dir output/stress/duration-contract --seed 20260514 --min-usable-pct 100 --min-strict-pct 100
```

Expected: exits `0`, strict-spec `54/54 (100.0%)`. Any blocking word-count issue means return to Task 1 or the relevant generator path before proceeding.

- [ ] **Step 7: Commit**

```powershell
git add tests/stress_test_script_generation.py
git commit -m "test: stress duration contracts across script types"
```

---

### Task 7: Full Verification And Live Audit Closure

**Files:**
- No new files.
- Verify all modified files.

- [ ] **Step 1: Run focused regression suite**

Run:

```powershell
pytest tests/test_duration_contracts.py tests/test_topic_researcher_queries.py tests/test_lifestyle_generation_regression.py tests/test_product_prompt3.py tests/test_video_quota_guard.py tests/test_video_duration_routing.py -v
```

Expected: pass with zero failures.

- [ ] **Step 2: Run read-only audit again**

Run:

```powershell
python scripts/audit_duration_contracts.py --output-dir output/audits --format both
```

Expected after approved repair: `blocking=0`, exit `0`.

- [ ] **Step 3: Run live stress full story**

Run:

```powershell
python tests/stress_test_script_generation.py --phases smoke,concurrency,heavy --concurrency-parallel 2 --heavy-parallel 3 --heavy-rounds 2 --post-types value,lifestyle,product --output-dir output/stress/duration-contract --seed 20260514 --min-usable-pct 100 --min-strict-pct 100
```

Expected: exit `0`, strict-spec `81/81 (100.0%)`.

- [ ] **Step 4: Inspect generated artifacts**

Open the newest files:

```powershell
Get-ChildItem output\\audits | Sort-Object LastWriteTime -Descending | Select-Object -First 4
Get-ChildItem output\\stress\\duration-contract | Sort-Object LastWriteTime -Descending | Select-Object -First 4
```

Expected: audit JSON/CSV and stress JSONL/Markdown exist for this run.

- [ ] **Step 5: Commit final verification artifact references**

Do not commit generated audit/stress outputs unless the repo already tracks similar outputs. Instead, record the artifact paths in the final implementation notes.

Run:

```powershell
git status --short
```

Expected: only intentional source/test/script changes are staged or unstaged.

---

## Repair Policy

- Do not auto-pad underlength scripts to make them pass.
- Existing bad `topic_scripts` rows become `audit_status='needs_repair'`.
- Existing bad `posts` rows get `seed_data.duration_contract_status='needs_repair'`.
- Bad posts without submitted/generated video clear `video_prompt_json` and return to `script_review_status='pending'`.
- Bad posts with submitted/generated video keep URL/operation metadata but get `video_metadata.duration_contract_status='needs_repair'`; regeneration is an operator action.
- Live stress failures are treated as generator defects, not as audit noise.

## Self-Review

Spec coverage:
- Canonical contract: Task 1.
- Read-only DB audit: Task 4.
- Dry audit and output artifacts: Task 4 and Task 7.
- Repair/quarantine: Task 5.
- Pre-video spend gate: Task 3.
- Script persistence gate: Task 2.
- 32s provider target ambiguity and metadata: Task 3 preserves provider target and adds script contract metadata.
- Regression tests: Tasks 1-5.
- Live stress testing for value/lifestyle/product: Task 6 and Task 7.

Placeholder scan:
- No placeholder-marker steps remain.
- Every code-changing task includes concrete code blocks and exact commands.

Type consistency:
- `get_script_duration_bounds(post_type, target_length_tier)` is defined in Task 1 and reused by validation, audit, and stress code.
- `validate_script_duration_contract(...)` returns `status`, `word_count`, `min_words`, `max_words`, and `estimated_duration_s`, which are reused consistently in metadata and tests.
- Repair tooling consumes audit rows using `table`, `row_id`, `status`, `post_type`, `target_length_tier`, `word_count`, `min_words`, `max_words`, and `has_video`, all produced by the audit script.

Plan complete and saved to `docs/superpowers/plans/2026-05-14-duration-contract-hardening.md`.

Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
