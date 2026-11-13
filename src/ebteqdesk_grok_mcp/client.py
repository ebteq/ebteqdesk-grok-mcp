"""HTTP client for the Ebteqdesk REST API v1."""

from __future__ import annotations

import logging
import mimetypes
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

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

logger = logging.getLogger(__name__)

# Size thresholds for ticket uploads
MAX_DIRECT_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB
MAX_PRESIGNED_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB


class EbteqdeskClient:
    """Asynchronous client interacting with the Ebteqdesk API v1."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            headers={
                "Authorization": f"Bearer {settings.api_token}",
                "Accept": "application/json",
                "User-Agent": "ebteqdesk-grok-mcp/0.1.0",
            },
            follow_redirects=True,
        )

    async def __aenter__(self) -> EbteqdeskClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client session if owned."""
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()

    def _handle_error(self, response: httpx.Response) -> None:
        """Translate HTTP error responses into structured Ebteqdesk exceptions."""
        status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"error": response.text or f"HTTP {status_code}"}

        message = body.get("error") or body.get("message") or f"HTTP {status_code}"
        reason = body.get("reason")
        errors = body.get("errors")

        logger.warning(
            "API request failed: status=%d message=%s reason=%s",
            status_code,
            message,
            reason,
        )

        if status_code == 401:
            raise AuthenticationError(message, status_code, reason=reason, errors=errors, response_body=body)
        elif status_code == 403:
            raise PermissionDeniedError(message, status_code, reason=reason, errors=errors, response_body=body)
        elif status_code == 404:
            raise NotFoundError(message, status_code, reason=reason, errors=errors, response_body=body)
        elif status_code == 422:
            raise ValidationError(message, status_code, reason=reason, errors=errors, response_body=body)
        elif status_code == 409:
            raise ConflictError(message, status_code, reason=reason, errors=errors, response_body=body)
        else:
            raise APIError(message, status_code, reason=reason, errors=errors, response_body=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request with error handling."""
        req_headers = dict(headers or {})

        response = await self._client.request(
            method=method,
            url=path,
            params=params,
            json=json,
            data=data,
            files=files,
            headers=req_headers,
        )

        if response.is_error:
            self._handle_error(response)

        return response

    # =========================================================================
    # 1. Identity & Options
    # =========================================================================

    async def whoami(self) -> dict[str, Any]:
        """GET /api/v1/user — Retrieve current authenticated user and resolved scopes."""
        resp = await self._request("GET", "/api/v1/user")
        return resp.json()

    async def search_contacts(self, search: str, limit: int = 8) -> dict[str, Any]:
        """GET /api/v1/contacts — Search contacts across brands."""
        clean_limit = min(max(int(limit), 1), 8)
        resp = await self._request("GET", "/api/v1/contacts", params={"search": search, "limit": clean_limit})
        return resp.json()

    async def get_ticket_options(self) -> dict[str, Any]:
        """GET /api/v1/ticket-options — Retrieve dropdown choices and options."""
        resp = await self._request("GET", "/api/v1/ticket-options")
        return resp.json()

    # =========================================================================
    # 2. Tickets
    # =========================================================================

    async def create_ticket(
        self,
        subject: str,
        body: str,
        *,
        contact_id: int | None = None,
        requester: dict[str, Any] | None = None,
        category: str | None = None,
        source: str | None = None,
        group_id: int | None = None,
        agent_id: int | None = None,
        priority: str | int | None = None,
        tags: list[str] | None = None,
        status: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/tickets — Open a new ticket."""
        payload: dict[str, Any] = {
            "subject": subject,
            "body": body,
            "description": body,
        }
        if contact_id is not None:
            payload["contact_id"] = contact_id
        if requester is not None:
            payload["requester"] = requester
        if category is not None:
            payload["category"] = category
        if source is not None:
            payload["source"] = source
        if group_id is not None:
            payload["group_id"] = group_id
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if priority is not None:
            payload["priority"] = priority
        if tags is not None:
            payload["tags"] = tags
        if status is not None:
            payload["status"] = status

        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
        }

        resp = await self._request("POST", "/api/v1/tickets", json=payload, headers=headers)
        return resp.json()

    async def attach_file_to_ticket(
        self,
        ticket_id: int | str,
        file_path: Path,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach a file to a ticket using direct multipart (<=20MB) or presigned uploads (>20MB up to 500MB)."""
        file_size = file_path.stat().st_size

        if file_size > MAX_PRESIGNED_UPLOAD_SIZE:
            raise FileTooLargeError(
                f"File '{file_path.name}' size ({file_size} bytes) exceeds maximum 500MB limit."
            )

        key = idempotency_key or str(uuid.uuid4())
        mime_type, _ = mimetypes.guess_type(file_path.name)
        mime = mime_type or "application/octet-stream"

        if file_size <= MAX_DIRECT_UPLOAD_SIZE:
            # Direct multipart upload
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, mime)}
                resp = await self._request(
                    "POST",
                    f"/api/v1/tickets/{ticket_id}/attachments",
                    files=files,
                    headers={"Idempotency-Key": key},
                )
            return resp.json()

        # Presigned upload path for 20MB < size <= 500MB
        start_payload = {
            "filename": file_path.name,
            "mime_type": mime,
            "size": file_size,
        }
        start_resp = await self._request(
            "POST",
            f"/api/v1/tickets/{ticket_id}/uploads",
            json=start_payload,
            headers={"Idempotency-Key": key},
        )
        start_data = start_resp.json().get("data", {})
        upload_id = start_data.get("upload_id")

        if start_data.get("status") == "completed":
            return {"data": start_data.get("attachment", {})}

        upload_info = start_data.get("upload", {})
        put_url = upload_info.get("url")
        put_headers = dict(upload_info.get("headers", {}))

        if not put_url:
            raise APIError("Presigned upload did not return a valid PUT URL", 500)

        # Upload binary directly to storage bucket
        with open(file_path, "rb") as f:
            content = f.read()

        put_resp = await self._client.put(put_url, content=content, headers=put_headers)
        if put_resp.is_error:
            raise APIError(
                f"Direct storage upload failed with status {put_resp.status_code}: {put_resp.text}",
                put_resp.status_code,
            )

        # Complete the upload
        complete_resp = await self._request(
            "POST",
            f"/api/v1/tickets/{ticket_id}/uploads/{upload_id}/complete",
            headers={"Idempotency-Key": key},
        )
        return complete_resp.json()

    async def get_ticket(self, ticket_id: int | str, thread_limit: int | None = None) -> dict[str, Any]:
        """GET /api/v1/tickets/{ticket} — Retrieve ticket details with conversation thread."""
        params = {}
        if thread_limit is not None:
            params["thread_limit"] = thread_limit

        resp = await self._request("GET", f"/api/v1/tickets/{ticket_id}", params=params)
        return resp.json()

    async def list_tickets(
        self,
        page: int = 1,
        per_page: int = 20,
        scope: str = "mine",
    ) -> dict[str, Any]:
        """GET /api/v1/tickets — Retrieve paginated tickets assigned to user or account."""
        params = {
            "page": max(int(page), 1),
            "per_page": min(max(int(per_page), 1), 20),
            "scope": scope,
        }
        resp = await self._request("GET", "/api/v1/tickets", params=params)
        return resp.json()

    # =========================================================================
    # 3. To-Do Kanban API
    # =========================================================================

    async def create_todo(
        self,
        title: str,
        description: str | None = None,
        *,
        status: str = "todo",
        priority: str = "normal",
        labels: list[str] | None = None,
        assigned_user_id: int | None = None,
        whatsapp_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/todos — Create a new To-Do card."""
        payload: dict[str, Any] = {
            "title": title,
            "status": status,
            "priority": priority,
        }
        if description is not None:
            payload["description"] = description
        if labels is not None:
            payload["labels"] = labels
        if assigned_user_id is not None:
            payload["assigned_user_id"] = assigned_user_id
        if whatsapp_context is not None:
            payload["whatsapp_context"] = whatsapp_context

        resp = await self._request("POST", "/api/v1/todos", json=payload)
        return resp.json()

    async def attach_screenshot_to_todo(
        self,
        todo_id: int | str,
        file_path: Path,
        comment_id: int | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/todos/{todo}/attachments — Attach a screenshot/file to a todo or comment."""
        mime_type, _ = mimetypes.guess_type(file_path.name)
        mime = mime_type or "application/octet-stream"

        data: dict[str, Any] = {}
        if comment_id is not None:
            data["comment_id"] = str(comment_id)

        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, mime)}
            resp = await self._request(
                "POST",
                f"/api/v1/todos/{todo_id}/attachments",
                data=data if data else None,
                files=files,
            )
        return resp.json()

    async def list_todos(
        self,
        status: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /api/v1/todos — List To-Do cards with optional filtering."""
        params: dict[str, Any] = {
            "limit": min(max(int(limit), 1), 200),
        }
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag
        if search:
            params["search"] = search

        resp = await self._request("GET", "/api/v1/todos", params=params)
        return resp.json()

    async def get_todo(self, todo_id: int | str) -> dict[str, Any]:
        """GET /api/v1/todos/{todo} — Fetch detailed To-Do card information."""
        resp = await self._request("GET", f"/api/v1/todos/{todo_id}")
        return resp.json()

    async def get_todo_attachment(self, attachment_id: int | str) -> tuple[bytes, str, str]:
        """GET /api/v1/todos/attachments/{attachment} — Download attachment raw binary and headers."""
        resp = await self._request("GET", f"/api/v1/todos/attachments/{attachment_id}")
        content = resp.content
        content_type = resp.headers.get("content-type", "application/octet-stream")

        # Extract filename from Content-Disposition if present
        filename = f"attachment_{attachment_id}"
        cd = resp.headers.get("content-disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"; ')

        return content, content_type, filename

    async def comment_on_todo(
        self,
        todo_id: int | str,
        body: str,
        *,
        source: str = "grok",
        file_path: Path | None = None,
        attachment_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/todos/{todo}/comments — Add a comment to a To-Do card."""
        if file_path is not None:
            mime_type, _ = mimetypes.guess_type(file_path.name)
            mime = mime_type or "application/octet-stream"
            data: dict[str, Any] = {
                "body": body,
                "source": source,
            }
            if attachment_ids:
                for idx, att_id in enumerate(attachment_ids):
                    data[f"attachment_ids[{idx}]"] = str(att_id)

            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, mime)}
                resp = await self._request(
                    "POST",
                    f"/api/v1/todos/{todo_id}/comments",
                    data=data,
                    files=files,
                )
            return resp.json()

        payload: dict[str, Any] = {
            "body": body,
            "source": source,
        }
        if attachment_ids is not None:
            payload["attachment_ids"] = attachment_ids

        resp = await self._request("POST", f"/api/v1/todos/{todo_id}/comments", json=payload)
        return resp.json()

    async def update_todo_status(self, todo_id: int | str, status: str) -> dict[str, Any]:
        """PATCH /api/v1/todos/{todo} — Update status of a To-Do card."""
        resp = await self._request("PATCH", f"/api/v1/todos/{todo_id}", json={"status": status})
        return resp.json()
