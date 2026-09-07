"""Cursor IDE automation adapter for desktop workflows."""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from eidos.actions.mouse_keyboard import InputSimulator
from eidos.perception.window_info import WindowDetector

logger = logging.getLogger(__name__)


class CursorAdapter:
    """
    Adapter for controlling and automating Cursor IDE via:
    1. CLI invocations (`cursor --goto`, `cursor -d`, workspace opening)
    2. Window focusing via X11 (`WindowDetector`)
    3. Keyboard shortcut automation (Command Palette `Ctrl+Shift+P`)
    """

    def __init__(
        self,
        binary_path: Optional[str] = None,
        window_detector: Optional[WindowDetector] = None,
        input_simulator: Optional[InputSimulator] = None,
    ):
        self.binary_path = binary_path or shutil.which("cursor") or "cursor"
        self.windows = window_detector or WindowDetector()
        self.inputs = input_simulator or InputSimulator()

    def is_installed(self) -> bool:
        """Check if cursor binary is found on PATH."""
        return shutil.which(self.binary_path) is not None

    def is_running(self) -> bool:
        """Check if Cursor is running and has an open window."""
        try:
            res = subprocess.run(
                ["xdotool", "search", "--class", "Cursor"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            return False

    def get_window_id(self) -> Optional[int]:
        """Find the main Cursor X11 window ID."""
        try:
            res = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", "Cursor"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                ids = res.stdout.strip().split()
                if ids:
                    return int(ids[0])
        except Exception as e:
            logger.debug("Failed to find Cursor window ID: %s", e)
        return None

    def focus_window(self) -> bool:
        """Bring Cursor IDE window to the foreground."""
        wid = self.get_window_id()
        if wid:
            return self.windows.activate_window(wid)
        return False

    def open_file(
        self,
        file_path: str,
        line: Optional[int] = None,
        col: Optional[int] = None,
        reuse_window: bool = True,
    ) -> Dict[str, Any]:
        """
        Open a file in Cursor at a specific line and column.
        Equivalent to: cursor -g file:line:col
        """
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File does not exist: {abs_path}"}

        target_arg = abs_path
        if line is not None:
            target_arg += f":{line}"
            if col is not None:
                target_arg += f":{col}"

        cmd = [self.binary_path]
        if reuse_window:
            cmd.append("-r")
        cmd.extend(["-g", target_arg])

        try:
            subprocess.Popen(cmd)
            time.sleep(0.3)
            self.focus_window()
            return {"success": True, "target": target_arg}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_workspace(self, folder_path: str, new_window: bool = False) -> Dict[str, Any]:
        """Open a folder/workspace in Cursor."""
        abs_path = os.path.abspath(os.path.expanduser(folder_path))
        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"Directory does not exist: {abs_path}"}

        cmd = [self.binary_path]
        if new_window:
            cmd.append("-n")
        else:
            cmd.append("-r")
        cmd.append(abs_path)

        try:
            subprocess.Popen(cmd)
            time.sleep(0.5)
            self.focus_window()
            return {"success": True, "workspace": abs_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_diff(self, file_a: str, file_b: str) -> Dict[str, Any]:
        """Open a side-by-side diff in Cursor."""
        path_a = os.path.abspath(os.path.expanduser(file_a))
        path_b = os.path.abspath(os.path.expanduser(file_b))

        cmd = [self.binary_path, "-r", "-d", path_a, path_b]
        try:
            subprocess.Popen(cmd)
            time.sleep(0.3)
            self.focus_window()
            return {"success": True, "file_a": path_a, "file_b": path_b}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_command_palette(self, command_name: str) -> Dict[str, Any]:
        """
        Focus Cursor, open Command Palette (Ctrl+Shift+P), type command, and press Enter.
        """
        if not self.focus_window():
            return {"success": False, "error": "Cursor is not running or could not be focused."}

        time.sleep(0.2)
        # 1. Trigger Ctrl+Shift+P
        self.inputs.hotkey("ctrl", "shift", "p")
        time.sleep(0.25)

        # 2. Type command name
        self.inputs.type_text(command_name, delay=0.01)
        time.sleep(0.25)

        # 3. Hit Enter
        self.inputs.press_key("enter")
        return {"success": True, "command": command_name}
