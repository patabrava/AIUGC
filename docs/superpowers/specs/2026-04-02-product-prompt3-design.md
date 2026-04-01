# Product Prompt 3 Design

Date: 2026-04-02
Scope: Add a product-only topic-generation lane for static product batches using a dedicated `Prompt 3`
Goal: Generate product scripts from the static LippeLift knowledge base without using deep research or JSON responses from Gemini
Locality Budget: `{files: 5-6, LOC/file: <=300 target and <=500 hard, deps: 0}`

## Problem Statement

The current topic system already separates research-backed `value` generation from on-the-spot `lifestyle` generation. Product batches need the same separation: a dedicated generation lane that uses the static company knowledge base, follows product-only rules, and keeps the user flow unchanged. The operator should still choose counts up front, then review final scripts in the next stage.

The key constraint is that Gemini responses must stay plain text. Structured JSON output has been a recurring failure mode, so `Prompt 3` must use a deterministic text format that the app parses locally.

## Scope

### In Scope

- Add a dedicated product-generation prompt type: `Prompt 3`
- Route `post_type=product` through `Prompt 3` only
- Read product facts from the static knowledge base file at runtime or from a cached normalized representation derived from that file
- Exclude inactive products and non-communicated products from generation
- Allow product batches to mix across active products automatically
- Allow repeated coverage of active products when the requested product count exceeds distinct product count
- Keep the existing batch create and review flow intact
- Preserve the existing review-stage handoff where the final script appears only after generation

### Out of Scope

- Changing `value` generation behavior
- Changing `lifestyle` generation behavior
- Adding a user-uploaded product knowledge base
- Adding Gemini JSON schema output for product generation
- Adding external research or web lookup to product generation
- Reworking the batch state machine

## Recommended Design

`Prompt 3` should be a product-only, text-first generator with a local parser.

The model receives:
- the active product facts extracted from the static LippeLift knowledge base
- the target count for the batch
- the requested hook style and platform tone
- the existing framework set, which remains compatible with the current review flow

The model returns plain text using a predictable line-based format. The app parses that text into a normalized internal payload, validates it, and then persists it through the existing post creation flow.

This gives three independent lanes:
- `Prompt 1`: research-backed value posts
- `Prompt 2`: lifestyle posts
- `Prompt 3`: product posts

## Product Source Model

The knowledge base is static and deployment-specific. That means the system should treat the file as a built-in source of truth, not as a user-managed asset.

The normalized product dataset should contain at least:
- product name
- active or inactive status
- short product summary
- usable product facts
- exclusions or “do not communicate” markers
- optional support facts such as service, warranty, and company credibility

The first pass should normalize this dataset from the text file and cache the result. The cache can be rebuilt when the file changes, but generation itself should use the normalized representation instead of reparsing prose repeatedly.

## Prompt 3 Contract

`Prompt 3` should produce a plain text response with a stable block structure. The exact wording may evolve, but the parser should expect fields in a predictable order.

Recommended output fields:
- `Produkt`
- `Angle`
- `Script`
- `CTA`
- `Fakten`

Recommended semantics:
- one response produces one product post seed
- the script stays single-line and final-stage ready
- the content is grounded in active product facts only
- the generator may use supporting company/service facts when they strengthen the product story
- inactive or excluded products are never eligible

The parser should reject:
- missing required fields
- empty scripts
- multi-line scripts where a single spoken line is expected
- references to inactive products
- responses that do not map to a recognized active product

## Generation Behavior

For a product batch, the generator should:
- select from the active product pool in the static knowledge base
- distribute requests across active products automatically
- allow repeats only after all active products have been covered once
- preserve the existing hook style conventions used by the platform
- keep the output appropriate for TikTok and Instagram-style short-form content

The product lane should remain compatible with the current framework system. The first implementation should reuse the existing framework set rather than introducing a separate framework taxonomy. The important difference is the source and prompt behavior, not the post-processing surface.

## Data Flow

1. The user creates a batch with `product` counts.
2. Batch creation persists the requested counts as it already does.
3. The discovery worker sees `product` in the batch mix.
4. The worker loads the normalized static product dataset.
5. The worker invokes `Prompt 3` once per requested product post.
6. Gemini returns plain text.
7. The local parser extracts product name, angle, script, CTA, and supporting facts.
8. Validation rejects malformed or out-of-policy responses.
9. Valid product posts go through the existing post creation and review-stage flow.

## Validation Rules

Validation should happen locally, not inside Gemini schema enforcement.

Required checks:
- active product only
- no excluded products
- non-empty script
- script length within existing post constraints
- plain-text parse success
- line-level integrity for the required fields

Recovery behavior:
- retry on parse failure
- retry on missing required fields
- fail the batch lane with a structured error if the generator cannot produce a valid product post after bounded retries

## UI Impact

The batch creation UI already supports `product` counts in the backend contract, so the default expectation is that no major UI redesign is required. If the form currently hides `product`, the minimal fix is to expose a third count field beside `value` and `lifestyle`.

The review stage should remain unchanged:
- the user selects counts first
- the system generates posts later
- the final script appears only in the normal post review stage

## Error Handling

Use the existing app error model and keep failures local to the product lane.

Key failure classes:
- cache load failure for the static knowledge base
- no eligible active products
- Gemini text parse failure
- inactive product reference
- validation failure on the generated script

Error responses should be explicit about which product lane failed and why, without leaking provider internals into the user-facing surface.

## Files

Likely files for implementation:
- [app/features/topics/prompt3_runtime.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompt3_runtime.py)
- [app/features/topics/product_knowledge.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/product_knowledge.py)
- [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py)
- [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py) if the batch create form needs a visible `product` count field
- [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py) if we need a small internal contract for parsed `Prompt 3` output

## Testing

Add regression coverage for:
- static product knowledge normalization
- active vs inactive product filtering
- prompt text parsing for valid and invalid Gemini responses
- product batch routing from discovery into `Prompt 3`
- repeated coverage behavior when requested count exceeds distinct active products
- batch create behavior with `product` counts

At minimum, the test suite should verify that product generation does not touch the deep-research path and that malformed text responses fail locally rather than propagating into persistence.

## Acceptance Criteria

- Product batches can request `product` counts independently of `value` and `lifestyle`
- Product scripts are generated from the static LippeLift knowledge base only
- Inactive products are never eligible
- Product batches may repeat active products after first coverage
- Gemini output remains plain text, not JSON
- The app parses and validates product responses locally
- The user still sees scripts in the normal review stage
- Existing value and lifestyle behavior remains unchanged

