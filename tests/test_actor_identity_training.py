from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.characters.schemas import ActorTrainingSet


def _urls(count: int) -> list[str]:
    return [f"https://cdn.example.com/actor/{idx}.png" for idx in range(count)]


def test_actor_training_set_accepts_8_to_20_public_urls():
    assert len(ActorTrainingSet(images=_urls(8)).images) == 8
    assert len(ActorTrainingSet(images=_urls(20)).images) == 20


@pytest.mark.parametrize("count", [0, 3, 7, 21])
def test_actor_training_set_rejects_invalid_image_count(count):
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=_urls(count))


def test_actor_training_set_rejects_non_public_urls():
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=["/local/file.png"] * 8)
