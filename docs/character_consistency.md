# VEO 3.1 Character Consistency Research

**Date:** 2026-04-01
**Status:** Research complete, implementation pending

---

## Current State Analysis

### What We Do Today (`app/features/posts/prompt_builder.py`)

- **Text-only prompts** — Character described in ~40 words inside `OPTIMIZED_PROMPT_TEMPLATE`
- **No image conditioning** — `veo_client.py` has a `reference_images` parameter (line 53) that logs a warning and is discarded (lines 94-98)
- **No seed parameter** — Each generation uses random noise independently
- **No drift-targeted negatives** — `VEO_NEGATIVE_PROMPT` targets visual artifacts (watermarks, blur) but nothing about character mutations
- **Deprecated model** — Currently on `veo-3.1-generate-preview`, scheduled for removal April 2, 2026. Must migrate to `veo-3.1-generate-001`

### Current Character Description (too short)

```
38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights,
hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.
```

Best practice is **100-150 words** with forensic-level detail covering: face shape, skin tone, eye shape/color, eyebrow shape, nose, lip shape/color, hair (cut, texture, color, parting), clothing (specific items, colors, fabrics), accessories (or explicit "no jewelry, no glasses").

---

## Three Mechanisms VEO 3.1 Offers

### 1. Reference Images (Character Locking)

Up to **3 asset reference images** per request. VEO preserves the subject's appearance.

**Gemini API payload format:**

```json
{
  "instances": [{
    "prompt": "...",
    "referenceImages": [
      {
        "image": {
          "inlineData": {
            "mimeType": "image/png",
            "data": "<base64_encoded_image>"
          }
        },
        "referenceType": "asset"
      }
    ]
  }],
  "parameters": {
    "aspectRatio": "16:9",
    "durationSeconds": 8
  }
}
```

**Constraints:**

| Constraint | Detail |
|---|---|
| Aspect ratio | **16:9 only**. 9:16 returns `INVALID_ARGUMENT`. Google says 9:16 support "forthcoming" — no date. |
| Duration | Must be **8 seconds** when using asset images. 4s and 6s rejected. |
| Max images | Up to **3 asset images** per request. |
| Format | `inlineData` with `mimeType` + `data` (base64). Not `bytesBase64Encoded` — that's Vertex AI format. |
| Cannot combine | Cannot use `referenceImages` AND `image` (first-frame) in the same request. |
| Model | Supported on `veo-3.1-generate-001`, `veo-3.1-fast-generate-001`. NOT on VEO 3.0. |

**Blocker for us:** Our UGC videos are **9:16**. Reference images won't work until Google ships 9:16 support. The plumbing is already stubbed in `veo_client.py`.

### 2. Image-to-Video (First Frame Anchoring)

Provide a starting image; VEO animates from it. Character appearance in frame 1 anchors the entire clip. **Works with 9:16.**

```json
{
  "instances": [{
    "prompt": "...",
    "image": {
      "inlineData": {
        "mimeType": "image/png",
        "data": "<base64_of_character_portrait>"
      }
    }
  }],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 8
  }
}
```

Also supports `lastFrame` for interpolation (16:9 only currently).

**This is our best near-term path for character consistency.**

### 3. Seed Parameter

A `seed` (uint32, range 0–4,294,967,295) in `parameters`. Same seed + same prompt = more consistent output. Not a character lock on its own, but reduces variance.

```json
"parameters": {
  "seed": 1234567890,
  ...
}
```

**Documentation status (verified 2026-04-01):**
- **Vertex AI API reference** (`cloud.google.com/vertex-ai`) explicitly lists `seed` as a supported `uint32` parameter for VEO 3.1.
- **Gemini API docs** (`ai.google.dev`) — the API surface we use — does not list it in the VEO 3.1 config table, but states: "the `seed` parameter is also available for Veo 3 models. It doesn't guarantee determinism, but slightly improves it."
- Impact is modest. Not a substitute for reference images or first-frame anchoring.

---

## Prompt-Only Techniques (No Code Changes)

| Technique | Impact | Notes |
|---|---|---|
| **Verbatim character bible** (100-150 words, identical across all clips) | ~40% consistency improvement | Our template does this but description is too short |
| **Continuity table** — lock lens, color grade, lighting, wardrobe | Reduces model degrees of freedom | We already lock scene/cinematography, which is good |
| **Drift-targeted negative prompts** | Prevents common mutations | Add: `"no hat, no glasses, no jewelry unless specified, no hair color change, no wardrobe change, no age change, different person"` |
| **Bridge shots** between scene changes | Smoother visual transitions | Relevant for 16/32-second chained videos |

### Expanded Character Bible (Example)

```
A 38-year-old German woman with an oval face, warm light-medium skin tone with natural
skin texture and subtle laugh lines at the eyes. Shoulder-length light brown hair with
subtle blonde highlights, soft natural waves, parted slightly off-center to the left.
Hazel eyes — green-brown with golden flecks — framed by natural untrimmed brows in a
soft arch. Straight nose with a slightly rounded tip. Full lips with a natural pink tone,
no lipstick. No visible jewelry, no glasses, no hat, no nail polish. She wears a casual
cream-colored oversized crewneck sweater with soft ribbed texture. No visible logos or
branding on clothing. Her expression is warm, approachable, and naturally animated —
she smiles easily and uses small hand gestures when speaking.
```

---

## Advanced Pipeline: Imagen + VEO (Google Cloud Community Approach)

A 6-stage pipeline documented by Google Cloud community for "remarkable facial consistency":

1. **Structured Feature Extraction** — Gemini 2.5 Pro decomposes a reference photo into a structured `FacialCompositeProfile` (Pydantic schema with enums for face shape, eye features, etc.)
2. **Semantic Bridging** — Translate the JSON profile back to natural language
3. **Image Synthesis** — Generate character images with Imagen 3.0 using `edit_image` with `SUBJECT_TYPE_PERSON`, producing 4 candidates
4. **Automated Curation** — Gemini 2.5 Pro selects the best-matching candidate
5. **Outpainting** — Expand 1:1 portrait to target aspect ratio via Imagen 3.0
6. **Video Synthesis** — Feed the enhanced frame as VEO's starting image

GitHub: `vertex-ai-creative-studio/experiments/veo3-character-consistency`

---

## Video Extension Chaining for Multi-Clip Consistency

Our codebase already uses `submit_video_extension()` for 16/32-second videos. This is inherently good for consistency because each extension inherits the visual context of the previous clip. The character appearance from clip 1 carries forward through all extensions.

**Current chain structure (from `video_profiles.py`):**

- **8 seconds:** Single VEO call
- **16 seconds:** Base (4s) + 2 extensions (7s each)
- **32 seconds:** Base (4s) + 4 extensions (7s each)

**Limitation:** Extensions locked to 720p resolution.

---

## Provider Comparison

| Feature | VEO 3.1 | Sora 2 | Kling 3.0 |
|---|---|---|---|
| Reference images | Up to 3 (16:9 only) | No | Image-to-video |
| Seed parameter | Yes | No | No |
| Image-to-video (first frame) | Yes (all ratios) | Yes | Yes |
| First + last frame | Yes (16:9 only) | No | No |
| Video extension | Yes | Yes | Yes |
| Native audio/lip-sync | Yes | No | No |
| Consistency rating | Best | Fair | Good |

---

## Complete VEO 3.1 API Parameters Reference

### `instances[0]` fields

| Field | Type | Notes |
|---|---|---|
| `prompt` | string | Required. Text description. |
| `image` | object | First frame. Mutually exclusive with `referenceImages`. |
| `lastFrame` | object | Last frame. Requires `image`. 16:9 only. |
| `video` | object | Input video for extension. |
| `referenceImages` | array (max 3) | Character/subject references. 16:9 + 8s only. |

### `parameters` fields

| Field | Type | Notes |
|---|---|---|
| `aspectRatio` | string | `"16:9"` or `"9:16"`, default `"16:9"` |
| `resolution` | string | `"720p"`, `"1080p"`, `"4k"` |
| `durationSeconds` | integer | 4, 6, or 8 |
| `negativePrompt` | string | Content to avoid |
| `sampleCount` | integer | 1-4 videos per request |
| `seed` | uint32 | Deterministic generation seed |
| `generateAudio` | boolean | Enable native audio |
| `personGeneration` | string | `"allow_adult"`, `"allow_all"`, `"dont_allow"` |
| `enhancePrompt` | boolean | Auto-improve prompt |
| `resizeMode` | string | `"pad"` or `"crop"` (image-to-video only) |
| `compressionQuality` | string | `"optimized"` or `"lossless"` |

---

## Action Plan

### Immediate (prompt-only)

- [ ] Expand character description in `OPTIMIZED_PROMPT_TEMPLATE` to 100-150 words
- [ ] Add character-drift negatives to `VEO_NEGATIVE_PROMPT`
- [ ] Migrate model ID from `veo-3.1-generate-preview` to `veo-3.1-generate-001`

### Short-term (code changes)

- [ ] Add `seed` parameter to `submit_video_generation()` — use consistent seed per batch
- [ ] Implement image-to-video (first frame) pipeline:
  1. Generate canonical character portrait via Imagen 3 during batch setup
  2. Store portrait in Supabase storage
  3. Pass as `image.inlineData` in every VEO request for that batch
- [ ] Wire `negativePrompt` with drift-targeted additions

### When Google ships 9:16 reference images

- [ ] Wire up existing `reference_images` stub in `veo_client.py`
- [ ] Generate 2-3 canonical character portraits (front, 3/4, profile) per batch
- [ ] Pass as `referenceType: "asset"` with every generation request

---

## Sources

- [Google Cloud: Generate VEO videos from reference images](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/use-reference-images-to-guide-video-generation)
- [Google AI Developers: Generate videos with VEO 3.1](https://ai.google.dev/gemini-api/docs/video)
- [Google Cloud: VEO video generation API reference](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)
- [Google Developers Blog: Introducing VEO 3.1](https://developers.googleblog.com/introducing-veo-3-1-and-new-creative-capabilities-in-the-gemini-api/)
- [Google Cloud Community: VEO 3 forensic character consistency approach](https://medium.com/google-cloud/veo-3-character-consistency-a-multi-modal-forensically-inspired-approach-972e4c1ceae5)
- [Google Cloud Blog: Ultimate prompting guide for VEO 3.1](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1)
- [BetterLink: Complete guide to VEO 3 character consistency](https://eastondev.com/blog/en/posts/ai/20251207-veo3-character-consistency-guide/)
- [Google AI Forum: Reference images API discussion](https://discuss.ai.google.dev/t/veo-3-1-reference-images-docs-say-available-api-says-not-supported/111853)
- [Skywork: VEO 3.1 multi-prompt storytelling best practices](https://skywork.ai/blog/multi-prompt-multi-shot-consistency-veo-3-1-best-practices/)
