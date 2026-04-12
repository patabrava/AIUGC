#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/aiugc-prod}"
REPO_DIR="${REPO_DIR:-$APP_ROOT/repo}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env.production}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://127.0.0.1:8000/health}"
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
git fetch origin main
git checkout main
git merge --ff-only origin/main

docker compose -f docker-compose.production.yml --env-file "$ENV_FILE" up -d --build --remove-orphans

for ((attempt=1; attempt<=MAX_HEALTH_RETRIES; attempt+=1)); do
  if curl --fail --silent --show-error "$HEALTHCHECK_URL" >/dev/null; then
    echo "Deploy healthy: $HEALTHCHECK_URL"
    exit 0
  fi
  sleep "$HEALTHCHECK_INTERVAL_SECONDS"
done

echo "Healthcheck failed after $MAX_HEALTH_RETRIES attempts: $HEALTHCHECK_URL" >&2
docker compose -f docker-compose.production.yml --env-file "$ENV_FILE" ps || true
exit 1
