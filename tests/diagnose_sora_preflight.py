"""
Sora Preflight Diagnostic Tool
Per Constitution § X: Hypothesis-Driven Debugging
Allows dry-run validation of Sora payloads without triggering paid renders.
"""

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import httpx

# Ensure app modules are importable when running directly from /tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.features.posts.prompt_builder import build_video_prompt_from_seed, build_optimized_prompt  # noqa: E402

SORA_API_BASE = "https://api.openai.com/v1"
SORA_VALIDATE_ENDPOINT = f"{SORA_API_BASE}/videos/validate"
SORA_SUBMIT_ENDPOINT = f"{SORA_API_BASE}/videos"

DEFAULT_MODEL = "sora-2-pro"
DEFAULT_SIZE = "1024x1792"
CANONICAL_PROMPT = (
    "Subject & Look:\n"
    "A 38-year-old German woman with long, slightly damp light-brown hair with natural blonde highlights; hazel almond-shaped eyes with faint crow’s feet; a friendly oval face with soft expression lines; warm light-medium skin with neutral undertones. She faces the camera with a neutral, friendly expression that softens into a gentle smile.\n\n"
    "Setting:\n"
    "A modern, tidy bedroom with blush-pink walls and minimal décor. Vertical frame.\n\n"
    "Format:\n"
    "Aspect ratio 9:16 vertical; resolution 720x1280 (720p); capture at 24 fps; single continuous take with no cuts.\n\n"
    "Cinematography:\n"
    "Camera shot: medium close-up, slightly high angle, centered; one continuous take, no cuts. Lens & DOF: smartphone front camera (~24 mm equiv.), deep depth of field with subtle natural falloff. Camera motion: subtle handheld sway and micro-jitter consistent with a selfie grip.\n\n"
    "Lighting & Palette:\n"
    "Key: soft vanity light frontal; Fill: window daylight camera-right; Rim: gentle ambient wrap. Palette anchors: blush pink, soft white, warm oak, brushed nickel.\n\n"
    "Action (8 s):\n"
    "0–2 s: seated in a wheelchair, steady head-and-shoulders, direct eye contact, neutral expression.\n"
    "2–5 s: small natural hand gesture; slight upper-body nods.\n"
    "5–8 s: expression warms into a gentle smile; brief 0.5 s pause after speaking.\n\n"
    "Dialogue:\n"
    "\"Kennst du das, wenn enge Türen stressen? Ich hab gelernt, Geduld und kleine Tricks zu nutzen.\"\n\n"
    "Audio:\n"
    "Clean smartphone voice, intimate presence, faint even room-tone; no music; no background HVAC.\n\n"
    "Constraints — Avoid:\n"
    "Text overlays or subtitles, logos or branding, poor lighting, heavy compression, excessive shake, off-sync audio, changes to character identity, cuts or angle changes, added background music."
)
ALLOWED_SECONDS = {4, 8, 12}


def _format_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _load_seed(seed_path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not seed_path:
        return None
    try:
        with open(seed_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Seed file '{seed_path}' not found. Export a post's seed_data to JSON before using --seed."
        ) from exc


def build_prompt(seed_json: Optional[Dict[str, Any]]) -> str:
    if not seed_json:
        return CANONICAL_PROMPT

    prompt = build_video_prompt_from_seed(seed_json)
    optimized = prompt.get("optimized_prompt")
    if optimized:
        return optimized

    action = prompt.get("action", "")
    dialogue = seed_json.get("script") or seed_json.get("dialog_script") or ""
    return build_optimized_prompt(dialogue or action)


def build_payload(prompt: str, *, model: str, seconds: int, size: str) -> Dict[str, Any]:
    if seconds not in ALLOWED_SECONDS:
        raise ValueError(f"Seconds must be one of {sorted(ALLOWED_SECONDS)}")
    return {
        "model": model,
        "prompt": prompt,
        "seconds": str(seconds),
        "size": size,
    }


def perform_validation(client: httpx.Client, payload: Dict[str, Any]) -> httpx.Response:
    return client.post(SORA_VALIDATE_ENDPOINT, json=payload)


def perform_submission(client: httpx.Client, payload: Dict[str, Any]) -> httpx.Response:
    return client.post(SORA_SUBMIT_ENDPOINT, json=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight Sora 2/2 Pro video generation payloads")
    parser.add_argument("--seconds", type=int, default=8, choices=sorted(ALLOWED_SECONDS), help="Clip length in seconds")
    parser.add_argument("--size", type=str, default=DEFAULT_SIZE, help="Target size, e.g. 1024x1792")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Sora model identifier")
    parser.add_argument("--seed", type=str, help="Path to seed JSON to build prompt from")
    parser.add_argument("--skip-submit", action="store_true", help="Only run validation; do not submit paid generation")
    args = parser.parse_args()

    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()
    api_key = settings.openai_api_key
    if not api_key:
        print("❌ OPENAI_API_KEY missing in environment (.env)")
        return 1

    seed_data = _load_seed(args.seed)
    prompt = build_prompt(seed_data)
    payload = build_payload(
        prompt,
        model=args.model,
        seconds=args.seconds,
        size=args.size,
    )

    print("\n=== Sora Payload Preview ===")
    print(_format_json(payload))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "video-preflight",
        "X-Correlation-ID": f"sora-preflight-{uuid.uuid4()}",
    }

    timeout = httpx.Timeout(timeout=60.0, connect=10.0, read=60.0, write=60.0)
    with httpx.Client(headers=headers, timeout=timeout) as client:
        print("\n=== Running validation (no cost) ===")
        validation_response = perform_validation(client, payload)
        print(f"Validation status: {validation_response.status_code}")
        try:
            validation_json = validation_response.json()
            print(_format_json(validation_json))
        except json.JSONDecodeError:
            print(validation_response.text[:500])

        if validation_response.status_code >= 400:
            print("❌ Validation failed; aborting submission.")
            return 1

        if args.skip_submit:
            print("✅ Validation passed. Submission skipped by flag.")
            return 0

        print("\n=== Submitting paid render ===")
        submission_response = perform_submission(client, payload)
        print(f"Submission status: {submission_response.status_code}")
        try:
            submission_json = submission_response.json()
            print(_format_json(submission_json))
        except json.JSONDecodeError:
            print(submission_response.text[:500])

        if submission_response.status_code >= 400:
            print("❌ Submission failed.")
            return 1

        print("✅ Submission queued successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
