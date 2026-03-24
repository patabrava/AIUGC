#!/usr/bin/env python3
"""
Live integration test: 1 base generation + 1 extension = 2 API calls max.

Usage: .venv/bin/python tests/live_test_16s_extension.py
"""

import os
import sys
import time
import struct

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.core.config import get_settings
from google import genai
from google.genai import types

POLL_INTERVAL = 10
MAX_POLL_MINUTES = 10


def parse_mp4_duration(data: bytes) -> float:
    idx = data.find(b"mvhd")
    if idx == -1:
        return -1.0
    version = data[idx + 4]
    if version == 0:
        ts = struct.unpack(">I", data[idx + 16 : idx + 20])[0]
        dur = struct.unpack(">I", data[idx + 20 : idx + 24])[0]
    else:
        ts = struct.unpack(">I", data[idx + 24 : idx + 28])[0]
        dur = struct.unpack(">Q", data[idx + 28 : idx + 36])[0]
    return dur / ts if ts else -1.0


def poll(client, op, label):
    deadline = time.time() + MAX_POLL_MINUTES * 60
    while not op.done:
        if time.time() > deadline:
            print(f"  TIMEOUT on {label}")
            sys.exit(1)
        print(f"  ...{label} processing")
        time.sleep(POLL_INTERVAL)
        op = client.operations.get(op)
    return op


def main():
    settings = get_settings()
    client = genai.Client(api_key=settings.google_ai_api_key)

    # --- API CALL 1: Base generation ---
    print("=== API CALL 1/2: Base generation ===")
    base_prompt = (
        "A woman with light brown hair sits at a desk in a bright modern office. "
        "She looks at the camera and smiles warmly, then begins speaking in a "
        "calm, friendly tone. The camera is still, medium close-up framing. "
        "Horizontal 16:9 framing."
    )
    op = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=base_prompt,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            resolution="720p",
            aspect_ratio="16:9",
        ),
    )
    print(f"  Op: {op.name}")
    op = poll(client, op, "base")

    if not op.response or not op.response.generated_videos:
        print("  FILTERED — base video was safety-filtered. No further API calls.")
        print("  (This is a Veo safety filter issue, not a code issue.)")
        sys.exit(1)

    base_video = op.response.generated_videos[0].video
    print(f"  Base video URI: {base_video.uri[:80]}...")

    # Download base to check duration
    import httpx
    resp = httpx.get(base_video.uri, headers={"x-goog-api-key": settings.google_ai_api_key}, follow_redirects=True, timeout=60)
    base_dur = parse_mp4_duration(resp.content)
    print(f"  Base: {len(resp.content):,} bytes, ~{base_dur:.1f}s")

    # --- API CALL 2: Extension ---
    print("\n=== API CALL 2/2: Extension ===")
    ext_prompt = (
        "She continues speaking enthusiastically, gesturing with her hands. "
        "She is engaged and expressive, maintaining eye contact with the camera."
    )
    ext_op = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        video=base_video,
        prompt=ext_prompt,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            resolution="720p",
        ),
    )
    print(f"  Op: {ext_op.name}")
    ext_op = poll(client, ext_op, "extension")

    if not ext_op.response or not ext_op.response.generated_videos:
        print("  FILTERED — extension was safety-filtered.")
        sys.exit(1)

    ext_video = ext_op.response.generated_videos[0].video
    print(f"  Extended video URI: {ext_video.uri[:80]}...")

    resp = httpx.get(ext_video.uri, headers={"x-goog-api-key": settings.google_ai_api_key}, follow_redirects=True, timeout=60)
    ext_dur = parse_mp4_duration(resp.content)
    print(f"  Extended: {len(resp.content):,} bytes, ~{ext_dur:.1f}s")

    # Save
    outpath = "/tmp/veo_extension_test.mp4"
    with open(outpath, "wb") as f:
        f.write(resp.content)

    print(f"\n{'='*40}")
    print(f"  Base:     ~{base_dur:.1f}s")
    print(f"  Extended: ~{ext_dur:.1f}s")
    print(f"  Saved:    {outpath}")
    if ext_dur > base_dur:
        print(f"  PASS — extended video ({ext_dur:.1f}s) > base ({base_dur:.1f}s)")
    else:
        print(f"  FAIL — extension did not increase duration")
        sys.exit(1)


if __name__ == "__main__":
    main()
