"""Direct Seedance 2.5 validation through the app's durable evaluation boundary.

Probe is non-paid. Preview never submits. Submit requires exact preview units;
all actor checks, database admission and submission fences remain authoritative.
"""
from __future__ import annotations
import argparse
import json
from app.adapters.modelark_client import get_modelark_client
from app.adapters.video_provider_transport import VideoProviderError
from app.core.config import get_settings
from app.core.errors import FlowForgeException
from app.features.runway_evaluations.providers import ProviderSettings
from app.features.runway_evaluations.service import (
    RunwayEvaluationRepository, default_dependencies, preview_evaluation,
    submit_evaluation, poll_seedance_evaluations,
)
from app.features.videos.quota_guard import get_seedance_budget_snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('probe','preview','submit','poll'))
    parser.add_argument('--post-id')
    parser.add_argument('--operator-email')
    parser.add_argument('--take-index',type=int,default=0)
    parser.add_argument('--resolution',choices=('480p','720p'),default='480p')
    parser.add_argument('--confirm-units',type=int)
    args=parser.parse_args()
    settings=ProviderSettings(get_settings(),'modelark')
    try:
        if args.action=='probe':
            result=get_modelark_client(settings).probe_access()
        elif args.action=='poll':
            result=poll_seedance_evaluations(worker_id='modelark-validation-cli')
        else:
            if not args.post_id or not args.operator_email:
                parser.error('preview/submit require --post-id and --operator-email')
            deps=default_dependencies()
            deps.settings=settings
            deps.repository=RunwayEvaluationRepository(provider='modelark')
            deps.budget_snapshot=lambda limit:get_seedance_budget_snapshot(provider='modelark_seedance_2_5',daily_unit_limit=limit)
            kwargs=dict(post_id=args.post_id,take_index=args.take_index,resolution=args.resolution,
                        operator_email=args.operator_email,deps=deps)
            if args.action=='preview':
                value=preview_evaluation(**kwargs)
                result={key:value.get(key) for key in ('eligible','provider','provider_model','estimate','reasons')}
            else:
                if args.confirm_units is None:
                    parser.error('submit requires --confirm-units equal to the preview reservation')
                value=submit_evaluation(**kwargs,confirm_estimated_credits=args.confirm_units,
                                        correlation_id='modelark-validation-cli')
                result={key:value.get(key) for key in ('id','provider','provider_model','status','estimated_credits','estimated_usd')}
        print(json.dumps(result,default=str))
        return 0
    except (VideoProviderError,FlowForgeException) as exc:
        # Do not print provider response bodies, credentials, image URLs or prompts.
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,
                          'code':str(getattr(exc,'kind',getattr(exc,'code','blocked')))}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
