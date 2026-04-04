# Vertex AI Video Testing

This document describes the minimum steps to test the dedicated Vertex AI video endpoint locally.

## Prerequisites
- `VERTEX_AI_PROJECT_ID` set to your Google Cloud project.
- `VERTEX_AI_LOCATION` set to the target region (default: `us-central1`).
- `VERTEX_AI_ENABLED=true`.

## Authentication

Local development (ADC):
```bash
gcloud auth application-default login
```

GCP runtime:
- Attach a service account with Vertex AI permissions.

## Endpoint

The explicit testing endpoint is:
`POST /videos/vertex`

### Text-to-Video
```bash
curl -X POST http://127.0.0.1:8000/videos/vertex \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "text",
    "prompt": "A cinematic product intro in a modern studio.",
    "aspect_ratio": "9:16",
    "duration_seconds": 8
  }'
```

### Image-to-Video
```bash
IMAGE_B64=$(base64 -i /path/to/image.jpg)

curl -X POST http://127.0.0.1:8000/videos/vertex \
  -H "Content-Type: application/json" \
  -d "{
    \"mode\": \"image\",
    \"prompt\": \"A cinematic reveal.\",
    \"aspect_ratio\": \"16:9\",
    \"duration_seconds\": 8,
    \"image_base64\": \"${IMAGE_B64}\",
    \"image_mime_type\": \"image/jpeg\"
  }"
```

## Notes
- The endpoint is isolated from the existing VEO/Sora flows.
- If the SDK lacks video methods, the adapter returns a validation error indicating missing support.
