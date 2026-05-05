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
def test_add_topic_to_registry_rehabilitates_quarantined_family(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "topic-1",
                    "title": "Fallback Familie",
                    "script": "Fallback script.",
                    "post_type": "value",
                    "canonical_topic": "Barrierefreiheit im ÖPNV",
                    "family_fingerprint": "barrierefreiheit im öpnv",
                    "status": "quarantined",
                    "use_count": 0,
                }
            ]
            self._filters = {}
            self._payload = None
            self._mode = None

        def table(self, *_args, **_kwargs):
            return self

        def select(self, *_args, **_kwargs):
            self._mode = "select"
            self._filters = {}
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def limit(self, *_args, **_kwargs):
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
            if self._mode == "update":
                for row in self.rows:
                    if all(row.get(key) == value for key, value in self._filters.items()):
                        row.update(self._payload)
                        return _FakeResponse([row])
                return _FakeResponse([])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = type("SB", (), {"client": fake_table})()

    from app.features.topics.queries import add_topic_to_registry

    updated = add_topic_to_registry(
        title="Provider Familie",
        script="Provider script.",
        post_type="value",
        canonical_topic="Barrierefreiheit im ÖPNV",
        status="provisional",
        increment_use_count=False,
    )

    assert updated["id"] == "topic-1"
    assert fake_table.rows[0]["status"] == "provisional"


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


def test_store_topic_bank_entry_increments_use_count(monkeypatch):
    from app.features.topics import queries as topic_queries

    captured = {}

    def fake_add_topic_to_registry(**kwargs):
        captured.update(kwargs)
        return {
            "id": "topic-1",
            "title": kwargs["title"],
            "use_count": kwargs.get("use_count", 0),
        }

    def fake_create_topic_research_dossier(**kwargs):
        return {"id": "dossier-1"}

    monkeypatch.setattr(topic_queries, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_queries, "create_topic_research_dossier", fake_create_topic_research_dossier)

    result = topic_queries.store_topic_bank_entry(
        title="Neues Thema",
        topic_script="Pruef deinen Pflegegrad jetzt sofort und nutze die Hilfe im Alltag.",
        post_type="value",
        target_length_tier=8,
        research_payload={"seed_topic": "Neues Thema", "topic": "Neues Thema"},
        origin_kind="provider",
    )

    assert result["id"] == "topic-1"
    assert captured["increment_use_count"] is True


def test_store_topic_bank_entry_uses_product_validation_bounds(monkeypatch):
    from app.features.topics import queries as topic_queries

    validation_calls = []

    def fake_validate(payload, *, target_length_tier, post_type=None, **kwargs):
        validation_calls.append(
            {
                "target_length_tier": target_length_tier,
                "post_type": post_type,
                "script": payload["script"],
            }
        )
        return dict(payload)

    def fake_add_topic_to_registry(**kwargs):
        return {
            "id": "topic-1",
            "title": kwargs["title"],
            "use_count": kwargs.get("use_count", 0),
        }

    def fake_create_topic_research_dossier(**kwargs):
        return {"id": "dossier-1"}

    monkeypatch.setattr(topic_queries, "validate_pre_persistence_topic_payload", fake_validate)
    monkeypatch.setattr(topic_queries, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_queries, "create_topic_research_dossier", fake_create_topic_research_dossier)

    topic_queries.store_topic_bank_entry(
        title="VARIO PLUS: Eine Schiene fuer heute und spaeter",
        topic_script="Hast du eine Treppe, die heute passt und morgen noch sicher ist?",
        post_type="product",
        target_length_tier=32,
        research_payload={"seed_topic": "VARIO PLUS", "topic": "VARIO PLUS"},
        origin_kind="provider",
    )

    assert validation_calls == [
        {
            "target_length_tier": 32,
            "post_type": "product",
            "script": "Hast du eine Treppe, die heute passt und morgen noch sicher ist?",
        }
    ]


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_applies_shared_quality_gate(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def table(self, *_args, **_kwargs):
            return self

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
                return _FakeResponse([])
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = type("SB", (), {"client": fake_table})()

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="MSZ — Rechte",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id=None,
        variants=[{
            "script": "Ab 2025 — dein Pflegegrad bleibt wichtig, weil Hilfe im Alltag oft schneller gebraucht wird.",
            "caption": "Cap — Ab 2025 gelten neue Hinweise fuer dich im Alltag.",
            "source_summary": "Zusammenfassung — Ab 2025 gelten neue Hinweise fuer dich im Alltag.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung — bitte die Lage 2026 pruefen.",
            "framework": "PAL",
            "hook_style": "question",
            "bucket": "canonical",
            "origin_kind": "provider",
        }],
    )

    assert len(stored) == 1
    assert "—" not in fake_table.rows[0]["title"]
    assert "—" not in fake_table.rows[0]["script"]
    assert "Seit 2025" in fake_table.rows[0]["script"]
    assert "—" not in fake_table.rows[0]["source_summary"]
    assert "Seit 2025" in fake_table.rows[0]["source_summary"]
    assert "—" not in fake_table.rows[0]["disclaimer"]


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_uses_product_word_bounds(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def table(self, *_args, **_kwargs):
            return self

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
                return _FakeResponse([])
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = type("SB", (), {"client": fake_table})()

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="VARIO PLUS",
        post_type="product",
        target_length_tier=8,
        topic_research_dossier_id=None,
        variants=[{
            "script": "VARIO PLUS gibt dir heute Sicherheit und spaeter Flexibilitaet, weil dieselbe Schiene mit deinem Alltag mitwachsen kann.",
            "caption": "VARIO PLUS im Alltag",
            "source_summary": "Kurzer Produktkontext fuer die Speicherung.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
            "framework": "PAL",
            "hook_style": "question",
            "bucket": "canonical",
            "lane_key": "product-canonical",
            "lane_family": "product",
            "origin_kind": "provider",
            "estimated_duration_s": 8,
        }],
        origin_kind="provider",
    )

    assert len(stored) == 1
    assert stored[0]["post_type"] == "product"


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_accepts_product_32s_midrange_script(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = []
            self._filters = {}
            self._payload = None
            self._mode = None

        def table(self, *_args, **_kwargs):
            return self

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
                return _FakeResponse([])
            if self._mode == "insert":
                row = dict(self._payload)
                row["id"] = f"row-{len(self.rows) + 1}"
                self.rows.append(row)
                return _FakeResponse([row])
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = type("SB", (), {"client": fake_table})()

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="VARIO PLUS",
        post_type="product",
        target_length_tier=32,
        topic_research_dossier_id=None,
        variants=[{
            "script": "VARIO PLUS passt sich deinem Alltag an, weil er gerade, kurvig, steil oder eng funktioniert. Du nutzt ihn innen und außen, als Plattform oder mit Sitz. Dabei bleibst du flexibel und sicher unterwegs. Mit 300 Kilo Tragkraft und nachrüstbarem Sitzwechsel wächst die Lösung mit. Made in Germany sorgt zusätzlich für Vertrauen im Alltag.",
            "caption": "VARIO PLUS fuer Zuhause",
            "source_summary": "Kurzer Produktkontext fuer die Speicherung.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
            "framework": "PAL",
            "hook_style": "question",
            "bucket": "canonical",
            "lane_key": "product-canonical",
            "lane_family": "product",
            "origin_kind": "provider",
            "estimated_duration_s": 32,
        }],
        origin_kind="provider",
    )

    assert len(stored) == 1
    assert stored[0]["post_type"] == "product"


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
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause wirklich sofort.",
                "hook_style": "canonical",
                "lane_key": "lane-1",
            },
            {
                "bucket": "testimonial",
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause wirklich sofort.",
                "hook_style": "testimonial",
                "lane_key": "lane-1",
            },
            {
                "bucket": "transformation",
                "script": "Pflegegrad prüfen spart dir Rückfragen, Zeit und unnötigen Stress bei der Begutachtung zuhause wirklich sofort.",
                "hook_style": "transformation",
                "lane_key": "lane-1",
            },
        ],
    )

    assert len(stored) == 1
    assert stored[0]["bucket"] == "canonical"


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_skips_high_confidence_suffix_pattern(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-1",
                    "topic_research_dossier_id": "dossier-1",
                    "title": "Merkzeichen B",
                    "script": (
                        "Merkzeichen B spart dir Rückfragen, weil du Begleitfahrt, Nachweise und Fristen deutlich ruhiger einordnest."
                    ),
                    "target_length_tier": 8,
                    "bucket": "canonical",
                    "lane_key": "lane-1",
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
        topic_registry_id="topic-1",
        title="Freifahrt im Nahverkehr",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-1",
        variants=[
            {
                "bucket": "testimonial",
                "script": (
                    "Freifahrt im Nahverkehr spart dir Rückfragen, weil du Begleitfahrt, Nachweise und Fristen deutlich ruhiger einordnest."
                ),
                "hook_style": "testimonial",
                "lane_key": "lane-2",
            }
        ],
    )

    assert stored == []
    assert len(fake_table.rows) == 1


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
def test_upsert_topic_script_variants_rehabilitates_exact_synthetic_fallback_duplicate(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-1",
                    "topic_research_dossier_id": "dossier-fallback",
                    "title": "Fallback Thema",
                    "script": "Diese Rampe musst du vor der Fahrt anmelden, sonst bleibst du am Bahnsteig stehen.",
                    "target_length_tier": 8,
                    "bucket": "canonical",
                    "lane_key": "lane-1",
                    "origin_kind": "synthetic_fallback",
                    "audit_status": "pending",
                    "audit_attempts": 0,
                    "use_count": 0,
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
        title="Provider Thema",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-provider",
        origin_kind="provider",
        variants=[
            {
                "bucket": "canonical",
                "script": "Diese Rampe musst du vor der Fahrt anmelden, sonst bleibst du am Bahnsteig stehen.",
                "hook_style": "canonical",
                "lane_key": "lane-1",
                "target_length_tier": 8,
            }
        ],
    )

    assert len(stored) == 1
    assert len(fake_table.rows) == 1
    assert fake_table.rows[0]["origin_kind"] == "provider"
    assert fake_table.rows[0]["topic_research_dossier_id"] == "dossier-provider"


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_replaces_fallback_owned_variant_slot(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-1",
                    "topic_research_dossier_id": "dossier-fallback",
                    "post_type": "value",
                    "title": "Fallback Thema",
                    "script": "Der Ersatzverkehr kippt, wenn du die Hilfe erst am Gleis anmeldest.",
                    "target_length_tier": 8,
                    "bucket": "canonical",
                    "lane_key": "lane-old",
                    "framework": "PAL",
                    "hook_style": "canonical",
                    "origin_kind": "synthetic_fallback",
                    "audit_status": "pending",
                    "audit_attempts": 1,
                    "use_count": 0,
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
        title="Provider Thema",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-provider",
        origin_kind="provider",
        variants=[
            {
                "bucket": "canonical",
                    "script": "Wenn du die Hilfe zu spät anmeldest, scheitert der Ersatzverkehr schon am Einstieg oft komplett.",
                "hook_style": "canonical",
                "framework": "PAL",
                "lane_key": "lane-provider",
                "target_length_tier": 8,
            }
        ],
    )

    assert len(stored) == 1
    assert len(fake_table.rows) == 1
    assert fake_table.rows[0]["script"] == "Wenn du die Hilfe zu spät anmeldest, scheitert der Ersatzverkehr schon am Einstieg oft komplett."
    assert fake_table.rows[0]["origin_kind"] == "provider"
    assert fake_table.rows[0]["topic_research_dossier_id"] == "dossier-provider"
    assert fake_table.rows[0]["lane_key"] == "lane-provider"


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_skips_taken_provider_variant_slot(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-1",
                    "topic_research_dossier_id": "dossier-provider",
                    "post_type": "value",
                    "title": "Provider Thema",
                    "script": "Diese Hilfe buchst du vorab, sonst fehlt dir der barrierefreie Umstieg.",
                    "target_length_tier": 8,
                    "bucket": "canonical",
                    "lane_key": "lane-old",
                    "framework": "PAL",
                    "hook_style": "canonical",
                    "origin_kind": "provider",
                    "audit_status": "pass",
                    "audit_attempts": 1,
                    "use_count": 0,
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
        title="Provider Thema",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-provider-2",
        origin_kind="provider",
        variants=[
            {
                "bucket": "canonical",
                "script": "Wenn du den Einstieg meldest, kommst du ohne Stress zum barrierefreien Umstieg.",
                "hook_style": "canonical",
                "framework": "PAL",
                "lane_key": "lane-new",
                "target_length_tier": 8,
            }
        ],
    )

    assert stored == []
    assert len(fake_table.rows) == 1
    assert fake_table.rows[0]["script"] == "Diese Hilfe buchst du vorab, sonst fehlt dir der barrierefreie Umstieg."
    assert fake_table.rows[0]["topic_research_dossier_id"] == "dossier-provider"


def test_list_topic_suggestions_prefers_low_use_count_and_older_last_used(monkeypatch):
    from app.features.topics import queries as topic_queries

    registry_rows = [
        {"id": "topic-1", "title": "Pflegegrad Tipps", "script": "A", "use_count": 8, "last_used_at": "2026-03-31T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "pflegegrad tipps"},
        {"id": "topic-2", "title": "Rollstuhl Rampen", "script": "B", "use_count": 1, "last_used_at": "2026-03-20T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "rollstuhl rampen"},
        {"id": "topic-3", "title": "ÖPNV Hilfe", "script": "C", "use_count": 1, "last_used_at": "2026-03-29T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "öpnv hilfe"},
    ]
    script_rows = [
        {"id": "script-1", "topic_registry_id": "topic-1", "title": "Pflegegrad Tipps", "script": "A", "audit_status": "pass"},
        {"id": "script-2", "topic_registry_id": "topic-2", "title": "Rollstuhl Rampen", "script": "B", "audit_status": "pass"},
        {"id": "script-3", "topic_registry_id": "topic-3", "title": "ÖPNV Hilfe", "script": "C", "audit_status": "pass"},
    ]

    monkeypatch.setattr(topic_queries, "get_all_topics_from_registry", lambda: registry_rows)
    monkeypatch.setattr(topic_queries, "_fetch_topic_script_rows", lambda **kwargs: script_rows)

    result = topic_queries.list_topic_suggestions(target_length_tier=8, limit=3, post_type="value")

    assert [row["topic_registry_id"] for row in result] == ["topic-2", "topic-3", "topic-1"]


def test_list_topic_suggestions_deduplicates_same_topic_family_title(monkeypatch):
    from app.features.topics import queries as topic_queries

    registry_rows = [
        {"id": "topic-1", "title": "Pflegegrad: Schnell-Check!", "script": "A", "use_count": 0, "last_used_at": "2026-03-20T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "pflegegrad schnell check"},
        {"id": "topic-2", "title": "Pflegegrad Schnell Check", "script": "B", "use_count": 0, "last_used_at": "2026-03-21T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "pflegegrad schnell check"},
        {"id": "topic-3", "title": "Barrierefreiheit im Alltag", "script": "C", "use_count": 0, "last_used_at": "2026-03-22T10:00:00+00:00", "post_type": "value", "status": "active", "family_fingerprint": "barrierefreiheit im alltag"},
    ]
    script_rows = [
        {"id": "script-1", "topic_registry_id": "topic-1", "title": "Pflegegrad: Schnell-Check!", "script": "A", "audit_status": "pass"},
        {"id": "script-2", "topic_registry_id": "topic-2", "title": "Pflegegrad Schnell Check", "script": "B", "audit_status": "pass"},
        {"id": "script-3", "topic_registry_id": "topic-3", "title": "Barrierefreiheit im Alltag", "script": "C", "audit_status": "pass"},
    ]

    monkeypatch.setattr(topic_queries, "get_all_topics_from_registry", lambda: registry_rows)
    monkeypatch.setattr(topic_queries, "_fetch_topic_script_rows", lambda **kwargs: script_rows)

    result = topic_queries.list_topic_suggestions(target_length_tier=8, limit=5, post_type="value")

    titles = [row["title"] for row in result]
    assert "Barrierefreiheit im Alltag" in titles
    assert len([title for title in titles if "Pflegegrad" in title]) == 1


def test_list_topic_suggestions_only_returns_active_pass_audited_families(monkeypatch):
    from app.features.topics import queries as topic_queries

    registry_rows = [
        {"id": "topic-active", "title": "Aktiv", "script": "A", "post_type": "value", "status": "active", "family_fingerprint": "aktiv"},
        {"id": "topic-provisional", "title": "Vorläufig", "script": "B", "post_type": "value", "status": "provisional", "family_fingerprint": "vorlaeufig"},
        {"id": "topic-quarantined", "title": "Quarantäne", "script": "C", "post_type": "value", "status": "quarantined", "family_fingerprint": "quarantaene"},
    ]
    script_rows = [
        {"id": "script-pass", "topic_registry_id": "topic-active", "title": "Aktiv", "script": "A", "audit_status": "pass"},
        {"id": "script-pending", "topic_registry_id": "topic-provisional", "title": "Vorläufig", "script": "B", "audit_status": "pending"},
        {"id": "script-reject", "topic_registry_id": "topic-quarantined", "title": "Quarantäne", "script": "C", "audit_status": "pass"},
    ]

    monkeypatch.setattr(topic_queries, "get_all_topics_from_registry", lambda: registry_rows)
    monkeypatch.setattr(topic_queries, "_fetch_topic_script_rows", lambda **kwargs: script_rows)

    result = topic_queries.list_topic_suggestions(target_length_tier=8, limit=10, post_type="value")

    assert [row["topic_registry_id"] for row in result] == ["topic-active"]


def test_list_topic_suggestions_falls_back_to_active_registry_rows_without_scripts(monkeypatch):
    from app.features.topics import queries as topic_queries

    registry_rows = [
        {"id": "topic-active-1", "title": "Aktiv 1", "script": "A", "post_type": "value", "status": "active", "family_fingerprint": "aktiv-1"},
        {"id": "topic-active-2", "title": "Aktiv 2", "script": "B", "post_type": "value", "status": "active", "family_fingerprint": "aktiv-2"},
    ]

    monkeypatch.setattr(topic_queries, "get_all_topics_from_registry", lambda: registry_rows)
    monkeypatch.setattr(topic_queries, "_fetch_topic_script_rows", lambda **kwargs: [])

    result = topic_queries.list_topic_suggestions(target_length_tier=8, limit=10, post_type="value")

    assert [row["topic_registry_id"] for row in result] == ["topic-active-1", "topic-active-2"]
    assert all(row["family_status"] == "active" for row in result)


def test_list_topic_suggestions_includes_used_scripts_but_prefers_unused(monkeypatch):
    from app.features.topics import queries as topic_queries

    registry_rows = [
        {"id": "topic-used", "title": "Verbraucht", "script": "A", "post_type": "value", "status": "active", "family_fingerprint": "verbraucht"},
        {"id": "topic-unused", "title": "Frei", "script": "B", "post_type": "value", "status": "active", "family_fingerprint": "frei"},
    ]
    script_rows = [
        {"id": "script-used", "topic_registry_id": "topic-used", "title": "Verbraucht", "script": "A", "audit_status": "pass", "use_count": 2, "last_used_at": "2026-03-31T10:00:00+00:00"},
        {"id": "script-unused", "topic_registry_id": "topic-unused", "title": "Frei", "script": "B", "audit_status": "pass", "use_count": 0},
    ]

    monkeypatch.setattr(topic_queries, "get_all_topics_from_registry", lambda: registry_rows)
    monkeypatch.setattr(topic_queries, "_fetch_topic_script_rows", lambda **kwargs: script_rows)

    result = topic_queries.list_topic_suggestions(target_length_tier=8, limit=10, post_type="value")

    assert [row["topic_registry_id"] for row in result] == ["topic-unused", "topic-used"]


def test_count_selectable_topic_families_uses_full_limit(monkeypatch):
    from app.features.topics import queries as topic_queries

    captured = {}

    def fake_list_topic_suggestions(*, target_length_tier=None, limit=None, post_type=None):
        captured["limit"] = limit
        return [object()] * 1300

    monkeypatch.setattr(topic_queries, "list_topic_suggestions", fake_list_topic_suggestions)

    count = topic_queries.count_selectable_topic_families(post_type="value", target_length_tier=8)

    assert count == 1300
    assert captured["limit"] == 10_000


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
def test_upsert_topic_script_variants_allows_distinct_scripts_within_same_topic(mock_get_sb):
    class _FakeResponse:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self):
            self.rows = [
                {
                    "id": "row-existing",
                    "topic_registry_id": "topic-1",
                    "topic_research_dossier_id": "dossier-1",
                    "title": "Bestehendes Thema",
                    "script": "Diese Rampe musst du vorher anmelden, sonst stehst du am Bahnsteig fest.",
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
        topic_registry_id="topic-1",
        title="Neues Thema",
        post_type="value",
        target_length_tier=8,
        topic_research_dossier_id="dossier-1",
        variants=[
            {
                "bucket": "testimonial",
                "script": "Beim Ersatzverkehr hilft dir frühes Nachfragen, damit du Aufzüge, Umstiege und Wartezeiten sauber einplanst.",
                "hook_style": "testimonial",
                "lane_key": "lane-new",
                "target_length_tier": 8,
            }
        ],
    )

    assert len(stored) == 1
    assert len(fake_table.rows) == 2


@patch("app.features.topics.queries.get_supabase")
def test_upsert_topic_script_variants_skips_live_fingerprint_conflict(mock_get_sb):
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
                return _FakeResponse([])
            if self._mode == "insert":
                raise Exception(
                    "duplicate key value violates unique constraint "
                    "\"topic_scripts_tier_fingerprint_key\""
                )
            return _FakeResponse([])

    fake_table = _FakeTable()
    mock_get_sb.return_value = MagicMock(client=MagicMock(table=MagicMock(return_value=fake_table)))

    from app.features.topics.queries import upsert_topic_script_variants

    stored = upsert_topic_script_variants(
        topic_registry_id="topic-new",
        title="Fallback Lifestyle",
        post_type="lifestyle",
        target_length_tier=32,
        topic_research_dossier_id=None,
        variants=[
            {
                "bucket": "problem_agitate_solution",
                "script": "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter.",
                "hook_style": "problem-agitate-solution",
                "target_length_tier": 32,
            }
        ],
    )

    assert stored == []


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
