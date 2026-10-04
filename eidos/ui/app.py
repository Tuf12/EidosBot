"""UI Controller and Qt Application integration for EidosBot."""

import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from eidos.core.loop import AgentLoop
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskLevel
from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.conversations_dialog import ConversationsDialog
from eidos.ui.dialogs import ConfirmationDialog
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill
from eidos.ui.profile_onboarding_dialog import maybe_show_profile_onboarding
from eidos.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class UIController(QObject):
    """
    Coordinates PyQt6 widgets (HUD overlay, floating pill, dialogs)
    with the asynchronous / background agent execution loop.
    """

    # Thread-safe signal to request a modal confirmation dialog from worker threads
    signal_prompt_confirmation = pyqtSignal(str, dict, int, str)
    signal_update_mic_state = pyqtSignal(bool)
    signal_voice_transcribed = pyqtSignal(str)

    # Thread-safe chat signals
    signal_chat_user_message = pyqtSignal(str)
    signal_chat_assistant_message = pyqtSignal(str)
    signal_chat_thought = pyqtSignal(str)
    signal_chat_tool_start = pyqtSignal(str, dict)
    signal_chat_tool_end = pyqtSignal(str, dict)
    signal_chat_error = pyqtSignal(str)
    signal_task_running = pyqtSignal(bool)
    signal_sync_conversation = pyqtSignal(str, str, list)

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        kill_switch: Optional[KillSwitch] = None,
        allowlist: Optional[AllowlistManager] = None,
    ):
        super().__init__()
        self.config = config or {}
        self.kill_switch = kill_switch or KillSwitch()
        self.allowlist = allowlist or AllowlistManager()

        # Thread synchronization for modal confirmation
        self._confirm_result_queue: queue.Queue = queue.Queue()

        # UI Components
        self.hud = HUDOverlay()
        self.pill = StatusPill()
        self.allowlist_dialog: Optional[AllowlistManagerDialog] = None
        self.settings_dialog: Optional[SettingsDialog] = None
        self.conversations_dialog: Optional[ConversationsDialog] = None

        self._agent_thread: Optional[threading.Thread] = None
        self._agent_loop: Optional[AgentLoop] = None
        self._telegram_gw = None
        self._remote_gw = None
        self._active_task_prompt: Optional[str] = None

        self._connect_signals()
        self._register_kill_switch_callbacks()

        # Prevent Qt from quitting when modal dialogs (Settings, Allowlist, Confirmations) close
        app = QApplication.instance()
        if app is not None:
            from eidos.ui.styles import apply_global_styles

            app.setQuitOnLastWindowClosed(False)
            icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "eidos_icon.png"))
            if os.path.exists(icon_path):
                from PyQt6.QtGui import QIcon
                app.setWindowIcon(QIcon(icon_path))
            apply_global_styles(app)

    def _connect_signals(self):
        # Confirmation signal handling on main Qt thread
        self.signal_prompt_confirmation.connect(self._show_confirmation_dialog)

        # Pill interactions
        self.pill.signal_emergency_kill.connect(self.trigger_emergency_kill)
        self.pill.signal_reset_kill.connect(self.reset_emergency_kill)
        self.pill.signal_open_allowlist.connect(self.open_allowlist_manager)
        self.pill.signal_open_settings.connect(self.open_settings_dialog)
        self.pill.signal_open_conversations.connect(self.open_conversations_dialog)
        self.pill.signal_open_workspace.connect(self.open_workspace_folder)
        self.pill.signal_open_skills.connect(self.open_skills_folder)
        self.pill.signal_new_chat.connect(self.start_new_conversation)
        self.pill.signal_quit_app.connect(self.quit_application)
        self.pill.signal_submit_task.connect(self.start_task_in_background)
        self.pill.signal_toggle_mic.connect(self._toggle_voice_mic)
        self.signal_update_mic_state.connect(self.pill.set_mic_recording)
        self.signal_voice_transcribed.connect(self._on_voice_transcribed)

        # Chat integration signals
        self.signal_chat_user_message.connect(self.pill.add_user_message)
        self.signal_chat_assistant_message.connect(self.pill.add_assistant_message)
        self.signal_chat_thought.connect(self.pill.add_thought_message)
        self.signal_chat_tool_start.connect(self.pill.add_tool_start_message)
        self.signal_chat_tool_end.connect(self.pill.add_tool_end_message)
        self.signal_chat_error.connect(self.pill.add_error_message)
        self.pill.signal_clear_chat.connect(self.clear_chat_history)
        self.pill.signal_stop_task.connect(self.stop_active_task)
        self.pill.signal_replay_tts.connect(self.replay_tts)
        self.pill.signal_edit_submitted.connect(self._on_message_edited)
        self.signal_task_running.connect(self.pill.set_task_running)
        self.signal_sync_conversation.connect(self._apply_conversation_sync)

    @pyqtSlot(str, str, list)
    def _apply_conversation_sync(self, conv_id: str, title: str, messages: list):
        """Update desktop chat view when active conversation changes remotely."""
        self.pill.load_conversation_messages(conv_id, title, messages)
        self.pill.set_status("idle", f"Conversation: {title}")

    @pyqtSlot()
    def clear_chat_history(self):
        """Clear conversational history in the active agent loop."""
        if self._agent_loop is not None:
            self._agent_loop.clear_history()
        self.pill.set_status("idle", "Chat history cleared")

    def _register_kill_switch_callbacks(self):
        """Notify UI whenever kill switch is activated."""
        def on_kill():
            self.hud.signal_show_alert.emit("kill", 2000)
            self.pill.signal_set_status.emit("killed", "Emergency stop tripped (Ctrl+Alt+Esc)")
            self._unblock_confirmation(wait=False)
            if self._agent_loop is not None and hasattr(self._agent_loop, "voice") and self._agent_loop.voice:
                try:
                    self._agent_loop.voice.stop_speaking()
                except Exception:
                    pass
            if self._agent_loop is not None and hasattr(self._agent_loop, "cancel_task"):
                try:
                    self._agent_loop.cancel_task()
                except Exception:
                    pass
            self.pill.set_task_running(False)

        self.kill_switch.register_callback(on_kill)

    def _drain_confirm_queue(self):
        """Discard stale confirmation responses before prompting again."""
        while True:
            try:
                self._confirm_result_queue.get_nowait()
            except queue.Empty:
                break

    def _unblock_confirmation(self, wait: bool = True):
        """Inject a denial into the confirmation queue to unblock worker threads."""
        try:
            self._confirm_result_queue.put_nowait((False, False))
        except queue.Full:
            if wait:
                self._drain_confirm_queue()
                self._confirm_result_queue.put((False, False))

    def show(self):
        """Display the HUD overlay and Floating Status Pill on screen."""
        self.hud.show()
        self.pill.show()

    def maybe_show_profile_onboarding(self):
        """First-run user profile wizard (local ~/.eidos only)."""
        if self._agent_loop is None or not getattr(self._agent_loop, "memory_enabled", False):
            return
        store = getattr(self._agent_loop, "user_store", None)
        if store is None:
            return
        maybe_show_profile_onboarding(store, parent=self.pill)

    def hide(self):
        """Hide HUD overlay and Floating Status Pill."""
        self.hud.hide()
        self.pill.hide()

    @pyqtSlot()
    def trigger_emergency_kill(self):
        """Trigger emergency stop from GUI."""
        self.kill_switch.trigger()
        self.hud.show_alert("kill", 2000)
        self.pill.set_status("killed", "Emergency stop triggered from GUI")
        self._unblock_confirmation(wait=False)

    @pyqtSlot()
    def reset_emergency_kill(self):
        """Reset emergency stop state."""
        self.kill_switch.reset()
        self.pill.set_status("idle", "Ready")

    @pyqtSlot()
    def open_allowlist_manager(self):
        """Open the Allowlist Manager window."""
        if self.allowlist_dialog is None:
            self.allowlist_dialog = AllowlistManagerDialog(allowlist=self.allowlist)
        else:
            self.allowlist_dialog.refresh_table()
        self.allowlist_dialog.show()
        self.allowlist_dialog.raise_()
        self.allowlist_dialog.activateWindow()

    @pyqtSlot()
    def open_settings_dialog(self):
        """Open the Settings dialog."""
        user_store = None
        if self._agent_loop is not None:
            user_store = getattr(self._agent_loop, "user_store", None)
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(config=self.config, user_store=user_store)
            self.settings_dialog.signal_settings_applied.connect(self.on_settings_applied)
            self.settings_dialog.signal_open_allowlist.connect(self.open_allowlist_manager)
        else:
            self.settings_dialog.config = self.config
            if user_store is not None:
                self.settings_dialog.user_store = user_store
            self.settings_dialog.load_from_config()

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    @pyqtSlot()
    def open_conversations_dialog(self):
        """Open the Past Conversations manager window."""
        if self._agent_loop is None or not getattr(self._agent_loop, "conversation_store", None):
            self.pill.set_status("idle", "Conversation persistence is disabled.")
            return

        store = self._agent_loop.conversation_store
        active_id = getattr(self._agent_loop, "active_conversation_id", None)

        if self.conversations_dialog is None:
            self.conversations_dialog = ConversationsDialog(
                conversation_store=store,
                active_conversation_id=active_id,
            )
            self.conversations_dialog.signal_conversation_selected.connect(self.switch_conversation)
            self.conversations_dialog.signal_new_conversation.connect(self.start_new_conversation)
            self.conversations_dialog.signal_conversation_deleted.connect(self._on_conversation_deleted)
        else:
            self.conversations_dialog.store = store
            self.conversations_dialog.active_conversation_id = active_id
            self.conversations_dialog.refresh_table()

        self.conversations_dialog.show()
        self.conversations_dialog.raise_()
        self.conversations_dialog.activateWindow()

    @pyqtSlot()
    def open_workspace_folder(self):
        """Open ~/EidosWorkspace in native desktop file manager (Nemo/xdg-open)."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        workspace_dir = os.path.abspath(os.path.expanduser(
            self.config.get("sandbox", {}).get("default_workspace", "~/EidosWorkspace")
        ))
        os.makedirs(workspace_dir, exist_ok=True)
        for sub in ("downloads", "documents", "jobs", "skills"):
            os.makedirs(os.path.join(workspace_dir, sub), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(workspace_dir))
        self.pill.set_status("idle", "Opened Eidos Workspace")

    @pyqtSlot()
    def open_skills_folder(self):
        """Open ~/.eidos/skills in native desktop file manager."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        skills_dir = os.path.abspath(os.path.expanduser(
            self.config.get("skills", {}).get("directory", "~/.eidos/skills")
        ))
        os.makedirs(skills_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(skills_dir))
        self.pill.set_status("idle", "Opened Skills folder")

    @pyqtSlot()
    def start_new_conversation(self):
        """Start a fresh conversation thread and update chat UI."""
        if self._agent_loop is None:
            self.pill.chat_history.clear_messages()
            self.pill.set_conversation_title("New Chat")
            self.pill.set_status("idle", "New chat started")
            return

        new_id = self._agent_loop.start_new_conversation(title="New Chat")
        self.pill.load_conversation_messages(new_id, "New Chat", [])
        self.pill.set_status("idle", "Started new conversation")

    @pyqtSlot(str)
    def switch_conversation(self, conv_id: str):
        """Switch active conversation and load its messages into the chat UI."""
        if self._agent_loop is None or not getattr(self._agent_loop, "conversation_store", None):
            return

        store = self._agent_loop.conversation_store
        success = self._agent_loop.switch_conversation(conv_id)
        if success:
            conv = store.get_conversation(conv_id)
            title = conv.get("title", "New Chat") if conv else "New Chat"
            messages = store.get_messages(conv_id)
            self.pill.load_conversation_messages(conv_id, title, messages)
            if not self.pill.is_expanded:
                self.pill.expand_chat()
            self.pill.set_status("idle", f"Loaded: {title}")

    @pyqtSlot(str)
    def _on_conversation_deleted(self, conv_id: str):
        """Handle conversation deletion from the dialog."""
        if self._agent_loop is None:
            return
        if getattr(self._agent_loop, "active_conversation_id", None) == conv_id:
            new_id = self._agent_loop.start_new_conversation(title="New Chat")
            self.pill.load_conversation_messages(new_id, "New Chat", [])
            self.pill.set_status("idle", "Conversation deleted")

    @pyqtSlot(dict)
    def on_settings_applied(self, new_config: dict):
        """Handle dynamic live update of system settings."""
        self.config = new_config

        # Update running AgentLoop
        if self._agent_loop is not None:
            self._agent_loop.update_config(new_config)

        # Dynamically manage Telegram Gateway
        gw_cfg = new_config.get("gateway", {})
        tg_cfg = gw_cfg.get("telegram", {})
        tg_enabled = bool(tg_cfg.get("enabled", False))
        bot_token = str(tg_cfg.get("bot_token", "")).strip()

        if tg_enabled and bot_token:
            raw_uids = tg_cfg.get("allowed_user_ids", [])
            parsed_uids = {int(x) for x in raw_uids if str(x).lstrip("-").isdigit()}
            if self._telegram_gw is None:
                try:
                    from eidos.gateway import TelegramGateway
                    self._telegram_gw = TelegramGateway(
                        agent_loop=self._agent_loop,
                        token=bot_token,
                        allowed_user_ids=list(parsed_uids),
                        token_env_var=tg_cfg.get("bot_token_env", "EIDOS_TELEGRAM_BOT_TOKEN"),
                        token_file=tg_cfg.get("token_file", "~/.eidos/telegram.token"),
                        notifications_config=tg_cfg.get("notifications"),
                        remote_kill_switch_enabled=tg_cfg.get("remote_kill_switch_enabled", True),
                    )
                    self._telegram_gw.start()
                    logger.info("TelegramGateway dynamically started from GUI settings.")
                except Exception as e:
                    logger.error("Failed to start TelegramGateway from GUI: %s", e)
            else:
                self._telegram_gw.token = bot_token
                self._telegram_gw.allowed_user_ids = parsed_uids
                self._telegram_gw.notifications_config = tg_cfg.get("notifications") or {}
                self._telegram_gw.remote_kill_switch_enabled = tg_cfg.get("remote_kill_switch_enabled", True)
                if not self._telegram_gw.is_running():
                    try:
                        self._telegram_gw.start()
                        logger.info("TelegramGateway started from updated GUI settings.")
                    except Exception as e:
                        logger.error("Failed to start TelegramGateway: %s", e)
        else:
            if self._telegram_gw and self._telegram_gw.is_running():
                try:
                    self._telegram_gw.stop()
                    logger.info("TelegramGateway stopped (disabled in GUI settings).")
                except Exception as e:
                    logger.error("Error stopping TelegramGateway: %s", e)

        self._rebind_confirmation_callbacks()

        pref = new_config.get("models", {}).get("provider_preference", "local")
        self.pill.set_status("idle", f"Settings updated (Mode: {pref})")

    def set_telegram_gateway(self, telegram_gw):
        """Set the Telegram Gateway instance."""
        self._telegram_gw = telegram_gw
        self._rebind_confirmation_callbacks()

    def set_remote_gateway(self, gateway):
        """Set the Remote Gateway Server instance."""
        self._remote_gw = gateway
        self._rebind_confirmation_callbacks()

    def _rebind_confirmation_callbacks(self):
        """Rebind multi-channel confirmation callback across Desktop modal, Web Gateway, and Telegram."""
        if self._agent_loop is None:
            return

        ui_cb = self.get_confirmation_callback()
        gw_cb = (
            self._remote_gw.create_confirmation_callback()
            if (self._remote_gw and hasattr(self._remote_gw, "is_running") and self._remote_gw.is_running())
            else None
        )
        tg_cb = (
            self._telegram_gw.create_confirmation_callback()
            if (self._telegram_gw and hasattr(self._telegram_gw, "is_running") and self._telegram_gw.is_running() and bool(self._telegram_gw.allowed_user_ids))
            else None
        )

        if gw_cb or tg_cb:
            from eidos.gateway import TelegramGateway
            self._agent_loop.gatekeeper.custom_prompt_callback = TelegramGateway.combine_callbacks(
                ui_cb, gw_cb, tg_cb
            )
        else:
            self._agent_loop.gatekeeper.custom_prompt_callback = ui_cb

    @pyqtSlot()
    def quit_application(self):
        """Cleanly exit EidosBot."""
        logger.info("Exiting EidosBot from GUI menu...")
        if self._telegram_gw:
            try:
                self._telegram_gw.stop()
            except Exception as e:
                logger.warning("Error stopping Telegram Gateway: %s", e)
        if self._remote_gw:
            try:
                self._remote_gw.stop()
            except Exception as e:
                logger.warning("Error stopping Remote Gateway: %s", e)
        self.kill_switch.stop()
        app = QApplication.instance()
        if app:
            app.quit()

    def get_confirmation_callback(self):
        """
        Returns a thread-safe callback function matching Gatekeeper's signature:
        callback(action_type, details, risk_level, reason) -> Tuple[bool, bool]
        """
        def callback(action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str) -> Tuple[bool, bool]:
            # Emit signal to main GUI thread and block until user clicks Approve/Reject
            self._drain_confirm_queue()
            self.pill.signal_set_status.emit("confirming", f"Confirm: {action_type}")
            self.signal_prompt_confirmation.emit(action_type, details, int(risk_level), reason)

            timeout_seconds = float(
                self.config.get("safety", {}).get("confirmation_timeout_seconds", 120)
            )
            deadline = time.monotonic() + timeout_seconds
            while True:
                if self.kill_switch.is_triggered():
                    logger.warning("Confirmation auto-denied: kill switch active")
                    return False, False

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "Confirmation timed out after %.0fs for action '%s'",
                        timeout_seconds,
                        action_type,
                    )
                    self.pill.signal_set_status.emit(
                        "error",
                        f"Confirmation timed out ({int(timeout_seconds)}s)",
                    )
                    self.signal_chat_error.emit(
                        f"Action confirmation timed out after {int(timeout_seconds)} seconds. "
                        "Look for the confirmation dialog or reset the kill switch and try again."
                    )
                    return False, False

                try:
                    approved, remember = self._confirm_result_queue.get(
                        timeout=min(1.0, remaining)
                    )
                    return approved, remember
                except queue.Empty:
                    continue

        return callback

    @pyqtSlot(str, dict, int, str)
    def _show_confirmation_dialog(self, action_type: str, details: dict, risk_level_int: int, reason: str):
        """Executed on Qt GUI main thread."""
        risk_level = RiskLevel(risk_level_int)
        dialog = ConfirmationDialog(action_type, details, risk_level, reason)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        result = dialog.exec()
        approved = (result == ConfirmationDialog.DialogCode.Accepted)
        remember = dialog.remember_checked

        try:
            self._confirm_result_queue.put_nowait((approved, remember))
        except queue.Full:
            self._drain_confirm_queue()
            self._confirm_result_queue.put((approved, remember))

    def set_agent_loop(self, agent_loop: AgentLoop):
        """Attach agent loop and hook UI visual feedback to perception and actions."""
        self._agent_loop = agent_loop

        # Hook gatekeeper's confirmation prompt to UI modal (and remote callbacks if present)
        self._rebind_confirmation_callbacks()

        # Wrap agent inputs to render HUD reticles before clicks
        original_click = agent_loop.inputs.click

        def visual_click(x: int, y: int, button: str = "left", clicks: int = 1):
            self.hud.signal_show_click.emit(x, y, "#ff5555", 350)
            self.pill.signal_set_status.emit("executing", f"Click at ({x}, {y})")
            return original_click(x=x, y=y, button=button, clicks=clicks)

        agent_loop.inputs.click = visual_click

        # Register voice state callbacks with VoiceManager
        if hasattr(agent_loop, "voice") and agent_loop.voice:
            agent_loop.voice.register_on_state_change(
                lambda is_rec: self.signal_update_mic_state.emit(is_rec)
            )
            agent_loop.voice.register_on_transcribed(
                lambda text: self.signal_voice_transcribed.emit(text)
            )

        # Register execution and chat callbacks with AgentLoop
        agent_loop.register_callback("on_thought", lambda t: self.signal_chat_thought.emit(t))
        agent_loop.register_callback("on_tool_start", lambda n, a: self.signal_chat_tool_start.emit(n, a))
        agent_loop.register_callback("on_tool_end", lambda n, r: self.signal_chat_tool_end.emit(n, r))
        agent_loop.register_callback("on_response", lambda r: self.signal_chat_assistant_message.emit(r))
        agent_loop.register_callback(
            "on_conversation_title_changed",
            lambda t: self.pill.signal_set_conversation_title.emit(t),
        )

        # Remote conversation change synchronization (e.g. switched from Mobile Gateway)
        def _on_remote_conv_changed(cid, title):
            msgs = []
            if getattr(agent_loop, "conversation_store", None) and cid:
                try:
                    msgs = agent_loop.conversation_store.get_messages(cid)
                except Exception:
                    pass
            self.signal_sync_conversation.emit(cid or "", title or "New Chat", msgs)

        agent_loop.register_callback("on_conversation_changed", _on_remote_conv_changed)

        # Remote task start synchronization (e.g. prompt sent from mobile gateway)
        def _on_remote_task_start(goal: str):
            if not self.is_task_running() or self._active_task_prompt != goal:
                self.signal_chat_user_message.emit(goal)
                self.pill.signal_set_status.emit("thinking", f"Task: {goal}")

        agent_loop.register_callback("on_task_start", _on_remote_task_start)

        # Restore persistent conversation into chat UI on startup
        if getattr(agent_loop, "conversation_store", None) and getattr(agent_loop, "active_conversation_id", None):
            conv = agent_loop.conversation_store.get_conversation(agent_loop.active_conversation_id)
            title = conv.get("title", "New Chat") if conv else "New Chat"
            msgs = agent_loop.conversation_store.get_messages(agent_loop.active_conversation_id)
            self.pill.load_conversation_messages(agent_loop.active_conversation_id, title, msgs)

    @pyqtSlot(str)
    def _on_voice_transcribed(self, text: str):
        """Place heard speech in the prompt bar and, by default, send it as the next turn."""
        cleaned = (text or "").strip()
        self.pill.set_prompt_text(cleaned)
        if not cleaned:
            return
        voice_cfg = self.config.get("voice", {})
        if not voice_cfg.get("enabled", True):
            return
        if not voice_cfg.get("auto_submit_transcription", True):
            return
        logger.info("Auto-submitting transcribed speech as chat turn.")
        self.start_task_in_background(cleaned)

    @pyqtSlot()
    def _toggle_voice_mic(self):
        """Toggle push-to-talk microphone recording."""
        if self._agent_loop is None or not hasattr(self._agent_loop, "voice") or not self._agent_loop.voice:
            self.pill.set_status("error", "Voice subsystem not available.")
            return

        voice = self._agent_loop.voice
        if voice.is_recording():
            self.pill.signal_set_status.emit("thinking", "Transcribing speech...")

            def _stop():
                text = voice.stop_listening()
                if text:
                    self.pill.signal_set_status.emit("idle", f"Heard: '{text}'")
                else:
                    self.pill.signal_set_status.emit("idle", "No speech detected.")

            threading.Thread(target=_stop, daemon=True).start()
        else:
            voice.start_listening()
            self.pill.signal_set_status.emit("perceiving", "Listening to mic...")

    def is_task_running(self) -> bool:
        """Return True when a background agent task thread is still active."""
        return self._agent_thread is not None and self._agent_thread.is_alive()

    def start_task_in_background(self, task_prompt: str):
        """Run an agent task in a dedicated worker thread so GUI remains responsive."""
        if self._agent_loop is None:
            logger.error("No AgentLoop registered with UIController.")
            self.signal_chat_error.emit("Error: AgentLoop is not registered with UIController.")
            return

        if self.is_task_running():
            logger.warning("An agent task is already running.")
            active = self._active_task_prompt or "unknown task"
            self.pill.set_status("executing", "Task already in progress...")
            self.signal_chat_error.emit(
                f"Still working on: \"{active}\". "
                "Wait for it to finish, approve/deny any pending confirmation dialog, "
                "or press Emergency Stop (Ctrl+Alt+Esc) to cancel."
            )
            return

        if self.kill_switch.is_triggered():
            self.signal_chat_error.emit(
                "Kill switch is active. Reset it from the pill menu before starting a new task."
            )
            self.pill.set_status("killed", "Reset kill switch to continue")
            return

        if self._agent_loop is not None and hasattr(self._agent_loop, "voice") and self._agent_loop.voice:
            try:
                self._agent_loop.voice.stop_speaking()
            except Exception:
                pass

        self.pill.clear_prompt()
        self.pill.set_status("thinking", f"Task: {task_prompt}")
        self.signal_task_running.emit(True)
        self.signal_chat_user_message.emit(task_prompt)
        self._active_task_prompt = task_prompt

        def worker():
            try:
                res = self._agent_loop.run_task(task_prompt)
                summary = res.get("summary", "")
                if res.get("success"):
                    self.pill.signal_set_status.emit("idle", f"Done: {summary}")
                    if summary:
                        self.signal_chat_assistant_message.emit(summary)
                else:
                    self.pill.signal_set_status.emit("error", f"Ended: {summary}")
                    self.signal_chat_error.emit(summary or "Task ended unsuccessfully.")
            except Exception as e:
                logger.error("Task execution error: %s", e)
                self.pill.signal_set_status.emit("error", str(e))
                self.signal_chat_error.emit(f"Execution Error: {e}")
            finally:
                self._active_task_prompt = None
                self._unblock_confirmation(wait=False)
                self.signal_task_running.emit(False)

        self._agent_thread = threading.Thread(target=worker, daemon=True)
        self._agent_thread.start()

    @pyqtSlot()
    def stop_active_task(self):
        """Stop/cancel the running agent task, halt speech playback, and unblock modals."""
        logger.info("Stopping active agent task requested by user.")
        # 1. Stop voice immediately
        if self._agent_loop is not None and hasattr(self._agent_loop, "voice") and self._agent_loop.voice:
            try:
                self._agent_loop.voice.stop_speaking()
            except Exception:
                pass

        # 2. Unblock any confirmation queue waiting on agent thread
        self._unblock_confirmation(wait=False)

        # 3. Request loop cancellation
        if self._agent_loop is not None and hasattr(self._agent_loop, "cancel_task"):
            self._agent_loop.cancel_task()

        self.pill.set_status("idle", "Task stopped by user")
        self.pill.set_task_running(False)
        self.signal_chat_assistant_message.emit("⏹ *Task stopped by user.*")

    @pyqtSlot(str)
    def replay_tts(self, text: str):
        """Replay speech (TTS) for a chat message, interrupting any active playback."""
        clean_text = (text or "").strip()
        if not clean_text:
            return
        if self._agent_loop is None or not hasattr(self._agent_loop, "voice") or not self._agent_loop.voice:
            self.pill.set_status("error", "Voice subsystem not available.")
            return

        voice = self._agent_loop.voice

        tts_avail = getattr(getattr(voice, "tts", None), "is_available", True)
        if tts_avail is False:
            self.pill.set_status("error", "Kokoro TTS is not available.")
            return

        # Stop any active or residual speech first so replay starts clean immediately
        try:
            voice.stop_speaking()
        except Exception:
            pass

        self.pill.set_status("executing", "Speaking response...")
        try:
            success = False
            if hasattr(voice, "speak_response"):
                success = voice.speak_response(clean_text)
            else:
                success = voice.speak(clean_text, blocking=False)
            if not success:
                logger.warning("Voice replay produced no speech clips.")
                self.pill.set_status("idle", "No speech to replay")
        except Exception as e:
            logger.error("Failed to replay TTS: %s", e)
            self.pill.set_status("error", f"TTS error: {e}")

    @pyqtSlot(str, str)
    def _on_message_edited(self, message_id: str, new_text: str):
        """Handle persistence update when a past message is edited."""
        if not message_id or not self._agent_loop:
            return
        store = getattr(self._agent_loop, "conversation_store", None)
        if store:
            try:
                store.update_message(message_id, new_text)
                active_id = getattr(self._agent_loop, "active_conversation_id", None)
                if active_id:
                    store.delete_messages_after(active_id, message_id, inclusive=False)
            except Exception as e:
                logger.warning("Failed to update edited message in store: %s", e)
