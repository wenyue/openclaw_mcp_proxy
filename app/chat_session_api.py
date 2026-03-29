from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .auth import require_app_token, require_websocket_app_token
from .chat_session_registry import ChatSessionRegistry
from .models import CreateChatSessionRequest, CreateChatSessionResponse, InvokeResultMessage

logger = logging.getLogger('openclaw_mcp_proxy')

_CHAT_SESSIONS_PATH = "/v1/chat/sessions"


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
        payload: CreateChatSessionRequest,
    ) -> CreateChatSessionResponse:
        session_id = uuid4().hex
        await registry.register(
            session_id=session_id,
            user_id=payload.userId,
            device_id=payload.deviceId,
            device_name=payload.deviceName,
            tools=payload.tools,
        )
        return CreateChatSessionResponse(
            mcpSessionId=session_id,
        )

    @router.delete(
        f"{_CHAT_SESSIONS_PATH}/{{session_id}}",
        dependencies=[Depends(require_token_dependency)],
    )
    async def delete_chat_session(session_id: str) -> JSONResponse:
        await registry.unregister(session_id)
        return JSONResponse({"ok": True})

    @router.websocket(f"{_CHAT_SESSIONS_PATH}/{{session_id}}/bridge")
    async def bridge_chat(websocket: WebSocket, session_id: str) -> None:
        bridge_attached = False
        try:
            await require_websocket_app_token(websocket, app_token)
        except RuntimeError:
            return
        try:
            await registry.attach_bridge(session_id, websocket)
            bridge_attached = True
        except KeyError:
            await websocket.close(code=4404, reason="Unknown session_id.")
            return
        except RuntimeError as exc:
            await websocket.close(code=4409, reason=str(exc))
            return

        try:
            await websocket.accept()
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
            if bridge_attached:
                await registry.detach_bridge(session_id, websocket)

    return router
