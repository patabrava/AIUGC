"""Prompt checks for segmented character-consistency anchor clips."""

from app.features.posts.prompt_builder import (
    build_character_consistency_mid_base_prompt,
    build_character_consistency_mid_continuation_prompt,
)


def test_segmented_character_anchor_prompt_locks_pose_and_framing():
    prompt = build_character_consistency_mid_base_prompt(
        "Das ist ein kurzer Testsatz.",
        include_final_ending=False,
        segmented_anchor=True,
    )

    assert "stable seated posture" in prompt
    assert "same camera distance" in prompt
    assert "neutral forward-facing pose" in prompt
    assert "distinct pose changes" not in prompt
    assert "lean slightly" not in prompt
    assert "off-camera glance" not in prompt
    assert "no concluding pause" in prompt


def test_segmented_character_i2v_prompt_locks_first_frame_continuity():
    prompt = build_character_consistency_mid_continuation_prompt(
        "Der naechste Satz beginnt hier.",
        include_final_ending=False,
    )

    assert "Continue from the supplied first frame" in prompt
    assert "same camera distance" in prompt
    assert "same medium vertical smartphone composition" in prompt
    assert "intentional UGC jump cut" not in prompt
    assert "fresh 8-second take" not in prompt
    assert "posture reset" not in prompt
    assert "mouth closes fully" in prompt
