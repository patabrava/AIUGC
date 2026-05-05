"""HTTP live stress test for topic seeding across post types and length tiers.

Generated artifact. Assumes the FastAPI app is reachable locally.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.topics.topic_validation import get_prompt1_word_bounds, get_prompt2_word_bounds, get_prompt3_word_bounds

MATRIX = [
    ("value", {"value": 2, "lifestyle": 0, "product": 0}),
    ("lifestyle", {"value": 0, "lifestyle": 2, "product": 0}),
    ("product", {"value": 0, "lifestyle": 0, "product": 2}),
    ("mixed", {"value": 1, "lifestyle": 1, "product": 1}),
]
TIERS = (8, 16, 32)
BALANCED_7 = {"value": 3, "lifestyle": 2, "product": 2}


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return {"http": response.status, "body": _decode(raw), "raw": raw}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return {"http": exc.code, "body": _decode(raw), "raw": raw}

    def login(self, email: str) -> Dict[str, Any]:
        data = urllib.parse.urlencode({"email": email}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/auth/send-otp",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=30) as response:
                return {"http": response.status, "url": response.geturl()}
        except urllib.error.HTTPError as exc:
            return {"http": exc.code, "url": exc.geturl()}


def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _data(response: Dict[str, Any]) -> Any:
    body = response.get("body")
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _word_bounds(tier: int, post_type: str) -> tuple[int, int]:
    if post_type == "product":
        return get_prompt3_word_bounds(tier)
    if post_type == "lifestyle":
        return get_prompt2_word_bounds(tier)
    return get_prompt1_word_bounds(tier)


def score_case(result: Dict[str, Any]) -> None:
    scores: List[int] = []
    for post in result.get("posts") or []:
        lower, upper = _word_bounds(int(result["tier"]), str(post["post_type"] or ""))
        words = int(post.get("script_words") or 0)
        within_bounds = lower <= words <= upper
        post_score = 0
        if within_bounds:
            post_score += 40
        if post.get("caption"):
            post_score += 20
        if int(post.get("script_chars") or 0) > 0:
            post_score += 20
        if post.get("tier") == result["tier"]:
            post_score += 20
        post["quality"] = {
            "word_bounds": [lower, upper],
            "within_bounds": within_bounds,
            "score": post_score,
        }
        scores.append(post_score)
    result["quality"] = {
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "all_posts_in_bounds": all(post.get("quality", {}).get("within_bounds") for post in result.get("posts") or []),
    }


def run_case(client: Client, run_id: str, mode: str, counts: Dict[str, int], tier: int, timeout_s: int) -> Dict[str, Any]:
    brand = f"Live Topic Stress {mode} {tier}s {run_id}"
    result: Dict[str, Any] = {
        "mode": mode,
        "tier": tier,
        "brand": brand,
        "expected_posts": sum(counts.values()),
        "samples": [],
        "posts": [],
        "ok": False,
    }
    create = client.request(
        "POST",
        "/batches",
        {
            "brand": brand,
            "creation_mode": "automated",
            "post_type_counts": counts,
            "target_length_tier": tier,
        },
    )
    result["create_http"] = create["http"]
    payload = _data(create)
    batch_id = payload.get("id") if isinstance(payload, dict) else None
    result["batch_id"] = batch_id
    if not batch_id:
        result["error"] = create["raw"][:1200]
        return result

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = client.request("GET", f"/batches/{batch_id}/status")
        status_data = _data(status) if status["http"] == 200 else {}
        progress = status_data.get("progress") if isinstance(status_data, dict) else None
        sample = {
            "at": _now(),
            "http": status["http"],
            "state": status_data.get("state") if isinstance(status_data, dict) else None,
            "posts_count": status_data.get("posts_count") if isinstance(status_data, dict) else None,
            "stage": progress.get("stage") if isinstance(progress, dict) else None,
            "current_post_type": progress.get("current_post_type") if isinstance(progress, dict) else None,
            "detail": progress.get("detail_message") if isinstance(progress, dict) else None,
        }
        if not result["samples"] or sample != result["samples"][-1]:
            result["samples"].append(sample)
        if sample["stage"] == "failed":
            break
        if sample["state"] == "S2_SEEDED" and sample["posts_count"] == result["expected_posts"]:
            break
        time.sleep(2)

    detail = client.request("GET", f"/batches/{batch_id}")
    result["detail_http"] = detail["http"]
    detail_data = _data(detail)
    if isinstance(detail_data, dict):
        result["state"] = detail_data.get("state")
        result["posts_count"] = detail_data.get("posts_count")
        for post in detail_data.get("posts") or []:
            seed_data = post.get("seed_data") or {}
            result["posts"].append(
                {
                    "id": post.get("id"),
                    "post_type": post.get("post_type"),
                    "title": post.get("topic_title"),
                    "tier": seed_data.get("target_length_tier"),
                    "script_words": len(str(post.get("topic_rotation") or "").split()),
                    "script_chars": len(str(post.get("topic_rotation") or "")),
                    "caption": bool(post.get("publish_caption") or seed_data.get("caption")),
                }
            )

    expected_types = {key: value for key, value in counts.items() if value}
    observed_types: Dict[str, int] = {}
    for post in result["posts"]:
        observed_types[post["post_type"]] = observed_types.get(post["post_type"], 0) + 1
    result["observed_types"] = observed_types
    score_case(result)
    result["ok"] = (
        result.get("state") == "S2_SEEDED"
        and result.get("posts_count") == result["expected_posts"]
        and observed_types == expected_types
        and all(post["tier"] == tier and post["caption"] and post["script_chars"] > 0 for post in result["posts"])
    )

    archive = client.request("PUT", f"/batches/{batch_id}/archive", {"archived": True})
    result["archive_http"] = archive["http"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="stress@example.test")
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--scripts-per-batch", type=int, default=7)
    parser.add_argument("--output-dir", type=Path, default=Path("agents/testscripts"))
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = Client(args.base_url)
    login = client.login(args.email)
    health = client.request("GET", "/health")
    results: List[Dict[str, Any]] = []
    run_plan: List[Dict[str, Any]] = []
    balanced_run = args.scripts_per_batch == 7
    for tier in TIERS:
        counts_matrix = [("balanced", BALANCED_7)] if balanced_run else MATRIX
        for mode, counts in counts_matrix:
            run_plan.append({"mode": mode, "tier": tier, "scripts_per_batch": args.scripts_per_batch})
            results.append(run_case(client, run_id, mode, counts, tier, args.timeout_s))

    payload = {
        "base_url": args.base_url,
        "run_id": run_id,
        "started_at": run_id,
        "completed_at": _now(),
        "login": login,
        "health": {"http": health["http"], "body": health["body"]},
        "matrix": run_plan,
        "results": results,
        "all_ok": all(item.get("ok") for item in results),
    }
    output_path = args.output_dir / f"live_topic_generation_matrix_{run_id}.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "all_ok": payload["all_ok"]}, indent=2))
    if not payload["all_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
