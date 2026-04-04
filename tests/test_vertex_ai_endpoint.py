"""Tests for the Vertex AI video endpoint."""

import base64
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.features.videos import handlers as video_handlers


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(video_handlers.router)
    return TestClient(app)


def test_vertex_endpoint_accepts_text_payload(monkeypatch):
    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            return {
                "operation_id": "op-text-1",
                "status": "submitted",
                "done": False,
                "provider_model": "veo-3.1-generate-001",
            }

    monkeypatch.setattr(
        "app.features.videos.handlers.get_vertex_ai_client",
        lambda: FakeVertexClient(),
    )
    client = _build_test_client()
    response = client.post(
        "/videos/vertex",
        json={
            "mode": "text",
            "prompt": "A cinematic product intro in a modern studio.",
            "aspect_ratio": "9:16",
            "duration_seconds": 8,
        },
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["operation_id"] == "op-text-1"
    assert payload["provider"] == "vertex_ai"
    assert payload["status"] == "submitted"


def test_vertex_endpoint_accepts_image_payload(monkeypatch):
    class FakeVertexClient:
        def submit_image_video(self, **kwargs):
            return {
                "operation_id": "op-image-1",
                "status": "submitted",
                "done": False,
                "provider_model": "veo-3.1-generate-001",
            }

    monkeypatch.setattr(
        "app.features.videos.handlers.get_vertex_ai_client",
        lambda: FakeVertexClient(),
    )
    image_bytes = b"image-bytes"
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    client = _build_test_client()
    response = client.post(
        "/videos/vertex",
        json={
            "mode": "image",
            "prompt": "A cinematic reveal.",
            "aspect_ratio": "16:9",
            "duration_seconds": 8,
            "image_base64": image_b64,
            "image_mime_type": "image/jpeg",
        },
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["operation_id"] == "op-image-1"
    assert payload["provider"] == "vertex_ai"
    assert payload["status"] == "submitted"
