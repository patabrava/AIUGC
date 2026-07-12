"""Compare a semantic UGC candidate with the X reference and prior control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.shot_production.reference_comparison import (  # noqa: E402
    build_artifact_bound_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce an artifact-bound semantic UGC reference comparison proof."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = build_artifact_bound_report(
        reference_path=args.reference,
        control_path=args.control,
        candidate_path=args.candidate,
        candidate_manifest_path=args.candidate_manifest,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["proof_gate"]["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
