# Prompt Consistency Notes

These notes capture the working idea from the recent Veo tests: the prompt should be split into a stable core plus small hop-specific deltas.

## What seems to help

- Keep the prompt slim.
- Keep the character identity stable across all hops.
- Keep scene invariants stable across all hops.
- Put only the new action or motion in each extension hop.
- Keep audio guidance concise and centralized.
- Avoid repeating the same background, wardrobe, and lighting details on every hop.

## What likely causes drift

- Overly detailed character descriptions in every hop.
- Repeating the full scene description in every extension.
- Repeating audio, negatives, and style notes in multiple places.
- Chaining too many extensions without reducing repeated context.

## Suggested structure

### 1. Canonical identity block

Use one stable block for:

- character appearance
- voice/personality
- wardrobe
- environment anchors that must not change

### 2. Per-hop motion block

Use a short block that only changes:

- what the character does now
- what the camera should follow now
- any brief spoken line for this hop

### 3. Audio block

Keep one concise audio block with:

- voice style
- room tone
- end-of-clip behavior

### 4. Negative prompt block

Keep exclusions minimal and stable.

## Practical rule

- Base hop: full canonical identity + scene + first action.
- Extension hops: reuse the identity and scene anchors, but only add the new motion beat.
- Final hop: focus on completion behavior and mouth-rest / clip-ending instructions.

## Current conclusion

The recent successful result was probably not caused by image-to-video.

It was more likely caused by:

- a slimmer prompt
- less repeated instruction clutter
- cleaner extension chaining

So the right move is not to remove all detail.
The right move is to preserve stable identity and trim repeated context from later hops.
