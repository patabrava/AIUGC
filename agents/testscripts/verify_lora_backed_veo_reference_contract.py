from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.adapters.supabase_client import get_supabase


EXPECTED_SOURCE = "actor_identity_scene_reference_set"
EXPECTED_ROLES = ["scene_reference", "scene_reference", "scene_reference"]


def _die(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, indent=2))
    raise SystemExit(1)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _latest_audit_for_post(
    audit_rows: list[dict[str, Any]],
    operation_id: str | None,
) -> dict[str, Any]:
    if operation_id:
        for row in reversed(audit_rows):
            if row.get("operation_id") == operation_id:
                return row
    return audit_rows[-1] if audit_rows else {}


def _submitted_post_result(
    post: dict[str, Any],
    audit_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = _as_dict(post.get("video_metadata"))
    provider_metadata = _as_dict(metadata.get("provider_metadata"))
    operation_id = post.get("video_operation_id")
    latest_audit = _latest_audit_for_post(audit_rows, operation_id)
    audit_reference = _as_dict(latest_audit.get("reference_image_metadata"))
    scene_reference_image_ids = provider_metadata.get("scene_reference_image_ids")
    reference_image_roles = provider_metadata.get("reference_image_roles")
    audit_scene_reference_image_ids = audit_reference.get("scene_reference_image_ids")
    audit_reference_image_roles = audit_reference.get("reference_image_roles")

    checks = {
        "provider_source": provider_metadata.get("source") == EXPECTED_SOURCE,
        "scene_reference_images_used_for_video": (
            provider_metadata.get("scene_reference_images_used_for_video") is True
        ),
        "reference_image_roles": reference_image_roles == EXPECTED_ROLES,
        "scene_reference_image_ids": (
            isinstance(scene_reference_image_ids, list)
            and len(scene_reference_image_ids) == 3
            and all(scene_reference_image_ids)
        ),
        "audit_source": audit_reference.get("source") == EXPECTED_SOURCE,
        "audit_scene_reference_images_used_for_video": (
            audit_reference.get("scene_reference_images_used_for_video") is True
        ),
        "audit_reference_image_roles": audit_reference_image_roles == EXPECTED_ROLES,
        "audit_scene_reference_image_ids": audit_scene_reference_image_ids == scene_reference_image_ids,
        "audit_reference_image_count": audit_reference.get("reference_image_count") == 3,
    }
    post_ok = all(checks.values())
    return {
        "post_id": post.get("id"),
        "video_status": post.get("video_status"),
        "operation_id": operation_id,
        "provider_source": provider_metadata.get("source"),
        "scene_reference_images_used_for_video": provider_metadata.get(
            "scene_reference_images_used_for_video"
        ),
        "reference_image_roles": reference_image_roles,
        "scene_reference_image_ids": scene_reference_image_ids,
        "audit_operation_id": latest_audit.get("operation_id"),
        "audit_source": audit_reference.get("source"),
        "audit_scene_reference_images_used_for_video": audit_reference.get(
            "scene_reference_images_used_for_video"
        ),
        "audit_reference_image_roles": audit_reference_image_roles,
        "audit_scene_reference_image_ids": audit_scene_reference_image_ids,
        "audit_reference_image_count": audit_reference.get("reference_image_count"),
        "checks": checks,
        "ok": post_ok,
    }


def main() -> int:
    if len(sys.argv) != 2:
        _die("Usage: python agents/testscripts/verify_lora_backed_veo_reference_contract.py <batch_id>")
    batch_id = sys.argv[1].strip()
    if not batch_id:
        _die("batch_id is required")

    sb = get_supabase().client
    posts = (
        sb.table("posts")
        .select("id,video_status,video_operation_id,video_metadata")
        .eq("batch_id", batch_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    if not posts:
        _die(f"No posts found for batch {batch_id}")

    post_ids = [str(post["id"]) for post in posts]
    audit_warning = None
    try:
        audits = (
            sb.table("video_prompt_audit")
            .select("post_id,operation_id,provider,prompt_path,reference_image_metadata,created_at")
            .in_("post_id", post_ids)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        if "reference_image_metadata" not in str(exc):
            raise
        audit_warning = "video_prompt_audit.reference_image_metadata column is missing"
        audits = (
            sb.table("video_prompt_audit")
            .select("post_id,operation_id,provider,prompt_path,created_at")
            .in_("post_id", post_ids)
            .order("created_at")
            .execute()
            .data
            or []
        )
    audits_by_post: dict[str, list[dict[str, Any]]] = {}
    for row in audits:
        audits_by_post.setdefault(str(row.get("post_id")), []).append(row)

    rows: list[dict[str, Any]] = []
    submitted_ok = True
    submitted_count = 0
    for post in posts:
        operation_id = post.get("video_operation_id")
        if not operation_id:
            rows.append(
                {
                    "post_id": post.get("id"),
                    "video_status": post.get("video_status"),
                    "operation_id": None,
                    "ok": None,
                    "reason": "not submitted",
                }
            )
            continue
        submitted_count += 1
        result = _submitted_post_result(post, audits_by_post.get(str(post.get("id")), []))
        submitted_ok = submitted_ok and result["ok"]
        rows.append(result)

    output = {
        "ok": submitted_count > 0 and submitted_ok,
        "batch_id": batch_id,
        "submitted_post_count": submitted_count,
        "posts": rows,
    }
    if audit_warning:
        output["warning"] = audit_warning
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
