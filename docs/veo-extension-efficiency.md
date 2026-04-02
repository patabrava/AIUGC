# Veo 8s Base Extension Experiment

## Summary
- Action mode: planning
- Goal: test whether long Veo chains can switch from `4s` base clips to `8s` base clips to reduce paid requests without damaging completion rate, continuity, or speech quality
- Budget: `{files: 6, LOC/file: <=150, deps: 0}`
- Safety rule: this experiment must stay isolated behind a flag and must not replace the current production route until canaries prove equal-or-better reliability

## Phase Zero - Context

### Current Production Contract
- `8s` route:
  - `8`
  - total requests: `1`
- `16s` route:
  - `4 + 7 + 7`
  - total requests: `3`
- `32s` route:
  - `4 + 7 + 7 + 7 + 7`
  - total requests: `5`

### Current Problems
- Long routes burn quota quickly:
  - `16s` costs `3` Veo generations
  - `32s` costs `5` Veo generations
- More hops means:
  - more seam risk
  - more chances to hit quota mid-chain
  - more spend per delivered video

### Desired Experiment
Keep Veo's extension flow, but start from `8s` instead of `4s`.

Candidate experimental tiers:
- efficient `16`:
  - `8 + 7`
  - total requests: `2`
  - real delivered length likely around `15s`
- efficient `32`:
  - `8 + 7 + 7 + 7`
  - total requests: `4`
  - real delivered length likely around `29s`

### Non-Functional Requirements
- No production regression by default.
- No new dependencies.
- Full observability of:
  - route chosen
  - base duration
  - hop count
  - final trimmed duration
  - completion/failure cause
- Prompt contract must stay continuation-safe and final-hop-safe.
- Quota guard must remain active for both old and experimental routes.

## Decision

Implement the `8s`-base route as an opt-in experiment behind config.

Do not replace the current exact-duration route yet.

## Experimental Design

### Feature Flag
Add config:
- `veo_enable_efficient_long_route: bool = False`

When `False`:
- keep current production route unchanged

When `True`:
- `16` uses `8 + 7`
- `32` uses `8 + 7 + 7 + 7`

### Route Rules

#### Production Route
- `8 -> 8`
- `16 -> 4 + 7 + 7`
- `32 -> 4 + 7 + 7 + 7 + 7`

#### Experimental Route
- `8 -> 8`
- `16 -> 8 + 7`
- `32 -> 8 + 7 + 7 + 7`

### Cost Impact
- `16`: `3 -> 2` Veo requests
- `32`: `5 -> 4` Veo requests

This is the primary expected savings.

### Main Risks
- scripts may be too short for an `8s` base and cause stretched delivery
- final delivered runtime may be shorter than the label suggests
- seam quality might improve from fewer hops, but base pacing may worsen if sentence budgets are not adjusted

## Phase Breakdown

### P1: Config-Gated Profile Layer
**Objective:** add an isolated experimental profile path without changing default production behavior.

**Files**
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/core/config.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/core/video_profiles.py`

**Changes**
- add feature flag `veo_enable_efficient_long_route`
- add alternate profile values for `16` and `32`
- keep `8` unchanged
- keep chain-cost math consistent with chosen profile

**Pass Gate**
- default config reproduces current production route
- enabled config switches only long tiers

### P2: Prompt and Segment Contract Review
**Objective:** ensure the longer `8s` base does not cause slow dead air or overlong single-segment pacing.

**Files**
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/features/posts/prompt_builder.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/features/videos/handlers.py`

**Changes**
- preserve the current continuation vs final-hop prompt split
- make sure extended base prompt still uses continuation wording
- review segment budgeting assumptions so `16` and `32` do not under-segment

**Pass Gate**
- non-final hops do not contain final-stop language
- final hop still contains mouth-rest/end-stop language

### P3: Quota Guard Compatibility
**Objective:** ensure quota reservation cost changes automatically with the experimental route.

**Files**
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/features/videos/quota_guard.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/core/video_profiles.py`

**Changes**
- quota cost must derive from live profile hop count
- experimental route must reserve:
  - `16 -> 2`
  - `32 -> 4`

**Pass Gate**
- quota guard blocks based on experimental cost when flag is on
- quota guard blocks based on production cost when flag is off

### P4: Observability
**Objective:** make the experiment measurable.

**Files**
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/workers/video_poller.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC-extension-exp/app/features/videos/handlers.py`

**Changes**
- log selected route profile
- log requested base duration
- log expected hop target
- log final trimmed duration
- log total operation count
- log experiment flag state in submission metadata

**Pass Gate**
- every experimental run is identifiable from DB metadata and logs

### P5: Canary Validation
**Objective:** validate quality and cost against the current route.

**Canaries**
- one `16` with flag off
- one `16` with flag on
- one `32` with flag off
- one `32` with flag on

**What to compare**
- total Veo requests
- completion success rate
- final duration
- speech pacing
- seam quality
- whether the full script is spoken
- whether the final hop ends cleanly

**Pass Gate**
- no increase in generation failures
- no dropped tail speech
- no visible seam regression
- reduced request count as expected

## Test Plan

### Automated
- unit tests for profile switching
- unit tests for quota cost derivation
- regression tests for prompt contract
- extension-chain tests with new hop targets under flag

### Runtime
- inspect prompt audit rows
- inspect completion summary logs
- verify actual operation id count per post

## Success Criteria
- `16s` experimental route uses `2` requests
- `32s` experimental route uses `4` requests
- completion quality is not worse than current production
- no new quota-leak path is introduced

## Failure Criteria
- more provider failures
- stretched/slow speech on the longer base clip
- cutoffs at segment boundaries
- shorter output that is unacceptable for the product

## Recommendation
Start by implementing only P1 through P4 behind the config flag, then run canaries before any broader rollout.
