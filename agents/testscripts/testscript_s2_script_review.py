"""
S2 script review testscript.
Verifies per-post approve/remove gating before S2 -> S4 and exclusion after removal.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
import app.adapters.supabase_client as supabase_module
import app.features.batches.handlers as batch_handlers
import app.features.posts.handlers as post_handlers
import app.features.topics.queries as topics_queries


BASE_FIXTURE = {
    "batches": {
        "batch-s2": {
            "id": "batch-s2",
            "brand": "Script Review Fixture",
            "state": "S2_SEEDED",
            "post_type_counts": {"value": 2, "lifestyle": 0, "product": 0},
            "created_at": "2026-03-16T09:00:00Z",
            "updated_at": "2026-03-16T09:00:00Z",
            "archived": False,
        }
    },
    "posts": {
        "post-1": {
            "id": "post-1",
            "batch_id": "batch-s2",
            "post_type": "value",
            "topic_title": "First approved script",
            "topic_rotation": "Fallback rotation one",
            "topic_cta": "CTA one",
            "spoken_duration": 12.0,
            "seed_data": {
                "script": "Approved script body",
                "description": "First post description",
                "script_review_status": "pending",
            },
            "video_prompt_json": None,
            "video_status": "pending",
            "created_at": "2026-03-16T09:00:00Z",
            "updated_at": "2026-03-16T09:00:00Z",
        },
        "post-2": {
            "id": "post-2",
            "batch_id": "batch-s2",
            "post_type": "value",
            "topic_title": "Second removed script",
            "topic_rotation": "Fallback rotation two",
            "topic_cta": "CTA two",
            "spoken_duration": 11.0,
            "seed_data": {
                "script": "Removed script body",
                "description": "Second post description",
                "script_review_status": "pending",
            },
            "video_prompt_json": None,
            "video_status": "pending",
            "created_at": "2026-03-16T09:00:00Z",
            "updated_at": "2026-03-16T09:00:00Z",
        },
    },
}


class FakeTable:
    def __init__(self, store: dict, name: str):
        self.store = store
        self.name = name
        self.filters = []
        self.operation = "select"
        self.columns = None
        self.payload = None
        self._count = None

    def select(self, *columns, count=None):
        self.operation = "select"
        self.columns = columns
        self._count = count
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, _value):
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def execute(self):
        rows = list(self.store[self.name].values())
        for column, value in self.filters:
            rows = [row for row in rows if row.get(column) == value]

        if self.operation == "update":
            updated = []
            for row in rows:
                row.update(copy.deepcopy(self.payload))
                updated.append(copy.deepcopy(row))
            return SimpleNamespace(data=updated, count=len(updated))

        selected = [self._select_columns(row) for row in rows]
        return SimpleNamespace(data=selected, count=len(selected) if self._count == "exact" else None)

    def _select_columns(self, row):
        if not self.columns or self.columns == ("*",):
            return copy.deepcopy(row)

        selected = {}
        for column in self.columns:
            if isinstance(column, str) and "," in column:
                for split_column in [item.strip() for item in column.split(",")]:
                    selected[split_column] = copy.deepcopy(row.get(split_column))
            else:
                selected[column] = copy.deepcopy(row.get(column))
        return selected


class FakeSupabaseClient:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name: str):
        return FakeTable(self.store, name)


class FakeSupabaseAdapter:
    def __init__(self, store: dict):
        self.client = FakeSupabaseClient(store)

    def health_check(self):
        return True


def get_posts_by_batch(store: dict, batch_id: str):
    return [copy.deepcopy(post) for post in store["posts"].values() if post["batch_id"] == batch_id]


def get_batch_by_id(store: dict, batch_id: str):
    return copy.deepcopy(store["batches"][batch_id])


def get_batch_posts_summary(store: dict, batch_id: str):
    posts = get_posts_by_batch(store, batch_id)
    return {
        "posts_count": len(posts),
        "posts_by_state": {store["batches"][batch_id]["state"]: len(posts)},
    }


def update_batch_state(store: dict, batch_id: str, target_state):
    state_value = target_state.value if hasattr(target_state, "value") else target_state
    store["batches"][batch_id]["state"] = state_value
    store["batches"][batch_id]["updated_at"] = "2026-03-16T09:05:00Z"
    return copy.deepcopy(store["batches"][batch_id])


def install_fakes(store: dict):
    fake_supabase = FakeSupabaseAdapter(store)

    main_module.get_supabase = lambda: fake_supabase
    supabase_module.get_supabase = lambda: fake_supabase
    post_handlers.get_supabase = lambda: fake_supabase
    batch_handlers.get_batch_by_id = lambda batch_id: get_batch_by_id(store, batch_id)
    batch_handlers.get_batch_posts_summary = lambda batch_id: get_batch_posts_summary(store, batch_id)
    batch_handlers.update_batch_state = lambda batch_id, target_state: update_batch_state(store, batch_id, target_state)
    topics_queries.get_posts_by_batch = lambda batch_id: get_posts_by_batch(store, batch_id)


def main():
    store = copy.deepcopy(BASE_FIXTURE)
    install_fakes(store)

    with TestClient(main_module.app) as client:
        html = client.get("/batches/batch-s2", headers={"accept": "text/html"}).text
        assert "Approve Script" in html
        assert "Remove Script" in html
        assert "Approved 0 / Removed 0 / Pending 2" in html
        assert 'hx-put="/batches/batch-s2/approve-scripts"' in html and "disabled" in html

        blocked = client.put("/batches/batch-s2/approve-scripts")
        assert blocked.status_code == 409, blocked.text
        assert "approved or removed" in blocked.text

        approved = client.put("/posts/post-1/script-review", json={"action": "approved"})
        assert approved.status_code == 200, approved.text

        removed = client.put("/posts/post-2/script-review", json={"action": "removed"})
        assert removed.status_code == 200, removed.text

        html = client.get("/batches/batch-s2", headers={"accept": "text/html"}).text
        assert "Approved 1 / Removed 1 / Pending 0" in html
        assert "Removed From Batch" in html

        advanced = client.put("/batches/batch-s2/approve-scripts")
        assert advanced.status_code == 200, advanced.text
        assert store["batches"]["batch-s2"]["state"] == "S4_SCRIPTED"

        html = client.get("/batches/batch-s2", headers={"accept": "text/html"}).text
        assert "excluded from prompt building and video generation" in html
        assert "Prompt Needed" in html

        removed_prompt = client.post("/posts/post-2/build-prompt")
        assert removed_prompt.status_code == 422, removed_prompt.text
        assert "Removed posts cannot build video prompts" in removed_prompt.text

    print("TS-S2-script-review: PASS")
    print("  initial batch approval blocked until all posts reviewed")
    print("  per-post approve/remove actions persisted")
    print("  removed posts excluded after S4 transition")
    print("  removed posts rejected by build-prompt endpoint")


if __name__ == "__main__":
    main()
