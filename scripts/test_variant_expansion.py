#!/usr/bin/env python3
"""Live end-to-end test for variant expansion.

Usage:
    python scripts/test_variant_expansion.py              # generate 1 variant per path
    python scripts/test_variant_expansion.py --dry-run    # show what would be generated
    python scripts/test_variant_expansion.py --count 3    # generate 3 variants
"""

import argparse
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.features.topics.queries import (
    get_all_topics_from_registry,
    get_topic_scripts_for_registry,
)
from app.features.topics.variant_expansion import (
    expand_topic_variants,
    pick_next_variant,
    _get_hook_style_names,
    LIFESTYLE_FRAMEWORKS,
    LIFESTYLE_HOOK_STYLES,
    DEFAULT_MAX_SCRIPTS_PER_TOPIC,
)


def print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="Live variant expansion test")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--count", type=int, default=1, help="Variants per topic")
    parser.add_argument("--tier", type=int, default=8, help="Target length tier")
    parser.add_argument("--post-type", choices=["value", "lifestyle"], help="Filter by post type")
    args = parser.parse_args()

    print_header("VARIANT EXPANSION LIVE TEST")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE (will write to DB)'}")
    print(f"Count: {args.count} variants per topic")
    print(f"Tier: {args.tier}s")

    topics = get_all_topics_from_registry()
    if not topics:
        print("ERROR: No topics in registry. Run topic seeding first.")
        sys.exit(1)

    print(f"\nFound {len(topics)} topics in registry")

    if args.post_type:
        topics = [t for t in topics if t.get("post_type") == args.post_type]
        print(f"Filtered to {len(topics)} {args.post_type} topics")

    value_topic = next((t for t in topics if t.get("post_type") == "value"), None)
    lifestyle_topic = next((t for t in topics if t.get("post_type") == "lifestyle"), None)

    results = []

    for label, topic in [("VALUE", value_topic), ("LIFESTYLE", lifestyle_topic)]:
        if topic is None:
            print(f"\n--- Skipping {label}: no topics of this type ---")
            continue

        print_header(f"{label} PATH: {topic['title']}")

        existing_scripts = get_topic_scripts_for_registry(topic["id"])
        print(f"Existing scripts: {len(existing_scripts)}")
        for s in existing_scripts:
            print(f"  - [{s.get('framework', '?')}/{s.get('hook_style', '?')}] {str(s.get('script', ''))[:60]}...")

        result = expand_topic_variants(
            topic_registry_id=topic["id"],
            title=topic["title"],
            post_type=topic.get("post_type") or "value",
            target_length_tier=args.tier,
            count=args.count,
            dry_run=args.dry_run,
        )

        print(f"\nGenerated: {result['generated']}")
        print(f"Total now: {result['total_existing']}")
        for detail in result.get("details", []):
            print(f"\n  Framework: {detail['framework']}")
            print(f"  Hook:      {detail['hook_style']}")
            if "script" in detail:
                print(f"  Script:    {detail['script']}")
            if detail.get("dry_run"):
                print(f"  (dry run - not stored)")

        results.append({"label": label, "topic": topic["title"], **result})

    print_header("SUMMARY")
    print(f"{'Path':<12} {'Topic':<30} {'Generated':<10} {'Total':<10} {'Remaining'}")
    print("-" * 80)
    for r in results:
        remaining = DEFAULT_MAX_SCRIPTS_PER_TOPIC - r["total_existing"]
        print(f"{r['label']:<12} {r['topic'][:28]:<30} {r['generated']:<10} {r['total_existing']:<10} {remaining}")

    print(f"\nDone. {'(DRY RUN - no changes made)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
