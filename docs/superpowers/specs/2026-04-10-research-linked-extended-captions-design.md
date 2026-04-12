# Research-Linked Extended Captions Design

## Goal

Add an optional long-form publish caption path for research-rich posts while preserving the current short caption system as the default and fallback.

## Scope Budget

{files: 3, LOC/file: <=320 target and <=500 hard, deps: 0}

## Research Notes

The external pattern is consistent across Instagram creator guidance and creator discussions:

- Strong hook first, then short scannable blocks.
- A small number of relevant hashtags is better than a long tag dump.
- Educational captions work best when they read like a mini-resource, not a blog post.
- Research references should be compact and easy to scan.

The practical implication is simple: the long-form path should feel like a useful field note, not a wall of text. It should still be written for quick scanning on mobile.

## Current System

The live publish-caption flow already works end to end:

1. `build_seed_payload(...)` stores research data, including `strict_seed`, `description`, and an optional `source` object.
2. `attach_caption_bundle(...)` creates a caption bundle from `strict_seed.facts` and writes the selected caption into both `caption` and `description`.
3. `create_post_for_batch(...)` resolves the final publish caption from `seed_data` via `resolve_selected_caption(...)`.

That means the safest change is inside `app/features/topics/captions.py`. The downstream publish flow does not need a schema rewrite or new tables.

## Design

### 1. Two Caption Profiles, One Entry Point

Keep the public caption bundle flow unchanged, but add a deterministic profile decision inside the generator:

- `standard` profile: current short caption behavior stays exactly as it is today.
- `extended` profile: a long-form research caption with a hook, short summary, evidence lines, source labels, CTA, and hashtags.

Both profiles still return the same bundle shape and still feed the same `selected_body` into `publish_caption`.

### 2. Research-Depth Gate

The extended profile should only activate when the payload is genuinely research-rich.

Use a simple rule:

- at least `1` source URL
- at least `5` usable facts
- no missing source metadata needed for the source block

If any of those checks fail, the generator must stay on the `standard` profile.

The gate should be pure and deterministic. It should inspect the already-built seed payload, not make new network calls and not retry the LLM just to discover whether the caption should be long or short.

### 3. Caption Shape For The Extended Profile

The extended caption should be structured for mobile scanning:

- `Hook`: one strong line that opens the caption
- `Kurz gesagt:` 1-2 short sentences that state the main takeaway quickly
- `Evidence`: 2-4 compact bullets or short paragraphs with concrete research facts
- `Sources`: 2-3 short source labels or source titles, shown plainly in the caption text
- `CTA`: one direct action, such as save, share, or comment
- `Hashtags`: `3-5` relevant tags, not a long spam block

Target length:

- `standard`: keep the current 80-400 character contract
- `extended`: target roughly `450-900` characters, with a hard upper bound of `1,000`

The caption should feel materially more informative than the current short format, but still be readable on a phone without becoming a blog post.

### 4. Source Link Handling

The extended caption must include readable source labels only when they are already present in the seed payload.

Source resolution order:

1. `seed_payload["source"]["title"]`
2. any normalized `source_urls` already attached to the payload path, transformed into readable labels for public copy
3. de-duplicated source titles/labels extracted from the research payload

Rules:

- never invent or fabricate source labels
- never include raw Vertex redirect URLs in the public caption body
- never include more than `3` source labels in the caption body
- if source labels are missing, fall back to the standard caption path
- keep the full raw URLs in internal metadata and the source drawer, not in the public caption

The source block should use compact labels like `Quelle: PBefG, hvv, VDV` or similar plain-language references that remain copyable and readable in Instagram.

### 5. Validation And Fallback

Validation should become profile-aware:

- `standard` profile keeps the current validator and current fallback behavior
- `extended` profile gets a stricter structure check:
  - hook present
  - summary block present
  - source-label block present
  - `3-5` hashtags
  - no obvious script echo
  - no metadata leakage
  - no mixed-language spillover

If the extended profile fails validation, the generator must not retry into more LLM calls just to force it through. It should fall back to the standard profile and continue with the existing deterministic short caption.

This is the key safety rule: the new path can improve captions, but it cannot block the existing pipeline.

### 6. Bundle Contract

Keep the existing bundle keys intact so downstream consumers do not change:

```python
{
    "variants": [...],
    "selected_key": "curiosity",
    "selected_body": "...",
    "selection_reason": "hash_variant",
}
```

Add only optional metadata inside the bundle payload, such as:

- `caption_profile`: `standard` or `extended`
- `caption_depth_reason`: short string explaining why the extended path was or was not used
- `source_urls`: normalized source list used for the caption

Nothing downstream should depend on those new metadata fields.

## Files

### `app/features/topics/captions.py`

Add the profile gate, the extended prompt shape, the extended validator, and the fallback routing.

### `app/features/topics/prompt_data/captions_prompt.txt`

Update the prompt template so it can render both the short and extended instructions from the same entry point.

### `tests/test_caption_generation.py`

Add regression tests for:

- extended profile selected when the payload has `1+` URL and `5+` facts
- standard profile retained when the payload is too thin
- fallback to standard when extended validation fails
- source labels included in the extended output

## Test Strategy

1. Validate that the current short caption behavior still passes unchanged.
2. Verify the depth gate is deterministic and only turns on with deep research payloads.
3. Verify an extended caption contains source labels, a short summary block, and a compact CTA/hashtag block.
4. Verify malformed or missing source data drops back to the short path without failing the batch.

## Non-Goals

- No new database tables
- No change to burned-in video captions
- No change to the caption worker
- No new LLM provider
- No search or browsing at generation time

## Risks

- If the extended profile becomes too long, it may feel more like an article than a caption. Keep the source block compact.
- If the gate is too permissive, thin research will start generating brittle long-form captions and trigger more fallbacks.
- If the gate is too strict, the feature will rarely activate and the system will look unchanged.

The safest default is to bias toward the standard caption unless the research payload is clearly rich enough to justify the extension.
