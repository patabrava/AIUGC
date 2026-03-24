"""Tests for video prompt audit recording."""

import uuid
from unittest.mock import patch, MagicMock

from app.features.videos.prompt_audit import record_prompt_audit


class TestRecordPromptAudit:
    """Tests for record_prompt_audit function."""

    @patch("app.features.videos.prompt_audit.get_supabase")
    def test_writes_audit_row(self, mock_get_supabase):
        """Audit row is inserted with all fields."""
        mock_client = MagicMock()
        mock_get_supabase.return_value.client = mock_client
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        post_id = str(uuid.uuid4())
        operation_id = "op_123"

        record_prompt_audit(
            post_id=post_id,
            operation_id=operation_id,
            provider="veo_3_1",
            prompt_text="Full prompt text here",
            negative_prompt="no watermarks",
            prompt_path="veo_prompt",
            aspect_ratio="9:16",
            resolution="720p",
            requested_seconds=8,
            correlation_id="gen_video_abc",
        )

        mock_client.table.assert_called_once_with("video_prompt_audit")
        insert_call = mock_client.table.return_value.insert
        insert_call.assert_called_once()
        row = insert_call.call_args[0][0]
        assert row["post_id"] == post_id
        assert row["operation_id"] == operation_id
        assert row["prompt_text"] == "Full prompt text here"
        assert row["negative_prompt"] == "no watermarks"
        assert row["prompt_path"] == "veo_prompt"
        assert row["provider"] == "veo_3_1"
        assert row["aspect_ratio"] == "9:16"
        assert row["resolution"] == "720p"
        assert row["requested_seconds"] == 8
        assert row["correlation_id"] == "gen_video_abc"
        assert row["batch_id"] is None

    @patch("app.features.videos.prompt_audit.get_supabase")
    def test_includes_batch_id_when_provided(self, mock_get_supabase):
        """batch_id is included when passed."""
        mock_client = MagicMock()
        mock_get_supabase.return_value.client = mock_client
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        batch_id = str(uuid.uuid4())

        record_prompt_audit(
            post_id=str(uuid.uuid4()),
            operation_id="op_456",
            provider="veo_3_1",
            prompt_text="prompt",
            negative_prompt=None,
            prompt_path="optimized_prompt",
            aspect_ratio="9:16",
            resolution="720p",
            requested_seconds=8,
            correlation_id="gen_all_abc",
            batch_id=batch_id,
        )

        row = mock_client.table.return_value.insert.call_args[0][0]
        assert row["batch_id"] == batch_id

    @patch("app.features.videos.prompt_audit.get_supabase")
    def test_does_not_raise_on_db_failure(self, mock_get_supabase):
        """Audit failure is swallowed — does not block video submission."""
        mock_client = MagicMock()
        mock_get_supabase.return_value.client = mock_client
        mock_client.table.return_value.insert.return_value.execute.side_effect = Exception("DB down")

        # Should not raise
        record_prompt_audit(
            post_id=str(uuid.uuid4()),
            operation_id="op_789",
            provider="veo_3_1",
            prompt_text="prompt",
            negative_prompt=None,
            prompt_path="veo_prompt",
            aspect_ratio="9:16",
            resolution="720p",
            requested_seconds=8,
            correlation_id="gen_video_xyz",
        )


from app.features.videos.handlers import _build_provider_prompt_request


class TestBuildProviderPromptRequest:
    """Tests that _build_provider_prompt_request returns prompt_path."""

    def test_veo_returns_veo_prompt_path(self):
        """When veo_prompt exists, prompt_path is 'veo_prompt'."""
        video_prompt = {
            "veo_prompt": "full veo prompt text",
            "optimized_prompt": "optimized fallback",
            "veo_negative_prompt": "no watermarks",
        }
        result = _build_provider_prompt_request(video_prompt, "veo_3_1")
        assert result["prompt_path"] == "veo_prompt"
        assert result["prompt_text"] == "full veo prompt text"

    def test_veo_falls_back_to_optimized_prompt_path(self):
        """When veo_prompt is missing, falls back to optimized_prompt."""
        video_prompt = {
            "optimized_prompt": "optimized text",
            "veo_negative_prompt": "no watermarks",
        }
        result = _build_provider_prompt_request(video_prompt, "veo_3_1")
        assert result["prompt_path"] == "optimized_prompt"
        assert result["prompt_text"] == "optimized text"

    def test_veo_falls_back_to_full_prompt_text(self):
        """When both are missing, falls back to full_prompt_text."""
        video_prompt = {
            "character": "A woman",
            "action": "speaks to camera",
        }
        result = _build_provider_prompt_request(video_prompt, "veo_3_1")
        assert result["prompt_path"] == "full_prompt_text_fallback"
        assert len(result["prompt_text"]) > 0

    def test_sora_returns_optimized_prompt_path(self):
        """Sora uses optimized_prompt when available."""
        video_prompt = {
            "optimized_prompt": "sora optimized text",
        }
        result = _build_provider_prompt_request(video_prompt, "sora_2")
        assert result["prompt_path"] == "sora_optimized_prompt"

    def test_sora_falls_back_to_full_prompt_text(self):
        """Sora falls back to full_prompt_text."""
        video_prompt = {
            "character": "A woman",
            "action": "speaks to camera",
        }
        result = _build_provider_prompt_request(video_prompt, "sora_2")
        assert result["prompt_path"] == "full_prompt_text_fallback"
