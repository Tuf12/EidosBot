"""Global hardware kill switch for EidosBot (Ctrl + Alt + Esc)."""

import logging
import threading
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class KillSwitch:
    """Monitors system-wide hotkeys and signals emergency shutdown."""

    def __init__(self, hotkey_str: str = "<ctrl>+<alt>+<esc>"):
        self.hotkey_str = hotkey_str
        self._stop_event = threading.Event()
        self._callbacks: List[Callable[[], None]] = []
        self._listener = None
        self._is_running = False

    def start(self):
        """Start global hotkey listener in background daemon thread."""
        if self._is_running:
            return

        try:
            from pynput import keyboard

            def on_activate():
                logger.warning("🚨 EMERGENCY KILL SWITCH ACTIVATED (Ctrl + Alt + Esc)!")
                self.trigger()

            hotkeys = {self.hotkey_str: on_activate}
            self._listener = keyboard.GlobalHotKeys(hotkeys)
            self._listener.daemon = True
            self._listener.start()
            self._is_running = True
            logger.info("Kill switch active on %s", self.hotkey_str)
        except Exception as e:
            logger.warning("Could not initialize pynput GlobalHotKeys (%s). Manual trigger still available.", e)

    def trigger(self):
        """Trigger emergency stop."""
        self._stop_event.set()
        for cb in self._callbacks:
            try:
                cb()
            except Exception as e:
                logger.error("Error in kill switch callback: %s", e)

    def reset(self):
        """Reset emergency stop state."""
        self._stop_event.clear()

    def is_triggered(self) -> bool:
        """Check if kill switch has been triggered."""
        return self._stop_event.is_set()

    def register_callback(self, callback: Callable[[], None]):
        """Register a function to be called immediately when kill switch fires."""
        self._callbacks.append(callback)

    def stop(self):
        """Stop background listener."""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._is_running = False
