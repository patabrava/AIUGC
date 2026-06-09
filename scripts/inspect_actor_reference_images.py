"""Inspect the reference images an ActorIdentity would contribute to a VEO submission.

Read-only. Shows, for one or all ActorIdentities, the stored LoRA picture URLs
(portrait/cover/training_images) and the exact actor anchor URLs the video submission path
resolves via `_actor_identity_anchor_urls`. Use it to confirm an actor exposes >=2 LoRA pictures
so a Character Consistency video attaches 2 actor anchors + 1 canonical scene image instead of
falling back to text-to-video.

Usage (from AIUGC/, with the venv active so .env credentials load):

    python scripts/inspect_actor_reference_images.py                 # all actors
    python scripts/inspect_actor_reference_images.py --actor <id>    # one actor by id
    python scripts/inspect_actor_reference_images.py --name AYRA      # match by name (substring)
    python scripts/inspect_actor_reference_images.py --json           # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.characters import queries as character_queries  # noqa: E402
from app.features.characters.actor_identity import actor_identity_training_ready  # noqa: E402
from app.features.videos.handlers import _actor_identity_anchor_urls  # noqa: E402


def _inspect(identity) -> dict:
    anchor_urls = _actor_identity_anchor_urls(identity)
    actor_anchor_urls = anchor_urls[:2]
    return {
        "actor_identity_id": identity.id,
        "name": identity.name,
        "is_active": identity.is_active,
        "training_status": identity.training_status,
        "training_ready": actor_identity_training_ready(identity),
        "provider_lora_id": identity.provider_lora_id,
        "provider_lora_name": identity.provider_lora_name,
        "portrait_image_url": identity.portrait_image_url,
        "cover_image_url": identity.cover_image_url,
        "training_images_count": len(identity.training_images),
        "training_images": list(identity.training_images),
        "resolved_anchor_urls": anchor_urls,
        "actor_anchor_urls_used": actor_anchor_urls,
        "attaches_two_actor_anchors": len(actor_anchor_urls) >= 2,
    }


def _print_human(report: dict) -> None:
    ok = "OK " if report["attaches_two_actor_anchors"] else "!! "
    print(f"{ok}{report['name']}  ({report['actor_identity_id']})")
    print(f"     active={report['is_active']}  status={report['training_status']}  ready={report['training_ready']}")
    print(f"     lora_id={report['provider_lora_id']}  lora_name={report['provider_lora_name']}")
    print(f"     portrait={report['portrait_image_url']}")
    print(f"     cover   ={report['cover_image_url']}")
    print(f"     training_images ({report['training_images_count']}):")
    for idx, url in enumerate(report["training_images"]):
        print(f"        [{idx}] {url}")
    print("     -> actor anchors the video submission would attach (need 2):")
    if report["actor_anchor_urls_used"]:
        for idx, url in enumerate(report["actor_anchor_urls_used"]):
            print(f"        actor_identity_anchor[{idx}] {url}")
    else:
        print("        (none)")
    if not report["attaches_two_actor_anchors"]:
        print("     !! Fewer than 2 distinct picture URLs -> submission would raise 422 (needs backfill).")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect ActorIdentity reference images.")
    parser.add_argument("--actor", help="Inspect a single ActorIdentity by id.")
    parser.add_argument("--name", help="Filter actors whose name contains this substring (case-insensitive).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    if args.actor:
        identity = character_queries.get_actor_identity_by_id(args.actor)
        identities = [identity] if identity else []
    else:
        identities = character_queries.list_actor_identities()
        if args.name:
            needle = args.name.lower()
            identities = [i for i in identities if needle in i.name.lower()]

    if not identities:
        print("No matching ActorIdentity found.", file=sys.stderr)
        return 1

    reports = [_inspect(identity) for identity in identities]

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            _print_human(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
