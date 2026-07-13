#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_NAME="${SEMANTIC_UGC_POSTGRES_CONTAINER:-semantic-ugc-postgres-gate-$$}"
CREATED_CONTAINER=0

cleanup() {
  if [[ "$CREATED_CONTAINER" == "1" ]]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  if [[ -n "${SEMANTIC_UGC_POSTGRES_CONTAINER:-}" ]]; then
    echo "PostgreSQL container is not running: $CONTAINER_NAME" >&2
    exit 1
  fi
  docker run --detach \
    --name "$CONTAINER_NAME" \
    --env POSTGRES_PASSWORD=postgres \
    postgres:14-alpine >/dev/null
  CREATED_CONTAINER=1
fi

for _attempt in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "$CONTAINER_NAME" pg_isready -U postgres >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready: $CONTAINER_NAME" >&2
  exit 1
fi

cd "$ROOT_DIR"
SEMANTIC_UGC_POSTGRES_CONTAINER="$CONTAINER_NAME" \
  python3 -m pytest \
    tests/test_semantic_batch_migration_postgres.py \
    tests/test_semantic_video_plan_migration_postgres.py \
    -q
