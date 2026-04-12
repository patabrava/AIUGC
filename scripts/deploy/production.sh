#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/aiugc-prod}"
REPO_DIR="${REPO_DIR:-$APP_ROOT/repo}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env.production}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://lippelift.xyz/health}"
MAX_HEALTH_RETRIES="${MAX_HEALTH_RETRIES:-20}"
HEALTHCHECK_INTERVAL_SECONDS="${HEALTHCHECK_INTERVAL_SECONDS:-3}"
REPO_REMOTE="${REPO_REMOTE:-https://github.com/patabrava/AIUGC.git}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$APP_ROOT"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone "$REPO_REMOTE" "$REPO_DIR"
fi

cd "$REPO_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Repository has uncommitted changes; refusing to deploy." >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available on the production host." >&2
  exit 1
fi

git fetch origin main
git checkout main
git merge --ff-only origin/main

"${COMPOSE_CMD[@]}" -f docker-compose.production.yml --env-file "$ENV_FILE" up -d --build --remove-orphans

for ((attempt=1; attempt<=MAX_HEALTH_RETRIES; attempt+=1)); do
  if curl --fail --silent --show-error --connect-timeout 5 --max-time 10 "$HEALTHCHECK_URL" >/dev/null; then
    echo "Deploy healthy: $HEALTHCHECK_URL"
    exit 0
  fi
  sleep "$HEALTHCHECK_INTERVAL_SECONDS"
done

echo "Healthcheck failed after $MAX_HEALTH_RETRIES attempts: $HEALTHCHECK_URL" >&2
"${COMPOSE_CMD[@]}" -f docker-compose.production.yml --env-file "$ENV_FILE" ps || true
exit 1
