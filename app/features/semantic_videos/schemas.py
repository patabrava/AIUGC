"""HTTP contracts for persisted Semantic UGC planning and approvals."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    base_seed: int = Field(default=240713, ge=0)
    resolution: str = Field(default="1080p", min_length=1, max_length=32)


class PlanTakeResponse(BaseModel):
    take_index: int
    attempt: int
    beat_text: str
    provider_duration_seconds: int
    request_hash: str
    submission_state: str


class PlanResponse(BaseModel):
    run_id: str
    revision: int
    stage: str
    plan_hash: str
    requested_duration_seconds: int
    take_count: int
    billable_provider_seconds: int
    quota_units: int
    price_per_provider_second_usd: str
    estimated_cost_usd: str
    takes: list[PlanTakeResponse]


class PlanApprovalRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    reason: Optional[str] = Field(default=None, max_length=500)


class ApprovalResponse(BaseModel):
    run_id: str
    revision: int
    stage: str
    approval_id: str
    contract_hash: str
    approved_take_indexes: list[int]
    approved_provider_seconds: int
    quota_units: int
    estimated_cost_usd: str


class ProgressTakeResponse(BaseModel):
    take_index: int
    attempt: int
    submission_state: str
    provider_duration_seconds: int
    request_hash: str
    transcript_passed: bool = False
    identity_passed: bool = False


class ProgressResponse(BaseModel):
    run_id: str
    revision: int
    stage: str
    plan_hash: Optional[str] = None
    total_takes: int
    generated_takes: int
    verified_takes: int
    failed_take_indexes: list[int]
    takes: list[ProgressTakeResponse]


class CandidateGenerationRequest(BaseModel):
    candidate_count: int = Field(default=3, ge=3, le=3)
    expected_revision: Optional[int] = Field(default=None, ge=0)


class CandidateResponse(BaseModel):
    index: int
    storage_uri: str
    storage_key: Optional[str] = None
    mime_type: str
    byte_length: int
    sha256: str
    provider_model: str
    visual_contract_hash: str
    actor_reference_fingerprint: str
    derivation_mode: Literal["bootstrap", "canonical_anchor"]
    canonical_anchor_id: Optional[str] = None
    canonical_anchor_sha256: Optional[str] = None
    canonical_anchor_source_run_id: Optional[str] = None


class CandidateGenerationResponse(BaseModel):
    run_id: str
    revision: int
    stage: str
    candidates: list[CandidateResponse]


class MasterApprovalRequest(BaseModel):
    candidate_index: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    reason: Optional[str] = Field(default=None, max_length=500)


class MasterApprovalResponse(BaseModel):
    run_id: str
    revision: int
    stage: str
    approval_id: str
    approved_candidate_index: int
    master_hash: str
    master_snapshot: dict[str, Any]


class RetryApprovalRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    failed_take_indexes: list[int] = Field(min_length=1)
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("failed_take_indexes")
    @classmethod
    def validate_indexes(cls, value: list[int]) -> list[int]:
        if any(isinstance(index, bool) or index < 0 for index in value):
            raise ValueError("Retry take indexes must be non-negative integers.")
        if len(set(value)) != len(value):
            raise ValueError("Retry take indexes must be unique.")
        return sorted(value)


class CancellationRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class CancellationResponse(BaseModel):
    run_id: str
    revision: int
    stage: Literal["failed"]
    cancelled_take_count: int
    reason: str


__all__ = [
    "ApprovalResponse",
    "CancellationRequest",
    "CancellationResponse",
    "CandidateGenerationRequest",
    "CandidateGenerationResponse",
    "CandidateResponse",
    "MasterApprovalRequest",
    "MasterApprovalResponse",
    "PlanApprovalRequest",
    "PlanCreateRequest",
    "PlanResponse",
    "PlanTakeResponse",
    "ProgressResponse",
    "ProgressTakeResponse",
    "RetryApprovalRequest",
]
