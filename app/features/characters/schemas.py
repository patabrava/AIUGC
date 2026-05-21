from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


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


class ActorIdentityRecord(BaseModel):
    id: str
    name: str
    is_active: bool
    provider: str = "magnific"
    provider_lora_id: Optional[str] = None
    provider_lora_name: Optional[str] = None
    provider_training_task_id: Optional[str] = None
    training_status: str = "queued"
    training_phase: str = "queued"
    training_progress_percent: int = Field(default=0, ge=0, le=100)
    training_started_at: Optional[datetime] = None
    training_completed_at: Optional[datetime] = None
    training_error: Optional[str] = None
    training_images: List[str] = Field(default_factory=list)
    consent_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime
