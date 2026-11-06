"""Delivery/optional-review separation and failure isolation through production functions."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.core.errors import ValidationError, StateTransitionError
from app.features.semantic_videos.optional_qa import delivery_gate_satisfied, optional_qa_projection
from workers.semantic_video_worker import ProductionStageRunner
from workers.semantic_video_review_worker import OptionalVideoQAWorker


@pytest.mark.parametrize('stage,key', [('identity_qa','visual_qa'), ('voice_qa','voice_qa')])
def test_foreground_defers_optional_evaluators_without_media_downloads(stage, key, tmp_path, monkeypatch):
    runner=ProductionStageRunner(storage=object(),work_root=tmp_path)
    monkeypatch.setattr(runner,'_materialize_manifest',lambda *args: pytest.fail('No download or evaluator before delivery'))
    run={'artifact_manifest':{'pipeline_manifest':{'takes':[{'transcript_qa':{'passed':True}}]}}}
    original=deepcopy(run)
    result=runner.run_stage(stage=stage,run=run,takes=[])
    manifest=result['artifacts']['pipeline_manifest']
    assert result['passed'] is True
    assert manifest[key]=={'passed':False,'status':'deferred'}
    assert delivery_gate_satisfied(manifest,key)
    assert manifest['takes']==original['artifact_manifest']['pipeline_manifest']['takes']
    assert run==original


def test_deferred_policy_never_turns_a_failed_or_missing_report_into_a_pass():
    for key in ('visual_qa','voice_qa'):
        assert not delivery_gate_satisfied({key:{'status':'deferred','passed':False}},key)
        assert not delivery_gate_satisfied({'optional_qa_deferred':True,key:{'passed':False}},key)
        assert not delivery_gate_satisfied({'optional_qa_deferred':True},key)
        assert not delivery_gate_satisfied({'optional_qa_deferred':True,'transcript_qa':{'status':'deferred'}},'transcript_qa')


@pytest.mark.parametrize('visual_outcome', ['passed','finding','unavailable'])
def test_post_delivery_visual_and_voice_checks_run_independently(tmp_path,monkeypatch,visual_outcome):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'takes':[],'visual_qa':{'passed':False,'status':'deferred'}}))
    calls=[]
    def write(key,report):
        value=json.loads(path.read_text());value[key]=report;path.write_text(json.dumps(value))
    class Pipeline:
        _atomic_write_json=staticmethod(lambda p,v:p.write_text(json.dumps(v)))
        @staticmethod
        def build_contact_sheet(p):calls.append('contact')
        @staticmethod
        def run_visual_qa(p):
            calls.append('visual')
            if visual_outcome=='unavailable':raise RuntimeError('private-provider-detail')
            write('visual_qa',{'passed':visual_outcome=='passed','blocking_reasons':[] if visual_outcome=='passed' else ['White subtitles detected.']})
            if visual_outcome=='finding':raise ValidationError('visual failed')
        @staticmethod
        def run_voice_qa(p):calls.append('voice');write('voice_qa',{'passed':True})
    runner=ProductionStageRunner(storage=object(),work_root=tmp_path)
    monkeypatch.setattr(runner,'_materialize_manifest',lambda *args:path)
    monkeypatch.setattr(runner,'_runner',lambda:Pipeline)
    run={'id':'run-1','stage':'completed','final_caption_uri':'unchanged','final_caption_sha256':'a'*64}
    original=deepcopy(run)
    report=runner.run_optional_qa(run=run,takes=[])
    assert calls==['contact','visual','voice']
    assert report['checks']['voice']['status']=='passed'
    assert report['status']==('failed' if visual_outcome=='unavailable' else 'completed')
    assert 'private-provider-detail' not in json.dumps(report)
    assert run==original
    if visual_outcome=='finding':assert report['findings']==['White subtitles detected.']


def test_optional_checks_cannot_start_before_completed_delivery(tmp_path):
    runner=ProductionStageRunner(storage=object(),work_root=tmp_path)
    with pytest.raises(StateTransitionError,match='completed delivery'):
        runner.run_optional_qa(run={'stage':'uploading'},takes=[])


@pytest.mark.parametrize('unavailable',[False,True])
def test_background_worker_uses_only_review_repository_and_preserves_media(unavailable):
    run={'id':'r','stage':'completed','lease_token':'lease','final_caption_uri':'video','final_caption_sha256':'a'*64}
    original=deepcopy(run);events=[]
    class Repo:
        def claim(self,**kwargs):events.append('claim');return deepcopy(run)
        def takes(self,r):return []
        def finish(self,**kwargs):events.append(('finish',kwargs))
    class Heartbeat:
        def __init__(self,**kwargs):pass
        def start(self):events.append('heartbeat')
        def stop(self):events.append('stop')
    class Stages:
        def run_optional_qa(self,**kwargs):
            events.append('evaluate')
            assert kwargs['run']['stage']=='completed'
            if unavailable:raise RuntimeError('private error')
            return {'status':'completed','checks':{},'findings':['White text needs review.']}
        def cleanup_run_workspace(self,r):events.append('cleanup')
    result=OptionalVideoQAWorker(repo=Repo(),stage_runner=Stages(),heartbeat_factory=Heartbeat,worker_id='review').tick()
    assert events[:3]==['claim','heartbeat','evaluate']
    assert events[-2:]==['stop','cleanup']
    assert result['status']==('failed' if unavailable else 'completed')
    assert run==original
    assert 'private error' not in json.dumps(result)


def test_public_review_projection_excludes_internal_results_and_leases():
    run={'artifact_manifest':{'optional_qa':{'status':'completed','lease_token':'secret','checks':{'provider':'private'},'findings':['Visible finding']}}}
    assert optional_qa_projection(run)=={'status':'completed','findings':['Visible finding']}


@pytest.mark.parametrize('status',['pending','running','completed','failed'])
def test_progress_api_preserves_playback_and_exposes_optional_status(monkeypatch,status):
    from tests.test_semantic_video_handlers import _install_repository,_seed_awaiting_paid_run
    from fastapi.testclient import TestClient
    handlers,state,_storage=_install_repository(monkeypatch)
    from app.main import app
    _seed_awaiting_paid_run(state)
    state['run'].update(stage='completed',final_video_uri='https://stored/raw.mp4',final_video_sha256='a'*64,final_caption_uri='https://stored/caption.mp4',final_caption_sha256='b'*64,artifact_manifest={'optional_qa':{'status':status,'findings':['Review this result.']}})
    result=TestClient(app,base_url='http://localhost').get('/semantic-videos/posts/post-1/progress')
    assert result.status_code==200
    progress=result.json()['data']
    assert progress['stage']=='completed'
    assert progress['final_caption_url']=='https://stored/caption.mp4'
    assert progress['optional_qa']=={'status':status,'findings':['Review this result.']}
    assert progress['generation_error'] is None
    assert result.headers['cache-control']=='no-store, max-age=0'
