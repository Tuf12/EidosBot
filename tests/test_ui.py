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
from eidos.ui.settings_dialog import SettingsDialog

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


def test_settings_dialog_load_and_save(qapp):
    with tempfile.NamedTemporaryFile(suffix=".yaml") as tmp_cfg:
        initial_cfg = {
            "models": {
                "provider_preference": "auto",
                "local": {
                    "base_url": "http://localhost:11434",
                    "reasoning_model": "test-reasoning",
                    "vision_model": "test-vision"
                },
                "cloud": {
                    "api_key": "initial_key",
                    "base_url": "https://llm.chutes.ai/v1",
                    "vision_model": "Qwen/Qwen3.5-397B-A17B-TEE",
                    "reasoning_model": "moonshotai/Kimi-K2.6-TEE"
                }
            },
            "safety": {
                "confirm_level_1_ui": False,
                "confirm_level_2_shell": True
            },
            "ui": {
                "hud_overlay_enabled": True,
                "target_reticle_duration_ms": 300,
                "status_pill": {"position": "top-right"}
            }
        }

        dialog = SettingsDialog(config=initial_cfg, config_path=tmp_cfg.name)

        # Check loaded values
        assert dialog.combo_preference.currentIndex() == 1  # auto
        assert dialog.edit_chutes_key.text() == "initial_key"
        assert dialog.combo_local_reasoning.currentText() == "test-reasoning"

        # Toggle password visibility
        assert dialog.edit_chutes_key.echoMode() == dialog.edit_chutes_key.EchoMode.Password
        dialog._toggle_key_visibility()
        assert dialog.edit_chutes_key.echoMode() == dialog.edit_chutes_key.EchoMode.Normal

        # Modify values
        dialog.edit_chutes_key.setText("new_secret_key")
        dialog.combo_preference.setCurrentIndex(2)  # cloud
        dialog.chk_confirm_ui.setChecked(True)

        extracted = dialog.extract_config()
        assert extracted["models"]["provider_preference"] == "cloud"
        assert extracted["models"]["cloud"]["api_key"] == "new_secret_key"
        assert extracted["safety"]["confirm_level_1_ui"] is True

        # Save and verify signal
        saved_holder = []
        dialog.signal_settings_applied.connect(lambda c: saved_holder.append(c))
        dialog._on_save_clicked()
        assert len(saved_holder) == 1
        assert saved_holder[0]["models"]["cloud"]["api_key"] == "new_secret_key"

        dialog.close()


def test_status_pill_menu_and_settings_buttons(qapp):
    pill = StatusPill()
    assert pill.btn_menu is not None
    assert pill.btn_settings is not None
    assert pill.btn_menu.text() == "☰"
    assert pill.btn_settings.text() == "⚙️"

    # Test signal emitted on settings click
    signals_received = []
    pill.signal_open_settings.connect(lambda: signals_received.append("settings"))
    pill.btn_settings.click()
    assert "settings" in signals_received

    pill.close()

