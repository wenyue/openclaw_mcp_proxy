from __future__ import annotations

import argparse
import os
from typing import Any, Sequence

import anyio
import httpx
from fastmcp.client import Client
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.server.dependencies import get_context
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import FastMCPProxy
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INVALID_REQUEST

from .config import load_config

_DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:8000"
_PROXY_URL_ENV = "OPENCLAW_PROXY_SERVER_URL"
_SESSION_ID_HEADER = "MCP-Session-Id"
_INITIALIZE_SESSION_ID_FIELD = "mcpSessionId"
_SESSION_ID_STATE_KEY = "session_id"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expose an OpenClaw MCP session over stdio.",
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


def resolve_mcp_url(proxy_base_url: str) -> str:
    return f"{proxy_base_url.rstrip('/')}/v1/mcp"


def build_transport_headers(
    *,
    openclaw_token: str,
    session_id: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if openclaw_token:
        headers["Authorization"] = f"Bearer {openclaw_token}"
    if session_id:
        headers[_SESSION_ID_HEADER] = session_id
    return headers


def extract_session_id(params: Any) -> str:
    model_extra = getattr(params, "model_extra", None) or {}
    session_id = model_extra.get(_INITIALIZE_SESSION_ID_FIELD)
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError(
            f"Missing required initialize field: {_INITIALIZE_SESSION_ID_FIELD}"
        )
    return session_id.strip()


class SessionBindingMiddleware(Middleware):
    def __init__(self, *, proxy_base_url: str, openclaw_token: str) -> None:
        self._proxy_base_url = proxy_base_url
        self._openclaw_token = openclaw_token

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next,
    ) -> Any:
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            raise McpError(
                ErrorData(
                    code=INVALID_REQUEST,
                    message="Missing FastMCP context during initialize.",
                )
            )

        try:
            session_id = extract_session_id(context.message.params)
        except ValueError as exc:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc

        if not ensure_session_exists(
            proxy_base_url=self._proxy_base_url,
            session_id=session_id,
            openclaw_token=self._openclaw_token,
        ):
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Unknown session id: {session_id}",
                )
            )

        response = await call_next(context)
        await fastmcp_context.set_state(_SESSION_ID_STATE_KEY, session_id)
        return response


async def create_http_proxy_client(
    *,
    proxy_base_url: str,
    openclaw_token: str,
) -> Client:
    context = get_context()
    session_id = await context.get_state(_SESSION_ID_STATE_KEY)
    if not isinstance(session_id, str) or not session_id:
        raise McpError(
            ErrorData(
                code=INVALID_REQUEST,
                message=(
                    "Session id is not bound. Initialize the stdio connection with "
                    f"`{_INITIALIZE_SESSION_ID_FIELD}` first."
                ),
            )
        )

    transport = StreamableHttpTransport(
        url=resolve_mcp_url(proxy_base_url),
        headers=build_transport_headers(
            openclaw_token=openclaw_token,
            session_id=session_id,
        ),
    )
    return Client(transport, name="openclaw-chat-http")


def build_chat_stdio_mcp_server(
    *,
    proxy_base_url: str,
    openclaw_token: str,
):
    return FastMCPProxy(
        client_factory=lambda: create_http_proxy_client(
            proxy_base_url=proxy_base_url,
            openclaw_token=openclaw_token,
        ),
        name="OtakuRoomChat",
        middleware=[
            SessionBindingMiddleware(
                proxy_base_url=proxy_base_url,
                openclaw_token=openclaw_token,
            )
        ],
    )


def ensure_session_exists(
    *,
    proxy_base_url: str,
    session_id: str,
    openclaw_token: str,
) -> bool:
    headers = {
        "Accept": "application/json, text/event-stream",
    }
    headers.update(
        build_transport_headers(
            openclaw_token=openclaw_token,
            session_id=session_id,
        )
    )

    response = httpx.post(
        resolve_mcp_url(proxy_base_url),
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

    server = build_chat_stdio_mcp_server(
        proxy_base_url=args.proxy_base_url,
        openclaw_token=config.openclaw_token,
    )
    anyio.run(server.run_stdio_async)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
