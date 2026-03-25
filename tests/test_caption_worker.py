"""Tests for caption worker polling and processing logic."""

import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock
from app.core.video_profiles import (
    VIDEO_STATUS_CAPTION_PENDING,
    VIDEO_STATUS_CAPTION_PROCESSING,
    VIDEO_STATUS_CAPTION_COMPLETED,
    VIDEO_STATUS_CAPTION_FAILED,
)
from app.adapters.deepgram_client import Word, WordLevelTranscript


class TestProcessCaptionPost:
    @patch("workers.caption_worker.burn_captions")
    @patch("workers.caption_worker.get_deepgram_client")
    @patch("workers.caption_worker.get_storage_client")
    @patch("workers.caption_worker.get_supabase")
    def test_full_caption_pipeline(self, mock_sb_factory, mock_storage, mock_dg, mock_burn):
        from workers.caption_worker import _process_caption_post

        mock_client = MagicMock()
        mock_sb_factory.return_value.client = mock_client
        mock_table = mock_client.table.return_value
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "state": "S5_PROMPTS_BUILT"
        }
        mock_table.select.return_value.eq.return_value.execute.return_value.data = []

        mock_storage_inst = MagicMock()
        mock_storage_inst.download_video.return_value = b"video_bytes"
        mock_storage_inst.upload_video.return_value = {
            "storage_key": "videos/captioned/test.mp4",
            "url": "https://cdn.example.com/videos/captioned/test.mp4",
            "size": 2048,
        }
        mock_storage.return_value = mock_storage_inst

        transcript = WordLevelTranscript(
            words=[Word("Hallo", 0.0, 0.5), Word("Welt", 0.6, 1.0)],
            full_text="Hallo Welt",
        )
        mock_dg_inst = MagicMock()
        mock_dg_inst.transcribe.return_value = transcript
        mock_dg.return_value = mock_dg_inst

        # Create a real temp file so open() works on the burn output path
        fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, b"captioned_video_bytes")
        os.close(fd)
        mock_burn.return_value = tmp_path

        post = {
            "id": "post_123",
            "batch_id": "batch_456",
            "video_url": "https://cdn.example.com/videos/original.mp4",
            "video_metadata": {"storage_key": "videos/original.mp4"},
        }

        try:
            _process_caption_post(post)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        mock_dg_inst.transcribe.assert_called_once()
        mock_burn.assert_called_once()
        mock_storage_inst.upload_video.assert_called_once()

    @patch("workers.caption_worker.get_deepgram_client")
    @patch("workers.caption_worker.get_storage_client")
    @patch("workers.caption_worker.get_supabase")
    def test_empty_transcript_skips_burn(self, mock_sb_factory, mock_storage, mock_dg):
        from workers.caption_worker import _process_caption_post

        mock_client = MagicMock()
        mock_sb_factory.return_value.client = mock_client
        mock_table = mock_client.table.return_value
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "state": "S5_PROMPTS_BUILT"
        }
        mock_table.select.return_value.eq.return_value.execute.return_value.data = []

        mock_storage_inst = MagicMock()
        mock_storage_inst.download_video.return_value = b"video_bytes"
        mock_storage.return_value = mock_storage_inst

        mock_dg_inst = MagicMock()
        mock_dg_inst.transcribe.return_value = WordLevelTranscript(words=[], full_text="")
        mock_dg.return_value = mock_dg_inst

        post = {
            "id": "post_123",
            "batch_id": "batch_456",
            "video_url": "https://cdn.example.com/videos/original.mp4",
            "video_metadata": {},
        }

        _process_caption_post(post)

        update_calls = mock_table.update.call_args_list
        final_update = update_calls[-1][0][0]
        assert final_update["video_status"] == VIDEO_STATUS_CAPTION_COMPLETED


class TestCaptionRetryLogic:
    @patch("workers.caption_worker.get_deepgram_client")
    @patch("workers.caption_worker.get_storage_client")
    @patch("workers.caption_worker.get_supabase")
    def test_transient_error_resets_to_pending(self, mock_sb_factory, mock_storage, mock_dg):
        from workers.caption_worker import _process_caption_post
        from app.adapters.deepgram_client import DeepgramError

        mock_client = MagicMock()
        mock_sb_factory.return_value.client = mock_client
        mock_table = mock_client.table.return_value
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_storage_inst = MagicMock()
        mock_storage_inst.download_video.return_value = b"video_bytes"
        mock_storage.return_value = mock_storage_inst

        mock_dg_inst = MagicMock()
        mock_dg_inst.transcribe.side_effect = DeepgramError("503 error", transient=True)
        mock_dg.return_value = mock_dg_inst

        post = {
            "id": "post_123",
            "batch_id": "batch_456",
            "video_url": "https://cdn.example.com/videos/original.mp4",
            "video_metadata": {"caption_retry_count": 0},
        }

        _process_caption_post(post)

        update_calls = mock_table.update.call_args_list
        final_update = update_calls[-1][0][0]
        assert final_update["video_status"] == VIDEO_STATUS_CAPTION_PENDING
        assert final_update["video_metadata"]["caption_retry_count"] == 1

    @patch("workers.caption_worker.get_deepgram_client")
    @patch("workers.caption_worker.get_storage_client")
    @patch("workers.caption_worker.get_supabase")
    def test_max_retries_marks_failed(self, mock_sb_factory, mock_storage, mock_dg):
        from workers.caption_worker import _process_caption_post
        from app.adapters.deepgram_client import DeepgramError

        mock_client = MagicMock()
        mock_sb_factory.return_value.client = mock_client
        mock_table = mock_client.table.return_value
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_storage_inst = MagicMock()
        mock_storage_inst.download_video.return_value = b"video_bytes"
        mock_storage.return_value = mock_storage_inst

        mock_dg_inst = MagicMock()
        mock_dg_inst.transcribe.side_effect = DeepgramError("503 error", transient=True)
        mock_dg.return_value = mock_dg_inst

        post = {
            "id": "post_123",
            "batch_id": "batch_456",
            "video_url": "https://cdn.example.com/videos/original.mp4",
            "video_metadata": {"caption_retry_count": 2},
        }

        _process_caption_post(post)

        update_calls = mock_table.update.call_args_list
        final_update = update_calls[-1][0][0]
        assert final_update["video_status"] == VIDEO_STATUS_CAPTION_FAILED
