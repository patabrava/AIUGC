"""Tests for topic_research_cron_runs CRUD functions."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import MagicMock, patch


def _mock_supabase():
    mock = MagicMock()
    mock.client.table.return_value = mock.client.table
    mock.client.table.insert.return_value = mock.client.table
    mock.client.table.update.return_value = mock.client.table
    mock.client.table.select.return_value = mock.client.table
    mock.client.table.eq.return_value = mock.client.table
    mock.client.table.order.return_value = mock.client.table
    mock.client.table.limit.return_value = mock.client.table
    mock.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "started_at": "2026-03-24T00:00:00Z",
        "completed_at": None,
        "status": "running",
        "topics_requested": 5,
        "topics_completed": 0,
        "topics_failed": 0,
        "seed_source": "yaml_bank",
        "topic_ids": [],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    return mock


@patch("app.features.topics.queries.get_supabase")
def test_create_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import create_cron_run
    result = create_cron_run(topics_requested=5, seed_source="yaml_bank")
    assert result["status"] == "running"
    assert result["topics_requested"] == 5


@patch("app.features.topics.queries.get_supabase")
def test_update_cron_run(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "status": "completed",
        "topics_completed": 4,
        "topics_failed": 1,
        "completed_at": "2026-03-24T00:10:00Z",
        "topics_requested": 5,
        "started_at": "2026-03-24T00:00:00Z",
        "seed_source": "yaml_bank",
        "topic_ids": ["t1", "t2", "t3", "t4"],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import update_cron_run
    result = update_cron_run(
        run_id="run-1",
        status="completed",
        topics_completed=4,
        topics_failed=1,
        topic_ids=["t1", "t2", "t3", "t4"],
    )
    assert result["status"] == "completed"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is not None
    assert result["id"] == "run-1"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run_empty(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is None


@patch("app.features.topics.queries.get_supabase")
def test_get_cron_run_stats(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[
        {"status": "completed", "topics_completed": 4},
        {"status": "completed", "topics_completed": 3},
    ])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_cron_run_stats
    result = get_cron_run_stats()
    assert "total_runs" in result
    assert "total_topics_researched" in result


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_skips_identical_scripts_within_registry_tier(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def select(self, *_args, **_kwargs):
            self._mode = "select"
            self._filters = {}
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def insert(self, payload):
            self._mode = "insert"
            self._payload = payload
            return self

        def update(self, payload):
            self._mode = "update"
            self._payload = payload
            return self

        def execute(self):
            if self._mode == "select":
                filtered = [
                    row for row in self.rows
                    if all(row.get(key) == value for key, value in self._filters.items())
                ]
                return _FakeResponse(filtered)
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            if self._mode == "update":
                for row in self.rows:
                    if all(row.get(key) == value for key, value in self._filters.items()):
                        row.update(self._payload)
                        return _FakeResponse([row])
                return _FakeResponse([])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = MagicMock(client=MagicMock(table=MagicMock(return_value=fake_table)))

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="Pflegegrad verstehen",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-1",
        variants=[
            {
                "bucket": "canonical",
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause.",
                "hook_style": "canonical",
                "lane_key": "lane-1",
            },
            {
                "bucket": "testimonial",
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause.",
                "hook_style": "testimonial",
                "lane_key": "lane-1",
            },
            {
                "bucket": "transformation",
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause.",
                "hook_style": "transformation",
                "lane_key": "lane-1",
            },
        ],
    )

    assert len(stored) == 1
    assert stored[0]["bucket"] == "canonical"


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_rejects_contaminated_canonical_scripts(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def select(self, *_args, **_kwargs):
            self._mode = "select"
            self._filters = {}
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def insert(self, payload):
            self._mode = "insert"
            self._payload = payload
            return self

        def execute(self):
            if self._mode == "select":
                filtered = [
                    row for row in self.rows
                    if all(row.get(key) == value for key, value in self._filters.items())
                ]
                return _FakeResponse(filtered)
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = MagicMock(client=MagicMock(table=MagicMock(return_value=fake_table)))

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="Merkzeichen richtig unterscheiden",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-1",
        variants=[
            {
                "bucket": "canonical",
                "script": "aG erfordert GdB 80 und massive Einschränkungen, G misst deine Wegstrecke. Zentrale Erkenntnisse.",
                "hook_style": "canonical",
                "lane_key": "lane-1",
                "target_length_tier": 8,
            }
        ],
    )

    assert stored == []
    assert fake_table.rows == []


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_skips_global_duplicate_scripts(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-existing",
                    "topic_research_dossier_id": "dossier-existing",
                    "title": "Bestehendes Thema",
                    "script": "Diese Rampe musst du vor der Fahrt anmelden, sonst bleibst du am Bahnsteig stehen.",
                    "target_length_tier": 8,
                    "bucket": "canonical",
                    "lane_key": "lane-existing",
                }
            ]
            self._filters = {}
            self._payload = None
            self._mode = None

        def select(self, *_args, **_kwargs):
            self._mode = "select"
            self._filters = {}
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def insert(self, payload):
            self._mode = "insert"
            self._payload = payload
            return self

        def execute(self):
            if self._mode == "select":
                filtered = [
                    row for row in self.rows
                    if all(row.get(key) == value for key, value in self._filters.items())
                ]
                return _FakeResponse(filtered)
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = MagicMock(client=MagicMock(table=MagicMock(return_value=fake_table)))

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-new",
        title="Neues Thema",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-new",
        variants=[
            {
                "bucket": "canonical",
                "script": "Diese Rampe musst du vor der Fahrt anmelden, sonst bleibst du am Bahnsteig stehen.",
                "hook_style": "canonical",
                "lane_key": "lane-new",
                "target_length_tier": 8,
            }
        ],
    )

    assert stored == []
    assert len(fake_table.rows) == 1


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_repairs_citation_residue_before_insert(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def select(self, *_args, **_kwargs):
            self._mode = "select"
            self._filters = {}
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def insert(self, payload):
            self._mode = "insert"
            self._payload = payload
            return self

        def execute(self):
            if self._mode == "select":
                filtered = [
                    row for row in self.rows
                    if all(row.get(key) == value for key, value in self._filters.items())
                ]
                return _FakeResponse(filtered)
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = MagicMock(client=MagicMock(table=MagicMock(return_value=fake_table)))

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="Merkzeichen richtig unterscheiden",
        post_type="value",
        target_length_tier=16,
        topic_research_dossier_id="dossier-1",
        variants=[
            {
                "bucket": "canonical",
                "script": (
                    "Wenn dir bei G und aG die Unterschiede unklar bleiben, verlierst du schnell Orientierung im Alltag. "
                    "Mit klar geprüften Voraussetzungen trennst du Wegstrecke, Einschränkung und Berechtigung endlich sauber voneinander. "
                    "So vermeidest du typische Fehlannahmen [cite: 1]."
                ),
                "hook_style": "canonical",
                "lane_key": "lane-1",
                "target_length_tier": 16,
            }
        ],
    )

    assert len(stored) == 1
    assert "[cite:" not in stored[0]["script"].lower()
    assert fake_table.rows[0]["script"] == stored[0]["script"]


@patch("app.features.topics.queries.get_supabase")
def test_create_post_for_batch_injects_target_tier_into_seed_data(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.payload = None

        def table(self, *_args, **_kwargs):
            return self

        def insert(self, payload):
            self.payload = payload
            return self

        def execute(self):
            row = dict(self.payload or {})
            row["id"] = "post-1"
            return _FakeResponse([row])

    fake_table = _FakeTable()
    mock_get_sb.return_value = type("SB", (), {"client": fake_table})()

    from app.features.topics.queries import create_post_for_batch

    post = create_post_for_batch(
        batch_id="batch-1",
        post_type="value",
        topic_title="Pflegegrad prüfen",
        topic_rotation="Rotation",
        topic_cta="CTA",
        spoken_duration=16,
        seed_data={"script": "Script", "dialog_script": "Dialog"},
        target_length_tier=16,
    )

    assert post["id"] == "post-1"
    assert fake_table.payload["seed_data"]["target_length_tier"] == 16
