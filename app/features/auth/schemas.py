"""
FLOW-FORGE Auth Schemas
Pydantic models for OTP login request and verification.
"""

from pydantic import BaseModel, EmailStr, field_validator


class OTPRequestSchema(BaseModel):
    """Schema for requesting an OTP email."""
    email: EmailStr


class OTPVerifySchema(BaseModel):
    """Schema for verifying an OTP token."""
    email: EmailStr
    token: str

    @field_validator("token")
    @classmethod
    def validate_token_length(cls, v: str) -> str:
        if len(v) < 6 or len(v) > 6:
            raise ValueError("Token must be exactly 6 characters")
        return v
