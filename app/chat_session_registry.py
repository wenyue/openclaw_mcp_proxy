from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from fastapi import WebSocket

from .audit_log import log_tool_call, log_tool_result
from .models import InvokeToolMessage, InvokeResultMessage, ToolSchema


class ChatSessionState(str, Enum):
    REGISTERED = "registered"
    BRIDGED = "bridged"
    INVALIDATING = "invalidating"
    REMOVED = "removed"


@dataclass(slots=True)
class ChatSession:
    chat_session_id: str
    user_id: str
    device_id: str
    device_name: str
    chat_id: str
    tools: list[ToolSchema]
    expires_at: datetime
    tool_name_map: dict[str, str]
    bridge: WebSocket | None = None
    pending_calls: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    mcp_app: Any | None = None
    state: ChatSessionState = ChatSessionState.REGISTERED
    next_request_id: int = 1


class ChatSessionRegistry:
    def __init__(self, *, session_ttl_seconds: int, tool_timeout_seconds: int) -> None:
        self._session_ttl_seconds = session_ttl_seconds
        self._tool_timeout_seconds = tool_timeout_seconds
        self._sessions: dict[str, ChatSession] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        chat_session_id: str,
        user_id: str,
        device_id: str,
        device_name: str,
        chat_id: str,
        tools: list[ToolSchema],
    ) -> ChatSession:
        tool_names = [tool.name for tool in tools]
        if len(set(tool_names)) != len(tool_names):
            raise ValueError("Tool names must be unique per chat session.")

        session = ChatSession(
            chat_session_id=chat_session_id,
            user_id=user_id,
            device_id=device_id,
            device_name=device_name,
            chat_id=chat_id,
            tools=tools,
            expires_at=self._next_expiry(),
            tool_name_map={tool.name: tool.name for tool in tools},
        )
        async with self._lock:
            self._sessions[chat_session_id] = session
        return session

    async def unregister(self, chat_session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(chat_session_id, None)
            if session is None:
                return
            session.state = ChatSessionState.INVALIDATING
            pending_calls = list(session.pending_calls.values())
            session.pending_calls.clear()
            bridge = session.bridge
            session.bridge = None

        for future in pending_calls:
            if not future.done():
                future.set_exception(RuntimeError("Chat session was unregistered."))
        if bridge is not None:
            await bridge.close()
        session.state = ChatSessionState.REMOVED

    async def attach_bridge(self, chat_session_id: str, websocket: WebSocket) -> ChatSession:
        expired = False
        async with self._lock:
            session = self._sessions.get(chat_session_id)
            if session is None or session.state in {
                ChatSessionState.INVALIDATING,
                ChatSessionState.REMOVED,
            }:
                raise KeyError(chat_session_id)
            if self._is_ttl_expired(session):
                expired = True
            elif session.bridge is not None and session.bridge is not websocket:
                raise RuntimeError("Chat bridge already connected.")
            else:
                session.bridge = websocket
                session.expires_at = self._next_expiry()
                session.state = ChatSessionState.BRIDGED
                return session

        if expired:
            await self.unregister(chat_session_id)
        raise KeyError(chat_session_id)

    async def detach_bridge(self, chat_session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            session = self._sessions.get(chat_session_id)
            if session is None:
                return
            if session.bridge is websocket:
                session.bridge = None
                if session.state == ChatSessionState.BRIDGED:
                    session.state = ChatSessionState.REGISTERED

    async def set_mcp_app(self, chat_session_id: str, mcp_app: Any) -> None:
        session = await self.get(chat_session_id)
        if session is None:
            raise KeyError(chat_session_id)
        session.mcp_app = mcp_app

    async def get(self, chat_session_id: str) -> ChatSession | None:
        expired = False
        async with self._lock:
            session = self._sessions.get(chat_session_id)
            if session is None:
                return None
            if session.state in {
                ChatSessionState.INVALIDATING,
                ChatSessionState.REMOVED,
            }:
                return None
            if self._is_ttl_expired(session):
                expired = True
            else:
                return session
        if expired:
            await self.unregister(chat_session_id)
        return None

    async def call_tool(
        self,
        chat_session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        session = await self.get(chat_session_id)
        if session is None:
            raise RuntimeError("Chat session is not active.")
        async with self._lock:
            current_session = self._sessions.get(chat_session_id)
            if current_session is not session or session.state in {
                ChatSessionState.INVALIDATING,
                ChatSessionState.REMOVED,
            }:
                raise RuntimeError("Chat session is not active.")
            if session.state != ChatSessionState.BRIDGED or session.bridge is None:
                raise RuntimeError("Chat bridge is not connected.")
            request_id = f"{chat_session_id}:{session.next_request_id}"
            session.next_request_id += 1
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            session.pending_calls[request_id] = future
            session.expires_at = self._next_expiry()
            bridge = session.bridge
            user_id = session.user_id
        log_tool_call(
            chat_session_id=chat_session_id,
            user_id=user_id,
            tool_name=tool_name,
            request_id=request_id,
        )
        try:
            await bridge.send_json(
                InvokeToolMessage(
                    chatSessionId=chat_session_id,
                    requestId=request_id,
                    toolName=tool_name,
                    arguments=arguments,
                ).model_dump(),
            )
            result = await asyncio.wait_for(future, timeout=self._tool_timeout_seconds)
            log_tool_result(
                chat_session_id=chat_session_id,
                user_id=user_id,
                tool_name=tool_name,
                request_id=request_id,
                ok=True,
            )
            return result
        except Exception:
            log_tool_result(
                chat_session_id=chat_session_id,
                user_id=user_id,
                tool_name=tool_name,
                request_id=request_id,
                ok=False,
            )
            raise
        finally:
            async with self._lock:
                session.pending_calls.pop(request_id, None)

    async def complete_tool_call(self, message: InvokeResultMessage) -> None:
        async with self._lock:
            session = self._sessions.get(message.chatSessionId)
            if session is None:
                return
            future = session.pending_calls.pop(message.requestId, None)
        if future is None or future.done():
            return
        try:
            if message.ok:
                future.set_result(message.content or {})
            else:
                future.set_exception(RuntimeError(message.error or "Tool call failed."))
        except asyncio.InvalidStateError:
            return

    async def cleanup_expired(self) -> None:
        async with self._lock:
            expired_ids = [
                session_id
                for session_id, session in self._sessions.items()
                if session.expires_at <= datetime.now(UTC)
                and session.state in {
                    ChatSessionState.REGISTERED,
                    ChatSessionState.BRIDGED,
                }
                and not session.pending_calls
            ]
        for session_id in expired_ids:
            await self.unregister(session_id)

    def _next_expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._session_ttl_seconds)

    def _is_ttl_expired(self, session: ChatSession) -> bool:
        if session.expires_at > datetime.now(UTC):
            return False
        return not session.pending_calls
