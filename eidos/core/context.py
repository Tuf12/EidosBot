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
            "name": "click_element",
            "description": "Locate an element visually by natural language description (e.g. 'Save button', 'Terminal tab', 'Search bar') and click it automatically via visual grounding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_description": {
                        "type": "string",
                        "description": "Visual description of the UI element to find and click."
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "default": "left"
                    }
                },
                "required": ["target_description"]
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
            "name": "optimalx_action",
            "description": "Interact directly with OptimalX Desktop 1.0 Electron application via local API or Chrome DevTools Protocol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get_status", "get_llm_status", "focus", "query_dom", "click_dom", "evaluate_js"],
                        "description": "Action to perform on OptimalX Desktop."
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector when using query_dom or click_dom."
                    },
                    "expression": {
                        "type": "string",
                        "description": "JavaScript expression when using evaluate_js."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cursor_action",
            "description": "Automate Cursor IDE (open file at line/col, open workspace, trigger Command Palette, open diff).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open_file", "open_workspace", "open_diff", "command_palette", "focus"],
                        "description": "Action to perform in Cursor IDE."
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Target file or workspace path."
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number to jump to."
                    },
                    "col": {
                        "type": "integer",
                        "description": "Column number to jump to."
                    },
                    "command_name": {
                        "type": "string",
                        "description": "Name of command to run in Cursor Command Palette (e.g. 'View: Toggle Terminal')."
                    },
                    "file_b": {
                        "type": "string",
                        "description": "Second file path when action is open_diff."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web using Brave / DuckDuckGo search. Returns relevant titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query."
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a webpage URL and extract clean, readable text content for research and analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL of webpage to fetch."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters of text content to extract (default: 8000)."
                    }
                },
                "required": ["url"]
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
