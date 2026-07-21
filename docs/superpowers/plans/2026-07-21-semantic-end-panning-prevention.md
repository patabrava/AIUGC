# Semantic End-Panning Prevention Implementation Plan

> Execute in the existing `codex/semantic-scene-plate-16s-fix` worktree using test-driven development.

## Task 1: Lock the contract with failing tests

- Extend prompt contract tests for locked post-speech camera and pan/tilt/dolly/orbit/camera-movement negatives.
- Add a stitcher regression proving the final 500 ms are excluded and the retained output is pitch-preservingly normalized to exact 16-second delivery.
- Extend contact-sheet tests to require a fourth `delivered-tail` row sampled immediately before the excluded margin.
- Run the focused tests and confirm the new assertions fail for the intended reasons.

## Task 2: Implement the Semantic-only fix

- Add the 500 ms delivery-tail safety constant to the Semantic duration contract.
- Apply the safety trim and pitch-preserving exact-duration normalization after transcript-safe Semantic composition in the same FFmpeg graph.
- Update the Veo positive and negative camera locks.
- Update contact-sheet sampling and layout to inspect the last delivered frame.

## Task 3: Verify behavior and regression safety

- Run focused prompt, planner, runner, and stitcher tests.
- Run Semantic handler, worker, and UI regressions.
- Recompose or inspect real source media to verify panning is absent after the safety-margin plus exact-duration retime path.
- Record the recurring repo prevention rule in `AGENTS.md`.

## Task 4: Publish and verify

- Review the final diff and repository status.
- Commit and push the isolated branch to `main` when all verification passes.
- Verify the deployment workflow and live health endpoints without starting a new paid Veo generation.
