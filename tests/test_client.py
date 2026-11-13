"""Tests for EbteqdeskClient HTTP operations and error translation."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from ebteqdesk_grok_mcp.client import EbteqdeskClient
from ebteqdesk_grok_mcp.config import Settings
from ebteqdesk_grok_mcp.errors import (
    APIError,
    AuthenticationError,
    ConflictError,
    FileTooLargeError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


@pytest.fixture
def base_url() -> str:
    return "https://help.example.com"


@pytest.mark.asyncio
async def test_whoami(sample_settings: Settings, base_url: str) -> None:
    """Test whoami retrieves user and resolved scopes."""
    async with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.get("/api/v1/user").respond(
            200,
            json={
                "data": {
                    "id": 1,
                    "name": "Support Bot",
                    "email": "bot@example.com",
                    "apiKey": {
                        "id": 10,
                        "name": "grok-key",
                        "scopes": ["ticket:read", "ticket:write", "todo:read", "todo:write"],
                    },
                }
            },
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.whoami()

        assert resp["data"]["id"] == 1
        assert "ticket:write" in resp["data"]["apiKey"]["scopes"]


@pytest.mark.asyncio
async def test_search_contacts(sample_settings: Settings, base_url: str) -> None:
    """Test searching customer contacts."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.get("/api/v1/contacts").respond(
            200,
            json={"data": [{"id": 101, "name": "Jane Doe", "email": "jane@example.com"}]},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.search_contacts("Jane", limit=5)

        assert route.called
        assert route.calls.last.request.url.params["search"] == "Jane"
        assert route.calls.last.request.url.params["limit"] == "5"
        assert len(resp["data"]) == 1
        assert resp["data"][0]["name"] == "Jane Doe"


@pytest.mark.asyncio
async def test_get_ticket_options(sample_settings: Settings, base_url: str) -> None:
    """Test fetching ticket options vocabulary."""
    async with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.get("/api/v1/ticket-options").respond(
            200,
            json={
                "data": {
                    "types": [{"key": "task", "label": "Task"}],
                    "priorities": [{"key": "urgent", "label": "Urgent"}],
                    "groups": [{"id": 1, "name": "Support"}],
                }
            },
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.get_ticket_options()

        assert "types" in resp["data"]
        assert resp["data"]["types"][0]["key"] == "task"


@pytest.mark.asyncio
async def test_create_ticket(sample_settings: Settings, base_url: str) -> None:
    """Test creating a new ticket with Idempotency-Key."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/tickets").respond(
            201,
            json={"data": {"id": 42, "subject": "Billing issue", "status": {"id": 1, "name": "new"}}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.create_ticket(
                subject="Billing issue",
                body="Customer cannot upgrade plan.",
                contact_id=101,
                priority="urgent",
                idempotency_key="idemp_create_123",
            )

        assert route.called
        req = route.calls.last.request
        assert req.headers["Idempotency-Key"] == "idemp_create_123"
        payload = httpx.Response(200, content=req.content).json()
        assert payload["subject"] == "Billing issue"
        assert payload["contact_id"] == 101
        assert payload["priority"] == "urgent"
        assert resp["data"]["id"] == 42


@pytest.mark.asyncio
async def test_attach_file_to_ticket_direct(sample_settings: Settings, temp_upload_dir: Path, base_url: str) -> None:
    """Test direct multipart attachment for files <= 20MB."""
    test_file = temp_upload_dir / "invoice.pdf"
    test_file.write_bytes(b"%PDF-1.4 small invoice file content")

    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/tickets/42/attachments").respond(
            201,
            json={"data": {"id": 501, "filename": "invoice.pdf", "size": len(test_file.read_bytes())}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.attach_file_to_ticket(42, test_file, idempotency_key="key_att_1")

        assert route.called
        assert route.calls.last.request.headers["Idempotency-Key"] == "key_att_1"
        assert "multipart/form-data" in route.calls.last.request.headers["Content-Type"]
        assert resp["data"]["id"] == 501


@pytest.mark.asyncio
async def test_attach_file_to_ticket_presigned(sample_settings: Settings, temp_upload_dir: Path, base_url: str) -> None:
    """Test presigned direct upload flow for files > 20MB and <= 500MB."""
    big_file = temp_upload_dir / "large_archive.zip"
    big_file.write_bytes(b"PK\x03\x04" + b"0" * 1024)  # Small on disk for test, but mocked size > 20MB

    storage_put_url = "https://storage.example.com/buckets/attachments/large_archive.zip"

    # Mock stat().st_size to return 25MB (26,214,400 bytes)
    with patch.object(Path, "stat") as mock_stat:
        mock_stat_res = mock_stat.return_value
        mock_stat_res.st_size = 25 * 1024 * 1024

        async with respx.mock as respx_mock:
            # Step 1: Start upload
            start_route = respx_mock.post(f"{base_url}/api/v1/tickets/42/uploads").respond(
                201,
                json={
                    "data": {
                        "upload_id": "ulid_upload_999",
                        "status": "pending",
                        "upload": {
                            "method": "PUT",
                            "url": storage_put_url,
                            "headers": {"x-amz-server-side-encryption": "AES256"},
                        },
                    }
                },
            )

            # Step 2: Storage PUT
            storage_route = respx_mock.put(storage_put_url).respond(200)

            # Step 3: Complete upload
            complete_route = respx_mock.post(f"{base_url}/api/v1/tickets/42/uploads/ulid_upload_999/complete").respond(
                201,
                json={"data": {"id": 888, "filename": "large_archive.zip", "size": 25 * 1024 * 1024}},
            )

            async with EbteqdeskClient(sample_settings) as client:
                resp = await client.attach_file_to_ticket(42, big_file, idempotency_key="key_presigned_1")

            assert start_route.called
            assert storage_route.called
            assert complete_route.called
            assert resp["data"]["id"] == 888


@pytest.mark.asyncio
async def test_attach_file_to_ticket_too_large(sample_settings: Settings, temp_upload_dir: Path) -> None:
    """Test file larger than 500MB is rejected with FileTooLargeError."""
    huge_file = temp_upload_dir / "huge.iso"
    huge_file.write_bytes(b"dummy")

    with patch.object(Path, "stat") as mock_stat:
        mock_stat.return_value.st_size = 501 * 1024 * 1024

        async with EbteqdeskClient(sample_settings) as client:
            with pytest.raises(FileTooLargeError, match="exceeds maximum 500MB limit"):
                await client.attach_file_to_ticket(42, huge_file)


@pytest.mark.asyncio
async def test_get_ticket(sample_settings: Settings, base_url: str) -> None:
    """Test retrieving ticket details."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.get("/api/v1/tickets/42").respond(
            200,
            json={
                "data": {
                    "id": 42,
                    "subject": "Printer issue",
                    "description": "Paper jam",
                    "conversation": [{"id": 1, "body": "Checked the tray."}],
                }
            },
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.get_ticket(42, thread_limit=10)

        assert route.called
        assert route.calls.last.request.url.params["thread_limit"] == "10"
        assert resp["data"]["id"] == 42


@pytest.mark.asyncio
async def test_list_tickets(sample_settings: Settings, base_url: str) -> None:
    """Test listing paginated tickets."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.get("/api/v1/tickets").respond(
            200,
            json={
                "data": [{"id": 1, "subject": "Ticket A"}, {"id": 2, "subject": "Ticket B"}],
                "meta": {"current_page": 2, "total": 30},
            },
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.list_tickets(page=2, per_page=15, scope="all")

        assert route.called
        params = route.calls.last.request.url.params
        assert params["page"] == "2"
        assert params["per_page"] == "15"
        assert params["scope"] == "all"
        assert len(resp["data"]) == 2


@pytest.mark.asyncio
async def test_create_todo(sample_settings: Settings, base_url: str) -> None:
    """Test creating a To-Do card."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/todos").respond(
            201,
            json={
                "data": {
                    "id": 7,
                    "title": "Investigate SLA breach",
                    "description": "High ticket backlog",
                    "status": "todo",
                    "priority": "high",
                }
            },
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.create_todo(
                title="Investigate SLA breach",
                description="High ticket backlog",
                priority="high",
            )

        assert route.called
        assert resp["data"]["id"] == 7
        assert resp["data"]["title"] == "Investigate SLA breach"


@pytest.mark.asyncio
async def test_attach_screenshot_to_todo(sample_settings: Settings, temp_upload_dir: Path, base_url: str) -> None:
    """Test attaching a screenshot to a To-Do card."""
    img_file = temp_upload_dir / "screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/todos/7/attachments").respond(
            201,
            json={"data": {"id": 20, "name": "screenshot.png", "mime_type": "image/png", "size": 40}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.attach_screenshot_to_todo(7, img_file, comment_id=12)

        assert route.called
        assert resp["data"]["id"] == 20


@pytest.mark.asyncio
async def test_list_todos(sample_settings: Settings, base_url: str) -> None:
    """Test listing To-Do cards with filters."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.get("/api/v1/todos").respond(
            200,
            json={"data": [{"id": 7, "title": "Card 1", "status": "in_progress"}]},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.list_todos(status="in_progress", limit=10)

        assert route.called
        params = route.calls.last.request.url.params
        assert params["status"] == "in_progress"
        assert params["limit"] == "10"
        assert len(resp["data"]) == 1


@pytest.mark.asyncio
async def test_get_todo(sample_settings: Settings, base_url: str) -> None:
    """Test retrieving a To-Do card."""
    async with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.get("/api/v1/todos/7").respond(
            200,
            json={"data": {"id": 7, "title": "Card 1", "comments": []}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.get_todo(7)

        assert resp["data"]["id"] == 7


@pytest.mark.asyncio
async def test_get_todo_attachment(sample_settings: Settings, base_url: str) -> None:
    """Test downloading a To-Do attachment binary."""
    async with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.get("/api/v1/todos/attachments/55").respond(
            200,
            content=b"file content bytes",
            headers={"Content-Type": "image/png", "Content-Disposition": 'attachment; filename="chart.png"'},
        )

        async with EbteqdeskClient(sample_settings) as client:
            content, ctype, fname = await client.get_todo_attachment(55)

        assert content == b"file content bytes"
        assert ctype == "image/png"
        assert fname == "chart.png"


@pytest.mark.asyncio
async def test_comment_on_todo_text(sample_settings: Settings, base_url: str) -> None:
    """Test posting a text comment on a To-Do card."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/todos/7/comments").respond(
            201,
            json={"data": {"id": 33, "body": "Checking in on this", "source": "grok"}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.comment_on_todo(7, "Checking in on this", source="grok")

        assert route.called
        payload = httpx.Response(200, content=route.calls.last.request.content).json()
        assert payload["body"] == "Checking in on this"
        assert payload["source"] == "grok"
        assert resp["data"]["id"] == 33


@pytest.mark.asyncio
async def test_comment_on_todo_with_file(sample_settings: Settings, temp_upload_dir: Path, base_url: str) -> None:
    """Test posting a comment with an attached file."""
    doc = temp_upload_dir / "note.txt"
    doc.write_text("Hello note", encoding="utf-8")

    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.post("/api/v1/todos/7/comments").respond(
            201,
            json={"data": {"id": 34, "body": "See attached note", "source": "grok"}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.comment_on_todo(7, "See attached note", file_path=doc)

        assert route.called
        assert "multipart/form-data" in route.calls.last.request.headers["Content-Type"]
        assert resp["data"]["id"] == 34


@pytest.mark.asyncio
async def test_update_todo_status(sample_settings: Settings, base_url: str) -> None:
    """Test updating To-Do status."""
    async with respx.mock(base_url=base_url) as respx_mock:
        route = respx_mock.patch("/api/v1/todos/7").respond(
            200,
            json={"data": {"id": 7, "status": "done"}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            resp = await client.update_todo_status(7, "done")

        assert route.called
        payload = httpx.Response(200, content=route.calls.last.request.content).json()
        assert payload["status"] == "done"
        assert resp["data"]["status"] == "done"


# =============================================================================
# Error Translation Tests
# =============================================================================

@pytest.mark.parametrize(
    "status,exception_cls",
    [
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (422, ValidationError),
        (409, ConflictError),
        (500, APIError),
    ],
)
@pytest.mark.asyncio
async def test_http_error_mappings(sample_settings: Settings, base_url: str, status: int, exception_cls: type) -> None:
    """Test API error status codes are correctly translated to specific exceptions."""
    async with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.get("/api/v1/user").respond(
            status,
            json={"error": "Something went wrong", "reason": "test_failure", "errors": {"field": ["msg"]}},
        )

        async with EbteqdeskClient(sample_settings) as client:
            with pytest.raises(exception_cls) as exc_info:
                await client.whoami()

        err = exc_info.value
        assert err.status_code == status
        assert "Something went wrong" in str(err)
        if status == 422:
            assert err.errors == {"field": ["msg"]}
