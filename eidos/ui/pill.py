"""PyQt6 Floating Status Pill widget for desktop state monitoring and expandable chat interaction."""

import json
import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from eidos.ui.chat import ChatHistoryWidget

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

RESIZE_CURSORS = {
    "left": Qt.CursorShape.SizeHorCursor,
    "right": Qt.CursorShape.SizeHorCursor,
    "bottom": Qt.CursorShape.SizeVerCursor,
    "bottom_left": Qt.CursorShape.SizeBDiagCursor,
    "bottom_right": Qt.CursorShape.SizeFDiagCursor,
}

PROMPT_STYLE = (
    "QPlainTextEdit { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; "
    "border-radius: 6px; padding: 6px 10px; font-size: 12px; } "
    "QPlainTextEdit:focus { border: 1px solid #bd93f9; }"
)


class AutoGrowPromptEdit(QPlainTextEdit):
    """Multi-line prompt that grows with content (same behavior as web ChatView textarea)."""

    submit_requested = pyqtSignal()
    height_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._chat_expanded = False
        self._adjusting = False
        self._pending_adjust = False
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self.setStyleSheet(PROMPT_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(2)
        self.document().contentsChanged.connect(self._adjust_height)
        self._apply_height(self._min_height())

    def set_chat_expanded(self, expanded: bool) -> None:
        self._chat_expanded = expanded
        self._adjust_height()

    def _min_height(self) -> int:
        return 40

    def _max_height(self) -> int:
        return 320 if self._chat_expanded else 180

    def _apply_height(self, height: int) -> None:
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.updateGeometry()

    def _content_pixel_height(self) -> int:
        """Pixel height of the laid-out text. document().size() is not pixels here."""
        doc = self.document()
        layout = doc.documentLayout()
        width = self.viewport().width()
        if self.verticalScrollBar().isVisible():
            width += self.verticalScrollBar().width()
        width = max(1, width)
        if int(doc.textWidth()) != width:
            doc.setTextWidth(width)
        content = 0.0
        block = doc.begin()
        while block.isValid():
            content += layout.blockBoundingRect(block).height()
            block = block.next()
        margins = self.contentsMargins()
        chrome = margins.top() + margins.bottom() + int(doc.documentMargin() * 2) + 8
        return int(content + chrome)

    def _adjust_height(self) -> None:
        if self._adjusting:
            # setTextWidth emits contentsChanged before the new layout can be measured.
            self._pending_adjust = True
            return
        self._adjusting = True
        try:
            for _ in range(3):
                self._pending_adjust = False
                target = max(self._min_height(), min(self._content_pixel_height(), self._max_height()))
                if target != self.maximumHeight() or target != self.minimumHeight():
                    self._apply_height(target)
                    self.height_changed.emit()
                if not self._pending_adjust:
                    break
        finally:
            self._adjusting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._adjust_height()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)

    def clear(self) -> None:
        super().clear()
        self._apply_height(self._min_height())
        self.height_changed.emit()


class ElidedLabel(QLabel):
    """QLabel that elides text when space is constrained and avoids expanding parent layouts."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._full_text: str = text

    def setText(self, text: str):
        self._full_text = text or ""
        super().setText(self._full_text)

    def text(self) -> str:
        return self._full_text

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(50, sh.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(1, self.width()))
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )


class StatusPill(QWidget):
    """
    Compact, draggable floating HUD widget positioned on top of the desktop.
    Displays live agent state, quick prompt input, emergency stop, allowlist manager,
    and seamlessly expands into a full rich-text Chat UI to view detailed responses.
    """

    # Signals
    signal_set_status = pyqtSignal(str, str)  # status_key, message
    signal_submit_task = pyqtSignal(str)  # user prompt
    signal_stop_task = pyqtSignal()  # stop active task / generation
    signal_retry_task = pyqtSignal(str)  # retry task prompt
    signal_replay_tts = pyqtSignal(str)  # replay TTS audio
    signal_edit_submitted = pyqtSignal(str, str)  # message_id, new_text
    signal_emergency_kill = pyqtSignal()
    signal_open_allowlist = pyqtSignal()
    signal_open_settings = pyqtSignal()
    signal_reset_kill = pyqtSignal()
    signal_quit_app = pyqtSignal()
    signal_toggle_mic = pyqtSignal()  # voice push-to-talk toggle
    signal_toggle_auto_speak = pyqtSignal(bool)  # auto-read responses on/off
    signal_toggle_expand = pyqtSignal(bool)  # is_expanded
    signal_clear_chat = pyqtSignal()
    signal_new_chat = pyqtSignal()
    signal_open_conversations = pyqtSignal()
    signal_set_conversation_title = pyqtSignal(str)
    signal_open_workspace = pyqtSignal()
    signal_open_skills = pyqtSignal()
    signal_link_clicked = pyqtSignal(str)  # activated in-chat link/file

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = QPoint()
        self._dragging: bool = False
        self._resizing: bool = False
        self._resize_edge: Optional[str] = None
        self._resize_start_pos = QPoint()
        self._resize_start_geom = QRect()
        self._current_state = "idle"
        self.is_expanded: bool = False

        self._collapsed_width: int = 0
        self._collapsed_height: int = 0
        self._expanded_width: int = 540
        self._expanded_height: int = 600
        self._collapsed_pos: Optional[QPoint] = None
        self._expanded_pos: Optional[QPoint] = None

        self._last_assistant_message: str = ""
        self._unread_responses: int = 0
        self._is_task_running: bool = False
        self._auto_speak: bool = True

        self._init_window()
        self._init_ui()
        self._connect_signals()
        self._setup_shortcuts()

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    def _init_ui(self):
        # Outer container frame for rounded corners & shadow look
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(0)

        self.card = QFrame()
        from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

        self.setStyleSheet(GLOBAL_TOOLTIP_STYLE)
        self.card.setStyleSheet(
            "QFrame#mainCard { background-color: #1e1f29; border: 1px solid #44475a; "
            f"border-radius: 12px; }}\n{GLOBAL_TOOLTIP_STYLE}"
        )
        self.card.setObjectName("mainCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(8)

        # 1. Top Bar: Controls, Grab Handle, Status & Expand/Collapse Toggle
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

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
        self.grab_icon.setToolTip("Click and drag to move floating HUD")
        self.grab_icon.setCursor(Qt.CursorShape.SizeAllCursor)
        top_bar.addWidget(self.grab_icon)

        # Status Indicator Label
        self.status_label = QLabel("● Ready")
        self.status_label.setStyleSheet("color: #50fa7b; font-weight: bold; font-size: 13px;")
        top_bar.addWidget(self.status_label)

        # Detailed message / active task
        self.details_label = ElidedLabel("EidosBot Idle")
        self.details_label.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        top_bar.addWidget(self.details_label, stretch=1)

        # Chat Toggle Button
        self.btn_expand = QPushButton("💬")
        self.btn_expand.setToolTip("Chat / History (Ctrl+E)")
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #6272a4; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )
        self.btn_expand.clicked.connect(self.toggle_chat)
        top_bar.addWidget(self.btn_expand)

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

        # Standard Window Controls: Minimize (−) and Close (✕)
        self.btn_collapse = QPushButton("−")
        self.btn_collapse.setToolTip("Minimize (Esc)")
        self.btn_collapse.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 6px; padding: 4px 9px; font-size: 14px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )
        self.btn_collapse.clicked.connect(self.collapse_chat)
        self.btn_collapse.setVisible(False)
        top_bar.addWidget(self.btn_collapse)

        # Quick Exit / Close Button (immediately to the right of Stop / Minimize)
        self.btn_close = QPushButton("✕")
        self.btn_close.setToolTip("Exit EidosBot")
        self.btn_close.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #ff5555; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #ff5555; color: white; border-color: #ff5555; }"
        )
        self.btn_close.clicked.connect(self.signal_quit_app.emit)
        top_bar.addWidget(self.btn_close)

        card_layout.addLayout(top_bar)

        # 2. Chat Container: Scrollable Message History (Hidden when collapsed)
        self.chat_container = QFrame()
        self.chat_container.setStyleSheet(
            "QFrame { background-color: #171821; border: 1px solid #282a36; border-radius: 8px; }"
        )
        chat_layout = QVBoxLayout(self.chat_container)
        chat_layout.setContentsMargins(6, 6, 6, 6)
        chat_layout.setSpacing(4)

        # Chat Navigation Toolbar: New Chat, Past Chats, and Active Conversation Title
        self.chat_header_bar = QWidget()
        self.chat_header_bar.setStyleSheet("background-color: transparent; border: none;")
        header_bar_layout = QHBoxLayout(self.chat_header_bar)
        header_bar_layout.setContentsMargins(2, 0, 2, 4)
        header_bar_layout.setSpacing(6)

        self.btn_new_chat = QPushButton("➕ New")
        self.btn_new_chat.setToolTip("Start a new conversation thread")
        self.btn_new_chat.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #50fa7b; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 3px 8px; font-size: 11px; } "
            "QPushButton:hover { background-color: #44475a; border-color: #50fa7b; }"
        )
        self.btn_new_chat.clicked.connect(self.signal_new_chat.emit)
        header_bar_layout.addWidget(self.btn_new_chat)

        self.btn_past_chats = QPushButton("📜 Past Chats")
        self.btn_past_chats.setToolTip("Browse and resume past conversations")
        self.btn_past_chats.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 3px 8px; font-size: 11px; } "
            "QPushButton:hover { background-color: #44475a; border-color: #8be9fd; }"
        )
        self.btn_past_chats.clicked.connect(self.signal_open_conversations.emit)
        header_bar_layout.addWidget(self.btn_past_chats)

        self.btn_workspace = QPushButton("📁 Files")
        self.btn_workspace.setToolTip("Open Eidos Workspace folder (~/EidosWorkspace)")
        self.btn_workspace.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f1fa8c; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 3px 8px; font-size: 11px; } "
            "QPushButton:hover { background-color: #44475a; border-color: #f1fa8c; }"
        )
        self.btn_workspace.clicked.connect(self.signal_open_workspace.emit)
        header_bar_layout.addWidget(self.btn_workspace)

        self.lbl_conv_title = QLabel("New Chat")
        self.lbl_conv_title.setStyleSheet("color: #bd93f9; font-size: 11px; font-weight: bold;")
        self.lbl_conv_title.setToolTip("Current conversation topic")
        header_bar_layout.addWidget(self.lbl_conv_title, stretch=1)

        chat_layout.addWidget(self.chat_header_bar)

        self.chat_history = ChatHistoryWidget(self.chat_container)
        chat_layout.addWidget(self.chat_history)

        self.chat_container.setVisible(False)
        card_layout.addWidget(self.chat_container, stretch=1)

        # 3. Voice bar: same Auto-Read toggle the web app shows above the composer
        self.voice_bar = QWidget()
        self.voice_bar.setStyleSheet("background-color: transparent; border: none;")
        voice_bar_layout = QHBoxLayout(self.voice_bar)
        voice_bar_layout.setContentsMargins(2, 0, 2, 0)
        voice_bar_layout.setSpacing(8)

        self.btn_auto_speak = QPushButton()
        self.btn_auto_speak.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_speak.clicked.connect(self._on_auto_speak_clicked)
        voice_bar_layout.addWidget(self.btn_auto_speak)
        voice_bar_layout.addStretch(1)
        self.set_auto_speak(True)
        card_layout.addWidget(self.voice_bar)

        # 4. Prompt Input Bar: Quick prompt entry, mic push-to-talk, send button
        prompt_bar = QHBoxLayout()
        prompt_bar.setSpacing(6)

        self.prompt_input = AutoGrowPromptEdit()
        self.prompt_input.setPlaceholderText(
            "Ask EidosBot to do something... (Shift+Enter for newline)"
        )
        self.prompt_input.submit_requested.connect(self._on_submit_prompt)
        self.prompt_input.height_changed.connect(self._on_prompt_height_changed)
        prompt_bar.addWidget(self.prompt_input, stretch=1, alignment=Qt.AlignmentFlag.AlignBottom)

        # Push-to-Talk Mic Button
        self.btn_mic = QPushButton("🎙️")
        self.btn_mic.setToolTip(
            "Push-to-Talk (click to start/stop). When you stop, speech is transcribed and sent as a chat turn."
        )
        self.btn_mic.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 6px; padding: 6px 10px; font-size: 13px; } "
            "QPushButton:hover { background-color: #44475a; border-color: #50fa7b; }"
        )
        self.btn_mic.clicked.connect(self.signal_toggle_mic.emit)
        prompt_bar.addWidget(self.btn_mic, alignment=Qt.AlignmentFlag.AlignBottom)

        self.btn_send = QPushButton("▶")
        self.btn_send.setToolTip("Execute prompt (Enter; Shift+Enter for newline)")
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #bd93f9; color: #282a36; font-weight: bold; "
            "border-radius: 6px; padding: 6px 12px; font-size: 12px; border: none; } "
            "QPushButton:hover { background-color: #ff79c6; }"
        )
        self.btn_send.clicked.connect(self._on_submit_prompt)
        prompt_bar.addWidget(self.btn_send, alignment=Qt.AlignmentFlag.AlignBottom)

        card_layout.addLayout(prompt_bar)

        # 5. Bottom Footer Bar: Hint + Resize Grip (Resize grip only visible when expanded)
        self.bottom_bar = QHBoxLayout()
        self.bottom_bar.setContentsMargins(0, 0, 0, 0)

        self.lbl_hint = QLabel("💡 Press Esc or Ctrl+E to minimize")
        self.lbl_hint.setStyleSheet("color: #6272a4; font-size: 10px;")
        self.lbl_hint.setVisible(False)
        self.bottom_bar.addWidget(self.lbl_hint, stretch=1)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("QSizeGrip { width: 14px; height: 14px; }")
        self.size_grip.setToolTip("Drag to resize window")
        self.size_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.size_grip.setVisible(False)
        self.bottom_bar.addWidget(self.size_grip, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        card_layout.addLayout(self.bottom_bar)

        main_layout.addWidget(self.card)

        # Enable mouse tracking and event filtering for frameless edge resizing
        self.setMouseTracking(True)
        self.card.setMouseTracking(True)
        self.card.installEventFilter(self)
        self.size_grip.installEventFilter(self)

        # Synchronize layout to calculate initial minimal dimensions
        self.card.layout().activate()
        self.layout().activate()
        self._collapsed_width = self.minimumSizeHint().width()
        self._collapsed_height = self.minimumSizeHint().height()

        # Initial positioning (top-right of screen with 40px margin)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.move(geom.width() - self._collapsed_width - 40, 40)
        self.resize(self._collapsed_width, self._collapsed_height)

    def _connect_signals(self):
        self.signal_set_status.connect(self.set_status)
        self.signal_set_conversation_title.connect(self.set_conversation_title)
        self.chat_history.signal_retry.connect(self._on_retry_task)
        self.chat_history.signal_edit_message.connect(self._on_edit_message)
        self.chat_history.signal_replay_tts.connect(self.signal_replay_tts.emit)
        self.chat_history.signal_copy_to_input.connect(self.set_prompt_text)
        self.chat_history.signal_link_clicked.connect(self._on_link_clicked)

    def _setup_shortcuts(self):
        # Ctrl+E to toggle expand/collapse
        self.shortcut_toggle = QShortcut(QKeySequence("Ctrl+E"), self)
        self.shortcut_toggle.activated.connect(self.toggle_chat)

        # Esc to stop active task if running, or quickly collapse chat
        self.shortcut_esc = QShortcut(QKeySequence("Escape"), self)
        self.shortcut_esc.activated.connect(self._on_escape_pressed)

    @pyqtSlot()
    def _on_escape_pressed(self):
        """Handle Escape key: stop active task if running, else collapse chat if expanded."""
        if self._is_task_running:
            self.signal_stop_task.emit()
            return
        if self.is_expanded:
            self.collapse_chat()

    @pyqtSlot()
    def toggle_chat(self):
        """Toggle between compact pill and expanded chat interface."""
        if self.is_expanded:
            self.collapse_chat()
        else:
            self.expand_chat()

    @pyqtSlot()
    def expand_chat(self):
        """Expand floating widget into full chat interface."""
        if self.is_expanded:
            return

        # Save collapsed dimensions and position before expanding
        self._collapsed_width = self.width() if self.width() > 0 else self.minimumSizeHint().width()
        self._collapsed_height = self.height() if self.height() > 0 else self.minimumSizeHint().height()
        self._collapsed_pos = self.pos()

        self.is_expanded = True
        self._unread_responses = 0

        self.btn_expand.setText("💬")
        self.btn_expand.setToolTip("Chat / History (Ctrl+E)")
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #44475a; color: #50fa7b; font-weight: bold; "
            "border: 1px solid #50fa7b; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #6272a4; }"
        )

        screen = self.screen() or QApplication.primaryScreen()
        target_w = max(420, self._expanded_width)
        target_h = max(350, self._expanded_height)
        target_x = self.x()
        target_y = self.y()

        if screen:
            avail = screen.availableGeometry()
            if target_x + target_w > avail.right():
                target_x = max(avail.left(), avail.right() - target_w)
            if target_y + target_h > avail.bottom():
                target_y = max(avail.top(), avail.bottom() - target_h)

        self._expanded_pos = QPoint(target_x, target_y)

        # Atomic layout transition: freeze visual updates during layout changes
        self.setUpdatesEnabled(False)
        try:
            self.btn_collapse.setVisible(True)
            self.details_label.setVisible(False)
            self.chat_container.setVisible(True)
            self.lbl_hint.setVisible(True)
            self.size_grip.setVisible(True)

            self.setGeometry(target_x, target_y, target_w, target_h)
            self.card.layout().activate()
            self.layout().activate()
        finally:
            self.setUpdatesEnabled(True)

        self.prompt_input.set_chat_expanded(True)
        QTimer.singleShot(40, self.chat_history.scroll_to_bottom)
        self.prompt_input.setFocus()
        self.signal_toggle_expand.emit(True)

    @pyqtSlot()
    def collapse_chat(self):
        """Collapse widget back to compact floating status pill."""
        if not self.is_expanded:
            return

        # Save current user-resized geometry for next expansion
        self._expanded_width = max(420, self.width())
        self._expanded_height = max(350, self.height())

        self.is_expanded = False
        self.btn_expand.setText("💬")
        self.btn_expand.setToolTip("Chat / History (Ctrl+E)")
        self._reset_expand_button_style()

        # Atomic layout transition: freeze visual updates during layout changes
        self.setUpdatesEnabled(False)
        try:
            self.btn_collapse.setVisible(False)
            self.details_label.setVisible(True)
            self.chat_container.setVisible(False)
            self.lbl_hint.setVisible(False)
            self.size_grip.setVisible(False)

            self.prompt_input.set_chat_expanded(False)
            self.card.layout().activate()
            self.layout().activate()

            # Restore pre-expansion position if user didn't drag the expanded window
            if self._collapsed_pos is not None and self._expanded_pos is not None:
                if self.pos() == self._expanded_pos:
                    self.move(self._collapsed_pos)

            # Shrink back down to compact pill minimal size
            target_w = self._collapsed_width if self._collapsed_width > 0 else self.minimumSizeHint().width()
            target_h = self._collapsed_height if self._collapsed_height > 0 else self.minimumSizeHint().height()
            self.resize(target_w, target_h)
        finally:
            self.setUpdatesEnabled(True)

        self.unsetCursor()
        self.signal_toggle_expand.emit(False)

    def _reset_expand_button_style(self):
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #6272a4; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )

    # Chat Message Slots
    @pyqtSlot(str)
    def add_user_message(self, text: str, message_id: Optional[str] = None):
        """Append user message bubble to chat."""
        if not text:
            return
        self.chat_history.add_message("user", text, message_id=message_id)
        if not self.is_expanded:
            self.expand_chat()

    @pyqtSlot(str)
    def add_assistant_message(self, text: str, message_id: Optional[str] = None):
        """Append assistant response bubble with markdown/code formatting."""
        if not text:
            return
        cleaned = text.strip()
        if self._last_assistant_message == cleaned:
            return
        self._last_assistant_message = cleaned
        self.chat_history.add_message("assistant", text, message_id=message_id)

        # If collapsed, notify user on expand button
        if not self.is_expanded:
            self._unread_responses += 1
            self.btn_expand.setText(f"💬 ({self._unread_responses})")
            self.btn_expand.setStyleSheet(
                "QPushButton { background-color: #bd93f9; color: #282a36; font-weight: bold; "
                "border: none; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
                "QPushButton:hover { background-color: #ff79c6; }"
            )

    @pyqtSlot(str)
    def add_thought_message(self, thought: str):
        """Append reasoning / thinking note to chat."""
        if not thought or not thought.strip():
            return
        self.chat_history.add_message("thought", thought.strip())

    @pyqtSlot(str, dict)
    def add_tool_start_message(self, tool_name: str, args: dict):
        """Append tool dispatch event to chat."""
        args_str = json.dumps(args, indent=2) if args else "{}"
        self.chat_history.add_message("tool", f"Calling:\n{args_str}", title=tool_name)

    @pyqtSlot(str, dict)
    def add_tool_end_message(self, tool_name: str, result: dict):
        """Append tool result event to chat."""
        res_str = json.dumps(result, indent=2) if result else "{}"
        if len(res_str) > 500:
            res_str = res_str[:497] + "..."
        self.chat_history.add_message("tool", f"Output:\n{res_str}", title=f"{tool_name} (Completed)")

    @pyqtSlot(str)
    def add_error_message(self, error_text: str):
        """Append system error event to chat."""
        self.chat_history.add_message("error", error_text)

    @pyqtSlot()
    def clear_chat(self):
        """Clear conversation history both in UI and underlying loop."""
        self._last_assistant_message = ""
        self._unread_responses = 0
        self.chat_history.clear_messages()
        self.signal_clear_chat.emit()

    @pyqtSlot(str)
    def set_conversation_title(self, title: str):
        """Update displayed conversation title in chat header bar."""
        clean = (title or "New Chat").strip()
        display = (clean[:34] + "...") if len(clean) > 37 else clean
        self.lbl_conv_title.setText(display)
        self.lbl_conv_title.setToolTip(clean)

    def load_conversation_messages(
        self,
        conv_id: str,
        title: str,
        messages: List[Dict[str, Any]],
    ):
        """Populate chat view with past messages from a persistent conversation."""
        self._last_assistant_message = ""
        self._unread_responses = 0
        self.set_conversation_title(title)
        self.chat_history.clear_messages()
        if not messages:
            return

        for msg in messages:
            role = (msg.get("role") or "assistant").lower()
            if role == "system":
                continue
            content = msg.get("content") or ""
            meta = msg.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}
            msg_title = meta.get("title") if isinstance(meta.get("title"), str) else None
            msg_id = msg.get("id") or ""

            thoughts = meta.get("thoughts") if isinstance(meta.get("thoughts"), list) else []
            actions = meta.get("actions") if isinstance(meta.get("actions"), list) else []
            if role == "assistant" and (thoughts or actions):
                self._append_saved_attempt(thoughts, actions, content, meta, msg_id)
                continue

            self.chat_history.add_message(role=role, content=content, title=msg_title, message_id=msg_id)

        self.chat_history.scroll_to_bottom()

    def _append_saved_attempt(
        self,
        thoughts: List[Any],
        actions: List[Any],
        content: str,
        meta: Dict[str, Any],
        message_id: str,
    ) -> None:
        """Replay a stored turn as reasoning and action bubbles, then a short closing line.

        The database keeps one assistant row so the model still sees the attempt.
        The chat shows the same reasoning and actions that were on screen when it ran.
        """
        for thought in thoughts:
            text = str(thought or "").strip()
            if text:
                self.chat_history.add_message("thought", text, message_id=message_id)

        for action in actions:
            if not isinstance(action, dict):
                continue
            tool_name = str(action.get("tool") or "action")
            arguments = str(action.get("arguments") or "")
            result = str(action.get("result") or "")
            body = f"Calling:\n{arguments}" if arguments else "Calling:\n{}"
            if result:
                body += f"\n\nOutput:\n{result}"
            self.chat_history.add_message("tool", body, title=tool_name, message_id=message_id)

        headline = str(meta.get("headline") or "").strip()
        outcome = str(meta.get("outcome") or "success").lower()
        trace_in_body = "Actions already taken:" in content or "\nReasoning:" in content
        visible = headline if headline and trace_in_body else (content or headline)
        if not visible:
            return
        closing_role = "error" if outcome not in ("success", "") else "assistant"
        self.chat_history.add_message(closing_role, visible, message_id=message_id)

    @pyqtSlot(bool)
    def set_task_running(self, running: bool):
        """Update prompt bar and send/stop button when task starts or finishes."""
        self._is_task_running = running
        if running:
            self.btn_send.setText("⏹")
            self.btn_send.setToolTip("Stop active task / Cancel (Esc)")
            self.btn_send.setStyleSheet(
                "QPushButton { background-color: #ff5555; color: #ffffff; font-weight: bold; "
                "border-radius: 6px; padding: 6px 12px; font-size: 12px; border: none; } "
                "QPushButton:hover { background-color: #ff3333; }"
            )
        else:
            self.btn_send.setText("▶")
            self.btn_send.setToolTip("Execute prompt (Enter)")
            self.btn_send.setStyleSheet(
                "QPushButton { background-color: #bd93f9; color: #282a36; font-weight: bold; "
                "border-radius: 6px; padding: 6px 12px; font-size: 12px; border: none; } "
                "QPushButton:hover { background-color: #ff79c6; }"
            )

    @pyqtSlot(str)
    def _on_retry_task(self, prompt: str):
        """Handle retry request from chat message bubble."""
        if not prompt or not prompt.strip():
            return
        if self._is_task_running:
            self.signal_stop_task.emit()
        if not self.is_expanded:
            self.expand_chat()
        self.signal_retry_task.emit(prompt.strip())
        self.signal_submit_task.emit(prompt.strip())

    @pyqtSlot(str, str)
    def _on_edit_message(self, message_id: str, new_text: str):
        """Handle edited message submission."""
        clean = (new_text or "").strip()
        if not clean:
            return
        if self._is_task_running:
            self.signal_stop_task.emit()
        if not self.is_expanded:
            self.expand_chat()
        self.signal_edit_submitted.emit(message_id, clean)
        self.signal_submit_task.emit(clean)

    @pyqtSlot(str)
    def _on_link_clicked(self, link: str):
        """Forward link activation signal."""
        self.signal_link_clicked.emit(link)

    @pyqtSlot(bool)
    def set_auto_speak(self, enabled: bool):
        """Show the same Auto-Read on/off state the web app uses."""
        self._auto_speak = bool(enabled)
        if self._auto_speak:
            self.btn_auto_speak.setText("🔊 Auto-Read: ON")
            self.btn_auto_speak.setToolTip(
                "Automatically read assistant responses aloud via Kokoro TTS. Click to turn audio off."
            )
            self.btn_auto_speak.setStyleSheet(
                "QPushButton { background-color: transparent; color: #50fa7b; font-weight: 600; "
                "border: none; padding: 2px 4px; font-size: 11px; text-align: left; } "
                "QPushButton:hover { color: #f8f8f2; }"
            )
        else:
            self.btn_auto_speak.setText("🔇 Auto-Read: OFF")
            self.btn_auto_speak.setToolTip(
                "Response audio is off. Click to automatically read assistant responses aloud."
            )
            self.btn_auto_speak.setStyleSheet(
                "QPushButton { background-color: transparent; color: #6272a4; font-weight: 500; "
                "border: none; padding: 2px 4px; font-size: 11px; text-align: left; } "
                "QPushButton:hover { color: #f8f8f2; }"
            )

    @pyqtSlot()
    def _on_auto_speak_clicked(self):
        """Toggle auto-read and notify the controller."""
        self.set_auto_speak(not self._auto_speak)
        self.signal_toggle_auto_speak.emit(self._auto_speak)

    @pyqtSlot(bool)
    def set_mic_recording(self, recording: bool):
        """Update mic button state during push-to-talk recording."""
        if recording:
            self.btn_mic.setText("🔴")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #ff5555; color: white; "
                "border: 1px solid #ff5555; border-radius: 6px; padding: 6px 10px; font-size: 13px; font-weight: bold; } "
                "QPushButton:hover { background-color: #ff3333; }"
            )
            self.prompt_input.setPlaceholderText("Listening... (Speak now, click 🔴 when done)")
        else:
            self.btn_mic.setText("🎙️")
            self.btn_mic.setStyleSheet(
                "QPushButton { background-color: #282a36; color: #f8f8f2; "
                "border: 1px solid #44475a; border-radius: 6px; padding: 6px 10px; font-size: 13px; } "
                "QPushButton:hover { background-color: #44475a; border-color: #50fa7b; }"
            )
            self.prompt_input.setPlaceholderText("Ask EidosBot to do something...")

    @pyqtSlot(str)
    def set_prompt_text(self, text: str):
        """Set text in the prompt input field."""
        self.prompt_input.setPlainText(text)
        self.prompt_input.setFocus()

    @pyqtSlot()
    def clear_prompt(self):
        """Clear the prompt input without submitting a task."""
        self.prompt_input.clear()

    @pyqtSlot(str, str)
    def set_status(self, status_key: str, message: str = ""):
        """Update the status pill display."""
        self._current_state = status_key.lower()
        color_hex, label_text = STATUS_COLORS.get(self._current_state, ("#50fa7b", f"● {status_key}"))

        self.status_label.setText(label_text)
        self.status_label.setStyleSheet(f"color: {color_hex}; font-weight: bold; font-size: 13px;")

        if message:
            # Truncate details if overly long for pill display
            display_msg = message if len(message) <= 50 else message[:47] + "..."
            self.details_label.setText(display_msg)
            self.details_label.setToolTip(message)
        else:
            self.details_label.setToolTip("")

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
        if self.is_expanded:
            action_expand = menu.addAction("−  Minimize Chat (Esc or Ctrl+E)")
        else:
            action_expand = menu.addAction("💬  Open Chat UI (Ctrl+E)")

        action_new = menu.addAction("➕  New Chat")
        action_past = menu.addAction("📜  Past Conversations...")
        menu.addSeparator()
        action_workspace = menu.addAction("📂  Open Workspace (~/EidosWorkspace)...")
        action_skills = menu.addAction("🧠  Open Skills Directory (~/.eidos/skills)...")
        menu.addSeparator()
        action_settings = menu.addAction("⚙️  Settings (Models & API Keys)...")
        action_allowlist = menu.addAction("🛡️  Shell Allowlist Manager...")
        menu.addSeparator()
        if self._current_state == "killed":
            action_kill = menu.addAction("🔄  Reset Emergency Stop")
        else:
            action_kill = menu.addAction("🛑  Emergency Stop (Ctrl+Alt+Esc)")
        menu.addSeparator()
        action_quit = menu.addAction("❌  Quit EidosBot")

        action = menu.exec(self.btn_menu.mapToGlobal(QPoint(0, self.btn_menu.height() + 4)))
        if action == action_expand:
            self.toggle_chat()
        elif action == action_new:
            self.signal_new_chat.emit()
        elif action == action_past:
            self.signal_open_conversations.emit()
        elif action == action_workspace:
            self.signal_open_workspace.emit()
        elif action == action_skills:
            self.signal_open_skills.emit()
        elif action == action_settings:
            self.signal_open_settings.emit()
        elif action == action_allowlist:
            self.signal_open_allowlist.emit()
        elif action == action_kill:
            self._on_kill_or_reset()
        elif action == action_quit:
            self.signal_quit_app.emit()

    def _on_kill_or_reset(self):
        if self._current_state == "killed":
            self.signal_reset_kill.emit()
            self.set_status("idle", "Kill switch reset")
        else:
            self.signal_emergency_kill.emit()

    def _on_submit_prompt(self):
        if self._is_task_running:
            # Clicking stop button cancels active task
            self.signal_stop_task.emit()
            return

        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            return

        # Auto-expand to show chat response if currently collapsed
        if not self.is_expanded:
            self.expand_chat()

        self.signal_submit_task.emit(prompt)

    def _on_prompt_height_changed(self) -> None:
        """Keep the prompt row and compact pill height in sync with wrapped text."""
        self.card.layout().activate()
        self.layout().activate()
        if self.is_expanded:
            return
        self.resize(self.width(), self.minimumSizeHint().height())

    # Window moving and frameless border/corner resizing
    def _get_resize_edge(self, global_pos: QPoint) -> Optional[str]:
        """Detect if cursor is over a resize border or corner in expanded mode."""
        if not self.is_expanded:
            return None

        pt = self.mapFromGlobal(global_pos)
        w, h = self.width(), self.height()
        x, y = pt.x(), pt.y()

        if x < -5 or y < -5 or x > w + 5 or y > h + 5:
            return None

        # Corners have a generous hit area (24px) for easy grabbing
        corner = 24
        border = 8

        if x >= w - corner and y >= h - corner:
            return "bottom_right"
        if x <= corner and y >= h - corner:
            return "bottom_left"

        if x <= border:
            return "left"
        if x >= w - border:
            return "right"
        if y >= h - border:
            return "bottom"

        return None

    def _is_draggable_area(self, global_pos: QPoint) -> bool:
        """Determine if a mouse click is in an allowed window dragging zone."""
        child = QApplication.widgetAt(global_pos)
        # Interactive controls are never draggable
        if isinstance(child, (QPushButton, QPlainTextEdit, QSizeGrip)):
            return False

        # In compact pill mode, the whole widget (outside buttons/input) moves the window
        if not self.is_expanded:
            return True

        # When expanded, allow moving from top_bar or chat_header_bar
        pt = self.card.mapFromGlobal(global_pos)
        if pt.y() <= 46:
            return True
        if self.chat_header_bar.isVisible():
            header_rect = QRect(
                self.chat_header_bar.mapToGlobal(QPoint(0, 0)),
                self.chat_header_bar.size(),
            )
            if header_rect.contains(global_pos):
                return True
        return False

    def _perform_resize(self, global_pos: QPoint):
        """Execute frameless resizing based on active edge and mouse delta."""
        edge = self._resize_edge
        if not edge:
            return

        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()
        start = self._resize_start_geom
        min_w = max(380, self.minimumSizeHint().width())
        min_h = max(250, self.minimumSizeHint().height())

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        max_w = avail.width()
        max_h = avail.height()

        new_x = start.x()
        new_y = start.y()
        new_w = start.width()
        new_h = start.height()

        if edge in ("right", "bottom_right"):
            new_w = max(min_w, min(max_w, start.width() + dx))
            if new_x + new_w > avail.right():
                new_w = avail.right() - new_x
        elif edge in ("left", "bottom_left"):
            new_w = max(min_w, min(max_w, start.width() - dx))
            new_x = start.right() - new_w + 1
            if new_x < avail.left():
                new_x = avail.left()
                new_w = start.right() - new_x + 1

        if edge in ("bottom", "bottom_right", "bottom_left"):
            new_h = max(min_h, min(max_h, start.height() + dy))
            if new_y + new_h > avail.bottom():
                new_h = avail.bottom() - new_y

        self.setGeometry(new_x, new_y, new_w, new_h)
        self._expanded_width = new_w
        self._expanded_height = new_h

    def _handle_mouse_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        global_pos = event.globalPosition().toPoint()
        edge = self._get_resize_edge(global_pos)
        if edge:
            self._resizing = True
            self._resize_edge = edge
            self._resize_start_pos = global_pos
            self._resize_start_geom = self.geometry()
            return True

        if self._is_draggable_area(global_pos):
            self._dragging = True
            self._drag_pos = global_pos - self.frameGeometry().topLeft()
            return True

        return False

    def _handle_mouse_move(self, event) -> bool:
        global_pos = event.globalPosition().toPoint()

        if self._resizing and self._resize_edge:
            self._perform_resize(global_pos)
            return True

        if self._dragging:
            self.move(global_pos - self._drag_pos)
            return True

        # Update hover cursor when mouse is not pressed
        if self.is_expanded and not event.buttons():
            edge = self._get_resize_edge(global_pos)
            if edge and edge in RESIZE_CURSORS:
                self.setCursor(RESIZE_CURSORS[edge])
            else:
                self.unsetCursor()

        return False

    def _handle_mouse_release(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        handled = False
        if self._resizing:
            self._resizing = False
            self._resize_edge = None
            self._expanded_width = max(380, self.width())
            self._expanded_height = max(250, self.height())
            self.unsetCursor()
            handled = True

        if self._dragging:
            self._dragging = False
            handled = True

        return handled

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.MouseMove:
            if self._handle_mouse_move(event):
                return True
        elif t == QEvent.Type.MouseButtonPress:
            if self._handle_mouse_press(event):
                return True
        elif t == QEvent.Type.MouseButtonRelease:
            if self._handle_mouse_release(event):
                return True
        elif t == QEvent.Type.Leave:
            if not self._resizing:
                self.unsetCursor()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if not self._handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._handle_mouse_release(event):
            super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._resizing:
            self.unsetCursor()
        super().leaveEvent(event)
