"""Action execution subsystem for shell commands and GUI input."""

from eidos.actions.mouse_keyboard import InputSimulator
from eidos.actions.shell import ShellExecutor

__all__ = ["ShellExecutor", "InputSimulator"]
