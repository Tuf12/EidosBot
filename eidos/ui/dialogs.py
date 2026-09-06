"""PyQt6 Confirmation Dialog for Level 2 and Level 3 actions."""

from typing import Any, Dict, Tuple
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from eidos.safety.risk_classifier import RiskLevel


class ConfirmationDialog(QDialog):
    """
    Modal confirmation dialog requesting user authorization for Level 2 or Level 3 actions.
    Returns:
        exec() -> QDialog.DialogCode.Accepted / Rejected
        remember_checked -> bool
    """

    def __init__(
        self,
        action_type: str,
        details: Dict[str, Any],
        risk_level: RiskLevel,
        reason: str,
        parent=None,
    ):
        super().__init__(parent)
        self.action_type = action_type
        self.details = details
        self.risk_level = risk_level
        self.reason = reason
        self.remember_checked = False

        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("EidosBot - Action Confirmation")
        self.setMinimumWidth(540)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        is_destructive = self.risk_level == RiskLevel.LEVEL_3_DESTRUCTIVE

        # Header with icon and status
        header_layout = QHBoxLayout()
        icon_label = QLabel("🚨" if is_destructive else "⚠️")
        icon_label.setStyleSheet("font-size: 30px;")
        header_layout.addWidget(icon_label)

        title_text = (
            "<b>DANGER: Destructive Action Requested</b>"
            if is_destructive
            else "<b>Safety Gatekeeper - Shell Command Confirmation</b>"
        )
        title_label = QLabel(title_text)
        title_label.setStyleSheet(
            f"font-size: 15px; color: {'#ff5555' if is_destructive else '#ffb86c'};"
        )
        header_layout.addWidget(title_label, stretch=1)
        layout.addLayout(header_layout)

        # Description / Explanation
        reason_label = QLabel(f"<b>Reason:</b> {self.reason}")
        reason_label.setWordWrap(True)
        reason_label.setStyleSheet("color: #e0e0e0; font-size: 13px;")
        layout.addWidget(reason_label)

        # Command / Details Display
        cmd_text = self.details.get("command", "")
        if cmd_text:
            cmd_label = QLabel("<b>Command:</b>")
            cmd_label.setStyleSheet("color: #8be9fd; font-weight: bold;")
            layout.addWidget(cmd_label)

            cmd_box = QTextEdit()
            cmd_box.setReadOnly(True)
            cmd_box.setPlainText(cmd_text)
            cmd_box.setMaximumHeight(85)
            cmd_box.setStyleSheet(
                "background-color: #1e1f29; color: #50fa7b; font-family: monospace; "
                "font-size: 13px; border: 1px solid #44475a; border-radius: 4px; padding: 6px;"
            )
            layout.addWidget(cmd_box)

        # Working directory if available
        cwd = self.details.get("cwd")
        if cwd:
            cwd_label = QLabel(f"<span style='color: #6272a4;'>Working Directory:</span> <code>{cwd}</code>")
            cwd_label.setStyleSheet("font-size: 12px; color: #f8f8f2;")
            cwd_label.setWordWrap(True)
            layout.addWidget(cwd_label)

        # Level 3 vs Level 2 allowlist checkbox
        if is_destructive:
            warning_box = QLabel(
                "⚠️ This action is categorized as Level 3 (Destructive). "
                "It CANNOT be saved to the allowlist and must be approved on each execution."
            )
            warning_box.setWordWrap(True)
            warning_box.setStyleSheet(
                "background-color: #441111; color: #ff8888; border: 1px solid #aa3333; "
                "border-radius: 4px; padding: 8px; font-size: 12px;"
            )
            layout.addWidget(warning_box)
            self.checkbox = None
        else:
            self.checkbox = QCheckBox("Remember and allow this command in the future (add to allowlist)")
            self.checkbox.setStyleSheet(
                "color: #f8f8f2; font-size: 13px; padding-top: 4px; padding-bottom: 4px;"
            )
            layout.addWidget(self.checkbox)

        # Action Buttons: Approve (Enter), Deny (Esc)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.btn_reject = QPushButton("Reject (Esc)")
        self.btn_reject.setStyleSheet(
            "background-color: #6272a4; color: #f8f8f2; padding: 8px 18px; "
            "border-radius: 5px; font-weight: bold;"
        )
        self.btn_reject.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_reject)

        self.btn_approve = QPushButton("Approve (Enter)")
        approve_color = "#ff5555" if is_destructive else "#50fa7b"
        text_color = "#ffffff" if is_destructive else "#282a36"
        self.btn_approve.setStyleSheet(
            f"background-color: {approve_color}; color: {text_color}; "
            f"padding: 8px 22px; border-radius: 5px; font-weight: bold;"
        )
        self.btn_approve.setDefault(True)
        self.btn_approve.clicked.connect(self._on_approve)
        btn_layout.addWidget(self.btn_approve)

        layout.addLayout(btn_layout)

        # Overall Dialog Theme
        self.setStyleSheet(
            "QDialog { background-color: #282a36; color: #f8f8f2; } "
            "QPushButton:hover { opacity: 0.9; } "
            "QCheckBox::indicator { width: 18px; height: 18px; }"
        )

    def _on_approve(self):
        if self.checkbox and self.checkbox.isChecked():
            self.remember_checked = True
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_approve()
        else:
            super().keyPressEvent(event)


def prompt_confirmation_gui(
    action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str, parent=None
) -> Tuple[bool, bool]:
    """
    Helper function to launch the ConfirmationDialog and return (approved, remember).
    """
    dialog = ConfirmationDialog(action_type, details, risk_level, reason, parent=parent)
    result = dialog.exec()
    approved = (result == QDialog.DialogCode.Accepted)
    return approved, dialog.remember_checked
