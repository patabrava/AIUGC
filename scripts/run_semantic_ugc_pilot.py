"""Run or resume the approved-frame Veo 3.1 semantic UGC pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.deepgram_client import get_deepgram_client  # noqa: E402
from app.adapters.vertex_ai_client import get_vertex_ai_client  # noqa: E402
from app.features.shot_production.runner import (  # noqa: E402
    build_contact_sheet,
    compose_and_caption,
    generate_raw_takes_in_waves,
    initialize_pilot,
    pilot_run_lock,
    reset_failed_take,
    reset_visual_failed_takes,
    revise_failed_beat,
    run_visual_qa,
    transcribe_and_validate_takes,
    upload_final,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one resumable, captioned semantic UGC pilot through Vertex Veo 3.1."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approved-frame", type=Path, required=True)
    parser.add_argument("--approved-sha", required=True)
    parser.add_argument("--script-input", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=240711)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-take", type=int, action="append", default=[])
    parser.add_argument("--retry-reason", default="manual failed-take retry")
    parser.add_argument(
        "--retry-guidance",
        help="Optional narrow delivery correction for explicitly retried takes.",
    )
    parser.add_argument("--revise-take", type=int)
    parser.add_argument("--replacement-beat")
    parser.add_argument("--revision-reason", default="audited failed-beat editorial revision")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--visual-model")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument(
        "--confirm-paid-plan",
        action="store_true",
        help="Explicitly approve submission of every still-pending Veo take in this manifest.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest_path = args.manifest.resolve()
    with pilot_run_lock(manifest_path):
        if manifest_path.exists():
            if not args.resume:
                raise SystemExit(f"Manifest already exists; rerun with --resume: {manifest_path}")
        else:
            initialize_pilot(
                manifest_path=manifest_path,
                approved_frame_path=args.approved_frame.resolve(),
                expected_sha256=args.approved_sha,
                script_input_path=args.script_input.resolve(),
                base_seed=args.base_seed,
            )

        if args.revise_take is not None:
            if not args.replacement_beat:
                raise SystemExit("--revise-take requires --replacement-beat")
            revise_failed_beat(
                manifest_path,
                index=args.revise_take,
                replacement_text=args.replacement_beat,
                reason=args.revision_reason,
            )

        retry_snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
        if len(args.retry_take) > 1 and (retry_snapshot.get("visual_qa") or {}).get("passed") is False:
            reset_visual_failed_takes(
                manifest_path,
                indexes=args.retry_take,
                reason=args.retry_reason,
                retry_guidance=args.retry_guidance,
            )
        else:
            for take_index in args.retry_take:
                reset_failed_take(
                    manifest_path,
                    index=take_index,
                    reason=args.retry_reason,
                    retry_guidance=args.retry_guidance,
                )

        planned = json.loads(manifest_path.read_text(encoding="utf-8"))
        pending = [take["index"] for take in planned["takes"] if not take.get("operation")]
        if pending and not args.confirm_paid_plan:
            raise SystemExit(
                "Paid Veo submission is paused for explicit approval. "
                f"Review {manifest_path}; pending take indexes are {pending}. "
                "Resume with --confirm-paid-plan when approved."
            )

        vertex = get_vertex_ai_client()
        deepgram = get_deepgram_client()
        generate_raw_takes_in_waves(
            manifest_path,
            vertex,
            max_inflight=2,
            poll_interval_seconds=args.poll_interval,
            timeout_seconds=args.timeout,
        )
        transcribe_and_validate_takes(manifest_path, deepgram)
        build_contact_sheet(manifest_path)
        run_visual_qa(manifest_path, model=args.visual_model)
        caption = compose_and_caption(manifest_path, deepgram)
        upload = upload_final(manifest_path) if args.upload else None
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "captioned_video": caption["captioned_path"],
                "public_url": (upload or {}).get("url"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
