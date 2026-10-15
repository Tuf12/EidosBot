"""PyQt6 Chat History Widget and Message Bubbles for EidosBot Floating HUD."""

from datetime import datetime
import html
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Any, Dict, List, Optional
import urllib.parse
import webbrowser

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QSyntaxHighlighter, QTextCharFormat
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


def _launch_external_url(url: str) -> bool:
    """Open external HTTP/HTTPS URL in default desktop browser."""
    logger.info("Opening external chat link: %s", url)
    # Prefer direct xdg-open on Linux to avoid Qt 6 D-Bus host portal application ID registration issues
    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception as exc:
            logger.debug("xdg-open external URL failed: %s", exc)

    try:
        if webbrowser.open(url):
            return True
    except Exception as exc:
        logger.debug("webbrowser.open failed: %s", exc)

    try:
        return bool(QDesktopServices.openUrl(QUrl(url)))
    except Exception as exc:
        logger.warning("QDesktopServices.openUrl failed: %s", exc)
        return False


def _launch_local_file(file_path: str) -> bool:
    """Open local document or directory in default system handler or editor."""
    logger.info("Opening local chat file: %s", file_path)
    abs_path = os.path.abspath(file_path)

    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(
                ["xdg-open", abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception as exc:
            logger.debug("xdg-open local file failed: %s", exc)
    elif sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            os.startfile(abs_path)
            return True
        except Exception:
            pass

    try:
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path)))
    except Exception as exc:
        logger.warning("QDesktopServices.openUrl failed for local file: %s", exc)
        return False


def _resolve_local_file_path(target: str, workspace_dir: Optional[str] = None) -> Optional[str]:
    """Resolve a relative or absolute file path to an actual file on disk."""
    clean_target = (target or "").strip()
    if not clean_target:
        return None

    # 1. Expand ~ and check if file exists directly
    expanded = os.path.expanduser(clean_target)
    if os.path.exists(expanded):
        return os.path.abspath(expanded)

    # 2. Check relative to current working directory
    cand_cwd = os.path.abspath(expanded)
    if os.path.exists(cand_cwd):
        return cand_cwd

    # 3. Determine workspace candidates
    ws_candidates: List[str] = []
    if workspace_dir:
        ws_candidates.append(os.path.abspath(os.path.expanduser(workspace_dir)))
    ws_candidates.append(os.path.abspath(os.path.expanduser("~/EidosWorkspace")))
    ws_candidates.append(os.path.abspath(os.getcwd()))

    clean_sub = clean_target.lstrip("/")
    clean_path = Path(clean_sub)
    file_name = clean_path.name

    for ws in ws_candidates:
        if not os.path.isdir(ws):
            continue
        ws_p = Path(ws)

        # Direct subpath in workspace
        cand = ws_p / clean_sub
        if cand.exists():
            return str(cand.resolve())

        # Check in common subdirectories
        for sub_dir in ("documents", "jobs", "downloads", "documents/analysis_phase_1"):
            cand_sub = ws_p / sub_dir / clean_sub
            if cand_sub.exists():
                return str(cand_sub.resolve())

        # Recursive search in workspace by relative path suffix or filename
        if file_name:
            try:
                for match in ws_p.rglob(file_name):
                    if str(match).endswith(clean_sub) and match.is_file():
                        return str(match.resolve())
            except Exception:
                pass

            try:
                for match in ws_p.rglob(file_name):
                    if match.is_file():
                        return str(match.resolve())
            except Exception:
                pass

    return None


# Autolink http(s), file://, absolute paths, and portfolio markdown refs.
# Backslash is excluded so JSON tool dumps (path.md\n# Heading) do not
# swallow the escaped newline into the href.
_CHAT_AUTOLINK_RE = re.compile(
    r'(?<![\w\'"])('
    r'(?:https?://|file://)[^\s<"\'\)\\]+?'
    r'|/(?:home|tmp|usr|opt|var)/[^\s<"\'\)\\]+?\.[A-Za-z0-9]+'
    r'|portfolios/[A-Za-z0-9_\-]+\.md'
    r')([\.,;]?(?=\s|$|<|\'|"|\\))'
)

_INLINE_LINK_RE = re.compile(
    r'^(?:https?://\S+|file://\S+|www\.\S+|/(?:home|tmp|usr|opt|var)/\S+|~/\S+|portfolios/[A-Za-z0-9_\-]+\.md)$'
)


def _clean_chat_link_target(url: str) -> str:
    """Drop JSON escape tails and hash fragments that are not part of a local path."""
    clean = (url or "").strip().strip("'\"")
    for token in ("\\r\\n", "\\n", "\\r", "\\t"):
        idx = clean.find(token)
        if idx != -1:
            clean = clean[:idx]
    clean = clean.strip().strip("'\"")
    if not clean.startswith(("http://", "https://", "www.")):
        if "#" in clean:
            clean = clean.split("#", 1)[0].strip()
    return clean


def linkify_escaped_text(text: str, color: str = "#8be9fd") -> str:
    """Wrap already-escaped URLs and file paths in anchor tags."""

    def repl(match: re.Match) -> str:
        url = match.group(1)
        trailing = match.group(2) or ""
        return (
            f"<a href='{url}' style='color: {color}; text-decoration: underline;'>"
            f"{url}</a>{trailing}"
        )

    return _CHAT_AUTOLINK_RE.sub(repl, text)


def open_chat_link(url: str, workspace_dir: Optional[str] = None) -> bool:
    """
    Robust desktop opener for in-chat links.
    Handles:
    - Web URLs (http://, https://, www.)
    - Local file paths (absolute /home/..., relative portfolios/item_114.md, ~)
    - File URLs (file://...)
    """
    clean = _clean_chat_link_target(url)
    if not clean:
        return False

    # In-chat copy-code protocol handled by MessageBubble
    if clean.startswith("copy-code:"):
        return True

    # External web URLs
    if clean.startswith(("http://", "https://")):
        return _launch_external_url(clean)
    if clean.startswith("www."):
        return _launch_external_url(f"https://{clean}")

    # File URLs (e.g. file:///path/to/file)
    if clean.startswith("file://"):
        parsed = urllib.parse.urlparse(clean)
        clean = urllib.parse.unquote(parsed.path)

    # Resolve as local file
    resolved = _resolve_local_file_path(clean, workspace_dir)
    if resolved:
        return _launch_local_file(resolved)

    # Fallback: check if domain without protocol
    if "." in clean and not clean.startswith("/") and not clean.endswith(
        (".md", ".txt", ".json", ".csv", ".py", ".pdf", ".html", ".log", ".png", ".jpg")
    ):
        return _launch_external_url(f"https://{clean}")

    # Missing local file: still attempt to open absolute path
    logger.warning("Target file for chat link was not found on disk: %s", clean)
    expanded = os.path.abspath(os.path.expanduser(clean))
    return _launch_local_file(expanded)


def markdown_to_chat_html(text: str) -> str:
    """
    Convert markdown formatting into clean Dracula-themed rich HTML for PyQt6 QLabel.
    Supports code blocks, inline code, bold, italics, headings (# through ######),
    and bullet lists.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    # 1. Extract triple-backtick code blocks
    code_blocks = []

    def replace_code_block(match):
        lang = (match.group(1) or "").strip()
        code = textwrap.dedent(match.group(2).strip("\n"))
        escaped_code = html.escape(code, quote=False)
        display_lang = lang.upper() if lang else "CODE"
        header_table = (
            f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-bottom: 4px; border-bottom: 1px solid #282a36; padding-bottom: 3px;'>"
            f"<tr>"
            f"<td align='left' style='color: #bd93f9; font-size: 10px; font-weight: bold; font-family: monospace;'>💻 {html.escape(display_lang, quote=False)}</td>"
            f"<td align='right'><a href='copy-code:{len(code_blocks)}' style='color: #50fa7b; text-decoration: none; font-size: 11px; font-weight: bold; font-family: sans-serif;'>📋 Copy</a></td>"
            f"</tr></table>"
        )
        block_html = (
            f"<div style='background-color: #14151b; border: 1px solid #44475a; "
            f"border-radius: 6px; padding: 8px 10px; margin: 6px 0;'>"
            f"{header_table}"
            f"<pre style='color: #50fa7b; font-family: monospace; font-size: 12px; margin: 0; "
            f"white-space: pre-wrap; line-height: 1.3;'>{escaped_code}</pre>"
            f"</div>"
        )
        idx = len(code_blocks)
        code_blocks.append(block_html)
        return f"___CODE_BLOCK_{idx}___"

    text = re.sub(
        r"(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n?(.*?)(?:^[ \t]*)?```",
        replace_code_block,
        text,
        flags=re.DOTALL | re.MULTILINE,
    )

    # 2. Extract inline backticks `code`
    inline_codes = []

    def replace_inline_code(match):
        code = match.group(1)
        target = code.strip()
        linkable = bool(_INLINE_LINK_RE.match(target))
        escaped = html.escape(code, quote=False)
        color = "#8be9fd" if linkable else "#ff79c6"
        deco = " text-decoration: underline;" if linkable else ""
        inner = (
            f"<code style='background-color: #14151b; color: {color}; border-radius: 3px; "
            f"padding: 1px 4px; font-family: monospace; font-size: 12px;{deco}'>{escaped}</code>"
        )
        if linkable:
            href = html.escape(target, quote=True)
            inner = (
                f"<a href=\"{href}\" style='color: #8be9fd; text-decoration: underline; font-weight: bold;'>"
                f"{inner}</a>"
            )
        idx = len(inline_codes)
        inline_codes.append(inner)
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
    md_links = []

    def replace_md_link(match):
        label = match.group(1).strip()
        url = match.group(2).strip().strip("'\"")
        idx = len(md_links)
        md_links.append(
            f"<a href='{url}' style='color: #8be9fd; text-decoration: underline; font-weight: bold;'>"
            f"{label}</a>"
        )
        return f"___MD_LINK_{idx}___"

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", replace_md_link, text)

    # 6. Autolink raw URLs and known local file references (not inside tags or existing links)
    text = linkify_escaped_text(text)

    # 7. Headings. Longer marks first so #### is not left as literal hashes.
    text = re.sub(
        r"^######[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
        r"<h6 style='color: #8be9fd; font-size: 12px; font-weight: 600; margin: 4px 0 2px 0;'>\1</h6>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^#####[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
        r"<h6 style='color: #8be9fd; font-size: 13px; font-weight: 600; margin: 4px 0 2px 0;'>\1</h6>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^####[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
        r"<h5 style='color: #8be9fd; margin: 6px 0 2px 0;'>\1</h5>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^###[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
        r"<h4 style='color: #8be9fd; margin: 6px 0 2px 0;'>\1</h4>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^##[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
        r"<h3 style='color: #8be9fd; margin: 8px 0 3px 0;'>\1</h3>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^#[ \t]+(.*?)(?:[ \t]+#+\s*)?$",
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

    # 8. Restore links, code blocks, and inline codes
    for i, l_html in enumerate(md_links):
        out = out.replace(f"___MD_LINK_{i}___", l_html)
    for i, cb in enumerate(code_blocks):
        out = out.replace(f"___CODE_BLOCK_{i}___", cb)
    for i, ic in enumerate(inline_codes):
        out = out.replace(f"___INLINE_{i}___", ic)

    return out


_FENCE_RE = re.compile(
    r"(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n?(.*?)(?:^[ \t]*)?```",
    re.DOTALL | re.MULTILINE,
)
_UNCLOSED_FENCE_RE = re.compile(
    r"(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n?([\s\S]*)$",
    re.MULTILINE,
)


def markdown_to_plain_text(text: str) -> str:
    """Readable wording of a chat message, matching what the bubble shows.

    Heading marks, emphasis, and link syntax are removed. Code fences are
    dropped and the code itself is kept. Links become ``label (url)``.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n")
    blocks: List[str] = []

    def stash_fence(match: re.Match) -> str:
        code = textwrap.dedent(match.group(2).strip("\n"))
        blocks.append(code)
        return f"\ue000CODE{len(blocks) - 1}\ue001"

    text = _FENCE_RE.sub(stash_fence, text)
    unclosed = _UNCLOSED_FENCE_RE.search(text)
    if unclosed:
        code = textwrap.dedent((unclosed.group(2) or "").strip("\n"))
        blocks.append(code)
        text = text[: unclosed.start()] + f"\ue000CODE{len(blocks) - 1}\ue001"

    plain = "\n".join(_plain_markdown_line(line) for line in text.split("\n"))
    for index, code in enumerate(blocks):
        plain = plain.replace(f"\ue000CODE{index}\ue001", code)
    return plain


def _plain_markdown_line(line: str) -> str:
    heading = re.match(r"^(#{1,6})[ \t]+(.*)$", line)
    if heading:
        title = re.sub(r"[ \t]+#+\s*$", "", heading.group(2))
        return _plain_inline(title)

    bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
    if bullet:
        return f"{bullet.group(1)}• {_plain_inline(bullet.group(2))}"

    ordered = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
    if ordered:
        return f"{ordered.group(1)}{ordered.group(2)}. {_plain_inline(ordered.group(3))}"

    return _plain_inline(line)


def _plain_inline(text: str) -> str:
    codes: List[str] = []

    def stash_code(match: re.Match) -> str:
        codes.append(match.group(1))
        return f"\ue000IN{len(codes) - 1}\ue001"

    text = re.sub(r"`([^`\n]+)`", stash_code, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)

    def replace_link(match: re.Match) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip().strip("'\"")
        if not label or label == url:
            return url
        return f"{label} ({url})"

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", replace_link, text)
    for index, code in enumerate(codes):
        text = text.replace(f"\ue000IN{index}\ue001", code)
    return text


def soften_long_tokens(markup: str, limit: int = 48) -> str:
    """Insert wrap points into long tokens so chat text stays inside the bubble.

    Markup tags, including href attributes, are left unchanged.
    """

    def break_token(token: str) -> str:
        if len(token) < limit:
            return token
        token = re.sub(r"([/\\_.\-:&#])", "\\1\u200b", token)
        pieces = []
        for piece in token.split("\u200b"):
            if len(piece) <= 24:
                pieces.append(piece)
            else:
                pieces.append("\u200b".join(piece[i:i + 24] for i in range(0, len(piece), 24)))
        return "\u200b".join(pieces)

    def soften_text(text: str) -> str:
        return re.sub(r"\S+", lambda match: break_token(match.group(0)), text)

    parts = re.split(r"(<[^>]+>)", markup)
    return "".join(part if part.startswith("<") else soften_text(part) for part in parts)


class ChatContentLabel(QLabel):
    """Word-wrapped message text that cannot force the chat panel wider."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return super().heightForWidth(max(40, width))

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def sizeHint(self) -> QSize:
        width = self.width() if self.width() > 40 else 280
        height = self.heightForWidth(width)
        if height < 1:
            height = super().sizeHint().height()
        return QSize(0, height)


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


class DraculaHighlighter(QSyntaxHighlighter):
    """Clean Dracula-themed syntax highlighter for code blocks."""

    def __init__(self, document, language: str = ""):
        super().__init__(document)
        self.rules = []
        lang = (language or "").lower()

        # Keyword format (Dracula pink #ff79c6)
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#ff79c6"))
        kw_fmt.setFontWeight(QFont.Weight.Bold)

        # Builtin/Type format (Dracula cyan #8be9fd)
        builtin_fmt = QTextCharFormat()
        builtin_fmt.setForeground(QColor("#8be9fd"))

        # String format (Dracula yellow #f1fa8c)
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#f1fa8c"))

        # Comment format (Dracula comment grey #6272a4)
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor("#6272a4"))
        comment_fmt.setFontItalic(True)

        # Number format (Dracula purple #bd93f9)
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor("#bd93f9"))

        # Numbers
        self.rules.append((re.compile(r"\b\d+(\.\d+)?\b"), num_fmt))

        # Strings: double, single, backticks
        self.rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), str_fmt))
        self.rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), str_fmt))

        # Comments
        self.rules.append((re.compile(r"//[^\n]*"), comment_fmt))
        self.rules.append((re.compile(r"#[^\n]*"), comment_fmt))

        # Common keywords across languages (Python, OpenSCAD, JS/TS, Bash, C/C++)
        keywords = [
            "def", "class", "import", "from", "return", "if", "elif", "else",
            "while", "for", "in", "try", "except", "finally", "with", "as",
            "lambda", "yield", "pass", "break", "continue", "raise", "assert",
            "True", "False", "None", "async", "await",
            # OpenSCAD
            "module", "function", "difference", "union", "intersection",
            "linear_extrude", "rotate_extrude", "translate", "rotate", "scale",
            "mirror", "multmatrix", "color", "offset", "hull", "minkowski",
            # JS/TS
            "const", "let", "var", "new", "typeof", "instanceof",
            "export", "default", "null", "undefined",
            # Bash / Shell
            "echo", "exit", "then", "fi", "do", "done", "case", "esac",
            "sudo", "cat", "grep", "sed", "awk", "chmod", "curl", "wget",
        ]
        kw_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b")
        self.rules.append((kw_re, kw_fmt))

        # Builtins & primitives
        builtins = [
            "print", "len", "range", "str", "int", "float", "dict", "list", "set",
            "open", "type", "sum", "min", "max",
            # OpenSCAD primitives & special variables
            "cube", "cylinder", "sphere", "polyhedron", "circle", "square", "polygon", "text",
            r"\$fn", r"\$fa", r"\$fs", r"\$t", r"\$vpr", r"\$vpt", r"\$vpd",
            # JS / DOM
            "console", "document", "window", "Math", "JSON", "Promise", "Array", "Object"
        ]
        builtin_re = re.compile(r"\b(" + "|".join(builtins) + r")\b")
        self.rules.append((builtin_re, builtin_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span()
                self.setFormat(start, end - start, fmt)


def parse_markdown_segments(text: str) -> List[Dict[str, str]]:
    """
    Parse markdown into alternating segments of text and fenced code blocks.
    Tolerates leading spaces on fences (e.g. from nested markdown lists/quotes)
    and dedents code blocks cleanly.
    """
    if not text:
        return []

    pattern = re.compile(
        r"(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n([\s\S]*?)(?:^[ \t]*)?```",
        re.MULTILINE
    )

    segments = []
    last_idx = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_idx:
            segments.append({"type": "text", "content": text[last_idx:start]})

        lang = (match.group(1) or "").strip()
        raw_code = match.group(2)
        clean_code = textwrap.dedent(raw_code.strip("\n"))
        segments.append({
            "type": "code",
            "lang": lang,
            "code": clean_code
        })
        last_idx = end

    if last_idx < len(text):
        segments.append({"type": "text", "content": text[last_idx:]})

    return segments


class CodeBlockWidget(QFrame):
    """
    Dedicated code block box widget for chat bubbles.
    Features:
    - Distinct Dracula dark box styling with rounded corners and subtle border
    - Top header bar with language badge and a dedicated '📋 Copy' button
    - Visual feedback on copy: changes to '✓ Copied!' for 2 seconds
    - Clean monospace editor with syntax styling and horizontal scrolling
    - Direct clipboard copy of clean code
    """
    signal_copied = pyqtSignal(str)

    def __init__(self, code: str, language: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.raw_code = code
        self.language = (language or "").strip()
        self._init_ui()

    def _init_ui(self):
        self.setObjectName("CodeBlockFrame")
        self.setStyleSheet(
            "QFrame#CodeBlockFrame { background-color: #14151b; border: 1px solid #44475a; "
            "border-radius: 8px; margin: 4px 0px; }\n"
            f"{GLOBAL_TOOLTIP_STYLE}"
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar
        header_widget = QWidget(self)
        header_widget.setStyleSheet(
            "QWidget { background-color: #181a24; border-top-left-radius: 7px; "
            "border-top-right-radius: 7px; border-bottom: 1px solid #44475a; }"
        )
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(10, 4, 8, 4)
        header_layout.setSpacing(8)

        display_lang = self.language.upper() if self.language else "CODE"
        lbl_lang = QLabel(
            f"💻 <span style='color: #bd93f9; font-weight: bold; font-family: monospace; font-size: 11px;'>{display_lang}</span>",
            header_widget
        )
        lbl_lang.setStyleSheet("border: none; background: transparent;")
        header_layout.addWidget(lbl_lang)
        header_layout.addStretch(1)

        self.btn_copy = QPushButton("📋 Copy", header_widget)
        self.btn_copy.setToolTip("Copy code snippet to clipboard")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 2px 8px; "
            "font-size: 11px; font-weight: bold; font-family: sans-serif; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )
        self.btn_copy.clicked.connect(self._copy_code)
        header_layout.addWidget(self.btn_copy)
        main_layout.addWidget(header_widget)

        # Code text edit
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(self.raw_code)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #14151b; color: #50fa7b; border: none; "
            "border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; "
            "padding: 8px 10px; font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', 'Consolas', monospace; "
            "font-size: 12px; selection-background-color: #44475a; selection-color: #f8f8f2; }"
        )

        # Syntax highlighter
        self.highlighter = DraculaHighlighter(self.text_edit.document(), self.language)

        # Calculate optimal height based on line count
        fm = self.text_edit.fontMetrics()
        line_spacing = max(fm.lineSpacing(), 18)
        line_count = len(self.raw_code.splitlines()) or 1
        target_height = min(max(line_count * line_spacing + 24, 48), 380)
        self.text_edit.setFixedHeight(target_height)

        if target_height >= 380:
            self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        main_layout.addWidget(self.text_edit)

    def _copy_code(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.raw_code)
        self.btn_copy.setText("✓ Copied!")
        self.btn_copy.setStyleSheet(
            "QPushButton { background-color: #50fa7b; color: #1e1f29; "
            "border: 1px solid #50fa7b; border-radius: 4px; padding: 2px 8px; "
            "font-size: 11px; font-weight: bold; font-family: sans-serif; }"
        )
        self.signal_copied.emit(self.raw_code)
        QTimer.singleShot(2000, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self.btn_copy.setText("📋 Copy")
        self.btn_copy.setStyleSheet(
            "QPushButton { background-color: #282a36; color: #f8f8f2; "
            "border: 1px solid #44475a; border-radius: 4px; padding: 2px 8px; "
            "font-size: 11px; font-weight: bold; font-family: sans-serif; } "
            "QPushButton:hover { background-color: #44475a; color: #50fa7b; border-color: #50fa7b; }"
        )


class MessageBubble(QFrame):
    """Single message bubble inside the scrollable chat container."""

    signal_copy_clicked = pyqtSignal(str)
    signal_edit_submitted = pyqtSignal(str, str)  # (message_id, new_content)
    signal_retry_requested = pyqtSignal(str)      # prompt/text to retry
    signal_replay_tts_requested = pyqtSignal(str) # text to replay via TTS
    signal_delete_requested = pyqtSignal(object)  # bubble instance
    signal_copy_to_input = pyqtSignal(str)        # text to copy to prompt bar
    signal_link_clicked = pyqtSignal(str)         # URL or file path clicked in chat

    def __init__(
        self,
        role: str,
        content: str,
        title: Optional[str] = None,
        message_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy message as shown", lambda: self._copy_to_clipboard(self.btn_copy))
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

            self.btn_copy = self._create_action_btn("📋 Copy", "Copy response as shown", lambda: self._copy_to_clipboard(self.btn_copy))
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

            self.btn_speak = self._create_action_btn("🔊 Audio", "Read reasoning aloud (TTS)", self.request_replay_tts)
            header_layout.addWidget(self.btn_speak)

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

        # Message Content Container (holds text labels and CodeBlockWidget instances)
        self.content_container = QWidget(self)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)

        # Primary label reference (for backwards compatibility)
        self.content_label = ChatContentLabel(self.content_container)
        self.content_label.setOpenExternalLinks(False)
        self.content_label.linkActivated.connect(self._handle_link_clicked)
        self.content_label.linkHovered.connect(self._handle_link_hovered)

        self._render_message_content(self.raw_content)

        layout.addWidget(self.content_container)

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

    def _render_message_content(self, text: str):
        """Render message text into rich labels and dedicated code block widgets."""
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if self.role == "thought":
            lbl = ChatContentLabel(self.content_container)
            lbl.setStyleSheet("color: #e0e0e0; font-size: 12px; font-style: italic;")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setText(soften_long_tokens(text or ""))
            self.content_label = lbl
            self.content_layout.addWidget(lbl)
            return

        if self.role == "tool":
            lbl = ChatContentLabel(self.content_container)
            lbl.setStyleSheet("color: #f8f8f2; font-family: monospace; font-size: 11px;")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setText(soften_long_tokens(self._tool_markup(text or "")))
            self.content_label = lbl
            self.content_layout.addWidget(lbl)
            return

        if self.role == "error":
            lbl = ChatContentLabel(self.content_container)
            lbl.setStyleSheet("color: #ff5555; font-size: 12px;")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setText(soften_long_tokens(text or ""))
            self.content_label = lbl
            self.content_layout.addWidget(lbl)
            return

        # Assistant or User message with rich markdown and code blocks
        segments = parse_markdown_segments(text)
        self.code_snippets = [seg["code"] for seg in segments if seg.get("type") == "code"]

        first_label = None
        has_code = False
        for seg in segments:
            if seg.get("type") == "text":
                content = seg.get("content", "")
                if not content.strip():
                    continue
                lbl = ChatContentLabel(self.content_container)
                lbl.setOpenExternalLinks(False)
                lbl.linkActivated.connect(self._handle_link_clicked)
                lbl.linkHovered.connect(self._handle_link_hovered)
                lbl.setStyleSheet("color: #f8f8f2; font-size: 13px; line-height: 1.4;")
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setText(soften_long_tokens(markdown_to_chat_html(content)))
                self.content_layout.addWidget(lbl)
                if first_label is None:
                    first_label = lbl
            elif seg.get("type") == "code":
                has_code = True
                code_widget = CodeBlockWidget(
                    code=seg.get("code", ""),
                    language=seg.get("lang", ""),
                    parent=self.content_container,
                )
                self.content_layout.addWidget(code_widget)

        if first_label is not None:
            self.content_label = first_label
        else:
            # Fallback if message is empty or consists purely of code blocks
            lbl = ChatContentLabel(self.content_container)
            lbl.setOpenExternalLinks(False)
            lbl.linkActivated.connect(self._handle_link_clicked)
            lbl.linkHovered.connect(self._handle_link_hovered)
            lbl.setStyleSheet("color: #f8f8f2; font-size: 13px; line-height: 1.4;")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setText(soften_long_tokens(markdown_to_chat_html(text or "")))
            self.content_label = lbl
            if not has_code:
                self.content_layout.addWidget(lbl)

    def start_editing(self):
        """Switch bubble into inline editing mode."""
        self.is_editing = True
        self.content_container.setVisible(False)
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
        self.content_container.setVisible(True)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def _show_content(self, markup: str, *, rich: bool = True) -> None:
        if hasattr(self, "content_label") and self.content_label:
            text = soften_long_tokens(markup or "")
            if rich:
                self.content_label.setTextFormat(Qt.TextFormat.RichText)
            else:
                self.content_label.setTextFormat(Qt.TextFormat.PlainText)
            self.content_label.setText(text)

    def _tool_markup(self, text: str) -> str:
        escaped = html.escape(text, quote=False)
        return linkify_escaped_text(escaped, color="#50fa7b").replace("\n", "<br/>")

    def _handle_link_clicked(self, url: str):
        """Handle link activation by user click, emit signal, and open in default app/browser."""
        if url.startswith("copy-code:"):
            try:
                idx = int(url.split("copy-code:")[1])
                if hasattr(self, "code_snippets") and 0 <= idx < len(self.code_snippets):
                    code_to_copy = self.code_snippets[idx]
                    clipboard = QApplication.clipboard()
                    if clipboard:
                        clipboard.setText(code_to_copy)
                    logger.info("Copied code snippet %d (%d chars) to clipboard", idx, len(code_to_copy))
                    if hasattr(self, "content_label") and self.content_label:
                        self.content_label.setToolTip("✓ Code copied to clipboard!")
                        QTimer.singleShot(2000, lambda: self.content_label.setToolTip(""))
                    return
            except Exception as exc:
                logger.warning("Error copying code from link: %s", exc)
                return

        logger.info("MessageBubble link clicked: %s", url)
        self.signal_link_clicked.emit(url)
        open_chat_link(url)

    def _handle_link_hovered(self, url: str):
        """Show destination URL/path as hover tooltip when hovering over links."""
        if url:
            self.content_label.setToolTip(f"Open: {url}")
        else:
            self.content_label.setToolTip("")

    def save_editing(self):
        """Save edited content, update label, and emit edit signal."""
        new_text = self.edit_text_area.toPlainText().strip()
        if not new_text:
            self.cancel_editing()
            return
        self.raw_content = new_text
        self.is_editing = False
        self.edit_container.setVisible(False)
        self._render_message_content(new_text)
        self.content_container.setVisible(True)
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
            self.btn_speak.setText("🔊 Audio")

    def request_delete(self):
        """Request deletion of this message."""
        self.signal_delete_requested.emit(self)

    def _copy_to_clipboard(self, button: Optional[QPushButton] = None):
        clipboard = QApplication.clipboard()
        if clipboard:
            text = self.raw_content or ""
            if self.role in ("user", "assistant"):
                text = markdown_to_plain_text(text)
            clipboard.setText(text)
            if button:
                button.setText("✓ Copied")
                button.setStyleSheet(
                    "QPushButton { background-color: #50fa7b; color: #282a36; font-weight: bold; "
                    "border-radius: 4px; padding: 2px 6px; font-size: 11px; border: none; }"
                )
                QTimer.singleShot(1500, lambda: self._reset_copy_button(button))
            self.signal_copy_clicked.emit(text)

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

        act_copy = menu.addAction("📋 Copy Message as Shown")
        act_copy.triggered.connect(lambda: self._copy_to_clipboard(self.btn_copy))

        act_copy_input = menu.addAction("📝 Copy to Prompt Input")
        act_copy_input.triggered.connect(lambda: self.signal_copy_to_input.emit(self.raw_content))

        if self.role in ("assistant", "thought"):
            act_speak = menu.addAction("🔊 Play Voice (TTS)")
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
    signal_link_clicked = pyqtSignal(str)       # link or file path clicked in chat

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
        self.container.setMinimumWidth(0)
        self.container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_container_width()

    def _fit_container_width(self) -> None:
        width = self.viewport().width()
        if width > 0:
            self.container.setMaximumWidth(width)

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
        bubble.signal_link_clicked.connect(self.signal_link_clicked.emit)

        self.bubbles.append(bubble)
        self.layout_messages.addWidget(bubble)
        self._fit_container_width()
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
