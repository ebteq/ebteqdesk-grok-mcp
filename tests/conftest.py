"""Pytest fixtures and configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

from ebteqdesk_grok_mcp.config import Settings


@pytest.fixture
def temp_upload_dir(tmp_path: Path) -> Path:
    """Create a temporary upload root directory with 0700 permissions."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.chmod(0o700)
    return upload_dir


@pytest.fixture
def sample_settings(temp_upload_dir: Path) -> Settings:
    """Provide valid test Settings using safe placeholder URL and dummy token."""
    return Settings(
        base_url="https://help.example.com",
        api_token="ebtd_example_123|secret",
        upload_root=temp_upload_dir,
        timeout=10.0,
    )


@pytest.fixture
def sample_file(temp_upload_dir: Path) -> Path:
    """Create a test file inside the safe upload root."""
    file_path = temp_upload_dir / "test_doc.txt"
    file_path.write_text("Test document content", encoding="utf-8")
    return file_path
