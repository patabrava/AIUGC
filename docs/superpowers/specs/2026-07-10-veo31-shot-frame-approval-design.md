# Veo 3.1 Still-First Character Consistency Design

Date: 2026-07-10
Status: Approved for the first live candidate pass

## Goal

Replace the Magnific-dependent scene-reference step with a deterministic still-first workflow: two immutable actor identity references plus one actor-free location reference produce an approved 9:16 start frame, and only that approved frame may enter Veo 3.1 image-to-video generation.

## Production Flow

1. Load exactly two actor identity images: front and three-quarter.
2. Load exactly one actor-free canonical location image.
3. Convert the shot brief into one Raw Camera Casting Realism prompt using the user-supplied prompt-writer system instruction verbatim.
4. Preserve the approved long character description verbatim in both the prompt-writer brief and the final composition prompt.
5. Send that prompt and the three ordered references to Gemini 3.1 Flash Image (`gemini-3.1-flash-image`, Nano Banana 2).
6. Generate reviewable 9:16 still candidates and preserve the inputs, prompt, provider model, and output bytes.
7. Stop. No Veo request is allowed until a human explicitly approves one candidate.
8. After approval, use the selected still as the only start frame for an 8-second Veo 3.1 image-to-video request.
9. Inspect the rendered video for actor identity, wardrobe, scene, speech, and visible temporal drift before stitching or publishing.

## Reference Roles

- Image 1: primary actor identity and cream knit wardrobe anchor.
- Image 2: actor identity from a three-quarter angle. Its blazer is not a wardrobe instruction.
- Image 3: actor-free location geometry, furniture, curtain, plant, mug, palette, and daylight anchor.

The image-generation prompt must state these roles explicitly. It must not average the references into a new face, copy the blazer from Image 2, add another person, or redesign the room.

## Prompt Architecture

The attached text is a prompt-writer system instruction and explicitly says not to generate an image. It therefore runs in a text-only first stage. Its single-paragraph output becomes the actual image prompt for the second stage. The image-generation request receives the two actor references, the location reference, and the finished prompt.

## Approval Boundary

Candidate generation and video generation are separate operations. Candidate creation may never call the Veo adapter. The selected candidate id or file must be supplied explicitly to the later video step. A missing approval fails closed.

## First Live Target

- Batch: `c066893a-cafe-4e53-9d37-cc91f62985cf`
- Post: `b2fc6a7b-d70d-49e0-a0e3-cb05a1b9a3ce`
- Actor: `AYRA Actor Long Character`
- Scene: `home_living_room_advice_a`
- Script: `Als Rollstuhlfahrer kennst du das: Normgerechte Rampen sind oft ein versteckter Marathon für deine Kraft.`
- Output: vertical 9:16, ordinary AIUGC talking-head start frame, cream knit sweater, no wheelchair invention unless visible in supplied references.

## Validation

- Unit-test ordered multipart Gemini payloads.
- Unit-test the exact two-actor-plus-one-location contract.
- Unit-test that candidate generation never touches Veo.
- Generate live candidates through the production adapter.
- Inspect every raster output before presenting it.
- Pause for human approval.
