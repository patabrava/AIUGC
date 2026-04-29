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
    """Get a heavy impact-style font (TikTok/Hormozi look)."""
    font_paths = [
        "/System/Library/Fonts/Supplemental/Impact.ttf",  # macOS — classic TikTok font
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",  # macOS fallback
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",  # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux fallback
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Measure rendered text size."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_caption_layout(
    *,
    draw: ImageDraw.ImageDraw,
    word_text: str,
    video_width: int,
    base_font_size: int,
) -> tuple[ImageFont.FreeTypeFont, int, int, int, int, int]:
    """Pick a single-line caption size that fits including outline and shadow."""
    max_text_width = int(video_width * 0.9)
    absolute_min_font_size = max(int(video_width * 0.04), 24)
    best_font = _get_font(absolute_min_font_size)
    best_font_size = absolute_min_font_size
    best_text_width, best_text_height = _measure_text(draw, word_text, best_font)
    best_outline = max(int(best_font_size * 0.06), 4)
    best_shadow = max(int(best_font_size * 0.04), 3)

    font_size = base_font_size
    while font_size >= absolute_min_font_size:
        font = _get_font(font_size)
        text_width, text_height = _measure_text(draw, word_text, font)
        outline_range = max(int(font_size * 0.06), 4)
        shadow_offset = max(int(font_size * 0.04), 3)
        padded_width = text_width + (outline_range * 2) + shadow_offset
        if padded_width <= max_text_width:
            return font, font_size, text_width, text_height, outline_range, shadow_offset
        best_font = font
        best_font_size = font_size
        best_text_width = text_width
        best_text_height = text_height
        best_outline = outline_range
        best_shadow = shadow_offset
        font_size -= 4

    return best_font, best_font_size, best_text_width, best_text_height, best_outline, best_shadow


def _render_caption_frame(
    text: str,
    highlight_index: int,
    words: list[Word],
    video_width: int,
    video_height: int,
    font_size: int,
    word_index_global: int = 0,
) -> Image.Image:
    """Render a single Hormozi-style caption frame.

    One word at a time, ALL CAPS, centered on screen, with yellow/white
    color cycling and thick black outline.
    """
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Hormozi style: show only the active word, ALL CAPS
    word_text = words[highlight_index].word.upper()
    font, resolved_font_size, text_width, text_height, outline_range, shadow_offset = _fit_caption_layout(
        draw=draw,
        word_text=word_text,
        video_width=video_width,
        base_font_size=font_size,
    )

    # Color cycling: alternate white and yellow based on global word index
    # Every other word gets yellow for visual rhythm
    if word_index_global % 2 == 1:
        fill = (255, 215, 0, 255)  # Yellow #FFD700
    else:
        fill = (255, 255, 255, 255)  # White

    # Position: lower third (75% down), standard TikTok caption zone
    # Above the TikTok UI buttons but clearly in the subtitle area
    padded_width = text_width + (outline_range * 2) + shadow_offset
    padded_height = text_height + (outline_range * 2) + shadow_offset
    x = ((video_width - padded_width) / 2) + outline_range
    y = int(video_height * 0.75) - (padded_height / 2) + outline_range

    for dx in range(-outline_range, outline_range + 1):
        for dy in range(-outline_range, outline_range + 1):
            if dx * dx + dy * dy <= outline_range * outline_range:
                draw.text((x + dx, y + dy), word_text, font=font, fill=(0, 0, 0, 255))

    draw.text((x + shadow_offset, y + shadow_offset), word_text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), word_text, font=font, fill=fill)

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

    font_size = max(int(video_width * 0.1), 72)

    # Create temp directory for overlay frames
    frames_dir = tempfile.mkdtemp(prefix="caption_frames_")
    output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
    os.close(output_fd)

    try:
        # Hormozi style: one word at a time, each as its own frame
        segments = []
        all_words = transcript.words
        for global_idx, word in enumerate(all_words):
            word_start = word.start
            # End time: use next word's start if available, else this word's end
            if global_idx < len(all_words) - 1:
                word_end = all_words[global_idx + 1].start
            else:
                word_end = word.end

            frame_img = _render_caption_frame(
                text=word.word,
                highlight_index=0,
                words=[word],
                video_width=video_width,
                video_height=video_height,
                font_size=font_size,
                word_index_global=global_idx,
            )
            frame_path = os.path.join(frames_dir, f"frame_{global_idx:04d}.png")
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
            "-movflags", "+faststart",
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
