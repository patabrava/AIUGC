from unittest.mock import MagicMock, patch
from app.adapters.storage_client import StorageClient


class TestStorageDownload:
    def test_download_video_returns_bytes(self):
        StorageClient._instance = None
        with patch.object(StorageClient, "__init__", lambda self: None):
            client = StorageClient()
        mock_response = MagicMock()
        mock_response.content = b"fake_video_data"
        mock_response.raise_for_status = MagicMock()
        client._http_client = MagicMock()
        client._http_client.get.return_value = mock_response
        result = client.download_video(
            video_url="https://cdn.example.com/videos/test.mp4",
            correlation_id="test_dl",
        )
        assert result == b"fake_video_data"
        client._http_client.get.assert_called_once_with("https://cdn.example.com/videos/test.mp4")
