"""PyQt6 Command Allowlist Manager GUI for reviewing and revoking shell permissions."""

from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from eidos.safety.allowlist import AllowlistManager
from eidos.safety.risk_classifier import RiskClassifier, RiskLevel


class AllowlistManagerDialog(QDialog):
    """
    GUI window for managing the Level 2 shell command allowlist.
    Allows viewing all allowed commands, searching, revoking permission, or adding new ones.
    """

    def __init__(self, allowlist: Optional[AllowlistManager] = None, parent=None):
        super().__init__(parent)
        self.allowlist = allowlist or AllowlistManager()
        self._init_ui()
        self.refresh_table()

    def _init_ui(self):
        self.setWindowTitle("EidosBot - Shell Command Allowlist Manager")
        self.setMinimumSize(780, 480)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header Title and Subtitle
        header_layout = QVBoxLayout()
        title = QLabel("🛡️ <b>Shell Command Allowlist</b>")
        title.setStyleSheet("font-size: 16px; color: #50fa7b;")
        subtitle = QLabel(
            "Commands listed here execute without prompting for confirmation. "
            "Click <b>Revoke</b> to restore confirmation prompts for any command."
        )
        subtitle.setStyleSheet("color: #bfbfbf; font-size: 12px;")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)

        # Search Bar + Add Button
        top_bar = QHBoxLayout()
        search_label = QLabel("Search:")
        search_label.setStyleSheet("color: #8be9fd; font-weight: bold;")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter commands by keyword (e.g., git, python, npm)...")
        self.search_input.setStyleSheet(
            "background-color: #1e1f29; color: #f8f8f2; border: 1px solid #44475a; "
            "border-radius: 4px; padding: 6px 10px; font-size: 13px;"
        )
        self.search_input.textChanged.connect(self._filter_table)

        self.btn_add = QPushButton("+ Add Command")
        self.btn_add.setStyleSheet(
            "background-color: #bd93f9; color: #282a36; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_add.clicked.connect(self._on_add_command)

        top_bar.addWidget(search_label)
        top_bar.addWidget(self.search_input, stretch=1)
        top_bar.addWidget(self.btn_add)
        layout.addLayout(top_bar)

        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Command", "Description", "Added Date", "Runs", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 100)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTableWidget { background-color: #21222c; alternate-background-color: #282a36; "
            "color: #f8f8f2; gridline-color: #44475a; border: 1px solid #44475a; border-radius: 4px; } "
            "QHeaderView::section { background-color: #191a21; color: #8be9fd; font-weight: bold; "
            "padding: 6px; border: 1px solid #44475a; }"
        )
        layout.addWidget(self.table)

        # Bottom Controls
        bottom_layout = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #50fa7b; font-size: 12px;")
        bottom_layout.addWidget(self.status_label, stretch=1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setStyleSheet(
            "background-color: #44475a; color: #f8f8f2; padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_refresh.clicked.connect(self.refresh_table)
        bottom_layout.addWidget(self.btn_refresh)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(
            "background-color: #6272a4; color: #f8f8f2; font-weight: bold; padding: 6px 18px; border-radius: 4px;"
        )
        self.btn_close.clicked.connect(self.accept)
        bottom_layout.addWidget(self.btn_close)

        layout.addLayout(bottom_layout)

        from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

        self.setStyleSheet(
            "QDialog { background-color: #282a36; color: #f8f8f2; } "
            "QPushButton:hover { opacity: 0.85; }\n"
            f"{GLOBAL_TOOLTIP_STYLE}"
        )

    def refresh_table(self):
        """Reload allowlist items from disk into table."""
        entries = self.allowlist.get_all()
        self.table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            cmd = entry.get("command", "")
            desc = entry.get("description", "")
            created_at = entry.get("created_at", "")[:10]  # Just YYYY-MM-DD
            runs = str(entry.get("run_count", 0))

            # Command Item
            cmd_item = QTableWidgetItem(cmd)
            cmd_item.setFlags(cmd_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cmd_item.setForeground(Qt.GlobalColor.green)
            self.table.setItem(row, 0, cmd_item)

            # Description Item
            desc_item = QTableWidgetItem(desc)
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, desc_item)

            # Date Item
            date_item = QTableWidgetItem(created_at)
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, date_item)

            # Runs Item
            runs_item = QTableWidgetItem(runs)
            runs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            runs_item.setFlags(runs_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, runs_item)

            # Revoke Button
            btn_revoke = QPushButton("Revoke")
            btn_revoke.setStyleSheet(
                "background-color: #ff5555; color: white; font-weight: bold; "
                "font-size: 11px; padding: 4px; border-radius: 3px;"
            )
            # Bind closure with current command
            btn_revoke.clicked.connect(lambda checked, c=cmd: self._on_revoke_command(c))
            self.table.setCellWidget(row, 4, btn_revoke)

        self.status_label.setText(f"Total allowed commands: {len(entries)}")
        self._filter_table(self.search_input.text())

    def _filter_table(self, text: str):
        """Filter table rows based on search input."""
        query = text.strip().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                cmd_text = item.text().lower()
                self.table.setRowHidden(row, query not in cmd_text)

    def _on_revoke_command(self, cmd: str):
        """Revoke permission for the given command."""
        confirm = QMessageBox.question(
            self,
            "Revoke Permission",
            f"Are you sure you want to revoke permission for:\n\n{cmd}\n\n"
            "Future executions will require your confirmation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.allowlist.revoke(cmd)
            self.refresh_table()
            self.status_label.setText(f"Revoked permission for: {cmd}")

    def _on_add_command(self):
        """Prompt to manually add a command to the allowlist."""
        cmd, ok = QInputDialog.getText(
            self,
            "Add Allowed Command",
            "Enter exact command to allow (e.g. 'git pull' or 'npm run build'):",
            QLineEdit.EchoMode.Normal,
        )
        if ok and cmd.strip():
            cmd_clean = cmd.strip()
            risk, reason = RiskClassifier.classify_shell_command(cmd_clean)
            if risk == RiskLevel.LEVEL_3_DESTRUCTIVE:
                QMessageBox.critical(
                    self,
                    "Cannot Allow Destructive Command",
                    f"Command '{cmd_clean}' is classified as Level 3 (Destructive).\n\n"
                    f"Reason: {reason}\n\n"
                    "Destructive commands cannot be added to the allowlist.",
                )
                return

            try:
                self.allowlist.add(cmd_clean, description="Manually added by user")
                self.refresh_table()
                self.status_label.setText(f"Added to allowlist: {cmd_clean}")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
