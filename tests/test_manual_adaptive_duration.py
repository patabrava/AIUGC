"""Manual copy drives approval, provider capacity, and speech-paced delivery."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient

from app.features.shot_production.duration import (
    ADAPTIVE_MANUAL_DURATION, build_manual_duration_contract,
    resolve_post_duration_contract, resolve_run_duration_contract,
)
from app.features.shot_production.planner import plan_manual_editorial_beats
from app.features.shot_production.runner import _script_delivery_duration_contract
from app.features.semantic_videos.service import compile_semantic_video_plan
from tests.test_semantic_video_plan import _png_bytes, _snapshots
from tests.test_posts_script_review import _manual_semantic_storage, _FakeSupabase, app, posts_handlers

SCREENSHOT_SCRIPT = (
    "Sie haben die Accounts. Wir bauen das System, das aus Interesse qualifizierte "
    "Pipeline macht, für Unternehmen mit komplexen Kaufentscheidungen."
)


@pytest.mark.parametrize("script", [
    "Hallo.", "Hallo Welt.", SCREENSHOT_SCRIPT,
    " ".join(["Wort"] * 37) + ".",
    " ".join(["Wort"] * 151) + ".",
    " ".join(["Wort"] * 799) + ".",
])
def test_manual_approval_to_free_plan_preserves_every_word(monkeypatch, script):
    storage = _manual_semantic_storage("post-adaptive", 8)
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    response = TestClient(app, base_url="http://localhost").put(
        "/posts/post-adaptive/script-review", data={"action": "approved", "script_text": script},
    )
    assert response.status_code == 200, response.text
    seed = storage["posts"][0]["seed_data"]
    assert seed["script"] == script
    assert seed["semantic_duration_mode"] == ADAPTIVE_MANUAL_DURATION
    master = _png_bytes()
    post, batch, reference = _snapshots(master=master, script=script, duration=8)
    batch["creation_mode"] = "manual_semantic_ugc"
    post["seed_data"] = seed
    plan = compile_semantic_video_plan(
        post_snapshot=post, batch_snapshot=batch, reference_snapshot=reference,
        approved_frame_bytes=master,
    )
    run = plan.run_payload
    contract = resolve_run_duration_contract(run)
    assert contract.as_dict() == seed["semantic_duration_contract"]
    beats = plan_manual_editorial_beats(script)
    assert " ".join(beat.text for beat in beats) == script
    assert all(beat.provider_duration_seconds in (4, 6, 8) for beat in beats)
    assert all(beat.estimated_speech_seconds <= 7.5 for beat in beats)
    assert contract.minimum_take_count == len(beats)
    snapshot = run["script_snapshot"]
    delivery = _script_delivery_duration_contract(snapshot)
    assert delivery["duration_mode"] == ADAPTIVE_MANUAL_DURATION
    assert delivery["maximum"] == sum(beat.provider_duration_seconds for beat in beats)
    assert delivery["minimum"] < sum(beat.estimated_speech_seconds for beat in beats)
    altered = deepcopy(run)
    altered["duration_contract"]["delivery_max_seconds"] += 1
    with pytest.raises(ValueError, match="changed after approval"):
        resolve_run_duration_contract(altered)


@pytest.mark.parametrize("script", ["", "   ", "...", "Wort " * 801, "Hallo.Welt."])
def test_manual_invalid_input_remains_blocked(script):
    with pytest.raises(ValueError):
        build_manual_duration_contract(script)


def test_historical_and_automatic_contracts_stay_fixed():
    post = {"seed_data": {"script": SCREENSHOT_SCRIPT}}
    batch = {"creation_mode": "manual_semantic_ugc", "target_duration_seconds": 16}
    assert resolve_post_duration_contract(post, batch).duration_mode == "fixed"
    post["seed_data"]["semantic_duration_mode"] = ADAPTIVE_MANUAL_DURATION
    assert resolve_post_duration_contract(post, batch).duration_mode == ADAPTIVE_MANUAL_DURATION
    batch["creation_mode"] = "semantic_ugc"
    assert resolve_post_duration_contract(post, batch).duration_mode == "fixed"


@pytest.mark.parametrize('script', ['Hallo Welt.', SCREENSHOT_SCRIPT, ' '.join(['Wort'] * 151) + '.'])
def test_worker_materializes_adaptive_contract_without_fixed_duration_rederivation(tmp_path, script):
    import json
    from hashlib import sha256
    from tests.test_semantic_video_worker import _takes, _actor_reference_snapshot
    from workers.semantic_video_worker import ProductionStageRunner, SemanticVideoWorker
    from app.features.shot_production.provenance import build_semantic_script_snapshot
    from app.features.shot_production.runner import _validate_duration_planning_contract

    contract = build_manual_duration_contract(script)
    master, takes = _takes(contract.minimum_take_count)
    raw_payloads = {}
    for take, beat in zip(takes, plan_manual_editorial_beats(script)):
        raw_bytes = f'raw-{beat.index}'.encode()
        uri = f'https://storage/raw-{beat.index}.mp4'
        raw_payloads[uri] = raw_bytes
        take.update(submission_state='completed', raw_artifact_uri=uri,
                    raw_artifact_sha256=sha256(raw_bytes).hexdigest(),
                    beat_text=beat.text, provider_duration_seconds=beat.provider_duration_seconds)
    class Storage:
        def download_video(self, *, video_url, correlation_id):
            return raw_payloads.get(video_url, master)
    run = {
        'id': 'adaptive-worker-proof', 'requested_duration_seconds': contract.requested_duration_seconds,
        'duration_contract': contract.as_dict(), 'duration_contract_hash': contract.contract_hash,
        'master_hash': sha256(master).hexdigest(),
        'master_snapshot': {'storage_uri': 'https://storage/master.png', 'sha256': sha256(master).hexdigest(),
                            'byte_length': len(master), 'mime_type': 'image/png'},
        'reference_snapshot': _actor_reference_snapshot(master), 'script_hash': sha256(script.encode()).hexdigest(),
        'script_snapshot': build_semantic_script_snapshot(text=script, review_status='approved',
                            word_count=contract.minimum_words, creation_mode='manual_semantic_ugc',
                            target_duration_seconds=contract.requested_duration_seconds,
                            duration_mode=ADAPTIVE_MANUAL_DURATION),
    }
    path = ProductionStageRunner(storage=Storage(), work_root=tmp_path)._materialize_manifest(run, takes)
    manifest = json.loads(path.read_text())
    delivery = _validate_duration_planning_contract(manifest)
    assert delivery == _script_delivery_duration_contract(run['script_snapshot'])
    assert not SemanticVideoWorker._is_single_paid_eight_second_delivery(run, takes)


def test_adaptive_composition_preserves_native_duration_and_speech_cut(tmp_path):
    import json
    from pathlib import Path
    from app.features.shot_production import runner as pipeline
    from app.features.shot_production.provenance import build_semantic_script_snapshot
    from tests.test_shot_production_runner import (
        _manifest_with_raw_takes, SINGLE_TAKE_SCRIPT, _read,
        _DeepgramByCall, _valid_final_probe,
    )
    path = _manifest_with_raw_takes(tmp_path, script=SINGLE_TAKE_SCRIPT, target_length_tier=8)
    payload = _read(path)
    text = 'Hallo Welt.'
    contract = build_manual_duration_contract(text)
    snapshot = build_semantic_script_snapshot(text=text, review_status='approved', word_count=2,
        creation_mode='manual_semantic_ugc', target_duration_seconds=8, duration_mode=ADAPTIVE_MANUAL_DURATION)
    payload['script'].update(snapshot)
    payload['script']['delivery_duration_seconds'] = _script_delivery_duration_contract(snapshot)
    payload['takes'][0]['beat']['text'] = text
    payload['takes'][0]['transcript_qa'] = {'passed': True, 'first_word_start_seconds': 0.2, 'final_word_end_seconds': 1.0}
    payload['takes'][0]['trim_window'] = {'start_seconds': 0.0, 'end_seconds': 1.25, 'source': 'deepgram_word_window'}
    payload['visual_qa'] = {'passed': True}
    payload['voice_qa'] = {'passed': True, 'status': 'not_applicable'}
    payload['request_contract_sha256'] = pipeline._canonical_sha256(pipeline._request_contract_payload(payload))
    path.write_text(json.dumps(payload))
    calls = []
    def stitch(**kwargs):
        calls.append(kwargs)
        return b'stitched', {'stitch_final_duration_s': 1.021333, 'stitch_segment_count': 1}
    def caption(**kwargs):
        output = path.parent / 'captioned.mp4'
        output.write_bytes(b'captioned')
        return str(output)
    result = pipeline.compose_and_caption(path, _DeepgramByCall([text]), stitch_fn=stitch,
        caption_fn=caption, probe_fn=lambda _path: _valid_final_probe('1.021333'))
    assert Path(result['captioned_path']).read_bytes() == b'captioned'
    assert calls[0].get('target_duration_seconds') is None
    assert calls[0]['trim_windows'][0]['end_seconds'] == pytest.approx(1 + 1024 / 48000)
    assert _read(path)['media_qa']['passed'] is True
