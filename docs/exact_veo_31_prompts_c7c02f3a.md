# Exact VEO 3.1 Prompts

Batch: `68fd1a7a-244b-4f71-9fef-644ac9b42129`
Post: `c7c02f3a-5cc8-4d9d-9b38-5f5181ba4545`

These are the exact prompt bodies recorded in `video_prompt_audit` for the VEO 3.1 submission and extension hop.

## Base Prompt

Prompt path: `veo_extended_segment`
Requested seconds: `16`
Operation:
`projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/547b5818-25ec-4976-acab-aad134d0a373`

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
"Das sagt dir kaum jemand: Deine Begleitperson fährt im Nahverkehr immer kostenlos mit dir!"

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

Negative prompt:

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```

## Extension Prompt

Prompt path: `veo_extension_hop`
Requested seconds: `7`
Operation:
`projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/6c648925-d405-4be2-a0db-4cbe8552555f`

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
"Das ist gesetzlich garantiert, auch wenn du keine Wertmarke besitzt. Dein gültiger Schwerbehindertenausweis mit Merkzeichen B ist dafür der Fahrschein."

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth closes and comes fully to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

Negative prompt:

```text
subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, extra plants, wall color change, bedding color change, different room, lighting shift
```
