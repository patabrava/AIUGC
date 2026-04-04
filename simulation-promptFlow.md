# VEO 3.1 Prompt Flow Simulation

This file reflects the current production code path after the lean-extension update.

- route: efficient `32s`
- base request: `8s`
- extension hops: `3`
- extension API duration: `8s` each
- internal packing target: base `8s`, hop budget `~7s`
- seed: generated once on submit and reused across all hops

## Script

`Als Rollstuhlnutzer kennst du das: Das gesuchte Produkt steht unerreichbar hoch im Supermarktregal. Spezielle Hubrollstühle mit stufenloser Gasdruckfederung und ergonomische Greifzangen erleichtern den Zugriff enorm. Für enge Gänge sind ankoppelbare Rollstuhl Einkaufswagen die beste Wahl. Seit 2025 verbessert das Barrierefreiheitsstärkungsgesetz Terminals, aber Übergangsfristen bremsen die Inklusion noch. Mit diesen Hacks meisterst du vertikale Barrieren beim Einkaufen.`

## Packed Segments

1. `0.0s-8.0s` approx
   `Als Rollstuhlnutzer kennst du das: Das gesuchte Produkt steht unerreichbar hoch im Supermarktregal. Spezielle Hubrollstühle mit stufenloser Gasdruckfederung und ergonomische Greifzangen erleichtern den Zugriff enorm.`
2. `8.0s-15.0s` approx
   `Für enge Gänge sind ankoppelbare Rollstuhl Einkaufswagen die beste Wahl.`
3. `15.0s-22.0s` approx
   `Seit 2025 verbessert das Barrierefreiheitsstärkungsgesetz Terminals, aber Übergangsfristen bremsen die Inklusion noch.`
4. `22.0s-29.0s` approx
   `Mit diesen Hacks meisterst du vertikale Barrieren beim Einkaufen.`

## Shared `negativePrompt`

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```

## Base Request

### Wire payload

```json
{
  "instances": [
    {
      "prompt": "<base prompt below>"
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 8,
    "negativePrompt": "<shared negativePrompt>",
    "seed": "<random uint32>"
  }
}
```

### Prompt text

```text
Character:
38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.

Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Scene:
A tidy modern bedroom with soft blush-pink walls, a white bed with warm beige bedding, and one warm bedside lamp on a small nightstand at camera-left. Bright soft vanity light and natural daylight from camera-right create an even, flattering indoor look. The wheelchair is partially visible in the frame. The room is uncluttered and visually stable across shots.

Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.

Dialogue:
"Als Rollstuhlnutzer kennst du das: Das gesuchte Produkt steht unerreichbar hoch im Supermarktregal. Spezielle Hubrollstühle mit stufenloser Gasdruckfederung und ergonomische Greifzangen erleichtern den Zugriff enorm."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

## Extension Hop 1

### Wire payload

```json
{
  "instances": [
    {
      "prompt": "<hop 1 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 8,
    "negativePrompt": "<shared negativePrompt>",
    "seed": "<same uint32 as base>"
  }
}
```

### Prompt text

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, warm light-medium skin tone, friendly oval face, natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Für enge Gänge sind ankoppelbare Rollstuhl Einkaufswagen die beste Wahl."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

## Extension Hop 2

### Prompt text

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, warm light-medium skin tone, friendly oval face, natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Seit 2025 verbessert das Barrierefreiheitsstärkungsgesetz Terminals, aber Übergangsfristen bremsen die Inklusion noch."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

## Extension Hop 3

### Prompt text

```text
Character:
Same person as the previous segment: 38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, warm light-medium skin tone, friendly oval face, natural expression.

Style:
Maintain the same realistic smartphone selfie video look from the previous segment.

Continuity:
Maintain the same bedroom, lighting, framing, camera position, and wardrobe from the previous segment.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
"Mit diesen Hacks meisterst du vertikale Barrieren beim Einkaufen."

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

## Current Production Shape

- base request remains full and descriptive
- extension hops are intentionally minimal
- extension hops no longer resend full scene/cinematography/audio boilerplate
- Veo negatives are sent via REST `negativePrompt`, not appended inside the prompt body
- extension audit rows now store `requested_seconds = 8`, matching the real Veo API call

## What To Test Next

1. whether face and room continuity improve after hop 1
2. whether hop 1 still shows language drift before the spoken line
3. whether hop 2 and hop 3 preserve wardrobe, framing, and facial detail better than the previous verbose-hop contract
