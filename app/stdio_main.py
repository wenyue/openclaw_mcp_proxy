from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

import anyio
import httpx
from fastmcp.mcp_config import MCPConfig, RemoteMCPServer
from fastmcp.server import create_proxy

from .config import load_config

_DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:8000"
_PROXY_URL_ENV = "OPENCLAW_PROXY_SERVER_URL"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expose a chat-scoped OpenClaw MCP session over stdio.",
    )
    parser.add_argument(
        "--chat-session-id",
        required=True,
        help="Existing chat session ID registered on the HTTP proxy.",
    )
    parser.add_argument(
        "--proxy-base-url",
        default=os.getenv(_PROXY_URL_ENV, _DEFAULT_PROXY_BASE_URL),
        help=(
            "Base URL for the running HTTP proxy server. "
            f"Defaults to {_PROXY_URL_ENV} or {_DEFAULT_PROXY_BASE_URL}."
        ),
    )
    return parser.parse_args(argv)


def resolve_session_mcp_url(proxy_base_url: str, chat_session_id: str) -> str:
    return f"{proxy_base_url.rstrip('/')}/v1/mcp/{chat_session_id}"


def build_chat_stdio_mcp_server(
    *,
    proxy_base_url: str,
    chat_session_id: str,
    openclaw_token: str,
):
    headers: dict[str, str] = {}
    if openclaw_token:
        headers["Authorization"] = f"Bearer {openclaw_token}"

    target = MCPConfig(
        mcpServers={
            "openclaw-chat": RemoteMCPServer(
                transport="http",
                url=resolve_session_mcp_url(proxy_base_url, chat_session_id),
                headers=headers,
            ),
        }
    )
    return create_proxy(target, name=f"OtakuRoomChat:{chat_session_id}")


def ensure_session_exists(
    *,
    proxy_base_url: str,
    chat_session_id: str,
    openclaw_token: str,
) -> bool:
    headers = {
        "Accept": "application/json, text/event-stream",
    }
    if openclaw_token:
        headers["Authorization"] = f"Bearer {openclaw_token}"

    response = httpx.post(
        resolve_session_mcp_url(proxy_base_url, chat_session_id),
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "openclaw-stdio-bootstrap",
                    "version": "1.0.0",
                },
            },
        },
        timeout=10.0,
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()

    if not ensure_session_exists(
        proxy_base_url=args.proxy_base_url,
        chat_session_id=args.chat_session_id,
        openclaw_token=config.openclaw_token,
    ):
        print(
            f"Unknown chat session: {args.chat_session_id}",
            file=sys.stderr,
        )
        return 1

    server = build_chat_stdio_mcp_server(
        proxy_base_url=args.proxy_base_url,
        chat_session_id=args.chat_session_id,
        openclaw_token=config.openclaw_token,
    )
    anyio.run(server.run_stdio_async)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
