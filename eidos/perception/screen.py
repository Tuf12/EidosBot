"""Screen perception module for X11 desktop captures."""

import base64
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import mss
from PIL import Image

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Fast screen capture utility leveraging X11 SHM via mss."""

    def __init__(self, default_monitor: int = 1, scale_factor: float = 1.0):
        self.default_monitor = default_monitor
        self.scale_factor = scale_factor
        self._sct = None

    def _get_sct(self) -> Any:
        if self._sct is None:
            # Support both mss.MSS (newer) and mss.mss (legacy)
            if hasattr(mss, "MSS"):
                self._sct = mss.MSS()
            else:
                self._sct = mss.mss()
        return self._sct

    def get_monitors(self) -> List[Dict[str, int]]:
        """Return list of available monitors and coordinates."""
        sct = self._get_sct()
        return [dict(m) for m in sct.monitors]

    def capture(
        self,
        monitor_index: Optional[int] = None,
        scale: Optional[float] = None,
        region: Optional[Tuple[int, int, int, int]] = None  # (left, top, width, height)
    ) -> Image.Image:
        """
        Capture desktop screen.
        If region is provided, captures specific bbox (left, top, width, height).
        Otherwise captures selected monitor (default: 1, which is primary monitor).
        """
        sct = self._get_sct()
        idx = monitor_index if monitor_index is not None else self.default_monitor

        if region is not None:
            left, top, width, height = region
            bbox = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        else:
            if idx >= len(sct.monitors):
                idx = 0  # Fallback to all-screen if monitor index exceeds count
            bbox = sct.monitors[idx]

        sct_img = sct.grab(bbox)
        # Convert raw BGRA to PIL Image RGB
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # Apply downscaling if configured
        scale_val = scale if scale is not None else self.scale_factor
        if scale_val != 1.0 and scale_val > 0:
            new_w = max(1, int(img.width * scale_val))
            new_h = max(1, int(img.height * scale_val))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        return img

    @staticmethod
    def to_base64(image: Image.Image, format: str = "JPEG", quality: int = 85) -> str:
        """Convert PIL Image to base64 string for LLM vision consumption."""
        buffered = io.BytesIO()
        image.save(buffered, format=format, quality=quality)
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    @staticmethod
    def to_data_uri(image: Image.Image, format: str = "JPEG", quality: int = 85) -> str:
        """Convert PIL Image to data URI format (data:image/jpeg;base64,...)."""
        b64 = ScreenCapture.to_base64(image, format=format, quality=quality)
        mime = f"image/{format.lower()}"
        return f"data:{mime};base64,{b64}"

    def close(self):
        """Clean up mss resources."""
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
