"""Tests for EidosBot Remote Mobile Gateway."""

import asyncio
import json
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock

import aiohttp
from PIL import Image

from eidos.gateway.server import RemoteGatewayServer, resolve_gateway_token
from eidos.main import SecretRedactionFilter
from eidos.safety.risk_classifier import RiskLevel


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestRemoteGateway(unittest.TestCase):
    """Test remote server endpoints, authentication, and dual-channel confirmation."""

    @classmethod
    def setUpClass(cls):
        cls.port = get_free_port()
        cls.auth_token = "test-secret-token-123"

        # Mock agent loop
        cls.mock_agent = MagicMock()
        cls.mock_agent.kill_switch = MagicMock()
        cls.mock_agent.kill_switch.is_triggered.return_value = False
        cls.mock_agent.run_task.return_value = {"success": True, "summary": "All done"}

        # Mock screen capture with a tiny image
        cls.mock_screen = MagicMock()
        cls.mock_screen.capture.return_value = Image.new("RGB", (100, 100), color="blue")

        # Mock inputs simulator
        cls.mock_agent.inputs = MagicMock()
        cls.mock_agent.inputs.click.return_value = {"success": True, "x": 100, "y": 200}

        # Mock voice subsystem
        cls.mock_voice = MagicMock()
        cls.mock_voice.tts = MagicMock()
        cls.mock_voice.tts.is_available = True
        cls.mock_voice.tts.default_voice = "bm_george"
        cls.mock_voice.stt = MagicMock()
        cls.mock_voice.stt.is_available = True
        cls.mock_agent.voice = cls.mock_voice

        cls.server = RemoteGatewayServer(
            agent_loop=cls.mock_agent,
            host="127.0.0.1",
            port=cls.port,
            auth_token=cls.auth_token,
            screen_capture=cls.mock_screen,
        )
        cls.server.start()
        # Give server thread a moment to bind and start listening
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        time.sleep(0.2)

    def test_auth_verification(self):
        req_valid_header = MagicMock(headers={"Authorization": f"Bearer {self.auth_token}"}, query={})
        self.assertTrue(self.server._verify_auth(req_valid_header))

        req_valid_query = MagicMock(headers={}, query={"token": self.auth_token})
        self.assertTrue(self.server._verify_auth(req_valid_query))

        req_invalid = MagicMock(headers={"Authorization": "Bearer wrong"}, query={})
        self.assertFalse(self.server._verify_auth(req_invalid))

    def test_dashboard_endpoint(self):
        async def _test():
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{self.port}/") as resp:
                    self.assertEqual(resp.status, 200)
                    text = await resp.text()
                    self.assertIn("EidosBot Gateway", text)

        asyncio.run(_test())

    def test_status_endpoint_auth(self):
        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                # Unauthorized request
                async with session.get(f"http://127.0.0.1:{self.port}/api/status") as resp:
                    self.assertEqual(resp.status, 401)

                # Authorized request
                async with session.get(f"http://127.0.0.1:{self.port}/api/status", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.assertIn("state", data)

        asyncio.run(_test())

    def test_screenshot_endpoint(self):
        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{self.port}/api/screenshot", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.content_type, "image/jpeg")
                    body = await resp.read()
                    self.assertGreater(len(body), 50)

        asyncio.run(_test())

    def test_kill_and_reset_endpoints(self):
        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                # Trigger kill
                async with session.post(f"http://127.0.0.1:{self.port}/api/kill", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.mock_agent.kill_switch.trigger.assert_called()

                # Trigger reset
                async with session.post(f"http://127.0.0.1:{self.port}/api/reset", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.mock_agent.kill_switch.reset.assert_called()

                # Stop cancels a task that is still running.
                self.server.current_state = "thinking"
                self.mock_agent.cancel_task = MagicMock()
                async with session.post(f"http://127.0.0.1:{self.port}/api/stop", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.mock_agent.cancel_task.assert_called()

                # Stopping again after the reply finished must not cancel the
                # agent or emit a finish event that would replace that reply.
                self.server.current_state = "idle"
                self.mock_agent.cancel_task.reset_mock()
                events = []
                original_broadcast = self.server.broadcast_event

                def _capture_broadcast(event, data):
                    events.append(event)
                    return original_broadcast(event, data)

                self.server.broadcast_event = _capture_broadcast
                try:
                    async with session.post(f"http://127.0.0.1:{self.port}/api/stop", headers=headers) as resp:
                        self.assertEqual(resp.status, 200)
                finally:
                    self.server.broadcast_event = original_broadcast
                self.mock_agent.cancel_task.assert_not_called()
                self.assertNotIn("task_finished", events)

        asyncio.run(_test())

    def test_prompt_endpoint(self):
        async def _test():
            self.mock_agent.run_task.reset_mock()
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/prompt",
                    headers=headers,
                    json={"prompt": "check system disk space"},
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))

            await asyncio.sleep(0.1)
            self.mock_agent.run_task.assert_called_with("check system disk space", speak_summary=False)

        asyncio.run(_test())

    def test_confirmation_flow(self):
        """Test registering a confirmation, querying via REST, and approving remotely."""
        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}

            # Spawn request_confirmation in worker thread
            outcome = []
            def _request_thread():
                res = self.server.request_confirmation(
                    action_type="execute_shell",
                    details={"command": "systemctl restart nginx"},
                    risk_level=2,
                    reason="Restarting system service",
                    timeout_seconds=5.0,
                )
                outcome.append(res)

            t = threading.Thread(target=_request_thread)
            t.start()
            time.sleep(0.2)

            async with aiohttp.ClientSession() as session:
                # Query pending confirmations
                async with session.get(f"http://127.0.0.1:{self.port}/api/confirmations", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    confs = data.get("confirmations", [])
                    matching = [c for c in confs if c.get("action_type") == "execute_shell"]
                    self.assertGreaterEqual(len(matching), 1)
                    cid = matching[0]["id"]
                    self.assertEqual(matching[0]["action_type"], "execute_shell")

                # Approve confirmation remotely
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/confirmations/{cid}",
                    headers=headers,
                    json={"approved": True, "allowlist": True},
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    res_data = await resp.json()
                    self.assertTrue(res_data.get("ok"))
                    self.assertTrue(res_data.get("approved"))

            t.join(timeout=2.0)
            self.assertEqual(len(outcome), 1)
            self.assertEqual(outcome[0], (True, True))

        asyncio.run(_test())

    def test_composite_confirmation_callback(self):
        """Test dual-channel confirmation callback where desktop answers first."""
        desktop_mock = MagicMock(return_value=(True, False))
        dual_cb = self.server.create_composite_confirmation_callback(desktop_mock)

        approved, remember = dual_cb(
            "click_element", {"target_description": "OK button"}, RiskLevel.LEVEL_1_UI, "UI click"
        )
        self.assertTrue(approved)
        self.assertFalse(remember)
        desktop_mock.assert_called_once()

    def test_history_and_clear_endpoints(self):
        """Test retrieving history and clearing conversational turns."""
        self.mock_agent.conversation_history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                # History endpoint
                async with session.get(f"http://127.0.0.1:{self.port}/api/history", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.assertEqual(len(data.get("messages", [])), 2)

                # Clear endpoint
                async with session.post(f"http://127.0.0.1:{self.port}/api/clear", headers=headers) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.assertEqual(len(self.mock_agent.conversation_history), 0)

        asyncio.run(_test())

    def test_manifest_endpoint(self):
        """Test manifest.json serving for PWA."""
        async def _test():
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{self.port}/manifest.json") as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertIn("name", data)
                    self.assertEqual(data.get("display"), "standalone")

        asyncio.run(_test())

    def test_service_worker_endpoint(self):
        """Test sw.js service worker serving for PWA."""
        async def _test():
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{self.port}/sw.js") as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn("javascript", resp.content_type)
                    body = await resp.text()
                    self.assertIn("addEventListener", body)

        asyncio.run(_test())

    def test_click_endpoint(self):
        """Test injecting click coordinates to host desktop."""
        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/click",
                    headers=headers,
                    json={"x": 100, "y": 200, "button": "left"},
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                    self.mock_agent.inputs.click.assert_called_with(x=100, y=200, button="left")

        asyncio.run(_test())

    def test_type_endpoint(self):
        """Phone keyboard text and special keys are relayed to the desktop."""
        async def _test():
            self.mock_agent.inputs.relay_typing.return_value = {"success": True}
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/type",
                    headers=headers,
                    json={"text": "Hi", "backspaces": 1},
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    data = await resp.json()
                    self.assertTrue(data.get("ok"))
                self.mock_agent.inputs.relay_typing.assert_called_with("Hi", 1, "")

                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/type",
                    headers=headers,
                    json={"key": "enter"},
                ) as resp:
                    self.assertEqual(resp.status, 200)
                self.mock_agent.inputs.relay_typing.assert_called_with("", 0, "enter")

                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/type",
                    headers=headers,
                    json={"key": "f12"},
                ) as resp:
                    self.assertEqual(resp.status, 400)

                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/type",
                    json={"text": "Hi"},
                ) as resp:
                    self.assertEqual(resp.status, 401)

        asyncio.run(_test())

    def test_websocket_bidirectional(self):
        """Test WebSocket connection, initial status push, and ping/pong."""
        async def _test():
            ws_url = f"ws://127.0.0.1:{self.port}/ws?token={self.auth_token}"
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    # Initial message should be status_change
                    msg = await ws.receive_json(timeout=2.0)
                    self.assertEqual(msg.get("event"), "status_change")

                    # Send ping, expect pong
                    await ws.send_json({"type": "ping"})
                    pong_msg = await ws.receive_json(timeout=2.0)
                    self.assertEqual(pong_msg.get("event"), "pong")

        asyncio.run(_test())

    def test_secret_redaction_filter(self):
        """Test that tokens and query parameters are redacted from log messages."""
        import logging
        filt = SecretRedactionFilter(explicit_secrets={"super-secret-token-999"})

        rec1 = logging.LogRecord("test", logging.INFO, "", 0, "GET /ws?token=my-secret-pass HTTP/1.1", (), None)
        filt.filter(rec1)
        self.assertNotIn("my-secret-pass", rec1.msg)
        self.assertIn("***REDACTED***", rec1.msg)

        rec2 = logging.LogRecord("test", logging.INFO, "", 0, "Bot token: 8708075940:AAGicWDcLMEfr2-iCWxwJEmQ7zQ269u1n2k was used", (), None)
        filt.filter(rec2)
        self.assertNotIn("8708075940:AAGicWDcLMEfr2-iCWxwJEmQ7zQ269u1n2k", rec2.msg)
        self.assertIn("***TELEGRAM_BOT_TOKEN_REDACTED***", rec2.msg)

        rec3 = logging.LogRecord("test", logging.INFO, "", 0, "Explicit: super-secret-token-999 inside message", (), None)
        filt.filter(rec3)
        self.assertNotIn("super-secret-token-999", rec3.msg)
        self.assertIn("***REDACTED***", rec3.msg)

    def test_resolve_gateway_token(self):
        """Test resolving gateway auth token from parameter or env var."""
        import os
        # Explicit token
        self.assertEqual(resolve_gateway_token(token="explicit-token"), "explicit-token")

        # Env var
        os.environ["TEST_GW_TOKEN"] = "env-token-123"
        try:
            self.assertEqual(resolve_gateway_token(token_env_var="TEST_GW_TOKEN"), "env-token-123")
        finally:
            del os.environ["TEST_GW_TOKEN"]

    def test_tts_endpoints(self):
        """Test GET and POST /api/tts Kokoro synthesis streams."""
        import tempfile

        # Prepare dummy WAV file
        dummy_wav_data = b"RIFF....WAVEfmt ...."
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(dummy_wav_data)
            temp_path = tf.name

        self.mock_voice.tts.synthesize.return_value = temp_path

        async def _test():
            async with aiohttp.ClientSession() as session:
                # 1. Unauthorized
                async with session.get(f"http://127.0.0.1:{self.port}/api/tts?text=hi") as res:
                    self.assertEqual(res.status, 401)

                # 2. Empty text
                async with session.get(
                    f"http://127.0.0.1:{self.port}/api/tts?text=&token={self.auth_token}"
                ) as res:
                    self.assertEqual(res.status, 400)

                # 3. Valid GET synthesis
                # Re-write file since previous may have been cleaned up
                with open(temp_path, "wb") as f:
                    f.write(dummy_wav_data)
                async with session.get(
                    f"http://127.0.0.1:{self.port}/api/tts?text=Hello+Android&token={self.auth_token}"
                ) as res:
                    self.assertEqual(res.status, 200)
                    self.assertEqual(res.headers.get("Content-Type"), "audio/wav")
                    body = await res.read()
                    self.assertEqual(body, dummy_wav_data)

                # 4. Valid POST synthesis
                with open(temp_path, "wb") as f:
                    f.write(dummy_wav_data)
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/tts",
                    headers={"Authorization": f"Bearer {self.auth_token}"},
                    json={"text": "Hello via POST", "voice": "bm_george", "speed": 1.0},
                ) as res:
                    self.assertEqual(res.status, 200)
                    self.assertEqual(res.headers.get("Content-Type"), "audio/wav")
                    body = await res.read()
                    self.assertEqual(body, dummy_wav_data)

        asyncio.run(_test())

    def test_tts_split_endpoint(self):
        """Test GET and POST /api/tts/split chunking endpoint."""
        async def _test():
            async with aiohttp.ClientSession() as session:
                # 1. Unauthorized
                async with session.get(f"http://127.0.0.1:{self.port}/api/tts/split?text=test") as res:
                    self.assertEqual(res.status, 401)

                # 2. Valid GET split
                long_sample = "This is sentence one. " * 10
                async with session.get(
                    f"http://127.0.0.1:{self.port}/api/tts/split?text={long_sample}&token={self.auth_token}"
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.assertIsInstance(data.get("chunks"), list)
                    self.assertGreater(len(data.get("chunks")), 0)

                # 3. Valid POST split
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/tts/split",
                    headers={"Authorization": f"Bearer {self.auth_token}"},
                    json={"text": "First sentence. Second sentence with details."},
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.assertIsInstance(data.get("chunks"), list)

        asyncio.run(_test())

    def test_voices_endpoint(self):
        """Test GET /api/voices catalog discovery."""
        async def _test():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{self.port}/api/voices?token={self.auth_token}"
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.assertEqual(data.get("default_voice"), "bm_george")
                    self.assertIsInstance(data.get("voices"), list)
                    self.assertGreater(len(data.get("voices")), 0)

        asyncio.run(_test())

    def test_stt_endpoint(self):
        """Test POST /api/stt audio transcription."""
        self.mock_voice.stt.transcribe.return_value = "Hello from Android voice"

        async def _test():
            async with aiohttp.ClientSession() as session:
                # 1. Unauthorized
                async with session.post(f"http://127.0.0.1:{self.port}/api/stt") as res:
                    self.assertEqual(res.status, 401)

                # 2. Empty audio body
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/stt?token={self.auth_token}",
                    data=b"",
                ) as res:
                    self.assertEqual(res.status, 400)

        asyncio.run(_test())

    def test_new_chat_and_conversations_api(self):
        """Test POST /api/conversations/new, GET /api/conversations, and POST /api/conversations/switch."""
        self.mock_agent.start_new_conversation = MagicMock(return_value="conv_test_123")
        self.mock_agent.switch_conversation = MagicMock(return_value=True)

        async def _test():
            headers = {"Authorization": f"Bearer {self.auth_token}"}
            async with aiohttp.ClientSession() as session:
                # 1. Start new chat
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/conversations/new",
                    headers=headers,
                    json={"title": "Fresh Topic"},
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.assertEqual(data.get("conversation_id"), "conv_test_123")
                    self.assertEqual(data.get("title"), "Fresh Topic")

                # 2. Switch conversation
                async with session.post(
                    f"http://127.0.0.1:{self.port}/api/conversations/switch",
                    headers=headers,
                    json={"conversation_id": "conv_past_456"},
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.mock_agent.switch_conversation.assert_called_with("conv_past_456")

                # 3. List conversations
                async with session.get(
                    f"http://127.0.0.1:{self.port}/api/conversations",
                    headers=headers,
                ) as res:
                    self.assertEqual(res.status, 200)
                    data = await res.json()
                    self.assertTrue(data.get("ok"))
                    self.assertIn("conversations", data)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
