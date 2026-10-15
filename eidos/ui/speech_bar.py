"""Compact speech player for the desktop pill, matching the phone player bar."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

_BTN = (
    "QPushButton { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; "
    "border-radius: 6px; padding: 2px 8px; font-size: 11px; } "
    "QPushButton:hover { background-color: #44475a; }"
)
_PLAY = (
    "QPushButton { background-color: #bd93f9; color: #1e1f29; font-weight: bold; "
    "border: none; border-radius: 6px; padding: 2px 10px; font-size: 11px; } "
    "QPushButton:hover { background-color: #ff79c6; }"
)
_STOP_ON = (
    "QPushButton { background-color: transparent; color: #ff5555; border: none; "
    "border-radius: 6px; padding: 2px 6px; font-size: 12px; } "
    "QPushButton:hover { background-color: rgba(255, 85, 85, 0.2); }"
)
_STOP_OFF = (
    "QPushButton { background-color: transparent; color: rgba(98, 114, 164, 0.45); "
    "border: none; border-radius: 6px; padding: 2px 6px; font-size: 12px; }"
)


def _clock(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


class SpeechPlayerBar(QWidget):
    """Play, pause, five-second seek, and stop for the current read-aloud."""

    toggle_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet("background-color: transparent; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(3)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar { background-color: rgba(68, 71, 90, 0.7); border: none; border-radius: 2px; } "
            "QProgressBar::chunk { background-color: #bd93f9; border-radius: 2px; }"
        )
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.btn_toggle = QPushButton("Play")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setStyleSheet(_PLAY)
        self.btn_toggle.clicked.connect(self.toggle_requested.emit)
        row.addWidget(self.btn_toggle)

        self.lbl_status = QLabel("Finished")
        self.lbl_status.setStyleSheet("color: #f8f8f2; font-size: 11px;")
        row.addWidget(self.lbl_status)

        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setStyleSheet("color: #6272a4; font-size: 11px;")
        row.addWidget(self.lbl_time)
        row.addStretch(1)

        self.btn_back = QPushButton("-5s")
        self.btn_forward = QPushButton("+5s")
        for button, delta, tip in (
            (self.btn_back, -5, "Rewind 5 seconds"),
            (self.btn_forward, 5, "Forward 5 seconds"),
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_BTN)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, step=delta: self.seek_requested.emit(step))
            row.addWidget(button)

        self.btn_stop = QPushButton("■")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.btn_stop)

        layout.addLayout(row)
        self.apply({"active": False})

    def apply(self, snap: dict) -> None:
        active = bool(snap.get("active"))
        self.setVisible(active)
        if not active:
            return
        playing = bool(snap.get("playing"))
        paused = bool(snap.get("paused"))
        buffering = bool(snap.get("buffering"))
        position = float(snap.get("position") or 0)
        duration = float(snap.get("duration") or 0)
        engaged = playing or paused or buffering
        if buffering:
            status = "Loading speech…"
        elif playing:
            status = "Playing voice"
        elif paused:
            status = "Paused"
        else:
            status = "Finished"
        self.lbl_status.setText(status)
        self.lbl_time.setText(f"{_clock(position)} / {_clock(duration)}")
        fraction = 0 if duration <= 0 else max(0.0, min(1.0, position / duration))
        self.progress.setValue(int(fraction * 1000))
        self.btn_toggle.setText("Pause" if playing else "Play")
        self.btn_toggle.setToolTip("Pause" if playing else "Play")
        self.btn_stop.setEnabled(engaged)
        self.btn_stop.setStyleSheet(_STOP_ON if engaged else _STOP_OFF)
        self.btn_stop.setToolTip("Stop playback" if engaged else "Playback finished")
