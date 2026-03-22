# Next Steps: Deep Research Output Expansion

## Goal
Maximize reusable script inventory per topic while keeping the flow stable and deduplicated.

## Current Gap
- Stability is improved, but fan-out depth is low.
- Variant persistence currently collapses when scripts are identical.
- Re-running the same topic does not reliably expand angles/coverage.

## Proposed Model

### 1) Topic Cluster = One Dossier
- Keep one deep-research request per topic.
- Normalize into multiple lane candidates (minimum 3, allow more when supported).

### 2) Generation Unit = Lane x Tier x Variant
- For each lane and tier (8/16/32), generate N variants (default: 3).
- Enforce distinctness per variant:
  - different hook opening
  - different concrete angle sentence
  - different CTA style

### 3) Novelty Memory (Anti-Duplicate)
- Before generation, fetch existing scripts for same topic cluster and tier.
- Inject do-not-repeat anchors into prompt context:
  - prior openings
  - prior claims used
  - prior CTA patterns
- After generation, run similarity checks against existing scripts.
- Retry only the candidate that fails novelty threshold.

### 4) Persistence Identity
- Use deterministic identity per variant:
  - topic_registry_id + target_length_tier + lane_key + bucket + variant_index
- Keep exact-duplicate guard, but do not overwrite all variants by script text alone.

### 5) Repeat Topic Behavior
- Default: extend inventory (append new lanes/variants only).
- Optional: refresh mode to replace stale scripts by date/quality thresholds.

## Minimal Data Changes (If Needed)
- Add `variant_index` (int) to `topic_scripts`.
- Add `generation_round` (int) to `topic_scripts`.

## Implementation Slice (Value-First)
1. Persistence identity update for non-colliding variants.
2. Prior-script memory injection in value PROMPT_1 generation.
3. Similarity gate + retry loop per candidate.
4. End-to-end test proving second run adds new scripts instead of overwriting.

## QA Focus
- Confirm each run increases inventory for repeated topics.
- Confirm no near-duplicate scripts are accepted.
- Confirm per-tier boundaries (8/16/32) remain valid.
- Confirm stored scripts remain German-native and prompt-compliant.
