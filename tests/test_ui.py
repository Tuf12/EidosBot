"""Tests for EidosBot PyQt6 UI components."""

import os
import sys
import tempfile
import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from eidos.safety.allowlist import AllowlistManager
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskLevel
from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.app import UIController
from eidos.ui.chat import (
    ChatHistoryWidget,
    MessageBubble,
    markdown_to_chat_html,
    markdown_to_plain_text,
    open_chat_link,
    _resolve_local_file_path,
)
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
            },
            "voice": {
                "enabled": True,
                "auto_speak_responses": False,
                "tts_voice": "af_bella",
                "tts_speed": 1.2,
            },
        }

        dialog = SettingsDialog(config=initial_cfg, config_path=tmp_cfg.name)

        # Check loaded values
        assert dialog.combo_preference.currentIndex() == 1  # auto
        assert dialog.edit_chutes_key.text() == "initial_key"
        assert dialog.combo_local_reasoning.currentText() == "test-reasoning"
        assert dialog.chk_auto_speak.isChecked() is False
        assert dialog.chk_auto_submit.isChecked() is True
        assert dialog.combo_tts_voice.currentText() == "af_bella"

        # Toggle password visibility
        assert dialog.edit_chutes_key.echoMode() == dialog.edit_chutes_key.EchoMode.Password
        dialog._toggle_key_visibility()
        assert dialog.edit_chutes_key.echoMode() == dialog.edit_chutes_key.EchoMode.Normal

        # Modify values
        dialog.edit_chutes_key.setText("new_secret_key")
        dialog.combo_preference.setCurrentIndex(2)  # cloud
        dialog.chk_confirm_ui.setChecked(True)
        dialog.chk_auto_speak.setChecked(True)
        dialog.chk_auto_submit.setChecked(False)

        extracted = dialog.extract_config()
        assert extracted["models"]["provider_preference"] == "cloud"
        assert extracted["models"]["cloud"]["api_key"] == "new_secret_key"
        assert extracted["safety"]["confirm_level_1_ui"] is True
        assert extracted["voice"]["auto_speak_responses"] is True
        assert extracted["voice"]["auto_submit_transcription"] is False

        # Save and verify signal
        saved_holder = []
        dialog.signal_settings_applied.connect(lambda c: saved_holder.append(c))
        dialog._on_save_clicked()
        assert len(saved_holder) == 1
        assert saved_holder[0]["models"]["cloud"]["api_key"] == "new_secret_key"

        dialog.close()


def test_settings_dialog_telegram_tab(qapp):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as tmp_cfg:
        initial_cfg = {
            "gateway": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ",
                    "allowed_user_ids": [987654321, 11223344],
                    "remote_kill_switch_enabled": True,
                    "notifications": {
                        "on_risk_approval": True,
                        "on_job_milestones": False,
                        "on_task_completed": True,
                    },
                }
            }
        }
        dialog = SettingsDialog(config=initial_cfg, config_path=tmp_cfg.name)

        # Tab existence
        assert hasattr(dialog, "tab_telegram")
        assert dialog.chk_telegram_enabled.isChecked() is True
        assert dialog.edit_telegram_token.text() == "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
        assert "987654321" in dialog.edit_telegram_user_ids.text()
        assert "11223344" in dialog.edit_telegram_user_ids.text()
        assert dialog.chk_tg_remote_kill.isChecked() is True
        assert dialog.chk_tg_risk_approval.isChecked() is True
        assert dialog.chk_tg_job_milestones.isChecked() is False
        assert dialog.chk_tg_task_completed.isChecked() is True

        # Toggle password visibility
        assert dialog.edit_telegram_token.echoMode() == dialog.edit_telegram_token.EchoMode.Password
        dialog._toggle_tg_token_visibility()
        assert dialog.edit_telegram_token.echoMode() == dialog.edit_telegram_token.EchoMode.Normal
        dialog._toggle_tg_token_visibility()
        assert dialog.edit_telegram_token.echoMode() == dialog.edit_telegram_token.EchoMode.Password

        # Modify values
        dialog.edit_telegram_token.setText("987654321:NEWTOKENXYZ")
        dialog.edit_telegram_user_ids.setText("55555, 66666")
        dialog.chk_tg_job_milestones.setChecked(True)

        extracted = dialog.extract_config()
        tg_out = extracted["gateway"]["telegram"]
        assert tg_out["bot_token"] == "987654321:NEWTOKENXYZ"
        assert tg_out["allowed_user_ids"] == [55555, 66666]
        assert tg_out["notifications"]["on_job_milestones"] is True

        dialog.close()


def test_ui_controller_telegram_dynamic_toggle(qapp):
    controller = UIController()
    assert controller._telegram_gw is None

    from unittest.mock import MagicMock
    mock_gw = MagicMock()
    mock_gw.is_running.return_value = True
    controller._telegram_gw = mock_gw

    # Applying settings with telegram disabled should stop the gateway
    disabled_cfg = {
        "gateway": {
            "telegram": {
                "enabled": False,
                "bot_token": "",
            }
        }
    }
    controller.on_settings_applied(disabled_cfg)
    mock_gw.stop.assert_called_once()

    controller.hud.close()
    controller.pill.close()


def test_status_pill_menu_and_close_buttons(qapp):
    pill = StatusPill()
    assert pill.btn_menu is not None
    assert pill.btn_close is not None
    assert pill.btn_menu.text() == "☰"
    assert pill.btn_close.text() == "✕"

    # Test signal emitted on close button click (quick exit)
    signals_received = []
    pill.signal_quit_app.connect(lambda: signals_received.append("quit"))
    pill.btn_close.click()
    assert "quit" in signals_received

    # Verify menu still has settings and allowlist signals
    assert hasattr(pill, "signal_open_settings")
    assert hasattr(pill, "signal_open_allowlist")

    pill.close()


def test_quit_on_last_window_closed_is_disabled(qapp):
    controller = UIController()
    assert qapp.quitOnLastWindowClosed() is False

    controller.show()
    assert controller.pill.isVisible()

    # Open and close settings dialog
    controller.open_settings_dialog()
    assert controller.settings_dialog is not None
    assert controller.settings_dialog.isVisible()

    # Close settings dialog
    controller.settings_dialog.close()
    assert not controller.settings_dialog.isVisible()

    # Verify status pill is still alive and visible
    assert controller.pill.isVisible()

    controller.hide()
    controller.hud.close()
    controller.pill.close()


def test_markdown_to_chat_html():
    raw_md = (
        "### Header 3\n"
        "Here is **bold** and *italic* and `inline_code`.\n"
        "```python\ndef greet():\n    return 'hello'\n```\n"
        "- Item A\n"
        "- Item B"
    )
    result = markdown_to_chat_html(raw_md)
    assert "<h4" in result
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert "<code" in result
    assert "inline_code" in result
    assert "<pre" in result
    assert "PYTHON" in result
    assert "def greet():" in result
    assert "<ul" in result
    assert "<li>Item A</li>" in result


def test_markdown_deep_headings_and_plain_copy():
    html_out = markdown_to_chat_html(
        "#### Section\n##### Smaller\n###### Smallest\n### Still three"
    )
    assert "<h5" in html_out
    assert "<h6" in html_out
    assert "<h4" in html_out
    assert "####" not in html_out
    assert ">Section<" in html_out

    plain = markdown_to_plain_text(
        "#### Shopping list\n"
        "Here is **bold** and *italic* and `code`.\n"
        "See [docs](https://example.com/docs).\n"
        "- Milk\n"
        "1. Bread\n"
        "```python\n# comment\nprint('hi')\n```\n"
    )
    assert "####" not in plain
    assert "**" not in plain
    assert "`" not in plain
    assert "```" not in plain
    assert "Shopping list" in plain
    assert "Here is bold and italic and code." in plain
    assert "See docs (https://example.com/docs)." in plain
    assert "• Milk" in plain
    assert "1. Bread" in plain
    assert "# comment" in plain
    assert "print('hi')" in plain


def test_message_bubbles_and_copy(qapp):
    # User message
    user_bubble = MessageBubble(role="user", content="Show active windows")
    assert user_bubble.role == "user"
    assert "Show active windows" in user_bubble.content_label.text()

    # Assistant message with copy button
    bot_bubble = MessageBubble(role="assistant", content="Here is the output: `active`")
    assert bot_bubble.role == "assistant"
    # Find copy button
    copy_btn = bot_bubble.findChild(QPushButton)
    assert copy_btn is not None
    assert "Copy" in copy_btn.text()
    copy_btn.click()
    clipboard = QApplication.clipboard()
    assert clipboard.text() == "Here is the output: active"

    # Thought message
    thought_bubble = MessageBubble(role="thought", content="Analyzing active desktop state...")
    assert thought_bubble.role == "thought"

    # Tool message
    tool_bubble = MessageBubble(role="tool", content="Output: PID 42", title="execute_shell")
    assert tool_bubble.role == "tool"
    assert "execute_shell" in tool_bubble.findChild(QLabel).text()

    # Error message
    err_bubble = MessageBubble(role="error", content="Failed to connect to gateway")
    assert err_bubble.role == "error"

    user_bubble.close()
    bot_bubble.close()
    thought_bubble.close()
    tool_bubble.close()
    err_bubble.close()


def test_chat_history_widget(qapp):
    chat = ChatHistoryWidget()
    chat.show()
    assert chat.message_count == 0
    assert chat.welcome_widget.isVisible()

    chat.add_message("user", "Hello EidosBot")
    assert chat.message_count == 1
    assert not chat.welcome_widget.isVisible()

    chat.add_message("assistant", "Hello! How can I help you today?")
    assert chat.message_count == 2

    chat.clear_messages()
    assert chat.message_count == 0
    assert not chat.welcome_widget.isHidden()

    chat.close()


def test_status_pill_expand_and_collapse(qapp):
    pill = StatusPill()
    pill.show()
    # Initially collapsed
    assert pill.is_expanded is False
    assert not pill.chat_container.isVisible()
    assert pill.btn_expand.text() == "💬"
    assert pill.details_label.isVisible()
    assert not pill.btn_collapse.isVisible()

    # Expand
    pill.expand_chat()
    assert pill.is_expanded is True
    assert pill.chat_container.isVisible()
    assert pill.btn_expand.text() == "💬"
    assert not pill.details_label.isVisible()
    assert pill.btn_collapse.isVisible()
    assert pill.btn_collapse.text() == "−"

    # Collapse via btn_collapse (standard window minimize control)
    pill.btn_collapse.click()
    assert pill.is_expanded is False
    assert not pill.chat_container.isVisible()
    assert pill.btn_expand.text() == "💬"
    assert pill.details_label.isVisible()
    assert not pill.btn_collapse.isVisible()

    # Expand and collapse via Escape shortcut
    pill.expand_chat()
    assert pill.is_expanded is True
    pill.shortcut_esc.activated.emit()
    assert pill.is_expanded is False
    assert not pill.chat_container.isVisible()

    # Toggle
    pill.toggle_chat()
    assert pill.is_expanded is True

    pill.toggle_chat()
    assert pill.is_expanded is False

    pill.close()


def test_status_pill_minimize_restores_minimal_size(qapp):
    import time
    pill = StatusPill()
    pill.show()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)

    initial_size = pill.size()
    initial_geom = pill.geometry()
    assert pill.is_expanded is False

    # Expand
    pill.expand_chat()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)
    assert pill.is_expanded is True
    assert pill.height() >= 400
    assert pill.width() >= 500

    # Collapse / Minimize
    pill.collapse_chat()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)
    assert pill.is_expanded is False
    # Verify it returns back to its minimal size and does not stay inflated
    assert pill.height() == pill.minimumSizeHint().height()
    assert pill.width() == pill.minimumSizeHint().width()
    assert pill.height() <= 140  # Compact height, including the Auto-Read row
    assert pill.geometry().topLeft() == initial_geom.topLeft()
    assert pill.geometry().width() == initial_geom.width()

    # Even after setting a long status message, collapsing returns to minimal size
    pill.expand_chat()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)
    pill.set_status("executing", "Running a very long task description that might stretch the label")
    pill.collapse_chat()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)
    assert pill.height() == pill.minimumSizeHint().height()
    assert pill.width() == pill.minimumSizeHint().width()
    assert pill.height() <= 140

    pill.close()


def test_status_pill_frameless_resizing_and_dragging(qapp):
    import time
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QMouseEvent

    pill = StatusPill()
    pill.show()
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)

    pill.expand_chat()
    pill.move(500, 200)
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)

    assert pill.is_expanded is True
    w, h = pill.width(), pill.height()
    origin = pill.mapToGlobal(QPoint(0, 0))

    # Test edge hit testing
    assert pill._get_resize_edge(origin + QPoint(3, 100)) == "left"
    assert pill._get_resize_edge(origin + QPoint(w - 3, 100)) == "right"
    assert pill._get_resize_edge(origin + QPoint(100, h - 3)) == "bottom"
    assert pill._get_resize_edge(origin + QPoint(w - 6, h - 6)) == "bottom_right"
    assert pill._get_resize_edge(origin + QPoint(6, h - 6)) == "bottom_left"
    assert pill._get_resize_edge(origin + QPoint(w // 2, h // 2)) is None

    # Test draggable area: grab handle is draggable, buttons/inputs are not
    grab_pt = pill.grab_icon.mapToGlobal(QPoint(5, 5))
    assert pill._is_draggable_area(grab_pt) is True
    assert pill._is_draggable_area(pill.btn_close.mapToGlobal(QPoint(5, 5))) is False
    assert pill._is_draggable_area(pill.prompt_input.mapToGlobal(QPoint(10, 10))) is False

    # Simulate bottom-right resize
    start_geom = pill.geometry()
    press_pt = origin + QPoint(w - 4, h - 4)
    ev_press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        pill.mapFromGlobal(press_pt).toPointF(),
        press_pt.toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill.mousePressEvent(ev_press)
    assert pill._resizing is True
    assert pill._resize_edge == "bottom_right"

    # Drag 40px right and 30px down
    move_pt = press_pt + QPoint(40, 30)
    ev_move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        pill.mapFromGlobal(move_pt).toPointF(),
        move_pt.toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill.mouseMoveEvent(ev_move)
    for _ in range(5):
        qapp.processEvents()

    assert pill.width() >= start_geom.width() + 30
    assert pill.height() >= start_geom.height() + 20

    # Release mouse
    ev_release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        pill.mapFromGlobal(move_pt).toPointF(),
        move_pt.toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill.mouseReleaseEvent(ev_release)
    assert pill._resizing is False

    pill.close()


def test_status_pill_chat_messages_and_auto_expand(qapp):
    pill = StatusPill()
    assert pill.is_expanded is False

    # Adding user message should auto-expand chat
    pill.add_user_message("What time is it?")
    assert pill.is_expanded is True
    assert pill.chat_history.message_count == 1

    # Collapse to test unread badge on assistant message
    pill.collapse_chat()
    assert pill.is_expanded is False

    pill.add_assistant_message("It is 12:00 PM.")
    assert pill.chat_history.message_count == 2
    assert "💬 (1)" in pill.btn_expand.text()

    # Expand resets unread
    pill.expand_chat()
    assert pill.btn_expand.text() == "💬"

    # Test thoughts and tool messages
    pill.add_thought_message("Thinking about solution")
    assert pill.chat_history.message_count == 3

    pill.add_tool_start_message("get_active_window", {})
    assert pill.chat_history.message_count == 4

    pill.add_tool_end_message("get_active_window", {"title": "Terminal"})
    assert pill.chat_history.message_count == 5

    pill.add_error_message("Sample error")
    assert pill.chat_history.message_count == 6

    # Test clear chat signal
    clear_signals = []
    pill.signal_clear_chat.connect(lambda: clear_signals.append(True))
    pill.clear_chat()
    assert len(clear_signals) == 1
    assert pill.chat_history.message_count == 0

    pill.close()


def test_ui_controller_chat_signals(qapp):
    controller = UIController()
    pill = controller.pill

    # Emit chat user message
    controller.signal_chat_user_message.emit("User via signal")
    assert pill.chat_history.message_count == 1

    # Emit assistant response
    controller.signal_chat_assistant_message.emit("Bot response via signal")
    assert pill.chat_history.message_count == 2

    # Clear chat
    pill.clear_chat()
    assert pill.chat_history.message_count == 0

    controller.hide()
    controller.hud.close()
    controller.pill.close()


def test_start_task_rejects_when_busy(qapp, monkeypatch):
    controller = UIController()
    started = {"count": 0}

    class FakeAgentLoop:
        def run_task(self, task_prompt, max_steps=15):
            started["count"] += 1
            import time
            time.sleep(2)
            return {"success": True, "summary": "done", "steps_taken": 1}

    controller._agent_loop = FakeAgentLoop()

    controller.start_task_in_background("first task")
    assert controller.is_task_running()
    assert controller.pill.chat_history.message_count == 1

    controller.pill.set_prompt_text("second task")
    controller.pill._on_submit_prompt()
    assert controller.pill.prompt_input.toPlainText() == "second task"
    assert controller.pill.chat_history.message_count == 2

    if controller._agent_thread:
        controller._agent_thread.join(timeout=5)

    assert started["count"] == 1
    controller.hide()
    controller.hud.close()
    controller.pill.close()


def test_conversations_dialog(qapp):
    from eidos.memory.conversation_store import ConversationStore
    from eidos.ui.conversations_dialog import ConversationsDialog

    store = ConversationStore(db_path=":memory:")
    c1 = store.create_conversation(title="Docker Setup")
    store.add_message(c1, "user", "How to install docker?")
    c2 = store.create_conversation(title="FastAPI Guide")
    store.add_message(c2, "user", "How to create fastapi endpoint?")

    dialog = ConversationsDialog(conversation_store=store, active_conversation_id=c1)
    assert dialog.table.rowCount() == 2

    # Verify active conversation marker
    assert "Active" in dialog.table.item(1, 0).text() or "Active" in dialog.table.item(0, 0).text()

    # Search filter
    dialog.search_input.setText("FastAPI")
    assert dialog.table.rowCount() == 1

    dialog.search_input.setText("")
    assert dialog.table.rowCount() == 2

    # Selection signal
    selected = []
    dialog.signal_conversation_selected.connect(lambda cid: selected.append(cid))
    dialog._on_select_conversation(c2)
    assert len(selected) == 1
    assert selected[0] == c2

    # Test delete confirmation dialog: "No" cancels deletion
    from unittest.mock import patch
    from PyQt6.QtWidgets import QMessageBox

    deleted = []
    dialog.signal_conversation_deleted.connect(lambda cid: deleted.append(cid))

    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.No):
        dialog._on_delete_conversation(c2, "FastAPI Guide")
        assert len(deleted) == 0
        assert store.get_conversation(c2) is not None
        assert dialog.table.rowCount() == 2

    # Test delete confirmation dialog: "Yes" confirms deletion
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
        dialog._on_delete_conversation(c2, "FastAPI Guide")
        assert len(deleted) == 1
        assert deleted[0] == c2
        assert store.get_conversation(c2) is None
        assert dialog.table.rowCount() == 1

    dialog.close()
    store.close()


def test_status_pill_conversation_header_and_loading(qapp):
    pill = StatusPill()
    pill.show()

    # Verify conversation subheader widgets exist
    assert hasattr(pill, "btn_new_chat")
    assert hasattr(pill, "btn_past_chats")
    assert hasattr(pill, "lbl_conv_title")
    assert pill.lbl_conv_title.text() == "New Chat"

    # Test title update
    pill.set_conversation_title("Python Async Development")
    assert "Python Async Development" in pill.lbl_conv_title.text()

    # Test loading messages into pill
    sample_msgs = [
        {"role": "USER", "content": "Hello there"},
        {"role": "ASSISTANT", "content": "General Kenobi!"},
    ]
    pill.load_conversation_messages("conv_123", "Star Wars Chat", sample_msgs)
    assert pill.chat_history.message_count == 2
    assert "Star Wars Chat" in pill.lbl_conv_title.text()

    failed_turn = {
        "role": "ASSISTANT",
        "content": (
            "Reached the step limit (50) before the task was finished.\n\n"
            "Reasoning:\n1. Look at the portfolio note.\n\n"
            "Actions already taken:\n1. execute_shell {} → ok"
        ),
        "metadata": {
            "headline": "Reached the step limit (50) before the task was finished.",
            "outcome": "max_steps",
            "thoughts": ["Look at the portfolio note."],
            "actions": [{"tool": "execute_shell", "arguments": "{}", "result": "ok"}],
        },
    }
    pill.load_conversation_messages("conv_fail", "Failed Turn", [failed_turn])
    roles = [bubble.role for bubble in pill.chat_history.bubbles]
    assert roles == ["thought", "tool", "error"]
    assert "portfolio note" in pill.chat_history.bubbles[0].raw_content
    assert pill.chat_history.bubbles[1].title == "execute_shell"
    assert "step limit" in pill.chat_history.bubbles[2].raw_content
    assert "Actions already taken" not in pill.chat_history.bubbles[2].raw_content

    pill.close()


def test_ui_controller_persistent_conversation_flow(qapp):
    from eidos.memory.conversation_store import ConversationStore

    store = ConversationStore(db_path=":memory:")
    c1 = store.create_conversation(title="Initial Conversation")
    store.add_message(c1, "user", "Initial question")
    store.add_message(c1, "assistant", "Initial answer")

    class MockLoop:
        def __init__(self, conv_store, active_id):
            self.conversation_store = conv_store
            self.active_conversation_id = active_id
            self.conversation_history = []
            self.gatekeeper = type("GK", (), {"custom_prompt_callback": None})()
            self.inputs = type("INP", (), {"click": lambda **kwargs: None})()
            self.voice = None
            self._callbacks = {}

        def register_callback(self, name, cb):
            self._callbacks[name] = cb

        def start_new_conversation(self, title="New Chat"):
            new_id = self.conversation_store.create_conversation(title=title)
            self.active_conversation_id = new_id
            self.conversation_history.clear()
            return new_id

        def switch_conversation(self, conv_id):
            self.active_conversation_id = conv_id
            return True

    mock_loop = MockLoop(store, c1)
    controller = UIController()
    controller.set_agent_loop(mock_loop)

    # Verify active conversation messages were restored on set_agent_loop
    assert controller.pill.chat_history.message_count == 2
    assert "Initial Conversation" in controller.pill.lbl_conv_title.text()

    # Test starting new conversation
    controller.start_new_conversation()
    assert controller.pill.chat_history.message_count == 0
    assert controller.pill.lbl_conv_title.text() == "New Chat"

    # Test switching back to initial conversation
    controller.switch_conversation(c1)
    assert controller.pill.chat_history.message_count == 2
    assert "Initial Conversation" in controller.pill.lbl_conv_title.text()

    controller.hide()
    controller.hud.close()
    controller.pill.close()
    store.close()


def test_markdown_to_chat_html_links():
    # Markdown links
    text1 = "Here is the [Patent Link](https://patents.google.com/patent/US20140216326) for you."
    html1 = markdown_to_chat_html(text1)
    assert "<a href='https://patents.google.com/patent/US20140216326'" in html1
    assert ">Patent Link</a>" in html1

    # Raw URLs
    text2 = "Check out https://finance.yahoo.com/quote/SNDL/ for stock info."
    html2 = markdown_to_chat_html(text2)
    assert "<a href='https://finance.yahoo.com/quote/SNDL/'" in html2
    assert ">https://finance.yahoo.com/quote/SNDL/</a>" in html2

    # Relative markdown portfolio links
    text3 = "Analysis ready: [Portfolio Item 114](portfolios/item_114.md)"
    html3 = markdown_to_chat_html(text3)
    assert "<a href='portfolios/item_114.md'" in html3
    assert ">Portfolio Item 114</a>" in html3

    # Absolute file path in markdown link
    text4 = "View [Ranking Summary](/home/tuf/EidosWorkspace/documents/RANKING_SUMMARY.md)"
    html4 = markdown_to_chat_html(text4)
    assert "<a href='/home/tuf/EidosWorkspace/documents/RANKING_SUMMARY.md'" in html4
    assert ">Ranking Summary</a>" in html4

    # Autolink raw portfolio path
    text5 = "Generated file portfolios/item_275.md for review."
    html5 = markdown_to_chat_html(text5)
    assert "<a href='portfolios/item_275.md'" in html5

    # Markdown link where label is the URL (must not create nested <a> tags)
    text6 = "Visit [https://example.com](https://example.com) now."
    html6 = markdown_to_chat_html(text6)
    assert "<a href='https://example.com'" in html6
    assert "<a href='https://example.com'><a href" not in html6


def test_message_bubble_link_activation(qapp, monkeypatch):
    """Test that clicking on links in MessageBubble emits signal_link_clicked and invokes open_chat_link."""
    opened_links = []
    monkeypatch.setattr("eidos.ui.chat.open_chat_link", lambda url, workspace_dir=None: opened_links.append(url) or True)

    bubble = MessageBubble(
        role="assistant",
        content="Review [Portfolio](portfolios/item_114.md) or https://patents.google.com",
    )
    assert not bubble.content_label.openExternalLinks()

    signals_received = []
    bubble.signal_link_clicked.connect(signals_received.append)

    # Simulate link activation signal from QLabel
    bubble.content_label.linkActivated.emit("portfolios/item_114.md")
    assert "portfolios/item_114.md" in signals_received
    assert "portfolios/item_114.md" in opened_links

    bubble.content_label.linkActivated.emit("https://patents.google.com")
    assert "https://patents.google.com" in signals_received
    assert "https://patents.google.com" in opened_links

    bubble.close()


def test_chat_history_widget_link_clicked_forwarding(qapp, monkeypatch):
    """Verify ChatHistoryWidget forwards signal_link_clicked from nested MessageBubbles."""
    monkeypatch.setattr("eidos.ui.chat.open_chat_link", lambda url, workspace_dir=None: True)

    chat = ChatHistoryWidget()
    received = []
    chat.signal_link_clicked.connect(received.append)

    bubble = chat.add_message(role="assistant", content="See [Link](https://eidos.ai)")
    bubble.content_label.linkActivated.emit("https://eidos.ai")

    assert "https://eidos.ai" in received
    chat.close()


def test_resolve_local_file_path(tmp_path):
    """Verify local file resolution against workspace and nested directories."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    portfolios_dir = ws / "documents" / "analysis_phase_1" / "portfolios"
    portfolios_dir.mkdir(parents=True)
    target_file = portfolios_dir / "item_114.md"
    target_file.write_text("# Item 114")

    # Relative path matching
    resolved = _resolve_local_file_path("portfolios/item_114.md", workspace_dir=str(ws))
    assert resolved == str(target_file.resolve())

    # Direct filename matching
    resolved_name = _resolve_local_file_path("item_114.md", workspace_dir=str(ws))
    assert resolved_name == str(target_file.resolve())

    # Absolute path directly on disk
    resolved_abs = _resolve_local_file_path(str(target_file.resolve()), workspace_dir=str(ws))
    assert resolved_abs == str(target_file.resolve())


def test_message_bubble_tool_links(qapp):
    bubble = MessageBubble(
        role="tool",
        content="Opened https://patents.google.com/patent/US20140216326 in browser.",
        title="browser_open",
    )
    assert "<a href='https://patents.google.com/patent/US20140216326'" in bubble.content_label.text()
    bubble.close()


def test_tool_json_path_link_stops_before_escaped_newline(qapp):
    """JSON tool dumps encode newlines as \\n. That tail must not become part of the href."""
    import json

    command = (
        "cat << 'EOF' > /home/tuf/EidosWorkspace/documents/analysis_phase_1/portfolios/item_166.md\n"
        "# Invention Analysis\n"
    )
    content = "Calling:\n" + json.dumps({"command": command}, indent=2)
    bubble = MessageBubble(role="tool", content=content, title="execute_shell")
    html_text = bubble.content_label.text()
    assert "<a href='/home/tuf/EidosWorkspace/documents/analysis_phase_1/portfolios/item_166.md'" in html_text
    assert "item_166.md\\n#" not in html_text
    assert "href='/home/tuf/EidosWorkspace/documents/analysis_phase_1/portfolios/item_166.md\\n" not in html_text
    bubble.close()


def test_chat_bubbles_fit_inside_history(qapp):
    """Long paths and tool output must not push bubbles past the chat viewport."""
    import json

    chat = ChatHistoryWidget()
    chat.resize(480, 460)
    chat.show()
    path = "/home/tuf/EidosWorkspace/" + ("documents/" * 12) + "item_166.md"
    command = "echo " + ("A" * 500) + " > " + path
    chat.add_message("assistant", f"Wrote `{path}` now.")
    chat.add_message("tool", "Calling:\n" + json.dumps({"command": command}), title="execute_shell")
    chat.add_message("user", "fit inside the chat")
    qapp.processEvents()

    viewport_width = chat.viewport().width()
    assert viewport_width > 0
    assert chat.container.width() <= viewport_width
    for bubble in chat.bubbles:
        assert bubble.geometry().right() <= viewport_width
        assert bubble.content_label.width() <= bubble.contentsRect().width()
        if bubble.btn_delete is not None:
            delete_right = bubble.btn_delete.mapTo(chat.viewport(), bubble.btn_delete.rect().topRight())
            assert delete_right.x() <= viewport_width
    chat.close()


def test_inline_code_path_is_clickable():
    text = "Overwritten `/home/tuf/EidosWorkspace/documents/analysis_phase_1/portfolios/item_166.md` on disk."
    html_out = markdown_to_chat_html(text)
    assert 'href="/home/tuf/EidosWorkspace/documents/analysis_phase_1/portfolios/item_166.md"' in html_out
    assert "<code" in html_out


def test_open_chat_link_strips_json_newline_tail(tmp_path, monkeypatch):
    target = tmp_path / "item_166.md"
    target.write_text("# item")
    opened = []
    monkeypatch.setattr("eidos.ui.chat._launch_local_file", lambda path: opened.append(path) or True)
    assert open_chat_link(f"{target}\\n#") is True
    assert opened == [str(target.resolve())]


def test_status_pill_workspace_button(qapp):
    pill = StatusPill()
    assert hasattr(pill, "btn_workspace")
    assert pill.btn_workspace is not None
    assert "Files" in pill.btn_workspace.text() or "Workspace" in pill.btn_workspace.text()

    received = []
    pill.signal_open_workspace.connect(lambda: received.append(True))
    pill.btn_workspace.click()
    assert len(received) == 1
    pill.close()


def test_message_bubble_copy_all_roles(qapp):
    copied = []

    # User bubble
    user_bubble = MessageBubble(role="user", content="User prompt to copy")
    user_bubble.signal_copy_clicked.connect(copied.append)
    assert user_bubble.btn_copy is not None
    user_bubble.btn_copy.click()
    assert copied[-1] == "User prompt to copy"

    # Assistant bubble
    asst_bubble = MessageBubble(role="assistant", content="Assistant reply to copy")
    asst_bubble.signal_copy_clicked.connect(copied.append)
    assert asst_bubble.btn_copy is not None
    asst_bubble.btn_copy.click()
    assert copied[-1] == "Assistant reply to copy"

    # Tool bubble
    tool_bubble = MessageBubble(role="tool", content="Tool result: status ok")
    tool_bubble.signal_copy_clicked.connect(copied.append)
    assert tool_bubble.btn_copy is not None
    tool_bubble.btn_copy.click()
    assert copied[-1] == "Tool result: status ok"

    # Thought bubble
    thought_bubble = MessageBubble(role="thought", content="Thought reasoning to copy")
    thought_bubble.signal_copy_clicked.connect(copied.append)
    assert thought_bubble.btn_copy is not None
    thought_bubble.btn_copy.click()
    assert copied[-1] == "Thought reasoning to copy"

    # Error bubble
    err_bubble = MessageBubble(role="error", content="Error output to copy")
    err_bubble.signal_copy_clicked.connect(copied.append)
    assert err_bubble.btn_copy is not None
    err_bubble.btn_copy.click()
    assert copied[-1] == "Error output to copy"

    user_bubble.close()
    asst_bubble.close()
    tool_bubble.close()
    thought_bubble.close()
    err_bubble.close()


def test_message_bubble_inline_editing(qapp):
    user_bubble = MessageBubble(role="user", content="Original prompt", message_id="msg_001")
    user_bubble.show()
    assert not user_bubble.is_editing
    assert not user_bubble.edit_container.isVisible()
    assert user_bubble.content_label.isVisible()

    # Click edit button
    assert user_bubble.btn_edit is not None
    user_bubble.btn_edit.click()
    assert user_bubble.is_editing
    assert user_bubble.edit_container.isVisible()
    assert not user_bubble.content_label.isVisible()
    assert user_bubble.edit_text_area.toPlainText() == "Original prompt"

    # Cancel editing
    user_bubble.edit_text_area.setPlainText("Changed but canceled")
    user_bubble.btn_cancel_edit.click()
    assert not user_bubble.is_editing
    assert not user_bubble.edit_container.isVisible()
    assert user_bubble.content_label.isVisible()
    assert user_bubble.raw_content == "Original prompt"

    # Edit and save
    received = []
    user_bubble.signal_edit_submitted.connect(lambda mid, txt: received.append((mid, txt)))
    user_bubble.start_editing()
    user_bubble.edit_text_area.setPlainText("Updated prompt text")
    user_bubble.btn_save_edit.click()

    assert not user_bubble.is_editing
    assert user_bubble.raw_content == "Updated prompt text"
    assert "Updated prompt text" in user_bubble.content_label.text()
    assert len(received) == 1
    assert received[0] == ("msg_001", "Updated prompt text")

    user_bubble.close()


def test_message_bubble_retry_user_and_assistant(qapp):
    chat = ChatHistoryWidget()
    chat.show()

    retried_prompts = []
    chat.signal_retry.connect(lambda p: retried_prompts.append(p))

    # Add user message and assistant reply
    u_bubble = chat.add_message("user", "What is the capital of France?")
    a_bubble = chat.add_message("assistant", "The capital of France is Paris.")

    # Retry user message directly
    assert u_bubble.btn_retry is not None
    u_bubble.btn_retry.click()
    assert len(retried_prompts) == 1
    assert retried_prompts[-1] == "What is the capital of France?"

    # Retry assistant message -> should resolve preceding user prompt
    assert a_bubble.btn_retry is not None
    a_bubble.btn_retry.click()
    assert len(retried_prompts) == 2
    assert retried_prompts[-1] == "What is the capital of France?"

    # Add error bubble and retry
    e_bubble = chat.add_message("error", "Temporary connection error")
    assert e_bubble.btn_retry is not None
    e_bubble.btn_retry.click()
    assert len(retried_prompts) == 3
    assert retried_prompts[-1] == "What is the capital of France?"

    chat.close()


def test_message_bubble_replay_tts(qapp):
    chat = ChatHistoryWidget()
    chat.show()

    spoken_texts = []
    chat.signal_replay_tts.connect(lambda t: spoken_texts.append(t))

    u_bubble = chat.add_message("user", "Hello EidosBot")
    a_bubble = chat.add_message("assistant", "Greetings, human!")
    t_bubble = chat.add_message("thought", "Checking what the user asked.")

    # User message should NOT have a TTS button
    assert u_bubble.btn_speak is None

    # Assistant message should have Replay button
    assert a_bubble.btn_speak is not None
    assert "Replay" in a_bubble.btn_speak.text()
    a_bubble.btn_speak.click()
    assert len(spoken_texts) == 1
    assert spoken_texts[-1] == "Greetings, human!"

    # Reasoning boxes speak their own text
    assert t_bubble.btn_speak is not None
    assert "Replay" in t_bubble.btn_speak.text()
    t_bubble.btn_speak.click()
    assert len(spoken_texts) == 2
    assert spoken_texts[-1] == "Checking what the user asked."

    chat.close()


def test_message_bubble_delete(qapp):
    chat = ChatHistoryWidget()
    chat.show()

    deleted_ids = []
    chat.signal_delete_message.connect(lambda mid: deleted_ids.append(mid))

    b1 = chat.add_message("user", "Message 1", message_id="id_1")
    b2 = chat.add_message("assistant", "Message 2", message_id="id_2")
    assert chat.message_count == 2

    # Delete first message
    assert b1.btn_delete is not None
    b1.btn_delete.click()
    assert chat.message_count == 1
    assert len(deleted_ids) == 1
    assert deleted_ids[-1] == "id_1"

    # Delete second message
    assert b2.btn_delete is not None
    b2.btn_delete.click()
    assert chat.message_count == 0
    assert len(deleted_ids) == 2
    assert deleted_ids[-1] == "id_2"
    assert not chat.welcome_widget.isHidden()

    chat.close()


def test_status_pill_task_running_and_stop_button(qapp):
    pill = StatusPill()
    pill.show()

    assert not pill._is_task_running
    assert pill.btn_send.text() == "▶"

    # Set task running
    pill.set_task_running(True)
    assert pill._is_task_running
    assert pill.btn_send.text() == "⏹"
    assert "Stop" in pill.btn_send.toolTip()

    # Clicking send/stop button while task running emits signal_stop_task
    stop_signals = []
    pill.signal_stop_task.connect(lambda: stop_signals.append(True))
    pill.btn_send.click()
    assert len(stop_signals) == 1

    # Pressing Escape while task running emits signal_stop_task instead of collapsing
    pill.expand_chat()
    assert pill.is_expanded
    pill.shortcut_esc.activated.emit()
    assert len(stop_signals) == 2
    assert pill.is_expanded  # Did not collapse because task was running!

    # Finish task
    pill.set_task_running(False)
    assert not pill._is_task_running
    assert pill.btn_send.text() == "▶"

    # Now Escape collapses chat normally
    pill.shortcut_esc.activated.emit()
    assert not pill.is_expanded

    pill.close()


def test_ui_controller_stop_active_task(qapp):
    controller = UIController()
    controller.show()

    # Fake running task
    controller.pill.set_task_running(True)
    assert controller.pill._is_task_running

    # Stop active task
    controller.stop_active_task()
    assert not controller.pill._is_task_running
    assert controller.pill._current_state == "idle"

    controller.hide()


def test_ui_controller_replay_tts_interrupts_and_plays(qapp):
    from unittest.mock import MagicMock

    controller = UIController()
    controller.show()

    mock_agent = MagicMock()
    mock_voice = MagicMock()
    mock_voice.tts.is_available = True
    mock_voice.is_speaking.return_value = False
    mock_agent.voice = mock_voice
    controller._agent_loop = mock_agent

    # 1. Idle -> should trigger speak_response
    controller.replay_tts("Hello from assistant")
    mock_voice.speak_response.assert_called_with("Hello from assistant")

    # 2. Even if already speaking -> should interrupt previous speech and replay new text immediately
    mock_voice.is_speaking.return_value = True
    mock_voice.speak_response.reset_mock()
    controller.replay_tts("Another assistant message")
    mock_voice.stop_speaking.assert_called()
    mock_voice.speak_response.assert_called_with("Another assistant message")

    controller.hide()


def test_conversation_store_mutations(tmp_path):
    from eidos.memory.conversation_store import ConversationStore

    store = ConversationStore(db_path=tmp_path / "test.db")
    conv_id = store.create_conversation("Mutation Test")

    m1 = store.add_message(conv_id, role="user", content="Prompt 1")
    store.add_message(conv_id, role="assistant", content="Reply 1")
    store.add_message(conv_id, role="user", content="Prompt 2")

    # Update m1
    success = store.update_message(m1, "Prompt 1 (edited)")
    assert success is True
    msgs = store.get_messages(conv_id)
    assert msgs[0]["content"] == "Prompt 1 (edited)"

    # Delete messages after m1
    deleted_count = store.delete_messages_after(conv_id, m1, inclusive=False)
    assert deleted_count == 2
    msgs = store.get_messages(conv_id)
    assert len(msgs) == 1
    assert msgs[0]["id"] == m1

    # Delete m1 directly
    assert store.delete_message(m1) is True
    assert len(store.get_messages(conv_id)) == 0

    store.close()


def test_tooltip_styling_and_palette(qapp):
    from PyQt6.QtGui import QPalette
    from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE, apply_global_styles

    apply_global_styles(qapp)

    # Check palette settings for tooltips
    palette = qapp.palette()
    assert palette.color(QPalette.ColorRole.ToolTipBase).name().lower() == "#21222c"
    assert palette.color(QPalette.ColorRole.ToolTipText).name().lower() == "#f8f8f2"

    # Check that QApplication stylesheet has QToolTip rule
    assert "QToolTip" in qapp.styleSheet()
    assert "background-color: #21222c" in qapp.styleSheet()
    assert "color: #f8f8f2" in qapp.styleSheet()

    # Check StatusPill contains QToolTip rule
    pill = StatusPill()
    assert "QToolTip" in pill.styleSheet() or "QToolTip" in pill.card.styleSheet()
    pill.close()

    # Check MessageBubble contains QToolTip rule
    bubble = MessageBubble(role="assistant", content="Test message")
    assert "QToolTip" in bubble.styleSheet()
    bubble.close()

    # Check ChatHistoryWidget contains QToolTip rule
    chat = ChatHistoryWidget()
    assert "QToolTip" in chat.styleSheet()
    chat.close()


def test_markdown_apostrophe_and_quotes_not_mangled():
    raw_text = "I've dimmed your screen brightness to 0.75 on the HDMI-0 output. &#x27;nice&#x27;"
    html_out = markdown_to_chat_html(raw_text)
    assert "&#x27;" not in html_out
    assert "I've dimmed your screen" in html_out
    assert "'nice'" in html_out

    code_md = "```python\nname = 'EidosBot'\n```"
    code_html = markdown_to_chat_html(code_md)
    assert "&#x27;" not in code_html
    assert "'EidosBot'" in code_html


def test_status_pill_auto_read_toggle(qapp):
    pill = StatusPill()
    pill.show()

    assert pill.btn_auto_speak.text() == "🔊 Auto-Read: ON"
    assert pill._auto_speak is True

    states = []
    pill.signal_toggle_auto_speak.connect(states.append)
    pill.btn_auto_speak.click()
    assert states == [False]
    assert pill.btn_auto_speak.text() == "🔇 Auto-Read: OFF"

    pill.btn_auto_speak.click()
    assert states == [False, True]
    assert pill.btn_auto_speak.text() == "🔊 Auto-Read: ON"

    pill.set_auto_speak(False)
    assert pill._auto_speak is False
    assert "OFF" in pill.btn_auto_speak.text()
    pill.close()


def test_ui_controller_auto_read_toggle_persists_and_stops_speech(qapp):
    from unittest.mock import MagicMock

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp_cfg:
        tmp_cfg.write(
            "voice:\n"
            "  enabled: true\n"
            "  auto_speak_responses: true  # Speak every chat reply aloud\n"
            "  tts_voice: bm_george\n"
        )
        cfg_path = tmp_cfg.name

    try:
        controller = UIController(
            config={"voice": {"enabled": True, "auto_speak_responses": True}},
            config_path=cfg_path,
        )
        controller.show()
        assert controller.pill.btn_auto_speak.text() == "🔊 Auto-Read: ON"

        mock_agent = MagicMock()
        mock_agent.config = controller.config
        controller._agent_loop = mock_agent

        controller.pill.btn_auto_speak.click()
        assert controller.config["voice"]["auto_speak_responses"] is False
        mock_agent.voice.stop_speaking.assert_called_once()
        assert controller.pill.btn_auto_speak.text() == "🔇 Auto-Read: OFF"

        with open(cfg_path, encoding="utf-8") as f:
            saved = f.read()
        assert "auto_speak_responses: false" in saved
        assert "# Speak every chat reply aloud" in saved
        assert "tts_voice: bm_george" in saved

        mock_agent.voice.stop_speaking.reset_mock()
        controller.pill.btn_auto_speak.click()
        assert controller.config["voice"]["auto_speak_responses"] is True
        mock_agent.voice.stop_speaking.assert_not_called()
        with open(cfg_path, encoding="utf-8") as f:
            saved_on = f.read()
        assert "auto_speak_responses: true" in saved_on
        assert "# Speak every chat reply aloud" in saved_on

        separate_cfg = {"voice": {"auto_speak_responses": True}}
        mock_agent.config = separate_cfg
        controller.pill.btn_auto_speak.click()
        assert separate_cfg["voice"]["auto_speak_responses"] is False
        assert controller.config["voice"]["auto_speak_responses"] is False

        controller.hide()

        missing_key = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        missing_key.write("voice:\n  enabled: true\n  tts_voice: bm_george\n")
        missing_key.close()
        try:
            controller.config_path = missing_key.name
            controller._persist_auto_speak(False)
            with open(missing_key.name, encoding="utf-8") as f:
                inserted = f.read()
            assert "auto_speak_responses: false" in inserted
            assert "tts_voice: bm_george" in inserted
        finally:
            os.remove(missing_key.name)
    finally:
        os.remove(cfg_path)




