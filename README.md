# ebteqdesk-grok-mcp

Dedicated Python MCP (Model Context Protocol) server for Ebteqdesk, designed for Grok Bot support automation, ticket creation parity, and To-Do Kanban workflow management.

## Features

- **Strictly 14 MCP Tools**: Comprehensive support for contacts, ticket options, ticket creation, attachments, threads, and To-Do Kanban cards.
- **Dual-Path Ticket Uploads**:
  - Direct multipart uploads for attachments <= 20MB (`POST /api/v1/tickets/{id}/attachments`).
  - Presigned direct-to-bucket upload flow for attachments > 20MB up to 500MB (`POST /api/v1/tickets/{id}/uploads` -> `PUT` -> `.../complete`).
- **Prompt Injection Defense (`untrusted_content` wrapper)**:
  - All user-authored text fields (`title`, `subject`, `body`, `description`, `comments[].body`) are wrapped in safety envelopes (`{"untrusted_content": ..., "untrusted_content_notice": ...}`) to instruct consuming language models to treat them as untrusted data rather than instructions.
- **Strict Upload Path Confinement**:
  - File operations are strictly confined within `EBTEQDESK_UPLOAD_ROOT` (default `~/.ebteqdesk-grok/uploads`).
  - Rejects path traversal (`..`) and symlinks escaping root *before* opening any file.
- **Visual Image Downscaling**:
  - Downscales To-Do image attachments with Pillow (`max_dimension=1568`, `ImageOps.exif_transpose`, `thumbnail`) and returns visual `Image` content blocks.
- **Stdio Transport Safety**:
  - Logging and diagnostic output stream strictly to `sys.stderr`, preventing corruption of stdio MCP JSON-RPC frames.
- **One-Command Setup and Validation**:
  - Built-in `setup` and `check` CLI commands for directory provisioning (`0700` upload dir, `0600` config file) and token scope verification.

---

## Tool Catalog

| Tool Name | Scope Required | Description |
|---|---|---|
| `whoami` | None | Retrieve authenticated user identity and resolved API key scopes. |
| `search_contacts` | `ticket:read` | Search customer contacts across brands by query (min 3 characters, limit 1–8). |
| `get_ticket_options` | `ticket:read` | Retrieve ticket types, sources, groups, agents, and priorities vocabulary. |
| `create_ticket` | `ticket:write` | Open a new support ticket with subject, body, requester/contact, group, and priority. |
| `attach_file_to_ticket` | `ticket:write` | Attach a file from the upload root to a ticket (<= 20MB multipart, > 20MB up to 500MB presigned). |
| `get_ticket` | `ticket:read` | Fetch full ticket metadata and conversation history with optional `thread_limit`. |
| `list_tickets` | `ticket:read` | List paginated tickets assigned to user (`scope="mine"`) or account (`scope="all"`). |
| `create_todo` | `todo:write` | Create a new Kanban To-Do card with title, description, status, priority, and labels. |
| `attach_screenshot_to_todo` | `todo:write` | Attach a screenshot/file from the upload root to a To-Do card or specific comment. |
| `list_todos` | `todo:read` | List To-Do cards with optional filtering by status, tag label, or text search. |
| `get_todo` | `todo:read` | Fetch detailed To-Do card information including threaded comments and attachments. |
| `get_todo_attachment` | `todo:read` | Download an attachment; returns downscaled `Image` for images or metadata dict for other files. |
| `comment_on_todo` | `todo:write` | Post a threaded comment on a To-Do card with optional file attachment. |
| `update_todo_status` | `todo:write` | Move a To-Do card to another Kanban status column (`todo`, `in_progress`, `ready_to_reply`, `done`). |

---

## Installation

Using `uv` (recommended):

```bash
uv venv
uv pip install -e ".[dev]"
```

Or using standard `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Configuration & CLI Commands

### 1. One-Action Setup (`setup`)

Run the interactive setup command to configure your connection, provision secure directories, and test connectivity:

```bash
ebteqdesk-grok-mcp setup \
  --base-url "https://help.example.com" \
  --api-token "ebtd_example_123|secret" \
  --upload-root "~/.ebteqdesk-grok/uploads" \
  --config-out "~/.ebteqdesk-grok/config.env"
```

The `setup` command automatically:
1. Creates the upload root directory with `0700` (`rwx------`) permissions.
2. Writes the configuration file with `0600` (`rw-------`) permissions using shell-safe escaping.
3. Performs a live `whoami` check against the server to verify required scopes (`ticket:read`, `ticket:write`, `todo:read`, `todo:write`).

### 2. Verify Scopes (`check`)

Verify that your configured credentials are valid and hold all necessary permissions:

```bash
ebteqdesk-grok-mcp check
```

Exits `0` if all scopes or wildcard `*` are present, or `1` with diagnostic error output on `stderr` if scopes are missing.

### 3. Run Server (`serve`)

Start the MCP server over stdio:

```bash
ebteqdesk-grok-mcp serve
```

*(Running `ebteqdesk-grok-mcp` with no arguments defaults to `serve`).*

### Configuration Options

| Variable | Default | Description |
|---|---|---|
| `EBTEQDESK_BASE_URL` | *(Required)* | Base URL of Ebteqdesk instance (e.g. `https://help.example.com`). |
| `EBTEQDESK_API_TOKEN` | *(Required)* | Personal access token / API key. |
| `EBTEQDESK_UPLOAD_ROOT` | `~/.ebteqdesk-grok/uploads` | Directory for uploads with strict confinement. |
| `EBTEQDESK_CONFIG_PATH` | `~/.ebteqdesk-grok/config.env` | Path to configuration file. |
| `EBTEQDESK_TIMEOUT` | `30.0` | Request timeout in seconds. |

---

## Integration with MCP Clients

Add the server to your MCP client configuration (e.g., `mcpServers`):

```json
{
  "mcpServers": {
    "ebteqdesk": {
      "command": "ebteqdesk-grok-mcp",
      "args": ["serve"],
      "env": {
        "EBTEQDESK_BASE_URL": "https://help.example.com",
        "EBTEQDESK_API_TOKEN": "ebtd_example_123|secret",
        "EBTEQDESK_UPLOAD_ROOT": "/Users/example/.ebteqdesk-grok/uploads"
      }
    }
  }
}
```

---

## Running Tests

Run the full automated test suite with pytest:

```bash
.venv/bin/pytest -v
```

All network interactions in tests are safely mocked with `respx`. Real network requests and credentials are never transmitted during testing.

## Security Notice

This repository adheres to strict public repository security guardrails:
- No company internal domains, real credentials, employee names, or customer data are included in source code or examples.
- Placeholder URLs use `https://help.example.com` and dummy tokens use `ebtd_example_123|secret`.
