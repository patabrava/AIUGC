from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.features.posts import handlers as posts_handlers  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_REVIEW_MIGRATION = (
    ROOT / "supabase/migrations/20260812000000_atomic_script_review.sql"
)


def test_standing_presentation_override_invalidates_prior_visual_snapshots():
    seed_data = {
        "semantic_reference_snapshot": {"contract_hash": "old"},
        "semantic_master_snapshot": {"sha256": "old"},
    }

    posts_handlers._apply_semantic_visual_overrides(
        seed_data,
        submitted_scene_key=None,
        submitted_wardrobe_description=None,
        submitted_presentation_mode="standing_presenter",
    )

    assert seed_data["semantic_presentation_mode"] == "standing_presenter"
    assert "semantic_reference_snapshot" not in seed_data
    assert "semantic_master_snapshot" not in seed_data


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name, operation_log):
        self.storage = storage
        self.table_name = table_name
        self.operation_log = operation_log
        self.filters = []
        self.payload = None
        self.operation = "select"
        self.selected_fields = ""

    def select(self, *fields):
        self.operation = "select"
        self.selected_fields = ",".join(str(field) for field in fields)
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        self.operation_log.append((self.table_name, self.operation, deepcopy(self.payload)))
        rows = self.storage[self.table_name]
        matches = [row for row in rows if all(row.get(key) == value for key, value in self.filters)]
        if self.operation == "update":
            updated = []
            for row in matches:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)
        selected = [deepcopy(row) for row in matches]
        if self.table_name == "posts" and "batch:batches" in self.selected_fields:
            for row in selected:
                row["batch"] = next(
                    (
                        deepcopy(batch)
                        for batch in self.storage.get("batches", [])
                        if batch.get("id") == row.get("batch_id")
                    ),
                    None,
                )
        return _FakeResponse(selected)


class _FakeRpc:
    def __init__(self, storage, operation_log, function_name, payload):
        self.storage = storage
        self.operation_log = operation_log
        self.function_name = function_name
        self.payload = payload

    def execute(self):
        self.operation_log.append(("rpc", self.function_name, deepcopy(self.payload)))
        assert self.function_name == "apply_post_script_review"
        post = next(row for row in self.storage["posts"] if row["id"] == self.payload["p_post_id"])
        post["seed_data"] = deepcopy(self.payload["p_seed_data"])
        post["video_prompt_json"] = deepcopy(self.payload["p_video_prompt_json"])
        if self.payload.get("p_video_status") is not None:
            post["video_status"] = self.payload["p_video_status"]
        if self.payload.get("p_post_type"):
            post["post_type"] = self.payload["p_post_type"]

        batch = next(row for row in self.storage["batches"] if row["id"] == post["batch_id"])
        statuses = [
            row.get("seed_data", {}).get("script_review_status", "pending")
            for row in self.storage["posts"]
            if row.get("batch_id") == post["batch_id"]
        ]
        if (
            batch.get("state") == "S2_SEEDED"
            and any(value == "approved" for value in statuses)
            and all(value in {"approved", "removed"} for value in statuses)
        ):
            batch["state"] = "S4_SCRIPTED"
        return _FakeResponse({"batch_state": batch.get("state")})


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage
        self.operation_log = []

    def table(self, table_name):
        return _FakeTable(self.storage, table_name, self.operation_log)

    def rpc(self, function_name, payload):
        return _FakeRpc(self.storage, self.operation_log, function_name, payload)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


def test_script_review_migration_is_atomic_and_service_role_only():
    source = SCRIPT_REVIEW_MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION public.apply_post_script_review" in source
    assert "FOR UPDATE;" in source
    assert "SET state = 'S4_SCRIPTED'" in source
    assert "approved_count > 0 AND pending_count = 0" in source
    assert "FROM PUBLIC, anon, authenticated" in source
    assert "TO service_role" in source


def test_remove_script_review_marks_post_removed_and_returns_success(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {"script_review_status": "pending", "script": "Hello world"},
                "video_prompt_json": {"existing": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "state": "S2_SEEDED",
                "creation_mode": "standard",
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put("/posts/post-1/script-review", data={"action": "removed"})

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["data"]["script_review_status"] == "removed"
    assert storage["posts"][0]["seed_data"]["script_review_status"] == "removed"
    assert storage["posts"][0]["seed_data"]["video_excluded"] is True
    assert storage["posts"][0]["video_prompt_json"] is None
    assert storage["posts"][0]["video_status"] == "pending"


def test_approve_manual_character_script_saves_text_and_marks_approved(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "post_type": "value",
                "seed_data": {
                    "script": "",
                    "manual_draft": True,
                    "manual_post_type": "",
                    "target_length_tier": 8,
                    "script_review_status": "pending",
                },
                "video_prompt_json": {"stale": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "creation_mode": "manual_character_consistency",
                "target_length_tier": 8,
            }
        ],
    }

    fake_supabase = _FakeSupabase(storage)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: fake_supabase)

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/posts/post-1/script-review",
        data={
            "action": "approved",
            "script_text": "Das ist ein kurzer gespeicherter Testtext fuer die Freigabe mit klarer Laenge im Zielbereich.",
            "post_type": "",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["script_review_status"] == "approved"
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["script"] == "Das ist ein kurzer gespeicherter Testtext fuer die Freigabe mit klarer Laenge im Zielbereich."
    assert seed_data["script_review_status"] == "approved"
    assert seed_data["manual_post_type"] == "value"
    assert storage["posts"][0]["video_prompt_json"] is None
    review_writes = [
        operation
        for operation in fake_supabase.client.operation_log
        if operation[0:2] == ("rpc", "apply_post_script_review")
    ]
    assert len(review_writes) == 1
    assert review_writes[0][2]["p_seed_data"]["script_review_status"] == "approved"


def test_approve_manual_character_script_auto_derives_duration_tier(monkeypatch):
    script = " ".join(["wort"] * 31)
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "post_type": "test",
                "seed_data": {
                    "script": "",
                    "manual_draft": True,
                    "manual_post_type": "test",
                    "target_length_tier": 8,
                    "script_review_status": "pending",
                },
                "video_prompt_json": {"stale": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "creation_mode": "manual_character_consistency",
                "target_length_tier": 8,
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/posts/post-1/script-review",
        data={
            "action": "approved",
            "script_text": script,
            "post_type": "test",
        },
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["script_review_status"] == "approved"
    assert seed_data["target_length_tier"] == 16
    assert seed_data["script_duration_contract"]["target_length_tier"] == 16
    assert seed_data["script_duration_contract"]["word_count"] == 31
    assert seed_data["script_duration_contract"]["status"] == "valid"
    assert storage["posts"][0]["video_prompt_json"] is None


def test_approve_semantic_script_refreshes_duration_contract_and_editorial_beats(monkeypatch):
    script = " ".join(
        [
            "Ein Kassenschalter wirkt unscheinbar, doch seine Maße entscheiden darüber, ob du ihn selbstständig nutzen kannst.",
            "Während Steharbeitsplätze etwa 96 Zentimeter hoch sind, brauchen Rollstuhlnutzende einen abgesenkten Bereich von höchstens 80 Zentimetern.",
            "Fehlt dieser niedrigere Abschnitt, werden Bezahlen, Unterschreiben und Nachfragen anstrengend oder für manche Menschen sogar unmöglich.",
            "Auch neue Zahlungsterminals müssen seit Juni 2025 barrierefrei bedienbar sein, damit dabei niemand fremde Hilfe braucht.",
            "Achte deshalb auf erreichbare Displays, verständliche Rückmeldungen und genügend freie Fläche direkt vor dem eigentlichen Schalter.",
            "Wenn du einen Ort planst, prüfe beide Arbeitshöhen im Entwurf und nicht erst nach dem Einbau.",
            "Und wenn du unterwegs bist, melde unzugängliche Kassen konkret, damit Betreiber die Barrieren erkennen und beheben.",
        ]
    )
    storage = {
        "posts": [
            {
                "id": "post-semantic",
                "batch_id": "batch-semantic",
                "post_type": "value",
                "seed_data": {
                    "script": "Stale fallback.",
                    "target_duration_seconds": 50,
                    "semantic_planned_beats": [{"text": "Stale fallback."}],
                    "script_review_status": "pending",
                },
                "video_prompt_json": {"stale": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-semantic",
                "creation_mode": "semantic_ugc",
                "target_length_tier": None,
                "target_duration_seconds": 50,
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/posts/post-semantic/script-review",
        data={"action": "approved", "script_text": script},
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["script"] == script
    assert seed_data["dialog_script"] == script
    assert seed_data["script_review_status"] == "approved"
    assert seed_data["semantic_script_word_count"] == 111
    assert seed_data["semantic_minimum_take_count"] == 7
    assert seed_data["semantic_planned_take_count"] == 7
    assert len(seed_data["semantic_planned_beats"]) == 7
    assert seed_data["semantic_duration_contract"]["requested_duration_seconds"] == 50
    assert seed_data["semantic_duration_contract_hash"]
    assert storage["posts"][0]["video_prompt_json"] is None


def test_final_semantic_script_approval_advances_batch_without_second_confirmation(
    monkeypatch,
):
    storage = {
        "posts": [
            {
                "id": "post-semantic-final",
                "batch_id": "batch-semantic-final",
                "post_type": "value",
                "seed_data": {
                    "script": "Ein klarer kurzer Satz erklärt den wichtigsten Punkt direkt und verständlich für alle.",
                    "script_review_status": "pending",
                },
                "video_prompt_json": None,
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-semantic-final",
                "state": "S2_SEEDED",
                "creation_mode": "semantic_ugc",
            }
        ],
    }
    fake_supabase = _FakeSupabase(storage)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: fake_supabase)

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-semantic-final/script-review",
        data={"action": "approved"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["batch_state"] == "S4_SCRIPTED"
    assert storage["batches"][0]["state"] == "S4_SCRIPTED"
    assert [(table, operation) for table, operation, _payload in fake_supabase.client.operation_log] == [
        ("posts", "select"),
        ("rpc", "apply_post_script_review"),
    ]


def test_final_automated_script_approval_advances_batch_without_second_confirmation(
    monkeypatch,
):
    storage = {
        "posts": [
            {
                "id": "post-product",
                "batch_id": "batch-automated",
                "post_type": "product",
                "seed_data": {
                    "script": "Der erste freigegebene Produkthinweis.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": None,
                "video_status": "pending",
            },
            {
                "id": "post-lifestyle",
                "batch_id": "batch-automated",
                "post_type": "lifestyle",
                "seed_data": {
                    "script": "Der zweite freigegebene Alltagshinweis.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": None,
                "video_status": "pending",
            },
            {
                "id": "post-value",
                "batch_id": "batch-automated",
                "post_type": "value",
                "seed_data": {
                    "script": "Der letzte noch offene Informationshinweis.",
                    "script_review_status": "pending",
                },
                "video_prompt_json": None,
                "video_status": "pending",
            },
        ],
        "batches": [
            {
                "id": "batch-automated",
                "state": "S2_SEEDED",
                "creation_mode": "automated",
            }
        ],
    }
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-value/script-review",
        data={"action": "approved"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["batch_state"] == "S4_SCRIPTED"
    assert storage["batches"][0]["state"] == "S4_SCRIPTED"


def test_approve_manual_semantic_script_persists_scene_and_outfit_overrides(monkeypatch):
    script = (
        "Ein barrierefreier Zugang macht deinen Alltag leichter, weil du dich sicher, ruhig "
        "und selbstständig bewegen kannst. Prüfe deshalb Wege, Türen und Rampen frühzeitig "
        "und plane immer genug Platz für deinen Rollstuhl ein."
    )
    storage = {
        "posts": [
            {
                "id": "post-semantic-visual",
                "batch_id": "batch-semantic-visual",
                "post_type": "value",
                "seed_data": {
                    "manual_draft": True,
                    "script": "",
                    "script_review_status": "pending",
                    "semantic_location_reference": {
                        "scene_key": "bathroom_accessibility_a",
                        "storage_uri": "https://cdn.example.com/old-bathroom.png",
                    },
                },
                "video_prompt_json": {"stale": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-semantic-visual",
                "creation_mode": "manual_semantic_ugc",
                "target_length_tier": None,
                "target_duration_seconds": 16,
            }
        ],
    }
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-semantic-visual/script-review",
        data={
            "action": "approved",
            "script_text": script,
            "post_type": "value",
            "semantic_scene_key": "garden_patio_a",
            "semantic_wardrobe_description": "navy cotton blouse with a round neckline",
            "semantic_presentation_mode": "standing_presenter",
        },
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["semantic_scene_key"] == "garden_patio_a"
    assert "semantic_location_reference" not in seed_data
    assert "garden patio" in seed_data["semantic_scene_description"].lower()
    assert seed_data["semantic_wardrobe_key"] == "custom"
    assert seed_data["semantic_wardrobe_description"] == (
        "navy cotton blouse with a round neckline"
    )
    assert seed_data["semantic_presentation_mode"] == "standing_presenter"
    assert seed_data["semantic_planned_take_count"] == 2


def test_manual_semantic_script_rejects_unknown_scene_override(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-semantic-invalid-scene",
                "batch_id": "batch-semantic-invalid-scene",
                "post_type": "value",
                "seed_data": {"manual_draft": True, "script": "old"},
                "video_prompt_json": None,
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-semantic-invalid-scene",
                "creation_mode": "manual_semantic_ugc",
                "target_length_tier": None,
                "target_duration_seconds": 16,
            }
        ],
    }
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-semantic-invalid-scene/script",
        data={
            "script_text": " ".join(["wort"] * 32),
            "post_type": "value",
            "semantic_scene_key": "unknown-room",
        },
    )

    assert response.status_code == 422, response.text
    assert storage["posts"][0]["seed_data"]["script"] == "old"


def _manual_semantic_storage(post_id: str, seconds: int) -> dict:
    return {
        "posts": [
            {
                "id": post_id,
                "batch_id": f"batch-semantic-{seconds}s",
                "post_type": "value",
                "seed_data": {
                    "manual_draft": True,
                    "script": "",
                    "script_review_status": "pending",
                },
                "video_prompt_json": None,
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": f"batch-semantic-{seconds}s",
                "state": "S2_SEEDED",
                "creation_mode": "manual_semantic_ugc",
                "target_length_tier": None,
                "target_duration_seconds": seconds,
            }
        ],
    }


def test_short_eight_second_semantic_script_names_the_word_envelope_miss(monkeypatch):
    storage = _manual_semantic_storage("post-semantic-8s-short", 8)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-semantic-8s-short/script-review",
        data={
            "action": "approved",
            "script_text": (
                "Hey, kauft Lippe sofort. Es ist der beste Treppenlift auf der Welt."
            ),
            "post_type": "value",
        },
    )

    assert response.status_code == 422, response.text
    # The reviewer must learn what to change, not just that a contract failed.
    assert response.json()["message"] == (
        "This 8s script needs 14-18 words; it has 12."
    )
    assert storage["posts"][0]["seed_data"]["script_review_status"] == "pending"


def test_eight_second_semantic_script_inside_word_envelope_is_approved(monkeypatch):
    script = (
        "Lippe baut den besten Treppenlift der Welt und bringt dich jeden Tag "
        "sicher nach oben."
    )
    storage = _manual_semantic_storage("post-semantic-8s-valid", 8)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-semantic-8s-valid/script-review",
        data={"action": "approved", "script_text": script, "post_type": "value"},
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["script_review_status"] == "approved"
    assert seed_data["semantic_script_word_count"] == 15
    assert seed_data["semantic_planned_take_count"] == 1
    assert seed_data["semantic_duration_contract"]["requested_duration_seconds"] == 8


def _planner_safe_manual_script(seconds: int) -> str:
    sentence_banks = [
        "Geprüfte Aufzugsmeldungen zeigen früh verlässliche Alternativen für spontane Termine ohne unnötige Wartezeit und hektische Rückwege heute deutlich",
        "Breite Wendeflächen erleichtern sichere Richtungswechsel im engen Eingangsbereich und bewahren täglich wertvolle Kraft bei jedem Besuch zuverlässig",
        "Aktuelle Baustellenhinweise verhindern überraschende Sperrungen während wichtiger Fahrten und nennen erreichbare Ersatzrouten rechtzeitig vor der Abreise klar",
        "Gespeicherte Ansprechpartner organisieren passende Unterstützung direkt sobald technische Störungen auftreten oder fremde Hilfe kurzfristig notwendig wird unkompliziert",
        "Niedrige Bedienelemente ermöglichen selbstständige Nutzung zuhause weil Tasten bequem erreichbar bleiben und klare Symbole jeden Schritt verständlich begleiten",
        "Rutschfeste Bodenflächen geben dem Rollstuhl stabilen Halt beim Einsteigen und senken das Risiko gefährlicher Bewegungen auf nassen Wegen",
        "Regelmäßige Wartung erhält die zuverlässige Funktion langfristig erkennt Verschleiß früh und schützt geplante Abläufe vor vermeidbaren Ausfällen wirksam",
        "Früh gebuchte Mobilitätshilfen sichern ruhige Bahnreisen nennen eindeutige Treffpunkte und vermeiden belastende Suche auf unbekannten Bahnsteigen zuverlässig",
    ]
    contract = posts_handlers.build_semantic_duration_contract(seconds)
    base_words, extra_words = divmod(
        contract.minimum_words,
        contract.minimum_take_count,
    )
    counts = [
        base_words + (1 if index < extra_words else 0)
        for index in range(contract.minimum_take_count)
    ]
    return " ".join(
        f"{' '.join(sentence_banks[index].split()[:word_count])}."
        for index, word_count in enumerate(counts)
    )


@pytest.mark.parametrize("seconds", range(8, 61))
def test_manual_semantic_script_approval_accepts_every_supported_duration(
    monkeypatch,
    seconds,
):
    post_id = f"post-semantic-{seconds}s-valid"
    storage = _manual_semantic_storage(post_id, seconds)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    script = _planner_safe_manual_script(seconds)

    response = TestClient(app, base_url="http://localhost").put(
        f"/posts/{post_id}/script-review",
        data={"action": "approved", "script_text": script, "post_type": "value"},
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    contract = posts_handlers.build_semantic_duration_contract(seconds)
    assert seed_data["script_review_status"] == "approved"
    assert seed_data["semantic_script_word_count"] == contract.minimum_words
    assert seed_data["semantic_planned_take_count"] == contract.minimum_take_count
    assert seed_data["semantic_duration_contract"]["requested_duration_seconds"] == seconds


def test_semantic_contract_message_reports_missing_batch_duration():
    message = posts_handlers._semantic_duration_contract_message(
        script_text="Ein Satz ohne gültige Batch-Dauer.",
        requested_duration_seconds=None,
        error=TypeError("int() argument must be a string"),
    )

    assert message == (
        "This batch has no valid Semantic UGC target duration, "
        "so the script cannot be approved."
    )


def test_update_prompt_bootstraps_from_seed_when_prompt_row_missing(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script": "Original script sentence.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": None,
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.patch(
        "/posts/post-1/prompt",
        json={
            "character": "Edited character",
            "style": "Edited style",
            "action": "Edited action",
            "scene": "Edited scene",
            "cinematography": "Edited cinematography",
            "dialogue": "Edited dialogue.",
            "ending": "Edited ending.",
            "audio_block": "Edited audio block.",
            "universal_negatives": "Edited universal negatives.",
            "veo_prompt": "Character:\nEdited character\n\nDialogue:\nEdited dialogue.",
            "veo_negative_prompt": "Edited veo negatives.",
        },
    )

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt is not None
    assert stored_prompt["character"] == "Edited character"
    assert stored_prompt["audio"]["dialogue"] == "Edited dialogue."
    assert stored_prompt["optimized_prompt"]
    assert stored_prompt["veo_prompt"] == "Character:\nEdited character\n\nDialogue:\nEdited dialogue."


def test_get_prompt_bootstraps_from_seed_when_prompt_row_missing(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script": "Original script sentence.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": None,
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "creation_mode": "automated",
                "scene_plan": None,
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.get("/posts/post-1/prompt")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["id"] == "post-1"
    assert payload["data"]["video_prompt"]["audio"]["dialogue"]
    assert payload["data"]["video_prompt"]["veo_prompt"]


def test_update_script_accepts_long_edits_within_generated_script_bounds(monkeypatch):
    long_script = " ".join(["Das ist eine sehr lange bearbeitbare Skriptzeile."] * 16)
    assert len(long_script) > 500

    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {"script_review_status": "pending", "script": "Kurzer Ausgangstext."},
                "video_prompt_json": {"existing": True},
                "video_status": "pending",
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put("/posts/post-1/script", data={"script_text": long_script})

    assert response.status_code == 200, response.text
    assert storage["posts"][0]["seed_data"]["script"] == long_script
    assert storage["posts"][0]["seed_data"]["script_review_status"] == "pending"
    assert "video_excluded" not in storage["posts"][0]["seed_data"]
    assert storage["posts"][0]["video_prompt_json"] is None


def test_update_manual_character_script_saves_underlength_draft(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "post_type": "value",
                "seed_data": {
                    "script": "",
                    "manual_draft": True,
                    "manual_post_type": "",
                    "target_length_tier": 8,
                    "script_review_status": "pending",
                },
                "video_prompt_json": {"stale": True},
                "video_status": "pending",
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "creation_mode": "manual_character_consistency",
                "target_length_tier": 8,
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/posts/post-1/script",
        data={"script_text": "Zu kurz.", "post_type": ""},
    )

    assert response.status_code == 200, response.text
    seed_data = storage["posts"][0]["seed_data"]
    assert seed_data["script"] == "Zu kurz."
    assert seed_data["script_review_status"] == "pending"
    assert seed_data["script_duration_contract"]["status"] == "underlength"
    assert storage["posts"][0]["video_prompt_json"] is None


def test_build_prompt_preserves_existing_manual_prompt_edits(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script_review_status": "approved",
                    "script": "Original script sentence.",
                },
                "video_prompt_json": {
                    "character": "Edited long character prompt",
                    "style": "Edited style",
                    "action": "Edited action",
                    "scene": "Edited scene",
                    "cinematography": "Edited cinematography",
                    "audio": {
                        "dialogue": "Edited dialogue.",
                        "capture": "Edited audio block.",
                    },
                    "ending_directive": "Edited ending.",
                    "audio_block": "Edited audio block.",
                    "universal_negatives": "Edited negatives.",
                    "veo_prompt": "Character:\nEdited long character prompt",
                    "veo_negative_prompt": "Edited veo negatives.",
                    "optimized_prompt": "Character:\nEdited long character prompt",
                },
            }
        ],
        "batches": [
            {
                "id": "batch-1",
                "creation_mode": "automated",
                "scene_plan": None,
            }
        ],
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.post("/posts/post-1/build-prompt")

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt["character"] == "Edited long character prompt"
    assert stored_prompt["veo_prompt"] == "Character:\nEdited long character prompt"
    assert stored_prompt["audio"]["dialogue"] == "Edited dialogue."


def test_update_prompt_rebuilds_veo_prompt_from_structured_fields_when_raw_prompt_unchanged(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script": "Original script sentence.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": {
                    "character": "Old character",
                    "style": "Old style",
                    "action": "Old action",
                    "scene": "Old scene",
                    "cinematography": "Old cinematography",
                    "audio": {
                        "dialogue": "Old dialogue.",
                        "capture": "Old audio block.",
                    },
                    "ending_directive": "Old ending.",
                    "audio_block": "Old audio block.",
                    "universal_negatives": "Old negatives.",
                    "veo_prompt": "Character:\nOld character\n\nDialogue:\nOld dialogue.",
                    "veo_negative_prompt": "Old veo negatives.",
                    "optimized_prompt": "Character:\nOld character",
                },
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.patch(
        "/posts/post-1/prompt",
        json={
            "character": "Edited long character prompt",
            "style": "Edited style",
            "action": "Edited action",
            "scene": "Edited scene",
            "cinematography": "Edited cinematography",
            "dialogue": "Edited dialogue.",
            "ending": "Edited ending.",
            "audio_block": "Edited audio block.",
            "universal_negatives": "Edited negatives.",
            "veo_prompt": "Character:\nOld character\n\nDialogue:\nOld dialogue.",
            "veo_negative_prompt": "Edited veo negatives.",
        },
    )

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt["character"] == "Edited long character prompt"
    assert "Edited long character prompt" in stored_prompt["veo_prompt"]
    assert "Edited dialogue." in stored_prompt["veo_prompt"]
