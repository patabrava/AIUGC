"""Tests for Cloudflare R2 storage uploads."""

from types import SimpleNamespace
from hashlib import sha256

from app.adapters import storage_client as storage_client_module


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)

    def head_object(self, *, Bucket, Key):
        uploaded = next(
            call for call in reversed(self.calls) if call["Bucket"] == Bucket and call["Key"] == Key
        )
        return {
            "ContentLength": len(uploaded["Body"]),
            "ContentType": uploaded["ContentType"],
            "Metadata": uploaded.get("Metadata") or {},
        }


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
    assert uploaded["Metadata"]["sha256"] == sha256(b"video-bytes").hexdigest()
    assert result["storage_provider"] == "cloudflare_r2"
    assert result["storage_key"].startswith("Lippe Lift Studio/videos/")
    assert result["url"].startswith("https://cdn.example.com/Lippe%20Lift%20Studio/videos/")
    assert result["sha256"] == sha256(b"video-bytes").hexdigest()

    verification = client.verify_video_upload(
        storage_key=result["storage_key"],
        expected_size=len(b"video-bytes"),
        expected_sha256=sha256(b"video-bytes").hexdigest(),
    )
    assert verification["passed"] is True


def test_verify_video_upload_fails_closed_for_remote_metadata_mismatch(monkeypatch):
    fake_s3 = FakeS3Client()
    client = _build_client(monkeypatch, fake_s3)
    result = client.upload_video(video_bytes=b"video-bytes", file_name="mismatch.mp4")
    fake_s3.calls[-1]["Metadata"]["sha256"] = "0" * 64

    verification = client.verify_video_upload(
        storage_key=result["storage_key"],
        expected_size=len(b"video-bytes"),
        expected_sha256=sha256(b"video-bytes").hexdigest(),
    )

    assert verification["passed"] is False
    assert verification["failure_reasons"] == ["sha256_mismatch"]


def test_prepare_video_upload_is_content_addressed_and_stable(monkeypatch):
    client = _build_client(monkeypatch, FakeS3Client())

    first = client.prepare_video_upload(
        file_name="semantic ugc final.mp4",
        expected_size=123,
        expected_sha256="a" * 64,
    )
    second = client.prepare_video_upload(
        file_name="semantic ugc final.mp4",
        expected_size=123,
        expected_sha256="a" * 64,
    )

    assert first == second
    assert first["storage_key"].endswith("aaaaaaaaaaaaaaaa_semantic-ugc-final.mp4")
    assert first["sha256"] == "a" * 64


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
    assert result["url"].startswith("https://cdn.example.com/Lippe%20Lift%20Studio/images/")
