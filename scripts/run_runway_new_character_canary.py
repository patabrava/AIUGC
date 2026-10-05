"""One isolated new-character Seedance 2.5 compatibility canary.

Native text-to-video requires no image. Optional Seedream image stages are
separate so the original image can be inspected before video submission.
Submission intent is persisted before each POST; ambiguous/terminal stages never
resubmit. No production writes, fallback video models, or moderation overrides.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POST = "91825c68-5da5-4b50-b1dc-cee45d90abc3"
RUN = "557fdd80-dd78-4bcf-80ad-f05e89b4b8ce"
TAKE = "c7ae2444-e51a-45f0-8794-0380923fdb1f"
PROMPT_HASH = "6bff943645a9d96189e24a0f3e435b4d305f7573434faa68950983958823927f"
REJECTED_SOURCES = {
    "d3fa8222a65afcba3b33179186b847e8e1d81aa4ab0e6bd551b9fb0c2b1523a6",
    "be962400783ba4cdcfc0f39e47b01fc4849b5b56f2721eb17cb5447e13dc843f",
}
BRIEF = """Create one new fictional adult presenter for an isolated camera-realism test,
entirely from text with no reference image and no resemblance to a named real person.
Create one upright 9:16 original smartphone photograph, chest-up and frontal, of
the adult seated comfortably at an ordinary home kitchen table, looking into the
camera with a relaxed closed mouth, ready to speak. Freeze a plain blue cotton top
and a modest everyday kitchen with one mug, a folded dish towel, mild wear, clear
walking space, and coherent soft window light. Preserve natural texture, subtle
asymmetry, and the imperfect exposure of a real front-camera capture. No captions,
labels, logos, montage, collage, filters, or watermarks. Write only the final image
renderer prompt. No existing production character is an identity reference."""


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".manifest-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fence(manifest: dict, stage: str) -> None:
    record = manifest.setdefault(stage, {})
    if record.get("status"):
        raise ValueError("Submission already entered; poll only, never resubmit")
    record.update(status="submission_intent", started_at=datetime.now(timezone.utc).isoformat())


def validate_source(manifest: dict, content: bytes, review: bool) -> None:
    record = manifest.get("image", {})
    if record.get("status") != "SUCCEEDED" or not review:
        raise ValueError("A successful, visually reviewed original image is required")
    checksum = digest(content)
    if checksum != record.get("sha256") or checksum in REJECTED_SOURCES:
        raise ValueError("Source changed or was already rejected")
    if manifest.get("model") != "seedance2_5" or manifest.get("source_mode") != "text_only_new_character":
        raise ValueError("Canary contract changed")


def baseline(db: Any) -> dict:
    post = db.table("posts").select("*").eq("id", POST).single().execute().data
    run = db.table("semantic_video_runs").select("*").eq("id", RUN).single().execute().data
    takes = db.table("semantic_video_takes").select("*").eq("run_id", RUN).order("id").execute().data
    return {"post_video": digest({k: v for k, v in post.items() if k.startswith("video_")}),
            "run": digest(run), "takes": digest(takes)}


def performance(db: Any) -> tuple[str, int | None]:
    take = db.table("semantic_video_takes").select("request_contract,seed").eq("id", TAKE).single().execute().data
    contract = take["request_contract"]
    prompt = contract["prompt"]
    if digest(prompt.encode()) != PROMPT_HASH:
        raise ValueError("Persisted performance prompt changed")
    return prompt, contract.get("seed", take.get("seed"))


def native_text_prompt(performance_prompt: str) -> tuple[str, str]:
    matches = re.findall(r'Dialogue:\s*[“"]([^”"]+)[”"]', performance_prompt)
    if len(matches) != 1 or len(matches[0].split()) != 15:
        raise ValueError("Frozen German dialogue could not be identified uniquely")
    dialogue = matches[0]
    prompt = (
        "One continuous unedited eight-second vertical smartphone UGC video. "
        "Create one entirely fictional adult presenter, not a named or recognizable real person, "
        "seated comfortably at an ordinary home kitchen table. Chest-up frontal arm's-length "
        "selfie framing, plain blue cotton top, coherent soft window light, natural skin texture, "
        "subtle asymmetry, modern phone HDR and deep depth of field. The ordinary mildly worn "
        "kitchen contains one mug and a folded dish towel, with clear walking space. "
        "Keep the same person, clothing, lighting, room and subject scale throughout. "
        "The phone maintains a fixed distance with tiny irregular handheld drift, breathing "
        "rise and fall, faint roll and wrist corrections under two percent of frame width. "
        "The presenter speaks directly to the lens with natural native-German lips and jaw "
        "articulation, restrained conversational expression, irregular blinking and relaxed "
        "hands. Dialogue is spoken advice, not a physical action cue. Deliver the complete "
        "dialogue exactly once with natural conversational cadence, beginning promptly. "
        "Remain engaged with the camera through the last frame, as if continuing a conversation. "
        "One shot, constant framing, no deliberate camera moves, cuts, concluding gestures, "
        "captions, logos, music or additional words. "
        f"Dialogue: “{dialogue}”\n"
        "Audio: one warm adult voice matching the visible fictional presenter, native German, "
        "clean close smartphone-microphone sound and quiet natural kitchen ambience."
    )
    return prompt, dialogue


class PromptWriter:
    def __init__(self, settings: Any):
        self.settings = settings

    def generate_gemini_text(self, **request: Any) -> str:
        import httpx
        if not self.settings.gemini_api_key:
            raise ValueError("Configured prompt-writer credential is missing")
        payload = {
            "systemInstruction": {"parts": [{"text": request["system_prompt"]}]},
            "contents": [{"role": "user", "parts": [{"text": request["prompt"]}]}],
            "generationConfig": {"maxOutputTokens": request["max_tokens"], "temperature": request["temperature"],
                                 "thinkingConfig": {"thinkingBudget": request["thinking_budget"]}},
        }
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/" + self.settings.gemini_topic_model + ":generateContent"
        response = httpx.post(endpoint, headers={"x-goog-api-key": self.settings.gemini_api_key}, json=payload, timeout=90)
        if response.status_code != 200:
            raise ValueError("Prompt-writer request failed")
        candidates = response.json().get("candidates") or []
        if len(candidates) != 1:
            raise ValueError("Prompt-writer response is incomplete")
        return "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if not p.get("thought"))


def download_image(url: str, settings: Any) -> bytes:
    import httpx
    from app.adapters.runway_client import parse_allowed_hosts, validate_output_url
    hosts = parse_allowed_hosts(settings.runway_output_allowed_hosts)
    with httpx.Client(timeout=90, follow_redirects=False) as client:
        for _ in range(4):
            validate_output_url(url, hosts)
            with client.stream("GET", url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0] != "image/png":
                    raise ValueError("Original image transport failed")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 16 * 1024 * 1024:
                        raise ValueError("Original image exceeds byte cap")
                return bytes(content)
    raise ValueError("Image redirect limit exceeded")


def execute(args: argparse.Namespace) -> dict:
    import structlog
    def drop_logs(*_args: Any) -> None:
        raise structlog.DropEvent
    logging.disable(logging.CRITICAL)
    structlog.configure(processors=[drop_logs])
    from app.features.runway_evaluations.providers import ProviderSettings
    from app.core.config import get_settings
    from app.adapters.runway_client import get_runway_client, RunwayError
    from app.adapters.supabase_client import get_supabase
    from app.core.image_generation_prompt import write_raw_camera_image_prompt, load_raw_camera_system_prompt
    from PIL import Image
    settings = ProviderSettings(get_settings(), "runway")
    client = get_runway_client(settings)
    db = get_supabase().client
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = folder / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        if args.stage == "text-video":
            if manifest:
                raise ValueError("Existing canary must be polled, never replaced or resubmitted")
            account = client.get_organization(correlation_id="new-character-text-preflight")
            if not account["seedance2_5_available"] or float(account["credit_balance"]) < 160:
                raise ValueError("Account cannot cover this single canary")
            original, seed = performance(db)
            prompt, dialogue = native_text_prompt(original)
            manifest = {"model": "seedance2_5", "source_mode": "native_text_to_video_new_character",
                        "reference_images": 0, "duration": 8, "ratio": "480:854", "audio": True,
                        "prompt_sha256": digest(prompt.encode()), "frozen_dialogue_sha256": digest(dialogue.encode()),
                        "frozen_performance_prompt_sha256": PROMPT_HASH, "dialogue_words": 15,
                        "estimated_runway_credits": 160, "balance_before": account["credit_balance"], "baseline": baseline(db)}
            fence(manifest, "video")
            save(path, manifest)
            body = {"model": "seedance2_5", "promptText": prompt, "duration": 8, "ratio": "480:854", "audio": True}
            if seed is not None:
                body["seed"] = seed
            response = client._request("POST", "/v1/text_to_video", json_body=body)
            if response.status_code != 200 or not response.json().get("id"):
                manifest["video"].update(status="submission_unknown", http_status=response.status_code)
            else:
                manifest["video"].update(status="submitted", task_id=response.json()["id"],
                                         estimated_credits=response.json().get("estimatedCost"))
        elif args.stage == "prepare":
            if manifest:
                raise ValueError("Existing canary must be resumed, not replaced")
            account = client.get_organization(correlation_id="new-character-20261003")
            if not account["seedance2_5_available"] or float(account["credit_balance"]) < 164:
                raise ValueError("Account cannot cover this single canary")
            performance(db)
            prompt = write_raw_camera_image_prompt(client=PromptWriter(settings), brief=BRIEF)
            if len(prompt) > 4000:
                raise ValueError("Image renderer prompt exceeds provider limit")
            prompt_path = folder / "renderer-prompt.txt"
            prompt_path.write_text(prompt)
            prompt_path.chmod(0o600)
            manifest = {"model": "seedance2_5", "source_mode": "text_only_new_character", "reference_images": 0,
                        "duration": 8, "ratio": "480:854", "audio": True, "prompt_sha256": PROMPT_HASH,
                        "image_prompt_sha256": digest(prompt.encode()), "writer_system_sha256": digest(load_raw_camera_system_prompt().encode()),
                        "estimated_runway_credits": 164, "balance_before": account["credit_balance"], "baseline": baseline(db)}
        elif args.stage == "image":
            if manifest.get("source_mode") != "text_only_new_character":
                raise ValueError("Prepared canary is required")
            prompt = (folder / "renderer-prompt.txt").read_text()
            if digest(prompt.encode()) != manifest["image_prompt_sha256"]:
                raise ValueError("Image prompt changed")
            fence(manifest, "image")
            save(path, manifest)
            response = client._request("POST", "/v1/text_to_image", json_body={"model": "seedream5_lite", "promptText": prompt,
                       "ratio": "1600:2848", "outputFormat": "png", "outputCount": 1})
            if response.status_code != 200 or not response.json().get("id"):
                manifest["image"].update(status="submission_unknown", http_status=response.status_code)
            else:
                manifest["image"].update(status="submitted", task_id=response.json()["id"])
        elif args.stage == "video":
            image_task = client.get_task(manifest["image"]["task_id"], correlation_id="new-character-source")
            if image_task.status != "SUCCEEDED" or len(image_task.output_urls) != 1:
                raise ValueError("Original image task is unavailable")
            content = download_image(image_task.output_urls[0], settings)
            validate_source(manifest, content, args.confirm_source_review)
            prompt, seed = performance(db)
            if baseline(db) != manifest["baseline"]:
                raise ValueError("Production baseline changed before canary submission")
            fence(manifest, "video")
            save(path, manifest)
            try:
                accepted = client.submit_image_to_video(prompt_image=image_task.output_urls[0], prompt_text=prompt,
                            ratio="480:854", duration_seconds=8, audio=True, seed=seed, correlation_id="new-character-20261003")
                manifest["video"].update(status="submitted", **accepted)
            except RunwayError as error:
                manifest["video"].update(status="not_submitted" if error.proves_no_task else "submission_unknown", error_kind=error.kind)
        else:
            stage = "image" if args.stage == "poll-image" else "video"
            record = manifest[stage]
            if not record.get("task_id"):
                raise ValueError("No acknowledged task; never resubmit")
            task = client.get_task(record["task_id"], correlation_id="new-character-poll")
            record.update(status=task.status, actual_credits=task.cost_credits, failure_code=task.failure_code)
            if task.status == "SUCCEEDED" and "sha256" not in record:
                if len(task.output_urls) != 1:
                    raise ValueError("Unexpected provider output count")
                if stage == "image":
                    content = download_image(task.output_urls[0], settings)
                    image = Image.open(io.BytesIO(content))
                    image.load()
                    if image.format != "PNG" or image.size != (1600, 2848) or digest(content) in REJECTED_SOURCES:
                        raise ValueError("New original image failed validation")
                    (folder / "new-character.png").write_bytes(content)
                    record.update(dimensions=list(image.size), sha256=digest(content), bytes=len(content))
                else:
                    from app.features.runway_evaluations.service import probe_mp4
                    output = client.download_output(task.output_urls[0], correlation_id="new-character-download")
                    probe = probe_mp4(output.content, expected_duration_seconds=8, expected_ratio="480:854", expect_audio=True)
                    (folder / "seedance-8s-480p.mp4").write_bytes(output.content)
                    record.update(sha256=digest(output.content), bytes=len(output.content), output_host=output.host, probe=probe)
            if task.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                record["balance_after"] = client.get_organization(correlation_id="new-character-cost")["credit_balance"]
                record["production_unchanged"] = baseline(db) == manifest["baseline"]
        save(path, manifest)
        return {k: manifest.get(k) for k in ("model", "source_mode", "image", "video", "estimated_runway_credits", "balance_before")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["text-video", "prepare", "image", "poll-image", "video", "poll-video"])
    parser.add_argument("--output", type=Path, default=ROOT / "output/runway-new-character-2026-10-03")
    parser.add_argument("--confirm-source-review", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "message": "Canary stopped; inspect the durable stage before any further action"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
