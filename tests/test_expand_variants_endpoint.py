"""Tests for the expand-variants endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from app.features.topics import handlers as topic_handlers


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(topic_handlers.router)
    return TestClient(app)


def test_expand_variants_endpoint_returns_json(monkeypatch):
    """POST /topics/expand-variants returns a JSON summary."""
    monkeypatch.setattr(
        "app.features.topics.handlers.expand_topic_variants",
        lambda **kw: {"generated": 1, "total_existing": 5, "details": [], "topic_registry_id": "t1", "post_type": "value", "target_length_tier": 8},
    )
    monkeypatch.setattr(
        "app.features.topics.handlers.get_topic_registry_by_id",
        lambda tid: {"id": tid, "title": "Test Topic", "post_type": "value"},
    )
    client = _build_test_client()
    response = client.post(
        "/topics/expand-variants",
        json={"topic_registry_id": "topic-1", "count": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] == 1
