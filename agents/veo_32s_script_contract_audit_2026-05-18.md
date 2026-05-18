# VEO 32s Script Contract Audit - 2026-05-18

## Scope

Live batch: `c26c7bfa-b3cd-4cc2-a019-53766665accf`

Goal: audit the recent 32s script and VEO-duration commits, identify why the live 32s batch failed, and close the contract gap for future value, product, and lifestyle 32s scripts.

## Environment Matrix

- Repo: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`
- Branch at audit start: `main`
- Deployed live app health during audit: `/livez` alive, `/health` healthy, database ok
- Live batch state at audit time: `S4_SCRIPTED`
- Live batch target tier: `32`
- Live batch creation mode: `automated`
- Local test runner: `python3 -m pytest`
- Local Python warning observed: Python 3.9 is past Google auth best-effort support, but tests ran successfully

## Live Batch Evidence

The live batch is correctly stored as 32s. The older duration-selection bug is not the cause here.

| post | type | words under current counter | old contract result | VEO preflight result |
| --- | --- | ---: | --- | --- |
| `2c8e8ade-49af-4c10-8acd-969f103c8c15` | value | 59 | previously valid via `54-74` | segment preflight valid: `[16, 15, 15, 13]` |
| `ce440501-76b7-4437-ad4a-c8ca3a227671` | product | 48 | previously valid via `32-66` | fails: segment 0 has 8 words, needs 16 |
| `fba326e7-323b-430a-b116-55520a102873` | lifestyle | 65 | valid via `64-84` | segment preflight valid: `[19, 16, 16, 14]` |

The exact failing post is the product post `ce440501-76b7-4437-ad4a-c8ca3a227671`.

The failing error boundary is local preflight before provider submission:

`Veo extended segment is too short for its assigned duration window.`

Failure details reproduced locally from the live row:

- target tier: `32`
- planned hops: `3`
- required segments: `4`
- segment 0 budget: `8s`
- segment 0 word count: `8`
- segment 0 minimum: `16`
- segment preview: `Du suchst einen Treppenlift, der sich wirklich anpasst?`

## Root Cause

The script duration contract and the VEO segment contract drifted apart.

Current 32s VEO submission uses an 8s base plus three 7s extensions. That path requires four spoken segments. The preflight lower bounds are:

- base segment: at least `16` words
- extension 1: at least `14` words
- extension 2: at least `14` words
- final extension: at least `12` words
- practical minimum for a 32s extended chain: `56` words, and ideally higher for natural pacing

But the central script contract still allowed:

- value 32s: `54-74`
- product 32s: `32-66`
- lifestyle 32s: `64-84`

So product scripts could be approved, persisted, and moved to video generation even when they could never satisfy the VEO 32s segment budget. The live product script is exactly that: 48 words, accepted upstream, rejected at VEO preflight.

There was also a product-specific prompt drift:

- `prompt3_32s.txt` told the model to write `40-66 Wörter`
- `PROMPT3_PRODUCT_WORD_BOUNDS` effectively allowed `32-66`
- `PROMPT3_PRODUCT_SENTENCE_BOUNDS` allowed `4-5` sentences while the prompt asked for `5-6`

That made the product path easier to undershoot than value/lifestyle.

## Commit Audit

### `6e5b627` - preserve automated batch duration

This earlier fix is still holding. The live batch row has `target_length_tier=32`, so this incident is not the old hidden-input/8s-drift bug.

### `315725f` - harden script duration contracts

This introduced `SCRIPT_WORD_BOUNDS` and the audit/repair tools. It improved enforcement, but it encoded product 32s as `32-66` and value 32s as `54-74`, which were below the VEO 32s segment floor.

### `87a7942` - 32s VEO consistency and recommendations

This added the right VEO preflight behavior: reject underfilled extension segments before spending provider quota. That guardrail is working. The problem is that upstream script generation still allowed scripts the guardrail must reject.

### `ad288d0` - harden 32s lifestyle script contracts

This fixed lifestyle by moving 32s lifestyle to `64-84`, but it did not apply the same discipline to product or value.

### `7e5c53a` - harden 32s lifestyle fallback scripts

This fixed lifestyle fallback padding and word counting. There was no equivalent product fallback hardening, so product fallback/provider-retry paths could still emit short scripts.

### `234aa7f` - strengthen VEO subtitle negatives

No root-cause contribution. This affects visual/text-overlay negatives, not script length.

### `4b5c83e` - harden Vertex VEO duration routing

This fixed formatted-number counting and segment rebalance logic, especially for 16s. It still left product 32s at `32-66` and the product prompt at `40-66`, so the live failure remained possible.

## Fix Applied Locally

Files changed:

- `app/core/video_profiles.py`
  - value 32s contract: `68-88`
  - product 32s contract: `64-84`
  - lifestyle stays `64-84`
- `app/features/topics/prompt_data/prompt3_32s.txt`
  - product prompt now asks for `64-84 Wörter`
- `app/features/topics/topic_validation.py`
  - product 32s sentence contract aligned to `5-6`
- `app/features/topics/prompt3_runtime.py`
  - product fallback now emits a longer, more useful 32s fallback script
- Tests updated to lock the corrected contracts and the live failure shape.

## Existing Batch Impact

The existing live batch should not be retried as-is after this patch. It contains two scripts that are now correctly classified as underlength:

- value post: 59 words, should be regenerated to `68-88`
- product post: 48 words, should be regenerated to `64-84`

The lifestyle post is already valid.

## Verification

Red test first:

```bash
python3 -m pytest -q \
  tests/test_duration_contracts.py::test_canonical_script_duration_bounds \
  tests/test_duration_contracts.py::test_legacy_prompt_bound_wrappers_use_canonical_contract \
  tests/test_duration_contracts.py::test_32s_product_script_below_veo_segment_floor_is_rejected_by_contract \
  tests/test_product_prompt3.py::test_generate_product_topics_synthesizes_tier_fallback_on_vertex_credential_failure \
  tests/test_topic_prompt_templates.py::test_build_prompt3_uses_32s_text_template \
  tests/test_video_duration_routing.py::test_prompt3_32s_template_uses_current_sentence_budget_language
```

Result before code fix: `7 failed, 9 passed`.

Focused post-fix suites:

```bash
python3 -m pytest -q \
  tests/test_duration_contracts.py \
  tests/test_product_prompt3.py \
  tests/test_lifestyle_generation_regression.py \
  tests/test_topic_prompt_templates.py \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py \
  tests/test_video_poller_extension_chain.py
```

Result: `215 passed`.

Adjacent topic/query regression:

```bash
python3 -m pytest -q \
  tests/test_topics_gemini_flow.py \
  tests/test_topic_researcher_queries.py \
  tests/test_batches_status_progress.py
```

Result: `109 passed`.

## Recommendation

Deploy the patch, then regenerate the value and product scripts for batch `c26c7bfa-b3cd-4cc2-a019-53766665accf` before retrying video generation. Do not manually force-submit the current product script; the VEO guardrail is correctly rejecting it.
