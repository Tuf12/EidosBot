"""Tests for EidosBot PyQt6 UI components."""

import sys
import tempfile
import pytest
from PyQt6.QtWidgets import QApplication

from eidos.safety.allowlist import AllowlistManager
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskLevel
from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.app import UIController
from eidos.ui.dialogs import ConfirmationDialog
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill

# Ensure single QApplication per test session
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_confirmation_dialog_level_2(qapp):
    details = {"command": "git pull", "cwd": "/tmp"}
    dialog = ConfirmationDialog(
        action_type="shell",
        details=details,
        risk_level=RiskLevel.LEVEL_2_SHELL,
        reason="Git network fetch"
    )
    assert dialog.checkbox is not None
    assert dialog.checkbox.text().startswith("Remember and allow")
    dialog.checkbox.setChecked(True)
    dialog._on_approve()
    assert dialog.remember_checked is True
    dialog.close()


def test_confirmation_dialog_level_3(qapp):
    details = {"command": "rm -rf /tmp/test", "cwd": "/tmp"}
    dialog = ConfirmationDialog(
        action_type="shell",
        details=details,
        risk_level=RiskLevel.LEVEL_3_DESTRUCTIVE,
        reason="Destructive deletion"
    )
    # Checkbox must not exist for Level 3
    assert dialog.checkbox is None
    dialog._on_approve()
    assert dialog.remember_checked is False
    dialog.close()


def test_allowlist_manager_dialog(qapp):
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        allowlist = AllowlistManager(storage_path=tmp.name)
        allowlist.add("git status", description="Status check")
        allowlist.add("npm run test", description="Unit test")

        dialog = AllowlistManagerDialog(allowlist=allowlist)
        assert dialog.table.rowCount() == 2

        # Search filter test
        dialog._filter_table("git")
        assert not dialog.table.isRowHidden(0)
        assert dialog.table.isRowHidden(1)

        # Clear filter
        dialog._filter_table("")
        assert not dialog.table.isRowHidden(1)

        dialog.close()


def test_hud_overlay_visuals(qapp):
    overlay = HUDOverlay()
    overlay.show_click_target(100, 200, "#ff5555", 100)
    assert overlay._click_target is not None
    assert overlay._click_target[0] == 100
    assert overlay._click_target[1] == 200

    overlay.show_bounding_box(10, 20, 100, 50, "Button 1", "#50fa7b", 100)
    assert len(overlay._bounding_boxes) == 1

    overlay.show_alert("kill", 100)
    assert overlay._alert_border is not None

    overlay.clear_all()
    assert overlay._click_target is None
    assert len(overlay._bounding_boxes) == 0
    assert overlay._alert_border is None
    overlay.close()


def test_status_pill_states(qapp):
    pill = StatusPill()
    assert pill._current_state == "idle"

    pill.set_status("thinking", "Thinking about next action")
    assert pill._current_state == "thinking"
    assert "Thinking" in pill.status_label.text()

    pill.set_status("killed", "Emergency stop")
    assert pill._current_state == "killed"
    assert "Reset" in pill.btn_kill.text()

    pill.close()


def test_ui_controller_initialization(qapp):
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        allowlist = AllowlistManager(storage_path=tmp.name)
        kill_switch = KillSwitch()
        controller = UIController(allowlist=allowlist, kill_switch=kill_switch)

        assert controller.hud is not None
        assert controller.pill is not None

        # Test emergency kill callback propagation
        kill_switch.trigger()
        assert controller.pill._current_state == "killed"

        controller.hud.close()
        controller.pill.close()
