"""PyQt6 Floating Status Pill widget for desktop state monitoring and quick interaction."""

import logging
from typing import Optional
from PyQt6.QtCore import QPoint, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

STATUS_COLORS = {
    "idle": ("#50fa7b", "● Ready"),
    "thinking": ("#f1fa8c", "● Thinking"),
    "perceiving": ("#8be9fd", "● Perceiving"),
    "confirming": ("#ffb86c", "● Awaiting Confirm"),
    "executing": ("#bd93f9", "● Executing"),
    "killed": ("#ff5555", "🛑 Stopped"),
    "error": ("#ff5555", "● Error"),
}


class StatusPill(QWidget):
    """
    Compact, draggable floating HUD widget positioned on top of the desktop.
    Displays live agent state, quick prompt input, emergency stop, and allowlist manager button.
    """

    # Signals
    signal_set_status = pyqtSignal(str, str)  # status_key, message
    signal_submit_task = pyqtSignal(str)      # user prompt
    signal_emergency_kill = pyqtSignal()
    signal_open_allowlist = pyqtSignal()
    signal_open_settings = pyqtSignal()
    signal_reset_kill = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = QPoint()
        self._current_state = "idle"
        self._init_window()
        self._init_ui()
        self._connect_signals()

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        # Initial positioning (top-right of screen)
        screen = self.screen()
        if screen:
            geom = screen.geometry()
            self.move(geom.width() - 480, 40)

    def _init_ui(self):
        # Outer container frame for rounded corners & shadow look
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        self.card = QFrame()
        self.card.setStyleSheet(
            "QFrame { background-color: #1e1f29; border: 1px solid #44475a; "
            "border-radius: 12px; }"
        )
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(8)

        # Top Bar: Status Dot + State Text + Controls
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        # Menu Button (Dropdown for Settings, Allowlist, Kill)
        self.btn_menu = QPushButton("☰")
        self.btn_menu.setToolTip("Main Menu (Settings, Allowlist, Actions)")
        self.btn_menu.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 6px; padding: 4px 8px; font-size: 13px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; }"
        )
        self.btn_menu.clicked.connect(self._show_menu)
        top_bar.addWidget(self.btn_menu)

        # Draggable Grab Indicator
        self.grab_icon = QLabel("⠿")
        self.grab_icon.setStyleSheet("color: #6272a4; font-size: 14px; font-weight: bold;")
        self.grab_icon.setToolTip("Click and drag to move pill")
        top_bar.addWidget(self.grab_icon)

        # Status Indicator Label
        self.status_label = QLabel("● Ready")
        self.status_label.setStyleSheet("color: #50fa7b; font-weight: bold; font-size: 13px;")
        top_bar.addWidget(self.status_label)

        # Detailed message / active task
        self.details_label = QLabel("EidosBot Idle")
        self.details_label.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        top_bar.addWidget(self.details_label, stretch=1)

        # Allowlist Button
        self.btn_allowlist = QPushButton("🛡️")
        self.btn_allowlist.setToolTip("Open Shell Command Allowlist Manager")
        self.btn_allowlist.setStyleSheet(
            "QPushButton { background-color: #282a36; border: 1px solid #6272a4; "
            "border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; }"
        )
        self.btn_allowlist.clicked.connect(self.signal_open_allowlist.emit)
        top_bar.addWidget(self.btn_allowlist)

        # Settings Button
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setToolTip("Open Settings (Models, API Keys, Safety)")
        self.btn_settings.setStyleSheet(
            "QPushButton { background-color: #282a36; border: 1px solid #6272a4; "
            "border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; }"
        )
        self.btn_settings.clicked.connect(self.signal_open_settings.emit)
        top_bar.addWidget(self.btn_settings)

        # Emergency Stop / Reset Button
        self.btn_kill = QPushButton("🛑 Stop")
        self.btn_kill.setToolTip("Emergency Stop (Ctrl + Alt + Esc)")
        self.btn_kill.setStyleSheet(
            "QPushButton { background-color: #ff5555; color: white; font-weight: bold; "
            "border-radius: 6px; padding: 4px 10px; font-size: 12px; border: none; } "
            "QPushButton:hover { background-color: #ff3333; }"
        )
        self.btn_kill.clicked.connect(self._on_kill_or_reset)
        top_bar.addWidget(self.btn_kill)

        card_layout.addLayout(top_bar)

        # Prompt Input Field (Expandable desktop prompt)
        prompt_bar = QHBoxLayout()
        prompt_bar.setSpacing(6)

        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Ask EidosBot to do something...")
        self.prompt_input.setStyleSheet(
            "QLineEdit { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; "
            "border-radius: 6px; padding: 6px 10px; font-size: 12px; } "
            "QLineEdit:focus { border: 1px solid #bd93f9; }"
        )
        self.prompt_input.returnPressed.connect(self._on_submit_prompt)
        prompt_bar.addWidget(self.prompt_input, stretch=1)

        self.btn_send = QPushButton("▶")
        self.btn_send.setToolTip("Execute prompt")
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #bd93f9; color: #282a36; font-weight: bold; "
            "border-radius: 6px; padding: 6px 12px; font-size: 12px; border: none; } "
            "QPushButton:hover { background-color: #ff79c6; }"
        )
        self.btn_send.clicked.connect(self._on_submit_prompt)
        prompt_bar.addWidget(self.btn_send)

        card_layout.addLayout(prompt_bar)
        main_layout.addWidget(self.card)

    def _connect_signals(self):
        self.signal_set_status.connect(self.set_status)

    @pyqtSlot(str, str)
    def set_status(self, status_key: str, message: str = ""):
        """Update the status pill display."""
        self._current_state = status_key.lower()
        color_hex, label_text = STATUS_COLORS.get(self._current_state, ("#50fa7b", f"● {status_key}"))

        self.status_label.setText(label_text)
        self.status_label.setStyleSheet(f"color: {color_hex}; font-weight: bold; font-size: 13px;")

        if message:
            # Truncate details if overly long
            display_msg = message if len(message) <= 50 else message[:47] + "..."
            self.details_label.setText(display_msg)

        if self._current_state == "killed":
            self.btn_kill.setText("🔄 Reset")
            self.btn_kill.setStyleSheet(
                "background-color: #ffb86c; color: #282a36; font-weight: bold; "
                "border-radius: 6px; padding: 4px 10px; font-size: 12px; border: none;"
            )
        else:
            self.btn_kill.setText("🛑 Stop")
            self.btn_kill.setStyleSheet(
                "background-color: #ff5555; color: white; font-weight: bold; "
                "border-radius: 6px; padding: 4px 10px; font-size: 12px; border: none;"
            )

    def _show_menu(self):
        """Display dropdown menu from ☰ button."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #21222c; color: #f8f8f2; border: 1px solid #44475a; border-radius: 6px; padding: 4px; } "
            "QMenu::item { padding: 6px 20px 6px 24px; border-radius: 4px; } "
            "QMenu::item:selected { background-color: #44475a; color: #50fa7b; }"
        )
        action_settings = menu.addAction("⚙️  Settings (Models & API Keys)...")
        action_allowlist = menu.addAction("🛡️  Shell Allowlist Manager...")
        menu.addSeparator()
        if self._current_state == "killed":
            action_kill = menu.addAction("🔄  Reset Emergency Stop")
        else:
            action_kill = menu.addAction("🛑  Emergency Stop (Ctrl+Alt+Esc)")

        # Position menu right below the button
        action = menu.exec(self.btn_menu.mapToGlobal(QPoint(0, self.btn_menu.height() + 4)))
        if action == action_settings:
            self.signal_open_settings.emit()
        elif action == action_allowlist:
            self.signal_open_allowlist.emit()
        elif action == action_kill:
            self._on_kill_or_reset()

    def _on_kill_or_reset(self):
        if self._current_state == "killed":
            self.signal_reset_kill.emit()
            self.set_status("idle", "Kill switch reset")
        else:
            self.signal_emergency_kill.emit()

    def _on_submit_prompt(self):
        prompt = self.prompt_input.text().strip()
        if not prompt:
            return
        self.prompt_input.clear()
        self.set_status("thinking", f"Task: {prompt}")
        self.signal_submit_task.emit(prompt)

    # Mouse drag events for repositioning the pill on the desktop
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
