"""ebteqdesk-grok-mcp: Dedicated Ebteqdesk MCP server for Grok Bot support automation."""

__version__ = "0.1.0"

from ebteqdesk_grok_mcp.client import EbteqdeskClient
from ebteqdesk_grok_mcp.config import Settings, load_settings, resolve_upload_path
from ebteqdesk_grok_mcp.errors import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    EbteqdeskError,
    FileTooLargeError,
    NotFoundError,
    PermissionDeniedError,
    SecurityError,
    ValidationError,
)
from ebteqdesk_grok_mcp.server import create_server

__all__ = [
    "__version__",
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "EbteqdeskClient",
    "EbteqdeskError",
    "FileTooLargeError",
    "NotFoundError",
    "PermissionDeniedError",
    "SecurityError",
    "Settings",
    "ValidationError",
    "create_server",
    "load_settings",
    "resolve_upload_path",
]
