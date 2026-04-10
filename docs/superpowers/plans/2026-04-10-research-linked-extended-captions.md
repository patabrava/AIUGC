# Research-Linked Extended Captions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a depth-gated extended publish-caption path that emits research-linked long-form captions only when the seed payload is rich enough, while preserving the current short caption as the default fallback.

**Architecture:** Keep the public caption bundle contract stable and implement the new behavior inside the existing topic caption generator. The generator will classify each payload as `standard` or `extended` using a pure depth gate over already-available seed data, then render the appropriate caption shape and fall back to the current short path whenever the long-form path is too thin or fails validation. Downstream publish code stays unchanged because `publish_caption` still comes from `resolve_selected_caption(...)` and the bundle shape remains the same.

**Tech Stack:** Python 3.11, pytest, existing `app.features.topics.captions` module, existing prompt text file, existing structured validation utilities. No new dependencies.

**Scope Budget:** `{files: 3, LOC/file: <=320 target and <=500 hard, deps: 0}`

---

### Task 1: Add the profile gate and extended prompt contract

**Files:**
- Modify: `app/features/topics/captions.py`
- Modify: `app/features/topics/prompt_data/captions_prompt.txt`

- [ ] **Step 1: Write the failing test**

Add a unit test that proves a seed payload with `3` distinct source URLs and `5` usable facts selects the `extended` profile, and a thin payload keeps `standard`.

```python
def test_caption_profile_gate_uses_extended_only_for_deep_payloads():
    deep_payload = {
        "strict_seed": {"facts": ["f1", "f2", "f3", "f4", "f5"]},
        "source": {"url": "https://one.example"},
        "source_urls": [
            {"url": "https://one.example"},
            {"url": "https://two.example"},
            {"url": "https://three.example"},
        ],
    }
    thin_payload = {
        "strict_seed": {"facts": ["f1", "f2"]},
        "source_urls": [{"url": "https://one.example"}],
    }

    assert captions.select_caption_profile(deep_payload) == "extended"
    assert captions.select_caption_profile(thin_payload) == "standard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_caption_generation.py::test_caption_profile_gate_uses_extended_only_for_deep_payloads -v`
Expected: FAIL because `select_caption_profile(...)` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add a small pure helper in `app/features/topics/captions.py` that reads `strict_seed.facts`, `source.url`, and `source_urls`, then returns `extended` only when the payload has at least `3` unique URLs and `5` facts.

```python
def select_caption_profile(seed_payload: Dict[str, Any]) -> str:
    facts = list((seed_payload.get("strict_seed") or {}).get("facts") or [])
    urls = _collect_caption_source_urls(seed_payload)
    if len(facts) >= 5 and len(urls) >= 3:
        return "extended"
    return "standard"
```

Update `captions_prompt.txt` so one prompt template can describe both profiles without changing the caller contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_caption_generation.py::test_caption_profile_gate_uses_extended_only_for_deep_payloads -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/captions.py app/features/topics/prompt_data/captions_prompt.txt tests/test_caption_generation.py
git commit -m "feat: add extended caption profile gate"
```

### Task 2: Implement the extended caption builder and fallback routing

**Files:**
- Modify: `app/features/topics/captions.py`
- Modify: `app/features/topics/prompt_data/captions_prompt.txt`

- [ ] **Step 1: Write the failing test**

Add tests that prove the extended path renders a long-form caption with a TL;DR block, a compact source block, and a short CTA/hashtag tail, while malformed extended output falls back to the existing short caption.

```python
def test_generate_caption_bundle_uses_extended_profile_when_research_is_deep():
    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein anderes Skript.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {"facts": ["F1", "F2", "F3", "F4", "F5"]},
            "source": {"url": "https://source-a.example"},
            "source_urls": [
                {"url": "https://source-a.example"},
                {"url": "https://source-b.example"},
                {"url": "https://source-c.example"},
            ],
        },
    )
    assert bundle["caption_profile"] == "extended"
    assert "TL;DR" in bundle["selected_body"]
    assert "https://source-a.example" in bundle["selected_body"]
```

```python
def test_generate_caption_bundle_falls_back_to_standard_when_extended_validation_fails():
    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein anderes Skript.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {"facts": ["F1", "F2", "F3", "F4", "F5"]},
            "source_urls": [
                {"url": "https://source-a.example"},
                {"url": "https://source-b.example"},
                {"url": "https://source-c.example"},
            ],
        },
    )
    assert bundle["selected_body"]
    assert bundle["caption_profile"] == "standard"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

`pytest tests/test_caption_generation.py::test_generate_caption_bundle_uses_extended_profile_when_research_is_deep -v`

`pytest tests/test_caption_generation.py::test_generate_caption_bundle_falls_back_to_standard_when_extended_validation_fails -v`

Expected: FAIL because the generator does not yet route between profiles.

- [ ] **Step 3: Write minimal implementation**

Extend `generate_caption_bundle(...)` so it:

- accepts or derives the caption profile from the payload
- renders an extended caption with hook, TL;DR, evidence, sources, CTA, and hashtags when the gate passes
- keeps the current short caption generation path unchanged for `standard`
- falls back to the standard caption if the extended output fails validation, is too long, or lacks usable source URLs

Use the existing validation helpers where possible, and keep any new long-form validation deterministic and local to `captions.py`.

```python
profile = select_caption_profile(seed_payload)
if profile == "extended":
    extended_body = build_extended_caption(...)
    if validate_extended_caption(extended_body, ...):
        return bundle_for_extended(...)
return bundle_for_standard(...)
```

Also add optional bundle metadata such as `caption_profile`, `caption_depth_reason`, and normalized `source_urls` without changing the bundle contract consumed by downstream code.

- [ ] **Step 4: Run test to verify it passes**

Run:

`pytest tests/test_caption_generation.py::test_generate_caption_bundle_uses_extended_profile_when_research_is_deep -v`

`pytest tests/test_caption_generation.py::test_generate_caption_bundle_falls_back_to_standard_when_extended_validation_fails -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/captions.py app/features/topics/prompt_data/captions_prompt.txt tests/test_caption_generation.py
git commit -m "feat: add extended caption generation and fallback"
```

### Task 3: Add regression coverage for source links, validation, and unchanged defaults

**Files:**
- Modify: `tests/test_caption_generation.py`

- [ ] **Step 1: Write the failing test**

Add coverage for the visible contract:

- the long-form caption includes source URLs when the payload has them
- the standard path remains unchanged for thin payloads
- the bundle still returns the same keys downstream code expects

```python
def test_extended_caption_includes_source_links_and_preserves_bundle_shape():
    bundle = captions.generate_caption_bundle(
        topic_title="Thema",
        post_type="value",
        script="Anderes Skript.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {"facts": ["F1", "F2", "F3", "F4", "F5"]},
            "source_urls": [
                {"url": "https://one.example"},
                {"url": "https://two.example"},
                {"url": "https://three.example"},
            ],
        },
    )
    assert set(bundle.keys()) >= {"variants", "selected_key", "selected_body", "selection_reason"}
    assert "https://one.example" in bundle["selected_body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_caption_generation.py::test_extended_caption_includes_source_links_and_preserves_bundle_shape -v`
Expected: FAIL until the new assertions are implemented.

- [ ] **Step 3: Write minimal implementation**

Extend `tests/test_caption_generation.py` with both a deep-payload and a thin-payload assertion so the contract is explicit:

```python
def test_extended_caption_includes_source_links_and_preserves_bundle_shape():
    bundle = captions.generate_caption_bundle(
        topic_title="Thema",
        post_type="value",
        script="Anderes Skript.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {"facts": ["F1", "F2", "F3", "F4", "F5"]},
            "source_urls": [
                {"url": "https://one.example"},
                {"url": "https://two.example"},
                {"url": "https://three.example"},
            ],
        },
    )
    assert bundle["caption_profile"] == "extended"
    assert "https://one.example" in bundle["selected_body"]
    assert "TL;DR" in bundle["selected_body"]
    assert set(bundle.keys()) >= {"variants", "selected_key", "selected_body", "selection_reason"}


def test_thin_payload_keeps_standard_caption_path():
    bundle = captions.generate_caption_bundle(
        topic_title="Thema",
        post_type="value",
        script="Anderes Skript.",
        research_facts=["F1", "F2"],
        seed_payload={
            "strict_seed": {"facts": ["F1", "F2"]},
            "source_urls": [{"url": "https://one.example"}],
        },
    )
    assert bundle["caption_profile"] == "standard"
    assert bundle["selected_body"]
```

Keep the generator contract stable while making the long-form output compact enough for Instagram scanning and strict enough to fall back cleanly when the payload is not deep enough.

- [ ] **Step 4: Run the full targeted caption test file**

Run: `pytest tests/test_caption_generation.py -v`
Expected: PASS with the existing short-caption tests still green and the new extended-caption tests green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_caption_generation.py
git commit -m "test: cover extended caption fallback and source links"
```

## Self-Review Checklist

- Spec coverage: the gate, the long-form shape, source-link handling, fallback behavior, and contract stability are all covered by Tasks 1-3.
- Placeholder scan: no TBD/TODO/ambiguous implementation placeholders remain.
- Type consistency: the plan uses only `captions.py`, `captions_prompt.txt`, and `tests/test_caption_generation.py`; the proposed helper names (`select_caption_profile`, `_collect_caption_source_urls`, `build_extended_caption`, `validate_extended_caption`) stay localized to the same module and are referenced consistently.
- Scope check: this is a single caption-system change, not a multi-subsystem refactor.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-10-research-linked-extended-captions.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
