from __future__ import annotations

import logging


logger = logging.getLogger("openclaw_mcp_proxy")


def log_tool_call(
    *,
    chat_session_id: str,
    user_id: str,
    tool_name: str,
    request_id: str,
) -> None:
    logger.info(
        "tool_call chat_session_id=%s user_id=%s tool=%s request_id=%s",
        chat_session_id,
        user_id,
        tool_name,
        request_id,
    )


def log_tool_result(
    *,
    chat_session_id: str,
    user_id: str,
    tool_name: str,
    request_id: str,
    ok: bool,
) -> None:
    logger.info(
        "tool_result chat_session_id=%s user_id=%s tool=%s request_id=%s ok=%s",
        chat_session_id,
        user_id,
        tool_name,
        request_id,
        ok,
    )
