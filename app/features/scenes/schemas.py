from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CanonicalSceneAssetRecord(BaseModel):
    id: str
    scene_key: str
    scene_bible_version: int
    status: Literal["pending", "generated", "failed"]
    provider: Literal["vertex_gemini"]
    provider_model: Optional[str] = None
    system_prompt_name: str
    prompt_text: str
    aspect_ratio: str
    image_size: str
    image_url: Optional[str] = None
    storage_key: Optional[str] = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

