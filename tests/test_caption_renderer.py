import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.adapters.caption_renderer import (
    group_words_into_phrases,
    burn_captions,
    CaptionRendererError,
    _render_caption_frame,
)


class TestPhraseGrouping:
    def test_groups_into_chunks(self):
        words = [
            Word("Mach", 0.0, 0.3),
            Word("diesen", 0.35, 0.6),
            Word("Fehler", 0.65, 1.0),
            Word("bei", 1.05, 1.2),
            Word("diesem", 1.25, 1.5),
            Word("Thema", 1.55, 1.9),
            Word("nicht", 1.95, 2.2),
        ]
        phrases = group_words_into_phrases(words, max_words=3)
        assert len(phrases) == 3
        assert phrases[0]["text"] == "Mach diesen Fehler"
        assert phrases[0]["start"] == 0.0
        assert phrases[0]["end"] == 1.0
        assert phrases[1]["text"] == "bei diesem Thema"
        assert phrases[2]["text"] == "nicht"

    def test_empty_words(self):
        phrases = group_words_into_phrases([], max_words=3)
        assert phrases == []

    def test_single_word(self):
        words = [Word("Hallo", 0.0, 0.5)]
        phrases = group_words_into_phrases(words, max_words=3)
        assert len(phrases) == 1
        assert phrases[0]["text"] == "Hallo"


class TestCaptionFrameRendering:
    def test_render_produces_rgba_image(self):
        words = [Word("Hallo", 0.0, 0.5), Word("Welt", 0.6, 1.0)]
        img = _render_caption_frame(
            text="Hallo Welt",
            highlight_index=0,
            words=words,
            video_width=1080,
            video_height=1920,
            font_size=70,
        )
        assert img.mode == "RGBA"
        assert img.size == (1080, 1920)

    def test_render_different_highlights(self):
        words = [Word("A", 0.0, 0.3), Word("B", 0.4, 0.6)]
        img0 = _render_caption_frame("A B", 0, words, 1080, 1920, 70)
        img1 = _render_caption_frame("A B", 1, words, 1080, 1920, 70)
        # Different highlight index should produce different images
        assert img0.tobytes() != img1.tobytes()


class TestBurnCaptions:
    @patch("app.adapters.caption_renderer.subprocess.run")
    @patch("app.adapters.caption_renderer._get_video_fps", return_value=30.0)
    @patch("app.adapters.caption_renderer._get_video_dimensions", return_value=(1080, 1920))
    def test_burn_success(self, mock_dims, mock_fps, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        transcript = WordLevelTranscript(words=[Word("Test", 0.0, 0.5)], full_text="Test")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake_video_data")
            input_path = f.name
        try:
            output_path = burn_captions(video_path=input_path, transcript=transcript, correlation_id="test_burn")
            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert "ffmpeg" in cmd[0]
            assert "-filter_complex" in cmd
        finally:
            os.unlink(input_path)
            if output_path and os.path.exists(output_path):
                os.unlink(output_path)

    @patch("app.adapters.caption_renderer.subprocess.run")
    @patch("app.adapters.caption_renderer._get_video_fps", return_value=30.0)
    @patch("app.adapters.caption_renderer._get_video_dimensions", return_value=(1080, 1920))
    def test_burn_ffmpeg_failure_raises(self, mock_dims, mock_fps, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="codec error")
        transcript = WordLevelTranscript(words=[Word("Test", 0.0, 0.5)], full_text="Test")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            input_path = f.name
        try:
            with pytest.raises(CaptionRendererError):
                burn_captions(video_path=input_path, transcript=transcript, correlation_id="test_fail")
        finally:
            os.unlink(input_path)
