"""
VEO 3.1 Preflight Diagnostic Tool
Per Constitution § X: Hypothesis-Driven Debugging
Helps inspect the payload that would be sent to Google VEO 3.1 before running a paid generation.
"""

import argparse
import json
import os
import sys
import uuid
from typing import Any, Dict, Optional

import httpx

# Ensure app modules are importable when running directly from /tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.features.posts.prompt_text import build_full_prompt_text  # noqa: E402
from app.features.posts.prompt_builder import build_video_prompt_from_seed  # noqa: E402

VEO_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning"
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_RESOLUTION = "1080p"

CANONICAL_SEED_PROMPT = {
    "character": "Character: 38-year-old German woman with long, damp, light brown hair with natural blonde highlights; hazel, almond-shaped eyes with subtle eye wrinkles (fine crow’s feet) at the outer corners; a friendly oval face; soft forehead lines (fine horizontal expression lines) that are faint at rest; gentle laugh lines (light nasolabial folds) framing the mouth; and a warm light-medium skin tone with neutral undertones. She is looking directly at the camera with a neutral, friendly expression. Filmed on an iPhone 15 Pro, bright soft vanity lighting, neutral clean color palette, hyper-realistic skin texture with visible pores..",
    "action": "Action: Sits in a wheelchair in the bedroom, hair still slightly damp, looking directly into camera with a neutral, friendly expression that turns to a gentle smile. Maintains steady head-and-shoulders orientation; uses small, natural hand gestures and subtle upper-body nods while speaking. Remains seated and centered for a single continuous take with no cuts or alternate angles and says: Kennst du das, wenn enge Türen stressen? Ich hab gelernt, Geduld und kleine Tricks zu nutzen. (stiller Halt)",
    "scene": "Scene: The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. Clean, minimal décor. Natural daylight streams through an unseen window camera-right, supplemented by soft ambient lighting creating even, flattering illumination across the space.",
    "cinematography": "Cinematography: Camera Shot: Medium close-up from a slightly high angle, with centered framing that keeps her head and shoulders in the shot. This camera shot does not change during the whole take. Lens & DOF: modern smartphone front camera (~24 mm equiv.), deep depth of field keeping the background in focus with a natural subtle falloff. Camera Motion: Subtle handheld sway and jitter consistent with a selfie grip, including very slight natural arm movements as she speaks and gestures.",
    "universal_negatives": "Universal Negatives (hard constraints): subtitles, captions, watermark, text overlays, words on screen, logo, branding, poor lighting, blurry footage, low resolution, artifacts, unwanted objects, inconsistent character appearance, audio sync issues, amateur quality, cartoon effects, unrealistic proportions, distorted hands, artificial lighting, oversaturation, compression noise, excessive camera shake.",
    "audio": {
        "dialogue": "Audio: Recorded through modern smartphone mic — clear, front-facing voice with intimate presence and a soft, short living-room bloom (RT60 ≈ 0.3–0.4 s). Camera 20–30 cm from mouth, mic unobstructed. HVAC/appliances off; noise floor ≤ –55 dBFS with a faint, even room-tone bed. No music, one-take natural pacing.",
        "capture": None,
    },
}


def _format_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _load_seed(seed_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not seed_path:
        return None
    with open(seed_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_prompt(seed_data: Optional[Dict[str, Any]]) -> str:
    if not seed_data:
        return build_full_prompt_text(CANONICAL_SEED_PROMPT)
    prompt = build_video_prompt_from_seed(seed_data)
    return build_full_prompt_text(prompt)


def build_request_body(prompt: str) -> Dict[str, Any]:
    # REST endpoint only accepts the prompt in instances per VEO doc.
    return {
        "instances": [
            {
                "prompt": prompt
            }
        ]
    }


def maybe_submit_request(client: httpx.Client, payload: Dict[str, Any]) -> httpx.Response:
    return client.post(VEO_ENDPOINT, json=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight Google VEO 3.1 video generation payload")
    parser.add_argument("--seed", type=str, help="Path to seed JSON to reconstruct full prompt")
    parser.add_argument("--submit", action="store_true", help="Actually call the VEO endpoint (paid request)")
    args = parser.parse_args()

    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()
    api_key = settings.google_ai_api_key
    if not api_key:
        print("❌ GOOGLE_AI_API_KEY missing in environment (.env)")
        return 1

    seed_data = _load_seed(args.seed)
    prompt_text = build_prompt(seed_data)
    payload = build_request_body(prompt_text)

    print("=== VEO Payload Preview ===")
    print(_format_json(payload))

    if not args.submit:
        print("\n✅ Dry run complete. Use --submit to call the VEO API.")
        return 0

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "X-Correlation-ID": f"veo-preflight-{uuid.uuid4()}",
    }
    timeout = httpx.Timeout(timeout=60.0, connect=10.0, read=60.0, write=60.0)

    logger.info(
        "veo_preflight_submission",
        has_seed=bool(seed_data),
        prompt_length=len(prompt_text),
    )

    with httpx.Client(headers=headers, timeout=timeout) as client:
        print("\n=== Submitting to VEO (paid) ===")
        response = maybe_submit_request(client, payload)
        print(f"Status: {response.status_code}")
        try:
            print(_format_json(response.json()))
        except json.JSONDecodeError:
            print(response.text[:500])

        if response.status_code >= 400:
            print("❌ VEO submission failed.")
            return 1

        print("✅ VEO submission accepted (queued). Operation ID in response JSON.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
