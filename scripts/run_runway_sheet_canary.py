"""Explicit one-shot AYRA sheet-only Seedance canary using the evaluation quota RPCs.

No legacy image loader or frame reconstructor is used. Script lineage comes from
one frozen take; image lineage comes exclusively from the new local sheet.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / 'output/ayra-seedance-character-sheet-2026-10-03/ayra-wheelchair-nine-view-character-sheet.png'
SHEET_SHA = '03bb788f0ec1cf899694ba1ffd43fb494c7aeebf751a0ea273d694b72828d609'
POST = '91825c68-5da5-4b50-b1dc-cee45d90abc3'
RUN = '557fdd80-dd78-4bcf-80ad-f05e89b4b8ce'
TAKE = 'c7ae2444-e51a-45f0-8794-0380923fdb1f'
SCRIPT_PROMPT_SHA = '6bff943645a9d96189e24a0f3e435b4d305f7573434faa68950983958823927f'
ACTOR = '4e5c4cbb-3db3-4a72-9c81-82f9b2cbf29b'
SCOPE = 'ayra-sheet-canary-20261003'


def digest(value: Any) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()
    return hashlib.sha256(value).hexdigest()


def sheet_bytes(path: Path = SHEET) -> bytes:
    from PIL import Image
    content = path.read_bytes()
    if digest(content) != SHEET_SHA:
        raise ValueError('Finalized sheet checksum changed')
    im = Image.open(io.BytesIO(content)); im.verify()
    im = Image.open(io.BytesIO(content))
    if im.format != 'PNG' or im.size != (1536, 1024):
        raise ValueError('Finalized sheet format changed')
    return content


def build_prompt(original: str) -> tuple[str, str]:
    if digest(original.encode()) != SCRIPT_PROMPT_SHA:
        raise ValueError('Frozen script source changed')
    lines = re.findall(r'Dialogue:\s*[“"]([^”"]+)[”"]', original)
    if len(lines) != 1 or len(lines[0].split()) != 15:
        raise ValueError('Frozen dialogue is not unique')
    dialogue = lines[0]
    prompt = (
        '@Image 1 is the sole actor identity and wardrobe reference. It is a multi-view '
        'character sheet of one adult presenter seated in her wheelchair. Use its views '
        'only to preserve that same person, face, hair, clothing and wheelchair; generate '
        'one continuous scene with one presenter, never the sheet layout or repeated people. '
        'The actor stays seated in that same wheelchair throughout. '
        'Generate a single eight-second vertical smartphone talking-head advice video in '
        'an ordinary quiet home with soft window daylight. Frame the face clearly, with '
        'the upper body, natural resting hands and chair context visible. Stable camera, '
        'minor natural head motion and small conversational gestures, one unbroken shot. '
        'Speak the following German dialogue exactly once with a natural German voice, '
        'synchronized mouth articulation and conversational cadence. Begin speaking promptly '
        'and fit the complete line naturally within eight seconds. '
        f'Dialogue: “{dialogue}” '
        'Audio: clear close speech, quiet source-location-neutral room ambience. '
        'No music, additional speech, captions, on-screen text, cuts or standing action.'
    )
    return prompt, dialogue


def save(path: Path, value: dict) -> None:
    from scripts.run_runway_new_character_canary import save as atomic_save
    atomic_save(path, value)


def run(args: argparse.Namespace) -> dict:
    import structlog
    logging.disable(logging.CRITICAL)
    def mute(*_args): raise structlog.DropEvent
    structlog.configure(processors=[mute])
    from app.features.runway_evaluations.providers import ProviderSettings
    from app.core.config import get_settings
    from app.adapters.supabase_client import get_supabase
    from app.adapters.runway_client import get_runway_client, build_image_data_uri, RunwayError
    from app.adapters.storage_client import get_storage_client
    from app.features.runway_evaluations.service import (
        RunwayEvaluationRepository, EvaluationDependencies, process_claimed_evaluation,
        poller_environment, probe_mp4, operator_authorized, estimate_credits,
    )
    from scripts.run_runway_new_character_canary import baseline
    settings = ProviderSettings(get_settings(), "runway"); db = get_supabase().client
    operators = [v.strip() for v in settings.runway_evaluation_operator_emails.split(',') if v.strip()]
    if len(operators) != 1 or not operator_authorized(operators[0], settings):
        raise ValueError('Exactly one authorized canary operator is required')
    operator = operators[0]
    repo = RunwayEvaluationRepository(db); client = get_runway_client(settings)
    folder = args.output.resolve(); folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        file = folder / 'manifest.json'
        manifest = json.loads(file.read_text()) if file.exists() else {}
        if args.stage in ('prepare', 'submit'):
            if manifest.get('submission_intent') or manifest.get('evaluation_id') or manifest.get('task_id'):
                raise ValueError('Submission already entered; poll only, never resubmit')
            content = sheet_bytes()
            actor = db.table('actor_identities').select('id,is_active').eq('id', ACTOR).single().execute().data
            if not actor['is_active']: raise ValueError('AYRA is no longer selected')
            take = db.table('semantic_video_takes').select('id,run_id,take_index,attempt,request_contract,seed').eq('id', TAKE).single().execute().data
            if take['run_id'] != RUN: raise ValueError('Script take ownership changed')
            prompt, dialogue = build_prompt(take['request_contract']['prompt'])
            estimate = estimate_credits(resolution='480p', duration_seconds=8, settings=settings)
            if estimate.credits != 160 or estimate.credits > settings.runway_evaluation_max_credits_per_run:
                raise ValueError('Explicit 160-credit canary limit changed')
            if repo.find_moderated_request(post_id=POST, prompt_sha256=digest(prompt.encode()), shot_sha256=SHEET_SHA):
                raise ValueError('This exact sheet and prompt were already moderation rejected')
            account = client.get_organization(correlation_id=SCOPE)
            if not account['seedance2_5_available'] or float(account['credit_balance']) < estimate.credits:
                raise ValueError('Account cannot cover this canary')
            seed = take['request_contract'].get('seed', take['seed'])
            contract = {'provider':'runway','provider_model':'seedance2_5','endpoint':'/v1/text_to_video',
                'reference_mode':'fresh_operator_sheet_only','reference_count':1,'legacy_image_inputs':0,
                'prompt_sha256':digest(prompt.encode()),'dialogue_sha256':digest(dialogue.encode()),
                'script_source_prompt_sha256':SCRIPT_PROMPT_SHA,'duration_seconds':8,'ratio':'480:854',
                'audio':True,'seed':seed,'prompt_image':{'role':'actor_sheet_reference','sha256':SHEET_SHA,
                'mime_type':'image/png','byte_length':len(content),'transport':'data_uri'},
                'estimated_credits':160,'estimated_usd':'1.60'}
            manifest = {'model':'seedance2_5','actor_id':ACTOR,'source_sheet':str(SHEET),
                'source_sha256':SHEET_SHA,'reference_count':1,'legacy_image_inputs':0,
                'script_choice':'earlier established eight-second German test script; H1 working assumption',
                'post_id':POST,'take_id':TAKE,'request_contract':contract,'baseline':baseline(db),
                'estimated_credits':160,'balance_before':account['credit_balance'],'status':'prepared'}
            save(file, manifest)
            if args.stage == 'prepare': return manifest
            if args.confirm_credits != 160: raise ValueError('Must explicitly confirm 160 credits')
            manifest['submission_intent'] = datetime.now(timezone.utc).isoformat(); save(file,manifest)
            payload = {'post_id':POST,'semantic_run_id':RUN,'take_id':TAKE,'take_index':take['take_index'],
                'take_attempt':take['attempt'],'provider_model':'seedance2_5','requested_resolution':'480p',
                'requested_ratio':'480:854','requested_duration_seconds':8,'request_contract':contract,
                'request_hash':digest(contract),'source_provenance':{'source_mode':'fresh_operator_sheet_only',
                'actor_identity_id':ACTOR,'sheet_sha256':SHEET_SHA,'reference_count':1,'legacy_image_inputs':0},
                'estimated_credits':160,'estimated_usd':'1.60',
                'reservation_key':f'runway_seedance_2_5:sheet:{uuid4().hex}','requested_by':operator,
                'poller_environment':poller_environment(settings),'poller_scope':SCOPE}
            admitted = repo.create(payload,daily_credit_limit=settings.runway_evaluation_daily_credit_limit,
                max_active=settings.runway_evaluation_max_active,min_submit_interval_seconds=settings.runway_evaluation_min_submit_interval_seconds)
            if not admitted.get('allowed'):
                manifest.update(status='admission_blocked',reason=admitted.get('reason')); save(file,manifest); return manifest
            eid = admitted['evaluation']['id']; manifest['evaluation_id']=eid; save(file,manifest)
            try: repo.mark_submitting(eid)
            except Exception:
                repo.fail_before_submit(eid,code='submit_intent_not_recorded',message='Runway was never called',details={})
                raise
            try:
                accepted = client.submit_reference_to_video(reference_image=build_image_data_uri(content,'image/png'),
                    prompt_text=prompt,ratio='480:854',duration_seconds=8,audio=True,seed=seed,correlation_id=SCOPE)
            except RunwayError as error:
                manifest.update(status='failed_before_submit' if error.proves_no_task else 'submission_unknown',error_kind=error.kind)
                save(file,manifest)
                if error.proves_no_task:
                    repo.fail_before_submit(eid,code=f'runway_{error.kind}',message=error.message,details={'status_code':error.status_code})
                else:
                    repo.mark_submission_unknown(eid,code='runway_submission_ambiguous',message=error.message,details={'kind':error.kind})
                return manifest
            except Exception as error:
                manifest.update(status='submission_unknown',error_type=type(error).__name__); save(file,manifest)
                repo.mark_submission_unknown(eid,code='runway_submission_unexpected',message='Unknown provider outcome',details={'error_type':type(error).__name__})
                return manifest
            manifest.update(status='submitted',task_id=accepted['task_id'],provider_estimated_credits=accepted['estimated_credits']); save(file,manifest)
            cancel_reason = None
            if accepted['estimated_credits'] is not None and accepted['estimated_credits'] > 160:
                cancelled=client.cancel_task(accepted['task_id'],correlation_id=SCOPE)
                cancel_reason='Provider estimate exceeds explicit cap' if cancelled else None
            repo.acknowledge(eid,task_id=accepted['task_id'],provider_estimated_credits=accepted['estimated_credits'],cancel_reason=cancel_reason)
        else:
            if not manifest.get('task_id'): raise ValueError('No acknowledged task; never resubmit')
            task = client.get_task(manifest['task_id'],correlation_id=SCOPE)
            manifest.update(status=task.status,actual_credits=task.cost_credits,failure_code=task.failure_code)
            deps=EvaluationDependencies(settings=settings,repository=repo,storage_factory=get_storage_client,
                runway_client_factory=lambda _:client,load_context=lambda _:None)
            claimed=repo.claim(worker_id=SCOPE,lease_seconds=120,limit=1,environment=poller_environment(settings),scope=SCOPE)
            for row in claimed:
                if row['id'] != manifest['evaluation_id']: raise ValueError('Unexpected canary lease')
                manifest['pipeline_outcome']=process_claimed_evaluation(row,client=client,deps=deps)
            row=db.table('runway_video_evaluations').select('id,status,error_code,actual_credits,output_sha256').eq('id',manifest['evaluation_id']).single().execute().data
            manifest['persisted_status']=row['status']; manifest['persisted_actual_credits']=row['actual_credits']
            if task.status=='SUCCEEDED' and not manifest.get('output_sha256'):
                if len(task.output_urls)!=1: raise ValueError('Unexpected provider output count')
                output=client.download_output(task.output_urls[0],correlation_id=SCOPE)
                manifest['probe']=probe_mp4(output.content,expected_duration_seconds=8,expected_ratio='480:854',expect_audio=True)
                media=folder/'seedance-sheet-8s-480p.mp4'; media.write_bytes(output.content)
                manifest.update(output_path=str(media),output_sha256=digest(output.content),output_host=output.host)
            if task.status in ('SUCCEEDED','FAILED','CANCELLED'):
                manifest['balance_after']=client.get_organization(correlation_id=SCOPE)['credit_balance']
                manifest['production_unchanged']=baseline(db)==manifest['baseline']
            save(file,manifest)
        return manifest


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=['prepare','submit','poll'],required=True)
    p.add_argument('--confirm-credits',type=int)
    p.add_argument('--output',type=Path,default=ROOT/'output/runway-ayra-sheet-h1-2026-10-03')
    args=p.parse_args()
    try:
        m=run(args)
        print(json.dumps({k:m.get(k) for k in ('model','status','evaluation_id','task_id','source_sha256','reference_count','legacy_image_inputs','estimated_credits','actual_credits','failure_code','persisted_status','balance_before','balance_after','production_unchanged','output_path')}))
        return 0
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'message':'Canary stopped. Inspect durable state; never resubmit entered work.'}))
        return 1

if __name__=='__main__': raise SystemExit(main())
