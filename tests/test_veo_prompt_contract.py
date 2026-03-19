import importlib
import os

from app.features.posts.prompt_builder import build_video_prompt_from_seed, split_dialogue_sentences


def test_veo_prompt_requires_exact_german_dialogue():
    script = (
        "Bevor du deinen barrierefreien Umbau startest, sichere dir unbedingt "
        "rechtzeitig die passenden Zuschüsse."
    )
    prompt = build_video_prompt_from_seed({"script": script})

    veo_prompt = prompt["veo_prompt"]
    assert "Critical speech requirements:" not in veo_prompt
    assert "She speaks in German at a natural conversational pace" in veo_prompt
    assert "saying the line exactly as written below" in veo_prompt
    assert "German dialogue, say exactly as written:" in veo_prompt
    assert "she stops speaking immediately, closes her mouth" in veo_prompt
    assert f"\"{script}\"" in veo_prompt


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
        "Die Pflegekasse zahlt bis zu 4.180 Euro pro Person, "
        "und auch die KfW-Förderung startet bald wieder. "
        "Beantrage die Hilfe rechtzeitig."
    )
    prompt = video_poller._build_veo_extension_prompt(
        {
            "seed_data": {"script": script},
        }
    )

    prompt_text = prompt["prompt_text"]
    assert "Character:" in prompt_text
    assert "German dialogue, say exactly as written:" in prompt_text
    assert "Die Pflegekasse zahlt bis zu 4.180 Euro pro Person" in prompt_text
    assert "Do not end the speech yet" in prompt_text


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


def test_split_dialogue_sentences_keeps_sentence_boundaries():
    script = "Erster Satz. Zweiter Satz! Dritter Satz?"
    segments = split_dialogue_sentences(script)
    assert segments == ["Erster Satz.", "Zweiter Satz!", "Dritter Satz?"]


def test_split_dialogue_sentences_ignores_trailing_fragment():
    script = "Erster Satz. Zweiter Satz. Abgeschnittener Rest ohne Punkt"
    segments = split_dialogue_sentences(script)
    assert segments == ["Erster Satz.", "Zweiter Satz."]
