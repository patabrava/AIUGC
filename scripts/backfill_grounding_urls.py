"""One-off backfill: replace dead vertexaisearch redirect URLs in legacy
``topic_research_dossiers`` rows and ``topic_scripts`` rows with their resolved
real destinations. Drop the ones that no longer resolve (expired tokens, 404s)
so the UI stops rendering broken links.

Run from the project root with the venv:

    .venv/bin/python scripts/backfill_grounding_urls.py
        # or: .venv/bin/python scripts/backfill_grounding_urls.py --dry-run

Idempotent — re-running after a successful backfill is a no-op.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import httpx

from app.adapters.grounding_url_resolver import (
    is_grounding_redirect_url,
    resolve_grounding_urls,
)
from app.adapters.supabase_client import get_supabase

PAGE_TIMEOUT_SECONDS = 8.0
PAGE_WORKERS = 8
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------- helpers ---------------------------------------------------------


def _host_label(url: str) -> str:
    """Fallback label derived from the URL host."""
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return url
    return host.removeprefix("www.") or url


def _fetch_title(url: str, client: httpx.Client) -> Optional[str]:
    """Best-effort fetch of the page <title>. Returns None on any failure."""
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    body = response.text[:200_000]  # cap on body parsing
    match = TITLE_RE.search(body)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title[:400] or None


def _build_title_map(final_urls: Iterable[str]) -> Dict[str, str]:
    """Return ``{final_url: page_title_or_host_label}``."""
    unique = sorted({u for u in final_urls if u})
    if not unique:
        return {}
    out: Dict[str, str] = {}
    with httpx.Client(
        follow_redirects=True,
        timeout=PAGE_TIMEOUT_SECONDS,
        headers={"User-Agent": "LippeLift-BackfillResolver/1.0"},
    ) as client:
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
            futures = {pool.submit(_fetch_title, u, client): u for u in unique}
            for future in as_completed(futures):
                url = futures[future]
                title = future.result() or _host_label(url)
                out[url] = title
    return out


# ---------- collection ------------------------------------------------------


def _collect_dossier_urls(dossier_row: Dict[str, Any]) -> Set[str]:
    payload = dossier_row.get("normalized_payload") or {}
    urls: Set[str] = set()
    for key in ("sources", "source_urls"):
        for item in payload.get(key) or []:
            url = item.get("url") if isinstance(item, dict) else item
            if isinstance(url, str) and is_grounding_redirect_url(url):
                urls.add(url)
    return urls


def _collect_script_urls(script_row: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
    primary = script_row.get("primary_source_url") or ""
    if is_grounding_redirect_url(primary):
        urls.add(primary)
    for item in script_row.get("source_urls") or []:
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str) and is_grounding_redirect_url(url):
            urls.add(url)
    return urls


def _collect_post_urls(post_row: Dict[str, Any]) -> Set[str]:
    urls: Set[str] = set()
    sd = post_row.get("seed_data") or {}
    src = sd.get("source")
    if isinstance(src, dict):
        url = src.get("url")
        if isinstance(url, str) and is_grounding_redirect_url(url):
            urls.add(url)
    for link in sd.get("caption_source_links") or []:
        url = link.get("url") if isinstance(link, dict) else link
        if isinstance(url, str) and is_grounding_redirect_url(url):
            urls.add(url)
    bc = post_row.get("blog_content") or {}
    if isinstance(bc, dict):
        for s in bc.get("sources") or []:
            url = s.get("url") if isinstance(s, dict) else s
            if isinstance(url, str) and is_grounding_redirect_url(url):
                urls.add(url)
    return urls


# ---------- rewriting -------------------------------------------------------


def _rewrite_sources_list(
    items: List[Any],
    *,
    resolved: Dict[str, str],
    title_for: Dict[str, str],
) -> Tuple[List[Dict[str, str]], int, int]:
    """Return (new_list, dropped_count, replaced_count). Drops vertexaisearch
    entries that did not resolve; replaces resolved ones with the final URL
    and a sensible title."""
    new: List[Dict[str, str]] = []
    dropped = 0
    replaced = 0
    seen_urls: Set[str] = set()
    for item in items:
        if isinstance(item, dict):
            url = str(item.get("url") or "")
            title = item.get("title")
        else:
            url = str(item or "")
            title = None
        if not url:
            continue
        if is_grounding_redirect_url(url):
            final = resolved.get(url)
            if not final:
                dropped += 1
                continue
            url = final
            title = title_for.get(final) or _host_label(final)
            replaced += 1
        if url in seen_urls:
            continue
        seen_urls.add(url)
        new.append({"title": str(title or _host_label(url))[:400], "url": url})
    return new, dropped, replaced


# ---------- main passes -----------------------------------------------------


def backfill_dossiers(
    *,
    dry_run: bool,
    resolved: Dict[str, str],
    title_for: Dict[str, str],
) -> Dict[str, int]:
    client = get_supabase().client
    page_size = 500
    offset = 0
    stats = {"scanned": 0, "updated": 0, "dropped_urls": 0, "replaced_urls": 0}
    while True:
        res = (
            client.table("topic_research_dossiers")
            .select("id,normalized_payload")
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for row in rows:
            stats["scanned"] += 1
            payload = row.get("normalized_payload") or {}
            sources = payload.get("sources") or []
            source_urls = payload.get("source_urls") or []
            has_redirect = any(
                is_grounding_redirect_url(
                    (s.get("url") if isinstance(s, dict) else s) or ""
                )
                for s in (sources + source_urls)
            )
            if not has_redirect:
                continue
            new_sources, d1, r1 = _rewrite_sources_list(
                sources, resolved=resolved, title_for=title_for
            )
            new_source_urls, d2, r2 = _rewrite_sources_list(
                source_urls, resolved=resolved, title_for=title_for
            )
            stats["dropped_urls"] += d1 + d2
            stats["replaced_urls"] += r1 + r2
            new_payload = dict(payload)
            new_payload["sources"] = new_sources[:8]
            new_payload["source_urls"] = new_source_urls[:8]
            if dry_run:
                stats["updated"] += 1
                continue
            client.table("topic_research_dossiers").update(
                {"normalized_payload": new_payload}
            ).eq("id", row["id"]).execute()
            stats["updated"] += 1
        if len(rows) < page_size:
            break
        offset += page_size
    return stats


def backfill_scripts(
    *,
    dry_run: bool,
    resolved: Dict[str, str],
    title_for: Dict[str, str],
) -> Dict[str, int]:
    client = get_supabase().client
    page_size = 500
    offset = 0
    stats = {"scanned": 0, "updated": 0, "dropped_urls": 0, "replaced_urls": 0}
    while True:
        res = (
            client.table("topic_scripts")
            .select("id,primary_source_url,primary_source_title,source_urls")
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for row in rows:
            stats["scanned"] += 1
            primary = row.get("primary_source_url") or ""
            src_urls = row.get("source_urls") or []
            has_redirect = is_grounding_redirect_url(primary) or any(
                is_grounding_redirect_url(
                    (s.get("url") if isinstance(s, dict) else s) or ""
                )
                for s in src_urls
            )
            if not has_redirect:
                continue
            update: Dict[str, Any] = {}
            if is_grounding_redirect_url(primary):
                final = resolved.get(primary)
                if final:
                    update["primary_source_url"] = final
                    update["primary_source_title"] = (
                        title_for.get(final) or _host_label(final)
                    )[:400]
                    stats["replaced_urls"] += 1
                else:
                    update["primary_source_url"] = None
                    update["primary_source_title"] = None
                    stats["dropped_urls"] += 1
            new_src_urls, dropped, replaced = _rewrite_sources_list(
                src_urls, resolved=resolved, title_for=title_for
            )
            stats["dropped_urls"] += dropped
            stats["replaced_urls"] += replaced
            update["source_urls"] = new_src_urls[:8]
            # If primary was wiped and there's a survivor, promote the first
            # surviving source to primary so the row still has SOMETHING.
            if (
                update.get("primary_source_url") is None
                and new_src_urls
            ):
                first = new_src_urls[0]
                update["primary_source_url"] = first["url"]
                update["primary_source_title"] = first["title"]
            if dry_run:
                stats["updated"] += 1
                continue
            client.table("topic_scripts").update(update).eq(
                "id", row["id"]
            ).execute()
            stats["updated"] += 1
        if len(rows) < page_size:
            break
        offset += page_size
    return stats


def backfill_posts(
    *,
    dry_run: bool,
    resolved: Dict[str, str],
    title_for: Dict[str, str],
) -> Dict[str, int]:
    client = get_supabase().client
    page_size = 500
    offset = 0
    stats = {"scanned": 0, "updated": 0, "dropped_urls": 0, "replaced_urls": 0}
    while True:
        res = (
            client.table("posts")
            .select("id,seed_data,blog_content")
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for row in rows:
            stats["scanned"] += 1
            sd = dict(row.get("seed_data") or {})
            bc = row.get("blog_content")
            bc_changed = False
            sd_changed = False

            # seed_data.source — singular dict
            src = sd.get("source")
            if isinstance(src, dict) and is_grounding_redirect_url(
                str(src.get("url") or "")
            ):
                final = resolved.get(str(src["url"]))
                if final:
                    sd["source"] = {
                        "title": (title_for.get(final) or _host_label(final))[:400],
                        "url": final,
                    }
                    stats["replaced_urls"] += 1
                else:
                    sd["source"] = None
                    stats["dropped_urls"] += 1
                sd_changed = True

            # seed_data.caption_source_links — list of {label,url}
            csl = sd.get("caption_source_links") or []
            new_csl: List[Dict[str, str]] = []
            csl_modified = False
            for link in csl:
                if isinstance(link, dict):
                    url = str(link.get("url") or "")
                else:
                    url = str(link or "")
                if not url:
                    csl_modified = True
                    continue
                if is_grounding_redirect_url(url):
                    final = resolved.get(url)
                    if not final:
                        csl_modified = True
                        stats["dropped_urls"] += 1
                        continue
                    label = (
                        link.get("label") if isinstance(link, dict) else None
                    ) or title_for.get(final) or _host_label(final)
                    new_csl.append({"label": str(label)[:400], "url": final})
                    csl_modified = True
                    stats["replaced_urls"] += 1
                    continue
                if isinstance(link, dict):
                    new_csl.append(link)
                else:
                    new_csl.append({"label": _host_label(url), "url": url})
            if csl_modified:
                sd["caption_source_links"] = new_csl
                sd_changed = True

            # blog_content.sources — list of {title,url}
            if isinstance(bc, dict):
                bc = dict(bc)
                new_bc_sources, d, r = _rewrite_sources_list(
                    bc.get("sources") or [],
                    resolved=resolved,
                    title_for=title_for,
                )
                if d or r or (
                    new_bc_sources != (bc.get("sources") or [])
                    and (bc.get("sources") or [])
                ):
                    bc["sources"] = new_bc_sources
                    stats["dropped_urls"] += d
                    stats["replaced_urls"] += r
                    bc_changed = True

            if not (sd_changed or bc_changed):
                continue
            update: Dict[str, Any] = {}
            if sd_changed:
                update["seed_data"] = sd
            if bc_changed:
                update["blog_content"] = bc
            if dry_run:
                stats["updated"] += 1
                continue
            client.table("posts").update(update).eq("id", row["id"]).execute()
            stats["updated"] += 1
        if len(rows) < page_size:
            break
        offset += page_size
    return stats


# ---------- driver ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limit-urls",
        type=int,
        default=None,
        help="Cap on number of distinct redirect URLs to resolve (debug aid)",
    )
    args = parser.parse_args()

    print(f"[backfill] dry_run={args.dry_run}")
    sb = get_supabase().client

    # ---- 1. Collect every distinct vertexaisearch URL across both tables.
    print("[backfill] collecting distinct redirect URLs...")
    distinct: Set[str] = set()
    res = sb.table("topic_research_dossiers").select(
        "id,normalized_payload"
    ).execute()
    for row in res.data or []:
        distinct.update(_collect_dossier_urls(row))
    res = sb.table("topic_scripts").select(
        "id,primary_source_url,source_urls"
    ).execute()
    for row in res.data or []:
        distinct.update(_collect_script_urls(row))
    res = sb.table("posts").select("id,seed_data,blog_content").execute()
    for row in res.data or []:
        distinct.update(_collect_post_urls(row))
    print(f"[backfill] {len(distinct)} distinct redirect URLs to resolve")
    if args.limit_urls:
        distinct = set(list(distinct)[: args.limit_urls])
        print(f"[backfill] (capped to {len(distinct)})")

    # ---- 2. Resolve them in parallel against the real network.
    t0 = time.time()
    resolved = resolve_grounding_urls(distinct) if distinct else {}
    print(
        f"[backfill] resolved {len(resolved)}/{len(distinct)} in "
        f"{time.time() - t0:.1f}s"
    )

    # ---- 3. Fetch human-readable titles for the resolved destinations.
    print("[backfill] fetching page titles for resolved URLs...")
    t0 = time.time()
    title_for = _build_title_map(resolved.values())
    print(f"[backfill] fetched {len(title_for)} titles in {time.time() - t0:.1f}s")

    # ---- 4. Rewrite dossier and script rows.
    print("[backfill] rewriting topic_research_dossiers...")
    dossier_stats = backfill_dossiers(
        dry_run=args.dry_run, resolved=resolved, title_for=title_for
    )
    print(f"[backfill] dossier stats: {dossier_stats}")

    print("[backfill] rewriting topic_scripts...")
    script_stats = backfill_scripts(
        dry_run=args.dry_run, resolved=resolved, title_for=title_for
    )
    print(f"[backfill] script stats: {script_stats}")

    print("[backfill] rewriting posts...")
    post_stats = backfill_posts(
        dry_run=args.dry_run, resolved=resolved, title_for=title_for
    )
    print(f"[backfill] post stats: {post_stats}")

    # ---- 5. Final verification — count remaining vertexaisearch URLs.
    if not args.dry_run:
        print("[backfill] verification scan...")
        remaining_dossiers = 0
        for row in (
            sb.table("topic_research_dossiers")
            .select("id,normalized_payload")
            .execute()
            .data
            or []
        ):
            payload = row.get("normalized_payload") or {}
            for key in ("sources", "source_urls"):
                for item in payload.get(key) or []:
                    url = item.get("url") if isinstance(item, dict) else item
                    if isinstance(url, str) and "vertexaisearch.cloud.google.com" in url:
                        remaining_dossiers += 1
                        break
                else:
                    continue
                break
        remaining_scripts = 0
        for row in (
            sb.table("topic_scripts")
            .select("id,primary_source_url,source_urls")
            .execute()
            .data
            or []
        ):
            primary = row.get("primary_source_url") or ""
            if "vertexaisearch.cloud.google.com" in primary:
                remaining_scripts += 1
                continue
            for item in row.get("source_urls") or []:
                url = item.get("url") if isinstance(item, dict) else item
                if isinstance(url, str) and "vertexaisearch.cloud.google.com" in url:
                    remaining_scripts += 1
                    break
        remaining_posts = 0
        for row in (
            sb.table("posts")
            .select("id,seed_data,blog_content")
            .execute()
            .data
            or []
        ):
            if _collect_post_urls(row):
                remaining_posts += 1
        print(
            f"[backfill] post-run residual: "
            f"{remaining_dossiers} dossiers, {remaining_scripts} scripts, "
            f"{remaining_posts} posts"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
