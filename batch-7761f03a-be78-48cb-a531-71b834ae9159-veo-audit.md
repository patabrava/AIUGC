# VEO Audit for Batch `7761f03a-be78-48cb-a531-71b834ae9159`

This document captures the exact prompt payloads recorded in `public.video_prompt_audit` for the batch that completed successfully.

## Summary

- approved post: `fa9cf0f9-cb52-40a8-a47e-4f4e97871e44`
- removed post: `21f23f56-62c8-4dc9-a562-7c8ac8face5c`
- route: efficient `32s`
- seed: `1930144744`

## Exact payloads

### Base request

- operation id: `models/veo-3.1-generate-preview/operations/d16w65o1bxlc`
- requested seconds: `32`
- prompt length: `1712`

```text
Character:
38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.

Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Scene:
A modern, tidy bedroom with blush-pink walls and minimal decor. Bright soft vanity light and natural daylight from camera-right create an even, flattering indoor look. The wheelchair is partially visible in the frame.

Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.

Dialogue:
"Niemand redet darüber, aber deine Rollstuhl-App steht im Spannungsfeld: Barrierefreiheit oder Cybersicherheit?"

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

### Extension hop 1

- operation id: `models/veo-3.1-generate-preview/operations/6u4evzow7ejg`
- requested seconds: `8`
- prompt length: `828`

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Ab Juni 2025 fordert das BFSG digitale Zugänglichkeit von Unternehmen, doch es ist komplex."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension hop 2

- operation id: `models/veo-3.1-generate-preview/operations/g13a2qd60qbo`
- requested seconds: `8`
- prompt length: `848`

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Digitale Gesundheitsanwendungen müssen schon jetzt strenge Regeln der BITV 2. 0 und des Datenschutzes erfüllen."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension hop 3

- operation id: `models/veo-3.1-generate-preview/operations/sc38at7o882k`
- requested seconds: `8`
- prompt length: `1050`

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Deine App, die etwa Joystick-Sensitivität anpasst, hat vielleicht mehr Zugriff, als sicher ist. Ein krasser Zielkonflikt!"

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

## Comparison to simulation

The batch-specific simulation is structurally correct.

The only literal mismatch I found is the `BITV 2. 0` spacing in the live hop 2 payload. The simulation currently shows `BITV 2.0`.

That means the prompt shape is aligned, but the live text is not a perfect character-for-character match to the simulation.
