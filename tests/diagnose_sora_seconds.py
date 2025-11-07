"""
Sora 2 Pro Seconds-Type Diagnostic Script
Per Constitution § X: Hypothesis-Driven Debugging
Reproduces the 400 "invalid_type" error observed in logs when `seconds` is sent as an integer.
"""

import json
import sys
import os
import uuid
import traceback
from typing import Any, Dict

import httpx

# Ensure app modules are importable when running directly from /tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402

SORA_API_URL = "https://api.openai.com/v1/videos"
DEFAULT_PROMPT = (
    "Character: 38-year-old German woman filmed selfie-style, neutral expression. "
    "Action: Speaks directly to camera about accessibility hacks while seated. "
    "Style: Smartphone UGC, bright soft lighting, unfiltered.")
DEFAULT_MODEL = "sora-2-pro"
DEFAULT_SIZE = "1024x1792"


def format_payload(payload: Dict[str, Any]) -> str:
    """Return pretty JSON string of payload for console output."""
    return json.dumps(payload, indent=2, ensure_ascii=False)


def send_request(client: httpx.Client, payload: Dict[str, Any], label: str) -> httpx.Response:
    """Send request to Sora API and print structured output."""
    print("\n" + "-" * 80)
    print(f"Attempt: {label}")
    print("Payload:")
    print(format_payload(payload))

    try:
        response = client.post(SORA_API_URL, json=payload)
    except Exception as exc:  # pragma: no cover - direct diagnostic output
        print(f"❌ Request failed: {exc}")
        raise

    print("Response status:", response.status_code)
    print("Response body:")
    print(response.text)
    return response


def main() -> int:
    """Run diagnostic attempts for Sora seconds type handling."""
    configure_logging()
    logger = get_logger(__name__)

    settings = get_settings()
    api_key = settings.openai_api_key
    if not api_key:
        print("❌ OPENAI API key not configured (settings.openai_api_key)")
        return 1

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    correlation_id = f"diagnostic-{uuid.uuid4()}"
    logger.info(
        "sora_seconds_diagnostic_started",
        correlation_id=correlation_id,
        model=DEFAULT_MODEL,
        size=DEFAULT_SIZE,
    )

    base_payload: Dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "prompt": DEFAULT_PROMPT,
        "size": DEFAULT_SIZE,
    }

    try:
        with httpx.Client(headers=headers, timeout=httpx.Timeout(15.0, read=60.0)) as client:
            # Attempt 1: seconds as integer (expected to reproduce 400 invalid_type)
            int_payload = {**base_payload, "seconds": 8}
            response_int = send_request(client, int_payload, "seconds as integer (expected 400)")

            # Attempt 2: seconds as string (expected to pass validation if value allowed)
            string_payload = {**base_payload, "seconds": "8"}
            response_str = send_request(client, string_payload, "seconds as string (expected 202/200)")

        logger.info(
            "sora_seconds_diagnostic_finished",
            correlation_id=correlation_id,
            int_status=response_int.status_code,
            str_status=response_str.status_code,
        )

        print("\n" + "=" * 80)
        print("Summary")
        print("=" * 80)
        print(f"Int payload status: {response_int.status_code}")
        print(f"String payload status: {response_str.status_code}")

        if response_int.status_code == 400 and response_str.status_code in {200, 201, 202}:
            print("✅ Diagnosis confirmed: Sora expects `seconds` as string literal (\"4\", \"8\", \"12\").")
        else:
            print("⚠️ Unexpected response codes. Inspect output above for details.")

        return 0

    except Exception as exc:
        logger.exception("sora_seconds_diagnostic_failed", correlation_id=correlation_id, error=str(exc))
        print("\n❌ Diagnostic run failed:", exc)
        print(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
