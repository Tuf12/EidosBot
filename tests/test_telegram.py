"""Tests for EidosBot Telegram Gateway & Remote Approvals."""

import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from eidos.gateway.telegram_bot import TelegramGateway, resolve_bot_token
from eidos.safety.risk_classifier import RiskLevel


class TestTelegramTokenResolution(unittest.TestCase):
    """Test token resolution logic across explicit param, env vars, and token file."""

    def test_explicit_token(self):
        token = resolve_bot_token(explicit_token="123456:ABC-DEF")
        self.assertEqual(token, "123456:ABC-DEF")

    def test_env_var_fallback(self):
        with patch.dict(os.environ, {"TEST_TG_TOKEN": "bot-env-token-xyz"}):
            token = resolve_bot_token(explicit_token="", env_var="TEST_TG_TOKEN")
            self.assertEqual(token, "bot-env-token-xyz")

    def test_file_fallback(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("  secret-file-token-999 \n")
            temp_path = f.name

        try:
            token = resolve_bot_token(
                explicit_token="",
                env_var="NON_EXISTENT_ENV_KEY_12345",
                file_path=temp_path,
            )
            self.assertEqual(token, "secret-file-token-999")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_token_missing(self):
        token = resolve_bot_token(
            explicit_token="",
            env_var="NON_EXISTENT_ENV_KEY_XYZ",
            file_path="/tmp/non_existent_token_file_12345.token",
        )
        self.assertIsNone(token)


class TestTelegramGatewayUnit(unittest.IsolatedAsyncioTestCase):
    """Unit tests for TelegramGateway handlers, authorization, and notifications."""

    def setUp(self):
        self.mock_agent = MagicMock()
        self.mock_agent.kill_switch = MagicMock()
        self.mock_agent.kill_switch.is_triggered.return_value = False
        self.mock_agent.run_task.return_value = {"success": True, "summary": "Task output simulated."}
        self.mock_agent.jobs = None
        self.mock_agent.clock = None
        self.mock_agent.context = MagicMock()

        self.allowed_users = [10001, 10002]
        self.gw = TelegramGateway(
            agent_loop=self.mock_agent,
            token="dummy_token_1234",
            allowed_user_ids=self.allowed_users,
        )

    def test_authorization_check(self):
        # Authorized user
        update_auth = MagicMock()
        update_auth.effective_user.id = 10001
        self.assertTrue(self.gw._is_authorized(update_auth))

        # Second authorized user
        update_auth2 = MagicMock()
        update_auth2.effective_user.id = 10002
        self.assertTrue(self.gw._is_authorized(update_auth2))

        # Unauthorized user
        update_unauth = MagicMock()
        update_unauth.effective_user.id = 99999
        self.assertFalse(self.gw._is_authorized(update_unauth))

        # No effective user
        update_none = MagicMock()
        update_none.effective_user = None
        self.assertFalse(self.gw._is_authorized(update_none))

    def test_authorization_empty_whitelist(self):
        gw_no_users = TelegramGateway(
            agent_loop=self.mock_agent,
            token="dummy",
            allowed_user_ids=[],
        )
        update = MagicMock()
        update.effective_user.id = 10001
        self.assertFalse(gw_no_users._is_authorized(update))

    async def test_handle_start_authorized(self):
        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_start(update, MagicMock())
        update.message.reply_text.assert_awaited_once()
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("EidosBot Telegram Gateway Online", sent_text)

    async def test_handle_start_unauthorized(self):
        update = MagicMock()
        update.effective_user.id = 99999
        update.message.reply_text = AsyncMock()

        await self.gw._handle_start(update, MagicMock())
        update.message.reply_text.assert_awaited_once()
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Unauthorized user", sent_text)

    async def test_handle_status(self):
        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_status(update, MagicMock())
        update.message.reply_text.assert_awaited_once()
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("EidosBot System Status", sent_text)

    async def test_handle_jobs_no_runner(self):
        self.mock_agent.jobs = None
        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_jobs(update, MagicMock())
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("not initialized", sent_text)

    async def test_handle_jobs_active_runner(self):
        mock_jobs = MagicMock()
        mock_jobs.get_status.return_value = {
            "job_name": "test-patent-eval",
            "job_id": "job_abc",
            "skill_name": "invention-evaluator",
            "status": "in_progress",
            "total_items": 10,
            "completed_items": 4,
            "failed_items": 0,
            "pending_items": 6,
            "progress_percent": 40.0,
            "active_item": {"id": "item_5"},
        }
        self.mock_agent.jobs = mock_jobs

        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_jobs(update, MagicMock())
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("test-patent-eval", sent_text)
        self.assertIn("Progress:* 4/10", sent_text)

    async def test_handle_pause_and_resume(self):
        mock_jobs = MagicMock()
        mock_jobs.pause.return_value = {"success": True, "job_id": "job_1"}
        mock_jobs.resume.return_value = {"success": True, "job_id": "job_1"}
        self.mock_agent.jobs = mock_jobs

        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_pause(update, MagicMock())
        mock_jobs.pause.assert_called_once()

        await self.gw._handle_resume(update, MagicMock())
        mock_jobs.resume.assert_called_once()

    async def test_handle_kill(self):
        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_kill(update, MagicMock())
        self.mock_agent.kill_switch.trigger.assert_called_once()
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("EMERGENCY STOP TRIPPED", sent_text)

    async def test_handle_clear(self):
        update = MagicMock()
        update.effective_user.id = 10001
        update.message.reply_text = AsyncMock()

        await self.gw._handle_clear(update, MagicMock())
        self.mock_agent.context.clear.assert_called_once()
        sent_text = update.message.reply_text.call_args[0][0]
        self.assertIn("Active conversation reset", sent_text)

    async def test_handle_user_message_triggers_task(self):
        update = MagicMock()
        update.effective_user.id = 10001
        update.effective_chat.id = 10001
        update.message.text = "Evaluate this website for me"
        update.message.reply_text = AsyncMock()

        self.gw.broadcast_message = MagicMock()
        await self.gw._handle_message(update, MagicMock())
        update.message.reply_text.assert_awaited_once()

        # Allow background execution thread to run
        time.sleep(0.1)
        self.mock_agent.run_task.assert_called_with("Evaluate this website for me")
        self.gw.broadcast_message.assert_called_once()


class TestTelegramRemoteApprovals(unittest.IsolatedAsyncioTestCase):
    """Test interactive approval callbacks and decision handling."""

    def setUp(self):
        self.mock_agent = MagicMock()
        self.mock_agent.kill_switch = MagicMock()
        self.mock_agent.gatekeeper = MagicMock()

        self.gw = TelegramGateway(
            agent_loop=self.mock_agent,
            token="dummy_token_1234",
            allowed_user_ids=[10001],
        )

    async def test_approval_callback_approve(self):
        event = threading.Event()
        approval_state = {
            "id": "test-id-1",
            "action_type": "shell_exec",
            "decision": None,
            "event": event,
            "sent_messages": [],
        }
        self.gw._pending_confirmations["test-id-1"] = approval_state

        update = MagicMock()
        update.effective_user.id = 10001
        update.callback_query.from_user.id = 10001
        update.callback_query.data = "confirm:test-id-1:approve"
        update.callback_query.answer = AsyncMock()

        await self.gw._handle_callback_query(update, MagicMock())

        self.assertTrue(event.is_set())
        self.assertEqual(approval_state["result"], (True, False))
        update.callback_query.answer.assert_awaited_once_with(text="Command approved.")

    async def test_approval_callback_reject(self):
        event = threading.Event()
        approval_state = {
            "id": "test-id-2",
            "action_type": "shell_exec",
            "decision": None,
            "event": event,
            "sent_messages": [],
        }
        self.gw._pending_confirmations["test-id-2"] = approval_state

        update = MagicMock()
        update.effective_user.id = 10001
        update.callback_query.from_user.id = 10001
        update.callback_query.data = "confirm:test-id-2:reject"
        update.callback_query.answer = AsyncMock()

        await self.gw._handle_callback_query(update, MagicMock())

        self.assertTrue(event.is_set())
        self.assertEqual(approval_state["result"], (False, False))
        update.callback_query.answer.assert_awaited_once_with(text="Command rejected.")

    async def test_approval_callback_always_allow(self):
        event = threading.Event()
        approval_state = {
            "id": "test-id-3",
            "action_type": "shell_exec",
            "decision": None,
            "event": event,
            "command": "git push origin main",
            "sent_messages": [],
        }
        self.gw._pending_confirmations["test-id-3"] = approval_state

        update = MagicMock()
        update.effective_user.id = 10001
        update.callback_query.from_user.id = 10001
        update.callback_query.data = "confirm:test-id-3:always"
        update.callback_query.answer = AsyncMock()

        await self.gw._handle_callback_query(update, MagicMock())

        self.assertTrue(event.is_set())
        self.assertEqual(approval_state["result"], (True, True))
        self.mock_agent.gatekeeper.add_to_allowlist.assert_called_with("git push origin main")
        update.callback_query.answer.assert_awaited_once_with(text="Command permanently added to allowlist.")

    async def test_approval_callback_emergency_kill(self):
        event = threading.Event()
        approval_state = {
            "id": "test-id-4",
            "action_type": "shell_exec",
            "decision": None,
            "event": event,
            "sent_messages": [],
        }
        self.gw._pending_confirmations["test-id-4"] = approval_state

        update = MagicMock()
        update.effective_user.id = 10001
        update.callback_query.from_user.id = 10001
        update.callback_query.data = "confirm:test-id-4:kill"
        update.callback_query.answer = AsyncMock()

        await self.gw._handle_callback_query(update, MagicMock())

        self.mock_agent.kill_switch.trigger.assert_called_once()
        self.assertTrue(event.is_set())
        self.assertEqual(approval_state["result"], (False, False))
        update.callback_query.answer.assert_awaited_once_with(text="🚨 EMERGENCY KILL SWITCH TRIGGERED")

    async def test_unauthorized_callback_query(self):
        update = MagicMock()
        update.callback_query.from_user.id = 99999
        update.callback_query.data = "confirm:test-id-5:approve"
        update.callback_query.answer = AsyncMock()

        await self.gw._handle_callback_query(update, MagicMock())
        update.callback_query.answer.assert_awaited_once_with("⛔ Unauthorized user ID", show_alert=True)


class TestTelegramCombineCallbacks(unittest.TestCase):
    """Test multi-channel confirmation callback combining."""

    def test_combine_fastest_approves(self):
        def cb1(act, det, risk, reason):
            time.sleep(0.05)
            return (True, False)

        def cb2(act, det, risk, reason):
            time.sleep(0.5)
            return (False, False)

        combined = TelegramGateway.combine_callbacks(cb1, cb2)
        decision, allowlist = combined("shell_exec", {"command": "ls -la"}, RiskLevel.LEVEL_2_SHELL, "list files")
        self.assertTrue(decision)
        self.assertFalse(allowlist)

    def test_combine_fastest_rejects(self):
        def cb1(act, det, risk, reason):
            time.sleep(0.5)
            return (True, False)

        def cb2(act, det, risk, reason):
            time.sleep(0.05)
            return (False, False)

        combined = TelegramGateway.combine_callbacks(cb1, cb2)
        decision, allowlist = combined("shell_exec", {"command": "rm -rf /tmp/foo"}, RiskLevel.LEVEL_3_DESTRUCTIVE, "cleanup")
        self.assertFalse(decision)

    def test_combine_single_callback(self):
        def cb1(act, det, risk, reason):
            return (True, False)

        combined = TelegramGateway.combine_callbacks(cb1)
        self.assertEqual(combined("shell_exec", {"command": "echo test"}, RiskLevel.LEVEL_1_UI, "echo"), (True, False))

    def test_combine_ignores_none_channel(self):
        def cb_inactive(act, det, risk, reason):
            # Simulated inactive channel that returns None immediately
            return None

        def cb_desktop(act, det, risk, reason):
            time.sleep(0.05)
            return (True, False)

        combined = TelegramGateway.combine_callbacks(cb_inactive, cb_desktop)
        decision, allowlist = combined("shell_exec", {"command": "curl http://example.com"}, RiskLevel.LEVEL_2_SHELL, "download")
        self.assertTrue(decision)
        self.assertFalse(allowlist)


if __name__ == "__main__":
    unittest.main()
