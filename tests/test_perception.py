"""Tests for EidosBot Perception Module."""

import pytest
from PIL import Image

from eidos.perception.screen import ScreenCapture
from eidos.perception.window_info import WindowDetector


def test_screen_capture_and_encoding():
    sc = ScreenCapture(scale_factor=0.5)
    monitors = sc.get_monitors()
    assert len(monitors) > 0

    img = sc.capture()
    assert isinstance(img, Image.Image)
    assert img.width > 0
    assert img.height > 0

    b64 = ScreenCapture.to_base64(img)
    assert isinstance(b64, str)
    assert len(b64) > 100

    uri = ScreenCapture.to_data_uri(img)
    assert uri.startswith("data:image/jpeg;base64,")

    sc.close()


def test_window_detector():
    detector = WindowDetector()
    info = detector.get_active_window_info()
    assert isinstance(info, dict)
    assert "window_id" in info
    assert "title" in info
    assert "geometry" in info
