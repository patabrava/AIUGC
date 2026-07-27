# Implementation Block — Semantic UGC Original-Actor Identity Contract

Task signal: Execute `character-consistency-final-implementation-plan.md`, including one controlled exact-16-second live generation.

Route: GENERAL → EYE implementation-block. LIRA re-entry only if current repository contracts contradict the approved plan.

Goal: Keep original actor references and the approved scene plate as separate immutable authorities. Require an original-actor identity pass plus human attestation before scene-master approval, and require original-actor identity plus scene continuity before delivery.

User-visible behavior: The approval card shows both original actor references beside all scene candidates, explains each candidate’s identity result, enables only passed candidates, and requires explicit identity attestation. Final review evidence carries actor references, scene master, contact sheet, and separate gate reports.

{files, approximate LOC, dependencies}:
- `app/core/config.py` +35: stable scene model, image size, contract version, evaluator models/confidence; Pydantic only.
- `app/adapters/llm_client.py` +2: stable Pro alias correction; no dependency.
- `app/features/shot_frames/wheelchair_scene_plate.py` +35/-35: three independent bootstrap candidates and strengthened physical-realism prompt.
- `app/features/shot_frames/identity_qa.py` new ~220: strict scene-candidate and video actor identity schemas/evaluators.
- `app/features/semantic_videos/visual_contract.py` +55: deterministic generation contract basis/hash and validator.
- `app/features/semantic_videos/queries.py`, `handlers.py`, `schemas.py` +180: contract-aware anchor lookup, candidate gate persistence, fail-closed approval, attestation contract, observability.
- `supabase/migrations/20260726000000_semantic_scene_identity_contract.sql` new ~340: generation-contract column/uniqueness, legacy ineligibility, atomic approval enforcement/promotion metadata.
- `templates/batches/detail/_semantic_video.html`, `static/js/batches/semantic_video.js`, `app/features/batches/handlers.py` +130: side-by-side reference evidence, statuses, attestation, accessible errors.
- `app/features/shot_production/visual_qa.py`, `runner.py`, `workers/semantic_video_worker.py` +260: separate video actor-identity and scene-continuity reports, original-reference materialization, combined gate.
- `app/features/semantic_videos/service.py`, worker paid boundary +80: generation-contract/identity/approval hashes carried and revalidated.
- Focused tests +500; existing dependencies only.

Capability slices:
1. Add configuration and deterministic scene-generation contract; exclude legacy anchors; generate independent bootstrap candidates.
2. Add strict original-actor candidate evaluator and persist every pass/fail result without discarding other candidates.
3. Enforce passed current gate plus explicit attestation in Python and PostgreSQL approval paths; promote only contract-current anchors.
4. Render original references, per-candidate evidence, attestation, and regeneration recovery in the real approval UI.
5. Split final visual QA into actor identity and scene continuity; require both in worker, composition, retry, and delivery paths.
6. Add observability and regression coverage, then run focused/full tests, browser validation, and a controlled paid exact-16-second live delivery.

Boundaries/contracts:
- Actor authority: ordered, byte/hash-verified `actor_front`, `actor_three_quarter`.
- Scene authority: approved immutable candidate, bound to actor fingerprint, visual contract, generation contract, identity gate, and human approval.
- Anchor lookup key: `(actor_identity_id, actor_reference_fingerprint, generation_contract_hash)`.
- Candidate evaluator input order: front, three-quarter, candidate. Server alone computes `passed`.
- Video actor evaluator input order: front, three-quarter, labeled contact sheet.
- Scene evaluator input order: approved scene plate, labeled contact sheet.
- Veo input remains deterministic crop-derived first-frame I2V with generated audio and no Magnific dependency.
- Existing user edits in UI, Docker, Vertex principal verification, topics, and scripts remain intact.

Data/state:
- Candidate JSONB gains `generation_contract_hash` and `identity_gate_result`.
- Approved master gains attestation version/note, approver/time, actor fingerprint, candidate hash, and generation contract.
- Anchor table gains `generation_contract_hash`, gate/approval evidence, status; existing rows become `legacy_unverified`.
- Pipeline manifest gains immutable actor reference records plus `actor_identity_qa`, `scene_continuity_qa`, and combined `visual_qa`.

Validation/errors:
- Finite confidence in `[0,1]`; minimum defaults `0.90`.
- Any missing/malformed evaluator output, reference mismatch, stale evaluator version, model mismatch, candidate mismatch, anchor mismatch, or unavailable model fails closed.
- Candidate generation keeps valid uploaded/gated candidates when siblings fail; reservation releases when none pass or operation fails before a usable set.
- Paid submission rechecks source/master/shot/visual/generation/gate/approval contracts.

Observability:
- Structured events for candidate generation/gating/rejection, approval, anchor promotion, video identity, and scene continuity.
- Log only IDs and fingerprint/hash prefixes, never images, full prompts, credentials, or personal data.

Tests/browser/live:
- Focused suites listed in the approved plan after each slice.
- Full `pytest -q`.
- Real browser/computer verification of candidate comparison, disabled/allowed approval, attestation, error state, and responsive/keyboard behavior.
- Controlled live exact-16-second run: consented existing actor, two immutable references, actor-free location, three Pro candidates, explicit human approval, two independent 8-second Veo takes, dual QA, final media probe and manual artifact inspection.

Regression path:
- No Magnific call from Semantic UGC candidate-to-delivery.
- Legacy Character Consistency/Magnific paths remain unchanged.
- Existing user changes in overlapping files stay present.
- Unknown paid submissions remain non-resubmittable.

Pass criteria:
- All definition-of-done clauses in the approved plan hold.
- Focused and full tests pass.
- Browser verification passes.
- Live delivery is exactly 16 seconds within the existing frame tolerance, both visual reports pass, hashes/model IDs/costs are recorded, and manual evidence review confirms the same actor and scene.

Fail criteria:
- Any legacy anchor is selectable; any candidate can be approved without current automated gate and attestation; any paid take lacks contract checks; actor identity is judged only against the generated plate; Magnific is invoked; tests/browser/live evidence are incomplete.

Primary risks:
- Stable `gemini-3-pro-image` availability depends on the configured Vertex project/location.
- Strict multimodal gates can false-reject; no production override is permitted.
- Existing database deployments require the new migration before the Python route is enabled.
- Live generation requires usable provider credentials, storage, Supabase state, a consented actor, and operator approval.
