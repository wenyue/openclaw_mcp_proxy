from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastmcp.server.context import reset_transport, set_transport
from fastmcp.server.http import set_http_request
from fastapi import FastAPI
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from .auth import require_bearer_token
from .chat_session_api import create_router
from .chat_session_registry import ChatSessionRegistry
from .config import load_config
from .openclaw_proxy_mcp import create_chat_mcp_server


class DynamicMcpProxyApp:
    def __init__(self, registry: ChatSessionRegistry, openclaw_token: str) -> None:
        self._registry = registry
        self._openclaw_token = openclaw_token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Unsupported scope.", status_code=400)
            await response(scope, receive, send)
            return

        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        try:
            require_bearer_token(
                headers.get("authorization"),
                self._openclaw_token,
                realm="openclaw",
            )
        except Exception as exc:
            response = JSONResponse({"error": str(exc)}, status_code=401)
            await response(scope, receive, send)
            return

        raw_path = scope.get("path", "/") or "/"
        root_path = scope.get("root_path", "") or ""
        relative_path = _strip_root_path(raw_path, root_path)
        segments = [segment for segment in relative_path.split("/") if segment]
        chat_session_id = headers.get("x-openclaw-chat-session")
        stripped_path = relative_path
        effective_root_path = root_path
        if segments:
            first_segment = segments[0]
            session = await self._registry.get(first_segment)
            if session is not None:
                chat_session_id = first_segment
                stripped_path = "/" + "/".join(segments[1:])
                if stripped_path == "/":
                    stripped_path = "/"
                effective_root_path = _join_root_path(root_path, chat_session_id)

        if not chat_session_id:
            response = JSONResponse({"error": "Missing chat session."}, status_code=400)
            await response(scope, receive, send)
            return

        session = await self._registry.get(chat_session_id)
        if session is None:
            response = JSONResponse({"error": "Unknown chat session."}, status_code=404)
            await response(scope, receive, send)
            return

        mcp_server = create_chat_mcp_server(
            self._registry,
            chat_session_id,
            session.tools,
        )
        await _serve_stateless_http_mcp(
            mcp_server=mcp_server,
            scope=scope,
            receive=receive,
            send=send,
            path=stripped_path,
            root_path=effective_root_path,
        )


def create_app() -> FastAPI:
    config = load_config()
    registry = ChatSessionRegistry(
        session_ttl_seconds=config.session_ttl_seconds,
        tool_timeout_seconds=config.tool_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_task = asyncio.create_task(_cleanup_loop(registry))
        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan)
    app.include_router(create_router(registry=registry, app_token=config.app_token))
    app.mount("/v1/mcp", DynamicMcpProxyApp(registry, config.openclaw_token))

    @app.get("/health")
    async def health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


async def _cleanup_loop(registry: ChatSessionRegistry) -> None:
    while True:
        await registry.cleanup_expired()
        await asyncio.sleep(15)


app = create_app()


def _strip_root_path(path: str, root_path: str) -> str:
    if root_path and path.startswith(root_path):
        trimmed = path[len(root_path) :]
        return trimmed or "/"
    return path or "/"


def _join_root_path(root_path: str, segment: str) -> str:
    if not root_path:
        return f"/{segment}"
    return f"{root_path.rstrip('/')}/{segment}"


async def _serve_stateless_http_mcp(
    *,
    mcp_server: Any,
    scope: dict[str, Any],
    receive: Any,
    send: Any,
    path: str,
    root_path: str,
) -> None:
    transport = StreamableHTTPServerTransport(
        mcp_session_id=None,
        is_json_response_enabled=False,
    )
    new_scope = dict(scope)
    new_scope["path"] = path
    new_scope["raw_path"] = path.encode("utf-8")
    new_scope["root_path"] = root_path

    transport_token = set_transport("streamable-http")
    try:
        with set_http_request(Request(new_scope)):
            async with mcp_server._lifespan_manager():
                async with transport.connect() as streams:
                    read_stream, write_stream = streams
                    async with anyio.create_task_group() as task_group:
                        server_stopped = anyio.Event()

                        async def run_server() -> None:
                            try:
                                await mcp_server._mcp_server.run(
                                    read_stream,
                                    write_stream,
                                    mcp_server._mcp_server.create_initialization_options(
                                        notification_options=NotificationOptions(
                                            tools_changed=True,
                                        )
                                    ),
                                    False,
                                    True,
                                )
                            finally:
                                server_stopped.set()

                        task_group.start_soon(run_server)
                        await transport.handle_request(new_scope, receive, send)
                        await transport.terminate()
                        with anyio.move_on_after(5):
                            await server_stopped.wait()
                        task_group.cancel_scope.cancel()
    finally:
        reset_transport(transport_token)
