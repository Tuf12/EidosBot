"""Shared theme styles and global Qt styling for EidosBot."""

from typing import Optional
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

GLOBAL_TOOLTIP_STYLE = (
    "QToolTip { "
    "background-color: #21222c; "
    "color: #f8f8f2; "
    "border: 1px solid #6272a4; "
    "border-radius: 4px; "
    "padding: 4px 8px; "
    "font-size: 11px; "
    "font-family: sans-serif; "
    "font-weight: 500; "
    "}"
)


def apply_global_styles(app: Optional[QApplication] = None) -> None:
    """Apply high-contrast Dracula dark theme tooltip and global styles to QApplication."""
    if app is None:
        app = QApplication.instance()
    if app is None:
        return

    # 1. Update application palette so OS/native theme integration respects tooltip colors
    try:
        palette = app.palette()
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#21222c"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f8f8f2"))
        app.setPalette(palette)
    except Exception:
        pass

    # 2. Inject QToolTip stylesheet rule into global QApplication stylesheet
    existing = app.styleSheet() or ""
    if "QToolTip" not in existing:
        app.setStyleSheet((existing + "\n" + GLOBAL_TOOLTIP_STYLE).strip())
