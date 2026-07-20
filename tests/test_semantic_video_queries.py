from types import SimpleNamespace

import pytest

from app.core.errors import ValidationError


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "posts"
        return _Query(self.rows)


def test_semantic_context_uses_image_only_actor_identity_and_canonical_location(monkeypatch):
    from app.features.semantic_videos import queries

    post = {
        "id": "post-1",
        "seed_data": {"script": "Approved script."},
        "batches": {
            "id": "batch-1",
            "actor_identity_id": "actor-1",
            "actor_identity_snapshot": {
                "actor_identity_id": "actor-1",
                "name": "AYRA Actor",
                "character_description": "Legacy text that must not enter semantic generation.",
                "reference_image_urls": [
                    "https://cdn.example.com/actor-front.png",
                    "https://cdn.example.com/actor-three-quarter.png",
                ],
            },
        },
    }
    asset = SimpleNamespace(
        image_url="https://cdn.example.com/canonical-home-office.png",
        storage_key="canonical-scenes/home_office_advice_a/v1.png",
        scene_key="home_office_advice_a",
        scene_bible_version=1,
    )
    bible = SimpleNamespace(
        scene_identity="The approved actor-free canonical home office.",
    )
    monkeypatch.setattr(
        queries,
        "require_canonical_scene_asset",
        lambda **kwargs: asset,
        raising=False,
    )
    monkeypatch.setattr(
        queries,
        "get_scene_bible",
        lambda scene_key: bible,
        raising=False,
    )

    context = queries.load_semantic_video_context("post-1", client=_Client([post]))

    reference = context["reference"]
    assert reference["actor"] == {
        "actor_identity_id": "actor-1",
        "name": "AYRA Actor",
        "reference_image_urls": [
            "https://cdn.example.com/actor-front.png",
            "https://cdn.example.com/actor-three-quarter.png",
        ],
    }
    assert [row["role"] for row in reference["actor_references"]] == [
        "actor_front",
        "actor_three_quarter",
    ]
    assert reference["location_reference"] == {
        "role": "location",
        "storage_uri": asset.image_url,
        "storage_key": asset.storage_key,
        "mime_type": "image/png",
        "scene_key": asset.scene_key,
        "scene_bible_version": asset.scene_bible_version,
    }
    assert reference["scene_description"] == bible.scene_identity
    assert reference["wardrobe_key"] == "grey_cardigan"
    assert reference["wardrobe_description"] == "light-grey cardigan over a plain white top"


class _AnchorQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, value):
        assert value == 1
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _AnchorClient:
    def __init__(self, rows):
        self.query = _AnchorQuery(rows)

    def table(self, name):
        assert name == "semantic_actor_scene_plate_anchors"
        return self.query


def test_get_actor_scene_plate_anchor_uses_exact_actor_and_reference_fingerprint():
    from app.features.semantic_videos.queries import get_actor_scene_plate_anchor

    fingerprint = "a" * 64
    row = {"id": "anchor-1", "actor_reference_fingerprint": fingerprint}
    client = _AnchorClient([row])

    result = get_actor_scene_plate_anchor(
        actor_identity_id="actor-1",
        actor_reference_fingerprint=fingerprint.upper(),
        client=client,
    )

    assert result == row
    assert client.query.filters == [
        ("actor_identity_id", "actor-1"),
        ("actor_reference_fingerprint", fingerprint),
    ]


@pytest.mark.parametrize(
    ("actor_identity_id", "fingerprint"),
    [("", "a" * 64), ("actor-1", "short"), ("actor-1", "z" * 64)],
)
def test_get_actor_scene_plate_anchor_rejects_ambiguous_identity(
    actor_identity_id,
    fingerprint,
):
    from app.features.semantic_videos.queries import get_actor_scene_plate_anchor

    with pytest.raises(ValidationError, match="anchor identity is invalid"):
        get_actor_scene_plate_anchor(
            actor_identity_id=actor_identity_id,
            actor_reference_fingerprint=fingerprint,
            client=_AnchorClient([]),
        )


def test_semantic_location_rotation_is_distinct_for_first_three_posts_and_override_wins(
    monkeypatch,
):
    from app.features.semantic_videos import queries

    requested_scene_keys = []

    def fake_asset(*, scene_key, **_kwargs):
        requested_scene_keys.append(scene_key)
        return SimpleNamespace(
            image_url=f"https://cdn.example.com/{scene_key}.png",
            storage_key=f"canonical-scenes/{scene_key}/v1.png",
            scene_key=scene_key,
            scene_bible_version=1,
        )

    monkeypatch.setattr(queries, "require_canonical_scene_asset", fake_asset)
    monkeypatch.setattr(
        queries,
        "get_scene_bible",
        lambda scene_key: SimpleNamespace(scene_identity=f"Scene {scene_key}"),
    )

    for index in range(3):
        queries._canonical_semantic_location(  # noqa: SLF001
            {"id": f"post-{index}", "post_type": "value"},
            {"target_duration_seconds": 16},
            {"semantic_rotation_index": index},
        )
    queries._canonical_semantic_location(  # noqa: SLF001
        {"id": "post-override", "post_type": "value"},
        {"target_duration_seconds": 16},
        {
            "semantic_rotation_index": 0,
            "semantic_scene_key": "home_office_advice_a",
        },
    )

    assert requested_scene_keys[:3] == [
        "bathroom_accessibility_a",
        "garden_patio_a",
        "home_office_advice_a",
    ]
    assert requested_scene_keys[3] == "home_office_advice_a"
