from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastmcp import Client

from app.chat_session_registry import ChatSessionRegistry
from app.models import ToolSchema
from app.openclaw_proxy_mcp import (
    build_chat_http_mcp_app,
    create_chat_mcp_server,
)


class OpenClawProxyMcpTest(unittest.TestCase):
    def test_create_chat_mcp_server_exposes_registered_tools(self) -> None:
        async def run() -> None:
            registry = ChatSessionRegistry(
                session_ttl_seconds=300,
                tool_timeout_seconds=120,
            )
            server = create_chat_mcp_server(
                registry,
                "session-1",
                [
                    ToolSchema(
                        name="demo-tool",
                        description="Demo tool.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "enabled": {"type": "boolean"},
                                "user-name": {"type": "string"},
                            },
                        },
                    ),
                ],
            )

            async with Client(server) as client:
                tools = await client.list_tools()

            self.assertEqual(["demo-tool"], [tool.name for tool in tools])

        asyncio.run(run())

    def test_create_chat_mcp_server_calls_registry_with_original_argument_names(self) -> None:
        async def run() -> None:
            registry = ChatSessionRegistry(
                session_ttl_seconds=300,
                tool_timeout_seconds=120,
            )
            registry.call_tool = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
            server = create_chat_mcp_server(
                registry,
                "session-1",
                [
                    ToolSchema(
                        name="demo-tool",
                        description="Demo tool.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "enabled": {"type": "boolean"},
                                "user-name": {"type": "string"},
                            },
                        },
                    ),
                ],
            )

            async with Client(server) as client:
                await client.call_tool("demo-tool", {"enabled": True, "user-name": "alice"})

            registry.call_tool.assert_awaited_once_with(
                "session-1",
                "demo-tool",
                {"enabled": True, "user-name": "alice"},
            )

        asyncio.run(run())

    def test_build_chat_http_mcp_app_returns_asgi_app(self) -> None:
        registry = ChatSessionRegistry(
            session_ttl_seconds=300,
            tool_timeout_seconds=120,
        )

        app = build_chat_http_mcp_app(registry, "session-1", [])

        self.assertTrue(callable(app))
        self.assertTrue(hasattr(app, "lifespan"))


if __name__ == "__main__":
    unittest.main()
