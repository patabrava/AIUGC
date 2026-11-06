"""Real PostgreSQL completion ordering, restart, fencing and report isolation."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from tests.test_semantic_video_worker_migration_postgres import _psql, _scalar, ROOT
from tests.test_semantic_video_runtime_recovery_postgres import database, seed, DB, RUN, TAKE, claim, release

pytestmark=pytest.mark.skipif(not os.getenv('SEMANTIC_UGC_POSTGRES_CONTAINER'),reason='Requires isolated PostgreSQL')
MIGRATION=ROOT/'supabase/migrations/20261106000000_semantic_video_optional_qa.sql'


@pytest.fixture(scope='module',autouse=True)
def optional_schema(database):
    _psql(DB,MIGRATION.read_text());_psql(DB,MIGRATION.read_text())


def ready(*,deferred=True):
    token=claim()['lease_token']
    _psql(DB,f"UPDATE public.semantic_video_takes SET submission_state='completed' WHERE id='{TAKE}'; UPDATE public.semantic_video_runs SET stage='uploading' WHERE id='{RUN}';")
    artifacts=json.dumps({'pipeline_manifest':{'optional_qa_deferred':deferred},'delivery':{'raw':{'url':'https://stored/final.mp4'}}})
    data=json.loads(_scalar(DB,f"SELECT public.complete_semantic_video_run('{RUN}','worker-1','{token}','https://stored/raw.mp4','{'a'*64}','https://stored/caption.mp4','{'b'*64}','{artifacts}'::jsonb)"))
    return token,data


def review_claim(worker='review'):
    value=_psql(DB,f"SET ROLE service_role; COPY (SELECT to_jsonb(r) FROM public.claim_semantic_video_optional_qa('{worker}',120) r) TO STDOUT;").stdout.strip()
    # The shared psql harness prints SET/COPY command tags around COPY rows.
    rows=[json.loads(line) for line in value.splitlines() if line.startswith('{')]
    assert len(rows)<=1
    return rows[0] if rows else None


def finish(token,sha='b'*64,check=True):
    report=json.dumps({'status':'completed','checks':{'visual':{'status':'needs_review'}},'findings':['White subtitle detected.'],'final_caption_uri':'https://attacker/overwrite'})
    return _psql(DB,f"SET ROLE service_role; SELECT public.finish_semantic_video_optional_qa('{RUN}','review','{token}','{sha}','{report}'::jsonb);",check=check)


def test_completion_atomically_enqueues_after_playable_media_and_does_not_block():
    assert review_claim() is None
    token,data=ready()
    assert data['run']['stage']=='completed'
    assert data['run']['artifact_manifest']['optional_qa']['status']=='pending'
    assert _scalar(DB,"SELECT video_status FROM public.posts")=='caption_completed'
    assert review_claim() is None  # foreground lease still held
    release(token)
    run=review_claim();assert run['stage']=='completed'
    assert run['artifact_manifest']['optional_qa']['status']=='running'
    before=_scalar(DB,f"SELECT jsonb_build_array(stage,revision,final_video_uri,final_video_sha256,final_caption_uri,final_caption_sha256,reserved_submission_count,reserved_estimated_cost_usd) FROM public.semantic_video_runs WHERE id='{RUN}'")
    finish(run['lease_token'])
    assert _scalar(DB,f"SELECT jsonb_build_array(stage,revision,final_video_uri,final_video_sha256,final_caption_uri,final_caption_sha256,reserved_submission_count,reserved_estimated_cost_usd) FROM public.semantic_video_runs WHERE id='{RUN}'")==before
    assert _scalar(DB,"SELECT video_url FROM public.posts")=='https://stored/caption.mp4'
    assert review_claim() is None


def test_only_one_optional_claim_and_stale_delivery_or_lease_cannot_finish():
    token,_=ready();release(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:review_claim(),range(2)))
    assert sum(value is not None for value in claims)==1
    first=next(value for value in claims if value)
    assert finish(first['lease_token'],sha='c'*64,check=False).returncode!=0
    _psql(DB,f"UPDATE public.semantic_video_runs SET lease_expires_at=now()-interval '1 second' WHERE id='{RUN}';")
    second=review_claim();assert second['lease_token']!=first['lease_token']
    assert finish(first['lease_token'],check=False).returncode!=0
    finish(second['lease_token'])


def test_crashed_optional_work_is_reclaimed_but_bounded_and_never_reopens_delivery():
    token,_=ready();release(token)
    for attempt in range(1,4):
        run=review_claim();assert run['artifact_manifest']['optional_qa']['attempts']==attempt
        _psql(DB,f"UPDATE public.semantic_video_runs SET lease_expires_at=now()-interval '1 second' WHERE id='{RUN}';")
    assert review_claim() is None
    assert _scalar(DB,f"SELECT stage||':'||(artifact_manifest #>> '{{optional_qa,status}}') FROM public.semantic_video_runs WHERE id='{RUN}'")=='completed:failed'
    assert _scalar(DB,"SELECT video_status FROM public.posts")=='caption_completed'


def test_legacy_completions_are_not_enqueued_and_unprivileged_calls_are_denied():
    token,_=ready(deferred=False);release(token)
    assert review_claim() is None
    for role in ('anon','authenticated'):
        assert _psql(DB,f"SET ROLE {role}; SELECT * FROM public.claim_semantic_video_optional_qa('review',120);",check=False).returncode!=0


def test_repaired_delivery_requeues_and_fences_inflight_old_result():
    token,_=ready();release(token)
    old=review_claim()
    _psql(DB,f"UPDATE public.semantic_video_runs SET final_caption_uri='https://stored/repaired.mp4',final_caption_sha256='{'c'*64}' WHERE id='{RUN}';")
    assert _scalar(DB,f"SELECT artifact_manifest #>> '{{optional_qa,status}}' FROM public.semantic_video_runs WHERE id='{RUN}'")=='pending'
    assert finish(old['lease_token'],check=False).returncode!=0
    _psql(DB,f"SELECT public.release_semantic_video_lease('{RUN}','review','{old['lease_token']}');")
    latest=review_claim();assert latest['artifact_manifest']['optional_qa']['attempts']==1
    finish(latest['lease_token'],sha='c'*64)
    assert _scalar(DB,f"SELECT final_caption_uri FROM public.semantic_video_runs WHERE id='{RUN}'")=='https://stored/repaired.mp4'
