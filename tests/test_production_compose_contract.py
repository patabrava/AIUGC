from pathlib import Path

import yaml

from app.core.config import Settings

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.production.yml"


def test_settings_respect_app_env_file_override(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=https://example.supabase.co",
                "SUPABASE_KEY=public-key",
                "SUPABASE_SERVICE_KEY=service-key",
                "CLOUDFLARE_R2_PUBLIC_BASE_URL=https://r2.example.com",
                "APP_URL=https://example.com",
                "ENVIRONMENT=production",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV_FILE", str(env_file))
    settings = Settings()
    assert settings.app_url == "https://example.com"
    assert settings.environment == "production"


def test_example_production_env_lists_required_live_keys():
    env_text = Path(__file__).resolve().parents[1].joinpath(".env.production.example").read_text(encoding="utf-8")
    required = [
        "SUPABASE_URL=",
        "SUPABASE_SERVICE_KEY=",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON=",
        "APP_URL=",
        "ENVIRONMENT=production",
        "VERTEX_AI_PROJECT_ID=",
        "CLOUDFLARE_R2_BUCKET_NAME=",
    ]
    for item in required:
        assert item in env_text


def test_production_compose_uses_repo_build_and_server_env_file():
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(compose_text)
    web = data["services"]["web"]
    worker = data["services"]["worker"]
    assert web["build"]["context"] == "."
    assert worker["build"]["context"] == "."
    assert any(".env.production" in entry for entry in web["env_file"])
    assert "DOCKER_BUILD_CONTEXT" not in compose_text
    assert "/opt/aiugc-prod/.env.production" not in compose_text
    assert "lippelift.xyz" not in compose_text
    assert "srv1498567.hstgr.cloud" not in compose_text
    assert "TRAEFIK_HOST_RULE" in compose_text
    assert "TRAEFIK_ENTRYPOINTS" in compose_text
    assert "TRAEFIK_CERTRESOLVER" in compose_text
    assert "TRAEFIK_NETWORK" in compose_text


def test_production_compose_has_live_healthcheck():
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    health = data["services"]["web"]["healthcheck"]
    assert "/health" in "".join(health["test"])
