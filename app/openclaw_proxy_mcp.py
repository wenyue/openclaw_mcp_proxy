from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool, ToolResult
from pydantic import ConfigDict

from .chat_session_registry import ChatSessionRegistry
from .models import ToolSchema


def create_chat_mcp_server(
    registry: ChatSessionRegistry,
    chat_session_id: str,
    tools: list[ToolSchema],
) -> FastMCP:
    mcp = FastMCP(f"OtakuRoomChat:{chat_session_id}")
    for tool in tools:
        mcp.add_tool(
            _ChatSessionTool.from_tool_schema(
                registry=registry,
                chat_session_id=chat_session_id,
                tool=tool,
            )
        )
    return mcp


def build_chat_http_mcp_app(
    registry: ChatSessionRegistry,
    chat_session_id: str,
    tools: list[ToolSchema],
):
    return create_chat_mcp_server(
        registry=registry,
        chat_session_id=chat_session_id,
        tools=tools,
    ).http_app(path="/", stateless_http=True)


class _ChatSessionTool(Tool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    registry: ChatSessionRegistry
    chat_session_id: str
    backend_tool_name: str

    @classmethod
    def from_tool_schema(
        cls,
        *,
        registry: ChatSessionRegistry,
        chat_session_id: str,
        tool: ToolSchema,
    ) -> "_ChatSessionTool":
        return cls(
            registry=registry,
            chat_session_id=chat_session_id,
            backend_tool_name=tool.name,
            name=tool.name,
            description=tool.description,
            parameters=tool.inputSchema,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        result = await self.registry.call_tool(
            self.chat_session_id,
            self.backend_tool_name,
            arguments,
        )
        return self.convert_result(result)
