from datetime import datetime, timedelta, timezone


def test_scene_image_worker_readiness_accepts_a_fresh_persisted_heartbeat(
    monkeypatch,
):
    from app import main
    from app.features.semantic_videos import queries

    monkeypatch.setattr(
        queries,
        "get_scene_image_worker_heartbeat",
        lambda: {
            "worker_id": "semantic-scene-image-v2-test",
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "contract": "semantic-scene-image-v2",
                "queue_probe_status": "ok",
                "queue_probe_checked_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )

    assert main._probe_scene_image_worker_health() == (True, None)


def test_scene_image_worker_readiness_rejects_a_stale_heartbeat(monkeypatch):
    from app import main
    from app.features.semantic_videos import queries

    monkeypatch.setattr(
        queries,
        "get_scene_image_worker_heartbeat",
        lambda: {
            "worker_id": "semantic-scene-image-v2-stale",
            "last_seen_at": (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat(),
            "metadata": {
                "contract": "semantic-scene-image-v2",
                "queue_probe_status": "ok",
                "queue_probe_checked_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )

    healthy, error = main._probe_scene_image_worker_health()
    assert healthy is False
    assert error is not None
    assert "heartbeat is" in error


def test_scene_image_worker_readiness_rejects_a_stale_queue_probe(monkeypatch):
    from app import main
    from app.features.semantic_videos import queries

    monkeypatch.setattr(
        queries,
        "get_scene_image_worker_heartbeat",
        lambda: {
            "worker_id": "semantic-scene-image-v2-stale-probe",
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "contract": "semantic-scene-image-v2",
                "queue_probe_status": "ok",
                "queue_probe_checked_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=5)
                ).isoformat(),
            },
        },
    )

    healthy, error = main._probe_scene_image_worker_health()
    assert healthy is False
    assert error is not None
    assert "queue probe is" in error


def test_scene_image_worker_readiness_rejects_an_obsolete_contract(monkeypatch):
    from app import main
    from app.features.semantic_videos import queries

    monkeypatch.setattr(
        queries,
        "get_scene_image_worker_heartbeat",
        lambda: {
            "worker_id": "semantic-scene-image-v1-old",
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"contract": "semantic-scene-image-v1"},
        },
    )

    assert main._probe_scene_image_worker_health() == (
        False,
        "scene-image worker heartbeat contract is obsolete",
    )


def test_scene_image_worker_readiness_rejects_a_persistent_claim_failure(
    monkeypatch,
):
    from app import main
    from app.features.semantic_videos import queries

    monkeypatch.setattr(
        queries,
        "get_scene_image_worker_heartbeat",
        lambda: {
            "worker_id": "semantic-scene-image-v2-broken-claim",
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "contract": "semantic-scene-image-v2",
                "queue_probe_status": "error",
                "queue_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "queue_probe_error_class": "APIError",
            },
        },
    )

    assert main._probe_scene_image_worker_health() == (
        False,
        "scene-image worker cannot query its durable queue",
    )
