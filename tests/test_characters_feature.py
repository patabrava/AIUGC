from __future__ import annotations

import io
import os
from types import SimpleNamespace

from postgrest.exceptions import APIError
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "dummy")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")

from fastapi.testclient import TestClient

from app.features.characters import handlers as character_handlers
from app.features.characters import queries as character_queries
from app.features.characters.schemas import CharacterRecord
from app.main import app


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def maybe_single(self):
        return self

    def insert(self, payload):
        self.payload = payload
        self.rows.append(payload)
        return self

    def update(self, payload):
        self.payload = payload
        if self.rows:
            self.rows[0].update(payload)
        return self

    def execute(self):
        if self.filters:
            rows = [row for row in self.rows if all(row.get(key) == value for key, value in self.filters)]
        else:
            rows = self.rows
        return _FakeResponse(rows[0] if rows else None)


def _fake_supabase(rows):
    table = _FakeTable(rows)
    return SimpleNamespace(client=SimpleNamespace(table=lambda _name: table))


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_get_active_character_returns_record(monkeypatch):
    rows = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Test",
            "front_image_url": "https://cdn/front.png",
            "three_quarter_image_url": "https://cdn/3q.png",
            "profile_image_url": "https://cdn/profile.png",
            "is_active": True,
            "created_at": "2026-05-08T00:00:00Z",
            "updated_at": "2026-05-08T00:00:00Z",
        }
    ]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    result = character_queries.get_active_character()

    assert isinstance(result, CharacterRecord)
    assert result.front_image_url == "https://cdn/front.png"


def test_get_active_character_returns_none_when_table_missing(monkeypatch):
    class _MissingTable:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            raise APIError(
                {
                    "code": "204",
                    "message": "Missing response",
                    "details": "characters relation not found",
                    "hint": None,
                }
            )

    monkeypatch.setattr(
        character_queries,
        "get_supabase",
        lambda: SimpleNamespace(client=SimpleNamespace(table=lambda _name: _MissingTable())),
    )

    assert character_queries.get_active_character() is None


def test_upload_character_persists_three_images(monkeypatch):
    captured_uploads = []

    class _FakeStorage:
        def upload_image(self, **kwargs):
            captured_uploads.append(kwargs)
            return {
                "url": f"https://cdn.example.com/{kwargs['file_name']}",
                "storage_key": f"images/{kwargs['file_name']}",
            }

    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(
        character_handlers.character_queries,
        "upsert_active_character",
        lambda **kwargs: CharacterRecord(
            id="00000000-0000-0000-0000-000000000001",
            name=kwargs["name"],
            front_image_url=kwargs["front_image_url"],
            three_quarter_image_url=kwargs["three_quarter_image_url"],
            profile_image_url=kwargs["profile_image_url"],
            is_active=True,
            created_at="2026-05-08T00:00:00Z",
            updated_at="2026-05-08T00:00:00Z",
        ),
    )

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/character",
        data={"name": "My Avatar"},
        files={
            "front": ("front.png", io.BytesIO(_png_bytes()), "image/png"),
            "three_quarter": ("3q.png", io.BytesIO(_png_bytes()), "image/png"),
            "profile": ("profile.png", io.BytesIO(_png_bytes()), "image/png"),
        },
        follow_redirects=False,
    )

    assert response.status_code in {200, 303}, response.text
    assert {item["file_name"] for item in captured_uploads} == {"front.png", "3q.png", "profile.png"}
    assert all(item["content_type"] == "image/png" for item in captured_uploads)
