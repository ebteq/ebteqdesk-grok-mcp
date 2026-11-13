"""Command-line interface for ebteqdesk-grok-mcp."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Sequence

from ebteqdesk_grok_mcp.client import EbteqdeskClient
from ebteqdesk_grok_mcp.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_UPLOAD_ROOT,
    Settings,
    load_settings,
)
from ebteqdesk_grok_mcp.errors import ConfigurationError, EbteqdeskError
from ebteqdesk_grok_mcp.server import create_server

REQUIRED_SCOPES = {"ticket:read", "ticket:write", "todo:read", "todo:write"}

logger = logging.getLogger("ebteqdesk_grok_mcp")


def configure_logging(verbose: bool = False) -> None:
    """Configure all logging strictly to sys.stderr to protect stdio MCP transport."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level)
    # Remove any existing handlers to prevent duplicate or stdout output
    root.handlers.clear()
    root.addHandler(handler)


async def run_check(settings: Settings) -> int:
    """Verify that credentials can authenticate and possess all required scopes."""
    sys.stderr.write(f"Checking Ebteqdesk connection at {settings.base_url}...\n")
    client = EbteqdeskClient(settings)
    try:
        data = await client.whoami()
    except EbteqdeskError as e:
        sys.stderr.write(f"Check failed: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Unexpected connection error: {e}\n")
        return 1
    finally:
        await client.close()

    user_data = data.get("data", {})
    api_key_info = user_data.get("apiKey") or {}
    scopes = api_key_info.get("scopes") or []

    sys.stderr.write(f"Authenticated as user: {user_data.get('name')} (ID: {user_data.get('id')})\n")
    sys.stderr.write(f"Resolved scopes: {scopes}\n")

    if "*" in scopes:
        sys.stderr.write("Wildcard scope '*' present. All required permissions granted.\n")
        return 0

    granted = set(scopes)
    missing = REQUIRED_SCOPES - granted
    if missing:
        sys.stderr.write(f"ERROR: Missing required scopes: {sorted(missing)}\n")
        sys.stderr.write(f"Required scopes are: {sorted(REQUIRED_SCOPES)}\n")
        return 1

    sys.stderr.write("All required scopes verified successfully.\n")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Create upload directory (0700), write config file (0600), and test connection."""
    upload_root = Path(os.path.expanduser(args.upload_root)).resolve()
    config_out = Path(os.path.expanduser(args.config_out)).resolve()

    sys.stderr.write(f"Setting up upload directory at: {upload_root}\n")
    upload_root.mkdir(parents=True, exist_ok=True)
    os.chmod(upload_root, 0o700)

    sys.stderr.write(f"Setting up configuration directory at: {config_out.parent}\n")
    config_out.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config_out.parent, 0o700)

    config_content = (
        "# Ebteqdesk Grok MCP Server Configuration\n"
        f"EBTEQDESK_BASE_URL={shlex.quote(args.base_url.rstrip('/'))}\n"
        f"EBTEQDESK_API_TOKEN={shlex.quote(args.api_token)}\n"
        f"EBTEQDESK_UPLOAD_ROOT={shlex.quote(str(upload_root))}\n"
    )

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(config_out, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(config_content)
    os.chmod(config_out, 0o600)

    sys.stderr.write(f"Wrote secure configuration (0600) to: {config_out}\n")

    # Run connection check with these new settings
    try:
        settings = Settings(
            base_url=args.base_url,
            api_token=args.api_token,
            upload_root=upload_root,
        )
    except Exception as e:
        sys.stderr.write(f"Configuration validation failed: {e}\n")
        return 1

    check_code = asyncio.run(run_check(settings))
    if check_code == 0:
        sys.stderr.write("Setup completed successfully!\n")
    else:
        sys.stderr.write("Setup wrote config, but connection check failed.\n")
    return check_code


def cmd_check(args: argparse.Namespace) -> int:
    """Verify credentials and scopes from existing configuration."""
    cfg_path = Path(os.path.expanduser(args.config)).resolve() if args.config else None
    try:
        settings = load_settings(config_path=cfg_path)
    except ConfigurationError as e:
        sys.stderr.write(f"Configuration error: {e}\n")
        return 1

    return asyncio.run(run_check(settings))


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the FastMCP server over stdio."""
    try:
        settings = load_settings()
    except ConfigurationError as e:
        sys.stderr.write(f"Failed to start server: {e}\n")
        return 1

    logger.info("Starting ebteqdesk-grok-mcp stdio server...")
    server = create_server(settings)
    server.run(transport="stdio")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for ebteqdesk-grok-mcp."""
    configure_logging()

    parser = argparse.ArgumentParser(
        prog="ebteqdesk-grok-mcp",
        description="Dedicated Ebteqdesk MCP server for Grok Bot support automation.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # serve (default)
    subparsers.add_parser("serve", help="Run MCP server over stdio (default)")

    # setup
    setup_parser = subparsers.add_parser("setup", help="Configure credentials and test connection")
    setup_parser.add_argument(
        "--base-url",
        required=True,
        help="Base URL of Ebteqdesk instance (e.g. https://help.example.com)",
    )
    setup_parser.add_argument(
        "--api-token",
        required=True,
        help="Sanctum API personal access token",
    )
    setup_parser.add_argument(
        "--upload-root",
        default=str(DEFAULT_UPLOAD_ROOT),
        help=f"Directory for safe uploads (default: {DEFAULT_UPLOAD_ROOT})",
    )
    setup_parser.add_argument(
        "--config-out",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to write config.env (default: {DEFAULT_CONFIG_PATH})",
    )

    # check
    check_parser = subparsers.add_parser("check", help="Verify credentials and scopes")
    check_parser.add_argument(
        "--config",
        default=None,
        help="Path to config.env to verify (optional)",
    )

    raw_args = list(argv if argv is not None else sys.argv[1:])
    # Default to 'serve' if no subcommand provided and not requesting help
    if not raw_args:
        raw_args = ["serve"]
    elif raw_args[0] not in ("serve", "setup", "check", "-h", "--help"):
        raw_args.insert(0, "serve")

    parsed = parser.parse_args(raw_args)

    if parsed.command == "setup":
        return cmd_setup(parsed)
    elif parsed.command == "check":
        return cmd_check(parsed)
    else:
        return cmd_serve(parsed)


if __name__ == "__main__":
    sys.exit(main())
