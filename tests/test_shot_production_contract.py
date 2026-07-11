from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from hashlib import sha256
import io

from PIL import Image
import pytest

from app.core.errors import ValidationError
from app.features.shot_production.planner import EditorialBeat


def _png_bytes(width: int = 90, height: int = 160) -> bytes:
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            (x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1), (x + y) % 256)
            for y in range(height)
            for x in range(width)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _beats() -> list[EditorialBeat]:
    texts = [
        "Kennst du das auch, wenn jede Stufe plötzlich zum Hindernis wird?",
        "Du planst jeden Weg und verlierst dabei jeden Tag wertvolle Freiheit.",
        "Mit der passenden Lösung bewegst du dich zuhause wieder sicher.",
        "Lass dich jetzt persönlich beraten und finde deinen nächsten Schritt.",
    ]
    durations = [4, 6, 4, 6]
    return [
        EditorialBeat(
            index=index,
            text=text,
            word_count=len(text.split()),
            estimated_speech_seconds=duration - 0.75,
            provider_duration_seconds=duration,
        )
        for index, (text, duration) in enumerate(zip(texts, durations))
    ]


def test_derive_shot_deck_returns_four_deterministic_immutable_png_variants():
    from app.features.shot_production.shot_deck import ShotVariant, derive_shot_deck

    source = _png_bytes()
    expected_hash = sha256(source).hexdigest()

    first = derive_shot_deck(
        approved_master_bytes=source,
        expected_sha256=expected_hash,
        mime_type="image/png",
    )
    second = derive_shot_deck(
        approved_master_bytes=source,
        expected_sha256=expected_hash,
        mime_type="image/png",
    )

    assert [field.name for field in fields(ShotVariant)] == [
        "index",
        "name",
        "source_sha256",
        "output_sha256",
        "crop_box",
        "width",
        "height",
        "mime_type",
        "image_bytes",
    ]
    assert len(first) == 4
    assert [variant.index for variant in first] == [0, 1, 2, 3]
    assert [variant.name for variant in first] == ["original", "center", "left", "right"]
    assert first == second

    assert first[0].image_bytes == source
    assert first[0].output_sha256 == expected_hash
    assert first[0].crop_box == (0, 0, 90, 160)

    for variant in first:
        assert variant.source_sha256 == expected_hash
        assert variant.output_sha256 == sha256(variant.image_bytes).hexdigest()
        assert (variant.width, variant.height) == (90, 160)
        assert variant.mime_type == "image/png"
        with Image.open(io.BytesIO(variant.image_bytes)) as image:
            assert image.format == "PNG"
            assert image.size == (90, 160)

        left, top, right, bottom = variant.crop_box
        assert 90 / (right - left) <= 1.06
        assert 160 / (bottom - top) <= 1.06

    assert len({variant.output_sha256 for variant in first}) == 4
    with pytest.raises(FrozenInstanceError):
        first[0].name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("image_bytes", "mime_type", "expected_hash", "message"),
    [
        (_png_bytes(), "image/png", "0" * 64, "SHA-256"),
        (_png_bytes(), "image/jpeg", sha256(_png_bytes()).hexdigest(), "PNG MIME"),
        (b"", "image/png", sha256(b"").hexdigest(), "non-empty"),
        (b"not-an-image", "image/png", sha256(b"not-an-image").hexdigest(), "valid PNG"),
        (_png_bytes(160, 90), "image/png", sha256(_png_bytes(160, 90)).hexdigest(), "vertical"),
    ],
)
def test_derive_shot_deck_fails_closed_for_unapproved_or_invalid_master(
    image_bytes: bytes,
    mime_type: str,
    expected_hash: str,
    message: str,
):
    from app.features.shot_production.shot_deck import derive_shot_deck

    with pytest.raises(ValidationError, match=message):
        derive_shot_deck(
            approved_master_bytes=image_bytes,
            expected_sha256=expected_hash,
            mime_type=mime_type,
        )


def test_compile_veo_take_requests_locks_first_frame_and_maps_beats_deterministically():
    from app.features.shot_production.prompts import (
        EFFECTIVE_NEGATIVE_PROMPT,
        VEO_MODEL,
        compile_veo_take_requests,
    )
    from app.features.shot_production.shot_deck import derive_shot_deck

    source = _png_bytes()
    deck = derive_shot_deck(
        approved_master_bytes=source,
        expected_sha256=sha256(source).hexdigest(),
        mime_type="image/png",
    )
    beats = _beats()

    requests = compile_veo_take_requests(beats=beats, shot_deck=deck, base_seed=240711)

    assert len(requests) == len(beats)
    assert [request.shot for request in requests] == list(deck)
    assert [request.model for request in requests] == [VEO_MODEL] * 4
    assert VEO_MODEL == "veo-3.1-generate-001"
    assert [request.aspect_ratio for request in requests] == ["9:16"] * 4
    assert [request.duration_seconds for request in requests] == [4, 6, 4, 6]
    assert [request.seed for request in requests] == [240711, 240712, 240713, 240714]

    for beat, request in zip(beats, requests):
        assert request.beat == beat
        assert request.prompt.count(beat.text) == 1
        assert "sole visual truth" in request.prompt
        assert "cream knit sweater" in request.prompt
        assert "native German" in request.prompt
        assert "naturally stop" in request.prompt
        assert "38-year-old" not in request.prompt
        assert "hazel" not in request.prompt
        assert "terracotta" not in request.prompt
        assert "seated" not in request.prompt.lower()
        assert "hands" not in request.prompt.lower()
        assert request.negative_prompt == EFFECTIVE_NEGATIVE_PROMPT
        assert request.negative_prompt.strip()

        submit_kwargs = request.as_vertex_submit_kwargs()
        assert set(submit_kwargs) == {
            "prompt",
            "image_bytes",
            "mime_type",
            "aspect_ratio",
            "duration_seconds",
            "model",
            "negative_prompt",
            "seed",
        }
        assert submit_kwargs["image_bytes"] == request.shot.image_bytes

    blocked_changes = (
        "face change",
        "age change",
        "hair change",
        "wardrobe change",
        "room change",
        "extra person",
        "zoom",
        "push-in",
        "reframe",
        "posture reset",
        "generated text",
        "subtitles",
        "music",
        "background voices",
        "extra speech",
    )
    negative = EFFECTIVE_NEGATIVE_PROMPT.lower()
    assert all(change in negative for change in blocked_changes)


@pytest.mark.parametrize("negative_prompt", ["", "generic blur and artifacts"])
def test_compile_veo_take_requests_rejects_missing_negative_prompt_locks(negative_prompt):
    from app.features.shot_production.prompts import compile_veo_take_requests
    from app.features.shot_production.shot_deck import derive_shot_deck

    source = _png_bytes()
    deck = derive_shot_deck(
        approved_master_bytes=source,
        expected_sha256=sha256(source).hexdigest(),
        mime_type="image/png",
    )

    with pytest.raises(ValidationError, match="negative prompt"):
        compile_veo_take_requests(
            beats=_beats(),
            shot_deck=deck,
            base_seed=12,
            negative_prompt=negative_prompt,
        )
