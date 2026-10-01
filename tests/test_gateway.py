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

from eidos.gateway.server import RemoteGatewayServer
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

        asyncio.run(_test())

    def test_prompt_endpoint(self):
        async def _test():
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


if __name__ == "__main__":
    unittest.main()
