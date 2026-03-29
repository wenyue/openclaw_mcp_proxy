from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from app.stdio_main import (
    build_chat_stdio_mcp_server,
    parse_args,
    resolve_session_mcp_url,
    run,
)


class StdioMainTest(unittest.TestCase):
    def test_resolve_session_mcp_url_joins_base_url_and_session_id(self) -> None:
        self.assertEqual(
            "http://127.0.0.1:8000/v1/mcp/session-1",
            resolve_session_mcp_url("http://127.0.0.1:8000", "session-1"),
        )

    def test_parse_args_accepts_required_chat_session_id(self) -> None:
        args = parse_args(["--chat-session-id", "session-1"])

        self.assertEqual("session-1", args.chat_session_id)

    def test_build_chat_stdio_mcp_server_creates_proxy_with_headers(self) -> None:
        with patch("app.stdio_main.create_proxy") as create_proxy:
            build_chat_stdio_mcp_server(
                proxy_base_url="http://127.0.0.1:8000",
                chat_session_id="session-1",
                openclaw_token="secret-token",
            )

        create_proxy.assert_called_once()
        target = create_proxy.call_args.args[0]
        self.assertEqual(
            "http://127.0.0.1:8000/v1/mcp/session-1",
            target.mcpServers["openclaw-chat"].url,
        )
        self.assertEqual(
            {"Authorization": "Bearer secret-token"},
            target.mcpServers["openclaw-chat"].headers,
        )

    def test_run_returns_exit_code_1_when_session_is_unknown(self) -> None:
        stderr = io.StringIO()
        with (
            patch("app.stdio_main.ensure_session_exists", return_value=False),
            redirect_stderr(stderr),
        ):
            exit_code = run(["--chat-session-id", "missing-session"])

        self.assertEqual(1, exit_code)
        self.assertIn("Unknown chat session", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
