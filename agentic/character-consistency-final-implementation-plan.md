# Final Character-Consistency Implementation Plan

Status: Ready for implementation
Scope: Semantic UGC scene-plate and Veo 3.1 production path
Decision date: 2026-07-26

## 1. Objective

Produce photorealistic Semantic UGC videos in which:

- the actor remains recognizably the same person as the two original actor references;
- the approved background, wardrobe, manual wheelchair, framing, and apparent age remain stable;
- a generated image is never allowed to become the sole authority for actor identity;
- Magnific and its character LoRA do not participate in Semantic UGC image or video generation;
- every paid Veo request starts from an approved, immutable scene plate;
- failed identity or continuity checks block approval and delivery.

The production architecture will be:

```text
Unchanged actor_front + unchanged actor_three_quarter + actor-free location
                                  |
                                  v
                    Gemini 3 Pro Image candidates
                                  |
                                  v
              Original-actor identity gate + human approval
                                  |
                                  v
                    Immutable canonical scene plate
                                  |
                                  v
                   Deterministic restrained crops
                                  |
                                  v
                 Independent Veo 3.1 image-to-video takes
                                  |
                                  v
        Original-actor identity gate + scene continuity gate + QA
                                  |
                                  v
                         Approved exact delivery
```

## 2. Historical conclusions that constrain the implementation

The implementation must preserve the lessons established by the repository history:

1. `09d3427` proved that direct Veo reference images can produce resemblance, but do not provide a sufficiently controlled scene master.
2. `6743f74` through `11c6311` proved that a working Magnific LoRA does not guarantee the exact face across independent Mystic generations. Magnific output also establishes an older, less realistic synthetic appearance that contaminates every downstream video.
3. `d132078` and the associated live matrix showed that Veo performs substantially better when actor and scene identity are supplied through images.
4. `d816a50` and later prompt corrections showed that written phenotype descriptions compete with image identity and can destabilize the face, mouth, blinking, and expression.
5. `19a8325` and `f970a44` showed that long continuation chains and independently generated segments introduce drift and visible transition problems.
6. `c6a2945` correctly made the original actor images authoritative and removed the written character description from Semantic UGC.
7. `0346a92` correctly introduced a canonical scene plate and deterministic shot-deck crops.
8. `15590bc` added useful continuity QA, but the current comparison is self-referential: it compares the generated videos with the generated plate rather than independently proving that the plate still matches the original actor.

These conclusions rule out another Magnific tuning cycle, prompt-only identity, independently invented take images, and unbounded Veo continuation.

## 3. Final source-of-truth model

### 3.1 Actor identity master

The persisted `actor_front` and `actor_three_quarter` assets are the only identity authority.

For both assets, preserve:

- storage URI;
- MIME type;
- byte length;
- SHA-256;
- ordered role;
- actor identity ID;
- combined actor-reference fingerprint.

The original bytes must be downloaded and hash-verified before candidate generation, master approval, paid-plan creation, and final QA. A generated scene plate must never replace these assets in the identity contract.

### 3.2 Scene master

The approved scene plate is the authority for:

- background and room geometry;
- manual wheelchair;
- wardrobe;
- seated pose;
- camera height and distance;
- framing;
- face size;
- lighting;
- starting expression.

The scene master is not, by itself, proof of actor identity. It is valid only while bound to:

- the exact actor-reference fingerprint;
- a scene-plate generation contract hash;
- a passed automated identity gate;
- an explicit human approval;
- immutable image bytes and SHA-256.

### 3.3 Video result

The video must satisfy both masters:

- actor identity must match the original actor references;
- scene continuity must match the approved scene plate.

Passing one of these conditions cannot compensate for failing the other.

## 4. Model and provider decisions

### 4.1 Scene-plate model

Use the explicit stable model identifier:

```text
gemini-3-pro-image
```

Do not use the `nanobananapro` alias while it resolves to `gemini-3-pro-image-preview`.

Default request contract:

- aspect ratio: `9:16`;
- output size: `2K`;
- temperature: `0.2`;
- three ordered input images;
- one image output;
- no automatic fallback to Flash or Magnific.

Two-kilopixel output is sufficient for the existing 720p Veo delivery while preserving more facial detail than the video output can reproduce. A later 4K experiment may be performed separately, but it is not required for the initial production correction.

If the configured Vertex project or location cannot serve `gemini-3-pro-image`, fail closed with a precise configuration error before candidate reservation. Do not silently use `gemini-3.1-flash-image`, because that would make the quality contract dependent on runtime availability.

### 4.2 Video model

Keep the current Veo 3.1 first-frame image-to-video route:

- independent paid request per take;
- approved shot crop as first frame;
- `9:16`;
- eight seconds per take for the exact-16-second contract;
- generated audio enabled;
- motion, dialogue, and acoustic instructions only;
- no written facial phenotype;
- no Magnific reference images;
- no extension chain.

### 4.3 Magnific boundary

Remove Magnific from the Semantic UGC dependency graph. This does not require deleting the legacy Character Consistency feature in the first implementation.

The Semantic UGC route must never:

- resolve or require a Magnific LoRA;
- call Mystic;
- load a Magnific-generated scene reference;
- include a Magnific provider ID in a candidate or plan;
- fall back to Magnific after a Gemini failure.

Add a focused regression test proving that the Semantic UGC candidate-to-delivery path never invokes the Magnific adapter.

## 5. Candidate-generation correction

### 5.1 Bootstrap behavior

The current bootstrap creates candidate one from the original references and then derives candidates two and three from unapproved candidate one. This can propagate a wrong or beautified face into all three candidates.

Change bootstrap generation so all three candidates are generated independently from:

1. unchanged `actor_front`;
2. unchanged `actor_three_quarter`;
3. actor-free `location`.

Each candidate receives the same frozen composition contract and independent generation evidence. No unapproved generated candidate may be used as identity input for another candidate.

### 5.2 Established-anchor behavior

An established canonical actor scene anchor may be used only if:

- it was approved under the new scene-plate generation contract;
- it carries the current actor-reference fingerprint;
- its original identity gate passed;
- its human approval is present;
- its stored bytes and hash still match.

For a new location, derived candidates may use:

1. the approved canonical actor scene anchor;
2. unchanged `actor_front` as identity correction;
3. the new actor-free location.

Every derived candidate must still pass the original-actor identity gate. An approved anchor reduces drift; it does not waive validation.

### 5.3 Prompt contract

Retain fixed reference roles and the existing wheelchair/framing constraints. Strengthen the realism section with physical evidence rather than generic quality adjectives:

- ordinary camera-file skin texture;
- visible pores and natural tonal variation;
- realistic hairline and flyaways;
- natural under-eye and lip texture;
- mild facial asymmetry;
- ordinary indoor optics and available light;
- no beauty retouching, poreless skin, glamour lighting, CGI smoothness, or face averaging.

Do not add a written description of the actor's age, hair color, eye color, nose, mouth, or face shape. Those details remain image-authoritative.

## 6. Pre-approval actor-identity gate

Create a dedicated scene-plate identity evaluator. Do not overload the current video continuity evaluator with a different contract.

### 6.1 Inputs

The evaluator receives, in a fixed order:

1. original `actor_front`;
2. original `actor_three_quarter`;
3. generated candidate.

### 6.2 Required result

Return and strictly validate JSON with at least:

```json
{
  "same_person": true,
  "facial_geometry_consistent": true,
  "apparent_age_consistent": true,
  "hairline_and_hair_consistent": true,
  "skin_texture_natural": true,
  "not_beautified_or_stylized": true,
  "no_face_artifacts": true,
  "confidence": 0.0,
  "blocking_reasons": [],
  "observed_differences": []
}
```

The server computes `passed`; the model cannot choose it.

Initial pass contract:

- every boolean is `true`;
- confidence is at least `0.90`;
- `blocking_reasons` is empty;
- the response matches the strict schema;
- all three input assets pass MIME, byte-length, and hash validation.

Malformed, incomplete, unavailable, or low-confidence evaluation fails closed.

Model confidence is supporting evidence, not calibrated biometric certainty. The first promotion of an actor's canonical anchor therefore also requires human confirmation.

### 6.3 Candidate metadata

Persist an `identity_gate_result` on every candidate inside the existing JSONB master snapshot:

- status: `passed` or `failed`;
- evaluator model;
- evaluator contract version;
- evaluated actor-reference fingerprint;
- candidate SHA-256;
- component results;
- confidence;
- blocking reasons;
- observed differences;
- evaluation timestamp.

No new relational column is required for the per-candidate result because the master snapshot is already JSONB.

## 7. Approval UI and server enforcement

### 7.1 UI

In the Semantic UGC scene-plate approval area, display:

- original front actor reference;
- original three-quarter actor reference;
- all generated candidates;
- identity-gate status per candidate;
- concise gate differences and failure reasons;
- scene, wardrobe, wheelchair, and framing reminders.

The original references must remain visible while the operator examines a candidate. Do not make the operator remember the actor from another screen.

Enable the approval action only for candidates whose automated identity gate passed.

Use an explicit human attestation:

> I confirm that this is the same actor as both original references, without material age, facial-geometry, or realism drift.

### 7.2 Server

Client-side disabling is informational. The `master-approve` endpoint must independently reject:

- a missing identity result;
- a failed result;
- a result for a different candidate hash;
- a result for a different actor-reference fingerprint;
- a result produced by an obsolete evaluator contract;
- stale or changed actor source assets.

Persist the approver, timestamp, attestation version, candidate hash, actor-reference fingerprint, and optional approval note.

There is no override for a failed identity gate in the production Semantic UGC path. The available recovery action is to regenerate candidates.

## 8. Canonical-anchor versioning and migration

Changing the default image model is insufficient because existing actor anchors are selected by actor ID and actor-reference fingerprint. Without versioning, an older Flash or Magnific-era anchor can continue to be reused after deployment.

Add a migration that introduces a scene-plate generation contract identifier on `semantic_actor_scene_plate_anchors`, for example:

```text
generation_contract_hash
```

The contract hash must include:

- stable scene-plate model ID;
- prompt-contract version;
- reference-role contract;
- aspect ratio;
- image size;
- identity-evaluator contract version;
- minimum identity confidence;
- actor-reference fingerprint.

Update anchor uniqueness and lookup to include:

```text
(actor_identity_id, actor_reference_fingerprint, generation_contract_hash)
```

Existing anchors should be marked or treated as `legacy_unverified`. Do not delete them automatically. They remain auditable but are ineligible for the new Semantic UGC production contract.

Only promote a new anchor after both automated identity validation and explicit human approval.

## 9. Shot-deck and Veo generation

Retain the existing deterministic shot-deck implementation:

- original approved plate;
- restrained center crop;
- restrained left crop;
- restrained right crop;
- SHA-256 for every derived crop;
- no generative image call per video take.

This is an important consistency guarantee: all take-start images are pixel-derived from one approved scene plate.

Before each paid Veo submission, validate:

- master SHA-256;
- derived crop SHA-256;
- visual contract hash;
- actor-reference fingerprint;
- scene-plate generation contract hash;
- candidate identity gate;
- human master approval.

Keep the current paid-boundary intent persistence and unresolved-submission protection.

## 10. Final video identity and continuity gates

The current visual QA compares the approved scene plate with a contact sheet. Replace the single self-referential decision with two explicit evaluations.

### 10.1 Actor identity evaluation

Inputs:

1. original `actor_front`;
2. original `actor_three_quarter`;
3. labeled contact sheet from all generated takes.

Required checks:

- same person in every sampled frame;
- apparent age remains consistent;
- facial geometry remains consistent during speech;
- hairline and hair remain consistent;
- no beautification, face replacement, or synthetic smoothing;
- no malformed eyes, mouth, teeth, or facial transitions.

Use a strict server-computed pass contract and a minimum confidence of `0.90`.

### 10.2 Scene continuity evaluation

Inputs:

1. approved scene plate;
2. labeled contact sheet.

Retain the existing checks:

- wardrobe;
- room/background;
- manual wheelchair;
- stable framing;
- no camera pan, tilt, dolly, orbit, or unintended zoom;
- no text, captions, logos, watermarks, or malformed glyphs;
- no visual artifacts;
- final deliverable-tail stability.

### 10.3 Combined result

The video passes visual QA only when:

```text
actor_identity_gate.passed
AND scene_continuity_gate.passed
```

Persist both reports separately. Do not collapse them into one ambiguous `identity_same_person` boolean.

Manual final QA must display the original actor references, approved scene master, and contact sheet together.

## 11. Retry policy

### Candidate generation

- Generate exactly three candidates in one reserved candidate-generation operation.
- Retain passing candidates even if another candidate fails identity.
- Permit approval if at least one candidate passes.
- If none pass, release the reservation and offer a bounded regeneration.
- Never promote a failed candidate or use it as a derivation anchor.

### Veo takes

- Retry only the failed take.
- Reuse the same approved crop and prompt contract.
- Use a bounded alternate seed for a genuine provider-generation retry.
- Preserve successful takes when hashes and contracts still match.
- Keep the existing unresolved-paid-submission and duplicate-charge protections.
- Do not fall back to text-to-video, reference-to-video without the approved first frame, Magnific, or a continuation chain.

## 12. Configuration

Add explicit configuration values with production-safe defaults:

```text
SEMANTIC_SCENE_PLATE_MODEL=gemini-3-pro-image
SEMANTIC_SCENE_PLATE_IMAGE_SIZE=2K
SEMANTIC_SCENE_PLATE_CONTRACT_VERSION=pro-identity-v1
SEMANTIC_SCENE_IDENTITY_GATE_MODEL=<configured Gemini vision model>
SEMANTIC_SCENE_IDENTITY_MIN_CONFIDENCE=0.90
SEMANTIC_VIDEO_IDENTITY_MIN_CONFIDENCE=0.90
```

The confidence values must parse as finite values from zero through one. Invalid configuration must fail at startup.

Do not add an automatic provider fallback setting to this contract.

## 13. Observability

Add structured events without logging image bytes or personal data:

- `semantic_scene_candidate_generated`;
- `semantic_scene_identity_gate_completed`;
- `semantic_scene_candidate_rejected`;
- `semantic_scene_master_approved`;
- `semantic_scene_anchor_promoted`;
- `semantic_video_identity_gate_completed`;
- `semantic_video_scene_gate_completed`.

Include:

- post and run identifiers;
- correlation ID;
- model;
- contract version/hash;
- actor-reference fingerprint prefix;
- candidate or take index;
- candidate/master SHA-256 prefix;
- pass/fail status;
- blocking reason codes;
- latency.

Never log the full actor images, generated images, prompts containing personal data, or provider credentials.

Track operational metrics:

- candidate identity pass rate;
- candidates required per approved master;
- Veo identity pass rate by take index;
- scene continuity pass rate;
- human rejection rate after automated pass;
- retries per approved delivery;
- provider cost per approved delivery.

## 14. Implementation blocks

### Block A — Model contract and canonical-anchor invalidation

Primary files:

- `app/core/config.py`
- `app/adapters/llm_client.py`
- `app/features/shot_frames/wheelchair_scene_plate.py`
- `app/features/semantic_videos/visual_contract.py`
- `app/features/semantic_videos/queries.py`
- new Supabase migration

Deliverables:

- explicit stable Pro model configuration;
- updated alias behavior or explicit bypass;
- scene-plate generation contract hash;
- contract-aware canonical-anchor lookup;
- legacy anchors excluded from the new route;
- three independent bootstrap candidates.

Exit test:

- a legacy anchor cannot be selected;
- all bootstrap candidates use the original actor references;
- every generated candidate records `gemini-3-pro-image`;
- model unavailability fails closed before partial persistence or paid video work.

### Block B — Candidate identity evaluator

Primary files:

- new `app/features/shot_frames/identity_qa.py`
- `app/features/semantic_videos/handlers.py`
- `app/features/semantic_videos/schemas.py`

Deliverables:

- strict evaluator input and result schemas;
- server-computed pass decision;
- candidate gate persistence;
- generation flow that tolerates individual candidate rejection;
- approval endpoint enforcement.

Exit test:

- malformed evaluator output fails closed;
- a candidate cannot be approved without a current passed gate;
- candidate and actor hash mismatches are rejected;
- no Magnific adapter is called.

### Block C — Approval interface

Primary files:

- `templates/batches/detail/_semantic_video.html`
- `static/js/batches/semantic_video.js`
- Semantic UGC progress/response schemas as required

Deliverables:

- original references displayed beside candidates;
- gate status and differences displayed;
- explicit human identity attestation;
- regenerate path when no candidate passes;
- accessible labels, focus behavior, and error messaging.

Exit test:

- the operator can compare all identity evidence without leaving the approval card;
- only passed candidates can submit approval;
- the server still rejects a manually forged approval request.

### Block D — Dual final visual QA

Primary files:

- `app/features/shot_production/visual_qa.py`
- `app/features/shot_production/runner.py`
- `app/features/semantic_videos/service.py`
- relevant worker/progress schemas

Deliverables:

- separate actor identity and scene continuity reports;
- original actor references available to final QA;
- combined fail-closed decision;
- separate remediation reasons and retry routing.

Exit test:

- a video matching the generated plate but not the original actor fails;
- a video matching the actor but changing the room or wheelchair fails;
- both reports must pass before final assembly/approval.

### Block E — Regression suite and live verification

Primary tests:

- `tests/test_shot_frames.py`
- `tests/test_semantic_video_handlers.py`
- `tests/test_semantic_video_ui.py`
- `tests/test_semantic_visual_contract.py`
- `tests/test_semantic_video_queries.py`
- `tests/test_semantic_actor_scene_plate_anchor_migration_postgres.py`
- `tests/test_shot_production_visual_qa.py`
- `tests/test_shot_production_runner.py`

Deliverables:

- unit and contract tests;
- migration test;
- UI tests;
- paid-boundary regression tests;
- one controlled live end-to-end verification.

## 15. Focused test matrix

### Candidate generation

- correct ordered actor/location inputs;
- actor asset URI, MIME, bytes, and SHA mismatch;
- Pro model success;
- Pro model unavailable;
- one, two, or three candidate identity failures;
- malformed image response;
- reservation release after failure;
- no derivation from an unapproved candidate.

### Candidate identity gate

- exact match;
- visible age drift;
- face averaging;
- beautified/poreless face;
- changed hairline;
- malformed eyes or mouth;
- low confidence;
- malformed JSON;
- stale evaluator version;
- candidate hash mismatch;
- actor fingerprint mismatch.

### Approval

- passed automated gate plus human approval;
- missing gate;
- failed gate;
- stale revision;
- forged candidate index;
- changed actor source;
- changed candidate bytes;
- double approval;
- legacy anchor promotion attempt.

### Final video QA

- same generated plate face but wrong original actor;
- correct actor with room drift;
- correct actor with wheelchair disappearance;
- correct scene with facial drift during speech;
- mouth/teeth artifacts;
- camera movement at the tail;
- subtitles or glyph artifacts;
- all checks passing.

### Paid-boundary behavior

- no Veo request before master approval;
- no automatic paid resubmission after unknown provider state;
- only failed takes retried;
- previously successful contract-matching take reused;
- no Magnific request;
- exact requested duration preserved.

## 16. Validation commands

Run the smallest focused suites after each block:

```bash
pytest -q tests/test_shot_frames.py
pytest -q tests/test_semantic_video_queries.py
pytest -q tests/test_semantic_video_handlers.py
pytest -q tests/test_semantic_video_ui.py
pytest -q tests/test_semantic_visual_contract.py
pytest -q tests/test_shot_production_visual_qa.py
pytest -q tests/test_shot_production_runner.py
pytest -q tests/test_semantic_actor_scene_plate_anchor_migration_postgres.py
```

Then run the repository's complete test suite before live verification.

The live verification must use:

- one consented existing actor;
- both immutable actor references;
- one actor-free location;
- three Pro candidates;
- one explicitly approved scene plate;
- the normal exact-16-second Semantic UGC path;
- two independent eight-second Veo takes;
- the full dual visual QA;
- manual review of the original references, master plate, raw take contact sheet, and final delivery.

Record provider model IDs, hashes, gate reports, durations, and costs. Do not call the live run successful solely because the API requests completed.

## 17. Rollout

1. Deploy the schema migration and contract-aware anchor lookup.
2. Deploy Pro candidate generation and the pre-approval identity gate behind a Semantic UGC-specific feature flag.
3. Run the gate in observation mode on internal test actors only; do not promote observation-mode anchors to production.
4. Review false passes and false failures and adjust the evaluator prompt/schema, not the original actor description.
5. Enable enforced candidate gating for internal production.
6. Enable the dual final-video QA.
7. Perform one controlled paid live delivery.
8. Enable the route for all Semantic UGC batches.
9. Monitor pass rates, human disagreement, retries, cost, and latency.
10. Remove the feature flag after the enforced route is stable; retain the contract version.

There must be no mixed production state in which some new Semantic UGC runs silently reuse legacy unverified anchors.

## 18. Definition of done

Implementation is complete only when:

- Semantic UGC uses `gemini-3-pro-image` for scene plates;
- Magnific is absent from the Semantic UGC runtime path;
- all bootstrap candidates originate directly from the unchanged actor references;
- original actor references and the scene master are represented as separate authorities;
- every approvable candidate has a current passed identity gate;
- the operator sees both original actor references during approval;
- the approved master is immutable and contract-versioned;
- old anchors cannot enter the new route;
- all Veo takes are deterministic crops of the one approved master;
- final actor identity is checked against the original references;
- final scene continuity is checked against the approved plate;
- both final gates pass before delivery;
- focused and full automated tests pass;
- the controlled live run passes visual inspection and exact-duration validation;
- no unrelated legacy workflow is broken.

## 19. Explicit non-goals

This implementation does not:

- retrain or tune Magnific LoRAs;
- integrate Kling or Seedance;
- promise mathematical zero-drift from a generative model;
- introduce a biometric face-embedding dependency;
- redesign the general Character Consistency feature;
- replace the original actor references with a generated portrait;
- create new generated shot images for every take;
- use written phenotype descriptions as identity conditioning.

A biometric embedding gate can be evaluated later as a separate privacy, consent, threshold-calibration, and dependency decision. The existing-tools solution deliberately uses immutable references, strict multimodal comparison, human approval, and fail-closed contracts.
