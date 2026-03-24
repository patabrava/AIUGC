"""Caption renderer — ASS subtitle generation + FFmpeg burn-in."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.logging import get_logger

logger = get_logger(__name__)


class CaptionRendererError(Exception):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def group_words_into_phrases(words: list[Word], *, max_words: int = 4) -> list[dict[str, Any]]:
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


def _format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_highlighted_phrase(phrase: dict[str, Any]) -> list[str]:
    lines = []
    words = phrase["words"]
    phrase_end = phrase["end"]
    for idx, word in enumerate(words):
        word_start = word.start
        word_end = word.end if idx < len(words) - 1 else phrase_end
        parts = []
        for j, w in enumerate(words):
            if j == idx:
                parts.append(r"{\c&H00FFFFFF&\b1}" + w.word + r"{\r}")
            else:
                parts.append(r"{\c&H808080&}" + w.word + r"{\r}")
        text = " ".join(parts)
        start_ts = _format_ass_time(word_start)
        end_ts = _format_ass_time(word_end)
        lines.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}")
    return lines


def generate_ass_content(
    transcript: WordLevelTranscript, *, video_width: int = 1080, video_height: int = 1920
) -> str:
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
    phrases = group_words_into_phrases(transcript.words, max_words=4)
    dialogue_lines = []
    for phrase in phrases:
        dialogue_lines.extend(_build_highlighted_phrase(phrase))
    return header + "\n".join(dialogue_lines) + "\n"


def burn_captions(
    *,
    video_path: str,
    transcript: WordLevelTranscript,
    correlation_id: str,
    video_width: int = 1080,
    video_height: int = 1920,
) -> str:
    logger.info("caption_burn_start", correlation_id=correlation_id, video_path=video_path)
    ass_content = generate_ass_content(transcript, video_width=video_width, video_height=video_height)
    ass_fd, ass_path = tempfile.mkstemp(suffix=".ass")
    output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
    os.close(ass_fd)
    os.close(output_fd)
    try:
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass={ass_path}",
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(
                "caption_burn_ffmpeg_failed",
                correlation_id=correlation_id,
                stderr=result.stderr[:500],
            )
            raise CaptionRendererError(
                f"FFmpeg failed (exit {result.returncode}): {result.stderr[:200]}",
                transient=False,
            )
        logger.info("caption_burn_done", correlation_id=correlation_id, output_path=output_path)
        return output_path
    finally:
        if os.path.exists(ass_path):
            os.unlink(ass_path)
