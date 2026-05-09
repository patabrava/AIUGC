from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
