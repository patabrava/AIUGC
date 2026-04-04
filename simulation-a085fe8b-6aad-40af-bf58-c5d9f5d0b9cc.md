# VEO 3.1 Simulation for Batch `a085fe8b-6aad-40af-bf58-c5d9f5d0b9cc`

This simulation reflects the current local production code path for the pending batch.

- batch: `a085fe8b-6aad-40af-bf58-c5d9f5d0b9cc`
- state: `S4_SCRIPTED`
- target_length_tier: `32`
- posts_count: `3`
- route: legacy `32s`
- base request: `4s`
- extension hops: `4`
- extension API duration: `7s` each
- negative prompt: sent via REST `negativePrompt`
- seed: not assigned for legacy `32s`

The batch contains three value posts; the simulation below reflects the current prompt chain for each post.

## Post 1

- post id: `834aeffa-0d8f-426e-9bd6-fcf762beacee`
- spoken duration: `26.0`
- review status: `removed`
- video_excluded: `True`
- script: `Niemand redet darüber, aber als Frau mit Behinderung erlebst du doppelte Diskriminierung. Du bist sowohl wegen deines Geschlechts als auch deiner Behinderung benachteiligt. Am Arbeitsmarkt heißt das oft: Teilzeit oder prekäre Jobs mit geringeren Chancen. In den Medien wirst du kaum gezeigt – nur knapp ein Drittel der sichtbaren Menschen mit Behinderung sind weiblich. Dein Alltag bleibt oft unsichtbar, stattdessen gibt es traurige Geschichten oder übertriebene Heldentaten.`

### Packed Dialogue

The approved post is packed into 5 dialogue segments:

1. `0.0s-4.0s` approx
   `Niemand redet darüber,`
2. `4.0s-11.0s` approx
   `aber als Frau mit Behinderung erlebst du doppelte Diskriminierung.`
3. `11.0s-18.0s` approx
   `Du bist sowohl wegen deines Geschlechts als auch deiner Behinderung benachteiligt.`
4. `18.0s-25.0s` approx
   `Am Arbeitsmarkt heißt das oft: Teilzeit oder prekäre Jobs mit geringeren Chancen.`
5. `25.0s-32.0s` approx
   `In den Medien wirst du kaum gezeigt – nur knapp ein Drittel der sichtbaren Menschen mit Behinderung sind weiblich. Dein Alltag bleibt oft unsichtbar, stattdessen gibt es traurige Geschichten oder übertriebene Heldentaten.`

### Shared `negativePrompt`

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```

### Base Request

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
    "durationSeconds": 4,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

### Base prompt text

```text
Character:
38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.

Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Scene:
The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.

Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.

Dialogue:
"Niemand redet darüber,"

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

### Extension Hop 1

```json
{
  "instances": [
    {
      "prompt": "<extension hop 1 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"aber als Frau mit Behinderung erlebst du doppelte Diskriminierung."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 2

```json
{
  "instances": [
    {
      "prompt": "<extension hop 2 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Du bist sowohl wegen deines Geschlechts als auch deiner Behinderung benachteiligt."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 3

```json
{
  "instances": [
    {
      "prompt": "<extension hop 3 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Am Arbeitsmarkt heißt das oft: Teilzeit oder prekäre Jobs mit geringeren Chancen."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 4

```json
{
  "instances": [
    {
      "prompt": "<extension hop 4 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"In den Medien wirst du kaum gezeigt – nur knapp ein Drittel der sichtbaren Menschen mit Behinderung sind weiblich. Dein Alltag bleibt oft unsichtbar, stattdessen gibt es traurige Geschichten oder übertriebene Heldentaten."

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

## Why This Is The Legacy Version

- The base request carries the full identity, scene, and camera contract.
- The extensions carry only the stable identity anchors, a short continuity cue, the next dialogue beat, and a short audio line.
- The extensions no longer resend the full scene paragraph or the full cinematography paragraph.
- The negative list is not embedded inline in the prompt body.
- The first `0.0s-4.0s` chunk is budgeted as a real spoken-time window, not just a sentence bucket.

---

## Post 2

- post id: `cbb83127-97ca-4d42-8f8a-c16570635e6b`
- spoken duration: `24.0`
- review status: `approved`
- video_excluded: `None`
- script: `Der Schreibtisch ist oft zu hoch oder der Platz davor viel zu eng. Dabei müssen Arbeitsflächen von 65 bis 125 cm höhenverstellbar sein, um dir einen gesunden Haltungswechsel zu ermöglichen. Du brauchst nach ASR V3a.2 mindestens 150x150 cm Bewegungsfreiheit direkt am Arbeitsplatz. Dein Körper und deine Gesundheit profitieren massiv von diesen ergonomischen Vorgaben. Nur so ist dein Arbeitsplatz wirklich barrierefrei und rückenfreundlich.`

### Packed Dialogue

The approved post is packed into 5 dialogue segments:

1. `0.0s-4.0s` approx
   `Der Schreibtisch ist oft zu hoch oder der Platz davor viel zu eng.`
2. `4.0s-11.0s` approx
   `Dabei müssen Arbeitsflächen von 65 bis 125 cm höhenverstellbar sein, um dir einen gesunden Haltungswechsel zu ermöglichen.`
3. `11.0s-18.0s` approx
   `Du brauchst nach ASR V3a.`
4. `18.0s-25.0s` approx
   `2 mindestens 150x150 cm Bewegungsfreiheit direkt am Arbeitsplatz.`
5. `25.0s-32.0s` approx
   `Dein Körper und deine Gesundheit profitieren massiv von diesen ergonomischen Vorgaben. Nur so ist dein Arbeitsplatz wirklich barrierefrei und rückenfreundlich.`

### Shared `negativePrompt`

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```

### Base Request

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
    "durationSeconds": 4,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

### Base prompt text

```text
Character:
38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.

Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Scene:
The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.

Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.

Dialogue:
"Der Schreibtisch ist oft zu hoch oder der Platz davor viel zu eng."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

### Extension Hop 1

```json
{
  "instances": [
    {
      "prompt": "<extension hop 1 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Dabei müssen Arbeitsflächen von 65 bis 125 cm höhenverstellbar sein, um dir einen gesunden Haltungswechsel zu ermöglichen."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 2

```json
{
  "instances": [
    {
      "prompt": "<extension hop 2 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Du brauchst nach ASR V3a."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 3

```json
{
  "instances": [
    {
      "prompt": "<extension hop 3 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"2 mindestens 150x150 cm Bewegungsfreiheit direkt am Arbeitsplatz."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 4

```json
{
  "instances": [
    {
      "prompt": "<extension hop 4 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Dein Körper und deine Gesundheit profitieren massiv von diesen ergonomischen Vorgaben. Nur so ist dein Arbeitsplatz wirklich barrierefrei und rückenfreundlich."

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

## Why This Is The Legacy Version

- The base request carries the full identity, scene, and camera contract.
- The extensions carry only the stable identity anchors, a short continuity cue, the next dialogue beat, and a short audio line.
- The extensions no longer resend the full scene paragraph or the full cinematography paragraph.
- The negative list is not embedded inline in the prompt body.
- The first `0.0s-4.0s` chunk is budgeted as a real spoken-time window, not just a sentence bucket.

---

## Post 3

- post id: `08c9c0d1-0d56-402e-a0b4-5288093c94fd`
- spoken duration: `22.0`
- review status: `removed`
- video_excluded: `True`
- script: `Dein Recht auf Gründungszuschuss? Deine Selbstständigkeit muss als nachhaltiger Haupterwerb überzeugen und mindestens 15 Wochenstunden umfassen. Du brauchst noch 150 Tage Anspruch auf Arbeitslosengeld I. Eine Tragfähigkeitsbescheinigung vom Fachmann ist zwingend, bevor du bis zu 20.000 Euro Förderung bekommst. Doch auch Zuschüsse für Auto-Umbauten oder Assistenz sind möglich! Der Weg in die Selbstständigkeit.`

### Packed Dialogue

The approved post is packed into 5 dialogue segments:

1. `0.0s-4.0s` approx
   `Dein Recht auf Gründungszuschuss?`
2. `4.0s-11.0s` approx
   `Deine Selbstständigkeit muss als nachhaltiger Haupterwerb überzeugen und mindestens 15 Wochenstunden umfassen.`
3. `11.0s-18.0s` approx
   `Du brauchst noch 150 Tage Anspruch auf Arbeitslosengeld I.`
4. `18.0s-25.0s` approx
   `Eine Tragfähigkeitsbescheinigung vom Fachmann ist zwingend, bevor du bis zu 20.000 Euro Förderung bekommst.`
5. `25.0s-32.0s` approx
   `Doch auch Zuschüsse für Auto-Umbauten oder Assistenz sind möglich! Der Weg in die Selbstständigkeit.`

### Shared `negativePrompt`

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```

### Base Request

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
    "durationSeconds": 4,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

### Base prompt text

```text
Character:
38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.

Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Scene:
The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.

Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.

Dialogue:
"Dein Recht auf Gründungszuschuss?"

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

### Extension Hop 1

```json
{
  "instances": [
    {
      "prompt": "<extension hop 1 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Deine Selbstständigkeit muss als nachhaltiger Haupterwerb überzeugen und mindestens 15 Wochenstunden umfassen."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 2

```json
{
  "instances": [
    {
      "prompt": "<extension hop 2 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Du brauchst noch 150 Tage Anspruch auf Arbeitslosengeld I."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 3

```json
{
  "instances": [
    {
      "prompt": "<extension hop 3 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Eine Tragfähigkeitsbescheinigung vom Fachmann ist zwingend, bevor du bis zu 20.000 Euro Förderung bekommst."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

### Extension Hop 4

```json
{
  "instances": [
    {
      "prompt": "<extension hop 4 prompt below>",
      "video": {
        "uri": "<previous video uri>"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "resolution": "720p",
    "durationSeconds": 7,
    "negativePrompt": "<shared negativePrompt>"
  }
}
```

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
"Doch auch Zuschüsse für Auto-Umbauten oder Assistenz sind möglich! Der Weg in die Selbstständigkeit."

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

## Why This Is The Legacy Version

- The base request carries the full identity, scene, and camera contract.
- The extensions carry only the stable identity anchors, a short continuity cue, the next dialogue beat, and a short audio line.
- The extensions no longer resend the full scene paragraph or the full cinematography paragraph.
- The negative list is not embedded inline in the prompt body.
- The first `0.0s-4.0s` chunk is budgeted as a real spoken-time window, not just a sentence bucket.

---
