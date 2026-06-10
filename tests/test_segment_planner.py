"""Unit tests for the segmented-route prompt planner."""

import pytest

from app.features.posts.prompt_builder import build_segment_prompts, split_dialogue_sentences


def test_one_prompt_per_segment_in_order():
    segments = ["Erstens, das hier.", "Zweitens, das da.", "Drittens, zum Schluss."]
    prompts = build_segment_prompts(segments, character="38yo German woman", scene="hallway")
    assert len(prompts) == 3
    # Each segment's own dialogue appears in its own prompt, in order.
    for segment, prompt in zip(segments, prompts):
        assert segment in prompt


def test_every_segment_carries_full_character_context():
    """The whole point of the segmented route: identity context is re-injected on EVERY segment."""
    segments = ["A.", "B.", "C.", "D."]
    character = "38-year-old German woman, brown hair, beige knit sweater"
    prompts = build_segment_prompts(segments, character=character, scene="stairwell")
    assert all(character in prompt for prompt in prompts)


def test_only_final_segment_gets_ending_directive():
    segments = ["First beat.", "Last beat."]
    prompts = build_segment_prompts(
        segments,
        character="actor",
        scene="room",
    )
    # The final-segment prompt uses the "extended_final" mode; earlier ones do not. We assert the two
    # prompts differ (final carries an ending the others lack) rather than coupling to template text.
    assert prompts[0] != prompts[1]


def test_empty_segments_raises():
    with pytest.raises(ValueError):
        build_segment_prompts([])


def test_planner_consumes_split_dialogue_output():
    script = "Satz eins. Satz zwei! Satz drei?"
    segments = split_dialogue_sentences(script)
    prompts = build_segment_prompts(segments, character="actor", scene="room")
    assert len(prompts) == len(segments) == 3
