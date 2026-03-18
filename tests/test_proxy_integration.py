import logging
import os
import unittest
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.main import create_app


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
        registration = self._register_chat()
        mcp_path = urlparse(registration["mcp_url"]).path

        response = self.client.post(
            mcp_path,
            headers=self._mcp_headers(),
            json=self._initialize_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)

    def test_header_routed_mcp_endpoint_accepts_initialize_request(self) -> None:
        registration = self._register_chat()

        response = self.client.post(
            "/mcp/",
            headers=self._mcp_headers(
                {"X-OpenClaw-Chat-Session": registration["chat_session_id"]},
            ),
            json=self._initialize_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)

    def test_bridge_disconnect_does_not_log_an_error_for_normal_close(self) -> None:
        registration = self._register_chat()

        with self.assertNoLogs("openclaw_mcp_proxy", level="ERROR"):
            with self.client.websocket_connect(
                f"/api/chats/bridge?chat_session_id={registration['chat_session_id']}",
            ):
                pass

    def _register_chat(self) -> dict:
        response = self.client.post(
            "/api/chats/register",
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
