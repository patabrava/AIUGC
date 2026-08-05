from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID, uuid5

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
API_MIGRATION = ROOT / "supabase/migrations/20260713000100_semantic_video_api_transactions.sql"
PROGRESS_MIGRATION = ROOT / "supabase/migrations/20260728020000_semantic_scene_plate_progress.sql"
PROGRESS_COALESCE_MIGRATION = ROOT / "supabase/migrations/20260731090000_semantic_scene_plate_progress_coalesce_fix.sql"
FAILURE_PROGRESS_MIGRATION = ROOT / "supabase/migrations/20260804000200_semantic_scene_plate_failure_progress.sql"
ATOMIC_FAILURE_MIGRATION = ROOT / "supabase/migrations/20260804000300_semantic_scene_plate_atomic_failure_release.sql"
SINGLE_IMAGE_MIGRATION = ROOT / "supabase/migrations/20260804000400_semantic_single_scene_image_jobs.sql"
STATE_MACHINE_MIGRATION = ROOT / "supabase/migrations/20260805000000_semantic_scene_image_state_machine.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_scene_image_queue_rpc_test"
NAMESPACE = UUID("7b651992-4f74-4a5e-a17b-68c922c8a0d0")


def _id(label: str) -> str:
    return str(uuid5(NAMESPACE, label))


def _psql(
    database: str, sql: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            str(CONTAINER),
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-U",
            "postgres",
            "-d",
            database,
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=check,
    )


def _psql_process(sql: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "docker",
            "exec",
            "-i",
            str(CONTAINER),
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-U",
            "postgres",
            "-d",
            DATABASE,
            "-c",
            sql,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _scalar(query: str) -> str:
    return _psql(DATABASE, f"COPY ({query}) TO STDOUT;").stdout.strip()


def _jsonb(value: object) -> str:
    return f"$json${json.dumps(value, separators=(',', ':'))}$json$::JSONB"


def _insert_batch_with_posts(label: str, script_count: int) -> tuple[str, list[str]]:
    batch_id = _id(f"batch-{label}")
    post_ids = [_id(f"post-{label}-{index}") for index in range(script_count)]
    post_rows = ",".join(
        "(" + ",".join(
            [
                f"'{post_id}'",
                f"'{batch_id}'",
                f"'Script {label} {index}'",
                f"'{{\"script_review_status\":\"approved\",\"script\":\"Approved script {label} {index}\"}}'::JSONB",
            ]
        ) + ")"
        for index, post_id in enumerate(post_ids)
    )
    _psql(
        DATABASE,
        f"""
        INSERT INTO public.batches (
          id, brand, creation_mode, target_length_tier,
          target_duration_seconds, video_pipeline_route
        ) VALUES (
          '{batch_id}', 'Queue {label}', 'semantic_ugc', NULL, 16, 'semantic_ugc'
        );
        INSERT INTO public.posts (id, batch_id, topic_rotation, seed_data)
        VALUES {post_rows};
        """,
    )
    return batch_id, post_ids


def _enqueue(post_id: str, expected_revision: str = "NULL") -> str:
    return _scalar(
        "SELECT id::TEXT FROM public.enqueue_semantic_scene_image("
        f"'{post_id}', {expected_revision}, 'operator@example.com', 'queue-test')"
    )


def _claim(worker_id: str, lease_seconds: int = 60) -> list[str]:
    output = _psql(
        DATABASE,
        "SET ROLE service_role; "
        "SELECT id::TEXT || '|' || lease_token::TEXT || '|' || attempt_count::TEXT "
        f"FROM public.claim_semantic_scene_image('{worker_id}', {lease_seconds});",
    ).stdout.strip()
    return output.split("|") if output else []


def _fail(job_id: str, worker_id: str, token: str, code: str = "test_terminal") -> None:
    _psql(
        DATABASE,
        "SET ROLE service_role; "
        "SELECT id FROM public.finish_semantic_scene_image("
        f"'{job_id}', '{worker_id}', '{token}', 'failed', NULL, "
        f"'{{\"code\":\"{code}\",\"message\":\"safe terminal test\"}}'::JSONB);",
    )


def _run_create(post_id: str, batch_id: str, label: str) -> dict[str, object]:
    return {
        "post_id": post_id,
        "batch_id": batch_id,
        "requested_duration_seconds": 16,
        "duration_contract": {"requested_duration_seconds": 16},
        "duration_contract_hash": f"duration-hash-{label}",
        "script_snapshot": {"text": f"Approved script {label}"},
        "script_hash": f"script-hash-{label}",
        "actor_identity_id": None,
        "actor_snapshot": {},
        "reference_snapshot": {},
        "reference_hash": f"reference-hash-{label}",
        "master_snapshot": {},
        "master_hash": None,
        "stage": "awaiting_reference_approval",
        "plan_snapshot": None,
        "plan_hash": None,
        "provider_model": None,
        "resolution": None,
        "estimated_cost_usd": None,
        "artifact_prefix": f"semantic/{label}",
        "failure_envelope": None,
    }


def test_scene_image_state_machine_source_has_single_authority_contract() -> None:
    sql = STATE_MACHINE_MIGRATION.read_text(encoding="utf-8")
    assert "semantic_scene_image_jobs_one_active_batch" in sql
    assert "active_claim_count >= 2" in sql
    assert "CREATE OR REPLACE FUNCTION public.renew_semantic_scene_image" in sql
    assert "CREATE OR REPLACE FUNCTION public.reserve_semantic_scene_image_candidates" in sql
    assert "CREATE OR REPLACE FUNCTION public.finalize_semantic_scene_image_job" in sql
    assert "reserve_semantic_video_candidates_legacy_queue_impl" in sql
    assert "direct candidate generation is retired" in sql
    assert "semantic_scene_image_worker_heartbeats" in sql
    assert "probe_semantic_scene_image_queue" in sql
    assert "INTERVAL '8 minutes'" in sql
    assert "INTERVAL '4 minutes'" in sql


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_scene_image_queue_is_batch_serial_lease_fenced_and_stress_proven() -> None:
    _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};")
    _psql("postgres", f"CREATE DATABASE {DATABASE};")
    cutover_holder: subprocess.Popen[str] | None = None
    cutover_stale: subprocess.Popen[str] | None = None
    try:
        _psql(
            DATABASE,
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            END;
            $$;
            ALTER ROLE service_role BYPASSRLS;
            ALTER ROLE anon NOBYPASSRLS;
            ALTER ROLE authenticated NOBYPASSRLS;
            CREATE TABLE public.batches (
              id UUID PRIMARY KEY,
              brand TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'S1_SETUP',
              creation_mode TEXT NOT NULL DEFAULT 'automated',
              post_type_counts JSONB NOT NULL DEFAULT '{}'::JSONB,
              manual_post_count INTEGER,
              target_length_tier INTEGER,
              video_pipeline_route TEXT,
              archived BOOLEAN NOT NULL DEFAULT FALSE
            );
            CREATE TABLE public.actor_identities (id UUID PRIMARY KEY);
            CREATE TABLE public.posts (
              id UUID PRIMARY KEY,
              batch_id UUID NOT NULL REFERENCES public.batches(id) ON DELETE CASCADE,
              topic_rotation TEXT NOT NULL DEFAULT '',
              seed_data JSONB NOT NULL DEFAULT '{}'::JSONB
            );
            CREATE OR REPLACE FUNCTION public.touch_updated_at()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            BEGIN
              NEW.updated_at = now();
              RETURN NEW;
            END;
            $$;
            """,
        )
        for migration in (
            BASE_MIGRATION,
            API_MIGRATION,
            PROGRESS_MIGRATION,
            PROGRESS_COALESCE_MIGRATION,
            FAILURE_PROGRESS_MIGRATION,
            ATOMIC_FAILURE_MIGRATION,
            SINGLE_IMAGE_MIGRATION,
        ):
            _psql(DATABASE, migration.read_text(encoding="utf-8"))

        # Enter the old reserve function before migration and pause it at its
        # per-post advisory lock. This preserves the exact rolling-cutover race:
        # renaming/revoking a function does not stop a body that already entered.
        cutover_batch, cutover_posts = _insert_batch_with_posts(
            "exact-entered-race", 1
        )
        cutover_post = cutover_posts[0]
        cutover_job = _enqueue(cutover_post)
        cutover_claim = _claim(
            "semantic-scene-image-v1-exact", lease_seconds=600
        )
        assert cutover_claim[0] == cutover_job
        cutover_token = _id("exact-entered-race-token")
        cutover_payload = _run_create(
            cutover_post, cutover_batch, "exact-entered-race"
        )
        cutover_holder = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                str(CONTAINER),
                "psql",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
                "-U",
                "postgres",
                "-d",
                DATABASE,
            ],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        assert cutover_holder.stdin is not None
        assert cutover_holder.stdout is not None
        cutover_holder.stdin.write(
            "BEGIN;\n"
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('{cutover_post}'::TEXT, 0));\n"
            "SELECT 'LOCKED';\n"
        )
        cutover_holder.stdin.flush()
        holder_deadline = time.time() + 10
        while time.time() < holder_deadline:
            if cutover_holder.stdout.readline().strip() == "LOCKED":
                break
        else:
            raise AssertionError("cutover lock holder did not acquire the advisory lock")

        cutover_stale = _psql_process(
            "/* exact_cutover_stale_reserve */ SET ROLE service_role; "
            "SELECT id::TEXT FROM public.reserve_semantic_video_candidates("
            f"'{cutover_post}', NULL, {_jsonb(cutover_payload)}, "
            "'semantic-scene-image-v1-exact', "
            f"'{cutover_token}', 1800);"
        )
        cutover_activity = ""
        activity_deadline = time.time() + 15
        while time.time() < activity_deadline:
            cutover_activity = _psql(
                DATABASE,
                "COPY (SELECT state || '|' || COALESCE(wait_event_type, '') || "
                "'|' || COALESCE(wait_event, '') FROM pg_stat_activity "
                "WHERE query LIKE '%exact_cutover_stale_reserve%' "
                "AND pid <> pg_backend_pid()) TO STDOUT;",
            ).stdout.strip()
            if cutover_activity == "active|Lock|advisory":
                break
            if cutover_stale.poll() is not None:
                stale_out, stale_err = cutover_stale.communicate()
                raise AssertionError(
                    "old reserve ended before cutover: "
                    f"rc={cutover_stale.returncode} out={stale_out!r} err={stale_err!r}"
                )
            time.sleep(0.1)
        else:
            raise AssertionError(
                f"old reserve never entered its advisory wait: {cutover_activity!r}"
            )
        assert _scalar(
            "SELECT count(*)::TEXT FROM public.semantic_video_runs "
            f"WHERE post_id = '{cutover_post}'"
        ) == "0"

        # True rolling-upgrade fixture: v1 has one job already reserved and a
        # second job claimed but not yet reserved when the v2 migration lands.
        upgrade_reserved_batch, upgrade_reserved_posts = _insert_batch_with_posts(
            "upgrade-reserved", 1
        )
        upgrade_reserved_job = _enqueue(upgrade_reserved_posts[0])
        upgrade_reserved_claim = _claim(
            "semantic-scene-image-v1-reserved", lease_seconds=600
        )
        assert upgrade_reserved_claim[0] == upgrade_reserved_job
        upgrade_reserved_token = _id("upgrade-reserved-token")
        upgrade_reserved_payload = _run_create(
            upgrade_reserved_posts[0],
            upgrade_reserved_batch,
            "upgrade-reserved",
        )
        upgrade_reserved_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_video_candidates("
            f"'{upgrade_reserved_posts[0]}', NULL, {_jsonb(upgrade_reserved_payload)}, "
            f"'semantic-scene-image-v1-reserved', '{upgrade_reserved_token}', 1800);",
        ).stdout.strip().split("|")
        assert _scalar(
            "SELECT (run_id IS NULL)::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{upgrade_reserved_job}'"
        ) == "true"

        upgrade_pending_batch, upgrade_pending_posts = _insert_batch_with_posts(
            "upgrade-pending", 1
        )
        upgrade_pending_job = _enqueue(upgrade_pending_posts[0])
        upgrade_pending_claim = _claim(
            "semantic-scene-image-v1-pending", lease_seconds=600
        )
        assert upgrade_pending_claim[0] == upgrade_pending_job
        direct_batch, direct_posts = _insert_batch_with_posts("direct-cutover", 1)
        direct_token = _id("direct-cutover-token")
        direct_payload = _run_create(direct_posts[0], direct_batch, "direct-cutover")
        direct_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_video_candidates("
            f"'{direct_posts[0]}', NULL, {_jsonb(direct_payload)}, "
            f"'legacy-direct-http', '{direct_token}', 1800);",
        ).stdout.strip().split("|")

        _psql(DATABASE, STATE_MACHINE_MIGRATION.read_text(encoding="utf-8"))

        # The old OID is still executing after migration. Releasing its lock
        # must hit the durable DML trigger rather than creating an unlinked run.
        assert cutover_stale.poll() is None
        cutover_holder.stdin.write("COMMIT;\n\\q\n")
        cutover_holder.stdin.flush()
        holder_stdout, holder_stderr = cutover_holder.communicate(timeout=10)
        assert cutover_holder.returncode == 0, (holder_stdout, holder_stderr)
        stale_stdout, stale_stderr = cutover_stale.communicate(timeout=15)
        assert cutover_stale.returncode != 0, (stale_stdout, stale_stderr)
        assert "stale direct candidate mutation is fenced" in stale_stderr
        assert _scalar(
            "SELECT count(*)::TEXT FROM public.semantic_video_runs "
            f"WHERE post_id = '{cutover_post}'"
        ) == "0"
        assert _scalar(
            "SELECT status || '|' || COALESCE(run_id::TEXT, 'null') || '|' || "
            "COALESCE(error ->> 'code', 'null') || '|' || "
            "(lease_token IS NULL)::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{cutover_job}'"
        ) == "failed|null|worker_contract_upgraded|true"

        # Cutover is deliberately fail-closed: incomplete v1 work becomes one
        # explicit retry, every old candidate token is cleared, and v1 cannot
        # claim or mutate after the migration commits.
        assert _scalar(
            "SELECT run_id::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{upgrade_reserved_job}'"
        ) == upgrade_reserved_run[0]
        assert _scalar(
            "SELECT status || '|' || (error ->> 'code') || '|' || "
            "(lease_token IS NULL)::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{upgrade_reserved_job}'"
        ) == "failed|worker_contract_upgraded|true"
        assert _scalar(
            "SELECT status || '|' || (error ->> 'code') || '|' || "
            "(lease_token IS NULL)::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{upgrade_pending_job}'"
        ) == "failed|worker_contract_upgraded|true"
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT || '|' || revision::TEXT "
            f"FROM public.semantic_video_runs WHERE id = '{upgrade_reserved_run[0]}'"
        ) == "true|1"
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT || '|' || revision::TEXT "
            f"FROM public.semantic_video_runs WHERE id = '{direct_run[0]}'"
        ) == "true|1"
        assert _claim(
            "semantic-scene-image-v1-rolling", lease_seconds=600
        ) == []

        stale_finalize = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.finalize_semantic_video_candidates("
            f"'{direct_run[0]}', {direct_run[1]}, '{direct_token}', "
            f"{_jsonb({**direct_payload, 'master_snapshot': {'candidates': []}})});",
            check=False,
        )
        assert stale_finalize.returncode != 0
        assert "direct candidate finalization is retired" in stale_finalize.stderr

        retry_job = _enqueue(upgrade_reserved_posts[0], "1")
        retry_claim = _claim("upgrade-v2-retry")
        assert retry_claim[0] == retry_job
        _fail(retry_job, "upgrade-v2-retry", retry_claim[1], code="upgrade_retry_ready")

        direct_retry_job = _enqueue(direct_posts[0], "1")
        direct_retry_claim = _claim("direct-v2-retry")
        assert direct_retry_claim[0] == direct_retry_job
        _fail(
            direct_retry_job,
            "direct-v2-retry",
            direct_retry_claim[1],
            code="direct_retry_ready",
        )

        direct_reservation = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.reserve_semantic_video_candidates("
            "NULL, NULL, '{}'::JSONB, 'legacy-direct', "
            f"'{_id('legacy-direct-token')}', 300);",
            check=False,
        )
        assert direct_reservation.returncode != 0
        assert "direct candidate generation is retired" in direct_reservation.stderr
        assert _scalar(
            "SELECT has_function_privilege("
            "'service_role', "
            "'public.reserve_semantic_video_candidates_legacy_queue_impl(uuid,integer,jsonb,text,uuid,integer)', "
            "'EXECUTE')::TEXT"
        ) == "false"
        assert _scalar(
            "SELECT has_table_privilege('service_role', "
            "'public.semantic_scene_image_jobs', 'SELECT')::TEXT || '|' || "
            "has_table_privilege('service_role', "
            "'public.semantic_scene_image_jobs', 'INSERT,UPDATE,DELETE')::TEXT || '|' || "
            "has_table_privilege('service_role', "
            "'public.semantic_scene_image_worker_heartbeats', 'INSERT,UPDATE,DELETE')::TEXT"
        ) == "true|false|false"

        # A real fresh-card revision zero is accepted and normalized to NULL.
        _batch, posts = _insert_batch_with_posts("fresh", 3)
        first_job = _enqueue(posts[0], "0")
        assert first_job
        assert _scalar(
            f"SELECT COALESCE(expected_revision::TEXT, 'null') FROM public.semantic_scene_image_jobs WHERE id = '{first_job}'"
        ) == "null"
        assert _enqueue(posts[0], "NULL") == first_job

        sibling = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.enqueue_semantic_scene_image("
            f"'{posts[1]}', NULL, 'operator@example.com', 'blocked-sibling');",
            check=False,
        )
        assert sibling.returncode != 0
        assert "another script image is already generating" in sibling.stderr

        claimed = _claim("worker-fresh")
        assert claimed[0] == first_job
        assert claimed[2] == "1"
        for expected_attempt in range(1, 4):
            authorized = _psql(
                DATABASE,
                "SET ROLE service_role; "
                "SELECT provider_attempt_count::TEXT "
                "FROM public.authorize_semantic_scene_image_provider_attempt("
                f"'{first_job}', 'worker-fresh', '{claimed[1]}');",
            ).stdout.strip()
            assert authorized == str(expected_attempt)
        exhausted_attempt = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.authorize_semantic_scene_image_provider_attempt("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}');",
            check=False,
        )
        assert exhausted_attempt.returncode != 0
        assert "provider-attempt budget" in exhausted_attempt.stderr
        stale_token = _id("stale-token")
        stale_renewal = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.renew_semantic_scene_image("
            f"'{first_job}', 'worker-fresh', '{stale_token}', 60);",
            check=False,
        )
        assert stale_renewal.returncode != 0
        assert "renewal lost its lease" in stale_renewal.stderr
        renewed = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.renew_semantic_scene_image("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}', 60);",
        )
        assert first_job in renewed.stdout
        # Reservation linkage plus one-image run finalization and queue
        # completion are a single token-fenced transaction.
        reservation_token = _id("fresh-reservation")
        run_create = _run_create(posts[0], _batch, "fresh")
        reserved = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT FROM public.reserve_semantic_scene_image_candidates("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}', '{posts[0]}', NULL, "
            f"{_jsonb(run_create)}, 'worker-fresh', '{reservation_token}', 1800);",
        ).stdout.strip().split("|")
        assert len(reserved) == 2
        audit_prompt = "literal audit prompt"
        master_snapshot = {
            "candidates": [
                {
                    "index": 1,
                    "storage_uri": "semantic/queue-test/image.png",
                    "sha256": "a" * 64,
                    "mime_type": "image/png",
                    "byte_length": 128,
                }
            ],
            "prompt_writer_system_prompt": audit_prompt,
            "prompt_writer_system_prompt_sha256": sha256(
                audit_prompt.encode("utf-8")
            ).hexdigest(),
            "prompt_writer_output": "Finished renderer prompt",
            "composition_prompt": "Finished renderer prompt",
        }
        upload_checkpoint = {
            "phase": "uploading_candidate",
            "details": {
                "candidate_count": 1,
                "partial_candidates": [
                    {
                        **master_snapshot["candidates"][0],
                        "prompt": "Finished renderer prompt",
                        "provider_model": "gemini-3.1-flash-image",
                        "upload_state": "pending",
                    }
                ],
            },
            "updated_at": "2026-08-05T00:00:00+00:00",
        }
        checkpointed = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.update_semantic_video_candidate_progress("
            f"'{reserved[0]}', {reserved[1]}, '{reservation_token}', "
            f"{_jsonb(upload_checkpoint)});",
        )
        assert reserved[0] in checkpointed.stdout
        assert _scalar(
            "SELECT candidate_generation_progress ->> 'phase' "
            f"FROM public.semantic_video_runs WHERE id = '{reserved[0]}'"
        ) == "uploading_candidate"
        run_update = {**run_create, "master_snapshot": master_snapshot}
        invalid_three_update = {
            **run_create,
            "master_snapshot": {
                **master_snapshot,
                "candidates": master_snapshot["candidates"] * 3,
            },
        }
        invalid_three_finish = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.finalize_semantic_scene_image_job("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}', '{reserved[0]}', "
            f"{reserved[1]}, '{reservation_token}', {_jsonb(invalid_three_update)});",
            check=False,
        )
        assert invalid_three_finish.returncode != 0
        assert "requires exactly one candidate" in invalid_three_finish.stderr
        assert _scalar(
            "SELECT status || '|' || (candidate_reservation_token IS NOT NULL)::TEXT "
            "FROM public.semantic_scene_image_jobs AS job "
            "JOIN public.semantic_video_runs AS run ON run.id = job.run_id "
            f"WHERE job.id = '{first_job}'"
        ) == "processing|true"
        finalized = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.finalize_semantic_scene_image_job("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}', '{reserved[0]}', "
            f"{reserved[1]}, '{reservation_token}', {_jsonb(run_update)});",
        )
        assert reserved[0] in finalized.stdout
        assert _scalar(
            f"SELECT status FROM public.semantic_scene_image_jobs WHERE id = '{first_job}'"
        ) == "completed"
        assert _scalar(
            "SELECT pg_catalog.jsonb_array_length(master_snapshot -> 'candidates')::TEXT "
            f"FROM public.semantic_video_runs WHERE id = '{reserved[0]}'"
        ) == "1"
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT || '|' || "
            "(candidate_generation_progress ->> 'phase') "
            f"FROM public.semantic_video_runs WHERE id = '{reserved[0]}'"
        ) == "true|ready"
        lost_response_failure_write = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.update_semantic_video_candidate_progress("
            f"'{reserved[0]}', {reserved[1]}, '{reservation_token}', "
            "'{\"phase\":\"failed\",\"details\":{\"retryable\":true},"
            "\"updated_at\":\"2026-08-05T00:00:00+00:00\"}'::JSONB);",
            check=False,
        )
        assert lost_response_failure_write.returncode != 0
        assert "candidate progress reservation is stale" in lost_response_failure_write.stderr
        assert _scalar(
            "SELECT candidate_generation_progress ->> 'phase' "
            f"FROM public.semantic_video_runs WHERE id = '{reserved[0]}'"
        ) == "ready"
        stale_atomic_finish = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.finalize_semantic_scene_image_job("
            f"'{first_job}', 'worker-fresh', '{claimed[1]}', '{reserved[0]}', "
            f"{reserved[1]}, '{reservation_token}', {_jsonb(run_update)});",
            check=False,
        )
        assert stale_atomic_finish.returncode != 0
        assert "finalization lost its job lease" in stale_atomic_finish.stderr
        second_job = _enqueue(posts[1])
        assert second_job != first_job
        second_claim = _claim("worker-second")
        _fail(second_job, "worker-second", second_claim[1])

        # Near-deadline work is made terminal before any worker/provider claim.
        _budget_batch, budget_posts = _insert_batch_with_posts("near-deadline", 1)
        budget_job = _enqueue(budget_posts[0])
        _psql(
            DATABASE,
            "UPDATE public.semantic_scene_image_jobs SET deadline_at = "
            "pg_catalog.clock_timestamp() + INTERVAL '239 seconds' "
            f"WHERE id = '{budget_job}';",
        )
        assert _claim("near-deadline-worker") == []
        assert _scalar(
            "SELECT status || '|' || (error ->> 'code') "
            "FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{budget_job}'"
        ) == "failed|insufficient_execution_budget"

        # PostgreSQL owns the global two-claim cap across worker replicas.
        global_jobs: list[str] = []
        for index in range(3):
            _batch_id, global_posts = _insert_batch_with_posts(f"global-{index}", 1)
            global_jobs.append(_enqueue(global_posts[0]))
        claim_a = _claim("global-worker-a")
        claim_b = _claim("global-worker-b")
        assert {claim_a[0], claim_b[0]} == set(global_jobs[:2])
        assert _claim("global-worker-c") == []
        _fail(claim_a[0], "global-worker-a", claim_a[1])
        claim_c = _claim("global-worker-c")
        assert claim_c[0] == global_jobs[2]
        _fail(claim_b[0], "global-worker-b", claim_b[1])
        _fail(claim_c[0], "global-worker-c", claim_c[1])

        # An expired lease is reclaimed exactly once and the old token is fenced.
        _batch_id, reclaim_posts = _insert_batch_with_posts("reclaim", 1)
        reclaim_job = _enqueue(reclaim_posts[0])
        first_claim = _claim("reclaim-worker-a")
        _psql(
            DATABASE,
            "UPDATE public.semantic_scene_image_jobs "
            "SET created_at = pg_catalog.clock_timestamp() - INTERVAL '3 minutes', "
            "started_at = pg_catalog.clock_timestamp() - INTERVAL '3 minutes', "
            "deadline_at = pg_catalog.clock_timestamp() + INTERVAL '5 minutes', "
            "lease_expires_at = pg_catalog.clock_timestamp() - INTERVAL '1 second' "
            f"WHERE id = '{reclaim_job}';",
        )
        second_claim = _claim("reclaim-worker-b")
        assert second_claim[0] == reclaim_job
        assert second_claim[1] != first_claim[1]
        assert second_claim[2] == "2"
        stale_finish = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id FROM public.finish_semantic_scene_image("
            f"'{reclaim_job}', 'reclaim-worker-a', '{first_claim[1]}', 'failed', NULL, "
            "'{\"code\":\"stale\"}'::JSONB);",
            check=False,
        )
        assert stale_finish.returncode != 0
        assert "job lease is stale" in stale_finish.stderr
        _fail(reclaim_job, "reclaim-worker-b", second_claim[1])

        # A crashed reserved attempt with less than four minutes remaining is
        # terminalized, rebased, and reservable on the same run by a fresh job.
        terminal_batch, terminal_posts = _insert_batch_with_posts(
            "crash-no-budget", 1
        )
        terminal_job = _enqueue(terminal_posts[0])
        terminal_claim = _claim("crash-no-budget-worker")
        assert terminal_claim[0] == terminal_job
        terminal_run_create = _run_create(
            terminal_posts[0], terminal_batch, "crash-no-budget"
        )
        terminal_first_reservation = _id("crash-no-budget-first-reservation")
        terminal_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{terminal_job}', 'crash-no-budget-worker', '{terminal_claim[1]}', "
            f"'{terminal_posts[0]}', NULL, {_jsonb(terminal_run_create)}, "
            "'crash-no-budget-worker', "
            f"'{terminal_first_reservation}', 1800);",
        ).stdout.strip().split("|")
        _psql(
            DATABASE,
            "UPDATE public.semantic_scene_image_jobs "
            "SET created_at = pg_catalog.clock_timestamp() - INTERVAL '4 minutes', "
            "started_at = pg_catalog.clock_timestamp() - INTERVAL '3 minutes', "
            "deadline_at = pg_catalog.clock_timestamp() + INTERVAL '239 seconds', "
            "lease_expires_at = pg_catalog.clock_timestamp() - INTERVAL '1 second' "
            f"WHERE id = '{terminal_job}';",
        )
        terminal_retry_job = _enqueue(terminal_posts[0], terminal_run[1])
        assert terminal_retry_job != terminal_job
        assert _scalar(
            "SELECT status || '|' || (error ->> 'code') "
            "FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{terminal_job}'"
        ) == "failed|insufficient_execution_budget"
        terminal_rebased_revision = str(int(terminal_run[1]) + 1)
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT || '|' || revision::TEXT "
            "FROM public.semantic_video_runs "
            f"WHERE id = '{terminal_run[0]}'"
        ) == f"true|{terminal_rebased_revision}"
        assert _scalar(
            "SELECT expected_run_id::TEXT || '|' || expected_revision::TEXT "
            "FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{terminal_retry_job}'"
        ) == f"{terminal_run[0]}|{terminal_rebased_revision}"
        terminal_retry_claim = _claim("crash-cleanup-worker")
        assert terminal_retry_claim[0] == terminal_retry_job
        terminal_retry_reservation = _id("crash-no-budget-retry-reservation")
        terminal_recovered_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{terminal_retry_job}', 'crash-cleanup-worker', "
            f"'{terminal_retry_claim[1]}', '{terminal_posts[0]}', "
            f"{terminal_rebased_revision}, {_jsonb(terminal_run_create)}, "
            f"'crash-cleanup-worker', '{terminal_retry_reservation}', 1800);",
        ).stdout.strip().split("|")
        assert terminal_recovered_run[0] == terminal_run[0]
        assert terminal_recovered_run[1] == str(int(terminal_rebased_revision) + 1)
        _fail(
            terminal_retry_job,
            "crash-cleanup-worker",
            terminal_retry_claim[1],
            code="operator_retry_after_crash",
        )

        # The global claim sweep may win the race and terminalize the expired
        # job before enqueue. Enqueue must still rebase that failed reservation.
        sweep_batch, sweep_posts = _insert_batch_with_posts(
            "claim-sweep-no-budget", 1
        )
        sweep_job = _enqueue(sweep_posts[0])
        sweep_claim = _claim("claim-sweep-worker")
        assert sweep_claim[0] == sweep_job
        sweep_run_create = _run_create(
            sweep_posts[0], sweep_batch, "claim-sweep-no-budget"
        )
        sweep_first_reservation = _id("claim-sweep-first-reservation")
        sweep_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{sweep_job}', 'claim-sweep-worker', '{sweep_claim[1]}', "
            f"'{sweep_posts[0]}', NULL, {_jsonb(sweep_run_create)}, "
            f"'claim-sweep-worker', '{sweep_first_reservation}', 1800);",
        ).stdout.strip().split("|")
        _psql(
            DATABASE,
            "UPDATE public.semantic_scene_image_jobs "
            "SET deadline_at = pg_catalog.clock_timestamp() + INTERVAL '239 seconds', "
            "lease_expires_at = pg_catalog.clock_timestamp() - INTERVAL '1 second' "
            f"WHERE id = '{sweep_job}';",
        )
        assert _claim("claim-sweep-terminalizer") == []
        assert _scalar(
            "SELECT status || '|' || (error ->> 'code') "
            "FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{sweep_job}'"
        ) == "failed|insufficient_execution_budget"
        sweep_retry_job = _enqueue(sweep_posts[0], sweep_run[1])
        sweep_rebased_revision = str(int(sweep_run[1]) + 1)
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT || '|' || revision::TEXT "
            "FROM public.semantic_video_runs "
            f"WHERE id = '{sweep_run[0]}'"
        ) == f"true|{sweep_rebased_revision}"
        assert _scalar(
            "SELECT expected_run_id::TEXT || '|' || expected_revision::TEXT "
            "FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{sweep_retry_job}'"
        ) == f"{sweep_run[0]}|{sweep_rebased_revision}"
        sweep_retry_claim = _claim("claim-sweep-retry-worker")
        assert sweep_retry_claim[0] == sweep_retry_job
        sweep_retry_reservation = _id("claim-sweep-retry-reservation")
        sweep_recovered_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{sweep_retry_job}', 'claim-sweep-retry-worker', "
            f"'{sweep_retry_claim[1]}', '{sweep_posts[0]}', "
            f"{sweep_rebased_revision}, {_jsonb(sweep_run_create)}, "
            f"'claim-sweep-retry-worker', '{sweep_retry_reservation}', 1800);",
        ).stdout.strip().split("|")
        assert sweep_recovered_run[0] == sweep_run[0]
        assert sweep_recovered_run[1] == str(int(sweep_rebased_revision) + 1)
        _fail(
            sweep_retry_job,
            "claim-sweep-retry-worker",
            sweep_retry_claim[1],
            code="operator_retry_after_claim_sweep",
        )

        # Migration-first deployment quiesces the recognizable v1 worker. A v2
        # worker can then claim; if it dies after reserving a run, the next v2
        # claim clears its candidate token transactionally and reuses that run.
        crash_batch, crash_posts = _insert_batch_with_posts("crash-reservation", 1)
        crash_job = _enqueue(crash_posts[0])
        assert _claim(
            "semantic-scene-image-v1-old-window", lease_seconds=600
        ) == []
        crash_first_claim = _claim("recovery-v2-worker-a")
        assert crash_first_claim[0] == crash_job
        crash_run_create = {
            **run_create,
            "post_id": crash_posts[0],
            "batch_id": crash_batch,
            "script_snapshot": {"text": "Approved crash recovery script"},
            "script_hash": "crash-script-hash",
            "artifact_prefix": "semantic/crash-recovery",
        }
        crash_first_reservation = _id("crash-first-reservation")
        crash_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{crash_job}', 'recovery-v2-worker-a', '{crash_first_claim[1]}', "
            f"'{crash_posts[0]}', NULL, {_jsonb(crash_run_create)}, "
            f"'recovery-v2-worker-a', '{crash_first_reservation}', 1800);",
        ).stdout.strip().split("|")
        assert _scalar(
            "SELECT (run.candidate_reservation_expires_at = job.lease_expires_at)::TEXT "
            "FROM public.semantic_video_runs AS run "
            "JOIN public.semantic_scene_image_jobs AS job ON job.run_id = run.id "
            f"WHERE job.id = '{crash_job}'"
        ) == "true"
        partial_candidate = {
            "index": 1,
            "storage_uri": "semantic/crash-recovery/checkpoint.png",
            "sha256": "b" * 64,
            "mime_type": "image/png",
            "byte_length": 256,
        }
        _psql(
            DATABASE,
            "UPDATE public.semantic_video_runs SET candidate_generation_progress = "
            f"{_jsonb({'phase': 'saving_candidates', 'details': {'partial_candidates': [partial_candidate]}})} "
            f"WHERE id = '{crash_run[0]}'; "
            "UPDATE public.semantic_scene_image_jobs "
            "SET lease_expires_at = pg_catalog.clock_timestamp() - INTERVAL '1 second' "
            f"WHERE id = '{crash_job}';",
        )
        crash_second_claim = _claim("recovery-v2-worker-b")
        assert crash_second_claim[0] == crash_job
        assert crash_second_claim[2] == "2"
        recovered_revision = _scalar(
            "SELECT expected_revision::TEXT FROM public.semantic_scene_image_jobs "
            f"WHERE id = '{crash_job}'"
        )
        assert _scalar(
            "SELECT (candidate_reservation_token IS NULL)::TEXT "
            f"FROM public.semantic_video_runs WHERE id = '{crash_run[0]}'"
        ) == "true"
        crash_second_reservation = _id("crash-second-reservation")
        recovered_run = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT id::TEXT || '|' || revision::TEXT "
            "FROM public.reserve_semantic_scene_image_candidates("
            f"'{crash_job}', 'recovery-v2-worker-b', '{crash_second_claim[1]}', "
            f"'{crash_posts[0]}', {recovered_revision}, {_jsonb(crash_run_create)}, "
            f"'recovery-v2-worker-b', '{crash_second_reservation}', 1800);",
        ).stdout.strip().split("|")
        assert recovered_run[0] == crash_run[0]
        _fail(
            crash_job,
            "recovery-v2-worker-b",
            crash_second_claim[1],
            code="failure_after_checkpoint",
        )
        assert _scalar(
            "SELECT (candidate_generation_progress #> '{details,partial_candidates,0}' "
            f"->> 'sha256') FROM public.semantic_video_runs WHERE id = '{crash_run[0]}'"
        ) == partial_candidate["sha256"]
        assert _scalar(
            "SELECT candidate_generation_progress #>> '{details,completed_candidates}' "
            f"FROM public.semantic_video_runs WHERE id = '{crash_run[0]}'"
        ) == "1"

        # Ten real PostgreSQL queue runs cover the requested variable sizes.
        batch_sizes = [3, 4, 5, 6, 7, 1, 2, 3, 7, 4]
        stress_job_ids: set[str] = set()
        for run_number, script_count in enumerate(batch_sizes, start=1):
            _batch_id, stress_posts = _insert_batch_with_posts(
                f"stress-{run_number}", script_count
            )
            for post_index, post_id in enumerate(stress_posts):
                job_id = _enqueue(post_id, "0")
                assert job_id not in stress_job_ids
                stress_job_ids.add(job_id)
                stress_claim = _claim(f"stress-worker-{run_number}-{post_index}")
                assert stress_claim[0] == job_id
                assert stress_claim[2] == "1"
                _fail(
                    job_id,
                    f"stress-worker-{run_number}-{post_index}",
                    stress_claim[1],
                    code="stress_terminal",
                )

        assert len(stress_job_ids) == sum(batch_sizes)
        assert _scalar(
            "SELECT count(*)::TEXT FROM public.semantic_scene_image_jobs "
            "WHERE status IN ('queued', 'processing')"
        ) == "0"

        heartbeat = _psql(
            DATABASE,
            "SET ROLE service_role; "
            "SELECT worker_id FROM public.heartbeat_semantic_scene_image_worker("
            "'heartbeat-worker', '{\"contract\":\"semantic-scene-image-v2\"}'::JSONB);",
        )
        assert "heartbeat-worker" in heartbeat.stdout
        assert _psql(
            DATABASE,
            "SET ROLE service_role; SELECT public.probe_semantic_scene_image_queue();",
        ).stdout.strip() == "semantic-scene-image-v2"
    finally:
        for process in (cutover_stale, cutover_holder):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
