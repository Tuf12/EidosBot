"""Context management and tool definitions for EidosBot."""

import os
from typing import Any, Dict, List

EIDOS_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "Execute a bash shell command in the local Linux environment. Use this for file operations, reading logs, running scripts, and checking system status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to run (e.g. 'ls -la', 'git status', 'python3 -m unittest')."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_window",
            "description": "Retrieve information about the currently focused desktop window (title, application class, geometry, PID).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture the current desktop screen to visually inspect UI elements or check application state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for taking the screenshot."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click the mouse at specific (x, y) screen coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate on screen."},
                    "y": {"type": "integer", "description": "Y coordinate on screen."},
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "double_click",
            "description": "Double click the left mouse button at (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate."},
                    "y": {"type": "integer", "description": "Y coordinate."}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text using the keyboard into the currently focused window or field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a single keyboard key (e.g. 'enter', 'tab', 'esc', 'space', 'backspace').",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name."}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": "Press a keyboard shortcut combination simultaneously (e.g. ['ctrl', 'c'], ['ctrl', 'alt', 't'], ['ctrl', 'shift', 'p']).",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of keys to press together."
                    }
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish_task",
            "description": "Signal that the user's task goal has been completed or cannot be completed further.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of actions taken and final result."
                    }
                },
                "required": ["summary"]
            }
        }
    }
]


def build_system_prompt(active_window_info: Dict[str, Any], current_dir: str) -> str:
    """Construct dynamic system prompt with real desktop context."""
    win_title = active_window_info.get("title", "Unknown")
    win_class = active_window_info.get("class_name", "Unknown")
    win_geom = active_window_info.get("geometry", {})

    return f"""You are EidosBot, an OS-level autonomous desktop co-pilot running locally on Linux Mint (X11).

SYSTEM CONTEXT:
- OS: Linux Mint (Cinnamon / X11)
- Working Directory: {current_dir}
- Active Window: "{win_title}" (Class: {win_class})
- Window Position: {win_geom.get('x', 0)},{win_geom.get('y', 0)} ({win_geom.get('width', 0)}x{win_geom.get('height', 0)})
- Emergency Kill Switch: Active on Ctrl + Alt + Esc

BEHAVIORAL PRINCIPLES:
1. DETERMINISTIC TIER FIRST: If a task can be solved directly via shell commands (e.g., file inspection, git status, search, running scripts), use `execute_shell`. Never guess mouse clicks when a shell command is faster and 100% reliable.
2. ACCURATE GROUNDING: When interacting with the GUI, first use `take_screenshot` or `get_active_window` to understand the current visual state before clicking or typing.
3. CONCISE REASONING: Explain what you plan to do, then emit the appropriate tool call.
4. COMPLETION: Once the user's goal has been met, call `finish_task` with a concise summary.
"""
