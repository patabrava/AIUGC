# Canonical Actor Lock Design

## Goal

Character consistency is the highest-priority production invariant. Manual Semantic UGC and automated Semantic UGC must use the same approved actor pixels, and the application must reject a paid Veo approval whenever the selected master is not the canonical actor reference.

## Root Cause

Both semantic modes snapshot the same active `actor_identity_id` and two ordered reference images. The current candidate endpoint nevertheless sends those references, the location image, and script-derived context to Gemini at `temperature=0.7` for every post. The operator then approves one newly synthesized candidate as that run's master. Downstream visual QA compares takes to the run-specific master, so it protects consistency inside one run while allowing different runs to establish slightly different faces.

The semantic pair happened to converge on one face and the manual pair on another. Cross-run identity was never a persisted invariant.

## Approved Product Decision

The canonical front actor reference is the sole video master source for both `semantic_ugc` and `manual_semantic_ugc`. Scene variety, generated master candidates, and script-conditioned start-frame composition are subordinate to exact actor identity.

The system may crop or scale the canonical master later when building the shot deck, because those operations preserve the source identity. It may not synthesize a replacement face before Veo submission.

## Data Contract

Candidate preparation continues to persist the existing three-entry database shape so the deployed transaction functions remain compatible. All three internal entries point to the same canonical front reference and have identical URI, SHA-256, byte length, MIME type, and provider marker `canonical-actor-reference/v1`.

The batch view collapses identical hashes so the operator sees one canonical master, not three duplicate cards.

The persisted reference snapshot remains immutable and contains:

- the batch `actor_identity_id`;
- the ordered `actor_front` and `actor_three_quarter` source snapshots;
- the byte length and SHA-256 verified when those objects are downloaded;
- the actor-free location reference for audit compatibility.

## Enforcement Boundaries

Before master approval, the application verifies that the selected candidate matches the persisted `actor_front` URI, SHA-256, byte length, and MIME type.

Before paid plan approval, the application downloads both the current canonical actor reference and the approved master, verifies their bytes and hashes, and rejects the approval if they differ. This check runs before any provider submission or quota reservation.

The existing worker continues to verify each generated take against the approved master. Segment crops are derived from the same master, preserving the existing segmented continuity path.

## User Interface

The batch page labels the action `Load canonical actor master` and explains that the actor is locked to the approved reference. The master area displays one deduplicated candidate. Approval remains explicit so the paid workflow keeps its current human review boundary.

## Error Handling

Identity contract failures are fail-closed `ValidationError` or `StateTransitionError` responses. The error identifies the mismatched field and confirms that no paid provider work was approved.

No automatic image regeneration or paid retry is allowed as a response to an identity mismatch.

## Verification

The change is accepted only when:

1. a regression test proves the old endpoint calls the generative candidate path and fails the new canonical-master expectation;
2. the fixed endpoint performs no Gemini image call and no candidate upload;
3. Manual Semantic and automated Semantic candidate preparation produce the same master SHA for the same actor snapshot;
4. tampering with the master URI, hash, or bytes blocks paid approval;
5. existing semantic handler, plan, worker, and migration-contract tests pass;
6. the production page displays the canonical actor lock and production health remains green;
7. a free production candidate preparation proves the persisted master SHA equals the approved front-reference SHA without starting a paid Veo generation.

## Scope

This change affects Semantic UGC and Manual Semantic UGC. Legacy modes retain their existing contracts. Explicitly replacing the active actor in Settings remains a deliberate administrative action; batches snapshot the selected actor at creation as they do today.
