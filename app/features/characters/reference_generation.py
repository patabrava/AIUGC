"""Create a verified three-quarter production reference from one canonical face."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.core.image_generation_prompt import write_raw_camera_image_prompt
from app.features.shot_frames.identity_qa import evaluate_actor_reference_pair


ACTOR_THREE_QUARTER_MODEL = "gemini-3-pro-image"
ACTOR_THREE_QUARTER_CANDIDATE_COUNT = 3
ACTOR_THREE_QUARTER_PROMPT = """Edit the supplied canonical frontal actor photograph into one photorealistic
left three-quarter portrait of the exact same person. Rotate only the head and camera viewpoint approximately
30 degrees. Preserve the exact facial geometry, skull and jaw proportions, eyes, eyebrows, nose, lips, ears,
hairline, hair color and texture, apparent age, skin tone, natural skin texture, mild asymmetry, and body
proportions from the source photograph. Keep the same camera distance, crop, expression, lighting character,
and ordinary unretouched camera-file appearance. Do not beautify, average, rejuvenate, reshape, stylize, add
makeup, alter the hairstyle, or invent a different person. Render exactly one person with no text or watermark."""


def generate_verified_three_quarter_reference(
    canonical_front: dict[str, Any],
    *,
    llm_client: Any | None = None,
    identity_model: str,
    minimum_confidence: float,
) -> dict[str, Any]:
    mime_type = str(canonical_front.get("mime_type") or "").strip().lower()
    image_bytes = canonical_front.get("image_bytes")
    if not mime_type.startswith("image/") or not isinstance(image_bytes, bytes) or not image_bytes:
        raise ValidationError("Canonical actor front reference requires image bytes and an image MIME type.")

    client = llm_client or get_llm_client()
    renderer_prompt = write_raw_camera_image_prompt(client=client, brief=ACTOR_THREE_QUARTER_PROMPT)
    candidates = []
    for index in range(1, ACTOR_THREE_QUARTER_CANDIDATE_COUNT + 1):
        generated = client.generate_gemini_image(
            prompt=renderer_prompt,
            model=ACTOR_THREE_QUARTER_MODEL,
            temperature=0,
            aspect_ratio="9:16",
            image_size="2K",
            input_images=[{"mime_type": mime_type, "image_bytes": image_bytes}],
        )
        generated_mime_type = str(generated.get("mime_type") or "").strip().lower()
        generated_bytes = generated.get("image_bytes")
        if (
            generated.get("model") != ACTOR_THREE_QUARTER_MODEL
            or generated_mime_type not in {"image/png", "image/jpeg"}
            or not isinstance(generated_bytes, bytes)
            or not generated_bytes
        ):
            raise ValidationError("Gemini Pro returned an invalid actor three-quarter reference.")
        identity_report = evaluate_actor_reference_pair(
            canonical_front,
            {"mime_type": generated_mime_type, "image_bytes": generated_bytes},
            llm_client=client,
            model=identity_model,
            minimum_confidence=minimum_confidence,
        )
        candidates.append(
            {
                "index": index,
                "image_bytes": generated_bytes,
                "mime_type": generated_mime_type,
                "identity_gate_result": asdict(identity_report),
            }
        )

    passing_candidates = [
        candidate
        for candidate in candidates
        if candidate["identity_gate_result"]["passed"] is True
    ]
    if not passing_candidates:
        raise ValidationError(
            "No Gemini Pro three-quarter candidate preserved the canonical frontal identity.",
            {
                "candidate_results": [
                    {
                        "index": candidate["index"],
                        "confidence": candidate["identity_gate_result"]["confidence"],
                        "blocking_reasons": candidate["identity_gate_result"]["blocking_reasons"],
                    }
                    for candidate in candidates
                ],
            },
        )
    selected = max(
        passing_candidates,
        key=lambda candidate: candidate["identity_gate_result"]["confidence"],
    )
    return {
        "image_bytes": selected["image_bytes"],
        "mime_type": selected["mime_type"],
        "model": ACTOR_THREE_QUARTER_MODEL,
        "prompt": ACTOR_THREE_QUARTER_PROMPT,
        "identity_gate_result": selected["identity_gate_result"],
        "selected_index": selected["index"],
        "candidates": candidates,
    }


__all__ = [
    "ACTOR_THREE_QUARTER_MODEL",
    "ACTOR_THREE_QUARTER_CANDIDATE_COUNT",
    "ACTOR_THREE_QUARTER_PROMPT",
    "generate_verified_three_quarter_reference",
]
