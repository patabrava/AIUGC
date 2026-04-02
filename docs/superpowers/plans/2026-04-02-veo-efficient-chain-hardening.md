# Veo Efficient Chain Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the efficient Veo 3.1 16s/32s route by packing dialogue for the 8-second base clip, preserving full-script coverage across fewer hops, and locking seed reuse across the extension chain.

**Architecture:** Keep the existing efficient `8 + 7 (+ 7 + 7)` route and fix the failure at the runtime segmentation boundary instead of reverting the route. Build profile-aware delivery chunks in the video submission slice, align upstream sentence guidance to the efficient hop budget, and add regression tests that verify base-prompt packing, extension payload continuity, and seed reuse on every hop.

**Tech Stack:** Python 3.11, FastAPI backend slice, pytest, existing Veo REST adapter

**Plan Budget:** {files: 6 modified + 1 new plan file, LOC/file: <=150 changed lines per file target, deps: 0}

---

## File Structure

**Modify**
- `app/features/videos/handlers.py`
  - Replace raw sentence-per-hop handling with profile-aware segment packing for efficient Veo chains.
- `app/core/video_profiles.py`
  - Align efficient 16s/32s prompt guidance with actual delivery-chunk budgets.
- `app/features/topics/prompt_data/prompt3_32s.txt`
  - Stop requesting `5-6` standalone sentences for the efficient 32s route.
- `workers/video_poller.py`
  - Preserve and audit seed reuse through every extension hop; no new adapter abstraction.
- `tests/test_video_duration_routing.py`
  - Add failing tests for efficient base packing and full-script preservation.
- `tests/test_video_poller_extension_chain.py`
  - Add regression tests that assert the same Veo seed is passed on every extension hop.

**Create**
- `docs/superpowers/plans/2026-04-02-veo-efficient-chain-hardening.md`
  - This implementation plan.

## Capability Map

- Efficient 16s route remains `8 + 7`.
- Efficient 32s route remains `8 + 7 + 7 + 7`.
- Base prompt for efficient routes must contain enough spoken content for an 8-second clip.
- Runtime must preserve the full script instead of dropping the last sentence when efficient routes use fewer hops.
- Extension hops must continue to send `durationSeconds: 8`.
- Extension hops must reuse the same Veo seed as the base generation.
- Prompt guidance must stop asking upstream for more standalone sentences than the efficient route can deliver.

## Debug Scopes

- `runtime_segment_packing`
  - Wrong number of delivery chunks, short 8-second base, dropped final sentence.
- `prompt_contract_alignment`
  - Upstream prompt templates still request `5-6` sentence scripts for efficient 32s chains.
- `extension_seed_continuity`
  - Missing or inconsistent `seed` between base and extensions, causing avoidable variance.

## Pass/Fail Criteria

- Efficient 32s scripts are delivered as exactly 4 runtime chunks.
- Efficient 16s scripts are delivered as exactly 2 runtime chunks.
- The first runtime chunk for efficient routes can merge multiple short sentences.
- No runtime chunk is silently dropped when the script fits the efficient route.
- Each extension request includes `durationSeconds: 8`.
- Each extension request includes the same Veo `seed` used by the base request when available.
- Existing Veo routing and chaining tests remain green.

### Task 1: Write the failing efficient-route packing tests

**Files:**
- Modify: `tests/test_video_duration_routing.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add a failing 32s packing test**

```python
def test_build_veo_extended_base_prompt_packs_short_opening_for_efficient_32s():
    seed_data = {
        "script": (
            "Sorry, aber Physiotherapie ist nicht gleich Ergotherapie. "
            "Physio fokussiert auf koerperliche Funktionen und Beweglichkeit, "
            "waehrend Ergo handlungsorientiert deine Selbststaendigkeit im Alltag staerkt. "
            "Durch Alltagstraining lernst du, Routinetaetigkeiten wieder selbst zu bewaeltigen. "
            "Die Kosten uebernehmen primaer die Krankenkassen, du leistest allerdings die gesetzliche Zuzahlung. "
            "Seit 2024 bietet die Blankoverordnung Therapeuten mehr Flexibilitaet fuer deine Behandlung."
        ),
        "estimated_duration_s": 29,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "Sorry, aber Physiotherapie ist nicht gleich Ergotherapie." in prompt
    assert "Physio fokussiert auf koerperliche Funktionen" in prompt
    assert seg_meta["veo_segments_total"] == 4
    assert len(seg_meta["veo_segments"]) == 4
```

- [ ] **Step 2: Add a failing 16s packing test**

```python
def test_build_veo_extended_base_prompt_packs_to_two_segments_for_efficient_16s():
    seed_data = {
        "script": "Erster kurzer Satz. Zweiter kurzer Satz. Dritter Satz mit etwas mehr Inhalt.",
        "estimated_duration_s": 15,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert len(seg_meta["veo_segments"]) == 2
```

- [ ] **Step 3: Run the focused test file and confirm failure**

Run:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected:
- FAIL on the new efficient-route assertions because the current implementation still uses `segments[0]` as the entire base chunk.

### Task 2: Implement profile-aware runtime segment packing

**Files:**
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add a focused packing helper in the existing video slice**

```python
def _pack_veo_segments_for_profile(
    segments: list[str],
    *,
    planned_extension_hops: int | None,
    target_length_tier: int | None,
) -> list[str]:
    ...
```

Implementation requirements:
- Keep legacy behavior unchanged when no efficient-route profile applies.
- For efficient `16s`, return exactly `2` delivery chunks.
- For efficient `32s`, return exactly `4` delivery chunks.
- Merge adjacent short sentences into the base chunk first.
- Preserve sentence order and preserve all spoken content.

- [ ] **Step 2: Thread packed segments into `_build_veo_extended_base_prompt(...)`**

Expected code shape:

```python
raw_segments = split_dialogue_sentences(script) if script else []
segments = _pack_veo_segments_for_profile(
    raw_segments,
    planned_extension_hops=planned_extension_hops,
    target_length_tier=target_length_tier,
)
base_segment = segments[0] if segments else ""
```

- [ ] **Step 3: Keep budget validation profile-aware**

Expected behavior:
- Validate against required delivery chunks after packing, not only raw sentence count.
- Reject genuinely under-segmented scripts.
- Do not silently drop trailing content.

- [ ] **Step 4: Re-run the focused routing tests**

Run:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected:
- PASS for the new packing tests.
- Existing routing assertions remain green.

- [ ] **Step 5: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "fix: pack efficient veo dialogue chunks"
```

### Task 3: Align upstream sentence guidance with efficient hop budgets

**Files:**
- Modify: `app/core/video_profiles.py`
- Modify: `app/features/topics/prompt_data/prompt3_32s.txt`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Update efficient-route guidance in `video_profiles.py`**

Required changes:
- Efficient `16s` guidance should no longer ask for `3-4` fully independent sentences if runtime expects `2` delivery chunks.
- Efficient `32s` guidance should no longer ask for `5-6` fully independent sentences if runtime expects `4` delivery chunks.

- [ ] **Step 2: Update the 32s product prompt template**

Replace the current contract:

```text
Halte den Scripttext auf 40-66 Woerter und 5-6 Saetze.
```

With a delivery-friendly contract that matches efficient 32s output, for example:

```text
Halte den Scripttext auf 40-66 Woerter und formuliere ihn so, dass er in vier natuerliche Sprechbloecke passt.
```

- [ ] **Step 3: Add or update assertions for the new guidance**

Example assertion target:

```python
assert get_duration_profile(32).prompt2_sentence_guidance == "4 Sprechbloecke"
```

- [ ] **Step 4: Run the focused routing tests again**

Run:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/video_profiles.py app/features/topics/prompt_data/prompt3_32s.txt tests/test_video_duration_routing.py
git commit -m "fix: align efficient veo prompt guidance"
```

### Task 4: Lock seed reuse across extension hops with regression coverage

**Files:**
- Modify: `workers/video_poller.py`
- Modify: `tests/test_video_poller_extension_chain.py`
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Add a failing seed continuity test**

```python
def test_submit_extension_hop_reuses_base_veo_seed():
    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
    post = {
        "id": "post-seed",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."],
            "veo_segments_total": 3,
            "veo_current_segment_index": 0,
            "veo_seed": 123456789,
            "requested_aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }
    ...
    assert mock_veo.submit_video_extension.call_args[1]["seed"] == 123456789
```

- [ ] **Step 2: Preserve existing behavior explicitly in `workers/video_poller.py`**

Required behavior:
- Continue passing `metadata.get("veo_seed")` into `submit_video_extension(...)`.
- If seed is missing, do not synthesize a new one in the poller.
- Keep the entire chain on one seed when the base submission created one.

- [ ] **Step 3: Add audit/log assertions if practical**

Prefer adding coverage that confirms extension-hop logs or audit metadata continue to expose the reused seed, but avoid widening scope if that requires schema changes.

- [ ] **Step 4: Run the focused extension tests**

Run:

```bash
.venv/bin/pytest -q tests/test_video_poller_extension_chain.py tests/test_veo_client_payload.py
```

Expected:
- PASS
- Extension payload tests still assert `durationSeconds == 8`.
- New extension-chain test asserts seed continuity.

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py tests/test_veo_client_payload.py
git commit -m "test: lock veo extension seed continuity"
```

### Task 5: Whole-slice verification

**Files:**
- Modify: none
- Test: `tests/test_video_duration_routing.py`
- Test: `tests/test_video_poller_extension_chain.py`
- Test: `tests/test_veo_client_payload.py`
- Test: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Run the full focused Veo regression slice**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py \
  tests/test_video_poller_extension_chain.py \
  tests/test_veo_client_payload.py \
  tests/test_veo_prompt_contract.py
```

Expected:
- PASS

- [ ] **Step 2: Manual code review against Veo 3.1 docs**

Checklist:
- `durationSeconds: 8` on extension
- `resolution: 720p` on extension
- prior Veo video reused as extension input
- speech continuity preserved into the last second for non-final hops
- same seed reused across the chain when available

- [ ] **Step 3: Final commit**

```bash
git status --short
git add app/core/video_profiles.py app/features/topics/prompt_data/prompt3_32s.txt app/features/videos/handlers.py workers/video_poller.py tests/test_video_duration_routing.py tests/test_video_poller_extension_chain.py tests/test_veo_client_payload.py
git commit -m "fix: harden efficient veo extension chains"
```

## Self-Review

- Spec coverage:
  - Efficient route retained: covered in Tasks 1-2.
  - Word/segment adaptation for 8-second base: covered in Tasks 1-2.
  - Upstream prompt contract alignment: covered in Task 3.
  - Seed continuity on each extension: covered in Task 4.
  - Verification against Veo 3.1 docs: covered in Task 5.
- Placeholder scan:
  - No `TODO`, `TBD`, or abstract “handle edge cases” steps remain.
- Type consistency:
  - Uses existing `veo_seed`, `veo_segments`, `veo_extension_hops_target`, and `submit_video_extension(...)` naming.

Plan complete and saved to `docs/superpowers/plans/2026-04-02-veo-efficient-chain-hardening.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
