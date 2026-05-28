from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.magnific_client import get_magnific_client
from app.core.errors import FlowForgeException
from app.features.characters.scene_reference import SCENE_BIBLES


def _provider_error_text(exc: Exception) -> str:
    if isinstance(exc, FlowForgeException):
        details = {
            key: value
            for key, value in exc.details.items()
            if key in {"provider", "path", "status_code", "body", "correlation_id", "error"}
        }
        return f"{type(exc).__name__}: {exc.message}; details={json.dumps(details, sort_keys=True)}"
    return f"{type(exc).__name__}: {exc}"


def _extract_prompt_text(task: dict[str, Any]) -> str:
    generated = task.get("generated")
    if isinstance(generated, list):
        return "\n".join(str(item) for item in generated if str(item).strip())
    if isinstance(generated, str):
        return generated
    result = task.get("result")
    if isinstance(result, list):
        return "\n".join(str(item) for item in result if str(item).strip())
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return json.dumps(result, sort_keys=True)
    return ""


def _status_text(task: dict[str, Any]) -> str:
    for key in ("status", "state", "phase"):
        if task.get(key):
            return str(task[key])
    return "unknown"


def _keywords_for_scene(scene_id: str) -> list[str]:
    if scene_id == "bathroom_accessibility_a":
        return ["grab rail", "sink", "window", "towel", "bathroom", "wheelchair"]
    if scene_id == "car_transfer_residential_a":
        return ["silver", "car", "door", "brick", "hedge", "wheelchair"]
    if scene_id == "home_living_room_advice_a":
        return ["table", "mug", "plant", "curtain", "living room", "wheelchair"]
    return ["wheelchair"]


def _score_text(scene_id: str, text: str) -> dict[str, Any]:
    lowered = text.lower()
    keywords = _keywords_for_scene(scene_id)
    present = [keyword for keyword in keywords if keyword in lowered]
    missing = [keyword for keyword in keywords if keyword not in lowered]
    return {
        "required_keywords": keywords,
        "present_keywords": present,
        "missing_keywords": missing,
        "score": len(present),
        "max_score": len(keywords),
        "passes_soft_gate": len(missing) <= 1,
    }


def _iter_manifest_images(manifest_path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    for item in manifest.get("items") or []:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("image_url") or "").strip()
        if image_url:
            rows.append(item)
    return rows


def _write_report(path: Path, manifest_path: Path, reports: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"manifest": str(manifest_path), "items": reports}, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run paid Magnific image-to-prompt anchor analysis for a scene sweep manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--poll-attempts", type=int, default=36)
    parser.add_argument("--poll-sleep", type=int, default=5)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _iter_manifest_images(manifest_path)
    client = get_magnific_client()
    reports = []

    for index, row in enumerate(rows, start=1):
        scene_id = str(row.get("scene_id") or "")
        if scene_id not in SCENE_BIBLES:
            continue
        image_url = str(row.get("image_url") or "")
        task_id = ""
        task: dict[str, Any] = {}
        error = None
        try:
            created = client.create_image_to_prompt_task(
                image=image_url,
                correlation_id=f"scene-anchor-itp-create-{index}",
            )
            task_id = str(created.get("task_id") or "")
            task = created
            if not task_id:
                raise RuntimeError("image-to-prompt task did not return task_id")
            for attempt in range(1, args.poll_attempts + 1):
                if _extract_prompt_text(task):
                    break
                task = client.get_image_to_prompt_task(
                    task_id=task_id,
                    correlation_id=f"scene-anchor-itp-poll-{task_id}-{attempt}",
                )
                if _extract_prompt_text(task):
                    break
                if _status_text(task).lower() in {"failed", "error", "cancelled", "canceled"}:
                    break
                time.sleep(args.poll_sleep)
        except Exception as exc:  # noqa: BLE001 - paid evidence script must preserve partial results
            error = _provider_error_text(exc)
        description = _extract_prompt_text(task)
        score = _score_text(scene_id, description)
        reports.append(
            {
                "scene_id": scene_id,
                "profile": row.get("profile"),
                "set_index": row.get("set_index"),
                "angle_key": row.get("angle_key"),
                "source_task_id": row.get("task_id"),
                "image_to_prompt_task_id": task_id,
                "image_to_prompt_status": _status_text(task),
                "description": description,
                "anchor_score": score,
                "error": error,
            }
        )
        _write_report(out_path, manifest_path, reports)
        print(
            json.dumps(
                {
                    "analyzed": len(reports),
                    "scene_id": scene_id,
                    "angle_key": row.get("angle_key"),
                    "score": score["score"],
                    "max_score": score["max_score"],
                    "error": error,
                }
            )
        )

    _write_report(out_path, manifest_path, reports)
    failing = [item for item in reports if not item["anchor_score"]["passes_soft_gate"]]
    errors = [item for item in reports if item.get("error")]
    print(json.dumps({"report": str(out_path), "analyzed": len(reports), "soft_gate_failures": len(failing), "errors": len(errors)}))
    return 2 if failing or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
