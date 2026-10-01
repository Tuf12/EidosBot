"""First-run wizard for local user profile (~/.eidos/user_profile.yaml)."""

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from eidos.memory.types import INTERACTION_STYLE_CHOICES
from eidos.memory.user_store import UserStore


class ProfileOnboardingDialog(QDialog):
    """Collect basic user conventions and write them to UserStore."""

    def __init__(self, user_store: Optional[UserStore] = None, parent=None):
        super().__init__(parent)
        self.user_store = user_store or UserStore()
        self._init_ui()
        self._prefill()

    def _init_ui(self) -> None:
        self.setWindowTitle("Welcome to EidosBot — User Profile Setup")
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(
            "<b>Personalize EidosBot</b><br>"
            "<span style='color:#bd93f9;'>Stored only on this machine at ~/.eidos/user_profile.yaml "
            "(never committed to git).</span>"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.edit_name = QLineEdit()
        form.addRow("Display name:", self.edit_name)

        self.edit_projects = QLineEdit()
        self.edit_projects.setPlaceholderText("~/Projects")
        form.addRow("Projects folder:", self.edit_projects)

        self.combo_editor = QComboBox()
        self.combo_editor.setEditable(True)
        self.combo_editor.addItems(["Cursor IDE", "VS Code", "Neovim", "Other"])
        form.addRow("Primary editor:", self.combo_editor)

        self.combo_style = QComboBox()
        self.combo_style.setEditable(True)
        self.combo_style.addItems(list(INTERACTION_STYLE_CHOICES))
        form.addRow("Interaction style:", self.combo_style)

        self.combo_tools = QComboBox()
        self.combo_tools.addItems([
            "Deterministic shell over GUI clicking when possible",
            "Balance shell and GUI equally",
            "Prefer visual GUI when unsure",
        ])
        form.addRow("Tool preference:", self.combo_tools)

        layout.addLayout(form)

        path_label = QLabel(f"Profile file: <code>{self.user_store.path}</code>")
        path_label.setTextFormat(Qt.TextFormat.RichText)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: #8be9fd; font-size: 11px;")
        layout.addWidget(path_label)

        buttons = QHBoxLayout()
        buttons.addStretch()

        self.btn_skip = QPushButton("Skip for now")
        self.btn_skip.setStyleSheet(
            "background-color: #44475a; color: #f8f8f2; padding: 8px 16px; border-radius: 4px;"
        )
        self.btn_skip.clicked.connect(self._on_skip)
        buttons.addWidget(self.btn_skip)

        self.btn_save = QPushButton("Save & Continue")
        self.btn_save.setStyleSheet(
            "background-color: #50fa7b; color: #282a36; font-weight: bold; padding: 8px 20px; border-radius: 4px;"
        )
        self.btn_save.clicked.connect(self._on_save)
        buttons.addWidget(self.btn_save)

        layout.addLayout(buttons)

        from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

        self.setStyleSheet(
            "QDialog { background-color: #1e1f29; color: #f8f8f2; } "
            "QLineEdit, QComboBox { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 6px; }\n"
            f"{GLOBAL_TOOLTIP_STYLE}"
        )

    def _prefill(self) -> None:
        profile = self.user_store.load()
        self.edit_name.setText(str(profile.user_identity.get("name", "")))
        projects = profile.environment_facts.get("projects_dir")
        if not projects:
            projects = str(Path.home() / "Projects")
        self.edit_projects.setText(str(projects))
        editor = profile.environment_facts.get("primary_editor")
        if editor:
            idx = self.combo_editor.findText(str(editor))
            if idx >= 0:
                self.combo_editor.setCurrentIndex(idx)
            else:
                self.combo_editor.setEditText(str(editor))
        style = profile.preferences.get("interaction_style")
        if style:
            idx = self.combo_style.findText(str(style))
            if idx >= 0:
                self.combo_style.setCurrentIndex(idx)
            else:
                self.combo_style.setEditText(str(style))
        tool_pref = profile.preferences.get("tool_preference")
        if tool_pref:
            idx = self.combo_tools.findText(str(tool_pref))
            if idx >= 0:
                self.combo_tools.setCurrentIndex(idx)

    def _on_skip(self) -> None:
        self.user_store.mark_onboarding_complete()
        self.accept()

    def _on_save(self) -> None:
        self.user_store.apply_onboarding(
            display_name=self.edit_name.text().strip() or os.environ.get("USER", "User"),
            projects_dir=self.edit_projects.text().strip() or str(Path.home() / "Projects"),
            primary_editor=self.combo_editor.currentText().strip(),
            interaction_style=self.combo_style.currentText().strip(),
            tool_preference=self.combo_tools.currentText().strip(),
            mark_complete=True,
        )
        self.accept()


def maybe_show_profile_onboarding(user_store: UserStore, parent=None) -> bool:
    """Show onboarding dialog if needed. Returns True if dialog was shown."""
    if not user_store.needs_onboarding():
        return False
    dialog = ProfileOnboardingDialog(user_store=user_store, parent=parent)
    dialog.exec()
    return True
