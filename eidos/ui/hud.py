"""PyQt6 Transparent Click-Through Overlay for visual reticles and bounding boxes."""

import logging
from typing import List, Optional, Tuple
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class HUDOverlay(QWidget):
    """
    Transparent, click-through, always-on-top canvas covering the desktop screen.
    Renders visual feedback (e.g. click reticles, bounding box highlights)
    without intercepting user or synthetic mouse events.
    """

    # Thread-safe signals to update visual elements from background agent threads
    signal_show_click = pyqtSignal(int, int, str, int)  # x, y, color, duration_ms
    signal_show_bbox = pyqtSignal(int, int, int, int, str, str, int)  # x, y, w, h, label, color, duration_ms
    signal_show_alert = pyqtSignal(str, int)  # alert_type ('kill', 'warning'), duration_ms
    signal_clear = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Active visual annotations
        self._click_target: Optional[Tuple[int, int, QColor, float]] = None  # x, y, color, radius
        self._bounding_boxes: List[Tuple[QRect, str, QColor]] = []
        self._alert_border: Optional[QColor] = None

        # Timers
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._clear_click_target)

        self._bbox_timer = QTimer(self)
        self._bbox_timer.setSingleShot(True)
        self._bbox_timer.timeout.connect(self._clear_bboxes)

        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.timeout.connect(self._clear_alert)

        self._init_window()
        self._connect_signals()

    def _init_window(self):
        # Configure frameless, stay on top, transparent background, and click-through
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # Cover entire primary desktop screen
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())

    def _connect_signals(self):
        self.signal_show_click.connect(self.show_click_target)
        self.signal_show_bbox.connect(self.show_bounding_box)
        self.signal_show_alert.connect(self.show_alert)
        self.signal_clear.connect(self.clear_all)

    @pyqtSlot(int, int, str, int)
    def show_click_target(self, x: int, y: int, color_hex: str = "#ff5555", duration_ms: int = 350):
        """Draw an animated crosshair reticle at (x, y)."""
        color = QColor(color_hex)
        self._click_target = (x, y, color, 24.0)
        self.update()
        self._click_timer.start(duration_ms)

    @pyqtSlot(int, int, int, int, str, str, int)
    def show_bounding_box(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str = "",
        color_hex: str = "#50fa7b",
        duration_ms: int = 1000,
    ):
        """Draw highlighted bounding box with optional label."""
        color = QColor(color_hex)
        rect = QRect(x, y, width, height)
        self._bounding_boxes.append((rect, label, color))
        self.update()
        self._bbox_timer.start(duration_ms)

    @pyqtSlot(str, int)
    def show_alert(self, alert_type: str = "kill", duration_ms: int = 1500):
        """Display full-screen pulsing border for emergency stop or critical alert."""
        if alert_type == "kill":
            self._alert_border = QColor(255, 30, 30, 200)
        else:
            self._alert_border = QColor(255, 184, 108, 180)
        self.update()
        self._alert_timer.start(duration_ms)

    @pyqtSlot()
    def clear_all(self):
        """Clear all active HUD annotations."""
        self._clear_click_target()
        self._clear_bboxes()
        self._clear_alert()

    def _clear_click_target(self):
        self._click_target = None
        self.update()

    def _clear_bboxes(self):
        self._bounding_boxes.clear()
        self.update()

    def _clear_alert(self):
        self._alert_border = None
        self.update()

    def paintEvent(self, event):
        """Paint HUD elements onto the transparent overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Alert Border (e.g. Kill Switch triggered)
        if self._alert_border is not None:
            pen = QPen(self._alert_border, 8)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(4, 4, -4, -4))

        # 2. Bounding Boxes
        font = QFont("Sans-Serif", 10, QFont.Weight.Bold)
        painter.setFont(font)

        for rect, label, color in self._bounding_boxes:
            # Box border
            pen = QPen(color, 2)
            painter.setPen(pen)
            fill_color = QColor(color.red(), color.green(), color.blue(), 35)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(rect, 4, 4)

            # Label badge
            if label:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                text_rect = QRect(rect.left(), max(0, rect.top() - 20), len(label) * 9 + 12, 18)
                painter.drawRoundedRect(text_rect, 3, 3)

                painter.setPen(QColor(20, 20, 20))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

        # 3. Click Reticle Target
        if self._click_target is not None:
            x, y, color, radius = self._click_target

            # Outer ring
            pen = QPen(color, 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(x, y), int(radius), int(radius))

            # Inner pulsing dot
            inner_color = QColor(color.red(), color.green(), color.blue(), 200)
            painter.setBrush(inner_color)
            painter.drawEllipse(QPoint(x, y), 5, 5)

            # Crosshairs
            painter.setPen(QPen(color, 1.5))
            cross_len = 10
            painter.drawLine(x - int(radius) - cross_len, y, x - int(radius), y)
            painter.drawLine(x + int(radius), y, x + int(radius) + cross_len, y)
            painter.drawLine(x, y - int(radius) - cross_len, x, y - int(radius))
            painter.drawLine(x, y + int(radius), x, y + int(radius) + cross_len)

        painter.end()
