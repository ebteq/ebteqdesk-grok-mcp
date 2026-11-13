"""Exception hierarchy for ebteqdesk-grok-mcp."""

from __future__ import annotations

from typing import Any


class EbteqdeskError(Exception):
    """Base exception for all Ebteqdesk Grok MCP server errors."""


class ConfigurationError(EbteqdeskError):
    """Raised when server or client configuration is invalid or missing."""


class SecurityError(EbteqdeskError):
    """Raised when an operation violates security boundaries (e.g. path traversal)."""


class FileTooLargeError(EbteqdeskError):
    """Raised when a file exceeds allowed upload size limits."""


class APIError(EbteqdeskError):
    """Base exception for API HTTP response errors."""

    def __init__(
        self,
        message: str,
        status_code: int,
        reason: str | None = None,
        errors: dict[str, list[str]] | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reason = reason
        self.errors = errors or {}
        self.response_body = response_body

    def __str__(self) -> str:
        parts = [f"[{self.status_code}] {self.message}"]
        if self.reason:
            parts.append(f"(reason: {self.reason})")
        if self.errors:
            parts.append(f"errors: {self.errors}")
        return " ".join(parts)


class AuthenticationError(APIError):
    """HTTP 401 Unauthorized."""


class PermissionDeniedError(APIError):
    """HTTP 403 Forbidden."""


class NotFoundError(APIError):
    """HTTP 404 Not Found."""


class ValidationError(APIError):
    """HTTP 422 Unprocessable Entity."""


class ConflictError(APIError):
    """HTTP 409 Conflict."""
