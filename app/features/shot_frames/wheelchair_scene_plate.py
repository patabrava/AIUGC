"""Generate identity-locked wheelchair scene plates before any Veo request."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
import os
import random
from threading import Condition
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError
from app.core.image_generation_prompt import write_raw_camera_image_prompt
from app.features.shot_frames.service import ShotFrameReference


WHEELCHAIR_VISUAL_CONTRACT = (
    "The same lightweight manual wheelchair in every image: matte dark-graphite frame, "
    "slim black armrests, black seat and back cushion, and silver hand rims."
)
FRAMING_CONTRACT = (
    "Use a static vertical 9:16 medium close-up from head to mid-torso at seated eye-level. "
    "Keep her face large and identity-readable while at least one armrest and part of a large "
    "rear wheel or silver hand rim remain clearly visible."
)
_REFERENCE_ROLES = ("identity_primary", "identity_support", "location")
_CANDIDATE_VARIATIONS = (
    (
        "Candidate 1 composition: use the centered baseline view. Keep her shoulders "
        "square to camera, direct eye contact, and both the near armrest and part of "
        "one rear wheel clearly readable."
    ),
    (
        "Candidate 2 composition: move the camera modestly to the actor's left for a "
        "clearly visible 10-degree right three-quarter view. Keep seated eye-level, "
        "the same camera distance and face size, direct eye contact, and the near "
        "armrest plus rear wheel clearly readable."
    ),
    (
        "Candidate 3 composition: move the camera modestly to the actor's right for a "
        "clearly visible 10-degree left three-quarter view. Keep seated eye-level, "
        "the same camera distance and face size, direct eye contact, and the near "
        "armrest plus rear wheel clearly readable."
    ),
)
_MAX_DIVERSITY_ATTEMPTS = 3
_PERCEPTUAL_HASH_WIDTH = 16
_PERCEPTUAL_HASH_HEIGHT = 16
_NEAR_DUPLICATE_HASH_DISTANCE = 8
_NEAR_DUPLICATE_MEAN_RGB_DELTA = 3.0
_PROVIDER_MAX_ATTEMPTS = 3
_TRANSIENT_PROVIDER_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_SCENE_PLATE_IMAGE_MAX_CONCURRENCY = max(
    1, int(os.environ.get("SEMANTIC_SCENE_PLATE_MAX_CONCURRENCY", "1"))
)
_SCENE_PLATE_SUCCESS_RAMP = max(
    1, int(os.environ.get("SEMANTIC_SCENE_PLATE_SUCCESS_RAMP", "3"))
)
_SCENE_PLATE_START_INTERVAL_SECONDS = max(
    0.0, float(os.environ.get("SEMANTIC_SCENE_PLATE_START_INTERVAL_SECONDS", "5"))
)
_SCENE_PLATE_THROTTLE_COOLDOWN_SECONDS = max(
    1.0, float(os.environ.get("SEMANTIC_SCENE_PLATE_THROTTLE_COOLDOWN_SECONDS", "30"))
)
_SCENE_PLATE_TRANSIENT_COOLDOWN_SECONDS = max(
    1.0, float(os.environ.get("SEMANTIC_SCENE_PLATE_TRANSIENT_COOLDOWN_SECONDS", "10"))
)
_SCENE_PLATE_BUNDLE_ENABLED = os.environ.get(
    "SEMANTIC_SCENE_PLATE_BUNDLE_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}


class _ScenePlateImageTrafficGate:
    """Fair, adaptive process-wide traffic shaping for expensive image calls."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._pending: list[tuple[object, str]] = []
        self._active = 0
        self._current_limit = 1
        self._healthy_successes = 0
        self._last_started_key = ""
        self._next_start_at = 0.0
        self._cooldown_until = 0.0
        self._adaptive_start_interval_seconds = (
            _SCENE_PLATE_START_INTERVAL_SECONDS
        )

    def _next_waiter_locked(self) -> Optional[object]:
        if not self._pending:
            return None
        pending_keys = list(dict.fromkeys(key for _, key in self._pending))
        if len(pending_keys) == 1:
            target_key = pending_keys[0]
        elif self._last_started_key in pending_keys:
            previous_index = pending_keys.index(self._last_started_key)
            target_key = pending_keys[(previous_index + 1) % len(pending_keys)]
        else:
            target_key = pending_keys[0]
        return next(
            waiter for waiter, key in self._pending if key == target_key
        )

    def acquire(self, traffic_key: str) -> None:
        waiter = object()
        normalized_key = str(traffic_key or "semantic-scene-plate")
        with self._condition:
            self._pending.append((waiter, normalized_key))
            while True:
                now = time.monotonic()
                ready_at = max(self._next_start_at, self._cooldown_until)
                if (
                    self._next_waiter_locked() is waiter
                    and self._active < self._current_limit
                    and now >= ready_at
                ):
                    self._pending = [
                        item for item in self._pending if item[0] is not waiter
                    ]
                    self._active += 1
                    self._last_started_key = normalized_key
                    self._next_start_at = (
                        now + self._adaptive_start_interval_seconds
                    )
                    self._condition.notify_all()
                    return
                timeout = max(0.05, min(1.0, ready_at - now))
                self._condition.wait(timeout=timeout)

    def release(self, *, succeeded: bool, status_code: Optional[int]) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            now = time.monotonic()
            if succeeded:
                self._healthy_successes += 1
                if (
                    self._healthy_successes >= _SCENE_PLATE_SUCCESS_RAMP
                    and self._current_limit < _SCENE_PLATE_IMAGE_MAX_CONCURRENCY
                ):
                    self._current_limit += 1
                    self._healthy_successes = 0
            else:
                self._healthy_successes = 0
                self._current_limit = 1
                if status_code == 429:
                    cooldown = _SCENE_PLATE_THROTTLE_COOLDOWN_SECONDS
                    self._adaptive_start_interval_seconds = max(
                        self._adaptive_start_interval_seconds,
                        min(
                            cooldown,
                            max(
                                15.0,
                                _SCENE_PLATE_START_INTERVAL_SECONDS * 3,
                            ),
                        ),
                    )
                elif status_code in _TRANSIENT_PROVIDER_STATUS_CODES:
                    cooldown = _SCENE_PLATE_TRANSIENT_COOLDOWN_SECONDS
                else:
                    cooldown = 0.0
                if cooldown:
                    self._cooldown_until = max(
                        self._cooldown_until,
                        now + cooldown + random.uniform(0.0, cooldown * 0.25),
                    )
            # Vertex DSQ can reject a new request that begins immediately after
            # a successful response. Preserve a quiet gap after completion,
            # not merely between request start timestamps. Once a 429 proves
            # the configured gap too small, retain the learned wider interval.
            self._next_start_at = max(
                self._next_start_at,
                now + self._adaptive_start_interval_seconds,
            )
            self._condition.notify_all()


_SCENE_PLATE_IMAGE_TRAFFIC_GATE = _ScenePlateImageTrafficGate()


@dataclass(frozen=True)
class ScenePlateCandidate:
    index: int
    image_bytes: bytes
    mime_type: str
    provider_model: str
    prompt: str


@dataclass(frozen=True)
class ScenePlateGenerationResult:
    candidates: Tuple[ScenePlateCandidate, ...]
    prompts: Tuple[str, ...]
    derivation_mode: str
    remaining_duplicate_candidate_indexes: Tuple[int, ...] = ()

    @property
    def diversity_recovery_exhausted(self) -> bool:
        return bool(self.remaining_duplicate_candidate_indexes)


def build_canonical_scene_plate_prompt(
    *,
    scene: str,
    wardrobe: str,
    variation_directive: str = "",
) -> str:
    return (
        "Create one photorealistic vertical start image using all three supplied images with fixed roles. "
        "Image 1 is the PRIMARY ACTOR IDENTITY reference. Image 2 is the SAME ACTOR from another view and "
        "is supporting identity evidence only. Image 3 is the ACTOR-FREE LOCATION reference. Place exactly "
        "the same adult woman from Images 1 and 2 inside Image 3. Preserve her exact facial geometry, "
        "hairline, hair, apparent age, body proportions, and ordinary camera-file skin texture with visible "
        "pores, natural tonal variation, natural under-eye and lip texture, mild facial asymmetry, and "
        "realistic hairline flyaways. Images 1 and 2 provide identity only: do not copy their clothing. "
        "Replace every visible upper-body garment from those references with the requested outfit below; "
        "its garment type and color must be visibly unmistakable. Do not average her into a new face. "
        "She is seated upright in a manual "
        "wheelchair. "
        f"{WHEELCHAIR_VISUAL_CONTRACT} {FRAMING_CONTRACT} "
        f"Her upper-body outfit is exactly: {wardrobe}. The location is exactly: {scene}. "
        "Her hands and wheelchair geometry are physically plausible. Use natural available light and a "
        "quiet conversational expression immediately before speaking, with her mouth closed. Render no "
        "other person, text, logo, watermark, mobility device, standing pose, walking pose, beauty "
        "retouching, poreless skin, glamour lighting, CGI smoothness, face averaging, camera tilt, wide "
        "shot, full-body shot, or cropped-out wheelchair. "
        f"{variation_directive}"
    )


def build_derived_scene_plate_prompt(
    *,
    scene: str,
    wardrobe: str,
    variation_directive: str = "",
) -> str:
    return (
        "Create one photorealistic vertical start image using all three supplied images with fixed roles. "
        "Image 1 is the canonical scene plate and is the authoritative source for the exact woman, exact "
        "manual wheelchair, seated posture, facial geometry, and scale. "
        "Image 2 is the unchanged front identity reference for the same woman and exists only to prevent "
        "facial drift. Image 3 is the ACTOR-FREE LOCATION reference. Preserve the exact woman from Images 1 "
        "and 2 and preserve the exact manual wheelchair, seated pose, camera height, camera distance, and "
        "face size from Image 1. Image 1's clothing is not authoritative: replace every visible upper-body "
        "garment with the requested outfit below, making its garment type and color visibly unmistakable. "
        "Apply only the modest candidate-specific horizontal viewpoint and shoulder "
        "angle described below; preserve the overall medium-close-up framing contract. "
        f"{WHEELCHAIR_VISUAL_CONTRACT} {FRAMING_CONTRACT} "
        f"Keep the actor-free location exactly: {scene}; and the upper-body outfit exactly: {wardrobe}. "
        "Keep her mouth closed with a quiet conversational expression. Preserve ordinary camera-file skin "
        "texture, visible pores, natural tonal variation, natural under-eye and lip texture, mild facial "
        "asymmetry, and realistic hairline flyaways under ordinary indoor optics and available light. Keep "
        "hands, wheelchair, and room perspective physically plausible. Render no other person, text, logo, "
        "watermark, standing pose, walking pose, wide shot, full-body shot, beauty retouching, poreless skin, "
        "glamour lighting, CGI smoothness, face averaging, camera movement, or cropped-out wheelchair."
        f" {variation_directive}"
    )


def _variation_directive(*, index: int, attempt: int) -> str:
    try:
        directive = _CANDIDATE_VARIATIONS[index - 1]
    except IndexError as exc:
        raise ValidationError(
            "Semantic scene-plate candidate index is outside the variation contract.",
            {"candidate_index": index},
        ) from exc
    if attempt <= 1:
        return directive
    return (
        f"{directive} DIVERSITY RECOVERY ATTEMPT {attempt}: the prior render was "
        "perceptually indistinguishable from another option. Make the specified "
        "left/right camera offset and shoulder angle unmistakably visible while "
        "preserving actor identity, wheelchair, outfit, room, face size, and crop."
    )


def _perceptual_signature(image_bytes: bytes) -> tuple[tuple[bool, ...], Image.Image] | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            rgb = image.convert("RGB")
            comparison = rgb.resize((64, 64))
            grayscale = rgb.convert("L").resize(
                (_PERCEPTUAL_HASH_WIDTH + 1, _PERCEPTUAL_HASH_HEIGHT)
            )
    except (OSError, UnidentifiedImageError):
        return None
    pixels = list(grayscale.getdata())
    row_width = _PERCEPTUAL_HASH_WIDTH + 1
    difference_hash = tuple(
        pixels[(row * row_width) + column]
        < pixels[(row * row_width) + column + 1]
        for row in range(_PERCEPTUAL_HASH_HEIGHT)
        for column in range(_PERCEPTUAL_HASH_WIDTH)
    )
    return difference_hash, comparison


def scene_plates_are_near_duplicates(first: bytes, second: bytes) -> bool:
    """Detect the same composition with only provider-level pixel variation."""
    first_signature = _perceptual_signature(first)
    second_signature = _perceptual_signature(second)
    if first_signature is None or second_signature is None:
        return False
    first_hash, first_image = first_signature
    second_hash, second_image = second_signature
    hash_distance = sum(
        first_bit != second_bit
        for first_bit, second_bit in zip(first_hash, second_hash)
    )
    difference = ImageChops.difference(first_image, second_image)
    mean_rgb_delta = sum(ImageStat.Stat(difference).mean) / 3
    return (
        hash_distance <= _NEAR_DUPLICATE_HASH_DISTANCE
        and mean_rgb_delta <= _NEAR_DUPLICATE_MEAN_RGB_DELTA
    )


def _duplicate_candidate_positions(
    candidates: Sequence[ScenePlateCandidate],
) -> tuple[int, ...]:
    duplicate_positions = []
    for candidate_position, candidate in enumerate(candidates):
        if any(
            scene_plates_are_near_duplicates(
                previous.image_bytes,
                candidate.image_bytes,
            )
            for previous in candidates[:candidate_position]
        ):
            duplicate_positions.append(candidate_position)
    return tuple(duplicate_positions)


def _is_retryable_provider_error(error: ThirdPartyError) -> bool:
    status_code = error.details.get("status_code")
    if status_code is None:
        # Transport failures and successful responses without image data do not
        # carry an HTTP status, but another render can safely resolve both.
        return True
    try:
        normalized_status = int(status_code)
    except (TypeError, ValueError):
        return True
    return normalized_status in _TRANSIENT_PROVIDER_STATUS_CODES


def generate_scene_plate(
    *,
    references: Sequence[ShotFrameReference],
    prompt: str,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
    image_size: str = "2K",
    traffic_key: Optional[str] = None,
) -> dict[str, Any]:
    if len(references) != 3 or tuple(item.role for item in references) != _REFERENCE_ROLES:
        raise ValidationError(
            "Scene-plate references must be explicit and ordered.",
            {
                "expected_roles": list(_REFERENCE_ROLES),
                "received_roles": [item.role for item in references],
            },
        )
    for reference in references:
        if not reference.mime_type.startswith("image/") or not reference.image_bytes:
            raise ValidationError(
                "Scene-plate references require non-empty image bytes.",
                {"role": reference.role, "mime_type": reference.mime_type},
            )
    normalized_prompt = " ".join(str(prompt or "").split())
    if not normalized_prompt:
        raise ValidationError("Scene-plate generation requires a prompt.")

    client = llm_client or get_llm_client()
    renderer_prompt = write_raw_camera_image_prompt(client=client, brief=normalized_prompt)
    def render() -> dict[str, Any]:
        return client.generate_gemini_image(
            prompt=renderer_prompt,
            model=image_model,
            temperature=0.2,
            aspect_ratio="9:16",
            image_size=image_size,
            input_images=[item.as_gemini_input() for item in references],
            # Scene-plate retries are coordinated by the adaptive traffic gate.
            # Disabling the adapter's nested retry prevents retry storms.
            provider_max_attempts=1,
        )
    if not traffic_key:
        return render()
    _SCENE_PLATE_IMAGE_TRAFFIC_GATE.acquire(traffic_key)
    try:
        result = render()
    except ThirdPartyError as exc:
        status_code = exc.details.get("status_code")
        try:
            normalized_status = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            normalized_status = None
        _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(
            succeeded=False,
            status_code=normalized_status,
        )
        raise
    except Exception:
        _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(
            succeeded=False,
            status_code=None,
        )
        raise
    _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(succeeded=True, status_code=None)
    return result


def generate_scene_plate_bundle(
    *,
    references: Sequence[ShotFrameReference],
    prompts: Sequence[str],
    llm_client: Any,
    image_model: str,
    image_size: str,
    traffic_key: Optional[str],
) -> list[dict[str, Any]]:
    """Render multiple standalone plates in one provider round trip."""
    if len(prompts) < 2:
        raise ValidationError("Scene-plate bundles require at least two prompts.")
    output_contract = (
        f"Create exactly {len(prompts)} SEPARATE standalone output images in this one "
        "response, in the numbered order below. Do not create a collage, contact sheet, "
        "grid, split screen, triptych, or borders. Every output must be a complete "
        "independent vertical 9:16 photograph at the requested resolution. Keep the exact "
        "same actor identity, wheelchair, wardrobe, and location across all outputs while "
        "applying the numbered camera variation for each image.\n\n"
        + "\n\n".join(
            f"OUTPUT IMAGE {index}:\n{prompt}"
            for index, prompt in enumerate(prompts, start=1)
        )
        + f"\n\nReturn exactly {len(prompts)} separate images and no combined image."
    )
    renderer_prompt = write_raw_camera_image_prompt(
        client=llm_client,
        brief=output_contract,
    )
    renderer_prompt = (
        f"{renderer_prompt}\n\nOUTPUT FORMAT REQUIREMENT: Return exactly "
        f"{len(prompts)} separate image parts in numbered order. Never combine them."
    )

    def render() -> dict[str, Any]:
        return llm_client.generate_gemini_images(
            prompt=renderer_prompt,
            model=image_model,
            temperature=0.2,
            aspect_ratio="9:16",
            image_size=image_size,
            input_images=[item.as_gemini_input() for item in references],
            provider_max_attempts=1,
        )

    if traffic_key:
        _SCENE_PLATE_IMAGE_TRAFFIC_GATE.acquire(traffic_key)
    try:
        generated = render()
    except ThirdPartyError as exc:
        if traffic_key:
            status_code = exc.details.get("status_code")
            try:
                normalized_status = int(status_code) if status_code is not None else None
            except (TypeError, ValueError):
                normalized_status = None
            _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(
                succeeded=False,
                status_code=normalized_status,
            )
        raise
    except Exception:
        if traffic_key:
            _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(
                succeeded=False,
                status_code=None,
            )
        raise
    if traffic_key:
        _SCENE_PLATE_IMAGE_TRAFFIC_GATE.release(succeeded=True, status_code=None)

    images = generated.get("images")
    if not isinstance(images, list) or not images:
        raise ThirdPartyError(
            "Gemini scene-plate bundle returned no images.",
            {"model": image_model},
        )
    return [dict(image) for image in images[: len(prompts)]]


def _as_role(reference: ShotFrameReference, role: str) -> ShotFrameReference:
    return ShotFrameReference(
        role=role,
        mime_type=reference.mime_type,
        image_bytes=reference.image_bytes,
    )


def generate_scene_plate_candidates(
    *,
    actor_references: Sequence[ShotFrameReference],
    location_reference: ShotFrameReference,
    canonical_scene_plate: Optional[ShotFrameReference] = None,
    scene: str,
    wardrobe: str,
    candidate_count: int = 3,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
    image_size: str = "2K",
    traffic_key: Optional[str] = None,
    initial_candidates: Sequence[ScenePlateCandidate] = (),
    candidate_ready_callback: Optional[Callable[[ScenePlateCandidate], None]] = None,
    progress_callback: Optional[
        Callable[[str, Mapping[str, Any]], None]
    ] = None,
) -> ScenePlateGenerationResult:
    """Generate three independent plates, or three derivatives from an approved anchor."""
    if len(actor_references) != 2:
        raise ValidationError(
            "Scene-plate generation requires exactly two immutable actor references.",
            {"actor_reference_count": len(actor_references)},
        )
    if candidate_count != 3:
        raise ValidationError("Semantic scene-plate generation requires exactly three candidates.")
    expected_roles = ("actor_front", "actor_three_quarter")
    if tuple(reference.role for reference in actor_references) != expected_roles:
        raise ValidationError(
            "Scene-plate actor references must remain actor_front then actor_three_quarter."
        )
    if location_reference.role != "location":
        raise ValidationError("Scene-plate location reference must use the location role.")

    client = llm_client or get_llm_client()
    if canonical_scene_plate is not None:
        if canonical_scene_plate.role != "canonical_scene_plate":
            raise ValidationError(
                "Established semantic scene plate must use the canonical_scene_plate role."
            )
        candidates = []
        canonical_reference = _as_role(canonical_scene_plate, "identity_primary")
        start_index = 1
        derivation_mode = "canonical_anchor"
    else:
        candidates = []
        canonical_reference = None
        start_index = 1
        derivation_mode = "bootstrap"
    def build_candidate_request(
        specification: tuple[int, int],
    ) -> tuple[tuple[ShotFrameReference, ...], str]:
        index, attempt = specification
        variation_directive = _variation_directive(index=index, attempt=attempt)
        if derivation_mode == "bootstrap":
            references = (
                _as_role(actor_references[0], "identity_primary"),
                _as_role(actor_references[1], "identity_support"),
                _as_role(location_reference, "location"),
            )
            prompt = build_canonical_scene_plate_prompt(
                scene=scene,
                wardrobe=wardrobe,
                variation_directive=variation_directive,
            )
        else:
            references = (
                canonical_reference,
                _as_role(actor_references[0], "identity_support"),
                _as_role(location_reference, "location"),
            )
            prompt = build_derived_scene_plate_prompt(
                scene=scene,
                wardrobe=wardrobe,
                variation_directive=variation_directive,
            )
        return references, prompt

    def generate_candidate_once(
        specification: tuple[int, int],
    ) -> ScenePlateCandidate:
        index, _attempt = specification
        references, prompt = build_candidate_request(specification)
        generated = generate_scene_plate(
            references=references,
            prompt=prompt,
            llm_client=client,
            image_model=image_model,
            image_size=image_size,
            traffic_key=traffic_key,
        )
        candidate = ScenePlateCandidate(
            index=index,
            image_bytes=generated["image_bytes"],
            mime_type=str(generated["mime_type"]),
            provider_model=str(generated["model"]),
            prompt=prompt,
        )
        return candidate

    def report_progress(phase: str, **details: Any) -> None:
        if progress_callback is not None:
            progress_callback(phase, details)

    def generate_candidate(
        specification: tuple[int, int],
    ) -> ScenePlateCandidate:
        candidate_index, diversity_attempt = specification
        for provider_attempt in range(1, _PROVIDER_MAX_ATTEMPTS + 1):
            try:
                candidate = generate_candidate_once(specification)
            except ThirdPartyError as exc:
                if (
                    provider_attempt >= _PROVIDER_MAX_ATTEMPTS
                    or not _is_retryable_provider_error(exc)
                ):
                    raise
                report_progress(
                    "generating_images",
                    candidate_count=candidate_count,
                    candidate_index=candidate_index,
                    diversity_attempt=diversity_attempt,
                    provider_attempt=provider_attempt + 1,
                    retrying=True,
                )
                continue
            if candidate_ready_callback is not None:
                # Persistence is deliberately outside the provider retry loop:
                # a storage/checkpoint failure must not purchase another image.
                candidate_ready_callback(candidate)
            return candidate
        raise AssertionError("Scene-plate provider retry loop exhausted unexpectedly.")

    # Fresh sets use Gemini's ordered multi-image response so operator latency is
    # one provider wait. Partial resumes, omitted outputs, and diversity repair
    # retain the independently retryable single-image path.
    report_progress(
        "generating_images",
        candidate_count=candidate_count,
        completed_candidates=0,
    )
    initial_by_index = {candidate.index: candidate for candidate in initial_candidates}
    if (
        len(initial_by_index) != len(tuple(initial_candidates))
        or any(index < start_index or index > candidate_count for index in initial_by_index)
    ):
        raise ValidationError("Initial scene-plate candidates have invalid indexes.")
    missing_specifications = [
        (index, 1)
        for index in range(start_index, candidate_count + 1)
        if index not in initial_by_index
    ]
    generated_candidates: list[ScenePlateCandidate] = []
    if missing_specifications:
        can_bundle = (
            _SCENE_PLATE_BUNDLE_ENABLED
            and not initial_by_index
            and len(missing_specifications) == candidate_count
            and callable(getattr(client, "generate_gemini_images", None))
        )
        if can_bundle:
            bundle_requests = [
                build_candidate_request(specification)
                for specification in missing_specifications
            ]
            bundle_references = bundle_requests[0][0]
            bundle_prompts = [request[1] for request in bundle_requests]
            bundle_images: list[dict[str, Any]] = []
            for provider_attempt in range(1, _PROVIDER_MAX_ATTEMPTS + 1):
                try:
                    bundle_images = generate_scene_plate_bundle(
                        references=bundle_references,
                        prompts=bundle_prompts,
                        llm_client=client,
                        image_model=image_model,
                        image_size=image_size,
                        traffic_key=traffic_key,
                    )
                    break
                except ThirdPartyError as exc:
                    if (
                        provider_attempt >= _PROVIDER_MAX_ATTEMPTS
                        or not _is_retryable_provider_error(exc)
                    ):
                        report_progress(
                            "generating_images",
                            candidate_count=candidate_count,
                            bundle_fallback=True,
                        )
                        break
                    report_progress(
                        "generating_images",
                        candidate_count=candidate_count,
                        provider_attempt=provider_attempt + 1,
                        retrying=True,
                        bundled=True,
                    )
            generated_candidates = [
                ScenePlateCandidate(
                    index=missing_specifications[position][0],
                    image_bytes=image["image_bytes"],
                    mime_type=str(image["mime_type"]),
                    provider_model=str(image_model),
                    prompt=bundle_prompts[position],
                )
                for position, image in enumerate(bundle_images)
            ]
            callback_error: Optional[Exception] = None
            if candidate_ready_callback is not None:
                for candidate in generated_candidates:
                    try:
                        candidate_ready_callback(candidate)
                    except Exception as exc:  # noqa: BLE001
                        callback_error = callback_error or exc
            if callback_error is not None:
                raise callback_error

        bundled_indexes = {candidate.index for candidate in generated_candidates}
        remaining_specifications = [
            specification
            for specification in missing_specifications
            if specification[0] not in bundled_indexes
        ]
        if remaining_specifications:
            with ThreadPoolExecutor(
                max_workers=len(remaining_specifications),
                thread_name_prefix="semantic-scene-plate",
            ) as executor:
                generated_candidates.extend(
                    executor.map(generate_candidate, remaining_specifications)
                )
    candidates = [
        initial_by_index.get(index)
        or next(candidate for candidate in generated_candidates if candidate.index == index)
        for index in range(start_index, candidate_count + 1)
    ]
    report_progress(
        "checking_diversity",
        candidate_count=candidate_count,
        completed_candidates=candidate_count,
    )
    for attempt in range(2, _MAX_DIVERSITY_ATTEMPTS + 1):
        duplicate_positions = _duplicate_candidate_positions(candidates)
        if not duplicate_positions:
            break
        duplicate_indexes = [
            candidates[position].index for position in duplicate_positions
        ]
        report_progress(
            "regenerating_duplicates",
            attempt=attempt,
            candidate_count=candidate_count,
            duplicate_candidate_indexes=duplicate_indexes,
        )
        with ThreadPoolExecutor(
            max_workers=len(duplicate_positions),
            thread_name_prefix="semantic-scene-plate-diversity",
        ) as executor:
            replacements = list(
                executor.map(
                    generate_candidate,
                    (
                        (candidates[position].index, attempt)
                        for position in duplicate_positions
                    ),
                )
        )
        for position, replacement in zip(duplicate_positions, replacements):
            candidates[position] = replacement
        report_progress(
            "checking_diversity",
            attempt=attempt,
            candidate_count=candidate_count,
            completed_candidates=candidate_count,
        )
    remaining_duplicates = _duplicate_candidate_positions(candidates)
    remaining_duplicate_candidate_indexes = tuple(
        candidates[position].index for position in remaining_duplicates
    )
    return ScenePlateGenerationResult(
        candidates=tuple(candidates),
        prompts=tuple(candidate.prompt for candidate in candidates),
        derivation_mode=derivation_mode,
        remaining_duplicate_candidate_indexes=remaining_duplicate_candidate_indexes,
    )


__all__ = [
    "FRAMING_CONTRACT",
    "WHEELCHAIR_VISUAL_CONTRACT",
    "ScenePlateCandidate",
    "ScenePlateGenerationResult",
    "build_canonical_scene_plate_prompt",
    "build_derived_scene_plate_prompt",
    "generate_scene_plate",
    "generate_scene_plate_bundle",
    "generate_scene_plate_candidates",
    "scene_plates_are_near_duplicates",
]
