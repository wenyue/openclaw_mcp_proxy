from __future__ import annotations

import keyword
import re
from typing import Any, Optional

from fastmcp import FastMCP

from .chat_session_registry import ChatSessionRegistry
from .models import ToolSchema


def build_chat_mcp_app(
    registry: ChatSessionRegistry,
    chat_session_id: str,
    tools: list[ToolSchema],
):
    mcp = FastMCP(f"OtakuRoomChat:{chat_session_id}")
    for tool in tools:
        handler = _build_tool_handler(registry, chat_session_id, tool)
        mcp.tool(name=tool.name, description=tool.description)(handler)
    return mcp.http_app(path="/", stateless_http=True)


def _build_tool_handler(
    registry: ChatSessionRegistry,
    chat_session_id: str,
    tool: ToolSchema,
):
    raw_properties = tool.inputSchema.get("properties", {})
    used_names: set[str] = set()
    parameters: list[str] = []
    assignments: list[str] = ["    arguments = {}"]

    for original_name, schema in raw_properties.items():
        python_name = _to_python_identifier(original_name, used_names)
        schema_type = schema.get("type")
        if schema_type == "boolean":
            parameters.append(f"{python_name}: Optional[bool] = None")
        else:
            parameters.append(f"{python_name}: Optional[str] = None")
        assignments.extend(
            [
                f"    if {python_name} is not None:",
                f"        arguments[{original_name!r}] = {python_name}",
            ],
        )

    body_lines = assignments + [
        f"    return await registry.call_tool({chat_session_id!r}, {tool.name!r}, arguments)",
    ]
    parameter_list = ", ".join(parameters)
    source = (
        f"async def _handler({parameter_list}):\n"
        + "\n".join(body_lines)
        + "\n"
    )
    namespace: dict[str, Any] = {
        "registry": registry,
        "Optional": Optional,
    }
    exec(source, namespace)
    handler = namespace["_handler"]
    handler.__name__ = f"tool_{_to_python_identifier(tool.name, set())}"
    return handler


def _to_python_identifier(value: str, used_names: set[str]) -> str:
    identifier = re.sub(r"[^0-9a-zA-Z_]", "_", value)
    if not identifier:
        identifier = "arg"
    if identifier[0].isdigit():
        identifier = f"arg_{identifier}"
    if keyword.iskeyword(identifier):
        identifier = f"{identifier}_arg"
    candidate = identifier
    suffix = 1
    while candidate in used_names:
        suffix += 1
        candidate = f"{identifier}_{suffix}"
    used_names.add(candidate)
    return candidate
