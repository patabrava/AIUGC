from __future__ import annotations

import argparse
import base64
import json
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.adapters.vertex_ai_client import get_vertex_ai_client
from app.core.config import get_settings
from app.features.shot_production.runner import load_video_uri


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
MODEL = "veo-3.1-generate-001"
SEED = 240713
DIALOGUE = (
    "Die Herausforderungen reichen von der aufzubringenden Kraft über die "
    "Positionierung des Rollstuhls bis hin zur Koordination der Bewegungen."
)

OLD_PROMPT = f"""Treat the supplied approved matched reference images as the sole visual truth. Use them to keep the same adult woman's exact identity, facial geometry, apparent age, hair, seated posture, camera position, and framing exactly as shown. Keep the frozen location exactly as shown and described: Home office advice scene A. Keep this exact room identity across all generated angles: tidy small home study, warm off-white wall behind, a simple light-oak desk against that wall, one closed silver-grey laptop centered on the desk, a small neat stack of plain white papers on the desk's right edge, a narrow oak wall shelf above carrying three or four upright books with muted spines, a plain window with a thin pale roller blind at far left letting in soft daylight, pale oak floor, calm and uncluttered, no people, no monitor, no desk lamp, no cables, no plants, no wall art, no pinboard, no coffee cup. Keep her upper-body outfit exactly: soft-beige blazer over a plain white top. Keep the wheelchair exactly: The same lightweight manual wheelchair in every image: matte dark-graphite frame, slim black armrests, black seat and back cushion, and silver hand rims. Keep the framing exactly: Use a static vertical 9:16 medium close-up from head to mid-torso at seated eye-level. Keep her face large and identity-readable while at least one armrest and part of a large rear wheel or silver hand rim remain clearly visible. Continue as restrained, natural phone-camera UGC with a subtle conversational expression, subtle blinking, and minimal head movement. Use the same warm adult German female voice across every take, speaking native German with natural conversational pacing and close smartphone microphone sound. Use the shot duration naturally, pacing the beat to place the final spoken word near 7.0 seconds without sounding slow or theatrical. She says exactly this German beat once: “{DIALOGUE}” Do not speak any other words or any English. After the final word, naturally stop speaking, close her mouth, and keep quiet eye contact. The camera remains locked in the exact same position after the final word: no pan, tilt, zoom, dolly, orbit, or reframing. She may keep subtle blinking and natural breathing without moving the camera. Do not freeze or perform an artificial end pose. Keep every frame completely free of on-screen text: no captions, subtitles, logos, watermarks, letters, symbols, or gibberish glyphs."""

NEW_PROMPT = f"""One continuous, unedited vertical smartphone UGC take guided by the supplied approved matched reference images.

Camera: static fixed 9:16 medium close-up at seated eye level, framed from head to mid-torso. Keep her face large and identity-readable, with at least one wheelchair armrest and part of a large rear wheel or silver hand rim clearly visible. The camera stays completely still from the opening frame through the final frame.

Visual continuity: use the two actor references as identity anchors for the same adult woman, preserving her facial geometry, apparent age, and hair. Place her in the exact calm, uncluttered home-office identity shown by the room reference. She wears a soft-beige blazer over a plain white top and sits in the same lightweight manual wheelchair with a matte dark-graphite frame, slim black armrests, black seat and back cushion, and silver hand rims.

Subject motion: restrained natural talking-head delivery. She maintains a subtle conversational expression with normal blinking, quiet breathing, minimal head movement, and no posture reset.

Timing: begin speaking naturally at the start. Pace the sentence conversationally so the final spoken word lands near 7.0 seconds. After the final word, close her mouth naturally and hold quiet eye contact with subtle blinking and breathing until the clip ends. Remain in the same continuous take; do not introduce a cut, transition, end card, freeze frame, or artificial end pose.

Audio: one warm adult female voice speaking native German with natural conversational pacing and close smartphone-microphone sound. No music, background voices, or extra speech.

Dialogue — speak exactly once and verbatim:
“{DIALOGUE}”

No other spoken words. No English. No on-screen text, captions, subtitles, logos, watermarks, letters, symbols, or gibberish glyphs."""

FINAL_PROMPT = f"""For this generated editorial take only: one continuous, unedited vertical smartphone UGC shot guided by the supplied approved matched reference images. Authentic consumer smartphone footage from a phone propped securely on a desk.

Camera: static fixed 9:16 medium close-up at seated eye level, framed from head to mid-torso. Keep her face large and identity-readable, with at least one wheelchair armrest and part of a large rear wheel or silver hand rim clearly visible. The camera remains completely still from the opening frame through the final frame while only the subject moves.

Reference authority: use the two actor references as identity anchors for the same adult woman, preserving her exact facial geometry, apparent age, natural skin texture, facial detail, and hair. Use the room reference as the authority for the exact calm, uncluttered home-office identity, lighting, exposure, color, and natural image texture. She wears a soft-beige blazer over a plain white top and sits in the same lightweight manual wheelchair with a matte dark-graphite frame, slim black armrests, black seat and back cushion, and silver hand rims. Preserve these details consistently throughout this take.

Performance: the seated woman speaks like a real person in a relaxed conversation. Use accurate native-German lip-sync with natural jaw and lip movement. Add small speech-coupled head movements and occasional micro-nods on stressed words, subtle eyebrow and cheek movement appropriate to the sentence, a warm gaze that mostly meets the lens with slight natural micro-shifts, irregular natural blinking, and quiet chest-and-shoulder breathing. Her movement remains restrained and asymmetrical rather than rhythmic or rehearsed. Her seated posture, wheelchair position, and overall composition remain stable.

Timing: she begins speaking promptly and delivers the complete line once at a natural conversational pace, finishing the final word around 7.0 seconds. Use subtle human timing variation without theatrical pauses. After the final word, her mouth relaxes closed while warm eye contact, quiet breathing, irregular blinking, and small living stillness continue naturally through the final frame.

Audio: one warm adult female voice speaking native German with natural conversational cadence and subtle human timing variation. Clean, dry, close smartphone-microphone sound with quiet home-office room tone.

Dialogue: the complete spoken performance is the following line, delivered once exactly as written.

The woman says: {DIALOGUE}"""

OLD_NEGATIVE = (
    "face change, age change, hair change, wardrobe change, room change, extra person, "
    "wheelchair change, cropped wheelchair, standing, walking, camera movement, camera pan, "
    "camera tilt, camera zoom, push-in, dolly, orbit, reframe, posture reset, generated text, "
    "subtitles, music, background voices, extra speech, hands entering frame, repeated dialogue, "
    "English speech, logos, watermarks, gibberish text"
)

NEW_NEGATIVE = (
    "identity drift, face change, facial distortion, age change, hair change, wardrobe change, "
    "room change, extra person, wheelchair change, wheelchair deformation, cropped wheelchair, "
    "standing, walking, camera movement, camera pan, camera tilt, camera zoom, push-in, dolly, "
    "orbit, reframe, posture reset, cut, jump cut, scene transition, wipe transition, end card, "
    "freeze frame, generated text, subtitles, captions, music, background voices, extra speech, "
    "hands entering frame, repeated dialogue, English speech, logos, watermarks, gibberish text"
)

FINAL_NEGATIVE = (
    "identity drift, facial identity change, face change, facial distortion, age change, hair change, "
    "wardrobe change, room change, lighting change, composition change, extra person, wheelchair change, "
    "wheelchair deformation, wheelchair absent, wheelchair fully obscured, missing armrest, missing rear wheel "
    "or hand rim, standing, walking, large gestures, hands entering frame, camera movement, handheld shake, "
    "camera pan, camera tilt, camera zoom, push-in, dolly, orbit, reframe, posture reset, internal cut, jump cut, "
    "scene transition, wipe transition, end card, freeze frame, held end pose, generated text, subtitles, "
    "captions, letters, symbols, logos, watermarks, gibberish text, music, background voices, voiceover, "
    "extra speech, added words, repeated dialogue, English speech, robotic voice, text-to-speech cadence, "
    "studio reverb, exaggerated mouth sounds, clicks, pops, plastic skin, waxy skin, over-smoothed skin, "
    "airbrushed skin, beauty filter, face slimming, doll face, cgi look, 3d render, video-game character, "
    "uncanny valley, frozen stare, dead eyes, robotic motion, stiff motion, rhythmic motion, exaggerated acting, "
    "mouth warping, teeth artifacts, melting face, lip-sync drift, artificial sharpening, hdr glow, "
    "oversaturated color, cinematic studio look"
)

VARIANT_NAMES = ("old", "new", "final")

REFERENCE_FILES = (
    ("01-actor-front.png", "image/png", "actor_identity_front"),
    ("02-actor-three-quarter.jpg", "image/jpeg", "actor_identity_three_quarter"),
    ("03-home-office.png", "image/png", "canonical_scene"),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(payload: dict[str, Any]) -> None:
    temporary = MANIFEST_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(MANIFEST_PATH)


def load_manifest() -> dict[str, Any]:
    if MANIFEST_PATH.exists():
        payload = json.loads(MANIFEST_PATH.read_text())
        if "final" not in payload["variants"]:
            payload["variants"]["final"] = {
                "prompt": FINAL_PROMPT,
                "negative_prompt": FINAL_NEGATIVE,
                "state": "prepared",
            }
            payload["experiment"] = "VEO 3.1 old-vs-new-vs-final prompt comparison"
            payload["status"] = "prepared"
            payload["updated_at"] = now()
            atomic_write(payload)
        return payload
    references = []
    for filename, mime_type, role in REFERENCE_FILES:
        path = ROOT / "references" / filename
        data = path.read_bytes()
        references.append(
            {
                "role": role,
                "path": str(path),
                "mime_type": mime_type,
                "sha256": sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    payload = {
        "experiment": "VEO 3.1 old-vs-new prompt A/B",
        "created_at": now(),
        "status": "prepared",
        "controlled_parameters": {
            "model": MODEL,
            "seed": SEED,
            "duration_seconds": 8,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
            "sample_count": 1,
            "dialogue": DIALOGUE,
            "reference_order": [item[2] for item in REFERENCE_FILES],
        },
        "references": references,
        "variants": {
            "old": {
                "prompt": OLD_PROMPT,
                "negative_prompt": OLD_NEGATIVE,
                "state": "prepared",
            },
            "new": {
                "prompt": NEW_PROMPT,
                "negative_prompt": NEW_NEGATIVE,
                "state": "prepared",
            },
            "final": {
                "prompt": FINAL_PROMPT,
                "negative_prompt": FINAL_NEGATIVE,
                "state": "prepared",
            },
        },
    }
    atomic_write(payload)
    return payload


def reference_payloads() -> list[dict[str, str]]:
    result = []
    for filename, mime_type, _role in REFERENCE_FILES:
        data = (ROOT / "references" / filename).read_bytes()
        result.append(
            {
                "data_base64": base64.b64encode(data).decode("ascii"),
                "mime_type": mime_type,
            }
        )
    return result


def submit() -> None:
    manifest = load_manifest()
    client = get_vertex_ai_client()
    output_gcs_uri = get_settings().vertex_ai_output_gcs_uri or None
    refs = reference_payloads()
    for name in VARIANT_NAMES:
        variant = manifest["variants"][name]
        if variant["state"] not in {"prepared"}:
            print(f"{name}: preserving existing state {variant['state']}")
            continue
        variant["state"] = "submitting"
        variant["submission_started_at"] = now()
        atomic_write(manifest)
        result = client.submit_text_video(
            prompt=variant["prompt"],
            correlation_id=f"veo-prompt-ab-20260730-{name}",
            aspect_ratio="9:16",
            duration_seconds=8,
            output_gcs_uri=output_gcs_uri,
            model=MODEL,
            reference_images=refs,
            negative_prompt=variant["negative_prompt"],
            seed=SEED,
            sample_count=1,
            generate_audio=True,
            resolution="720p",
        )
        variant.update(
            {
                "state": "submitted",
                "operation_id": result["operation_id"],
                "provider_model": result["provider_model"],
                "submitted_at": now(),
            }
        )
        atomic_write(manifest)
        print(f"{name}: submitted {result['operation_id']}")
    manifest["status"] = "submitted"
    atomic_write(manifest)


def poll() -> None:
    manifest = load_manifest()
    client = get_vertex_ai_client()
    started = time.monotonic()
    while True:
        pending = []
        for name in VARIANT_NAMES:
            variant = manifest["variants"][name]
            if variant["state"] == "completed":
                continue
            operation_id = variant.get("operation_id")
            if not operation_id:
                raise RuntimeError(f"{name}: missing accepted operation id")
            result = client.check_operation_status(
                operation_id=operation_id,
                correlation_id=f"veo-prompt-ab-20260730-{name}",
            )
            variant["last_polled_at"] = now()
            variant["provider_status"] = result["status"]
            if result.get("status") == "failed":
                variant["state"] = "failed"
                variant["error"] = result.get("error")
                atomic_write(manifest)
                raise RuntimeError(f"{name}: provider failed: {result.get('error')}")
            if not result.get("done"):
                variant["state"] = "processing"
                pending.append(name)
                print(f"{name}: processing")
                continue
            video_uri = result.get("video_uri")
            if not video_uri:
                raise RuntimeError(f"{name}: completed without video URI")
            video_bytes = load_video_uri(video_uri)
            video_path = ROOT / f"{name}-prompt.mp4"
            video_path.write_bytes(video_bytes)
            variant.update(
                {
                    "state": "completed",
                    "completed_at": now(),
                    "video_uri": video_uri,
                    "artifact": {
                        "path": str(video_path),
                        "sha256": sha256(video_bytes).hexdigest(),
                        "bytes": len(video_bytes),
                    },
                }
            )
            print(f"{name}: downloaded {video_path}")
        atomic_write(manifest)
        if not pending:
            manifest["status"] = "completed"
            manifest["completed_at"] = now()
            atomic_write(manifest)
            return
        if time.monotonic() - started > 1800:
            raise TimeoutError("Timed out waiting for VEO operations; no resubmission was made.")
        time.sleep(15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "submit", "poll"))
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = load_manifest()
        print(MANIFEST_PATH)
        print({name: item["state"] for name, item in manifest["variants"].items()})
    elif args.command == "submit":
        submit()
    else:
        poll()


if __name__ == "__main__":
    main()
