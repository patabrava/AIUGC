#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_NAME="${SEMANTIC_UGC_POSTGRES_CONTAINER:-semantic-ugc-postgres-gate-$$}"
POSTGRES_IMAGE="${SEMANTIC_UGC_POSTGRES_IMAGE:-postgres:14-alpine@sha256:6765739f422606933bc2aece3a2288e40e491488fd7e7c14e3323dfeefb10e38}"
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
    --publish 127.0.0.1::5432 \
    "$POSTGRES_IMAGE" >/dev/null
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

POSTGRES_PORT="$(docker port "$CONTAINER_NAME" 5432/tcp | awk -F: 'END { print $NF }')"
if [[ -z "$POSTGRES_PORT" ]]; then
  echo "PostgreSQL container must publish 5432 for the Supabase CLI migration gate: $CONTAINER_NAME" >&2
  exit 1
fi

docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
END;
$$;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
SQL

PGSSLMODE=disable supabase migration up \
  --db-url "postgres://postgres:postgres@127.0.0.1:${POSTGRES_PORT}/postgres" \
  --include-all \
  --workdir "$ROOT_DIR"

docker exec -i "$CONTAINER_NAME" psql -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM supabase_migrations.schema_migrations
    WHERE version = '20260713000000'
  ) OR NOT EXISTS (
    SELECT 1
    FROM supabase_migrations.schema_migrations
    WHERE version = '20260713000100'
  ) THEN
    RAISE EXCEPTION 'Supabase CLI did not record both Semantic UGC migrations';
  END IF;
  IF to_regprocedure('public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'Supabase CLI did not install Semantic UGC RPCs';
  END IF;
END;
$$;
SQL
