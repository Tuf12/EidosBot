"""PyQt6 Past Conversations Manager GUI for browsing, opening, and managing chat history."""

from datetime import datetime
import html
import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
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
    QWidget,
)

from eidos.memory.conversation_store import ConversationStore

logger = logging.getLogger(__name__)


class ConversationsDialog(QDialog):
    """
    GUI window for browsing, resuming, searching, and managing past chat conversations.
    """

    signal_conversation_selected = pyqtSignal(str)
    signal_new_conversation = pyqtSignal()
    signal_conversation_deleted = pyqtSignal(str)

    def __init__(
        self,
        conversation_store: ConversationStore,
        active_conversation_id: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = conversation_store
        self.active_conversation_id = active_conversation_id
        self._conversations_cache = []
        self._init_ui()
        self.refresh_table()

    def _init_ui(self):
        self.setWindowTitle("EidosBot - Past Conversations")
        self.setMinimumSize(840, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header Title and Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title = QLabel("📜 <b>Past Conversations</b>")
        title.setStyleSheet("font-size: 16px; color: #bd93f9;")
        subtitle = QLabel(
            "Browse, resume, search, or delete past conversation threads. "
            "Double-click any row or click <b>Open</b> to load the chat into the HUD."
        )
        subtitle.setStyleSheet("color: #6272a4; font-size: 12px;")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)

        # Top Bar: Search input + New Chat button
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        search_lbl = QLabel("Search:")
        search_lbl.setStyleSheet("color: #8be9fd; font-weight: bold; font-size: 12px;")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter conversations by topic or message keyword...")
        self.search_input.setStyleSheet(
            "background-color: #1e1f29; color: #f8f8f2; border: 1px solid #44475a; "
            "border-radius: 6px; padding: 6px 10px; font-size: 12px;"
        )
        self.search_input.textChanged.connect(self._on_search_changed)

        self.btn_new = QPushButton("➕ New Chat")
        self.btn_new.setToolTip("Start a clean conversation thread")
        self.btn_new.setStyleSheet(
            "QPushButton { background-color: #50fa7b; color: #282a36; font-weight: bold; "
            "padding: 6px 14px; border-radius: 6px; font-size: 12px; border: none; } "
            "QPushButton:hover { background-color: #38ef7d; }"
        )
        self.btn_new.clicked.connect(self._on_click_new)

        top_bar.addWidget(search_lbl)
        top_bar.addWidget(self.search_input, stretch=1)
        top_bar.addWidget(self.btn_new)
        layout.addLayout(top_bar)

        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Title", "Messages", "Last Active", "Preview", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 130)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setStyleSheet(
            "QTableWidget { background-color: #21222c; alternate-background-color: #282a36; "
            "color: #f8f8f2; gridline-color: #44475a; border: 1px solid #44475a; border-radius: 6px; } "
            "QHeaderView::section { background-color: #191a21; color: #8be9fd; font-weight: bold; "
            "padding: 6px; border: 1px solid #44475a; }"
        )
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table)

        # Bottom Bar: Summary + Close
        bottom_bar = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #6272a4; font-size: 11px;")
        bottom_bar.addWidget(self.lbl_status, stretch=1)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; "
            "padding: 6px 16px; border-radius: 6px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; }"
        )
        self.btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(self.btn_close)
        layout.addLayout(bottom_bar)

        from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

        self.setStyleSheet(
            f"QDialog {{ background-color: #1e1f29; color: #f8f8f2; }}\n{GLOBAL_TOOLTIP_STYLE}"
        )

    def refresh_table(self):
        """Fetch conversations from SQLite and populate the table."""
        search = self.search_input.text().strip()
        convs = self.store.list_conversations(limit=300, search_query=search)
        self._conversations_cache = convs

        self.table.setRowCount(len(convs))
        for row_idx, conv in enumerate(convs):
            conv_id = conv["id"]
            is_active = (conv_id == self.active_conversation_id)

            # 0. Title
            title_text = conv.get("title", "Untitled")
            if is_active:
                title_item = QTableWidgetItem(f"★ {title_text} (Active)")
                title_item.setForeground(Qt.GlobalColor.green)
            else:
                title_item = QTableWidgetItem(title_text)
                title_item.setForeground(Qt.GlobalColor.cyan)
            title_item.setData(Qt.ItemDataRole.UserRole, conv_id)
            self.table.setItem(row_idx, 0, title_item)

            # 1. Message Count
            msg_count = conv.get("message_count", 0)
            count_item = QTableWidgetItem(f"{msg_count} msgs")
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row_idx, 1, count_item)

            # 2. Last Active
            updated_ms = conv.get("updatedAt", conv.get("createdAt", 0))
            if updated_ms:
                try:
                    dt = datetime.fromtimestamp(updated_ms / 1000.0)
                    date_str = dt.strftime("%b %d, %Y %H:%M")
                except Exception:
                    date_str = "Unknown"
            else:
                date_str = "Unknown"
            date_item = QTableWidgetItem(date_str)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row_idx, 2, date_item)

            # 3. Preview
            preview = conv.get("last_preview") or ""
            # Strip newlines for preview
            preview_clean = " ".join(preview.splitlines()).strip()
            if len(preview_clean) > 80:
                preview_clean = preview_clean[:77] + "..."
            preview_item = QTableWidgetItem(preview_clean)
            preview_item.setToolTip(preview)
            self.table.setItem(row_idx, 3, preview_item)

            # 4. Action Buttons (Open, Delete)
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)

            btn_open = QPushButton("Open")
            btn_open.setToolTip("Switch to this conversation")
            btn_open.setStyleSheet(
                "QPushButton { background-color: #282a36; color: #50fa7b; font-weight: bold; "
                "border: 1px solid #44475a; border-radius: 4px; padding: 2px 6px; font-size: 11px; } "
                "QPushButton:hover { background-color: #50fa7b; color: #282a36; }"
            )
            btn_open.clicked.connect(lambda _, cid=conv_id: self._on_select_conversation(cid))
            action_layout.addWidget(btn_open)

            btn_delete = QPushButton("🗑️")
            btn_delete.setToolTip("Delete this conversation")
            btn_delete.setStyleSheet(
                "QPushButton { background-color: #282a36; border: 1px solid #44475a; "
                "border-radius: 4px; padding: 2px 4px; font-size: 11px; } "
                "QPushButton:hover { background-color: #ff5555; }"
            )
            btn_delete.clicked.connect(lambda _, cid=conv_id, t=title_text: self._on_delete_conversation(cid, t))
            action_layout.addWidget(btn_delete)

            self.table.setCellWidget(row_idx, 4, action_widget)

        self.lbl_status.setText(f"Showing {len(convs)} conversation(s).")

    def _on_search_changed(self):
        self.refresh_table()

    def _on_cell_double_clicked(self, row: int, col: int):
        title_item = self.table.item(row, 0)
        if title_item:
            conv_id = title_item.data(Qt.ItemDataRole.UserRole)
            if conv_id:
                self._on_select_conversation(conv_id)

    def _on_select_conversation(self, conversation_id: str):
        """Emit selection signal and close dialog."""
        self.active_conversation_id = conversation_id
        self.signal_conversation_selected.emit(conversation_id)
        self.accept()

    def _on_click_new(self):
        """Emit new conversation signal and close dialog."""
        self.signal_new_conversation.emit()
        self.accept()

    def _on_delete_conversation(self, conversation_id: str, title: str):
        """Confirm with Yes/No before deleting a conversation from the database."""
        display_title = title.strip() or "Untitled Chat"
        if len(display_title) > 60:
            display_title = display_title[:57] + "..."

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Conversation")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText("Are you sure you want to delete this conversation?")
        msg_box.setInformativeText(
            f"\"{display_title}\"\n\nThis will permanently remove this conversation and all associated messages."
        )
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg_box.setStyleSheet(
            "QMessageBox { background-color: #21222c; } "
            "QLabel { color: #f8f8f2; font-size: 12px; } "
            "QPushButton { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; "
            "border-radius: 4px; padding: 5px 16px; min-width: 65px; font-size: 12px; font-weight: bold; } "
            "QPushButton:hover { background-color: #44475a; } "
            "QPushButton:focus { border: 1px solid #bd93f9; }"
        )

        reply = msg_box.exec()
        if reply == QMessageBox.StandardButton.Yes:
            self.store.delete_conversation(conversation_id)
            self.signal_conversation_deleted.emit(conversation_id)
            if self.active_conversation_id == conversation_id:
                self.active_conversation_id = None
            self.refresh_table()
