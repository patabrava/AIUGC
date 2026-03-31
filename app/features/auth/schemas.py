"""
FLOW-FORGE Auth Schemas
Pydantic models for OTP login request and verification.
"""

from pydantic import BaseModel, EmailStr, field_validator

from app.core.config import get_settings


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
        expected_length = get_settings().auth_otp_code_length
        if len(v) != expected_length:
            raise ValueError(f"Token must be exactly {expected_length} characters")
        return v
