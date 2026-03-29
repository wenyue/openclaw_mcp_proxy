import os
import queue
import threading
import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from anyio.streams.memory import MemoryObjectReceiveStream
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.routing import Mount
from starlette.websockets import WebSocketDisconnect

from app.chat_session_registry import ChatSessionRegistry
from app.models import InvokeResultMessage
from app.main import create_app


def _close_receive_stream_on_gc(self) -> None:
    if not self._closed:
        self.close()


MemoryObjectReceiveStream.__del__ = _close_receive_stream_on_gc

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

    def test_header_routed_mcp_endpoint_accepts_initialize_request(self) -> None:
        session = self._create_chat_session()

        response = self.client.post(
            "/v1/mcp/",
            headers=self._mcp_headers(
                {"X-OpenClaw-Chat-Session": session["mcpSessionId"]},
            ),
            json=self._initialize_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)

    def test_bridge_disconnect_does_not_log_an_error_for_normal_close(self) -> None:
        session = self._create_chat_session()

        with self.assertNoLogs("openclaw_mcp_proxy", level="ERROR"):
            with self.client.websocket_connect(
                f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
            ):
                pass

    def test_delete_chat_session_endpoint_returns_ok(self) -> None:
        session = self._create_chat_session()

        response = self.client.delete(
            f"/v1/chat/sessions/{session['mcpSessionId']}",
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"ok": True}, response.json())

    def test_create_chat_session_accepts_camel_case_tool_schema_without_path(self) -> None:
        response = self.client.post(
            "/v1/chat/sessions",
            json={
                "userId": "test-user",
                "deviceId": "test-device",
                "deviceName": "test-device-name",
                "appVersion": "1.0.0",
                "tools": [
                    {
                        "name": "echo_text",
                        "description": "Echo text.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                },
                            },
                        },
                    },
                ],
            },
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"mcpSessionId": response.json()["mcpSessionId"]}, response.json())

    def test_delete_chat_session_closes_attached_bridge(self) -> None:
        session = self._create_chat_session()

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as websocket:
            response = self.client.delete(
                f"/v1/chat/sessions/{session['mcpSessionId']}",
            )

            self.assertEqual(200, response.status_code, response.text)
            self._assert_bridge_closed(websocket)

    def test_expired_session_read_path_closes_attached_bridge(self) -> None:
        session = self._create_chat_session()

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as websocket:
            self._expire_session_in_registry(session["mcpSessionId"])

            response = self.client.post(
                f"/v1/mcp/{session['mcpSessionId']}",
                headers=self._mcp_headers(),
                json=self._initialize_payload(),
            )

            self.assertIn(response.status_code, {400, 404}, response.text)
            self._assert_bridge_closed(websocket)

    def test_second_bridge_attach_is_rejected_with_first_bridge_preserved(self) -> None:
        session = self._create_chat_session_with_tool()
        mcp_path = f"/v1/mcp/{session['mcpSessionId']}"

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as first_bridge:
            with self.assertRaises(WebSocketDisconnect) as second_disconnect:
                with self.client.websocket_connect(
                    f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
                ):
                    pass

            self.assertEqual(4409, second_disconnect.exception.code)

            future = self._submit_tool_call(self.client, mcp_path)
            invoke = self._receive_json(first_bridge)

            self.assertEqual("invoke_tool", invoke["type"])
            self.assertEqual("demo_tool", invoke["toolName"])

            first_bridge.send_json(
                {
                    "type": "invoke_result",
                    "mcpSessionId": session["mcpSessionId"],
                    "requestId": invoke["requestId"],
                    "ok": True,
                    "content": {"ok": True},
                },
            )
            response = future.result(timeout=2)

            self.assertEqual(200, response.status_code, response.text)
            self.assertIn('"isError":false', response.text)

    def test_delete_chat_session_is_idempotent(self) -> None:
        session = self._create_chat_session()

        first = self.client.delete(f"/v1/chat/sessions/{session['mcpSessionId']}")
        second = self.client.delete(f"/v1/chat/sessions/{session['mcpSessionId']}")

        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(200, second.status_code, second.text)
        self.assertEqual({"ok": True}, second.json())

    def test_tool_call_without_bridge_returns_not_bridged_error(self) -> None:
        session = self._create_chat_session_with_tool()
        response = self.client.post(
            f"/v1/mcp/{session['mcpSessionId']}",
            headers=self._mcp_headers(),
            json=self._tool_call_payload(),
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('"isError":true', response.text)
        self.assertIn("Chat bridge is not connected.", response.text)

    def test_inflight_tool_call_fails_when_session_is_invalidated(self) -> None:
        session = self._create_chat_session_with_tool()
        mcp_path = f"/v1/mcp/{session['mcpSessionId']}"

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as websocket:
            future = self._submit_tool_call(self.client, mcp_path)
            invoke = self._receive_json(websocket)

            self.assertEqual("invoke_tool", invoke["type"])

            response = self.client.delete(
                f"/v1/chat/sessions/{session['mcpSessionId']}",
            )
            self.assertEqual(200, response.status_code, response.text)

            tool_response = future.result(timeout=2)
            self.assertEqual(200, tool_response.status_code, tool_response.text)
            self.assertIn('"isError":true', tool_response.text)
            self.assertIn("Chat session was unregistered.", tool_response.text)
            self._assert_bridge_closed(websocket)

    def test_ttl_cleanup_skips_session_with_pending_call(self) -> None:
        session = self._create_chat_session_with_tool()
        mcp_path = f"/v1/mcp/{session['mcpSessionId']}"

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as websocket:
            future = self._submit_tool_call(self.client, mcp_path)
            invoke = self._receive_json(websocket)

            self._expire_session_in_registry(session["mcpSessionId"])
            self._run_cleanup_once()

            websocket.send_json(
                {
                    "type": "invoke_result",
                    "mcpSessionId": session["mcpSessionId"],
                    "requestId": invoke["requestId"],
                    "ok": True,
                    "content": {"ok": True},
                },
            )
            response = future.result(timeout=2)

            self.assertEqual(200, response.status_code, response.text)
            self.assertIn('"isError":false', response.text)

    def test_read_path_expiry_skips_session_with_pending_call(self) -> None:
        session = self._create_chat_session_with_tool()
        mcp_path = f"/v1/mcp/{session['mcpSessionId']}"

        with self.client.websocket_connect(
            f"/v1/chat/sessions/{session['mcpSessionId']}/bridge",
        ) as websocket:
            future = self._submit_tool_call(self.client, mcp_path)
            invoke = self._receive_json(websocket)

            self._expire_session_in_registry(session["mcpSessionId"])
            initialize_response = self.client.post(
                mcp_path,
                headers=self._mcp_headers(),
                json=self._initialize_payload(),
            )

            self.assertEqual(200, initialize_response.status_code, initialize_response.text)

            websocket.send_json(
                {
                    "type": "invoke_result",
                    "mcpSessionId": session["mcpSessionId"],
                    "requestId": invoke["requestId"],
                    "ok": True,
                    "content": {"ok": True},
                },
            )
            response = future.result(timeout=2)

            self.assertEqual(200, response.status_code, response.text)
            self.assertIn('"isError":false', response.text)

    def test_complete_tool_call_ignores_late_result_after_future_completion_race(self) -> None:
        registry = ChatSessionRegistry(session_ttl_seconds=300, tool_timeout_seconds=1)
        request_id = "session-1:1"

        class RaceyFuture:
            def done(self) -> bool:
                return False

            def set_result(self, value) -> None:
                raise asyncio.InvalidStateError()

            def set_exception(self, error: Exception) -> None:
                raise asyncio.InvalidStateError()

        async def seed() -> None:
            session = await registry.register(
                chat_session_id="session-1",
                user_id="test-user",
                device_id="test-device",
                device_name="test-device-name",
                tools=[],
            )
            session.pending_calls[request_id] = RaceyFuture()

        self.client.portal.call(seed)
        self.client.portal.call(
            registry.complete_tool_call,
            InvokeResultMessage(
                mcpSessionId="session-1",
                requestId=request_id,
                ok=True,
                content={"ok": True},
            ),
        )

    def test_failed_bridge_accept_does_not_leave_stale_bridge(self) -> None:
        session = self._create_chat_session_with_tool()
        bridge_path = f"/v1/chat/sessions/{session['mcpSessionId']}/bridge"

        with patch.object(WebSocket, "accept", autospec=True, side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                with self.client.websocket_connect(bridge_path):
                    pass

        with self.client.websocket_connect(bridge_path):
            pass

    def _create_chat_session(self) -> dict:
        return self._create_chat_session_with_client(self.client)

    def _create_chat_session_with_client(
        self,
        client: TestClient,
        *,
        tools: list[dict] | None = None,
    ) -> dict:
        response = client.post(
            "/v1/chat/sessions",
            json={
                "userId": "test-user",
                "deviceId": "test-device",
                "deviceName": "test-device-name",
                "appVersion": "1.0.0",
                "tools": tools or [],
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _create_chat_session_with_tool(self) -> dict:
        return self._create_chat_session_with_client(
            self.client,
            tools=[
                {
                    "name": "demo_tool",
                    "description": "Demo tool.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                        },
                    },
                },
            ],
        )

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

    def _tool_call_payload(self, *, request_id: int = 2) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "demo_tool",
                "arguments": {"text": "hello"},
            },
        }

    def _submit_tool_call(self, client: TestClient, mcp_path: str):
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            client.post,
            mcp_path,
            headers=self._mcp_headers(),
            json=self._tool_call_payload(),
        )
        self.addCleanup(executor.shutdown, wait=False)
        return future

    def _receive_json(self, websocket):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(websocket.receive_json)
            return future.result(timeout=2)

    def _assert_bridge_closed(self, websocket) -> None:
        result_queue: queue.Queue[BaseException | str] = queue.Queue(maxsize=1)

        def receive() -> None:
            try:
                result_queue.put(websocket.receive_text())
            except Exception as exc:
                result_queue.put(exc)

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            result = result_queue.get(timeout=1)
        except queue.Empty:
            self.fail("Expected bridge websocket to close promptly.")
        if isinstance(result, WebSocketDisconnect):
            return
        if isinstance(result, Exception):
            self.fail(f"Expected WebSocketDisconnect, got {type(result).__name__}: {result}")
        self.fail("Expected bridge websocket to close, but it stayed open.")

    def _expire_session_in_registry(self, chat_session_id: str) -> None:
        registry = self._registry()

        async def expire() -> None:
            session = registry._sessions[chat_session_id]
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        self.client.portal.call(expire)

    def _run_cleanup_once(self) -> None:
        self.client.portal.call(self._registry().cleanup_expired)

    def _registry(self):
        for route in self.client.app.routes:
            if isinstance(route, Mount) and route.path == "/v1/mcp":
                return route.app._registry
        self.fail("Could not locate chat session registry.")

    def _restore_env(self, key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
            return
        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
