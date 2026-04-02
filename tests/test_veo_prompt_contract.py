import importlib
import os

import pytest

from app.features.posts.prompt_builder import build_video_prompt_from_seed, split_dialogue_sentences, build_optimized_prompt, build_veo_prompt_segment
from app.features.posts.prompt_defaults import DEFAULT_SCENE, DEFAULT_SCENE_BODY, LEGACY_SCENE
from app.features.posts.schemas import AudioSection, UpdatePromptRequest, VideoPrompt


def test_veo_prompt_requires_exact_german_dialogue():
    script = (
        "Bevor du deinen barrierefreien Umbau startest, sichere dir unbedingt "
        "rechtzeitig die passenden Zuschüsse."
    )
    prompt = build_video_prompt_from_seed({"script": script})

    veo_prompt = prompt["veo_prompt"]
    assert "Character:" in veo_prompt
    assert "Dialogue:" in veo_prompt
    assert script in veo_prompt
    assert "After the final spoken word, speech stops completely." in veo_prompt
    assert "mouth comes to rest" in veo_prompt
    assert prompt["audio"]["dialogue"] == script
    assert prompt["audio_block"] == prompt["audio"]["capture"]
    assert prompt["ending_directive"].startswith("After the final spoken word")


def test_build_optimized_prompt_supports_custom_sections():
    result = build_optimized_prompt(
        "Test dialogue.",
        negative_constraints=None,
        character="A 25-year-old man with dark hair.",
        action="Custom action block.",
        style="Cinematic drone footage.",
        scene="An open rooftop at sunset.",
        cinematography="Wide-angle lens, slow dolly.",
        ending="He turns away and walks off.",
        audio_block="Studio recording with boom mic.",
    )

    assert "A 25-year-old man with dark hair." in result
    assert "Cinematic drone footage." in result
    assert "Custom action block." in result
    assert "An open rooftop at sunset." in result
    assert "Wide-angle lens, slow dolly." in result
    assert "He turns away and walks off." in result
    assert "Studio recording with boom mic." in result
    assert "Test dialogue." in result
    assert "38-year-old German woman" not in result


def test_build_veo_prompt_segment_supports_custom_sections():
    result = build_veo_prompt_segment(
        "Test dialogue.",
        include_quotes=False,
        include_ending=True,
        character="A 25-year-old man with dark hair.",
        action="Custom action block.",
        style="Cinematic drone footage.",
        scene="An open rooftop at sunset.",
        cinematography="Wide-angle lens, slow dolly.",
        ending="He turns away and walks off.",
        audio_block="Studio recording with boom mic.",
        negative_constraints="No subtitles, no logos.",
    )

    assert "A 25-year-old man with dark hair." in result
    assert "Cinematic drone footage." in result
    assert "Custom action block." in result
    assert "An open rooftop at sunset." in result
    assert "Wide-angle lens, slow dolly." in result
    assert "He turns away and walks off." in result
    assert "Studio recording with boom mic." in result
    assert "No subtitles, no logos." in result
    assert "Test dialogue." in result


def test_scene_default_is_shared_between_schema_and_prompt_builder():
    assert DEFAULT_SCENE.startswith("Scene: ")
    assert DEFAULT_SCENE_BODY in DEFAULT_SCENE
    assert VideoPrompt.model_fields["scene"].default == DEFAULT_SCENE

    prompt = build_video_prompt_from_seed({"script": "Beispielsatz."})
    assert prompt["scene"] == DEFAULT_SCENE


def test_legacy_scene_is_refreshed_for_display_only():
    from app.features.batches.handlers import _refresh_prompt_scene_for_display

    refreshed = _refresh_prompt_scene_for_display({"scene": LEGACY_SCENE, "style": "kept"})
    assert refreshed is not None
    assert refreshed["scene"] == DEFAULT_SCENE
    assert refreshed["style"] == "kept"

    custom_scene = {"scene": "Scene: A custom attic studio."}
    assert _refresh_prompt_scene_for_display(custom_scene) is custom_scene


def test_video_prompt_and_update_request_roundtrip_editable_fields():
    prompt = VideoPrompt(
        audio=AudioSection(dialogue="Hallo Welt", capture="Audio block"),
        ending_directive="She rests.",
        audio_block="Audio block",
    )
    assert "The camera is stable" in prompt.cinematography
    assert "handheld but stable" not in prompt.cinematography
    restored = VideoPrompt.model_validate(prompt.model_dump())
    assert restored.audio.dialogue == "Hallo Welt"
    assert restored.ending_directive == "She rests."
    assert restored.audio_block == "Audio block"

    request = UpdatePromptRequest(
        character="Custom character",
        style="Custom style",
        action="Custom action",
        scene="Custom scene",
        cinematography="Custom cinematography",
        dialogue="Custom dialogue",
        ending="Custom ending",
        audio_block="Custom audio",
        universal_negatives="Custom negatives",
        veo_negative_prompt="Custom veo negatives",
    )
    assert request.dialogue == "Custom dialogue"


def test_update_prompt_request_rejects_empty_fields():
    with pytest.raises(Exception):
        UpdatePromptRequest(
            character="",
            style="Custom style",
            action="Custom action",
            scene="Custom scene",
            cinematography="Custom cinematography",
            dialogue="Custom dialogue",
            ending="Custom ending",
            audio_block="Custom audio",
            universal_negatives="Custom negatives",
            veo_negative_prompt="Custom veo negatives",
        )


def test_veo_extension_prompt_preserves_approved_german_script():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
    os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
    os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
    os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
    os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
    os.environ.setdefault("CRON_SECRET", "test-cron-secret")
    video_poller = importlib.import_module("workers.video_poller")

    script = (
        "Die Pflegekasse zahlt bis zu viertausend Euro pro Person. "
        "Beantrage die Hilfe rechtzeitig."
    )
    prompt = video_poller._build_veo_extension_prompt(
        {
            "seed_data": {"script": script},
            "video_metadata": {
                "veo_extension_hops_target": 2,
                "veo_extension_hops_completed": 0,
            },
        }
    )

    prompt_text = prompt["prompt_text"]
    assert "Character:" in prompt_text
    assert "Die Pflegekasse zahlt bis zu viertausend Euro pro Person." in prompt_text
    assert "Do not end the speech yet." in prompt_text
    assert "brisk but natural pacing" in prompt_text
    assert "no settling room tone" in prompt_text
    assert "mouth closes" not in prompt_text
    assert "After the final spoken word" not in prompt_text


def test_veo_extension_prompt_uses_saved_prompt_sections_over_seed_defaults():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
    os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
    os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
    os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
    os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
    os.environ.setdefault("CRON_SECRET", "test-cron-secret")
    video_poller = importlib.import_module("workers.video_poller")

    prompt = video_poller._build_veo_extension_prompt(
        {
            "seed_data": {"script": "Old seed sentence one. Old seed sentence two."},
            "video_prompt_json": {
                "character": "Edited character",
                "style": "Edited style",
                "action": "Edited action",
                "scene": "Edited scene",
                "cinematography": "Edited cinematography",
                "audio": {"dialogue": "Edited dialogue one. Edited dialogue two.", "capture": "Edited audio block"},
                "ending_directive": "Edited ending",
                "audio_block": "Edited audio block",
                "veo_negative_prompt": "Edited negative prompt",
            },
            "video_metadata": {
                "veo_extension_hops_target": 2,
                "veo_extension_hops_completed": 0,
            },
        },
        segment_index=0,
    )

    prompt_text = prompt["prompt_text"]
    assert "Edited character" in prompt_text
    assert "Edited style" in prompt_text
    assert "Edited action" in prompt_text
    assert "Edited scene" in prompt_text
    assert "Edited cinematography" in prompt_text
    assert "Edited dialogue one." in prompt_text
    assert "Old seed sentence one." not in prompt_text
    assert "Edited negative prompt" in prompt_text
    assert "Do not end the speech yet." in prompt_text


def test_veo_extension_prompt_uses_requested_next_segment():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
    os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
    os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
    os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
    os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
    os.environ.setdefault("CRON_SECRET", "test-cron-secret")
    video_poller = importlib.import_module("workers.video_poller")

    script = "Erster Satz. Zweiter Satz. Dritter Satz."
    prompt = video_poller._build_veo_extension_prompt(
        {"seed_data": {"script": script}},
        segment_index=1,
    )

    prompt_text = prompt["prompt_text"]
    assert "Zweiter Satz." in prompt_text
    assert "Erster Satz." not in prompt_text
    assert "Dritter Satz." not in prompt_text


def test_veo_extension_prompt_prefers_packed_metadata_segments_over_raw_script():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
    os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
    os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
    os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
    os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
    os.environ.setdefault("CRON_SECRET", "test-cron-secret")
    video_poller = importlib.import_module("workers.video_poller")

    prompt = video_poller._build_veo_extension_prompt(
        {
            "seed_data": {
                "script": (
                    "So kurz ist die europaweite Frist. "
                    "Die neue EU-Verordnung hat die alte 48-Stunden-Frist halbiert. "
                    "Innerhalb Deutschlands wird aber weiterhin eine Anmeldung bis 20 Uhr am Vortag dringend empfohlen. "
                    "Auch Oesterreich hat spezielle Fristen."
                )
            },
            "video_metadata": {
                "veo_segments": [
                    (
                        "So kurz ist die europaweite Frist. "
                        "Die neue EU-Verordnung hat die alte 48-Stunden-Frist halbiert."
                    ),
                    "Innerhalb Deutschlands wird aber weiterhin eine Anmeldung bis 20 Uhr am Vortag dringend empfohlen.",
                    "Auch Oesterreich hat spezielle Fristen.",
                ],
                "veo_extension_hops_target": 2,
                "veo_extension_hops_completed": 0,
            },
        },
        segment_index=1,
    )

    prompt_text = prompt["prompt_text"]
    assert "Innerhalb Deutschlands wird aber weiterhin eine Anmeldung bis 20 Uhr am Vortag dringend empfohlen." in prompt_text
    assert "Die neue EU-Verordnung hat die alte 48-Stunden-Frist halbiert." not in prompt_text
    assert "Auch Oesterreich hat spezielle Fristen." not in prompt_text


def test_veo_extension_prompt_final_hop_uses_explicit_stop_and_mouth_rest():
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
    os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
    os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
    os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
    os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
    os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
    os.environ.setdefault("CRON_SECRET", "test-cron-secret")
    video_poller = importlib.import_module("workers.video_poller")

    prompt = video_poller._build_veo_extension_prompt(
        {
            "seed_data": {"script": "Erster Satz. Letzter Satz."},
            "video_metadata": {
                "veo_extension_hops_target": 2,
                "veo_extension_hops_completed": 1,
            },
        },
        segment_index=1,
    )

    prompt_text = prompt["prompt_text"]
    assert "Letzter Satz." in prompt_text
    assert "After the final spoken word, speech stops completely." in prompt_text
    assert "mouth closes and comes fully to rest" in prompt_text
    assert "no settling room tone" not in prompt_text


def test_split_dialogue_sentences_keeps_sentence_boundaries():
    script = "Erster Satz. Zweiter Satz! Dritter Satz?"
    segments = split_dialogue_sentences(script)
    assert segments == ["Erster Satz.", "Zweiter Satz!", "Dritter Satz?"]


def test_split_dialogue_sentences_appends_trailing_fragment_to_last():
    script = "Erster Satz. Zweiter Satz. Abgeschnittener Rest ohne Punkt"
    segments = split_dialogue_sentences(script)
    assert segments == ["Erster Satz.", "Zweiter Satz. Abgeschnittener Rest ohne Punkt"]
