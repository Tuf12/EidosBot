"""Tests for Kernel Sandboxing via Bubblewrap (bwrap)."""

import os
import shutil
import tempfile
import pytest

from eidos.actions.shell import ShellExecutor
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch
from eidos.sandbox.bwrap import BubblewrapSandbox


def test_bwrap_available():
    """Verify that bubblewrap availability correctly returns a boolean."""
    avail = BubblewrapSandbox.is_available()
    assert isinstance(avail, bool)


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_sandbox_workspace_creation():
    """Verify BubblewrapSandbox creates designated workspace directories."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws_path = os.path.join(tmp_dir, "nonexistent_subfolder")
        assert not os.path.exists(ws_path)
        sandbox = BubblewrapSandbox(workspace_dir=ws_path)
        assert os.path.exists(ws_path)
        assert sandbox.workspace_dir == os.path.abspath(ws_path)


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_sandbox_write_inside_workspace():
    """Test 1: Write file inside workspace (Expect: Success)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox = BubblewrapSandbox(workspace_dir=tmp_dir)
        test_file = os.path.join(tmp_dir, "test_output.txt")
        test_payload = "EidosBot Sandbox Operational 2026"

        proc = sandbox.run(f"echo '{test_payload}' > {test_file}")

        assert proc.returncode == 0
        assert os.path.exists(test_file)
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        assert content == test_payload


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_sandbox_write_outside_workspace_rejected():
    """Test 2: Attempt rm or write outside workspace (Expect: Kernel Read-only file system error)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox = BubblewrapSandbox(workspace_dir=tmp_dir)

        # Attempt to write to /tmp_test (directly under root /)
        proc = sandbox.run("touch /tmp_test")
        assert proc.returncode != 0
        assert "Read-only file system" in proc.stderr
        assert not os.path.exists("/tmp_test")


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_sandbox_extra_writable_mount():
    """Verify extra writable paths are bound and modifiable inside the sandbox."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as extra_dir:
        sandbox = BubblewrapSandbox(workspace_dir=ws_dir, extra_writable=[extra_dir])

        extra_file = os.path.join(extra_dir, "extra.txt")
        proc = sandbox.run(f"echo 'extra data' > {extra_file}")

        assert proc.returncode == 0
        assert os.path.exists(extra_file)
        with open(extra_file, "r", encoding="utf-8") as f:
            assert f.read().strip() == "extra data"


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_sandbox_network_isolation():
    """Verify network access can be isolated via --unshare-net."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox_offline = BubblewrapSandbox(workspace_dir=tmp_dir, allow_network=False)
        cmd_args = sandbox_offline.build_command("echo ok")
        assert "--unshare-net" in cmd_args


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_shell_executor_sandboxed_auto_allowed():
    """Verify ShellExecutor auto-allows sandboxed workspace commands without prompting."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, confirm_level_2_shell=True)
        # Deny callback would fail un-sandboxed commands
        gatekeeper.custom_prompt_callback = lambda act, det, risk, reason: (False, False)

        executor = ShellExecutor(
            gatekeeper=gatekeeper,
            sandbox_config={"enabled": True, "default_workspace": ws_dir, "network_access": True},
        )

        test_file = os.path.join(ws_dir, "sandboxed_run.txt")
        res = executor.execute(f"echo 'Sandboxed Execution' > {test_file}", sandboxed=True)

        assert res["success"] is True
        assert res["sandboxed"] is True
        assert not res["denied"]
        assert os.path.exists(test_file)
        with open(test_file, "r", encoding="utf-8") as f:
            assert f.read().strip() == "Sandboxed Execution"


@pytest.mark.skipif(not BubblewrapSandbox.is_available(), reason="bwrap not installed on host")
def test_shell_executor_unsandboxed_requires_gatekeeper():
    """Verify ShellExecutor routes sandboxed=False commands through Gatekeeper approval."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, confirm_level_2_shell=True)
        # Mock user rejection
        gatekeeper.custom_prompt_callback = lambda act, det, risk, reason: (False, False)

        executor = ShellExecutor(
            gatekeeper=gatekeeper,
            sandbox_config={"enabled": True, "default_workspace": ws_dir},
        )

        # Mode B: Explicitly requesting host privilege
        res = executor.execute("python3 -c 'print(123)'", sandboxed=False)

        assert res["success"] is False
        assert res["denied"] is True
        assert res["sandboxed"] is False
        assert "denied by safety gatekeeper" in res["error"].lower()


def test_shell_executor_kill_switch_sandboxed():
    """Verify kill switch halts sandboxed execution immediately."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        kill_switch = KillSwitch()
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)

        executor = ShellExecutor(
            gatekeeper=gatekeeper,
            sandbox_config={"enabled": True, "default_workspace": ws_dir},
        )

        kill_switch.trigger()
        res = executor.execute("echo 'should not run'", sandboxed=True)
        assert res["success"] is False
        assert "kill switch is active" in res["error"].lower()
