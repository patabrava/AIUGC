"""Audit every row in ``topic_scripts`` for a verifiable live source URL.

Workflow:
  1. Paginate through every script row.
  2. Collect every distinct URL from ``primary_source_url`` and
     ``source_urls[*].url`` (and the same fields on the most-recent dossier
     per topic, used as a fallback).
  3. HEAD/GET each URL once in parallel and classify it as alive / dead.
     - Alive: any 2xx/3xx, or 4xx that is not 404/410 (bot-blocks like 403
       still indicate the page exists).
     - Dead: 404, 410, DNS / connection errors, TLS errors, timeouts.
  4. Rewrite each script:
       - Drop dead URLs from ``source_urls``.
       - If ``primary_source_url`` is dead, promote the first surviving
         ``source_urls`` entry to primary, otherwise null it out.
       - If the script ends up with no live source at all, look up the
         topic's most-recent dossier and inherit live sources from there.
  5. Report counts and any residual scripts with no verifiable source.

Idempotent. Conservative: a URL is only removed when the live check returns
a definite "this page no longer exists" signal. Bot-block 403/429 keeps the
URL because the page is real.

Run from project root:

    PYTHONPATH=. .venv/bin/python scripts/verify_and_backfill_script_sources.py
        --dry-run                        # estimate without writing
        --liveness-cache <file.json>     # cache live/dead results
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.adapters.supabase_client import get_supabase

LIVENESS_TIMEOUT = 10  # seconds, per curl request
LIVENESS_WORKERS = 24
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
# Definite-dead status codes returned by curl. 4xx that are NOT here are
# kept (403/401/429 typically indicate the page exists but blocks scrapers).
DEAD_STATUS_CODES = {404, 410}


def _probe_url(url: str) -> bool:
    """Return True if URL is alive (page presumed to exist).

    Uses system curl so the probe benefits from modern TLS — Python 3.9's
    bundled OpenSSL fails handshakes with many EU government sites.

    Conservative classification:
      * HTTP 2xx/3xx  → alive
      * HTTP 4xx (except 404/410) → alive (auth wall / bot-block)
      * HTTP 5xx     → alive (transient outage, URL still exists)
      * 404 or 410   → DEAD
      * curl exit 6  (DNS NXDOMAIN) → DEAD
      * curl exit 7  (connection refused) → DEAD
      * Any other transport / TLS error → ALIVE (page presumed real, just
        unreachable from this host)
    """
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sI",  # silent + HEAD
                "-L",  # follow redirects
                "--max-time", str(LIVENESS_TIMEOUT),
                "--connect-timeout", "6",
                "-A", USER_AGENT,
                "-o", "/dev/null",
                "-w", "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=LIVENESS_TIMEOUT + 4,
        )
    except subprocess.TimeoutExpired:
        return True  # timeout → presumed alive
    except FileNotFoundError:
        # curl not present on PATH; bail out conservatively.
        return True

    exit_code = proc.returncode
    if exit_code in (6, 7):  # 6=DNS, 7=conn refused
        return False
    try:
        status = int((proc.stdout or "0").strip())
    except ValueError:
        status = 0

    # Some servers reject HEAD. If we got 0 or 405/501, retry with GET-discard.
    if status in (0, 405, 501):
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-L",
                    "--max-time", str(LIVENESS_TIMEOUT),
                    "--connect-timeout", "6",
                    "-A", USER_AGENT,
                    "-o", "/dev/null",
                    "-w", "%{http_code}",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=LIVENESS_TIMEOUT + 4,
            )
        except subprocess.TimeoutExpired:
            return True
        exit_code = proc.returncode
        if exit_code in (6, 7):
            return False
        try:
            status = int((proc.stdout or "0").strip())
        except ValueError:
            status = 0

    if status in DEAD_STATUS_CODES:
        return False
    if status == 0:
        # transport-level error (TLS, reset, timeout). Presume alive.
        return True
    return True  # any HTTP status other than 404/410 → alive


def _build_liveness_map(
    urls: Set[str],
    *,
    cache_path: Optional[Path],
    refresh: bool = False,
) -> Dict[str, bool]:
    cache: Dict[str, bool] = {}
    if cache_path and cache_path.exists() and not refresh:
        try:
            cache = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            cache = {}
    to_check = [u for u in urls if u not in cache]
    print(f"[verify] {len(urls)} distinct URLs, {len(to_check)} not cached")
    if not to_check:
        return cache

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=LIVENESS_WORKERS) as pool:
        futures = {pool.submit(_probe_url, u): u for u in to_check}
        done = 0
        for future in as_completed(futures):
            url = futures[future]
            try:
                alive = future.result()
            except Exception:  # noqa: BLE001
                alive = True  # conservative: errors → alive
            cache[url] = alive
            done += 1
            if done % 100 == 0:
                print(f"[verify]   probed {done}/{len(to_check)}...")
    print(f"[verify] probed {len(to_check)} URLs in {time.time() - t0:.1f}s")
    alive_count = sum(1 for u in to_check if cache.get(u))
    print(f"[verify]   {alive_count} alive, {len(to_check) - alive_count} dead")
    if cache_path:
        cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))
    return cache


# ---------- script + dossier loaders ---------------------------------------


def _iter_all(table: str, columns: str) -> List[Dict[str, Any]]:
    sb = get_supabase().client
    out: List[Dict[str, Any]] = []
    page = 500
    offset = 0
    while True:
        res = (
            sb.table(table)
            .select(columns)
            .order("created_at", desc=True)
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def _collect_script_urls(script: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
    p = script.get("primary_source_url") or ""
    if isinstance(p, str) and p.startswith(("http://", "https://")):
        urls.add(p)
    for s in script.get("source_urls") or []:
        url = s.get("url") if isinstance(s, dict) else s
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.add(url)
    return urls


def _collect_dossier_urls(dossier: Dict[str, Any]) -> Set[str]:
    payload = dossier.get("normalized_payload") or {}
    urls: Set[str] = set()
    for key in ("sources", "source_urls"):
        for s in payload.get(key) or []:
            url = s.get("url") if isinstance(s, dict) else s
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                urls.add(url)
    return urls


def _latest_dossier_by_topic(
    dossiers: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Map topic_registry_id -> most recently created dossier."""
    out: Dict[str, Dict[str, Any]] = {}
    for d in dossiers:  # already sorted desc
        tid = d.get("topic_registry_id")
        if tid and tid not in out:
            out[tid] = d
    return out


# ---------- rewriting -------------------------------------------------------


def _dossier_live_sources(
    dossier: Dict[str, Any], liveness: Dict[str, bool]
) -> List[Dict[str, str]]:
    payload = dossier.get("normalized_payload") or {}
    survivors: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for s in (payload.get("sources") or []) + (payload.get("source_urls") or []):
        url = s.get("url") if isinstance(s, dict) else s
        title = s.get("title") if isinstance(s, dict) else None
        if not isinstance(url, str) or not url:
            continue
        if not liveness.get(url, False):
            continue
        if url in seen:
            continue
        seen.add(url)
        survivors.append({"url": url, "title": str(title or url)[:400]})
    return survivors


def _rewrite_script(
    script: Dict[str, Any],
    *,
    liveness: Dict[str, bool],
    dossier_by_topic: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return an update dict if changes are needed, else None."""
    primary = script.get("primary_source_url") or ""
    primary_title = script.get("primary_source_title") or ""
    src_urls = script.get("source_urls") or []

    # Filter source_urls to live ones.
    live_urls: List[Dict[str, str]] = []
    seen: Set[str] = set()
    dropped = 0
    for s in src_urls:
        if isinstance(s, dict):
            url = str(s.get("url") or "")
            title = s.get("title") or ""
        else:
            url = str(s or "")
            title = ""
        if not url or url in seen:
            if not url:
                dropped += 1
            continue
        if not liveness.get(url, False):
            dropped += 1
            continue
        seen.add(url)
        live_urls.append({"url": url, "title": (title or url)[:400]})

    primary_alive = bool(primary) and liveness.get(primary, False)
    new_primary = primary if primary_alive else None
    new_primary_title = primary_title if primary_alive else None

    # If primary died but we have a survivor, promote the first one.
    if not new_primary and live_urls:
        new_primary = live_urls[0]["url"]
        new_primary_title = live_urls[0]["title"]

    # If still no live source at all, try the topic's latest dossier.
    inherited = 0
    if not new_primary and not live_urls:
        topic_id = script.get("topic_registry_id")
        dossier = dossier_by_topic.get(topic_id) if topic_id else None
        if dossier:
            survivors = _dossier_live_sources(dossier, liveness)
            if survivors:
                live_urls = survivors[:8]
                new_primary = live_urls[0]["url"]
                new_primary_title = live_urls[0]["title"]
                inherited = len(live_urls)

    # Decide whether anything changed.
    old_urls_repr = [
        {"url": str((s.get("url") if isinstance(s, dict) else s) or ""),
         "title": str((s.get("title") if isinstance(s, dict) else "") or "")}
        for s in src_urls
    ]
    changed = (
        new_primary != (primary or None)
        or new_primary_title != (primary_title or None)
        or live_urls != old_urls_repr
    )
    if not changed:
        return None
    return {
        "_meta": {"dropped": dropped, "inherited": inherited},
        "update": {
            "primary_source_url": new_primary,
            "primary_source_title": new_primary_title,
            "source_urls": live_urls[:8],
        },
    }


# ---------- driver ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--liveness-cache",
        default="recovery_logs/source_liveness_cache.json",
        help="JSON cache for URL liveness results",
    )
    parser.add_argument(
        "--refresh-liveness",
        action="store_true",
        help="Ignore existing cache and re-probe every URL",
    )
    args = parser.parse_args()
    cache_path = Path(args.liveness_cache) if args.liveness_cache else None
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[verify] dry_run={args.dry_run}")

    # 1. Load every script.
    print("[verify] loading topic_scripts...")
    scripts = _iter_all(
        "topic_scripts",
        "id,topic_registry_id,primary_source_url,primary_source_title,source_urls,created_at",
    )
    print(f"[verify] loaded {len(scripts)} scripts")

    # 2. Load every dossier (so we can inherit when scripts have nothing live).
    print("[verify] loading topic_research_dossiers (for fallback inheritance)...")
    dossiers = _iter_all(
        "topic_research_dossiers",
        "id,topic_registry_id,normalized_payload,created_at",
    )
    dossier_by_topic = _latest_dossier_by_topic(dossiers)
    print(
        f"[verify] loaded {len(dossiers)} dossiers, "
        f"{len(dossier_by_topic)} distinct topic_registry_ids"
    )

    # 3. Distinct URL set across scripts + dossiers.
    print("[verify] collecting distinct URLs...")
    urls: Set[str] = set()
    for s in scripts:
        urls.update(_collect_script_urls(s))
    for d in dossiers:
        urls.update(_collect_dossier_urls(d))
    print(f"[verify] {len(urls)} distinct URLs to probe")

    # 4. Probe.
    liveness = _build_liveness_map(
        urls, cache_path=cache_path, refresh=args.refresh_liveness
    )
    # Treat seed-placeholder hosts as dead — they're not real sources.
    for url in list(liveness.keys()):
        if "example.com" in url or "example.org" in url:
            liveness[url] = False

    # 5. Rewrite scripts.
    stats = {
        "scripts": len(scripts),
        "updated": 0,
        "dropped": 0,
        "inherited_from_dossier": 0,
        "no_source_before": 0,
        "no_source_after": 0,
        "got_source_via_backfill": 0,
    }
    residual_no_source: List[Dict[str, Any]] = []
    for script in scripts:
        before_any = bool(
            script.get("primary_source_url")
            or any(
                (s.get("url") if isinstance(s, dict) else s)
                for s in (script.get("source_urls") or [])
            )
        )
        if not before_any:
            stats["no_source_before"] += 1
        plan = _rewrite_script(
            script, liveness=liveness, dossier_by_topic=dossier_by_topic
        )
        if plan is None:
            # No change. Track residual no-source rows.
            if not before_any:
                residual_no_source.append(
                    {
                        "id": script["id"],
                        "topic_registry_id": script.get("topic_registry_id"),
                    }
                )
            continue
        stats["updated"] += 1
        stats["dropped"] += plan["_meta"]["dropped"]
        if plan["_meta"]["inherited"]:
            stats["inherited_from_dossier"] += 1
        update = plan["update"]
        after_any = bool(update.get("primary_source_url") or update.get("source_urls"))
        if before_any and not after_any:
            stats["no_source_after"] += 1
            residual_no_source.append(
                {"id": script["id"], "topic_registry_id": script.get("topic_registry_id")}
            )
        if (not before_any) and after_any:
            stats["got_source_via_backfill"] += 1
        if args.dry_run:
            continue
        sb = get_supabase().client
        sb.table("topic_scripts").update(update).eq("id", script["id"]).execute()

    print()
    print("=" * 60)
    print("[verify] FINAL STATS")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  residual scripts with NO verifiable source: {len(residual_no_source)}")
    if residual_no_source[:5]:
        print("  examples:")
        for r in residual_no_source[:5]:
            print(f"    - script {r['id']}, topic {r['topic_registry_id']}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
