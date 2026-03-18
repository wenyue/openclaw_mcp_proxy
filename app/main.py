from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.responses import JSONResponse, PlainTextResponse

from .auth import require_bearer_token
from .chat_registration_api import create_router
from .chat_session_registry import ChatSessionRegistry
from .config import load_config
from .openclaw_proxy_mcp import build_chat_mcp_app


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

        mcp_app = build_chat_mcp_app(self._registry, chat_session_id, session.tools)
        new_scope = dict(scope)
        new_scope["path"] = stripped_path
        new_scope["raw_path"] = stripped_path.encode("utf-8")
        new_scope["root_path"] = effective_root_path
        new_scope["app"] = mcp_app
        async with mcp_app.lifespan(mcp_app):
            await mcp_app(new_scope, receive, send)


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

    app = FastAPI(lifespan=lifespan)
    app.include_router(create_router(registry=registry, app_token=config.app_token))
    app.mount("/mcp", DynamicMcpProxyApp(registry, config.openclaw_token))

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
