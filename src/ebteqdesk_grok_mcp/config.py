"""Configuration management and upload path confinement for ebteqdesk-grok-mcp."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ebteqdesk_grok_mcp.errors import ConfigurationError, SecurityError

DEFAULT_CONFIG_PATH = Path.home() / ".ebteqdesk-grok" / "config.env"
DEFAULT_UPLOAD_ROOT = Path.home() / ".ebteqdesk-grok" / "uploads"


class Settings(BaseModel):
    """Configuration settings for Ebteqdesk MCP server and client."""

    base_url: str
    api_token: str
    upload_root: Path = Field(default_factory=lambda: DEFAULT_UPLOAD_ROOT)
    timeout: float = 30.0

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        url = v.strip().rstrip("/")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"Invalid base_url: '{v}'. Must start with http:// or https://")
        return url

    @field_validator("api_token")
    @classmethod
    def validate_api_token(cls, v: str) -> str:
        token = v.strip()
        if not token:
            raise ValueError("api_token cannot be empty")
        return token

    @field_validator("upload_root", mode="before")
    @classmethod
    def validate_upload_root(cls, v: Any) -> Path:
        if isinstance(v, str):
            p = Path(os.path.expanduser(v.strip()))
        elif isinstance(v, Path):
            p = Path(os.path.expanduser(str(v)))
        else:
            p = DEFAULT_UPLOAD_ROOT
        return p


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE configuration file."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env

    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return env

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            env[key] = val
    return env


def load_settings(
    base_url: str | None = None,
    api_token: str | None = None,
    upload_root: str | Path | None = None,
    timeout: float | None = None,
    config_path: Path | None = None,
) -> Settings:
    """Load settings from explicitly passed values, environment variables, or config file."""
    # Check config file location
    cfg_file = config_path or Path(os.getenv("EBTEQDESK_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))).expanduser()
    file_env = parse_env_file(cfg_file)

    # Resolution precedence: explicit arg -> os.environ -> config.env
    resolved_base_url = (
        base_url
        or os.getenv("EBTEQDESK_BASE_URL")
        or file_env.get("EBTEQDESK_BASE_URL")
    )
    resolved_api_token = (
        api_token
        or os.getenv("EBTEQDESK_API_TOKEN")
        or file_env.get("EBTEQDESK_API_TOKEN")
    )
    resolved_upload_root = (
        upload_root
        or os.getenv("EBTEQDESK_UPLOAD_ROOT")
        or file_env.get("EBTEQDESK_UPLOAD_ROOT")
        or DEFAULT_UPLOAD_ROOT
    )

    env_timeout = os.getenv("EBTEQDESK_TIMEOUT") or file_env.get("EBTEQDESK_TIMEOUT")
    resolved_timeout = timeout if timeout is not None else (float(env_timeout) if env_timeout else 30.0)

    if not resolved_base_url:
        raise ConfigurationError(
            "Missing EBTEQDESK_BASE_URL. Provide via argument, environment variable, "
            f"or configuration file at {cfg_file}."
        )

    if not resolved_api_token:
        raise ConfigurationError(
            "Missing EBTEQDESK_API_TOKEN. Provide via argument, environment variable, "
            f"or configuration file at {cfg_file}."
        )

    try:
        return Settings(
            base_url=resolved_base_url,
            api_token=resolved_api_token,
            upload_root=resolved_upload_root,
            timeout=resolved_timeout,
        )
    except Exception as e:
        raise ConfigurationError(f"Invalid configuration: {e}") from e


def resolve_upload_path(file_path: str | Path, upload_root: Path) -> Path:
    """Confine and resolve an upload file path strictly within upload_root.

    Rejects directory traversal ('..') and escaping symlinks BEFORE opening the file.
    Raises SecurityError if the resolved path escapes upload_root.
    Raises FileNotFoundError if the file does not exist.
    """
    if not file_path:
        raise ValueError("file_path cannot be empty")

    root = upload_root.expanduser().resolve()
    raw = Path(file_path)

    # Fast-check on parts for obvious traversal
    if ".." in raw.parts:
        # Check if even after potential resolution it tries to escape
        pass

    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (root / raw).resolve()

    # Path confinement check: target must be inside root
    try:
        target.relative_to(root)
    except ValueError:
        raise SecurityError(
            f"Access denied: path '{file_path}' resolves outside allowed upload root '{root}'."
        )

    if not target.is_file():
        raise FileNotFoundError(f"File not found: '{file_path}' (resolved: '{target}')")

    return target
