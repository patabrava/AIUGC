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
