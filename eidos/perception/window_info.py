"""X11 active window and desktop process perception."""

import logging
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class WindowDetector:
    """Queries X11 desktop state using xdotool and xprop."""

    def __init__(self):
        self.has_xdotool = shutil.which("xdotool") is not None
        self.has_xprop = shutil.which("xprop") is not None
        if not self.has_xdotool or not self.has_xprop:
            logger.warning("xdotool or xprop missing! Window perception may be limited.")

    def get_active_window_id(self) -> Optional[int]:
        """Return the window ID of the currently focused window."""
        if not self.has_xdotool:
            return None
        try:
            res = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0 and res.stdout.strip():
                return int(res.stdout.strip())
        except Exception as e:
            logger.debug("Failed to get active window ID: %s", e)
        return None

    def get_window_title(self, window_id: int) -> str:
        """Return title of the given window ID."""
        if not self.has_xdotool:
            return ""
        try:
            res = subprocess.run(
                ["xdotool", "getwindowname", str(window_id)],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return ""

    def get_window_geometry(self, window_id: int) -> Dict[str, int]:
        """Return dict with x, y, width, height for window ID."""
        geom = {"x": 0, "y": 0, "width": 0, "height": 0}
        if not self.has_xdotool:
            return geom

        try:
            res = subprocess.run(
                ["xdotool", "getwindowgeometry", str(window_id)],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                pos_match = re.search(r"Position:\s*(\d+),(\d+)", res.stdout)
                size_match = re.search(r"Geometry:\s*(\d+)x(\d+)", res.stdout)
                if pos_match:
                    geom["x"] = int(pos_match.group(1))
                    geom["y"] = int(pos_match.group(2))
                if size_match:
                    geom["width"] = int(size_match.group(1))
                    geom["height"] = int(size_match.group(2))
        except Exception as e:
            logger.debug("Failed to get geometry for window %s: %s", window_id, e)

        return geom

    def get_window_attributes(self, window_id: int) -> Dict[str, Any]:
        """Extract WM_CLASS, PID, and WM_NAME via xprop."""
        attrs = {"class_name": "", "instance_name": "", "pid": None}
        if not self.has_xprop:
            return attrs

        try:
            res = subprocess.run(
                ["xprop", "-id", str(window_id), "WM_CLASS", "_NET_WM_PID", "_NET_WM_NAME"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                output = res.stdout
                # WM_CLASS(STRING) = "optimalx-desktop", "optimalx-desktop"
                class_match = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]*)",\s*"([^"]*)"', output)
                if class_match:
                    attrs["instance_name"] = class_match.group(1)
                    attrs["class_name"] = class_match.group(2)

                # _NET_WM_PID(CARDINAL) = 3372668
                pid_match = re.search(r'_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)', output)
                if pid_match:
                    attrs["pid"] = int(pid_match.group(1))
        except Exception as e:
            logger.debug("Failed to extract xprop attributes for window %s: %s", window_id, e)

        return attrs

    def get_active_window_info(self) -> Dict[str, Any]:
        """Aggregate active window metadata into a single dictionary."""
        wid = self.get_active_window_id()
        if wid is None:
            return {
                "window_id": None,
                "title": "",
                "class_name": "",
                "instance_name": "",
                "geometry": {"x": 0, "y": 0, "width": 0, "height": 0},
                "pid": None
            }

        title = self.get_window_title(wid)
        geom = self.get_window_geometry(wid)
        attrs = self.get_window_attributes(wid)

        return {
            "window_id": wid,
            "title": title,
            "class_name": attrs.get("class_name", ""),
            "instance_name": attrs.get("instance_name", ""),
            "geometry": geom,
            "pid": attrs.get("pid")
        }

    def activate_window(self, window_id: int) -> bool:
        """Bring window to foreground."""
        if not self.has_xdotool:
            return False
        try:
            res = subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(window_id)],
                capture_output=True,
                timeout=2.0
            )
            return res.returncode == 0
        except Exception:
            return False
