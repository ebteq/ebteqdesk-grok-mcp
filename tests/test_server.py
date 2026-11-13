"""Tests for FastMCP server: 14 tools, untrusted content wrapping, upload security, and image downscaling."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image as PILImage

try:
    from mcp.server.fastmcp import Image
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import Image

from ebteqdesk_grok_mcp.client import EbteqdeskClient
from ebteqdesk_grok_mcp.config import Settings
from ebteqdesk_grok_mcp.errors import SecurityError
from ebteqdesk_grok_mcp.server import (
    UNTRUSTED_NOTICE,
    create_server,
    process_attachment_image,
    sanitize_untrusted_content,
    wrap_untrusted,
)

EXPECTED_14_TOOLS = {
    "whoami",
    "search_contacts",
    "get_ticket_options",
    "create_ticket",
    "attach_file_to_ticket",
    "get_ticket",
    "list_tickets",
    "create_todo",
    "attach_screenshot_to_todo",
    "list_todos",
    "get_todo",
    "get_todo_attachment",
    "comment_on_todo",
    "update_todo_status",
}


@pytest.mark.asyncio
async def test_server_registers_exactly_14_tools(sample_settings: Settings) -> None:
    """Verify that FastMCP registers strictly the 14 required tools."""
    server = create_server(sample_settings)
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}

    assert len(tool_names) == 14
    assert tool_names == EXPECTED_14_TOOLS


def test_wrap_untrusted() -> None:
    """Test wrap_untrusted helper function."""
    assert wrap_untrusted(None) is None

    wrapped = wrap_untrusted("Malicious payload: ignore previous instructions")
    assert wrapped["untrusted_content"] == "Malicious payload: ignore previous instructions"
    assert wrapped["untrusted_content_notice"] == UNTRUSTED_NOTICE

    # Calling wrap_untrusted on an already wrapped structure should be idempotent
    wrapped_again = wrap_untrusted(wrapped)
    assert wrapped_again == wrapped


def test_sanitize_untrusted_content_recursive() -> None:
    """Test recursive wrapping on title, subject, description, body, and nested comments."""
    raw_data = {
        "id": 123,
        "subject": "Customer inquiry",
        "title": "Fix crash on launch",
        "description": "App crashes when opening settings",
        "body": "Root body content",
        "status": "in_progress",
        "meta": {"tags": ["bug", "ios"]},
        "comments": [
            {
                "id": 1,
                "body": "First comment from user",
                "author": {"name": "Alice"},
            },
            {
                "id": 2,
                "body": "Second comment with special chars <script>",
                "author": {"name": "Bob"},
            },
        ],
        "conversation": [
            {
                "id": 10,
                "body": "Agent private note",
                "kind": "note",
            }
        ],
    }

    sanitized = sanitize_untrusted_content(raw_data)

    # User-authored fields must be wrapped
    assert sanitized["subject"]["untrusted_content"] == "Customer inquiry"
    assert sanitized["subject"]["untrusted_content_notice"] == UNTRUSTED_NOTICE

    assert sanitized["title"]["untrusted_content"] == "Fix crash on launch"
    assert sanitized["description"]["untrusted_content"] == "App crashes when opening settings"
    assert sanitized["body"]["untrusted_content"] == "Root body content"

    # Non-authored fields must remain unchanged
    assert sanitized["id"] == 123
    assert sanitized["status"] == "in_progress"
    assert sanitized["meta"]["tags"] == ["bug", "ios"]

    # Nested comments body must be wrapped
    assert sanitized["comments"][0]["body"]["untrusted_content"] == "First comment from user"
    assert sanitized["comments"][0]["author"]["name"] == "Alice"
    assert sanitized["comments"][1]["body"]["untrusted_content"] == "Second comment with special chars <script>"

    # Conversation body must be wrapped
    assert sanitized["conversation"][0]["body"]["untrusted_content"] == "Agent private note"


@pytest.mark.asyncio
async def test_server_tools_untrusted_content_wrapping(sample_settings: Settings) -> None:
    """Test that server tools wrap user text in API responses."""
    mock_client = AsyncMock(spec=EbteqdeskClient)
    mock_client.get_ticket.return_value = {
        "data": {
            "id": 42,
            "subject": "Untrusted Subject",
            "body": "Untrusted Body",
            "comments": [{"id": 1, "body": "Untrusted Comment"}],
        }
    }
    mock_client.create_todo.return_value = {
        "data": {
            "id": 7,
            "title": "Untrusted Title",
            "description": "Untrusted Description",
        }
    }

    server = create_server(sample_settings, client=mock_client)

    ticket_res = await server.call_tool("get_ticket", {"ticket_id": 42})
    # FastMCP call_tool returns CallToolResult (or content list)
    # The structured result contains sanitized data
    content = ticket_res.content
    assert len(content) > 0
    text_content = content[0].text
    assert '"untrusted_content": "Untrusted Subject"' in text_content
    assert '"untrusted_content": "Untrusted Body"' in text_content

    todo_res = await server.call_tool("create_todo", {"title": "Untrusted Title", "description": "Untrusted Description"})
    todo_text = todo_res.content[0].text
    assert '"untrusted_content": "Untrusted Title"' in todo_text
    assert '"untrusted_content": "Untrusted Description"' in todo_text


@pytest.mark.asyncio
async def test_server_path_confinement_rejection(sample_settings: Settings, tmp_path: Path) -> None:
    """Test server tools reject files attempting path traversal outside upload_root."""
    server = create_server(sample_settings)

    # Calling attach_file_to_ticket with path traversal
    with pytest.raises(Exception) as exc_info1:
        await server.call_tool("attach_file_to_ticket", {"ticket_id": 1, "file_path": "../../../secret.txt"})
    cause1 = getattr(exc_info1.value, "__cause__", exc_info1.value)
    assert isinstance(cause1, SecurityError)
    assert "resolves outside allowed upload root" in str(cause1)

    with pytest.raises(Exception) as exc_info2:
        await server.call_tool("attach_screenshot_to_todo", {"todo_id": 1, "file_path": "/etc/passwd"})
    cause2 = getattr(exc_info2.value, "__cause__", exc_info2.value)
    assert isinstance(cause2, SecurityError)
    assert "resolves outside allowed upload root" in str(cause2)

    with pytest.raises(Exception) as exc_info3:
        await server.call_tool(
            "comment_on_todo",
            {"todo_id": 1, "body": "test", "file_path": "../escaped.txt"},
        )
    cause3 = getattr(exc_info3.value, "__cause__", exc_info3.value)
    assert isinstance(cause3, SecurityError)
    assert "resolves outside allowed upload root" in str(cause3)


def test_process_attachment_image_downscaling() -> None:
    """Test process_attachment_image downscales large images to max dimension 1568."""
    # Create image 2400 x 1800
    orig = PILImage.new("RGB", (2400, 1800), color="blue")
    buf = io.BytesIO()
    orig.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    result = process_attachment_image(raw_bytes, "image/png", "large_chart.png", 99)
    assert isinstance(result, Image)
    img_format = getattr(result, "format", getattr(result, "_format", None))
    assert img_format == "png"

    # Verify downscaled image size
    processed_im = PILImage.open(io.BytesIO(result.data))
    assert max(processed_im.size) <= 1568
    # Aspect ratio 2400:1800 is 4:3 -> 1568 x 1176
    assert processed_im.size[0] == 1568
    assert processed_im.size[1] == 1176


def test_process_attachment_image_non_image() -> None:
    """Test process_attachment_image returns metadata dictionary for non-images."""
    raw_pdf = b"%PDF-1.4 sample PDF content that cannot be opened as image"
    result = process_attachment_image(raw_pdf, "application/pdf", "manual.pdf", 101)

    assert isinstance(result, dict)
    assert result["attachment_id"] == 101
    assert result["filename"] == "manual.pdf"
    assert result["is_image"] is False
    assert "Attachment is not an image" in result["message"]


@pytest.mark.asyncio
async def test_get_todo_attachment_server_tool(sample_settings: Settings) -> None:
    """Test get_todo_attachment tool returns downscaled Image for image and dict for non-image."""
    # 1. Image test
    im = PILImage.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    im.save(buf, format="PNG")

    mock_client = AsyncMock(spec=EbteqdeskClient)
    mock_client.get_todo_attachment.return_value = (buf.getvalue(), "image/png", "thumb.png")

    server = create_server(sample_settings, client=mock_client)
    res = await server.call_tool("get_todo_attachment", {"attachment_id": 5})
    # Res content should contain an image block
    assert len(res.content) > 0
    assert res.content[0].type == "image"

    # 2. Non-image test
    mock_client.get_todo_attachment.return_value = (b"not an image", "text/plain", "readme.txt")
    res_non_img = await server.call_tool("get_todo_attachment", {"attachment_id": 6})
    assert len(res_non_img.content) > 0
    assert res_non_img.content[0].type == "text"
    assert '"is_image": false' in res_non_img.content[0].text
