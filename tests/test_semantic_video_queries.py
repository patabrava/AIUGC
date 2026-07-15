from types import SimpleNamespace


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
    assert reference["wardrobe_description"] == (
        "Use the wardrobe visible in actor reference Image 1 as the sole wardrobe reference."
    )
