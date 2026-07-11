"""Derive deterministic, non-generative crops from one approved PNG master."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import math
import re
from typing import Tuple

from PIL import Image, UnidentifiedImageError

from app.core.errors import ValidationError


_CROP_ZOOM = 1.05
_PNG_MIME_TYPE = "image/png"
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_TARGET_ASPECT_RATIO = 9 / 16
_ASPECT_RATIO_RELATIVE_TOLERANCE = 0.01


@dataclass(frozen=True)
class ShotVariant:
    index: int
    name: str
    source_sha256: str
    output_sha256: str
    crop_box: Tuple[int, int, int, int]
    width: int
    height: int
    mime_type: str
    image_bytes: bytes


def _load_approved_png(
    *,
    approved_master_bytes: bytes,
    expected_sha256: str,
    mime_type: str,
) -> Tuple[Image.Image, str]:
    if not isinstance(approved_master_bytes, bytes) or not approved_master_bytes:
        raise ValidationError("Approved shot master requires non-empty PNG bytes.")
    if str(mime_type or "").strip().lower() != _PNG_MIME_TYPE:
        raise ValidationError(
            "Approved shot master requires the image/png PNG MIME type.",
            {"mime_type": mime_type},
        )
    if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(expected_sha256):
        raise ValidationError("Approved shot master requires a valid expected SHA-256 hash.")

    source_sha256 = sha256(approved_master_bytes).hexdigest()
    if source_sha256 != expected_sha256.lower():
        raise ValidationError(
            "Approved shot master SHA-256 does not match the approved hash.",
            {"expected_sha256": expected_sha256.lower(), "actual_sha256": source_sha256},
        )

    try:
        with Image.open(io.BytesIO(approved_master_bytes)) as source:
            if source.format != "PNG":
                raise ValidationError(
                    "Approved shot master bytes must contain a valid PNG image.",
                    {"detected_format": source.format},
                )
            source.load()
            image = source.copy()
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError(
            "Approved shot master bytes must contain a valid PNG image.",
            {"error": str(exc)},
        ) from exc

    width, height = image.size
    if width <= 0 or height <= 0 or height <= width:
        raise ValidationError(
            "Approved shot master must be a vertical image.",
            {"width": width, "height": height},
        )
    aspect_ratio = width / height
    relative_ratio_error = abs(aspect_ratio - _TARGET_ASPECT_RATIO) / _TARGET_ASPECT_RATIO
    if relative_ratio_error > _ASPECT_RATIO_RELATIVE_TOLERANCE:
        raise ValidationError(
            "Approved shot master must use a 9:16 aspect ratio.",
            {
                "width": width,
                "height": height,
                "aspect_ratio": aspect_ratio,
                "relative_tolerance": _ASPECT_RATIO_RELATIVE_TOLERANCE,
            },
        )
    return image, source_sha256


def _encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def derive_shot_deck(
    *,
    approved_master_bytes: bytes,
    expected_sha256: str,
    mime_type: str,
) -> Tuple[ShotVariant, ShotVariant, ShotVariant, ShotVariant]:
    """Return the approved original plus restrained center, left, and right crops."""
    master, source_sha256 = _load_approved_png(
        approved_master_bytes=approved_master_bytes,
        expected_sha256=expected_sha256,
        mime_type=mime_type,
    )
    width, height = master.size

    crop_width = min(width, math.ceil(width / _CROP_ZOOM))
    crop_height = min(height, math.ceil(height / _CROP_ZOOM))
    centered_top = (height - crop_height) // 2
    centered_left = (width - crop_width) // 2
    crop_boxes = (
        (0, 0, width, height),
        (centered_left, centered_top, centered_left + crop_width, centered_top + crop_height),
        (0, centered_top, crop_width, centered_top + crop_height),
        (width - crop_width, centered_top, width, centered_top + crop_height),
    )
    names = ("original", "center", "left", "right")

    variants = []
    for index, (name, crop_box) in enumerate(zip(names, crop_boxes)):
        if index == 0:
            image_bytes = approved_master_bytes
        else:
            cropped = master.crop(crop_box)
            resized = cropped.resize((width, height), Image.Resampling.LANCZOS)
            image_bytes = _encode_png(resized)
        variants.append(
            ShotVariant(
                index=index,
                name=name,
                source_sha256=source_sha256,
                output_sha256=sha256(image_bytes).hexdigest(),
                crop_box=crop_box,
                width=width,
                height=height,
                mime_type=_PNG_MIME_TYPE,
                image_bytes=image_bytes,
            )
        )

    return tuple(variants)  # type: ignore[return-value]


__all__ = ["ShotVariant", "derive_shot_deck"]
