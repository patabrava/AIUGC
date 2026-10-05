"""Request models for Runway Seedance 2.5 evaluations."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, AliasChoices, ConfigDict, Field


class RunwayEvaluationSubmitRequest(BaseModel):
    """One explicitly confirmed paid evaluation of one Semantic take."""

    model_config = ConfigDict(extra="forbid")

    take_index: int = Field(default=0, ge=0, le=63)
    resolution: Optional[Literal["480p", "720p", "1080p"]] = None
    confirm_estimated_credits: int = Field(..., gt=0, validation_alias=AliasChoices("confirm_estimated_units", "confirm_estimated_credits"), description="Must equal preview estimate units: ModelArk USD microdollars or Runway credits.")
