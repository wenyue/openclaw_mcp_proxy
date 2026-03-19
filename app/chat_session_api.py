from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .auth import require_app_token, require_websocket_app_token
from .chat_session_registry import ChatSessionRegistry
from .models import CreateChatSessionRequest, CreateChatSessionResponse, InvokeResultMessage

logger = logging.getLogger('openclaw_mcp_proxy')

_CHAT_SESSIONS_PATH = "/v1/chat/sessions"
_MCP_PATH = "/v1/mcp"


def create_router(
    *,
    registry: ChatSessionRegistry,
    app_token: str,
) -> APIRouter:
    router = APIRouter()

    def require_token_dependency(authorization: str | None = Header(default=None)) -> None:
        require_app_token(authorization, app_token)

    @router.post(
        _CHAT_SESSIONS_PATH,
        response_model=CreateChatSessionResponse,
        dependencies=[Depends(require_token_dependency)],
    )
    async def create_chat_session(
        request: Request,
        payload: CreateChatSessionRequest,
    ) -> CreateChatSessionResponse:
        chat_session_id = uuid4().hex
        await registry.register(
            chat_session_id=chat_session_id,
            user_id=payload.user_id,
            device_id=payload.device_id,
            device_name=payload.device_name,
            chat_id=payload.chat_id,
            tools=payload.tools,
        )
        base_url = str(request.base_url).rstrip("/")
        return CreateChatSessionResponse(
            chat_session_id=chat_session_id,
            bridge_url=f"{_to_ws_base(base_url)}{_CHAT_SESSIONS_PATH}/{chat_session_id}/bridge",
            mcp_url=f"{base_url}{_MCP_PATH}/{chat_session_id}",
        )

    @router.delete(
        f"{_CHAT_SESSIONS_PATH}/{{chat_session_id}}",
        dependencies=[Depends(require_token_dependency)],
    )
    async def delete_chat_session(chat_session_id: str) -> JSONResponse:
        await registry.unregister(chat_session_id)
        return JSONResponse({"ok": True})

    @router.websocket(f"{_CHAT_SESSIONS_PATH}/{{chat_session_id}}/bridge")
    async def bridge_chat(websocket: WebSocket, chat_session_id: str) -> None:
        try:
            await require_websocket_app_token(websocket, app_token)
        except RuntimeError:
            return
        try:
            await registry.attach_bridge(chat_session_id, websocket)
        except KeyError:
            await websocket.close(code=4404, reason="Unknown chat_session_id.")
            return

        await websocket.accept()
        try:
            while True:
                payload = InvokeResultMessage.model_validate_json(await websocket.receive_text())
                if payload.type == "invoke_result":
                    await registry.complete_tool_call(payload)
        except WebSocketDisconnect as exc:
            if exc.code not in (1000, 1001):
                logger.warning(
                    'Bridge websocket disconnected unexpectedly.',
                    exc_info=exc,
                )
        finally:
            await registry.detach_bridge(chat_session_id, websocket)

    return router


def _to_ws_base(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    raise HTTPException(status_code=500, detail="Unsupported base URL.")
