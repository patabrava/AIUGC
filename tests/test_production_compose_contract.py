from pathlib import Path

import yaml

from app.core.config import Settings

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.production.yml"
LEGACY_COMPOSE_PATHS = [
    Path(__file__).resolve().parents[1] / "docker-compose.yml",
    Path(__file__).resolve().parents[1] / "docker-compose.yaml",
]
DEPLOY_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "production.sh"
WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-production.yml"


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
        "TIKTOK_ENVIRONMENT=production",
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
    assert '${TRAEFIK_HOST_RULE:-Host(`lippelift.xyz`)}' in compose_text
    assert "srv1498567.hstgr.cloud" not in compose_text
    assert "TRAEFIK_HOST_RULE" in compose_text
    assert "TRAEFIK_ENTRYPOINTS" in compose_text
    assert "TRAEFIK_CERTRESOLVER" in compose_text
    assert "traefik.http.routers.lippelift-prod-web-v3.rule" in compose_text
    assert "TRAEFIK_NETWORK" not in compose_text
    assert "external: true" not in compose_text


def test_production_compose_has_live_healthcheck():
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    health = data["services"]["web"]["healthcheck"]
    assert health["test"][-1] == "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"


def test_legacy_compose_files_follow_production_build_contract():
    assert LEGACY_COMPOSE_PATHS[0].read_text(encoding="utf-8") == LEGACY_COMPOSE_PATHS[1].read_text(encoding="utf-8")
    for path in LEGACY_COMPOSE_PATHS:
        compose_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(compose_text)
        web = data["services"]["web"]
        worker = data["services"]["worker"]
        assert web["build"]["context"] == "."
        assert worker["build"]["context"] == "."
        assert any(".env.production" in entry for entry in web["env_file"])
        assert "DOCKER_BUILD_CONTEXT" not in compose_text
        assert "git clone" not in compose_text
        assert '${TRAEFIK_HOST_RULE:-Host(`lippelift.xyz`)}' in compose_text
        assert "TRAEFIK_NETWORK" not in compose_text
        assert "traefik.http.routers.lippelift-prod-web-v3.rule" in compose_text


def test_production_deploy_script_contract():
    script_text = DEPLOY_SCRIPT_PATH.read_text(encoding="utf-8")
    required = [
        "git fetch origin main",
        "git merge --ff-only origin/main",
        "COMPOSE_CMD=(docker compose)",
        "command -v docker-compose",
        'echo "Neither \'docker compose\' nor \'docker-compose\' is available on the production host."',
        '--env-file "$ENV_FILE"',
        'up -d --build --remove-orphans',
        '"${COMPOSE_CMD[@]}" -f docker-compose.production.yml',
        'curl --fail --silent --show-error --connect-timeout 5 --max-time 10 "$HEALTHCHECK_URL"',
    ]
    for item in required:
        assert item in script_text
    assert 'HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://lippelift.xyz/health}"' in script_text


def test_hostinger_runtime_checkout_tracks_remote_main():
    compose_text = (Path(__file__).resolve().parents[1] / "docker-compose.hostinger-runtime.yaml").read_text(encoding="utf-8")
    assert 'git checkout -f -B "$$repo_ref" "origin/$$repo_ref"' in compose_text
    assert 'git reset --hard "origin/$$repo_ref"' in compose_text
    assert "traefik.http.routers.lippelift-prod-web-v3.rule" in compose_text
    assert "TIKTOK_ENVIRONMENT: ${TIKTOK_ENVIRONMENT:-production}" in compose_text
    assert "external: true" not in compose_text


def test_hostinger_runtime_does_not_include_web_healthcheck():
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "docker-compose.hostinger-runtime.yaml").read_text(encoding="utf-8"))
    assert "healthcheck" not in data["services"]["web"]


def test_github_action_deploys_on_push_to_main():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert data["name"] == "Deploy Production"
    workflow_on = data.get("on", data.get(True))
    assert workflow_on["push"]["branches"] == ["main"]
    assert data["jobs"]["deploy"]["environment"] == "production"
    steps = data["jobs"]["deploy"]["steps"]
    step_text = "\n".join(str(step) for step in steps)
    assert "actions/checkout@v5" in step_text
    assert "appleboy/ssh-action" in step_text
    assert "scripts/deploy/production.sh" in step_text
    assert "mkdir -p \"$APP_ROOT\"" in step_text
    assert "git clone https://github.com/patabrava/AIUGC.git \"$REPO_DIR\"" in step_text
    assert "git fetch origin main" in step_text
    assert "git checkout -B main origin/main" in step_text
    assert "git reset --hard origin/main" in step_text
    assert "git clean -fd" in step_text
    assert "export APP_ROOT=\"${APP_ROOT:-/opt/aiugc-prod}\"" in step_text
    assert "export REPO_DIR=\"${APP_ROOT}/repo\"" in step_text
    assert "export ENV_FILE=\"${APP_ROOT}/.env.production\"" in step_text
    assert "Validate SSH deploy config" in step_text
    assert "Missing PROD_SSH_HOST" in step_text
    assert "env.PROD_SSH_HOST" in step_text
