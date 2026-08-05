import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

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

    def test_download_video_enforces_an_absolute_wall_clock_deadline(self, monkeypatch):
        from app.adapters import storage_client as module

        class SlowClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                await asyncio.sleep(1)

        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: SlowClient())
        StorageClient._instance = None
        with patch.object(StorageClient, "__init__", lambda self: None):
            client = StorageClient()

        started = time.monotonic()
        with pytest.raises(TimeoutError, match="absolute deadline"):
            client.download_video(
                video_url="https://cdn.example.com/images/reference.png",
                correlation_id="deadline-test",
                timeout_seconds=0.02,
            )
        assert time.monotonic() - started < 0.5

    def test_deadline_download_distinguishes_a_definitive_missing_object(
        self, monkeypatch
    ):
        from app.adapters import storage_client as module

        class NotFoundClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **_kwargs):
                return module.httpx.Response(
                    404,
                    request=module.httpx.Request("GET", url),
                )

        monkeypatch.setattr(
            module.httpx, "AsyncClient", lambda **_kwargs: NotFoundClient()
        )
        StorageClient._instance = None
        with patch.object(StorageClient, "__init__", lambda self: None):
            client = StorageClient()

        with pytest.raises(FileNotFoundError, match="not found"):
            client.download_video(
                video_url="https://cdn.example.com/images/missing.png",
                correlation_id="missing-object-test",
                timeout_seconds=0.1,
            )
