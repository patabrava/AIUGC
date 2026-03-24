"""Caption renderer — Pillow text rendering + FFmpeg overlay burn-in.

Uses Pillow to render caption text as transparent PNG frames, then FFmpeg's
overlay filter to composite them onto the video. This approach works with
any FFmpeg build (no libass/libfreetype required).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.logging import get_logger

logger = get_logger(__name__)


class CaptionRendererError(Exception):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def group_words_into_phrases(words: list[Word], *, max_words: int = 4) -> list[dict[str, Any]]:
    """Group words into display phrases of max_words each."""
    if not words:
        return []
    phrases = []
    for i in range(0, len(words), max_words):
        chunk = words[i : i + max_words]
        phrases.append({
            "text": " ".join(w.word for w in chunk),
            "start": chunk[0].start,
            "end": chunk[-1].end,
            "words": chunk,
        })
    return phrases


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get a bold font, falling back gracefully."""
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",  # Arch Linux
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_caption_frame(
    text: str,
    highlight_index: int,
    words: list[Word],
    video_width: int,
    video_height: int,
    font_size: int,
) -> Image.Image:
    """Render a single caption frame as a transparent PNG with word highlight."""
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size)

    # Build word segments with colors
    word_texts = [w.word for w in words]

    # Measure total width to center
    space_width = draw.textlength(" ", font=font)
    word_widths = [draw.textlength(w, font=font) for w in word_texts]
    total_width = sum(word_widths) + space_width * (len(word_texts) - 1)

    # Position: centered horizontally, lower third vertically
    x_start = (video_width - total_width) / 2
    y_pos = int(video_height * 0.72)

    x = x_start
    for i, word_text in enumerate(word_texts):
        if i == highlight_index:
            fill = (255, 255, 255, 255)  # Bright white for active word
        else:
            fill = (180, 180, 180, 200)  # Dimmer for inactive

        # Draw outline (black border for readability)
        outline_range = 3
        for dx in range(-outline_range, outline_range + 1):
            for dy in range(-outline_range, outline_range + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y_pos + dy), word_text, font=font, fill=(0, 0, 0, 200))

        # Draw text
        draw.text((x, y_pos), word_text, font=font, fill=fill)
        x += word_widths[i] + space_width

    return img


def _get_video_fps(video_path: str) -> float:
    """Get video FPS using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return 30.0  # Default fallback
    try:
        data = json.loads(result.stdout)
        rate_str = data["streams"][0]["r_frame_rate"]
        num, den = rate_str.split("/")
        return float(num) / float(den)
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        return 30.0


def _get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width and height using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return 1080, 1920
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError):
        return 1080, 1920


def generate_ass_content(
    transcript: WordLevelTranscript, *, video_width: int = 1080, video_height: int = 1920
) -> str:
    """Generate ASS subtitle content (kept for compatibility/testing)."""
    font_size = max(int(video_width * 0.065), 48)
    margin_bottom = int(video_height * 0.25)
    header = f"""[Script Info]
Title: Auto Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Bold,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if not transcript.words:
        return header
    return header


def burn_captions(
    *,
    video_path: str,
    transcript: WordLevelTranscript,
    correlation_id: str,
    video_width: int = 0,
    video_height: int = 0,
) -> str:
    """Burn captions into video using Pillow + FFmpeg overlay. Returns output path."""
    logger.info("caption_burn_start", correlation_id=correlation_id, video_path=video_path)

    # Auto-detect dimensions if not provided
    if video_width == 0 or video_height == 0:
        video_width, video_height = _get_video_dimensions(video_path)

    fps = _get_video_fps(video_path)
    font_size = max(int(video_width * 0.065), 48)

    phrases = group_words_into_phrases(transcript.words, max_words=4)

    # Create temp directory for overlay frames
    frames_dir = tempfile.mkdtemp(prefix="caption_frames_")
    output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
    os.close(output_fd)

    try:
        # Build a timeline of (start_sec, end_sec, frame_path) for each word highlight
        segments = []
        for phrase in phrases:
            words = phrase["words"]
            phrase_end = phrase["end"]
            for idx, word in enumerate(words):
                word_start = word.start
                word_end = word.end if idx < len(words) - 1 else phrase_end

                frame_img = _render_caption_frame(
                    text=phrase["text"],
                    highlight_index=idx,
                    words=words,
                    video_width=video_width,
                    video_height=video_height,
                    font_size=font_size,
                )
                frame_path = os.path.join(frames_dir, f"frame_{len(segments):04d}.png")
                frame_img.save(frame_path)
                segments.append((word_start, word_end, frame_path))

        if not segments:
            # No segments to overlay — just copy
            logger.warning("caption_burn_no_segments", correlation_id=correlation_id)
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        # Build FFmpeg filter_complex with timed overlays
        # Strategy: chain overlay filters, each enabled only during its time window
        inputs = ["-i", video_path]
        filter_parts = []
        prev_label = "[0:v]"

        for i, (start, end, frame_path) in enumerate(segments):
            inputs.extend(["-i", frame_path])
            input_idx = i + 1
            out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"
            filter_parts.append(
                f"{prev_label}[{input_idx}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{out_label}"
            )
            prev_label = out_label

        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            output_path,
        ]

        logger.info("caption_burn_ffmpeg_start", correlation_id=correlation_id, segments=len(segments))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode != 0:
            logger.error(
                "caption_burn_ffmpeg_failed",
                correlation_id=correlation_id,
                stderr=result.stderr[-500:],
            )
            raise CaptionRendererError(
                f"FFmpeg failed (exit {result.returncode}): {result.stderr[-200:]}",
                transient=False,
            )

        logger.info("caption_burn_done", correlation_id=correlation_id, output_path=output_path)
        return output_path

    finally:
        # Clean up frame images
        for f in os.listdir(frames_dir):
            try:
                os.unlink(os.path.join(frames_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(frames_dir)
        except OSError:
            pass
