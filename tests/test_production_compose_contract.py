from pathlib import Path

from app.core.config import Settings


def test_settings_respect_app_env_file_override(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=https://example.supabase.co",
                "SUPABASE_KEY=public-key",
                "SUPABASE_SERVICE_KEY=service-key",
                "CLOUDFLARE_R2_PUBLIC_BASE_URL=https://r2.example.com",
                "APP_URL=https://lippelift.xyz",
                "ENVIRONMENT=production",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV_FILE", str(env_file))
    settings = Settings()
    assert settings.app_url == "https://lippelift.xyz"
    assert settings.environment == "production"


def test_example_production_env_lists_required_live_keys():
    env_text = Path(".env.production.example").read_text(encoding="utf-8")
    required = [
        "SUPABASE_URL=",
        "SUPABASE_SERVICE_KEY=",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON=",
        "APP_URL=https://lippelift.xyz",
        "ENVIRONMENT=production",
        "VERTEX_AI_PROJECT_ID=",
        "CLOUDFLARE_R2_BUCKET_NAME=",
    ]
    for item in required:
        assert item in env_text
