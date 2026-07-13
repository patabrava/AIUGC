from dataclasses import FrozenInstanceError

import pytest

from app.features.shot_production.duration import build_semantic_duration_contract


@pytest.mark.parametrize(
    ("seconds", "takes", "minimum_words", "maximum_words"),
    [
        (8, 1, 14, 18),
        (16, 2, 29, 36),
        (32, 4, 61, 72),
        (50, 7, 109, 118),
        (60, 8, 127, 142),
    ],
)
def test_semantic_duration_examples(seconds, takes, minimum_words, maximum_words):
    contract = build_semantic_duration_contract(seconds)

    assert contract.minimum_take_count == takes
    assert (contract.minimum_words, contract.maximum_words) == (
        minimum_words,
        maximum_words,
    )
    assert len(contract.contract_hash) == 64


def test_every_supported_integer_has_a_valid_contract():
    for seconds in range(8, 61):
        contract = build_semantic_duration_contract(seconds)

        assert contract.minimum_words <= contract.maximum_words
        assert contract.minimum_words > 18 * (contract.minimum_take_count - 1)


@pytest.mark.parametrize("seconds", [7, 61, 8.5, True, float("nan")])
def test_semantic_duration_rejects_invalid_values(seconds):
    with pytest.raises(ValueError):
        build_semantic_duration_contract(seconds)


def test_explicit_maximum_and_environment_maximum_are_fail_closed(monkeypatch):
    monkeypatch.setenv("SEMANTIC_UGC_MAX_DURATION_SECONDS", "50")

    assert build_semantic_duration_contract(50).maximum_duration_seconds == 50
    with pytest.raises(ValueError):
        build_semantic_duration_contract(51)

    assert (
        build_semantic_duration_contract(51, maximum_seconds=60).maximum_duration_seconds
        == 60
    )
    monkeypatch.setenv("SEMANTIC_UGC_MAX_DURATION_SECONDS", "invalid")
    with pytest.raises(ValueError):
        build_semantic_duration_contract(8)


def test_contract_is_immutable_and_hashes_canonical_json():
    first = build_semantic_duration_contract(33)
    second = build_semantic_duration_contract(33)

    assert first.as_dict() == second.as_dict()
    assert first.contract_hash == second.contract_hash
    with pytest.raises(FrozenInstanceError):
        first.minimum_words = 1
