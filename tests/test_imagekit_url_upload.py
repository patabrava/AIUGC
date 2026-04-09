"""Tests for Cloudflare R2 storage uploads."""

from types import SimpleNamespace

from app.adapters import storage_client as storage_client_module


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


class FakeHttpResponse:
    def __init__(self, content: bytes, content_type: str = "video/mp4"):
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


class FakeHttpClient:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = []

    def get(self, url: str):
        self.calls.append(url)
        return FakeHttpResponse(self.content)


def _make_fake_settings():
    return SimpleNamespace(
        cloudflare_r2_account_id="acct123",
        cloudflare_r2_access_key_id="key123",
        cloudflare_r2_secret_access_key="secret123",
        cloudflare_r2_bucket_name="ugc-videos",
        cloudflare_r2_public_base_url="https://cdn.example.com",
        cloudflare_r2_region="auto",
        cloudflare_r2_endpoint_url=None,
        cloudflare_r2_video_prefix="Lippe Lift Studio/videos",
        cloudflare_r2_image_prefix="Lippe Lift Studio/images",
    )


def _build_client(monkeypatch, fake_s3):
    monkeypatch.setattr(storage_client_module, "get_settings", _make_fake_settings)
    monkeypatch.setattr(storage_client_module.boto3, "client", lambda *args, **kwargs: fake_s3)
    storage_client_module.StorageClient._instance = None
    return storage_client_module.get_storage_client()


def test_upload_video_to_cloudflare_r2(monkeypatch):
    """Verify the storage adapter uploads video bytes to Cloudflare R2."""
    fake_s3 = FakeS3Client()
    client = _build_client(monkeypatch, fake_s3)
    result = client.upload_video(
        video_bytes=b"video-bytes",
        file_name="test_url_upload.mp4",
        correlation_id="test_url_upload_001",
    )

    assert fake_s3.calls, "Cloudflare R2 client did not receive put_object call"
    uploaded = fake_s3.calls[0]
    assert uploaded["Bucket"] == "ugc-videos"
    assert uploaded["ContentType"] == "video/mp4"
    assert result["storage_provider"] == "cloudflare_r2"
    assert result["storage_key"].startswith("Lippe Lift Studio/videos/")
    assert result["url"].startswith("https://cdn.example.com/Lippe Lift Studio/videos/")


def test_ingest_video_from_public_url(monkeypatch):
    """Verify source URL ingestion downloads bytes and stores them in Cloudflare R2."""
    fake_s3 = FakeS3Client()
    client = _build_client(monkeypatch, fake_s3)
    client._http_client = FakeHttpClient(b"remote-video-bytes")

    result = client.upload_video_from_url(
        video_url="https://provider.example.com/video.mp4",
        file_name="ingested.mp4",
        correlation_id="test_url_ingest_001",
    )

    assert client._http_client.calls == ["https://provider.example.com/video.mp4"]
    assert fake_s3.calls[0]["Body"] == b"remote-video-bytes"
    assert result["storage_key"].startswith("Lippe Lift Studio/videos/")


def test_upload_image_to_cloudflare_r2(monkeypatch):
    """Verify the storage adapter uploads image bytes to Cloudflare R2."""
    fake_s3 = FakeS3Client()
    client = _build_client(monkeypatch, fake_s3)
    result = client.upload_image(
        image_bytes=b"image-bytes",
        file_name="cover.png",
        correlation_id="test_image_upload_001",
    )

    assert fake_s3.calls, "Cloudflare R2 client did not receive put_object call"
    uploaded = fake_s3.calls[0]
    assert uploaded["Bucket"] == "ugc-videos"
    assert uploaded["ContentType"] == "image/png"
    assert result["storage_provider"] == "cloudflare_r2"
    assert result["storage_key"].startswith("Lippe Lift Studio/images/")
    assert result["url"].startswith("https://cdn.example.com/Lippe Lift Studio/images/")
