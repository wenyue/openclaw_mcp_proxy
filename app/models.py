from __future__ import annotations

from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    name: str
    description: str
    inputSchema: dict = Field(default_factory=dict)


class CreateChatSessionRequest(BaseModel):
    deviceId: str
    deviceName: str = ""
    appVersion: str
    tools: list[ToolSchema] = Field(default_factory=list)


class CreateChatSessionResponse(BaseModel):
    mcpSessionId: str

class InvokeToolMessage(BaseModel):
    type: str = "invoke_tool"
    mcpSessionId: str
    requestId: str
    toolName: str
    arguments: dict = Field(default_factory=dict)


class InvokeResultMessage(BaseModel):
    type: str = "invoke_result"
    mcpSessionId: str
    requestId: str
    ok: bool
    content: dict | None = None
    error: str | None = None
