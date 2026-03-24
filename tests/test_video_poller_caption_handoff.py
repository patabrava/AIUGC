"""Verify poller sets caption_pending and batch waits for caption_completed."""
import os
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "service-key")
os.environ.setdefault("SUPABASE_KEY", "service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "account-id")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "access-key")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "secret-key")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "bucket-name")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")
os.environ.setdefault("CRON_SECRET", "cron-secret")

from unittest.mock import patch, MagicMock
from app.core.video_profiles import VIDEO_STATUS_CAPTION_PENDING, VIDEO_STATUS_CAPTION_COMPLETED


class TestPollerCaptionHandoff:
    @patch("workers.video_poller.get_storage_client")
    @patch("workers.video_poller.get_supabase")
    def test_store_completed_video_sets_caption_pending(self, mock_sb_factory, mock_storage):
        """After upload, video_status should be caption_pending, not completed."""
        from workers.video_poller import _store_completed_video

        mock_storage_instance = MagicMock()
        mock_storage_instance.upload_video.return_value = {
            "storage_provider": "cloudflare_r2",
            "storage_key": "test/key.mp4",
            "url": "https://cdn.example.com/test/key.mp4",
            "size": 1024,
            "file_path": "test/key.mp4",
            "file_type": "video/mp4",
            "thumbnail_url": None,
        }
        mock_storage.return_value = mock_storage_instance

        mock_client = MagicMock()
        mock_sb_factory.return_value.client = mock_client
        mock_table = mock_client.table.return_value
        mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "video_metadata": {},
            "batch_id": "batch_123",
        }
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        _store_completed_video(
            post_id="post_123",
            provider="veo",
            video_source=b"fake_video_bytes",
            correlation_id="test_corr",
            provider_metadata={"model": "veo-3.1"},
            existing_metadata={},
        )

        update_calls = mock_table.update.call_args_list
        found = False
        for call in update_calls:
            data = call[0][0]
            if "video_status" in data:
                assert data["video_status"] == VIDEO_STATUS_CAPTION_PENDING
                found = True
        assert found, "No update call set video_status"
