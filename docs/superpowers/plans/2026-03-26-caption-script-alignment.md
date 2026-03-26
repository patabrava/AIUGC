# Caption Script Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Deepgram only for word-level timing, but replace its transcription text with the known-correct script from `seed_data.script` — eliminating German transcription errors in burned captions.

**Architecture:** A new `align_transcript_to_script` function takes the Deepgram `WordLevelTranscript` and the original script string. It tokenizes the script into words, then sequentially aligns each script word to the closest Deepgram word using normalized string similarity. The output is a new `WordLevelTranscript` with correct script text and Deepgram timing. The caption worker calls this function between transcription and caption burning.

**Tech Stack:** Python 3.9+ / difflib.SequenceMatcher (stdlib, no new deps)

---

### Task 1: Script-to-Transcript Alignment Function

**Files:**
- Create: `app/adapters/caption_aligner.py`
- Test: `tests/test_caption_aligner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_caption_aligner.py
"""Tests for caption script alignment."""

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.adapters.caption_aligner import align_transcript_to_script


def test_perfect_match_preserves_timing():
    """When Deepgram matches script exactly, timing is preserved."""
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="Juli", start=0.8, end=1.1),
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="Ab Juli wird",
    )
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli", "wird"]
    assert result.words[0].start == 0.5
    assert result.words[2].end == 1.4


def test_misspelled_word_gets_corrected():
    """Deepgram mishears a German word — script word replaces it."""
    transcript = WordLevelTranscript(
        words=[
            Word(word="Entlastungs", start=1.0, end=1.5),
            Word(word="budget", start=1.5, end=2.0),
        ],
        full_text="Entlastungs budget",
    )
    script = "Entlastungsbudget"
    result = align_transcript_to_script(transcript=transcript, script=script)
    # The single script word should use timing spanning both Deepgram words
    assert len(result.words) == 1
    assert result.words[0].word == "Entlastungsbudget"
    assert result.words[0].start == 1.0
    assert result.words[0].end == 2.0


def test_extra_deepgram_word_is_dropped():
    """Deepgram hallucinates an extra word not in script."""
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="uh", start=0.8, end=0.9),
            Word(word="Juli", start=1.0, end=1.3),
        ],
        full_text="Ab uh Juli",
    )
    script = "Ab Juli"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli"]
    assert result.words[0].start == 0.5
    assert result.words[1].start == 1.0


def test_missing_deepgram_word_gets_interpolated():
    """Deepgram misses a word — timing is interpolated from neighbors."""
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="Ab wird",
    )
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli", "wird"]
    # Interpolated "Juli" should fill the gap
    assert result.words[1].start == 0.7  # end of "Ab"
    assert result.words[1].end == 1.2  # start of "wird"


def test_empty_transcript_returns_empty():
    """Empty Deepgram result returns empty aligned transcript."""
    transcript = WordLevelTranscript(words=[], full_text="")
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert result.words == []


def test_empty_script_returns_empty():
    """Empty script returns empty aligned transcript."""
    transcript = WordLevelTranscript(
        words=[Word(word="Ab", start=0.5, end=0.7)],
        full_text="Ab",
    )
    script = ""
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert result.words == []


def test_real_german_sentence_alignment():
    """Realistic German caption alignment with typical Deepgram errors."""
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="Juli", start=0.8, end=1.1),
            Word(word="2026", start=1.2, end=1.8),
            Word(word="wird", start=1.9, end=2.1),
            Word(word="deine", start=2.2, end=2.5),
            Word(word="häusliche", start=2.6, end=3.1),
            Word(word="Pflege", start=3.2, end=3.6),
            Word(word="mit", start=3.7, end=3.8),
            Word(word="dem", start=3.9, end=4.0),
            Word(word="neuen", start=4.1, end=4.4),
            # Deepgram splits compound word
            Word(word="Entlastungs", start=4.5, end=5.0),
            Word(word="budget", start=5.0, end=5.4),
            Word(word="flexibler", start=5.5, end=6.0),
        ],
        full_text="Ab Juli 2026 wird deine häusliche Pflege mit dem neuen Entlastungs budget flexibler",
    )
    script = "Ab Juli 2026 wird deine häusliche Pflege mit dem neuen Entlastungsbudget flexibler."
    result = align_transcript_to_script(transcript=transcript, script=script)

    words = [w.word for w in result.words]
    assert "Entlastungsbudget" in words
    assert "Entlastungs" not in words
    assert "budget" not in words
    # Punctuation stripped for caption display
    assert words[-1] == "flexibler"
    assert len(result.words) == 12  # 13 deepgram words → 12 script words
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caption_aligner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.caption_aligner'`

- [ ] **Step 3: Write the alignment implementation**

```python
# app/adapters/caption_aligner.py
"""Align Deepgram word-level transcription to the known-correct script.

Deepgram provides accurate timing but can misspell German compound words
or split them incorrectly. This module replaces Deepgram's text with the
original script while preserving Deepgram's word-level timestamps.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.logging import get_logger

logger = get_logger(__name__)


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """Normalized similarity score between two strings."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _strip_trailing_punctuation(word: str) -> str:
    """Remove trailing punctuation from caption words (periods, commas, etc.)."""
    return re.sub(r"[.,!?;:]+$", "", word)


def align_transcript_to_script(
    *,
    transcript: WordLevelTranscript,
    script: str,
    similarity_threshold: float = 0.4,
) -> WordLevelTranscript:
    """Align Deepgram transcript words to the known script.

    Uses sequential matching: walks through script words in order,
    consuming Deepgram words that best match. Handles:
    - Misspelled words (replaced with script text)
    - Split compound words (merged timing)
    - Extra Deepgram words (skipped)
    - Missing Deepgram words (timing interpolated)

    Args:
        transcript: Deepgram word-level transcription result.
        script: The original script text the character was supposed to say.
        similarity_threshold: Minimum similarity to consider a match.

    Returns:
        New WordLevelTranscript with script words and Deepgram timing.
    """
    if not transcript.words or not script.strip():
        return WordLevelTranscript(words=[], full_text="")

    script_words = script.split()
    dg_words = transcript.words
    aligned: list[Word] = []

    dg_idx = 0

    for script_word in script_words:
        clean_script = _strip_trailing_punctuation(script_word)
        norm_script = _normalize(clean_script)

        if dg_idx >= len(dg_words):
            # Deepgram ran out of words — interpolate from last aligned word
            if aligned:
                aligned.append(Word(
                    word=clean_script,
                    start=aligned[-1].end,
                    end=aligned[-1].end + 0.3,
                ))
            continue

        # Try direct match at current position
        best_score = _similarity(dg_words[dg_idx].word, clean_script)
        best_idx = dg_idx

        # Look ahead up to 3 positions for a better match
        for lookahead in range(1, min(4, len(dg_words) - dg_idx)):
            score = _similarity(dg_words[dg_idx + lookahead].word, clean_script)
            if score > best_score:
                best_score = score
                best_idx = lookahead + dg_idx

        if best_score >= similarity_threshold:
            # Good match found — use script word with Deepgram timing
            aligned.append(Word(
                word=clean_script,
                start=dg_words[best_idx].start,
                end=dg_words[best_idx].end,
            ))
            dg_idx = best_idx + 1
        else:
            # No good single-word match — check if Deepgram split a compound word.
            # Try merging current + next Deepgram words and compare.
            merged = False
            for merge_count in range(2, min(5, len(dg_words) - dg_idx + 1)):
                merged_text = "".join(
                    dg_words[dg_idx + j].word for j in range(merge_count)
                )
                if _similarity(merged_text, clean_script) >= similarity_threshold:
                    aligned.append(Word(
                        word=clean_script,
                        start=dg_words[dg_idx].start,
                        end=dg_words[dg_idx + merge_count - 1].end,
                    ))
                    dg_idx += merge_count
                    merged = True
                    break

            if not merged:
                # Can't find a match — interpolate timing
                if aligned:
                    gap_start = aligned[-1].end
                else:
                    gap_start = dg_words[dg_idx].start
                gap_end = dg_words[dg_idx].start if dg_idx < len(dg_words) else gap_start + 0.3
                if gap_end <= gap_start:
                    gap_end = gap_start + 0.3
                aligned.append(Word(
                    word=clean_script,
                    start=gap_start,
                    end=gap_end,
                ))

    full_text = " ".join(w.word for w in aligned)

    logger.info(
        "script_alignment_complete",
        script_words=len(script_words),
        deepgram_words=len(dg_words),
        aligned_words=len(aligned),
        consumed_dg_words=dg_idx,
    )

    return WordLevelTranscript(words=aligned, full_text=full_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caption_aligner.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/caption_aligner.py tests/test_caption_aligner.py
git commit -m "feat: add caption script alignment — correct Deepgram text with known script"
```

---

### Task 2: Integrate Alignment into Caption Worker

**Files:**
- Modify: `workers/caption_worker.py:57-98`

- [ ] **Step 1: Write failing test**

```python
# tests/test_caption_worker_alignment.py
"""Test that caption worker uses script alignment."""

from unittest.mock import MagicMock, patch

from app.adapters.deepgram_client import Word, WordLevelTranscript


def test_caption_worker_aligns_transcript_to_script():
    """The caption worker should align Deepgram output to seed_data.script."""
    from workers.caption_worker import _process_caption_post

    fake_post = {
        "id": "test-post-id",
        "batch_id": "test-batch-id",
        "video_url": "https://example.com/video.mp4",
        "video_metadata": {},
        "seed_data": {"script": "Ab Juli wird"},
    }

    mock_transcript = WordLevelTranscript(
        words=[
            Word(word="ab", start=0.5, end=0.7),
            Word(word="Julie", start=0.8, end=1.1),  # Misspelled
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="ab Julie wird",
    )

    with (
        patch("workers.caption_worker.get_supabase") as mock_sb,
        patch("workers.caption_worker.get_storage_client") as mock_storage,
        patch("workers.caption_worker.get_deepgram_client") as mock_dg,
        patch("workers.caption_worker.burn_captions") as mock_burn,
        patch("workers.caption_worker._mark_caption_completed"),
        patch("workers.caption_worker._check_batch_caption_complete"),
        patch("builtins.open", MagicMock()),
        patch("os.close"),
        patch("os.unlink"),
        patch("tempfile.mkstemp", return_value=(0, "/tmp/fake.mp4")),
    ):
        mock_sb.return_value.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        mock_storage.return_value.download_video.return_value = b"fake-video-bytes"
        mock_storage.return_value.upload_video.return_value = {
            "url": "https://example.com/captioned.mp4",
            "storage_key": "test-key",
            "size": 100,
        }
        mock_dg.return_value.transcribe.return_value = mock_transcript
        mock_burn.return_value = "/tmp/fake_output.mp4"

        _process_caption_post(fake_post)

        # Verify burn_captions received the aligned transcript
        burn_call = mock_burn.call_args
        transcript_arg = burn_call.kwargs.get("transcript") or burn_call[1].get("transcript")
        aligned_words = [w.word for w in transcript_arg.words]
        assert "Juli" in aligned_words, f"Expected 'Juli' in aligned words, got {aligned_words}"
        assert "Julie" not in aligned_words
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_caption_worker_alignment.py -v`
Expected: FAIL — `"Julie"` is still in the transcript because alignment isn't wired in yet.

- [ ] **Step 3: Modify caption worker to use alignment**

In `workers/caption_worker.py`, add the import at the top (after existing imports):

```python
from app.adapters.caption_aligner import align_transcript_to_script
```

Then modify `_process_caption_post` — after the `deepgram.transcribe()` call (line 79) and before the empty-transcript check (line 81), insert the alignment step:

```python
        transcript = deepgram.transcribe(audio_bytes=video_bytes, correlation_id=correlation_id)

        # Align Deepgram transcription to the known script to fix misspellings
        seed_data = post.get("seed_data") or {}
        original_script = seed_data.get("script") or seed_data.get("dialog_script") or ""
        if original_script and transcript.words:
            transcript = align_transcript_to_script(
                transcript=transcript,
                script=original_script,
            )
            logger.info(
                "caption_transcript_aligned",
                correlation_id=correlation_id,
                post_id=post_id,
                aligned_word_count=len(transcript.words),
            )

        if not transcript.words:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caption_worker_alignment.py tests/test_caption_aligner.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/caption_worker.py tests/test_caption_worker_alignment.py
git commit -m "feat: integrate script alignment into caption worker"
```
