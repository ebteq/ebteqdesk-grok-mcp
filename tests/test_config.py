"""Tests for configuration parsing, settings validation, and upload path confinement."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ebteqdesk_grok_mcp.config import (
    DEFAULT_UPLOAD_ROOT,
    Settings,
    load_settings,
    parse_env_file,
    resolve_upload_path,
)
from ebteqdesk_grok_mcp.errors import ConfigurationError, SecurityError


def test_settings_valid(temp_upload_dir: Path) -> None:
    """Test valid settings instantiation and trailing slash stripping."""
    settings = Settings(
        base_url="https://help.example.com///",
        api_token="ebtd_example_123|secret",
        upload_root=temp_upload_dir,
    )
    assert settings.base_url == "https://help.example.com"
    assert settings.api_token == "ebtd_example_123|secret"
    assert settings.upload_root == temp_upload_dir
    assert settings.timeout == 30.0


def test_settings_invalid_url(temp_upload_dir: Path) -> None:
    """Test rejection of URLs that do not start with http:// or https://."""
    with pytest.raises(ValueError, match="Invalid base_url"):
        Settings(
            base_url="ftp://help.example.com",
            api_token="ebtd_example_123|secret",
            upload_root=temp_upload_dir,
        )


def test_settings_empty_token(temp_upload_dir: Path) -> None:
    """Test rejection of empty api_token."""
    with pytest.raises(ValueError, match="api_token cannot be empty"):
        Settings(
            base_url="https://help.example.com",
            api_token="   ",
            upload_root=temp_upload_dir,
        )


def test_parse_env_file(tmp_path: Path) -> None:
    """Test parsing .env file with comments, quotes, and whitespace."""
    env_file = tmp_path / "test.env"
    env_file.write_text(
        """
        # Leading comment
        EBTEQDESK_BASE_URL="https://help.example.com"
        EBTEQDESK_API_TOKEN='ebtd_token_123|sec'

        # Another comment
        EBTEQDESK_TIMEOUT=15.5
        INVALID_LINE_NO_EQUALS
        """,
        encoding="utf-8",
    )

    parsed = parse_env_file(env_file)
    assert parsed["EBTEQDESK_BASE_URL"] == "https://help.example.com"
    assert parsed["EBTEQDESK_API_TOKEN"] == "ebtd_token_123|sec"
    assert parsed["EBTEQDESK_TIMEOUT"] == "15.5"
    assert "INVALID_LINE_NO_EQUALS" not in parsed


def test_parse_env_file_nonexistent(tmp_path: Path) -> None:
    """Test parse_env_file returns empty dict when file does not exist."""
    assert parse_env_file(tmp_path / "does_not_exist.env") == {}


def test_load_settings_explicit(temp_upload_dir: Path) -> None:
    """Test load_settings with explicit arguments."""
    settings = load_settings(
        base_url="https://help.example.com",
        api_token="ebtd_token_123|sec",
        upload_root=temp_upload_dir,
        timeout=25.0,
    )
    assert settings.base_url == "https://help.example.com"
    assert settings.api_token == "ebtd_token_123|sec"
    assert settings.upload_root == temp_upload_dir
    assert settings.timeout == 25.0


def test_load_settings_from_env(monkeypatch: pytest.MonkeyPatch, temp_upload_dir: Path) -> None:
    """Test load_settings reading from environment variables."""
    monkeypatch.setenv("EBTEQDESK_BASE_URL", "https://help.example.com")
    monkeypatch.setenv("EBTEQDESK_API_TOKEN", "ebtd_env_token|123")
    monkeypatch.setenv("EBTEQDESK_UPLOAD_ROOT", str(temp_upload_dir))
    monkeypatch.setenv("EBTEQDESK_TIMEOUT", "45")

    settings = load_settings()
    assert settings.base_url == "https://help.example.com"
    assert settings.api_token == "ebtd_env_token|123"
    assert settings.upload_root == temp_upload_dir
    assert settings.timeout == 45.0


def test_load_settings_from_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test load_settings falling back to configuration file."""
    # Clear environment variables
    monkeypatch.delenv("EBTEQDESK_BASE_URL", raising=False)
    monkeypatch.delenv("EBTEQDESK_API_TOKEN", raising=False)
    monkeypatch.delenv("EBTEQDESK_UPLOAD_ROOT", raising=False)

    cfg = tmp_path / "config.env"
    cfg.write_text(
        f"EBTEQDESK_BASE_URL=https://help.example.com\n"
        f"EBTEQDESK_API_TOKEN=ebtd_file_token|456\n"
        f"EBTEQDESK_UPLOAD_ROOT={tmp_path / 'file_uploads'}\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path=cfg)
    assert settings.base_url == "https://help.example.com"
    assert settings.api_token == "ebtd_file_token|456"
    assert settings.upload_root == (tmp_path / "file_uploads").resolve()


def test_load_settings_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test load_settings raising ConfigurationError when mandatory keys are missing."""
    monkeypatch.delenv("EBTEQDESK_BASE_URL", raising=False)
    monkeypatch.delenv("EBTEQDESK_API_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="Missing EBTEQDESK_BASE_URL"):
        load_settings(config_path=tmp_path / "empty.env")

    with pytest.raises(ConfigurationError, match="Missing EBTEQDESK_API_TOKEN"):
        load_settings(base_url="https://help.example.com", config_path=tmp_path / "empty.env")


# =============================================================================
# Path Confinement & Security Tests
# =============================================================================

def test_resolve_upload_path_valid(temp_upload_dir: Path) -> None:
    """Test resolving a valid file inside upload_root."""
    file = temp_upload_dir / "document.pdf"
    file.write_text("dummy", encoding="utf-8")

    # Relative path
    resolved_rel = resolve_upload_path("document.pdf", temp_upload_dir)
    assert resolved_rel == file.resolve()

    # Absolute path
    resolved_abs = resolve_upload_path(str(file), temp_upload_dir)
    assert resolved_abs == file.resolve()


def test_resolve_upload_path_traversal_rejected(temp_upload_dir: Path, tmp_path: Path) -> None:
    """Test path traversal with '..' is rejected before opening."""
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    with pytest.raises(SecurityError, match="resolves outside allowed upload root"):
        resolve_upload_path("../secret.txt", temp_upload_dir)

    with pytest.raises(SecurityError, match="resolves outside allowed upload root"):
        resolve_upload_path(str(outside_file), temp_upload_dir)


def test_resolve_upload_path_escaping_symlink_rejected(temp_upload_dir: Path, tmp_path: Path) -> None:
    """Test a symlink inside upload_root pointing outside is rejected."""
    outside_target = tmp_path / "target_outside.txt"
    outside_target.write_text("external data", encoding="utf-8")

    evil_link = temp_upload_dir / "evil_symlink.txt"
    evil_link.symlink_to(outside_target)

    with pytest.raises(SecurityError, match="resolves outside allowed upload root"):
        resolve_upload_path("evil_symlink.txt", temp_upload_dir)


def test_resolve_upload_path_internal_symlink_allowed(temp_upload_dir: Path) -> None:
    """Test a symlink pointing to a file inside upload_root is allowed."""
    real_file = temp_upload_dir / "real.txt"
    real_file.write_text("internal data", encoding="utf-8")

    good_link = temp_upload_dir / "link_to_real.txt"
    good_link.symlink_to(real_file)

    resolved = resolve_upload_path("link_to_real.txt", temp_upload_dir)
    assert resolved == real_file.resolve()


def test_resolve_upload_path_nonexistent_file(temp_upload_dir: Path) -> None:
    """Test resolving a non-existent file inside upload_root raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="File not found"):
        resolve_upload_path("ghost.txt", temp_upload_dir)


def test_resolve_upload_path_empty_raises(temp_upload_dir: Path) -> None:
    """Test empty file_path raises ValueError."""
    with pytest.raises(ValueError, match="file_path cannot be empty"):
        resolve_upload_path("", temp_upload_dir)
