"""Tests for EidosBot Action Module."""

import tempfile
import pytest

from eidos.actions.shell import ShellExecutor
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch


def test_shell_executor_safe_commands():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist)
        executor = ShellExecutor(gatekeeper=gatekeeper)

        # Level 0 command (echo) is auto-allowed
        res = executor.execute("echo 'Hello Eidos'")
        assert res["success"] is True
        assert res["stdout"] == "Hello Eidos"
        assert res["exit_code"] == 0
        assert not res["denied"]


def test_shell_executor_denied_command():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, confirm_level_2_shell=True)
        # Mock rejection
        gatekeeper.custom_prompt_callback = lambda act, det, risk, reason: (False, False)
        executor = ShellExecutor(gatekeeper=gatekeeper)

        # Level 2 command without approval should be blocked
        res = executor.execute("python3 -c 'print(42)'")
        assert res["success"] is False
        assert res["denied"] is True
        assert "denied by safety gatekeeper" in res["error"].lower()


def test_shell_executor_allowlist_command():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        allowlist.add("python3 -c 'print(42)'")
        gatekeeper = Gatekeeper(allowlist=allowlist, confirm_level_2_shell=True)
        executor = ShellExecutor(gatekeeper=gatekeeper)

        # Should execute successfully without prompt
        res = executor.execute("python3 -c 'print(42)'")
        assert res["success"] is True
        assert res["stdout"] == "42"
        assert not res["denied"]


def test_shell_executor_background_command():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, confirm_level_2_shell=False)
        executor = ShellExecutor(gatekeeper=gatekeeper)

        res = executor.execute("sleep 30 &")
        assert res["success"] is True
        assert res.get("background") is True
        assert res.get("pid")
        assert res["duration_ms"] < 5000


def test_shell_executor_kill_switch_during_run():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        kill_switch = KillSwitch()
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        executor = ShellExecutor(gatekeeper=gatekeeper)

        kill_switch.trigger()
        res = executor.execute("echo 'test'")
        assert res["success"] is False
        assert "kill switch is active" in res["error"].lower()
