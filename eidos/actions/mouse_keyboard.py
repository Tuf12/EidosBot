"""Synthetic mouse and keyboard actions for desktop UI automation."""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import pyautogui
from eidos.safety.gatekeeper import Gatekeeper

logger = logging.getLogger(__name__)

# Keys the phone keyboard bar is allowed to press on the desktop.
_RELAY_KEYS = {
    "enter": "enter",
    "return": "enter",
    "backspace": "backspace",
    "tab": "tab",
    "esc": "esc",
    "escape": "esc",
    "space": "space",
    "delete": "delete",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
}

# Configure PyAutoGUI safety settings
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class InputSimulator:
    """Simulates hardware mouse clicks, movement, and keyboard typing."""

    def __init__(self, gatekeeper: Optional[Gatekeeper] = None):
        self.gatekeeper = gatekeeper or Gatekeeper()

    def _check_safety(self, action_type: str, details: Dict[str, Any]) -> Tuple[bool, str]:
        """Verify with gatekeeper and kill switch before injecting hardware input."""
        if self.gatekeeper.kill_switch.is_triggered():
            return False, "Aborted: Emergency kill switch active."
        return self.gatekeeper.evaluate(action_type, details)

    def move(self, x: int, y: int, duration: float = 0.1) -> Dict[str, Any]:
        """Move mouse pointer to (x, y)."""
        allowed, reason = self._check_safety("move_mouse", {"x": x, "y": y})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return {"success": True, "x": x, "y": y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click at (x, y)."""
        allowed, reason = self._check_safety("click", {"x": x, "y": y, "button": button, "clicks": clicks})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.click(x=x, y=y, clicks=clicks, button=button)
            return {"success": True, "x": x, "y": y, "button": button, "clicks": clicks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def double_click(self, x: int, y: int) -> Dict[str, Any]:
        """Double click at (x, y)."""
        return self.click(x=x, y=y, clicks=2)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.2) -> Dict[str, Any]:
        """Click and drag from (start_x, start_y) to (end_x, end_y)."""
        allowed, reason = self._check_safety(
            "drag", {"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y}
        )
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.moveTo(start_x, start_y)
            pyautogui.dragTo(end_x, end_y, duration=duration, button="left")
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """Scroll vertical wheel. Positive = up, negative = down."""
        allowed, reason = self._check_safety("scroll", {"clicks": clicks, "x": x, "y": y})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.scroll(clicks, x=x, y=y)
            return {"success": True, "clicks": clicks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, delay: float = 0.01) -> Dict[str, Any]:
        """Type text character by character."""
        allowed, reason = self._check_safety("type_text", {"text": text})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.write(text, interval=delay)
            return {"success": True, "text": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def relay_typing(self, text: str = "", backspaces: int = 0, key: str = "") -> Dict[str, Any]:
        """Type a burst from the phone keyboard into the focused desktop field."""
        text = text or ""
        key_name = (key or "").lower().strip()
        if not isinstance(text, str) or backspaces < 0 or backspaces > 500 or len(text) > 4000:
            return {"success": False, "error": "Typing request is out of range"}
        if key_name and key_name not in _RELAY_KEYS:
            return {"success": False, "error": "Unsupported key"}
        if not text and backspaces == 0 and not key_name:
            return {"success": False, "error": "Nothing to type"}

        allowed, reason = self._check_safety(
            "type_text", {"text": text, "backspaces": backspaces, "key": key_name}
        )
        if not allowed:
            return {"success": False, "error": reason}
        try:
            for _ in range(backspaces):
                pyautogui.press("backspace", _pause=False)
            if text:
                self._inject_text(text)
            if key_name:
                pyautogui.press(_RELAY_KEYS[key_name], _pause=False)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _inject_text(self, text: str) -> None:
        """Type characters, using real Enter and Tab keys for line breaks."""
        from pynput.keyboard import Controller

        controller = Controller()
        chunk: List[str] = []

        def flush() -> None:
            if not chunk:
                return
            controller.type("".join(chunk))
            chunk.clear()

        for char in text:
            if char in "\r\n":
                flush()
                pyautogui.press("enter", _pause=False)
            elif char == "\t":
                flush()
                pyautogui.press("tab", _pause=False)
            else:
                chunk.append(char)
        flush()

    def press_key(self, key: str) -> Dict[str, Any]:
        """Press a single key (e.g., 'enter', 'tab', 'esc', 'space')."""
        allowed, reason = self._check_safety("press_key", {"key": key})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.press(key.lower())
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press a combination of keys (e.g., 'ctrl', 'c' or 'ctrl', 'alt', 't')."""
        normalized_keys = [k.lower().strip() for k in keys]
        allowed, reason = self._check_safety("hotkey", {"keys": normalized_keys})
        if not allowed:
            return {"success": False, "error": reason}
        try:
            pyautogui.hotkey(*normalized_keys)
            return {"success": True, "keys": normalized_keys}
        except Exception as e:
            return {"success": False, "error": str(e)}
