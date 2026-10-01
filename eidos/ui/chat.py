"""PyQt6 Chat History Widget and Message Bubbles for EidosBot Floating HUD."""

from datetime import datetime
import html
import logging
import re
from typing import Any, Dict, Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

logger = logging.getLogger(__name__)


def markdown_to_chat_html(text: str) -> str:
    """
    Convert markdown formatting into clean Dracula-themed rich HTML for PyQt6 QLabel.
    Supports code blocks, inline code, bold, italics, headings, bullet/numbered lists.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    # 1. Extract triple-backtick code blocks
    code_blocks = []

    def replace_code_block(match):
        lang = (match.group(1) or "").strip()
        code = match.group(2)
        escaped_code = html.escape(code.strip("\n"), quote=False)
        lang_badge = (
            f"<div style='color: #bd93f9; font-size: 10px; font-weight: bold; margin-bottom: 4px;'>"
            f"{html.escape(lang.upper(), quote=False)}</div>"
            if lang
            else ""
        )
        block_html = (
            f"<div style='background-color: #14151b; border: 1px solid #44475a; "
            f"border-radius: 6px; padding: 8px 10px; margin: 6px 0;'>"
            f"{lang_badge}"
            f"<pre style='color: #50fa7b; font-family: monospace; font-size: 12px; margin: 0; "
            f"white-space: pre-wrap; line-height: 1.3;'>{escaped_code}</pre>"
            f"</div>"
        )
        idx = len(code_blocks)
        code_blocks.append(block_html)
        return f"___CODE_BLOCK_{idx}___"

    text = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n?(.*?)```", replace_code_block, text, flags=re.DOTALL)

    # 2. Extract inline backticks `code`
    inline_codes = []

    def replace_inline_code(match):
        code = match.group(1)
        escaped = html.escape(code, quote=False)
        idx = len(inline_codes)
        inline_codes.append(
            f"<code style='background-color: #14151b; color: #ff79c6; border-radius: 3px; "
            f"padding: 1px 4px; font-family: monospace; font-size: 12px;'>{escaped}</code>"
        )
        return f"___INLINE_{idx}___"

    text = re.sub(r"`([^`\n]+)`", replace_inline_code, text)

    # 3. Normalize any literal numeric entities for apostrophes and quotes before escaping
    text = text.replace("&#x27;", "'").replace("&#39;", "'").replace("&apos;", "'")
    # Escape remaining raw HTML without mangling quotes/apostrophes into &#x27;
    text = html.escape(text, quote=False)

    # 4. Bold and Italic formatting
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)

    # 5. Markdown Links [label](url)
    def replace_md_link(match):
        label = match.group(1)
        url = match.group(2)
        return (
            f"<a href='{url}' style='color: #8be9fd; text-decoration: underline; font-weight: bold;'>"
            f"{label}</a>"
        )

    text = re.sub(r"\[([^\]]+)\]\(((?:https?://|file://)[^\s\)]+)\)", replace_md_link, text)

    # 6. Autolink raw URLs (not already inside href attribute)
    def replace_raw_url(match):
        url = match.group(1)
        trailing = match.group(2) or ""
        return (
            f"<a href='{url}' style='color: #8be9fd; text-decoration: underline;'>"
            f"{url}</a>{trailing}"
        )

    text = re.sub(
        r'(?<!href=[\'"])((?:https?://|file://)[^\s<"\'\)]+?)([\.,;]?(?=\s|$|<|\'|"))',
        replace_raw_url,
        text,
    )

    # 7. Headings
    text = re.sub(
        r"^###\s+(.*?)$",
        r"<h4 style='color: #8be9fd; margin: 6px 0 2px 0;'>\1</h4>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^##\s+(.*?)$",
        r"<h3 style='color: #8be9fd; margin: 8px 0 3px 0;'>\1</h3>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^#\s+(.*?)$",
        r"<h2 style='color: #bd93f9; margin: 10px 0 4px 0;'>\1</h2>",
        text,
        flags=re.MULTILINE,
    )

    # 6. Bullet lists
    lines = text.split("\n")
    processed_lines = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                processed_lines.append("<ul style='margin: 4px 0; padding-left: 18px;'>")
                in_list = True
            item = stripped[2:]
            processed_lines.append(f"<li>{item}</li>")
        else:
            if in_list:
                processed_lines.append("</ul>")
                in_list = False
            processed_lines.append(line)
    if in_list:
        processed_lines.append("</ul>")

    # 7. Convert line breaks (outside list and code blocks)
    out_parts = []
    for line in processed_lines:
        if line.startswith("<ul") or line.endswith("</ul>") or line.startswith("<h"):
            out_parts.append(line)
        elif line.startswith("___CODE_BLOCK_"):
            out_parts.append(line)
        else:
            out_parts.append(line + "<br/>")

    out = "".join(out_parts)
    out = re.sub(r"(<br/>\s*){3,}", "<br/><br/>", out)
    if out.endswith("<br/>"):
        out = out[:-5]

    # 8. Restore code blocks and inline codes
    for i, cb in enumerate(code_blocks):
        out = out.replace(f"___CODE_BLOCK_{i}___", cb)
    for i, ic in enumerate(inline_codes):
        out = out.replace(f"___INLINE_{i}___", ic)

    return out


class ChatEditArea(QPlainTextEdit):
    """Text area for inline editing of chat messages with keyboard shortcuts."""
    signal_save = pyqtSignal()
    signal_cancel = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(
            "QPlainTextEdit { background-color: #14151b; color: #f8f8f2; "
            "border: 1px solid #bd93f9; border-radius: 6px; padding: 6px; font-size: 12px; } "
            "QPlainTextEdit:focus { border: 1px solid #50fa7b; }"
        )
        self.setMaximumHeight(120)
        self.setMinimumHeight(55)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.signal_save.emit()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Escape:
            self.signal_cancel.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    """Single message bubble inside the scrollable chat container."""

    signal_copy_clicked = pyqtSignal(str)
    signal_edit_submitted = pyqtSignal(str, str)  # (message_id, new_content)
    signal_retry_requested = pyqtSignal(str)      # prompt/text to retry
    signal_replay_tts_requested = pyqtSignal(str) # text to replay via TTS
    signal_delete_requested = pyqtSignal(object)  # bubble instance
    signal_copy_to_input = pyqtSignal(str)        # text to copy to prompt bar

    def __init__(
        self,
        role: str,
        content: str,
        title: Optional[str] = None,
        message_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.role = role.lower()
        self.raw_content = content
        self.title = title
        self.message_id = message_id or ""
        self.is_editing = False

        self.btn_copy: Optional[QPushButton] = None
        self.btn_edit: Optional[QPushButton] = None
        self.btn_retry: Optional[QPushButton] = None
        self.btn_speak: Optional[QPushButton] = None
        self.btn_delete: Optional[QPushButton] = None

        self._init_ui()

    def _create_action_btn(self, text: str, tooltip: str, on_click: Any) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 2px 6px; font-size: 11px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )
        btn.clicked.connect(on_click)
        return btn

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        now_str = datetime.now().strftime("%H:%M")

        # Header Row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        if self.role == "user":
            self.setStyleSheet(
                "QFrame { background-color: #2b2e3f; border: 1px solid #44475a; "
                f"border-radius: 8px; margin-left: 24px; margin-right: 2px; }}\n{GLOBAL_TOOLTIP_STYLE}"
            )
            lbl_title = QLabel(f"👤 <b>You</b> <span style='color: #6272a4; font-size: 11px;'>• {now_str}</span>")
            lbl_title.setStyleSheet("color: #bd93f9; font-size: 12px;")
            header_layout.addWidget(lbl_title)
            header_layout.addStretch(1)

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy message to clipboard", lambda: self._copy_to_clipboard(self.btn_copy))
            header_layout.addWidget(self.btn_copy)

            self.btn_edit = self._create_action_btn("✏️ Edit", "Edit message and resubmit", self.start_editing)
            header_layout.addWidget(self.btn_edit)

            self.btn_retry = self._create_action_btn("🔄 Retry", "Re-run prompt", self.request_retry)
            header_layout.addWidget(self.btn_retry)

            self.btn_delete = self._create_action_btn("🗑️", "Delete message", self.request_delete)
            header_layout.addWidget(self.btn_delete)

        elif self.role == "assistant":
            self.setStyleSheet(
                "QFrame { background-color: #1e1f29; border: 1px solid #6272a4; "
                f"border-radius: 8px; margin-left: 2px; margin-right: 24px; }}\n{GLOBAL_TOOLTIP_STYLE}"
            )
            name_text = self.title or "EidosBot"
            lbl_title = QLabel(f"🤖 <b>{name_text}</b> <span style='color: #6272a4; font-size: 11px;'>• {now_str}</span>")
            lbl_title.setStyleSheet("color: #8be9fd; font-size: 12px;")
            header_layout.addWidget(lbl_title)
            header_layout.addStretch(1)

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy response to clipboard", lambda: self._copy_to_clipboard(self.btn_copy))
            header_layout.addWidget(self.btn_copy)

            self.btn_retry = self._create_action_btn("🔄 Retry", "Regenerate / retry response", self.request_retry)
            header_layout.addWidget(self.btn_retry)

            self.btn_speak = self._create_action_btn("🔊 Replay", "Replay response via voice (TTS)", self.request_replay_tts)
            header_layout.addWidget(self.btn_speak)

            self.btn_delete = self._create_action_btn("🗑️", "Delete message", self.request_delete)
            header_layout.addWidget(self.btn_delete)

        elif self.role == "thought":
            self.setStyleSheet(
                "QFrame { background-color: #191a21; border-left: 3px solid #f1fa8c; "
                "border-top: 1px solid #282a36; border-right: 1px solid #282a36; border-bottom: 1px solid #282a36; "
                f"border-radius: 6px; margin: 2px 10px; }}\n{GLOBAL_TOOLTIP_STYLE}"
            )
            lbl_title = QLabel(f"💭 <i>Reasoning</i> <span style='color: #6272a4; font-size: 10px;'>• {now_str}</span>")
            lbl_title.setStyleSheet("color: #f1fa8c; font-size: 11px;")
            header_layout.addWidget(lbl_title)
            header_layout.addStretch(1)

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy reasoning to clipboard", lambda: self._copy_to_clipboard(self.btn_copy))
            header_layout.addWidget(self.btn_copy)

            self.btn_delete = self._create_action_btn("🗑️", "Delete message", self.request_delete)
            header_layout.addWidget(self.btn_delete)

        elif self.role == "tool":
            self.setStyleSheet(
                "QFrame { background-color: #191a21; border-left: 3px solid #50fa7b; "
                "border-top: 1px solid #282a36; border-right: 1px solid #282a36; border-bottom: 1px solid #282a36; "
                f"border-radius: 6px; margin: 2px 10px; }}\n{GLOBAL_TOOLTIP_STYLE}"
            )
            tool_name = self.title or "Action"
            lbl_title = QLabel(f"⚡ <b>Action: {tool_name}</b> <span style='color: #6272a4; font-size: 10px;'>• {now_str}</span>")
            lbl_title.setStyleSheet("color: #50fa7b; font-size: 11px;")
            header_layout.addWidget(lbl_title)
            header_layout.addStretch(1)

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy tool output to clipboard", lambda: self._copy_to_clipboard(self.btn_copy))
            header_layout.addWidget(self.btn_copy)

            self.btn_delete = self._create_action_btn("🗑️", "Delete message", self.request_delete)
            header_layout.addWidget(self.btn_delete)

        elif self.role == "error":
            self.setStyleSheet(
                "QFrame { background-color: #191a21; border-left: 3px solid #ff5555; "
                "border-top: 1px solid #ff5555; border-right: 1px solid #ff5555; border-bottom: 1px solid #ff5555; "
                f"border-radius: 6px; margin: 2px 10px; }}\n{GLOBAL_TOOLTIP_STYLE}"
            )
            lbl_title = QLabel(f"⚠️ <b>System Error</b> <span style='color: #ff5555; font-size: 10px;'>• {now_str}</span>")
            lbl_title.setStyleSheet("color: #ff5555; font-size: 11px;")
            header_layout.addWidget(lbl_title)
            header_layout.addStretch(1)

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy error to clipboard", lambda: self._copy_to_clipboard(self.btn_copy))
            header_layout.addWidget(self.btn_copy)

            self.btn_retry = self._create_action_btn("🔄 Retry", "Retry prompt", self.request_retry)
            header_layout.addWidget(self.btn_retry)

            self.btn_delete = self._create_action_btn("🗑️", "Delete message", self.request_delete)
            header_layout.addWidget(self.btn_delete)

        layout.addLayout(header_layout)

        # Message Content Label
        self.content_label = QLabel()
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.content_label.setOpenExternalLinks(True)

        if self.role == "thought":
            self.content_label.setStyleSheet("color: #e0e0e0; font-size: 12px; font-style: italic;")
            self.content_label.setText(self.raw_content)
        elif self.role == "tool":
            self.content_label.setStyleSheet("color: #f8f8f2; font-family: monospace; font-size: 11px;")
            escaped = html.escape(self.raw_content, quote=False)
            escaped_with_links = re.sub(
                r'((?:https?://|file://)[^\s<"\'\)]+?)([\.,;]?(?=\s|$|<|\'|"))',
                r"<a href='\1' style='color: #50fa7b; text-decoration: underline;'>\1</a>\2",
                escaped,
            )
            escaped_with_links = escaped_with_links.replace("\n", "<br/>")
            self.content_label.setText(escaped_with_links)
        elif self.role == "error":
            self.content_label.setStyleSheet("color: #ff5555; font-size: 12px;")
            self.content_label.setText(self.raw_content)
        else:
            self.content_label.setStyleSheet("color: #f8f8f2; font-size: 13px; line-height: 1.4;")
            html_content = markdown_to_chat_html(self.raw_content)
            self.content_label.setText(html_content)

        layout.addWidget(self.content_label)

        # Edit Container (for in-place editing)
        self.edit_container = QWidget()
        self.edit_container.setVisible(False)
        edit_layout = QVBoxLayout(self.edit_container)
        edit_layout.setContentsMargins(0, 4, 0, 0)
        edit_layout.setSpacing(4)

        self.edit_text_area = ChatEditArea(self.edit_container)
        self.edit_text_area.setPlainText(self.raw_content)
        self.edit_text_area.signal_save.connect(self.save_editing)
        self.edit_text_area.signal_cancel.connect(self.cancel_editing)
        edit_layout.addWidget(self.edit_text_area)

        edit_btn_row = QHBoxLayout()
        edit_btn_row.setContentsMargins(0, 0, 0, 0)
        edit_btn_row.setSpacing(6)

        self.btn_save_edit = QPushButton("💾 Save & Run")
        self.btn_save_edit.setToolTip("Save edited message and execute task (Ctrl+Enter)")
        self.btn_save_edit.setStyleSheet(
            "QPushButton { background-color: #50fa7b; color: #282a36; font-weight: bold; "
            "border-radius: 4px; padding: 3px 8px; font-size: 11px; border: none; } "
            "QPushButton:hover { background-color: #69ff94; }"
        )
        self.btn_save_edit.clicked.connect(self.save_editing)
        edit_btn_row.addWidget(self.btn_save_edit)

        self.btn_cancel_edit = QPushButton("Cancel")
        self.btn_cancel_edit.setToolTip("Discard edits (Esc)")
        self.btn_cancel_edit.setStyleSheet(
            "QPushButton { background-color: #44475a; color: #f8f8f2; "
            "border-radius: 4px; padding: 3px 8px; font-size: 11px; border: none; } "
            "QPushButton:hover { background-color: #6272a4; }"
        )
        self.btn_cancel_edit.clicked.connect(self.cancel_editing)
        edit_btn_row.addWidget(self.btn_cancel_edit)

        hint_lbl = QLabel("<span style='color: #6272a4; font-size: 10px;'>Ctrl+Enter to save, Esc to cancel</span>")
        edit_btn_row.addWidget(hint_lbl)
        edit_btn_row.addStretch(1)

        edit_layout.addLayout(edit_btn_row)
        layout.addWidget(self.edit_container)

    def start_editing(self):
        """Switch bubble into inline editing mode."""
        self.is_editing = True
        self.content_label.setVisible(False)
        self.edit_text_area.setPlainText(self.raw_content)
        self.edit_container.setVisible(True)
        self.edit_text_area.setFocus()
        cursor = self.edit_text_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.edit_text_area.setTextCursor(cursor)

    def cancel_editing(self):
        """Cancel inline editing mode and restore original content."""
        self.is_editing = False
        self.edit_text_area.setPlainText(self.raw_content)
        self.edit_container.setVisible(False)
        self.content_label.setVisible(True)

    def save_editing(self):
        """Save edited content, update label, and emit edit signal."""
        new_text = self.edit_text_area.toPlainText().strip()
        if not new_text:
            return
        self.raw_content = new_text
        if self.role == "user":
            self.content_label.setText(markdown_to_chat_html(new_text))
        else:
            self.content_label.setText(new_text)
        self.is_editing = False
        self.edit_container.setVisible(False)
        self.content_label.setVisible(True)
        self.signal_edit_submitted.emit(self.message_id, new_text)

    def request_retry(self):
        """Request retry/regeneration of this message or prompt."""
        self.signal_retry_requested.emit(self.raw_content)

    def request_replay_tts(self):
        """Request TTS voice playback for this message."""
        if self.btn_speak:
            self.btn_speak.setText("🔊 Playing...")
            QTimer.singleShot(2500, self._reset_speak_button)
        self.signal_replay_tts_requested.emit(self.raw_content)

    def _reset_speak_button(self):
        if self.btn_speak:
            self.btn_speak.setText("🔊 Replay")

    def request_delete(self):
        """Request deletion of this message."""
        self.signal_delete_requested.emit(self)

    def _copy_to_clipboard(self, button: Optional[QPushButton] = None):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.raw_content)
            if button:
                button.setText("✓ Copied")
                button.setStyleSheet(
                    "QPushButton { background-color: #50fa7b; color: #282a36; font-weight: bold; "
                    "border-radius: 4px; padding: 2px 6px; font-size: 11px; border: none; }"
                )
                QTimer.singleShot(1500, lambda: self._reset_copy_button(button))
            self.signal_copy_clicked.emit(self.raw_content)

    def _reset_copy_button(self, button: QPushButton):
        button.setText("📋 Copy")
        button.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 2px 6px; font-size: 11px; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )

    def contextMenuEvent(self, event):
        """Show full context menu on right-click."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #21222c; color: #f8f8f2; border: 1px solid #44475a; "
            "border-radius: 6px; padding: 4px; } "
            "QMenu::item { padding: 4px 18px; border-radius: 4px; } "
            "QMenu::item:selected { background-color: #44475a; color: #50fa7b; } "
            "QMenu::separator { height: 1px; background-color: #44475a; margin: 4px 0; }"
        )

        act_copy = menu.addAction("📋 Copy Message")
        act_copy.triggered.connect(lambda: self._copy_to_clipboard(self.btn_copy))

        act_copy_input = menu.addAction("📝 Copy to Prompt Input")
        act_copy_input.triggered.connect(lambda: self.signal_copy_to_input.emit(self.raw_content))

        if self.role == "assistant":
            act_speak = menu.addAction("🔊 Replay Voice (TTS)")
            act_speak.triggered.connect(self.request_replay_tts)

        if self.role == "user":
            menu.addSeparator()
            act_edit = menu.addAction("✏️ Edit Message")
            act_edit.triggered.connect(self.start_editing)

            act_retry = menu.addAction("🔄 Retry Prompt")
            act_retry.triggered.connect(self.request_retry)

        elif self.role in ("assistant", "error"):
            menu.addSeparator()
            act_retry = menu.addAction("🔄 Regenerate / Retry")
            act_retry.triggered.connect(self.request_retry)

        menu.addSeparator()
        act_delete = menu.addAction("🗑️ Delete Message")
        act_delete.triggered.connect(self.request_delete)

        menu.exec(event.globalPos())


class ChatHistoryWidget(QScrollArea):
    """Scrollable chat history container hosting MessageBubble elements."""

    signal_retry = pyqtSignal(str)              # prompt text to run
    signal_edit_message = pyqtSignal(str, str)  # (message_id, new_content)
    signal_replay_tts = pyqtSignal(str)         # message text to speak
    signal_copy_to_input = pyqtSignal(str)      # text to put in prompt bar
    signal_delete_message = pyqtSignal(str)     # message_id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._message_count = 0
        self.bubbles: list[MessageBubble] = []
        self._init_ui()

    def _init_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea { background-color: transparent; border: none; } "
            "QScrollBar:vertical { background: #191a21; width: 6px; border-radius: 3px; margin: 0; } "
            "QScrollBar::handle:vertical { background: #44475a; border-radius: 3px; min-height: 20px; } "
            "QScrollBar::handle:vertical:hover { background: #6272a4; } "
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; } "
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }\n"
            f"{GLOBAL_TOOLTIP_STYLE}"
        )

        self.container = QWidget()
        self.container.setStyleSheet("background-color: transparent;")
        self.layout_messages = QVBoxLayout(self.container)
        self.layout_messages.setContentsMargins(4, 4, 4, 4)
        self.layout_messages.setSpacing(8)

        # Welcome placeholder
        self.welcome_widget = self._create_welcome_widget()
        self.layout_messages.addWidget(self.welcome_widget)

        # Bottom stretch to push bubbles up
        self.spacer = QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.layout_messages.addItem(self.spacer)

        self.setWidget(self.container)

    def _create_welcome_widget(self) -> QWidget:
        box = QFrame()
        box.setStyleSheet(
            "QFrame { background-color: #21222c; border: 1px dashed #44475a; "
            "border-radius: 8px; padding: 14px; margin: 10px 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        title = QLabel("🤖 <b>EidosBot Desktop Co-Pilot</b>")
        title.setStyleSheet("color: #bd93f9; font-size: 14px;")
        layout.addWidget(title)

        desc = QLabel(
            "Welcome! Ask a question or give a desktop task below.<br/>"
            "I can interact with windows, execute shell commands, read screens, "
            "and answer system queries."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6272a4; font-size: 12px; line-height: 1.4;")
        layout.addWidget(desc)
        return box

    def add_message(
        self,
        role: str,
        content: str,
        title: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> MessageBubble:
        """Add a new message bubble to the chat history and auto-scroll to bottom."""
        if self.welcome_widget and self.welcome_widget.isVisible():
            self.welcome_widget.hide()

        # Remove bottom spacer before adding widget
        self.layout_messages.removeItem(self.spacer)

        bubble = MessageBubble(
            role=role,
            content=content,
            title=title,
            message_id=message_id,
            parent=self.container,
        )
        bubble.signal_retry_requested.connect(self._handle_bubble_retry)
        bubble.signal_edit_submitted.connect(self._handle_bubble_edit)
        bubble.signal_replay_tts_requested.connect(self.signal_replay_tts.emit)
        bubble.signal_copy_to_input.connect(self.signal_copy_to_input.emit)
        bubble.signal_delete_requested.connect(self._handle_bubble_delete)

        self.bubbles.append(bubble)
        self.layout_messages.addWidget(bubble)
        self._message_count = len(self.bubbles)

        # Re-add bottom spacer
        self.layout_messages.addItem(self.spacer)

        # Auto-scroll to bottom
        QTimer.singleShot(50, self.scroll_to_bottom)
        return bubble

    def _handle_bubble_retry(self, text: str):
        """Dispatch retry request, finding preceding user prompt if sender is assistant/error."""
        sender = self.sender()
        prompt = text
        if isinstance(sender, MessageBubble):
            if sender.role in ("assistant", "error"):
                try:
                    idx = self.bubbles.index(sender)
                    for b in reversed(self.bubbles[:idx]):
                        if b.role == "user" and b.raw_content.strip():
                            prompt = b.raw_content
                            break
                except ValueError:
                    pass
            elif sender.role == "user":
                prompt = sender.raw_content
        self.signal_retry.emit(prompt)

    def _handle_bubble_edit(self, message_id: str, new_content: str):
        self.signal_edit_message.emit(message_id, new_content)

    def _handle_bubble_delete(self, bubble: MessageBubble):
        msg_id = getattr(bubble, "message_id", "")
        if bubble in self.bubbles:
            self.bubbles.remove(bubble)
        self.layout_messages.removeWidget(bubble)
        bubble.deleteLater()
        self._message_count = len(self.bubbles)
        if len(self.bubbles) == 0 and self.welcome_widget:
            self.welcome_widget.show()
        self.signal_delete_message.emit(msg_id)

    def clear_messages(self):
        """Remove all message bubbles and restore welcome card."""
        for b in self.bubbles:
            b.deleteLater()
        self.bubbles.clear()

        while self.layout_messages.count() > 0:
            item = self.layout_messages.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self._message_count = 0
        self.welcome_widget = self._create_welcome_widget()
        self.layout_messages.addWidget(self.welcome_widget)
        self.welcome_widget.show()
        self.spacer = QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.layout_messages.addItem(self.spacer)

    def get_last_user_message(self) -> Optional[str]:
        """Return the text of the most recent user prompt in history."""
        for b in reversed(self.bubbles):
            if b.role == "user" and b.raw_content.strip():
                return b.raw_content
        return None

    def scroll_to_bottom(self):
        """Scroll vertical scrollbar to bottom-most position."""
        v_bar = self.verticalScrollBar()
        if v_bar:
            v_bar.setValue(v_bar.maximum())

    @property
    def message_count(self) -> int:
        return self._message_count
