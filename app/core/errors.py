"""
Lippe Lift Studio Error Models and Exception Classes
Standard error envelopes per Canon § 5.
Per Constitution § II: Validated Boundaries
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum


class ErrorCode(str, Enum):
    """Standard error codes per Canon § 5.3"""
    AUTH_FAIL = "auth_fail"
    VALIDATION_ERROR = "validation_error"
    STATE_TRANSITION_ERROR = "state_transition_error"
    THIRD_PARTY_FAIL = "third_party_fail"
    RATE_LIMIT = "rate_limit"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class ErrorResponse(BaseModel):
    """Standard error envelope per Canon § 5.1"""
    ok: bool = Field(default=False, description="Always false for errors")
    status: int = Field(..., description="HTTP status code")
    code: ErrorCode = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error context")


class SuccessResponse(BaseModel):
    """Standard success envelope per Canon § 5.2"""
    ok: bool = Field(default=True, description="Always true for success")
    data: Any = Field(..., description="Response payload")


# Custom Exception Classes

class FlowForgeException(Exception):
    """Base exception for all Lippe Lift Studio errors."""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)
    
    def to_response(self) -> ErrorResponse:
        """Convert exception to error response model."""
        return ErrorResponse(
            status=self.status_code,
            code=self.code,
            message=self.message,
            details=self.details
        )


def error_code_for_status(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.AUTH_FAIL
    if status_code == 404:
        return ErrorCode.NOT_FOUND
    if status_code == 409:
        return ErrorCode.STATE_TRANSITION_ERROR
    if status_code == 422:
        return ErrorCode.VALIDATION_ERROR
    if status_code == 429:
        return ErrorCode.RATE_LIMIT
    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.VALIDATION_ERROR


class AuthenticationError(FlowForgeException):
    """Authentication failed (401)."""
    
    def __init__(self, message: str = "Authentication failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.AUTH_FAIL,
            message=message,
            details=details,
            status_code=401
        )


class ValidationError(FlowForgeException):
    """Input validation failed (422)."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
            status_code=422
        )


class StateTransitionError(FlowForgeException):
    """Invalid state transition (409)."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.STATE_TRANSITION_ERROR,
            message=message,
            details=details,
            status_code=409
        )


class ThirdPartyError(FlowForgeException):
    """External service failed (503)."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.THIRD_PARTY_FAIL,
            message=message,
            details=details,
            status_code=503
        )


class RateLimitError(FlowForgeException):
    """Rate limit exceeded (429)."""
    
    def __init__(self, message: str = "Rate limit exceeded", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.RATE_LIMIT,
            message=message,
            details=details,
            status_code=429
        )


class NotFoundError(FlowForgeException):
    """Resource not found (404)."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.NOT_FOUND,
            message=message,
            details=details,
            status_code=404
        )


class IdempotencyConflictError(FlowForgeException):
    """Idempotency key conflict (409)."""
    
    def __init__(self, message: str = "Idempotency key conflict", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=ErrorCode.IDEMPOTENCY_CONFLICT,
            message=message,
            details=details,
            status_code=409
        )
