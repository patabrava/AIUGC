# Veo 32s Dynamic Downgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make underfilled 32s Veo submissions automatically downgrade to the shortest viable hop chain so the final video stays coherent instead of stretching weak scripts across too many pauses.

**Architecture:** Keep the current 32s submission path, but stop treating `8 + 7 + 7 + 7` as mandatory when the actual script cannot sustain it. At submission time, pack the dialogue into the smallest hop count that still preserves all spoken content and sentence boundaries, persist the effective hop target in metadata, and let the worker consume only that effective target. This keeps the fix inside the video slice and avoids rewriting prompt generation first.

**Tech Stack:** Python 3.11, FastAPI backend slice, pytest, existing Veo worker flow, existing dialogue split utilities

**Locality Budget:** `{files: 3 modified + 1 plan file, LOC/file: <=180 touched lines target, deps: 0}`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/features/videos/handlers.py` | Choose the effective hop target from actual script capacity, repack dialogue into that hop count, and persist shortened-chain metadata |
| `tests/test_video_duration_routing.py` | Lock the downgrade decision, the repacked segment shape, and the metadata truth for 32s submissions |
| `tests/test_video_poller_extension_chain.py` | Prove the worker stops at the effective hop target and does not keep consuming unused tail segments |
| `app/core/video_profiles.py` | Optional only if the tests show the current 32s prompt floor still produces too many underfilled submissions; keep this out unless it is needed |

## Capability Map

- 32s still means the long-form route, but the runtime may downgrade from `8 + 7 + 7 + 7` to `8 + 7 + 7` when the script cannot support the full chain.
- The downgrade must happen after sentence-safe packing, not by dropping the tail segment.
- Metadata must record both the planned 32s hop count and the effective downgraded hop count.
- The worker must stop at the effective hop count only.
- The fix must preserve all spoken content and sentence boundaries.

## Debug Scopes

- `route_selection`
  - Wrong hop target chosen for an underfilled 32s script.
- `segment_repacking`
  - Tail content gets dropped or split awkwardly when the chain is shortened.
- `worker_consumption`
  - Extension worker still follows the planned 3-hop chain even after metadata says the route was shortened.

## Pass/Fail Criteria

- A 32s script that cannot support 3 extension hops is repacked into a 2-hop chain.
- A 32s script that can support 3 extension hops keeps the full chain.
- The metadata reflects `veo_planned_extension_hops_target = 3` and a smaller `veo_extension_hops_target` when downgraded.
- The worker only requests extension hops up to the effective target.
- No segment is cut mid-sentence.

### Task 1: Lock The Downgrade Contract In Tests

**Files:**
- Modify: `tests/test_video_duration_routing.py`
- Modify: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Add a failing 32s downgrade test**

```python
def test_build_veo_extended_base_prompt_downgrades_underfilled_32s_chain():
    seed_data = {
        "script": (
            "Deutschland 2026. Und du suchst eine wirklich altersgerechte Wohnung. "
            "Langfristige Planung ist dabei entscheidend, besonders für Mehrgenerationen und Pflegearrangements. "
            "Der Zuschuss 455 B hilft zwar mit bis zu 2.500 Euro, deckt aber oft nur einen Bruchteil der Kosten ab. "
            "Rechtliche Aspekte wie Eigentumsverhältnisse, Regelungen für den Todesfall und die Kostenaufteilung sollten vertraglich klar geregelt werden."
        ),
        "estimated_duration_s": 22,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_planned_extension_hops_target"] == 3
    assert seg_meta["veo_extension_hops_target"] == 2
    assert seg_meta["veo_chain_shortened_to_available_segments"] is True
    assert seg_meta["veo_segments_total"] == 3
```

- [ ] **Step 2: Add a failing 32s full-chain test**

```python
def test_build_veo_extended_base_prompt_keeps_full_32s_chain_when_script_supports_it():
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

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_extension_hops_target"] == 3
    assert seg_meta["veo_segments_total"] == 4
```

- [ ] **Step 3: Run the focused tests and confirm the current failure**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_downgrades_underfilled_32s_chain \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_keeps_full_32s_chain_when_script_supports_it \
  tests/test_video_poller_extension_chain.py
```

Expected: the new assertions fail until the runtime chooses an effective hop target and repacks the chain.

### Task 2: Implement Submission-Time Hop Downgrade And Repacking

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add a hop-selection helper beside the existing packer**

```python
def _resolve_veo_extension_hops_target(
    *,
    segments: list[str],
    planned_hops: int,
    profile: Any,
) -> int:
    effective_hops = max(int(planned_hops or 0), 0)
    while effective_hops > 0:
        required_segments = _required_veo_segments_for_profile_hops(effective_hops)
        if len(segments) < required_segments:
            effective_hops -= 1
            continue

        budgets = [
            _segment_time_budget_seconds(profile=profile, segment_index=index)
            for index in range(required_segments)
        ]
        if all(
            len(segment.split()) >= max(12, budgets[index] * 2 - 2)
            for index, segment in enumerate(segments[:required_segments])
        ):
            return effective_hops

        effective_hops -= 1

    return 0
```

Implementation rule:
- Keep the planned hop count when the packed script can support it.
- Drop to `planned_hops - 1` when the packed script cannot support the full chain cleanly.
- Continue dropping one hop at a time until the remaining segments fit the shorter chain.
- Never return a hop target that would require more segments than the packed script contains.

- [ ] **Step 2: Repack the script to the effective hop count**

```python
raw_segments = split_dialogue_sentences(script) if script else []
effective_hops = _resolve_veo_extension_hops_target(
    segments=raw_segments,
    planned_hops=planned_extension_hops,
    profile=profile,
)
segments = _pack_veo_segments_for_profile(
    raw_segments,
    planned_extension_hops=effective_hops,
    target_length_tier=target_length_tier,
)
```

Implementation rule:
- Pack after the effective hop target is known, not before.
- Keep all content.
- Preserve sentence order and sentence endings.

- [ ] **Step 3: Persist shortened-chain metadata truth**

```python
segment_metadata.update(
    {
        "veo_required_segments": _required_veo_segments_for_profile_hops(planned_extension_hops),
        "veo_planned_extension_hops_target": planned_extension_hops,
        "veo_extension_hops_target": effective_hops,
        "veo_chain_shortened_to_available_segments": effective_hops < planned_extension_hops,
    }
)
```

Implementation rule:
- The planned 32s target stays visible for audit.
- The effective target is what the worker consumes.

- [ ] **Step 4: Re-run the routing tests**

Run:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected:
- The downgrade test passes.
- The full-chain test still passes.
- Existing routing assertions remain green.

### Task 3: Verify The Worker Honors The Shortened Chain

**Files:**
- Modify: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Add a worker test that confirms the shorter chain stops early**

```python
def test_extension_chain_stops_after_effective_two_hops():
    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_segments": ["S1.", "S2.", "S3."],
        "veo_segments_total": 3,
        "veo_extension_hops_target": 2,
        "veo_extension_hops_completed": 1,
    }

    assert _needs_extension_hop(metadata) is True
    metadata["veo_extension_hops_completed"] = 2
    assert _needs_extension_hop(metadata) is False
```

- [ ] **Step 2: Run the worker test file**

Run:

```bash
.venv/bin/pytest -q tests/test_video_poller_extension_chain.py
```

Expected: the worker respects the effective hop target and does not try to consume a third extension hop.

- [ ] **Step 3: Commit the finished change**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py tests/test_video_poller_extension_chain.py docs/superpowers/plans/2026-05-15-veo-32s-dynamic-downgrade.md
git commit -m "fix: downgrade underfilled veo 32s chains"
```

## Self-Review

- Spec coverage: submission-time downgrade, repacking, metadata truth, and worker consumption are all covered.
- Placeholder scan: no TBD/TODO/fill-in sections remain.
- Type consistency: `planned_extension_hops`, `veo_extension_hops_target`, and `veo_chain_shortened_to_available_segments` are used consistently across tasks.
- Scope check: the plan stays inside the video slice and does not require a prompt-system rewrite.
