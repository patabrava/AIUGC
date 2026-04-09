# Vertex AI Veo 3.1 Video Generation - API Reference

## Project Configuration

- **Project ID**: `onyx-authority-492115-m6`
- **Location**: `us-central1`
- **Model**: `veo-3.1-generate-001`
- **Endpoint**: `https://us-central1-aiplatform.googleapis.com/v1/projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001:predictLongRunning`
- **Auth**: OAuth2 Bearer token (Application Default Credentials via `gcloud auth application-default login`)
- **Reference Image**: `static/images/sarah.jpg` (802KB JPEG)

---

## Chain Profile: 32s Legacy (4+7+7+7+7)

| Hop | Type | Duration | Script Segment |
|-----|------|----------|----------------|
| 0 | Base (image-to-video) | 4s | "Seniorengerecht klingt gut, aber ist rechtlich wertlos!" |
| 1 | Extension | 7s | "Viele Begriffe auf Immobilienportalen wie 'behindertenfreundlich' sind ungeschützt und garantieren keine echte Barrierefreiheit." |
| 2 | Extension | 7s | "Nur 'rollstuhlgerecht' nach DIN 18040-2 sichert dir wirklich ausreichend Bewegungsflächen und Türbreiten." |
| 3 | Extension | 7s | "Achte auf die Kennzeichnung 'R', um böse Überraschungen zu vermeiden." |
| 4 | Extension | 7s | "Stell Förderanträge bei der Pflegekasse oder KfW immer, bevor du Maßnahmen beginnst." |

---

## Hop 0: Base Video (Image-to-Video)

**Endpoint**: `POST https://us-central1-aiplatform.googleapis.com/v1/projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001:predictLongRunning`

**Headers**:
```
Authorization: Bearer <oauth2_token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "instances": [
    {
      "prompt": "Character:\n38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.\n\nStyle:\nNatural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.\n\nAction:\nSeated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: Seniorengerecht klingt gut, aber ist rechtlich wertlos! (After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.)\n\nScene:\nThe woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.\n\nCinematography:\nVertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.\n\nEnding:\nAfter the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.\n\nAudio:\nAudio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. After the final word, the audio gently settles into a quiet room tone for a brief moment before the clip ends.",
      "image": {
        "bytesBase64Encoded": "<base64_encoded_sarah_jpg>",
        "mimeType": "image/jpeg"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 4
  }
}
```

**Response**:
```json
{
  "name": "projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/<operation_uuid>"
}
```

---

## Hops 1-4: Video Extension

**Endpoint**: Same as base — `POST ...:predictLongRunning`

**Request Body** (template for each extension hop):
```json
{
  "instances": [
    {
      "prompt": "<segment_prompt>",
      "video": {
        "uri": "gs://<bucket>/<path>/<previous_hop_video>.mp4"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 7
  }
}
```

### Hop 1 (4s → 11s)

```json
{
  "instances": [
    {
      "prompt": "Character:\n38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.\n\nStyle:\nNatural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.\n\nAction:\nSeated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: Viele Begriffe auf Immobilienportalen wie \"behindertenfreundlich\" sind ungeschützt und garantieren keine echte Barrierefreiheit. (After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.)\n\nScene:\nThe woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.\n\nCinematography:\nVertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.\n\nEnding:\nAfter the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.\n\nAudio:\nAudio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. After the final word, the audio gently settles into a quiet room tone for a brief moment before the clip ends.",
      "video": {
        "uri": "gs://<gcs_bucket_from_hop_0>/output.mp4"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 7
  }
}
```

### Hop 2 (11s → 18s)

```json
{
  "instances": [
    {
      "prompt": "Character:\n38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.\n\nStyle:\nNatural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.\n\nAction:\nSeated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: Nur \"rollstuhlgerecht\" nach DIN 18040-2 sichert dir wirklich ausreichend Bewegungsflächen und Türbreiten. (After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.)\n\nScene:\nThe woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.\n\nCinematography:\nVertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.\n\nEnding:\nAfter the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.\n\nAudio:\nAudio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. After the final word, the audio gently settles into a quiet room tone for a brief moment before the clip ends.",
      "video": {
        "uri": "gs://<gcs_bucket_from_hop_1>/output.mp4"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 7
  }
}
```

### Hop 3 (18s → 25s)

```json
{
  "instances": [
    {
      "prompt": "Character:\n38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.\n\nStyle:\nNatural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.\n\nAction:\nSeated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: Achte auf die Kennzeichnung \"R\", um böse Überraschungen zu vermeiden. (After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.)\n\nScene:\nThe woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.\n\nCinematography:\nVertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.\n\nEnding:\nAfter the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.\n\nAudio:\nAudio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. After the final word, the audio gently settles into a quiet room tone for a brief moment before the clip ends.",
      "video": {
        "uri": "gs://<gcs_bucket_from_hop_2>/output.mp4"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 7
  }
}
```

### Hop 4 (25s → 32s)

```json
{
  "instances": [
    {
      "prompt": "Character:\n38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.\n\nStyle:\nNatural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.\n\nAction:\nSeated in a wheelchair in the bedroom, she speaks directly to camera in one continuous take. She speaks at a natural conversational pace, uses small natural hand gestures and subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly at the end of the line. She says: Stell Förderanträge bei der Pflegekasse oder KfW immer, bevor du Maßnahmen beginnst. (After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.)\n\nScene:\nThe woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space. The wheelchair is partially visible in the frame.\n\nCinematography:\nVertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.\n\nEnding:\nAfter the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.\n\nAudio:\nAudio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. After the final word, the audio gently settles into a quiet room tone for a brief moment before the clip ends.",
      "video": {
        "uri": "gs://<gcs_bucket_from_hop_3>/output.mp4"
      }
    }
  ],
  "parameters": {
    "aspectRatio": "9:16",
    "durationSeconds": 7
  }
}
```

---

## Polling Operation Status

**Endpoint**: `GET https://us-central1-aiplatform.googleapis.com/v1/projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/<operation_uuid>`

**Headers**:
```
Authorization: Bearer <oauth2_token>
```

**Response (processing)**:
```json
{
  "name": "projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/<uuid>",
  "metadata": {
    "@type": "type.googleapis.com/google.cloud.aiplatform.v1beta1.OperationMetadata",
    "createTime": "2026-04-04T22:04:32.123Z",
    "updateTime": "2026-04-04T22:04:35.456Z"
  },
  "done": false
}
```

**Response (completed)**:
```json
{
  "name": "projects/onyx-authority-492115-m6/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/<uuid>",
  "done": true,
  "response": {
    "@type": "type.googleapis.com/google.cloud.aiplatform.v1beta1.GenerateVideosResponse",
    "generatedSamples": [
      {
        "video": {
          "uri": "gs://<bucket>/<path>/output.mp4",
          "mimeType": "video/mp4"
        }
      }
    ]
  }
}
```

---

## Key Notes

1. **Image-to-Video**: The `image` field requires `bytesBase64Encoded` (base64 string) and `mimeType` (e.g., `"image/jpeg"`) as sibling keys — NOT nested under `inlineData`.

2. **Video Extension**: The `video` field requires a GCS URI (`gs://bucket/path/file.mp4`) from the previous hop's output. The extension duration is added to the existing video length.

3. **Legacy 32s Chain**: Uses `4+7+7+7+7` seconds (5 total requests). The base hop is 4s image-to-video, each extension hop adds 7s.

4. **Segment Splitting**: The full German script is split into 5 segments aligned to the time windows:
   - 0-4s: "Seniorengerecht klingt gut, aber ist rechtlich wertlos!"
   - 4-11s: "Viele Begriffe auf Immobilienportalen wie 'behindertenfreundlich' sind ungeschützt und garantieren keine echte Barrierefreiheit."
   - 11-18s: "Nur 'rollstuhlgerecht' nach DIN 18040-2 sichert dir wirklich ausreichend Bewegungsflächen und Türbreiten."
   - 18-25s: "Achte auf die Kennzeichnung 'R', um böse Überraschungen zu vermeiden."
   - 25-32s: "Stell Förderanträge bei der Pflegekasse oder KfW immer, bevor du Maßnahmen beginnst."

5. **Prompt Structure**: Each hop prompt contains the same Character, Style, Scene, Cinematography, and Audio blocks for consistency. Only the spoken dialogue in the Action section changes per segment.

6. **Auth**: Vertex AI does NOT support API keys. Requires OAuth2 via Application Default Credentials (`gcloud auth application-default login`) or a service account key.
