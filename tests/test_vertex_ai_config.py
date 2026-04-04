from app.core.config import Settings


def test_vertex_settings_default_to_disabled():
    settings = Settings.model_validate(
        {
            "supabase_url": "https://example.supabase.co",
            "SUPABASE_KEY": "x",
            "SUPABASE_SERVICE_KEY": "y",
            "cloudflare_r2_public_base_url": "https://r2.example.com",
        }
    )
    assert settings.vertex_ai_project_id == ""
    assert settings.vertex_ai_location == "us-central1"
    assert settings.vertex_ai_enabled is False


def test_vertex_settings_use_explicit_project_and_location():
    settings = Settings.model_validate(
        {
            "supabase_url": "https://example.supabase.co",
            "SUPABASE_KEY": "x",
            "SUPABASE_SERVICE_KEY": "y",
            "cloudflare_r2_public_base_url": "https://r2.example.com",
            "vertex_ai_project_id": "my-project",
            "vertex_ai_location": "europe-west4",
            "vertex_ai_enabled": True,
        }
    )
    assert settings.vertex_ai_project_id == "my-project"
    assert settings.vertex_ai_location == "europe-west4"
    assert settings.vertex_ai_enabled is True
