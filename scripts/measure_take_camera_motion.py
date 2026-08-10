#!/usr/bin/env python
"""Measure camera motion in a raw Veo take.

Handheld micro-motion and a slow push-in look nothing alike but score almost
identically under a per-frame scene-score gate: a 25% zoom spread across 192
frames only moves each frame by ~0.007, far below
``TERMINAL_RESET_SCENE_SCORE``. This reports both signals separately so an
intentional handheld look can be told apart from an unwanted camera move.

Usage:
    python scripts/measure_take_camera_motion.py raw-veo-8s.mp4
    python scripts/measure_take_camera_motion.py a.mp4 b.mp4   # compare takes
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.features.shot_production.visual_seams import (  # noqa: E402
    TERMINAL_RESET_SCENE_SCORE,
)


# A take whose first and last frames differ by more than this much scale is
# performing a camera move rather than drifting in someone's hand.
PUSH_IN_TOLERANCE = 1.02
_SCALE_SEARCH_MAX = 1.30
_SCALE_SEARCH_STEP = 0.005

_FRAME = re.compile(r"\bframe:\s*\d+.*?\bpts_time:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))")
_SCORE = re.compile(r"lavfi\.scene_score=(-?(?:\d+(?:\.\d*)?|\.\d+))")


def _probe_duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True, text=True, timeout=60,
    )
    return float(result.stdout.strip())


def scene_scores(video: Path) -> list[tuple[float, float]]:
    """Per-frame scene scores across the whole take."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-i", str(video),
            "-vf", "select='gte(scene,0)',metadata=print",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=600,
    )
    rows: list[tuple[float, float]] = []
    timestamp: float | None = None
    for line in result.stderr.splitlines():
        frame = _FRAME.search(line)
        if frame:
            timestamp = float(frame.group(1))
            continue
        score = _SCORE.search(line)
        if score and timestamp is not None:
            rows.append((timestamp, float(score.group(1))))
            timestamp = None
    return rows


def _grab(video: Path, seconds: float, destination: Path) -> Image.Image:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error",
            "-ss", f"{seconds:.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "2", str(destination), "-y",
        ],
        capture_output=True, text=True, timeout=120,
    )
    return Image.open(destination).convert("L")


def push_in_scale(video: Path) -> float:
    """Best-fit zoom factor between the first and last frame.

    Compares a background-only strip so the speaking subject cannot dominate
    the fit. 1.00 means the framing held; higher means the camera pushed in.
    """
    duration = _probe_duration(video)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        first = _grab(video, 0.2, root / "first.jpg")
        last = _grab(video, max(0.3, duration - 0.05), root / "last.jpg")

    width, height = first.size
    # Right-hand strip: background only, clear of the centred subject.
    region = (int(width * 0.70), 0, width, int(height * 0.62))

    def zoomed(image: Image.Image, scale: float) -> Image.Image:
        crop_w, crop_h = width / scale, height / scale
        left, top = (width - crop_w) / 2, (height - crop_h) / 2
        return image.crop(
            (int(left), int(top), int(left + crop_w), int(top + crop_h))
        ).resize((width, height), Image.LANCZOS)

    best_scale, best_difference = 1.0, None
    steps = int((_SCALE_SEARCH_MAX - 1.0) / _SCALE_SEARCH_STEP) + 1
    for step in range(steps):
        scale = 1.0 + step * _SCALE_SEARCH_STEP
        difference = ImageStat.Stat(
            ImageChops.difference(
                zoomed(first, scale).crop(region), last.crop(region)
            )
        ).mean[0]
        if best_difference is None or difference < best_difference:
            best_scale, best_difference = scale, difference
    return best_scale


def report(video: Path) -> bool:
    rows = scene_scores(video)
    scores = sorted(score for _, score in rows)
    scale = push_in_scale(video)
    over = [(t, s) for t, s in rows if s >= TERMINAL_RESET_SCENE_SCORE]

    print(f"\n{video}")
    print(f"  frames scored      : {len(rows)}")
    print(f"  scene score median : {statistics.median(scores):.6f}")
    print(f"  scene score p90    : {scores[int(len(scores) * 0.90)]:.6f}")
    print(f"  scene score p99    : {scores[int(len(scores) * 0.99)]:.6f}")
    print(f"  scene score max    : {scores[-1]:.6f}")
    print(f"  tail-gate threshold: {TERMINAL_RESET_SCENE_SCORE}")
    print(f"  frames over gate   : {len(over)}")
    for timestamp, score in over:
        print(f"      t={timestamp:.3f}  {score:.6f}")

    passed = scale <= PUSH_IN_TOLERANCE
    verdict = "OK (framing held)" if passed else "FAIL (camera move)"
    print(f"  push-in scale      : {scale:.3f}x  ({(scale - 1) * 100:+.1f}%)  {verdict}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="+", type=Path)
    arguments = parser.parse_args()

    missing = [video for video in arguments.videos if not video.is_file()]
    if missing:
        parser.error(f"missing video(s): {', '.join(str(v) for v in missing)}")

    results = [report(video) for video in arguments.videos]
    print()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
