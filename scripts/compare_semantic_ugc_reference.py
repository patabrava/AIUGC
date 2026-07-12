"""Compare a semantic UGC candidate with the X reference and prior control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.shot_production.reference_comparison import (  # noqa: E402
    compare_edit_profiles,
    probe_edit_metrics,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare semantic UGC editorial cut density.")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare_edit_profiles(
        reference=probe_edit_metrics(args.reference),
        control=probe_edit_metrics(args.control),
        candidate=probe_edit_metrics(args.candidate),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
