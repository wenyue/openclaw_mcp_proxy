import logging
import os
import unittest
import warnings
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.main import create_app

warnings.filterwarnings(
    "ignore",
    message=r"Unclosed <MemoryObjectReceiveStream at .*?>",
    category=ResourceWarning,
)


class ProxyIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_app_token = os.environ.get("OPENCLAW_PROXY_APP_TOKEN")
        self._previous_openclaw_token = os.environ.get("OPENCLAW_PROXY_OPENCLAW_TOKEN")
        os.environ["OPENCLAW_PROXY_APP_TOKEN"] = ""
        os.environ["OPENCLAW_PROXY_OPENCLAW_TOKEN"] = ""
        self._client_context = TestClient(create_app())
        self.client = self._client_context.__enter__()

    def tearDown(self) -> None:
        self._client_context.__exit__(None, None, None)
        self._restore_env("OPENCLAW_PROXY_APP_TOKEN", self._previous_app_token)
        self._restore_env(
            "OPENCLAW_PROXY_OPENCLAW_TOKEN",
            self._previous_openclaw_token,
        )

    def test_registered_mcp_url_accepts_initialize_request(self) -> None:
        session = self._create_chat_session()
        mcp_path = urlparse(session["mcp_url"]).path

        response = self.client.post(
            mcp_path,
            headers=self._mcp_headers(),
            json=self._initialize_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)

    def test_header_routed_mcp_endpoint_accepts_initialize_request(self) -> None:
        session = self._create_chat_session()

        response = self.client.post(
            "/v1/mcp/",
            headers=self._mcp_headers(
                {"X-OpenClaw-Chat-Session": session["chat_session_id"]},
            ),
            json=self._initialize_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)

    def test_bridge_disconnect_does_not_log_an_error_for_normal_close(self) -> None:
        session = self._create_chat_session()

        with self.assertNoLogs("openclaw_mcp_proxy", level="ERROR"):
            with self.client.websocket_connect(
                f"/v1/chat/sessions/{session['chat_session_id']}/bridge",
            ):
                pass

    def test_delete_chat_session_endpoint_returns_ok(self) -> None:
        session = self._create_chat_session()

        response = self.client.delete(
            f"/v1/chat/sessions/{session['chat_session_id']}",
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"ok": True}, response.json())

    def _create_chat_session(self) -> dict:
        response = self.client.post(
            "/v1/chat/sessions",
            json={
                "user_id": "test-user",
                "device_id": "test-device",
                "device_name": "test-device-name",
                "app_version": "1.0.0",
                "chat_id": "test-chat",
                "tools": [],
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _initialize_payload(self) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "proxy-test-client",
                    "version": "1.0.0",
                },
            },
        }

    def _mcp_headers(self, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
        }
        if extra_headers is not None:
            headers.update(extra_headers)
        return headers

    def _restore_env(self, key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
            return
        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
