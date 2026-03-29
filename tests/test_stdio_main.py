from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from mcp.types import ClientCapabilities, Implementation, InitializeRequestParams

from app.stdio_main import (
    build_transport_headers,
    extract_session_id,
    parse_args,
    resolve_mcp_url,
    run,
)


class StdioMainTest(unittest.TestCase):
    def test_resolve_mcp_url_joins_base_url(self) -> None:
        self.assertEqual(
            "http://127.0.0.1:8000/v1/mcp",
            resolve_mcp_url("http://127.0.0.1:8000"),
        )

    def test_parse_args_does_not_require_session_id(self) -> None:
        args = parse_args([])

        self.assertEqual("http://127.0.0.1:8000", args.proxy_base_url)

    def test_build_transport_headers_include_token_and_session_id(self) -> None:
        self.assertEqual(
            {
                "Authorization": "Bearer secret-token",
                "MCP-Session-Id": "session-1",
            },
            build_transport_headers(
                openclaw_token="secret-token",
                session_id="session-1",
            ),
        )

    def test_extract_session_id_reads_mcp_session_id_from_initialize_params(self) -> None:
        params = InitializeRequestParams(
            protocolVersion="2025-03-26",
            capabilities=ClientCapabilities(),
            clientInfo=Implementation(name="proxy-test-client", version="1.0.0"),
            mcpSessionId="session-1",
        )

        self.assertEqual(
            "session-1",
            extract_session_id(params),
        )

    def test_extract_session_id_requires_mcp_session_id(self) -> None:
        params = InitializeRequestParams(
            protocolVersion="2025-03-26",
            capabilities=ClientCapabilities(),
            clientInfo=Implementation(name="proxy-test-client", version="1.0.0"),
        )

        with self.assertRaisesRegex(ValueError, "mcpSessionId"):
            extract_session_id(params)

    def test_run_starts_stdio_server_without_startup_session_check(self) -> None:
        server = Mock()
        with (
            patch("app.stdio_main.build_chat_stdio_mcp_server", return_value=server) as build_server,
            patch("app.stdio_main.load_config") as load_config,
            patch("app.stdio_main.anyio.run") as run_async,
        ):
            load_config.return_value.openclaw_token = "secret-token"
            exit_code = run([])

        self.assertEqual(0, exit_code)
        build_server.assert_called_once_with(
            proxy_base_url="http://127.0.0.1:8000",
            openclaw_token="secret-token",
        )
        run_async.assert_called_once_with(server.run_stdio_async)


if __name__ == "__main__":
    unittest.main()
