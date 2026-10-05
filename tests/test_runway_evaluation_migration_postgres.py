"""Runway evaluation migration: RPC-only writes, submission fence, credit ledger, and poll leases."""

import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261001000000_runway_video_evaluations.sql"
QUOTA_MIGRATIONS = (
    ROOT / "supabase/migrations/0211_add_video_provider_quota_guard.sql",
    ROOT / "supabase/migrations/20260401192535_fix_completed_video_quota_accounting.sql",
)
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")

FUNCTIONS = {
    "create_runway_video_evaluation": "JSONB, INTEGER, INTEGER, INTEGER",
    "mark_runway_video_evaluation_submitting": "UUID",
    "acknowledge_runway_video_evaluation": "UUID, TEXT, NUMERIC, TEXT",
    "fail_runway_video_evaluation_before_submit": "UUID, TEXT, TEXT, JSONB",
    "mark_runway_video_evaluation_submission_unknown": "UUID, TEXT, TEXT, JSONB",
    "reconcile_stale_runway_video_evaluations": "INTEGER, INTEGER",
    "claim_runway_video_evaluations": "TEXT, INTEGER, INTEGER, TEXT, TEXT",
    "record_runway_video_evaluation_poll": "UUID, UUID, TEXT, NUMERIC, TIMESTAMPTZ, JSONB",
    "complete_runway_video_evaluation": "UUID, UUID, JSONB, INTEGER",
    "fail_runway_video_evaluation_after_submit": "UUID, UUID, TEXT, TEXT, TEXT, JSONB, TEXT, INTEGER",
}


def test_migration_static_contract():
    text = MIGRATION.read_text()
    bodies = re.split(r"CREATE OR REPLACE FUNCTION public\.", text)[1:]
    assert sorted(body.split("(", 1)[0] for body in bodies) == sorted(FUNCTIONS)
    for body in bodies:
        header = body.split("AS $$", 1)[0]
        assert "SECURITY DEFINER" in header and "SET search_path = ''" in header, body.split("(", 1)[0]
    for name, signature in FUNCTIONS.items():
        assert f"REVOKE ALL ON FUNCTION public.{name}({signature})\n  FROM PUBLIC, anon, authenticated;" in text
        assert f"GRANT EXECUTE ON FUNCTION public.{name}({signature})\n  TO service_role;" in text
    assert "REVOKE ALL ON TABLE public.runway_video_evaluations FROM PUBLIC, anon, authenticated, service_role;" in text
    assert "GRANT SELECT ON TABLE public.runway_video_evaluations TO service_role;" in text
    assert "ENABLE ROW LEVEL SECURITY" in text
    # Evaluations must never touch production delivery state.
    for forbidden in ("UPDATE public.posts", "UPDATE public.semantic_video_takes", "UPDATE public.semantic_video_runs", "INSERT INTO public.posts"):
        assert forbidden not in text
    # Veo limits and freezes are never borrowed: reservations are always for the Runway provider.
    assert text.count("public.reserve_video_provider_quota(\n    'runway_seedance_2_5'") == 1
    assert "veo_3_1" not in text


def _sql(text: str) -> str:
    return subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-At", "-d", "postgres"],
        input=text,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


SETUP = """
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF;
END $$;
CREATE OR REPLACE FUNCTION public.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;
CREATE TABLE public.posts (id uuid PRIMARY KEY);
CREATE TABLE public.batches (id uuid PRIMARY KEY);
CREATE TABLE public.semantic_video_runs (id uuid PRIMARY KEY, post_id uuid, batch_id uuid);
INSERT INTO public.posts VALUES ('00000000-0000-0000-0000-0000000000a1'), ('00000000-0000-0000-0000-0000000000a2');
INSERT INTO public.batches VALUES ('00000000-0000-0000-0000-0000000000b1');
INSERT INTO public.semantic_video_runs VALUES
    ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1');
"""

CHECKS = r"""
CREATE TEMP TABLE results (name text, value text);
CREATE FUNCTION pg_temp.payload(p_suffix text, p_post uuid DEFAULT '00000000-0000-0000-0000-0000000000a1') RETURNS jsonb
LANGUAGE sql AS $$
  SELECT jsonb_build_object(
    'post_id', p_post,
    'semantic_run_id', '00000000-0000-0000-0000-0000000000c1',
    'take_id', '00000000-0000-0000-0000-0000000000d1',
    'take_index', 0, 'take_attempt', 1, 'provider_model', 'seedance2_5',
    'requested_resolution', '720p', 'requested_ratio', '720:1280', 'requested_duration_seconds', 8,
    'request_contract', jsonb_build_object('suffix', p_suffix),
    'request_hash', repeat('1', 64),
    'source_provenance', jsonb_build_object('suffix', p_suffix),
    'estimated_credits', 240, 'estimated_usd', 2.40,
    'reservation_key', 'runway_seedance_2_5:evaluation:' || p_suffix,
    'requested_by', 'ops@example.test', 'poller_environment', 'production', 'poller_scope', 'studio.example.test'
  ) $$;

-- 1. Admission reserves Runway credits in the shared ledger.
SELECT 'first_allowed=' || (public.create_runway_video_evaluation(pg_temp.payload('one'), 2000, 1, 0) ->> 'allowed');
SELECT 'ledger=' || provider || ':' || reserved_units || ':' || status FROM public.video_provider_quota_reservations WHERE reservation_key = 'runway_seedance_2_5:evaluation:one';
SELECT 'second_reason=' || (public.create_runway_video_evaluation(pg_temp.payload('two'), 2000, 1, 0) ->> 'reason');
SELECT 'budget_reason=' || (public.create_runway_video_evaluation(pg_temp.payload('budget'), 100, 5, 0) ->> 'reason');
SELECT 'paced_reason=' || (public.create_runway_video_evaluation(pg_temp.payload('paced'), 2000, 5, 3600) ->> 'reason');
DO $$ BEGIN
  PERFORM public.create_runway_video_evaluation(pg_temp.payload('foreign', '00000000-0000-0000-0000-0000000000a2'), 2000, 5, 0);
  INSERT INTO results VALUES ('foreign_run', 'allowed');
EXCEPTION WHEN raise_exception THEN INSERT INTO results VALUES ('foreign_run', 'blocked');
END $$;

-- 2. The submit fence admits exactly one intent.
SELECT 'submitting=' || (public.mark_runway_video_evaluation_submitting(id) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:one';
DO $$ BEGIN
  PERFORM public.mark_runway_video_evaluation_submitting(id) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:one';
  INSERT INTO results VALUES ('double_intent', 'allowed');
EXCEPTION WHEN serialization_failure THEN INSERT INTO results VALUES ('double_intent', 'blocked');
END $$;

-- 3. Acknowledgement consumes credits and is idempotent for the same task only.
SELECT 'ack=' || (public.acknowledge_runway_video_evaluation(id, 'task_0123456789abcdef', 240) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:one';
SELECT 'ack_replay=' || (public.acknowledge_runway_video_evaluation(id, 'task_0123456789abcdef', 240) ->> 'task_id') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:one';
SELECT 'consumed=' || consumed_units FROM public.video_provider_quota_reservations WHERE reservation_key = 'runway_seedance_2_5:evaluation:one';
DO $$ BEGIN
  PERFORM public.acknowledge_runway_video_evaluation(id, 'task_other_0123456789', 240) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:one';
  INSERT INTO results VALUES ('second_task', 'allowed');
EXCEPTION WHEN serialization_failure THEN INSERT INTO results VALUES ('second_task', 'blocked');
END $$;

-- 4. Claims are fenced by environment, scope, schedule, and lease.
UPDATE public.runway_video_evaluations SET next_poll_at = now() - interval '1 second' WHERE reservation_key LIKE '%:one';
SELECT 'wrong_scope=' || count(*) FROM public.claim_runway_video_evaluations('poller-a', 300, 5, 'production', 'other.example.test');
CREATE TEMP TABLE claimed AS SELECT * FROM public.claim_runway_video_evaluations('poller-a', 300, 5, 'production', 'studio.example.test');
SELECT 'claimed=' || count(*) FROM claimed;
SELECT 'double_claim=' || count(*) FROM public.claim_runway_video_evaluations('poller-b', 300, 5, 'production', 'studio.example.test');
DO $$ BEGIN
  PERFORM public.record_runway_video_evaluation_poll(id, gen_random_uuid(), 'RUNNING', 0.2, now()) FROM claimed;
  INSERT INTO results VALUES ('stolen_lease', 'allowed');
EXCEPTION WHEN serialization_failure THEN INSERT INTO results VALUES ('stolen_lease', 'blocked');
END $$;
SELECT 'polled=' || (public.record_runway_video_evaluation_poll(id, lease_token, 'RUNNING', 0.2, now()) ->> 'status') FROM claimed;

-- 5. Completion requires a fresh lease and finalizes the ledger.
UPDATE public.runway_video_evaluations SET next_poll_at = now() - interval '1 second' WHERE reservation_key LIKE '%:one';
CREATE TEMP TABLE claimed_again AS SELECT * FROM public.claim_runway_video_evaluations('poller-a', 300, 5, 'production', 'studio.example.test');
SELECT 'completed=' || (public.complete_runway_video_evaluation(
  id, lease_token,
  jsonb_build_object('storage_key', 'videos/runway-evaluations/x.mp4', 'url', 'https://cdn.example.test/x.mp4', 'sha256', repeat('a', 64), 'byte_length', 1024, 'probe', '{}'::jsonb),
  240) ->> 'status') FROM claimed_again;
SELECT 'ledger_final=' || status FROM public.video_provider_quota_reservations WHERE reservation_key = 'runway_seedance_2_5:evaluation:one';

-- 6. A provable rejection releases the reservation.
SELECT 'third_allowed=' || (public.create_runway_video_evaluation(pg_temp.payload('three'), 2000, 1, 0) ->> 'allowed');
SELECT 'third_submitting=' || (public.mark_runway_video_evaluation_submitting(id) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:three';
SELECT 'rejected=' || (public.fail_runway_video_evaluation_before_submit(id, 'runway_rejected', 'bad request', '{}'::jsonb) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:three';
SELECT 'released=' || released_units || ':' || status FROM public.video_provider_quota_reservations WHERE reservation_key = 'runway_seedance_2_5:evaluation:three';

-- 7. An ambiguous submission keeps its reservation and blocks new admissions.
SELECT 'fourth_allowed=' || (public.create_runway_video_evaluation(pg_temp.payload('four'), 2000, 1, 0) ->> 'allowed');
SELECT 'fourth_submitting=' || (public.mark_runway_video_evaluation_submitting(id) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:four';
SELECT 'unknown=' || (public.mark_runway_video_evaluation_submission_unknown(id, 'runway_submission_ambiguous', 'timeout', '{}'::jsonb) ->> 'status') FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:four';
SELECT 'unknown_ledger=' || reserved_units || ':' || released_units || ':' || status FROM public.video_provider_quota_reservations WHERE reservation_key = 'runway_seedance_2_5:evaluation:four';
SELECT 'after_unknown_reason=' || (public.create_runway_video_evaluation(pg_temp.payload('five'), 2000, 1, 0) ->> 'reason');
SELECT 'unknown_claimable=' || count(*) FROM public.claim_runway_video_evaluations('poller-a', 300, 5, 'production', 'studio.example.test');

-- 8. Crash recovery releases abandoned reservations and blocks stale intents.
SELECT 'sixth_allowed=' || (public.create_runway_video_evaluation(pg_temp.payload('six'), 2000, 5, 0) ->> 'allowed');
UPDATE public.runway_video_evaluations SET created_at = now() - interval '1 hour' WHERE reservation_key LIKE '%:six';
SELECT 'reconciled=' || public.reconcile_stale_runway_video_evaluations(300, 900)::text;
SELECT 'six_status=' || status || ':' || error_code FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:six';

-- 9. Privileges: RPC-only writes for service_role, nothing for anon/authenticated.
SELECT 'anon_exec=' || has_function_privilege('anon', 'public.create_runway_video_evaluation(jsonb,integer,integer,integer)', 'EXECUTE');
SELECT 'auth_exec=' || has_function_privilege('authenticated', 'public.claim_runway_video_evaluations(text,integer,integer,text,text)', 'EXECUTE');
SELECT 'service_exec=' || has_function_privilege('service_role', 'public.create_runway_video_evaluation(jsonb,integer,integer,integer)', 'EXECUTE');
SELECT 'service_select=' || has_table_privilege('service_role', 'public.runway_video_evaluations', 'SELECT');
SELECT 'service_insert=' || has_table_privilege('service_role', 'public.runway_video_evaluations', 'INSERT');
SELECT 'service_update=' || has_table_privilege('service_role', 'public.runway_video_evaluations', 'UPDATE');
SELECT 'anon_select=' || has_table_privilege('anon', 'public.runway_video_evaluations', 'SELECT');

SELECT name || '=' || value FROM results ORDER BY name;
ROLLBACK;
"""


@pytest.mark.skipif(not CONTAINER, reason="Requires a disposable PostgreSQL container")
def test_runway_evaluation_lifecycle_in_postgres():
    script = SETUP + "".join(path.read_text() for path in QUOTA_MIGRATIONS) + MIGRATION.read_text() + CHECKS
    lines = set(_sql(script).splitlines())
    expected = {
        "first_allowed=true",
        "ledger=runway_seedance_2_5:240:reserved",
        "second_reason=concurrency_limit_reached",
        "budget_reason=daily_quota_exhausted",
        "paced_reason=submission_paced",
        "foreign_run=blocked",
        "submitting=submitting",
        "double_intent=blocked",
        "ack=submitted",
        "ack_replay=task_0123456789abcdef",
        "consumed=240",
        "second_task=blocked",
        "wrong_scope=0",
        "claimed=1",
        "double_claim=0",
        "stolen_lease=blocked",
        "polled=processing",
        "completed=completed",
        "ledger_final=completed",
        "third_allowed=true",
        "third_submitting=submitting",
        "rejected=failed",
        "released=240:released",
        "fourth_allowed=true",
        "fourth_submitting=submitting",
        "unknown=submission_unknown",
        "unknown_ledger=240:0:reserved",
        "after_unknown_reason=concurrency_limit_reached",
        "unknown_claimable=0",
        "sixth_allowed=true",
        "six_status=failed:abandoned_before_submit",
        "anon_exec=false",
        "auth_exec=false",
        "service_exec=true",
        "service_select=true",
        "service_insert=false",
        "service_update=false",
        "anon_select=false",
    }
    missing = expected - lines
    assert not missing, f"missing: {sorted(missing)}\noutput: {sorted(lines)}"
    assert any(line.startswith("reconciled=") and '"released": 1' in line for line in lines)
