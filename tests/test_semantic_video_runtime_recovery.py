from datetime import datetime, timedelta, timezone

import pytest
from app.features.semantic_videos.recovery import generation_error


def run_error(**updates):
    return {"id": "run-1", "stage": "failed", "failure_envelope": {"runtime_failure": True, "code": "ReadError", "stage": "generating"}, **updates}


def test_runtime_error_offers_safe_retry_and_never_exposes_exception_payload():
    run = run_error()
    run['failure_envelope']['message'] = 'secret-key=do-not-display'
    result = generation_error(run, [{"take_index": 0, "submission_state": "completed"}])
    assert result['retry_allowed'] is True
    assert 'secret-key' not in result['message']


@pytest.mark.parametrize('state', ['intent_persisted', 'submission_unknown'])
def test_unknown_submission_is_visible_but_cannot_be_retried(state):
    takes = [{"id": "take", "take_index": 0, "submission_state": state}]
    for stage in ['generating', 'failed']:
        result = generation_error(run_error(stage=stage), takes)
        assert result['retry_allowed'] is False
        assert 'Contact support' in result['message']


def test_live_intent_does_not_announce_failure_while_original_worker_is_running():
    run = run_error(stage='generating', lease_owner='worker', lease_expires_at=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat())
    assert generation_error(run, [{'take_index': 0, 'submission_state': 'intent_persisted'}]) is None


def test_proven_pre_provider_failure_offers_retry_only_for_exact_latest_take():
    error = {'runtime_failure': True, 'code': 'submission_intent_acknowledgement_lost', 'provider_call_entered': False, 'take_id': 'take', 'request_hash': 'hash'}
    run = run_error(failure_envelope=error)
    take = {'id': 'take', 'take_index': 0, 'attempt': 1, 'request_hash': 'hash', 'submission_state': 'intent_persisted', 'quota_state': 'reserved'}
    assert generation_error(run, [take])['retry_allowed'] is True
    for field, value in [('id','other'), ('request_hash','other'), ('operation_id','accepted'), ('quota_state','consumed'), ('submission_state','submission_unknown')]:
        assert generation_error(run, [{**take, field: value}])['retry_allowed'] is False
    assert generation_error(run, [take, {**take, 'id': 'new', 'attempt': 2}])['retry_allowed'] is False


def test_progress_exposes_durable_error_and_retry_endpoint_uses_rpc(monkeypatch):
    from tests.test_semantic_video_handlers import _install_repository, _seed_awaiting_paid_run
    from app.main import app
    from fastapi.testclient import TestClient
    handlers, state, _ = _install_repository(monkeypatch)
    _seed_awaiting_paid_run(state)
    state['run'].update(stage='failed', plan_hash='a'*64, failure_envelope={'runtime_failure': True, 'stage': 'generating', 'code': 'ReadError'})
    calls = []
    monkeypatch.setattr(handlers, 'resume_runtime_failure', lambda **kw: calls.append(kw) or {**state['run'], 'stage': 'generating'})
    client = TestClient(app, base_url='http://localhost')
    progress = client.get('/semantic-videos/posts/post-1/progress').json()['data']
    assert progress['generation_error']['retry_allowed'] is True
    assert progress['estimated_remaining_seconds'] is None
    result = client.post('/semantic-videos/posts/post-1/generation-retry', json={'expected_revision': progress['revision'], 'plan_hash': progress['plan_hash']})
    assert result.status_code == 200, result.text
    assert len(calls) == 1 and calls[0]['expected_revision'] == progress['revision']
    state['takes'] = [{'id':'ambiguous','take_index':0,'submission_state':'submission_unknown'}]
    result = client.post('/semantic-videos/posts/post-1/generation-retry', json={'expected_revision': progress['revision'], 'plan_hash': progress['plan_hash']})
    assert result.status_code == 409
    assert len(calls) == 1


def test_missing_worker_is_reported_after_reclaim_window_without_timing_out_a_live_provider():
    updated=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
    run=run_error(stage='generating',failure_envelope=None,updated_at=updated)
    takes=[{'take_index':0,'submission_state':'submitted','operation_id':'accepted'}]
    assert generation_error(run,takes)['code']=='worker_not_progressing'
    run.update(lease_owner='worker',lease_expires_at=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat())
    assert generation_error(run,takes) is None
