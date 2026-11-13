"""Tests for CLI setup, check, and serve commands."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import respx

from ebteqdesk_grok_mcp.cli import main, run_check
from ebteqdesk_grok_mcp.config import Settings


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --help exits cleanly."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_run_check_success(sample_settings: Settings) -> None:
    """Test run_check succeeds with all required scopes."""
    async with respx.mock(base_url="https://help.example.com") as respx_mock:
        respx_mock.get("/api/v1/user").respond(
            200,
            json={
                "data": {
                    "id": 1,
                    "name": "Grok Bot",
                    "apiKey": {
                        "scopes": ["ticket:read", "ticket:write", "todo:read", "todo:write"],
                    },
                }
            },
        )

        exit_code = await run_check(sample_settings)
        assert exit_code == 0


@pytest.mark.asyncio
async def test_run_check_wildcard_scope(sample_settings: Settings) -> None:
    """Test run_check succeeds when token possesses wildcard '*' scope."""
    async with respx.mock(base_url="https://help.example.com") as respx_mock:
        respx_mock.get("/api/v1/user").respond(
            200,
            json={
                "data": {
                    "id": 1,
                    "name": "Admin Token",
                    "apiKey": {
                        "scopes": ["*"],
                    },
                }
            },
        )

        exit_code = await run_check(sample_settings)
        assert exit_code == 0


@pytest.mark.asyncio
async def test_run_check_missing_scopes(sample_settings: Settings) -> None:
    """Test run_check returns 1 when required scopes are missing."""
    async with respx.mock(base_url="https://help.example.com") as respx_mock:
        respx_mock.get("/api/v1/user").respond(
            200,
            json={
                "data": {
                    "id": 1,
                    "name": "Grok Bot",
                    "apiKey": {
                        "scopes": ["ticket:read"],  # missing ticket:write, todo:read, todo:write
                    },
                }
            },
        )

        exit_code = await run_check(sample_settings)
        assert exit_code == 1


@pytest.mark.asyncio
async def test_run_check_auth_failure(sample_settings: Settings) -> None:
    """Test run_check returns 1 when token is invalid (401)."""
    async with respx.mock(base_url="https://help.example.com") as respx_mock:
        respx_mock.get("/api/v1/user").respond(401, json={"error": "Unauthenticated."})

        exit_code = await run_check(sample_settings)
        assert exit_code == 1


def test_cmd_setup_permissions_and_config(tmp_path: Path) -> None:
    """Test setup command creates 0700 upload dir, 0600 config file, and verifies."""
    upload_root = tmp_path / "custom_uploads"
    config_out = tmp_path / "sub_config" / "config.env"

    args = [
        "setup",
        "--base-url",
        "https://help.example.com",
        "--api-token",
        "ebtd_test_key|secret'with$quote",
        "--upload-root",
        str(upload_root),
        "--config-out",
        str(config_out),
    ]

    with patch("ebteqdesk_grok_mcp.cli.run_check", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = 0
        exit_code = main(args)
        assert exit_code == 0

    # Verify upload directory permissions (0700)
    assert upload_root.is_dir()
    upload_mode = stat.S_IMODE(upload_root.stat().st_mode)
    assert upload_mode == 0o700

    # Verify config directory permissions (0700)
    assert config_out.parent.is_dir()
    cfg_dir_mode = stat.S_IMODE(config_out.parent.stat().st_mode)
    assert cfg_dir_mode == 0o700

    # Verify config file permissions (0600)
    assert config_out.is_file()
    cfg_mode = stat.S_IMODE(config_out.stat().st_mode)
    assert cfg_mode == 0o600

    # Verify config content safely quoted
    content = config_out.read_text(encoding="utf-8")
    assert "EBTEQDESK_BASE_URL=https://help.example.com" in content
    assert "EBTEQDESK_UPLOAD_ROOT=" in content
    assert "EBTEQDESK_API_TOKEN=" in content


def test_cmd_check_cli(tmp_path: Path) -> None:
    """Test check CLI subcommand reading from config file."""
    cfg = tmp_path / "test.env"
    cfg.write_text(
        "EBTEQDESK_BASE_URL=https://help.example.com\n"
        "EBTEQDESK_API_TOKEN=ebtd_check_token|123\n",
        encoding="utf-8",
    )

    with patch("ebteqdesk_grok_mcp.cli.run_check", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = 0
        ret = main(["check", "--config", str(cfg)])
        assert ret == 0
        assert mock_check.called


def test_cmd_serve_default() -> None:
    """Test default command invokes serve."""
    with patch("ebteqdesk_grok_mcp.cli.load_settings") as mock_load, \
         patch("ebteqdesk_grok_mcp.cli.create_server") as mock_server_factory:

        mock_server = mock_server_factory.return_value
        mock_server.run.return_value = None

        ret = main(["serve"])
        assert ret == 0
        assert mock_load.called
        assert mock_server.run.called
