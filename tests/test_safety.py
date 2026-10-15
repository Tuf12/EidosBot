"""Tests for EidosBot Safety Module."""

import tempfile
import pytest

from eidos.safety.allowlist import AllowlistManager
from eidos.safety.audit import AuditLogger
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskClassifier, RiskLevel


def test_risk_classifier_levels():
    # Level 0
    level, _ = RiskClassifier.classify_action("screenshot", {})
    assert level == RiskLevel.LEVEL_0_PASSIVE

    level, _ = RiskClassifier.classify_shell_command("ls -la")
    assert level == RiskLevel.LEVEL_0_PASSIVE

    level, _ = RiskClassifier.classify_shell_command("git status")
    assert level == RiskLevel.LEVEL_0_PASSIVE

    # Level 0 OptimalX read-only tools
    for opt_tool in (
        "optimalx_search_notes", "optimalx_read_note", "optimalx_list_folders",
        "optimalx_read_notex_doc", "optimalx_list_notex_docs", "optimalx_list_pending_reviews"
    ):
        lvl, _ = RiskClassifier.classify_action(opt_tool, {})
        assert lvl == RiskLevel.LEVEL_0_PASSIVE

    # Level 1
    level, _ = RiskClassifier.classify_action("click", {"x": 100, "y": 200})
    assert level == RiskLevel.LEVEL_1_UI

    level, _ = RiskClassifier.classify_action("type_text", {"text": "hello"})
    assert level == RiskLevel.LEVEL_1_UI

    level, _ = RiskClassifier.classify_action("press_key", {"key": "enter"})
    assert level == RiskLevel.LEVEL_1_UI

    # Level 1 OptimalX mutation/review tools
    for opt_write in ("optimalx_write_note", "optimalx_accept_pending_review", "optimalx_reject_pending_review"):
        lvl, _ = RiskClassifier.classify_action(opt_write, {})
        assert lvl == RiskLevel.LEVEL_1_UI

    # Level 2
    level, _ = RiskClassifier.classify_shell_command("mkdir new_folder")
    assert level == RiskLevel.LEVEL_2_SHELL

    level, _ = RiskClassifier.classify_shell_command("python3 script.py")
    assert level == RiskLevel.LEVEL_2_SHELL

    # Level 3
    level, _ = RiskClassifier.classify_shell_command("rm -rf /tmp/test")
    assert level == RiskLevel.LEVEL_3_DESTRUCTIVE

    level, _ = RiskClassifier.classify_shell_command("chmod -R 777 /")
    assert level == RiskLevel.LEVEL_3_DESTRUCTIVE

    level, _ = RiskClassifier.classify_shell_command("sudo systemctl restart nginx")
    assert level == RiskLevel.LEVEL_3_DESTRUCTIVE


def test_allowlist_manager():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        mgr = AllowlistManager(storage_path=tmp.name)
        assert not mgr.is_allowed("git status")

        # Add command
        assert mgr.add("git status")
        assert mgr.is_allowed("git status")

        # Usage tracking
        mgr.record_usage("git status")
        entries = mgr.get_all()
        assert len(entries) == 1
        assert entries[0]["run_count"] == 1

        # Level 3 command must be rejected
        with pytest.raises(ValueError):
            mgr.add("rm -rf /")

        # Revoke command
        assert mgr.revoke("git status")
        assert not mgr.is_allowed("git status")


def test_gatekeeper_allowlist_bypass():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow, \
         tempfile.NamedTemporaryFile(suffix=".log") as tmp_log:
        
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        audit = AuditLogger(log_path=tmp_log.name)
        kill_switch = KillSwitch()
        gatekeeper = Gatekeeper(allowlist=allowlist, audit_logger=audit, kill_switch=kill_switch)

        # Level 0 is auto-allowed
        allowed, msg = gatekeeper.evaluate("screenshot", {})
        assert allowed

        # Level 2 not in allowlist -> mock rejection
        gatekeeper.custom_prompt_callback = lambda act, det, risk, reason: (False, False)
        allowed, _ = gatekeeper.evaluate("shell", {"command": "curl http://example.com"})
        assert not allowed

        # Add to allowlist -> should now be allowed without prompt
        allowlist.add("curl http://example.com")
        allowed, _ = gatekeeper.evaluate("shell", {"command": "curl http://example.com"})
        assert allowed

        # Kill switch active -> aborts even allowlisted commands
        kill_switch.trigger()
        allowed, msg = gatekeeper.evaluate("shell", {"command": "curl http://example.com"})
        assert not allowed
        assert "kill switch is active" in msg.lower()
