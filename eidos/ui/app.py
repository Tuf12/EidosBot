"""UI Controller and Qt Application integration for EidosBot."""

import logging
import queue
import threading
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from eidos.core.loop import AgentLoop
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskLevel
from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.dialogs import ConfirmationDialog
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill
from eidos.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class UIController(QObject):
    """
    Coordinates PyQt6 widgets (HUD overlay, floating pill, dialogs)
    with the asynchronous / background agent execution loop.
    """

    # Thread-safe signal to request a modal confirmation dialog from worker threads
    signal_prompt_confirmation = pyqtSignal(str, dict, int, str)

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

        self._agent_thread: Optional[threading.Thread] = None
        self._agent_loop: Optional[AgentLoop] = None

        self._connect_signals()
        self._register_kill_switch_callbacks()

        # Prevent Qt from quitting when modal dialogs (Settings, Allowlist, Confirmations) close
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

    def _connect_signals(self):
        # Confirmation signal handling on main Qt thread
        self.signal_prompt_confirmation.connect(self._show_confirmation_dialog)

        # Pill interactions
        self.pill.signal_emergency_kill.connect(self.trigger_emergency_kill)
        self.pill.signal_reset_kill.connect(self.reset_emergency_kill)
        self.pill.signal_open_allowlist.connect(self.open_allowlist_manager)
        self.pill.signal_open_settings.connect(self.open_settings_dialog)
        self.pill.signal_quit_app.connect(self.quit_application)
        self.pill.signal_submit_task.connect(self.start_task_in_background)

    def _register_kill_switch_callbacks(self):
        """Notify UI whenever kill switch is activated."""
        def on_kill():
            self.hud.signal_show_alert.emit("kill", 2000)
            self.pill.signal_set_status.emit("killed", "Emergency stop tripped (Ctrl+Alt+Esc)")

        self.kill_switch.register_callback(on_kill)

    def show(self):
        """Display the HUD overlay and Floating Status Pill on screen."""
        self.hud.show()
        self.pill.show()

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
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(config=self.config)
            self.settings_dialog.signal_settings_applied.connect(self.on_settings_applied)
            self.settings_dialog.signal_open_allowlist.connect(self.open_allowlist_manager)
        else:
            self.settings_dialog.config = self.config
            self.settings_dialog.load_from_config()

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    @pyqtSlot(dict)
    def on_settings_applied(self, new_config: dict):
        """Handle dynamic live update of system settings."""
        self.config = new_config

        # Update running AgentLoop
        if self._agent_loop is not None:
            self._agent_loop.update_config(new_config)

        pref = new_config.get("models", {}).get("provider_preference", "local")
        self.pill.set_status("idle", f"Settings updated (Mode: {pref})")

    @pyqtSlot()
    def quit_application(self):
        """Cleanly exit EidosBot."""
        logger.info("Exiting EidosBot from GUI menu...")
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
            self.pill.signal_set_status.emit("confirming", f"Confirm: {action_type}")
            self.signal_prompt_confirmation.emit(action_type, details, int(risk_level), reason)
            approved, remember = self._confirm_result_queue.get()
            return approved, remember

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

        self._confirm_result_queue.put((approved, remember))

    def set_agent_loop(self, agent_loop: AgentLoop):
        """Attach agent loop and hook UI visual feedback to perception and actions."""
        self._agent_loop = agent_loop

        # Hook gatekeeper's confirmation prompt to UI modal
        agent_loop.gatekeeper.custom_prompt_callback = self.get_confirmation_callback()

        # Wrap agent inputs to render HUD reticles before clicks
        original_click = agent_loop.inputs.click

        def visual_click(x: int, y: int, button: str = "left", clicks: int = 1):
            self.hud.signal_show_click.emit(x, y, "#ff5555", 350)
            self.pill.signal_set_status.emit("executing", f"Click at ({x}, {y})")
            return original_click(x=x, y=y, button=button, clicks=clicks)

        agent_loop.inputs.click = visual_click

    def start_task_in_background(self, task_prompt: str):
        """Run an agent task in a dedicated worker thread so GUI remains responsive."""
        if self._agent_loop is None:
            logger.error("No AgentLoop registered with UIController.")
            return

        if self._agent_thread and self._agent_thread.is_alive():
            logger.warning("An agent task is already running.")
            self.pill.set_status("executing", "Task already in progress...")
            return

        def worker():
            self.pill.signal_set_status.emit("thinking", f"Task: {task_prompt}")
            try:
                res = self._agent_loop.run_task(task_prompt)
                if res.get("success"):
                    self.pill.signal_set_status.emit("idle", f"Done: {res.get('summary', '')}")
                else:
                    self.pill.signal_set_status.emit("error", f"Ended: {res.get('summary', '')}")
            except Exception as e:
                logger.error("Task execution error: %s", e)
                self.pill.signal_set_status.emit("error", str(e))

        self._agent_thread = threading.Thread(target=worker, daemon=True)
        self._agent_thread.start()
