from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CharacterRecord(BaseModel):
    id: str
    name: str
    front_image_url: str
    three_quarter_image_url: str
    profile_image_url: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CharacterSnapshot(BaseModel):
    character_id: str
    name: str
    front_image_url: str
    three_quarter_image_url: str
    profile_image_url: str
    snapshotted_at: datetime


class ActorTrainingSet(BaseModel):
    images: list[str] = Field(min_length=8, max_length=20)
    consent_source: str = Field(default="", max_length=500)

    @field_validator("images")
    @classmethod
    def validate_public_urls(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("Training image URLs cannot be blank")
        if any(not item.startswith(("https://", "http://")) for item in cleaned):
            raise ValueError("Training images must be public URLs")
        return cleaned


class ActorIdentityRecord(BaseModel):
    id: str
    name: str
    is_active: bool
    provider: Literal["magnific"]
    provider_lora_id: Optional[str] = None
    provider_lora_name: Optional[str] = None
    provider_training_task_id: Optional[str] = None
    character_description: Optional[str] = None
    portrait_image_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    training_status: str
    training_phase: str
    training_progress_percent: int
    training_error: Optional[str] = None
    training_images: list[str]
    consent_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    training_started_at: Optional[datetime] = None
    training_completed_at: Optional[datetime] = None

    @property
    def primary_image_url(self) -> Optional[str]:
        for candidate in (
            self.portrait_image_url,
            self.cover_image_url,
            *(self.training_images[:1] if self.training_images else []),
        ):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return None


class IdentityGateResult(BaseModel):
    status: Literal["pending", "passed", "failed", "manual_required"]
    reason: str
    score: Optional[float] = None
    gate_type: Literal["manual", "automated", "unavailable"] = "manual"
    checked_at: Optional[datetime] = None
    details: dict[str, Any] = Field(default_factory=dict)


class SceneReferenceImageRecord(BaseModel):
    id: str
    actor_identity_id: str
    post_id: str
    scene_key: str
    wardrobe_key: str
    provider: Literal["magnific"]
    provider_task_id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    identity_gate_result: Optional[IdentityGateResult] = None
    status: str
    created_at: datetime
    updated_at: datetime


REQUIRED_SCENE_REFERENCE_ANGLE_KEYS = ("front_mid", "left_three_quarter", "right_profile")
VIDEO_ACTOR_REFERENCE_ANGLE_KEYS = ("front_mid", "left_three_quarter")


class SceneReferenceSetSummary(BaseModel):
    post_id: str
    reference_set_id: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    approved_rows: list[dict[str, Any]] = Field(default_factory=list)
    missing_angle_keys: list[str] = Field(default_factory=list)
    is_ready: bool = False
    video_actor_rows: list[dict[str, Any]] = Field(default_factory=list)
    missing_video_actor_angle_keys: list[str] = Field(default_factory=list)
    is_video_actor_ready: bool = False

    @classmethod
    def from_rows(
        cls,
        *,
        post_id: str,
        reference_set_id: str,
        rows: list[dict[str, Any]],
    ) -> "SceneReferenceSetSummary":
        approved_by_angle: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
            angle_key = str(metadata.get("angle_key") or "")
            gate = row.get("identity_gate_result") if isinstance(row.get("identity_gate_result"), dict) else {}
            gate_details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
            set_gate_passed = (
                gate_details.get("scene_consistency_set_approved") is True
                and str(gate_details.get("reference_set_id") or "") == reference_set_id
            )
            if row.get("status") == "approved" and row.get("image_url") and gate.get("status") == "passed" and set_gate_passed:
                approved_by_angle[angle_key] = row

        approved_rows = [
            approved_by_angle[key]
            for key in REQUIRED_SCENE_REFERENCE_ANGLE_KEYS
            if key in approved_by_angle
        ]
        missing = [key for key in REQUIRED_SCENE_REFERENCE_ANGLE_KEYS if key not in approved_by_angle]
        video_actor_rows = [
            approved_by_angle[key]
            for key in VIDEO_ACTOR_REFERENCE_ANGLE_KEYS
            if key in approved_by_angle
        ]
        missing_video_actor = [key for key in VIDEO_ACTOR_REFERENCE_ANGLE_KEYS if key not in approved_by_angle]
        return cls(
            post_id=post_id,
            reference_set_id=reference_set_id,
            rows=rows,
            approved_rows=approved_rows,
            missing_angle_keys=missing,
            is_ready=len(missing) == 0 and len(approved_rows) == len(REQUIRED_SCENE_REFERENCE_ANGLE_KEYS),
            video_actor_rows=video_actor_rows,
            missing_video_actor_angle_keys=missing_video_actor,
            is_video_actor_ready=(
                len(missing_video_actor) == 0
                and len(video_actor_rows) == len(VIDEO_ACTOR_REFERENCE_ANGLE_KEYS)
            ),
        )
