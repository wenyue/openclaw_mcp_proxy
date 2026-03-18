from __future__ import annotations

from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    name: str
    path: str
    description: str
    input_schema: dict = Field(default_factory=dict)


class RegisterChatRequest(BaseModel):
    user_id: str
    device_id: str
    device_name: str = ""
    app_version: str
    chat_id: str
    tools: list[ToolSchema] = Field(default_factory=list)


class RegisterChatResponse(BaseModel):
    chat_session_id: str
    bridge_url: str
    mcp_url: str


class UnregisterChatRequest(BaseModel):
    chat_session_id: str


class InvokeToolMessage(BaseModel):
    type: str = "invoke_tool"
    chat_session_id: str
    request_id: str
    tool_name: str
    arguments: dict = Field(default_factory=dict)


class InvokeResultMessage(BaseModel):
    type: str = "invoke_result"
    chat_session_id: str
    request_id: str
    ok: bool
    content: dict | None = None
    error: str | None = None
