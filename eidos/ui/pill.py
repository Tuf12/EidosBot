"""PyQt6 Floating Status Pill widget for desktop state monitoring and expandable chat interaction."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from eidos.ui.attachments import (
    MAX_ATTACHMENTS,
    discard_staged,
    is_image_path,
    pasted_image_name,
    pending_from_path,
    staging_path,
)

from eidos.ui.chat import ChatHistoryWidget
from eidos.ui.speech_bar import SpeechPlayerBar

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

PROMPT_PLACEHOLDER = (
    "Ask EidosBot... paste a photo or attach a file (Shift+Enter for a new line)"
)

_SIDE_BUTTON_STYLE = (
    "QPushButton { background-color: #282a36; color: #f8f8f2; "
    "border: 1px solid #44475a; border-radius: 6px; padding: 6px 10px; font-size: 13px; } "
    "QPushButton:hover { background-color: #44475a; border-color: #50fa7b; }"
)


def _local_file_paths(mime) -> List[str]:
    """Local files carried by a clipboard or drop payload."""
    if mime is None or not mime.hasUrls():
        return []
    paths: List[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if path:
            paths.append(path)
    return paths


# PyQt6 QMimeData.hasFormat/data take str. bytes used to raise TypeError inside
# insertFromMimeData, and an exception there segfaults the process.
_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp")


def _qimage_from_mime(mime) -> Optional[QImage]:
    """Image pixels from a clipboard or drop payload."""
    if mime is None:
        return None
    try:
        if mime.hasImage():
            raw = mime.imageData()
            image = raw if isinstance(raw, QImage) else QImage(raw)
            if image is not None and not image.isNull():
                return image
    except Exception:
        logger.debug("Clipboard image decode failed", exc_info=True)
    for fmt in _IMAGE_MIME_TYPES:
        try:
            if not mime.hasFormat(fmt):
                continue
            image = QImage()
            if image.loadFromData(bytes(mime.data(fmt))):
                return image
        except Exception:
            logger.debug("Clipboard format %s skipped", fmt, exc_info=True)
    return None


def _preview_pixmap(path: str, edge: int = 28) -> Optional[QPixmap]:
    """Small thumbnail for an image file. Other files return None."""
    if not is_image_path(path):
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and not size.isEmpty():
        size.scale(edge, edge, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    if image.isNull():
        return None
    return QPixmap.fromImage(image)


class AutoGrowPromptEdit(QPlainTextEdit):
    """Multi-line prompt that grows with content (same behavior as web ChatView textarea)."""

    submit_requested = pyqtSignal()
    height_changed = pyqtSignal()
    image_pasted = pyqtSignal(QImage)
    files_pasted = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._chat_expanded = False
        self._adjusting = False
        self._pending_adjust = False
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self.setAcceptDrops(True)
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
        # The box never grows past _max_height, so stop once the text is
        # already taller than that. Laying out every line of a long script
        # on the paste event freezes the window.
        limit = float(self._max_height())
        block = doc.begin()
        while block.isValid():
            content += layout.blockBoundingRect(block).height()
            if content >= limit:
                break
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

    def insertFromMimeData(self, source):
        """Paste a copied photo or copied files instead of dumping them as text.

        A raised exception here is fatal: PyQt aborts the process while it is
        still inside the paste event. Probe failures fall back to plain text.
        """
        paths: List[str] = []
        image = None
        try:
            paths = _local_file_paths(source)
            image = None if paths else _qimage_from_mime(source)
        except Exception:
            logger.exception("Paste payload could not be inspected; inserting text")
        if image is not None:
            self.image_pasted.emit(image)
            return
        if paths:
            self.files_pasted.emit(paths)
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if _local_file_paths(event.mimeData()) or _qimage_from_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        paths = _local_file_paths(mime)
        if paths:
            self.files_pasted.emit(paths)
            event.acceptProposedAction()
            return
        image = _qimage_from_mime(mime)
        if image is not None:
            self.image_pasted.emit(image)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def clear(self) -> None:
        super().clear()
        self._apply_height(self._min_height())
        self.height_changed.emit()


class AttachmentChip(QFrame):
    """One pending file or photo sitting above the message box."""

    remove_requested = pyqtSignal(str)

    def __init__(
        self,
        item_id: str,
        name: str,
        preview: Optional[QPixmap] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.item_id = item_id
        self.setStyleSheet(
            "QFrame { background-color: #282a36; border: 1px solid #44475a; border-radius: 6px; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 2, 2)
        row.setSpacing(4)
        if preview is not None and not preview.isNull():
            thumb = QLabel()
            thumb.setPixmap(preview)
            thumb.setFixedSize(preview.size())
            thumb.setStyleSheet("border: none; background: transparent;")
            row.addWidget(thumb)
        shown = name if len(name) <= 28 else name[:25] + "…"
        label = QLabel(shown)
        label.setToolTip(name)
        label.setStyleSheet("color: #f8f8f2; font-size: 11px; border: none; background: transparent;")
        row.addWidget(label)
        button = QPushButton("✕")
        button.setFixedSize(18, 18)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Remove attachment")
        button.setStyleSheet(
            "QPushButton { background: transparent; color: #6272a4; border: none; font-size: 11px; } "
            "QPushButton:hover { color: #ff5555; }"
        )
        button.clicked.connect(lambda: self.remove_requested.emit(self.item_id))
        row.addWidget(button)


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


class PillRestoreChip(QWidget):
    """Small always-on-top handle shown when the desktop has no system tray."""

    restore_requested = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("EidosBot")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._button = QPushButton("Eidos")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setStyleSheet(
            "QPushButton { background-color: #1e1f29; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 12px; padding: 4px 10px; font-size: 12px; } "
            "QPushButton:hover { border-color: #bd93f9; color: #f8f8f2; }"
        )
        self._button.clicked.connect(self.restore_requested.emit)
        layout.addWidget(self._button)
        self.set_hint("EidosBot is minimized. Click to restore.")
        self.adjustSize()

    def set_hint(self, tip: str) -> None:
        self.setToolTip(tip)
        self._button.setToolTip(tip)

    def set_unread(self, count: int) -> None:
        self._button.setText(f"Eidos ({count})" if count else "Eidos")
        self.adjustSize()


class StatusPill(QWidget):
    """
    Compact, draggable floating HUD widget positioned on top of the desktop.
    Displays live agent state, quick prompt input, emergency stop, allowlist manager,
    and seamlessly expands into a full rich-text Chat UI to view detailed responses.
    """

    # Signals
    signal_set_status = pyqtSignal(str, str)  # status_key, message
    signal_set_background_activity = pyqtSignal(bool, str, str)  # active, task, details
    signal_submit_task = pyqtSignal(str, object)  # prompt text, attachment payloads
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
    signal_playback_toggle = pyqtSignal()
    signal_playback_stop = pyqtSignal()
    signal_playback_seek = pyqtSignal(int)
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
        self._background_active: bool = False
        self._background_task: str = ""
        self._background_details: str = ""
        self._minimized: bool = False
        self._tray: Optional[QSystemTrayIcon] = None
        self._tray_menu: Optional[QMenu] = None
        self._restore_chip: Optional[PillRestoreChip] = None
        self._minimize_hint_shown: bool = False
        self._pending_attachments: List[Dict[str, Any]] = []
        self._attach_dir: str = str(Path.home())

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

        # Project Job Scope Tag Label (PROJECT_JOBS.md Phase 13.6)
        self.job_scope_label = QLabel("")
        self.job_scope_label.setStyleSheet(
            "QLabel { background-color: rgba(139, 233, 253, 0.15); color: #8be9fd; "
            "border: 1px solid rgba(139, 233, 253, 0.4); border-radius: 4px; "
            "padding: 1px 6px; font-size: 11px; font-weight: bold; }"
        )
        self.job_scope_label.setVisible(False)
        top_bar.addWidget(self.job_scope_label)

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

        # Minimize hides the whole pill. Chat toggle stays on the button to its left.
        self.btn_minimize = QPushButton("−")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.btn_minimize.setToolTip("Minimize pill. Click the tray icon to restore.")
        else:
            self.btn_minimize.setToolTip("Minimize pill. Click the small Eidos button to restore.")
        self.btn_minimize.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #44475a; border-radius: 6px; padding: 4px 9px; font-size: 14px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )
        self.btn_minimize.clicked.connect(self.minimize_pill)
        top_bar.addWidget(self.btn_minimize)

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

        # Same player as the phone: play, pause, ±5s, and stop. Stays up after the
        # read-aloud ends so Play can start over and -5s can step back.
        self.speech_bar = SpeechPlayerBar()
        self.speech_bar.toggle_requested.connect(self.signal_playback_toggle.emit)
        self.speech_bar.stop_requested.connect(self.signal_playback_stop.emit)
        self.speech_bar.seek_requested.connect(self.signal_playback_seek.emit)
        card_layout.addWidget(self.speech_bar)

        # 4. Prompt Input Bar: attachments, message box, mic, send
        composer = QVBoxLayout()
        composer.setSpacing(4)

        self.attach_notice = QLabel("")
        self.attach_notice.setWordWrap(True)
        self.attach_notice.setStyleSheet("color: #ff5555; font-size: 11px;")
        self.attach_notice.setVisible(False)
        composer.addWidget(self.attach_notice)

        self.attach_scroll = QScrollArea()
        self.attach_scroll.setWidgetResizable(True)
        self.attach_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attach_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attach_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.attach_scroll.setFixedHeight(40)
        self.attach_scroll.setVisible(False)
        self.attach_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.attach_host = QWidget()
        self.attach_host.setStyleSheet("background: transparent;")
        self.attach_row_layout = QHBoxLayout(self.attach_host)
        self.attach_row_layout.setContentsMargins(0, 0, 0, 0)
        self.attach_row_layout.setSpacing(4)
        self.attach_scroll.setWidget(self.attach_host)
        composer.addWidget(self.attach_scroll)

        prompt_bar = QHBoxLayout()
        prompt_bar.setSpacing(6)

        self.prompt_input = AutoGrowPromptEdit()
        self.prompt_input.setPlaceholderText(PROMPT_PLACEHOLDER)
        self.prompt_input.setToolTip("Paste a copied photo with Ctrl+V, or drop files here")
        self.prompt_input.submit_requested.connect(self._on_submit_prompt)
        self.prompt_input.height_changed.connect(self._on_prompt_height_changed)
        self.prompt_input.image_pasted.connect(self._add_clipboard_image)
        self.prompt_input.files_pasted.connect(self._add_file_paths)
        prompt_bar.addWidget(self.prompt_input, stretch=1, alignment=Qt.AlignmentFlag.AlignBottom)

        self.btn_attach = QPushButton("📎")
        self.btn_attach.setToolTip("Attach files or photos for Eidos to view")
        self.btn_attach.setStyleSheet(_SIDE_BUTTON_STYLE)
        self.btn_attach.clicked.connect(self._choose_attachments)
        prompt_bar.addWidget(self.btn_attach, alignment=Qt.AlignmentFlag.AlignBottom)

        # Push-to-Talk Mic Button
        self.btn_mic = QPushButton("🎙️")
        self.btn_mic.setToolTip(
            "Push-to-Talk (click to start/stop). When you stop, speech is transcribed and sent as a chat turn."
        )
        self.btn_mic.setStyleSheet(_SIDE_BUTTON_STYLE)
        self.btn_mic.clicked.connect(self.signal_toggle_mic.emit)
        prompt_bar.addWidget(self.btn_mic, alignment=Qt.AlignmentFlag.AlignBottom)

        self.btn_send = QPushButton("▶")
        self.btn_send.setToolTip("Send the message and any attached files (Enter; Shift+Enter for a new line)")
        self.btn_send.setStyleSheet(
            "QPushButton { background-color: #bd93f9; color: #282a36; font-weight: bold; "
            "border-radius: 6px; padding: 6px 12px; font-size: 12px; border: none; } "
            "QPushButton:hover { background-color: #ff79c6; }"
        )
        self.btn_send.clicked.connect(self._on_submit_prompt)
        prompt_bar.addWidget(self.btn_send, alignment=Qt.AlignmentFlag.AlignBottom)
        composer.addLayout(prompt_bar)

        card_layout.addLayout(composer)
        self.setAcceptDrops(True)

        # 5. Bottom Footer Bar: Hint + Resize Grip (Resize grip only visible when expanded)
        self.bottom_bar = QHBoxLayout()
        self.bottom_bar.setContentsMargins(0, 0, 0, 0)

        self.lbl_hint = QLabel("💡 Press Esc or Ctrl+E to close chat")
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
        self.signal_set_background_activity.connect(self.set_background_activity)
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

    def _apply_layout_while_hidden(self, apply_fn) -> None:
        """Hide during resize so Linux compositors do not leave stale pill frames."""
        visible = self.isVisible()
        if visible:
            self.hide()
        self.setUpdatesEnabled(False)
        try:
            apply_fn()
        finally:
            self.setUpdatesEnabled(True)
            if visible:
                self.show()
                self.raise_()

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

        def _apply_expand() -> None:
            self.details_label.setVisible(False)
            self.chat_container.setVisible(True)
            self.lbl_hint.setVisible(True)
            self.size_grip.setVisible(True)
            self.setGeometry(target_x, target_y, target_w, target_h)
            self.card.layout().activate()
            self.layout().activate()

        self._apply_layout_while_hidden(_apply_expand)

        self.prompt_input.set_chat_expanded(True)
        self.chat_history._fit_container_width()
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

        target_w = self._collapsed_width if self._collapsed_width > 0 else self.minimumSizeHint().width()
        target_h = self._collapsed_height if self._collapsed_height > 0 else self.minimumSizeHint().height()
        restore_pos = self._collapsed_pos
        expanded_pos = self._expanded_pos

        def _apply_collapse() -> None:
            self.details_label.setVisible(True)
            self.chat_container.setVisible(False)
            self.lbl_hint.setVisible(False)
            self.size_grip.setVisible(False)
            self.prompt_input.set_chat_expanded(False)
            self.card.layout().activate()
            self.layout().activate()
            if restore_pos is not None and expanded_pos is not None and self.pos() == expanded_pos:
                self.move(restore_pos)
            self.resize(target_w, target_h)

        self._apply_layout_while_hidden(_apply_collapse)
        if self.speech_bar.isVisible():
            self._fit_collapsed_to_content()

        self.unsetCursor()
        self.signal_toggle_expand.emit(False)

    @pyqtSlot()
    def minimize_pill(self):
        """Hide the floating pill until the user restores it from the tray or chip."""
        if self._minimized:
            return
        self._minimized = True
        self.hide()
        self._show_minimized_affordance()

    @pyqtSlot()
    def restore_pill(self):
        """Bring the floating pill back in the same chat state it had when minimized."""
        self._minimized = False
        self._hide_minimized_affordance()
        self.show()
        self.raise_()
        self.activateWindow()

    def _minimized_tooltip(self) -> str:
        if self._unread_responses:
            return f"EidosBot — {self._unread_responses} unread. Click to restore."
        return "EidosBot is minimized. Click to restore."

    def _eidos_icon(self) -> QIcon:
        icon_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "assets", "eidos_icon.png")
        )
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            if not icon.isNull():
                return icon
        style = QApplication.style()
        if style is None:
            return QIcon()
        return style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)

    def _ensure_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        if self._tray is not None:
            return self._tray
        tray = QSystemTrayIcon(self._eidos_icon(), QApplication.instance())
        tray.setToolTip(self._minimized_tooltip())
        menu = QMenu()
        show_action = menu.addAction("Show EidosBot")
        show_action.triggered.connect(self.restore_pill)
        menu.addSeparator()
        quit_action = menu.addAction("Quit EidosBot")
        quit_action.triggered.connect(self.signal_quit_app.emit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.messageClicked.connect(self.restore_pill)
        self._tray = tray
        self._tray_menu = menu
        return tray

    def _ensure_restore_chip(self) -> PillRestoreChip:
        if self._restore_chip is None:
            chip = PillRestoreChip()
            chip.restore_requested.connect(self.restore_pill)
            self._restore_chip = chip
        return self._restore_chip

    def _position_restore_chip(self) -> None:
        chip = self._restore_chip
        if chip is None:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.right() - chip.width() - 12
        y = avail.top() + 12
        chip.move(max(avail.left(), x), max(avail.top(), y))

    def _show_minimized_affordance(self) -> None:
        tray = self._ensure_tray()
        if tray is not None:
            tray.setToolTip(self._minimized_tooltip())
            tray.show()
            if not self._minimize_hint_shown:
                self._minimize_hint_shown = True
                tray.showMessage(
                    "EidosBot",
                    "Pill minimized. Click the tray icon to restore it.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
            return
        chip = self._ensure_restore_chip()
        chip.set_unread(self._unread_responses)
        chip.set_hint(self._minimized_tooltip())
        self._position_restore_chip()
        chip.show()
        chip.raise_()
        logger.info("System tray unavailable; showing Eidos restore button")

    def _hide_minimized_affordance(self) -> None:
        if self._tray is not None:
            self._tray.hide()
        if self._restore_chip is not None:
            self._restore_chip.hide()

    def _sync_minimized_affordance(self) -> None:
        if not self._minimized:
            return
        tip = self._minimized_tooltip()
        if self._tray is not None and self._tray.isVisible():
            self._tray.setToolTip(tip)
        if self._restore_chip is not None and self._restore_chip.isVisible():
            self._restore_chip.set_unread(self._unread_responses)
            self._restore_chip.set_hint(tip)
            self._position_restore_chip()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.restore_pill()

    def _reset_expand_button_style(self):
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #8be9fd; font-weight: bold; "
            "border: 1px solid #6272a4; border-radius: 6px; padding: 4px 8px; font-size: 12px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )

    # Chat Message Slots
    @pyqtSlot(str)
    def add_user_message(self, text: str, message_id: Optional[str] = None):
        """Append one user bubble. A repeat of the bubble already at the bottom is ignored."""
        if not text:
            return
        cleaned = text.strip()
        bubbles = getattr(self.chat_history, "bubbles", None) or []
        if bubbles:
            last = bubbles[-1]
            if getattr(last, "role", "") == "user" and (getattr(last, "raw_content", "") or "").strip() == cleaned:
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
        if self._minimized:
            self._sync_minimized_affordance()

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
        if (
            tool_name == "share_file"
            and isinstance(result, dict)
            and result.get("success")
            and result.get("path")
        ):
            path = str(result.get("path"))
            self.chat_history.add_message("tool", f"Open file:\n{path}", title="File")
            return
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

    @pyqtSlot(object, object)
    def update_job_scope(
        self,
        primary: Optional[Dict[str, Any]],
        running: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Update visible project job and running worker scope tags (PROJECT_JOBS.md Section 10)."""
        if not primary and not running:
            self.job_scope_label.setVisible(False)
            self.job_scope_label.setText("")
            return

        parts: List[str] = []
        if primary and isinstance(primary, dict):
            if primary.get("integration_focus"):
                parts.append(f"Integration · {primary['integration_focus']}")
            else:
                j_name = primary.get("name") or primary.get("job_name", "Job")
                j_int = primary.get("integration")
                if j_int:
                    parts.append(f"Job · {j_name} · {j_int}")
                else:
                    parts.append(f"Job · {j_name}")

        if running and isinstance(running, list):
            for r in running:
                if isinstance(r, dict):
                    r_name = r.get("name") or r.get("job_id", "Worker")
                    done = r.get("completed_items", 0)
                    total = r.get("total_items", 0)
                    prog_str = f" {done}/{total}" if total else ""
                    parts.append(f"Running · {r_name}{prog_str}")

        tag_text = " | ".join(parts)
        self.job_scope_label.setText(tag_text)
        tooltip = ""
        if primary and isinstance(primary, dict):
            tooltip = f"Primary Job: {primary.get('name')}\nID: {primary.get('job_id')}"
            if primary.get("path"):
                tooltip += f"\nPath: {primary.get('path')}"
        self.job_scope_label.setToolTip(tooltip)
        self.job_scope_label.setVisible(True)

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
        self.signal_submit_task.emit(prompt.strip(), [])

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
        self.signal_submit_task.emit(clean, [])

    @pyqtSlot(str)
    def _on_link_clicked(self, link: str):
        """Forward link activation signal."""
        self.signal_link_clicked.emit(link)

    @pyqtSlot(bool)
    def set_playback(self, snap: dict) -> None:
        """Show or update the speech player. Grows the collapsed pill while it is open."""
        was_visible = self.speech_bar.isVisible()
        self.speech_bar.apply(snap or {"active": False})
        if self.speech_bar.isVisible() != was_visible and not self.is_expanded:
            self._fit_collapsed_to_content()

    def _fit_collapsed_to_content(self) -> None:
        if self.is_expanded:
            return
        self.card.layout().activate()
        self.layout().activate()
        height = max(self.minimumSizeHint().height(), self.sizeHint().height())
        width = max(self.width(), self.minimumSizeHint().width())
        self._collapsed_height = height
        self._collapsed_width = max(self._collapsed_width, width)
        if self.height() != height or self.width() < width:
            self.resize(width, height)

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
            self.prompt_input.setPlaceholderText(PROMPT_PLACEHOLDER)

    @pyqtSlot(str)
    def set_prompt_text(self, text: str):
        """Set text in the prompt input field."""
        self.prompt_input.setPlainText(text)
        self.prompt_input.setFocus()

    @pyqtSlot()
    def clear_prompt(self):
        """Clear the prompt input without submitting a task."""
        self.prompt_input.clear()

    def attachment_payload(self) -> List[Dict[str, Any]]:
        """Plain attachment records safe to hand to the agent thread."""
        return [
            {
                "name": item["name"],
                "path": item["path"],
                "staged": bool(item.get("staged")),
            }
            for item in self._pending_attachments
        ]

    def set_attach_notice(self, text: str) -> None:
        """Show or hide a short attachment error under the message box."""
        self.attach_notice.setText(text or "")
        self.attach_notice.setVisible(bool(text))
        self._fit_collapsed_to_content()

    def clear_attachments(self, delete_staged: bool = True) -> None:
        """Remove pending chips. Staged clipboard files are deleted unless a send already consumed them."""
        items = list(self._pending_attachments)
        self._pending_attachments.clear()
        self._rebuild_attachment_chips()
        if delete_staged:
            discard_staged(items)

    def _choose_attachments(self) -> None:
        dialog = QFileDialog(
            self,
            "Attach files for Eidos",
            self._attach_dir,
            "All files (*);;Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        paths = dialog.selectedFiles()
        if not paths:
            return
        self._attach_dir = str(Path(paths[0]).parent)
        self._add_file_paths(paths)

    def _add_file_paths(self, paths: List[str]) -> None:
        notices: List[str] = []
        for path in paths:
            try:
                item = pending_from_path(path)
            except ValueError as exc:
                notices.append(str(exc))
                continue
            problem = self._add_pending(item, _preview_pixmap(item["path"]))
            if problem:
                notices.append(problem)
                break
        self.set_attach_notice(" ".join(dict.fromkeys(notices)))

    def _add_clipboard_image(self, image: QImage) -> None:
        if image is None or image.isNull():
            self.set_attach_notice("Clipboard image is empty.")
            return
        dest = staging_path(".png")
        if not image.save(str(dest), "PNG"):
            self.set_attach_notice("Could not save the pasted image.")
            return
        try:
            item = pending_from_path(str(dest), staged=True, name=pasted_image_name())
        except ValueError as exc:
            discard_staged([{"staged": True, "path": str(dest)}])
            self.set_attach_notice(str(exc))
            return
        preview = QPixmap.fromImage(image).scaled(
            28,
            28,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        problem = self._add_pending(item, preview)
        self.set_attach_notice(problem or "")

    def _add_pending(self, item: Dict[str, Any], preview: Optional[QPixmap]) -> str:
        """Keep one attachment. Return an error message, or an empty string."""
        if any(existing["path"] == item["path"] for existing in self._pending_attachments):
            if item.get("staged"):
                discard_staged([item])
            return ""
        if len(self._pending_attachments) >= MAX_ATTACHMENTS:
            if item.get("staged"):
                discard_staged([item])
            return f"You can attach up to {MAX_ATTACHMENTS} files at a time."
        if preview is not None and not preview.isNull():
            item["preview"] = preview
        self._pending_attachments.append(item)
        self._rebuild_attachment_chips()
        return ""

    def _remove_attachment(self, item_id: str) -> None:
        kept: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        for item in self._pending_attachments:
            if item.get("id") == item_id:
                removed.append(item)
            else:
                kept.append(item)
        self._pending_attachments = kept
        discard_staged(removed)
        self._rebuild_attachment_chips()

    def _rebuild_attachment_chips(self) -> None:
        while self.attach_row_layout.count():
            layout_item = self.attach_row_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._pending_attachments:
            self.attach_scroll.setVisible(False)
            self._fit_collapsed_to_content()
            return
        for item in self._pending_attachments:
            chip = AttachmentChip(item["id"], item["name"], item.get("preview"))
            chip.remove_requested.connect(self._remove_attachment)
            self.attach_row_layout.addWidget(chip)
        self.attach_row_layout.addStretch(1)
        self.attach_scroll.setVisible(True)
        self._fit_collapsed_to_content()

    @pyqtSlot(bool, str, str)
    def set_background_activity(self, active: bool, task: str = "", details: str = ""):
        """Update background activity indicator on status pill."""
        self._background_active = bool(active)
        self._background_task = task if active else ""
        self._background_details = details if active else ""

        if self._current_state == "idle":
            if self._background_active:
                bg_note = f"⚡ {task}" if task else "⚡ Background"
                self.status_label.setText(f"● Ready ({bg_note})")
                self.status_label.setStyleSheet("color: #bd93f9; font-weight: bold; font-size: 13px;")
                if details:
                    display_msg = details if len(details) <= 50 else details[:47] + "..."
                    self.details_label.setText(display_msg)
                    self.details_label.setToolTip(f"Background: {task}\n{details}")
            else:
                self.status_label.setText("● Ready")
                self.status_label.setStyleSheet("color: #50fa7b; font-weight: bold; font-size: 13px;")
                self.details_label.setText("")
                self.details_label.setToolTip("")

    @pyqtSlot(str, str)
    def set_status(self, status_key: str, message: str = ""):
        """Update the status pill display."""
        self._current_state = status_key.lower()
        color_hex, label_text = STATUS_COLORS.get(self._current_state, ("#50fa7b", f"● {status_key}"))

        if self._current_state == "idle" and self._background_active:
            bg_note = f"⚡ {self._background_task}" if self._background_task else "⚡ Background"
            self.status_label.setText(f"● Ready ({bg_note})")
            self.status_label.setStyleSheet("color: #bd93f9; font-weight: bold; font-size: 13px;")
        else:
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
            action_expand = menu.addAction("💬  Close Chat (Esc or Ctrl+E)")
        else:
            action_expand = menu.addAction("💬  Open Chat (Ctrl+E)")
        action_minimize = menu.addAction("−  Minimize Pill")

        action_new = menu.addAction("➕  New Chat")
        action_past = menu.addAction("📜  Past Conversations...")
        menu.addSeparator()
        action_workspace = menu.addAction("📂  Open Workspace (~/EidosWorkspace)...")
        action_skills = menu.addAction("🧠  Open Skills Directory (~/EidosWorkspace/skills)...")
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
        elif action == action_minimize:
            self.minimize_pill()
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
        attachments = self.attachment_payload()
        if not prompt and not attachments:
            return

        # Auto-expand to show chat response if currently collapsed
        if not self.is_expanded:
            self.expand_chat()

        self.signal_submit_task.emit(prompt, attachments)

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
        if isinstance(child, (QPushButton, QPlainTextEdit, QSizeGrip, QScrollArea)):
            return False
        if self.attach_scroll.isVisible():
            attach_rect = QRect(
                self.attach_scroll.mapToGlobal(QPoint(0, 0)),
                self.attach_scroll.size(),
            )
            if attach_rect.contains(global_pos):
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

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if _local_file_paths(mime) or _qimage_from_mime(mime) is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        paths = _local_file_paths(mime)
        if paths:
            self._add_file_paths(paths)
            event.acceptProposedAction()
            return
        image = _qimage_from_mime(mime)
        if image is not None:
            self._add_clipboard_image(image)
            event.acceptProposedAction()
            return
        event.ignore()

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
