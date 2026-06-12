"""Prompt checks for segmented character-consistency anchor clips."""

from app.features.posts.prompt_builder import build_character_consistency_mid_base_prompt


def test_segmented_character_anchor_prompt_requests_pose_variety():
    prompt = build_character_consistency_mid_base_prompt(
        "Das ist ein kurzer Testsatz.",
        include_final_ending=False,
        segmented_anchor=True,
    )

    assert "anchor segment" in prompt
    assert "distinct pose changes" in prompt
    assert "lean" in prompt
    assert "hand position" in prompt
    assert "glance" in prompt
    assert "no concluding pause" in prompt
