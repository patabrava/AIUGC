# Production Auto-Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every successful push to `main` deploy the exact same app code to `lippelift.xyz` automatically, without manual Hostinger redeploy clicks and without depending on repo `.env` secrets.

**Architecture:** Move production deployment ownership from the Hostinger repo-wrapper to the repository itself. Build the production containers from the checked-out repo on the VPS, keep production secrets in a server-only env file, and trigger deploys from GitHub Actions over SSH on every push to `main`, with a health check gate and rollback-ready logs.

**Tech Stack:** Docker Compose, GitHub Actions, OpenSSH, FastAPI, existing Python app, existing Hostinger VPS

**Plan Budget:** `{files: 9, LOC/file: <=250 new or touched, deps: 0}`

---

## File Structure

- Create: `.github/workflows/deploy-production.yml`
  - GitHub Actions workflow that runs on pushes to `main`, uploads nothing, SSHes into the VPS, and runs the deploy script.
- Create: `Dockerfile`
  - Single application image used by `web`, `worker`, `topic-worker`, `expansion-worker`, and `caption-worker`.
- Create: `docker-compose.production.yml`
  - Production-only compose file that builds from the repo checkout and reads secrets from a server-managed env file.
- Create: `.env.production.example`
  - Canonical list of required production secrets and non-secret config, with no real values.
- Create: `scripts/deploy/production.sh`
  - Idempotent server-side deploy entrypoint used by GitHub Actions and manual recovery.
- Create: `tests/test_production_compose_contract.py`
  - Regression tests for the compose/workflow/settings contract.
- Create: `docs/deployment/production-auto-deploy.md`
  - Operator runbook for initial VPS setup, GitHub secrets, deploy flow, and rollback.
- Modify: `app/core/config.py`
  - Allow an explicit env-file override for non-container entrypoints and make the deployment contract predictable.
- Modify: `AGENTS.md`
  - Add one dense repo rule capturing the deployment fix once implementation is done.

## Assumptions

- Production remains on the existing Hostinger VPS and existing `lippelift.xyz` Traefik routing.
- GitHub can store deployment secrets:
  - `PROD_SSH_HOST`
  - `PROD_SSH_USER`
  - `PROD_SSH_PRIVATE_KEY`
  - `PROD_APP_ROOT`
- The production server will store secrets in `/opt/aiugc-prod/.env.production`.
- The production repo checkout will live at `/opt/aiugc-prod/repo`.

### Task 1: Lock the Production Config Contract

**Files:**
- Create: `tests/test_production_compose_contract.py`
- Create: `.env.production.example`
- Modify: `app/core/config.py`

- [ ] **Step 1: Write the failing tests for env-file override and production env completeness**

```python
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
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: FAIL because `APP_ENV_FILE` is not honored yet and `.env.production.example` does not exist.

- [ ] **Step 3: Implement the env-file override and the example production env**

Update `app/core/config.py` so `SettingsConfigDict` reads from `APP_ENV_FILE` first and falls back to `.env`.

```python
DEFAULT_ENV_FILE = os.getenv("APP_ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

Create `.env.production.example` with the exact production contract:

```dotenv
SUPABASE_URL=https://qnvgiihzbihkedakggth.supabase.co
SUPABASE_KEY=
SUPABASE_SERVICE_KEY=
SUPABASE_SERVICE_ROLE_KEY=
GEMINI_API_KEY=
GEMINI_API_KEY=
GOOGLE_APPLICATION_CREDENTIALS_JSON=
VERTEX_AI_PROJECT_ID=project-89aac146-ec35-4755-b83
VERTEX_AI_LOCATION=us-central1
VERTEX_AI_ENABLED=true
VERTEX_AI_OUTPUT_GCS_URI=gs://project-89aac146-ec35-4755-b83-vertex-output/
CLOUDFLARE_R2_BUCKET_NAME=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
CLOUDFLARE_R2_REGION=auto
CLOUDFLARE_R2_ENDPOINT_URL=
DEEPGRAM_API_KEY=
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=https://lippelift.xyz/publish/meta/callback
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=https://lippelift.xyz/publish/tiktok/callback
TIKTOK_ENVIRONMENT=sandbox
APP_URL=https://lippelift.xyz
APP_HOST=lippelift.xyz
PRIVACY_POLICY_URL=https://lippelift.xyz/privacy
TERMS_URL=https://lippelift.xyz/terms
TOKEN_ENCRYPTION_KEY=
CRON_SECRET=
ALLOWED_EMAILS=
REVIEWER_LOGIN_EMAIL=
REVIEWER_LOGIN_TOKEN=
WEBFLOW_API_TOKEN=
WEBFLOW_SITE_ID=
WEBFLOW_COLLECTION_ID=
VEO_DISABLE_LOCAL_QUOTA_GUARD=false
VEO_DISABLE_ALL_QUOTA_CONTROLS=false
DEBUG=0
LOG_LEVEL=INFO
ENVIRONMENT=production
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: PASS with both tests green.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py .env.production.example tests/test_production_compose_contract.py
git commit -m "feat: define production env contract"
```

### Task 2: Replace the Fragile Hostinger Wrapper with a Deterministic Production Compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.production.yml`
- Test: `tests/test_production_compose_contract.py`

- [ ] **Step 1: Extend the failing test to lock the production compose contract**

Append these tests to `tests/test_production_compose_contract.py`:

```python
import yaml


def test_production_compose_uses_repo_build_and_server_env_file():
    data = yaml.safe_load(Path("docker-compose.production.yml").read_text(encoding="utf-8"))
    web = data["services"]["web"]
    worker = data["services"]["worker"]
    assert web["build"]["context"] == "."
    assert worker["build"]["context"] == "."
    assert web["env_file"] == ["${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}"]
    assert "DOCKER_BUILD_CONTEXT" not in Path("docker-compose.production.yml").read_text(encoding="utf-8")


def test_production_compose_has_live_healthcheck():
    data = yaml.safe_load(Path("docker-compose.production.yml").read_text(encoding="utf-8"))
    health = data["services"]["web"]["healthcheck"]
    assert "/health" in "".join(health["test"])
```

- [ ] **Step 2: Run the test file to verify the new contract fails**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: FAIL because `Dockerfile` and `docker-compose.production.yml` do not exist yet.

- [ ] **Step 3: Create the production image and compose file**

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Create `docker-compose.production.yml`:

```yaml
services:
  web:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - ${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    labels:
      - traefik.enable=true
      - traefik.http.routers.aiugc-prod-web.rule=Host(`lippelift.xyz`) || Host(`www.lippelift.xyz`) || Host(`srv1498567.hstgr.cloud`)
      - traefik.http.routers.aiugc-prod-web.entrypoints=websecure
      - traefik.http.routers.aiugc-prod-web.tls.certresolver=letsencrypt
      - traefik.http.services.aiugc-prod-web.loadbalancer.server.port=8000
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

  worker:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - ${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}
    command: python workers/video_poller.py

  expansion-worker:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - ${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}
    command: python workers/expansion_worker.py

  topic-worker:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - ${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}
    command: python workers/topic_worker.py

  caption-worker:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - ${APP_ENV_FILE:-/opt/aiugc-prod/.env.production}
    command: python -m workers.caption_worker
```

- [ ] **Step 4: Run the compose contract tests**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: PASS for the compose contract assertions.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.production.yml tests/test_production_compose_contract.py
git commit -m "feat: add deterministic production compose"
```

### Task 3: Add a Single Server-Side Deploy Script

**Files:**
- Create: `scripts/deploy/production.sh`
- Test: `tests/test_production_compose_contract.py`

- [ ] **Step 1: Add a failing test for the deploy script contract**

Append these tests to `tests/test_production_compose_contract.py`:

```python
def test_deploy_script_runs_compose_build_and_health_check():
    script = Path("scripts/deploy/production.sh").read_text(encoding="utf-8")
    assert "git fetch origin main" in script
    assert "git merge --ff-only origin/main" in script
    assert "docker compose -f docker-compose.production.yml --env-file \"$ENV_FILE\" up -d --build --remove-orphans" in script
    assert "curl --fail --silent --show-error \"$HEALTHCHECK_URL\"" in script
```

- [ ] **Step 2: Run the tests to verify the script contract fails**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: FAIL because `scripts/deploy/production.sh` does not exist yet.

- [ ] **Step 3: Create the idempotent deploy script**

Create `scripts/deploy/production.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/aiugc-prod}"
REPO_DIR="${REPO_DIR:-$APP_ROOT/repo}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env.production}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://lippelift.xyz/health}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$APP_ROOT"

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone https://github.com/patabrava/AIUGC.git "$REPO_DIR"
fi

cd "$REPO_DIR"
git fetch origin main
git checkout main
git merge --ff-only origin/main

docker compose -f docker-compose.production.yml --env-file "$ENV_FILE" up -d --build --remove-orphans

for _ in 1 2 3 4 5 6; do
  if curl --fail --silent --show-error "$HEALTHCHECK_URL" >/dev/null; then
    exit 0
  fi
  sleep 10
done

docker compose -f docker-compose.production.yml --env-file "$ENV_FILE" ps
exit 1
```

- [ ] **Step 4: Verify the script is syntactically valid and the tests pass**

Run: `bash -n scripts/deploy/production.sh && pytest tests/test_production_compose_contract.py -v`

Expected: `bash -n` exits 0 and pytest passes.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy/production.sh tests/test_production_compose_contract.py
git commit -m "feat: add production deploy script"
```

### Task 4: Trigger Production Deploys Automatically on Push to `main`

**Files:**
- Create: `.github/workflows/deploy-production.yml`
- Test: `tests/test_production_compose_contract.py`

- [ ] **Step 1: Add a failing test for the workflow trigger**

Append these tests to `tests/test_production_compose_contract.py`:

```python
def test_github_action_deploys_on_push_to_main():
    data = yaml.safe_load(Path(".github/workflows/deploy-production.yml").read_text(encoding="utf-8"))
    assert data["name"] == "Deploy Production"
    assert data["on"]["push"]["branches"] == ["main"]
    steps = data["jobs"]["deploy"]["steps"]
    step_text = "\n".join(str(step) for step in steps)
    assert "appleboy/ssh-action" in step_text
    assert "scripts/deploy/production.sh" in step_text
```

- [ ] **Step 2: Run the test file to verify the workflow contract fails**

Run: `pytest tests/test_production_compose_contract.py -v`

Expected: FAIL because the workflow file does not exist yet.

- [ ] **Step 3: Create the GitHub Actions workflow**

Create `.github/workflows/deploy-production.yml`:

```yaml
name: Deploy Production

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Deploy over SSH
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.PROD_SSH_HOST }}
          username: ${{ secrets.PROD_SSH_USER }}
          key: ${{ secrets.PROD_SSH_PRIVATE_KEY }}
          script: |
            export APP_ROOT="${{ secrets.PROD_APP_ROOT }}"
            export REPO_DIR="${{ secrets.PROD_APP_ROOT }}/repo"
            export ENV_FILE="${{ secrets.PROD_APP_ROOT }}/.env.production"
            export HEALTHCHECK_URL="https://lippelift.xyz/health"
            cd "$REPO_DIR"
            bash scripts/deploy/production.sh
```

- [ ] **Step 4: Validate the workflow YAML and the test suite**

Run: `python - <<'PY'\nimport yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/deploy-production.yml').read_text())\nprint('workflow yaml ok')\nPY && pytest tests/test_production_compose_contract.py -v`

Expected: `workflow yaml ok` and pytest passes.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy-production.yml tests/test_production_compose_contract.py
git commit -m "feat: auto-deploy production on push to main"
```

### Task 5: Write the Ops Runbook and Prevent Regression

**Files:**
- Create: `docs/deployment/production-auto-deploy.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Draft the runbook content with exact bootstrap and rollback commands**

Create `docs/deployment/production-auto-deploy.md` with:

```md
# Production Auto-Deploy

## Server bootstrap

```bash
sudo mkdir -p /opt/aiugc-prod
sudo chown "$USER":"$USER" /opt/aiugc-prod
cp .env.production.example /opt/aiugc-prod/.env.production
chmod 600 /opt/aiugc-prod/.env.production
git clone https://github.com/patabrava/AIUGC.git /opt/aiugc-prod/repo
cd /opt/aiugc-prod/repo
bash scripts/deploy/production.sh
```

## GitHub secrets

- `PROD_SSH_HOST=srv1498567.hstgr.cloud`
- `PROD_SSH_USER=<deploy-user>`
- `PROD_SSH_PRIVATE_KEY=<private key contents>`
- `PROD_APP_ROOT=/opt/aiugc-prod`

## Manual rollback

```bash
cd /opt/aiugc-prod/repo
git log --oneline -5
git checkout <good-commit>
docker compose -f docker-compose.production.yml --env-file /opt/aiugc-prod/.env.production up -d --build --remove-orphans
curl --fail https://lippelift.xyz/health
```
```

- [ ] **Step 2: Add the AGENTS rule for future engineers**

Add this dense line under `2) Specific repo rules` in `AGENTS.md`:

```md
- Live `lippelift.xyz` deploys must run from a VPS checkout plus `/opt/aiugc-prod/.env.production` through `scripts/deploy/production.sh`; do not rely on Hostinger repo-wrapper env injection or repo `.env`, or pushes to `main` will diverge from the live runtime.
```

- [ ] **Step 3: Verify the docs mention the health gate and the server-only env file**

Run: `rg -n "/opt/aiugc-prod/.env.production|scripts/deploy/production.sh|lippelift.xyz/health" docs/deployment/production-auto-deploy.md AGENTS.md`

Expected: three or more matches showing the runbook and the repo rule are in place.

- [ ] **Step 4: Run the full targeted verification set**

Run: `pytest tests/test_production_compose_contract.py -v && bash -n scripts/deploy/production.sh && python - <<'PY'\nimport yaml, pathlib\nfor path in ['docker-compose.production.yml', '.github/workflows/deploy-production.yml']:\n    yaml.safe_load(pathlib.Path(path).read_text())\nprint('yaml ok')\nPY`

Expected:
- pytest: PASS
- `bash -n`: exits 0
- python: prints `yaml ok`

- [ ] **Step 5: Commit**

```bash
git add docs/deployment/production-auto-deploy.md AGENTS.md
git commit -m "docs: document automatic production deploy flow"
```

## Self-Review

### Spec coverage

- Requirement: pushing to `main` should automatically reach live deployment.
  - Covered by Task 4 (`.github/workflows/deploy-production.yml`) and Task 3 (`scripts/deploy/production.sh`).
- Requirement: live deployment must use the same code as local `main`.
  - Covered by Task 3 with `git fetch origin main` plus `git merge --ff-only origin/main`.
- Requirement: production should stop depending on repo `.env`.
  - Covered by Task 1 (`.env.production.example`, `APP_ENV_FILE` contract) and Task 2/3 (`/opt/aiugc-prod/.env.production`).
- Requirement: deployment must be testable and diagnosable.
  - Covered by Task 2 compose tests, Task 3 deploy script contract, and Task 5 runbook plus health gate.

### Placeholder scan

- No `TODO`, `TBD`, or deferred “handle later” instructions remain.
- Every code-changing step includes exact code.
- Every verification step includes an exact command and expected result.

### Type consistency

- The plan consistently uses:
  - `APP_ENV_FILE`
  - `/opt/aiugc-prod/.env.production`
  - `/opt/aiugc-prod/repo`
  - `scripts/deploy/production.sh`
  - `docker-compose.production.yml`

**Plan complete and saved to `docs/superpowers/plans/2026-04-12-production-auto-deploy.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
