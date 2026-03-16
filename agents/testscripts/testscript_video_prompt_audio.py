"""
Prompt audio regression testscript.
Verifies provider prompt text uses one normalized audio block with natural room-tone decay.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.features.posts.prompt_builder import STANDARD_AUDIO_BLOCK, VEO_NEGATIVE_PROMPT, build_video_prompt_from_seed
from app.features.videos.handlers import _build_provider_prompt_request


FORBIDDEN_TERMS = (
    "dead-silent",
    "dead silent",
    "perfect silence",
    "absolute silence",
    "absolutely silent",
    "zero room tone",
    "zero room-tone",
    "rt60",
    "dbfs",
    "lufs",
)


def assert_forbidden_terms_absent(text: str) -> None:
    lowered = text.lower()
    for term in FORBIDDEN_TERMS:
        assert term not in lowered, f"Forbidden audio term found: {term}"


def main() -> None:
    prompt = build_video_prompt_from_seed(
        {
            "script": "Ich erkläre dir heute kurz, warum dieses Produkt meinen Alltag wirklich einfacher macht."
        }
    )

    assert prompt["audio"]["dialogue"] == STANDARD_AUDIO_BLOCK
    assert prompt["audio"]["capture"] == ""
    assert prompt["post"] == ""
    assert prompt["sound_effects"] == ""
    assert prompt["optimized_prompt"].count("Audio:") == 1
    assert prompt["optimized_prompt"].count("Universal Negatives") == 1
    assert "quiet room tone" in prompt["optimized_prompt"]
    assert_forbidden_terms_absent(prompt["optimized_prompt"])
    assert prompt["veo_prompt"].count("Audio:") == 1
    assert "Universal Negatives" not in prompt["veo_prompt"]
    assert prompt["veo_negative_prompt"] == VEO_NEGATIVE_PROMPT
    assert "background voices" in prompt["veo_negative_prompt"]
    assert "no background voices" not in prompt["veo_negative_prompt"].lower()
    assert_forbidden_terms_absent(prompt["veo_prompt"])

    for provider in ("veo_3_1", "sora_2"):
        request_payload = _build_provider_prompt_request(prompt, provider)
        prompt_text = request_payload["prompt_text"] or ""
        assert prompt_text.count("Audio:") == 1, f"{provider} prompt should contain exactly one audio block"
        assert "no cuts or angle changes" in prompt_text.lower()
        assert "quiet room tone" in prompt_text.lower()
        assert_forbidden_terms_absent(prompt_text)
        if provider == "veo_3_1":
            assert request_payload["negative_prompt"] == VEO_NEGATIVE_PROMPT
            assert "Universal Negatives" not in prompt_text
        else:
            assert request_payload["negative_prompt"] is None
            assert "Universal Negatives" in prompt_text

    print("TS-video-prompt-audio: PASS")
    print("  single audio block present for Sora and Veo")
    print("  Veo uses positive prompt plus dedicated negativePrompt")
    print("  natural room-tone decay preserved at clip end")
    print("  forbidden silence and engineering audio terms absent")


if __name__ == "__main__":
    main()
