"""Dynamic Tool Registry for EidosBot.

Decouples tool schemas into a minimal base kernel toolset and specialized
domain toolsets unlocked on demand via living SKILL.md declarations.
`share_file` stays in the base set so files can reach the web app, including
on a phone, without loading a skill. `list_skills` stays in the base set so
the agent can see which skills exist before calling `load_skill`.
"""

from typing import Any, Dict, List, Optional


class ToolRegistry:
    """Central catalog and scoping engine for all EidosBot tool schemas."""

    BASE_TOOLS: List[Dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "execute_shell",
                "description": (
                    "Execute a bash shell command in the local Linux environment. "
                    "Use this for deterministic file operations, reading logs, running scripts, and checking system status."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to run (e.g. 'ls -la', 'df -h', 'curl -sI https://example.com').",
                        },
                        "sandboxed": {
                            "description": (
                                "Execution surface: omit or 'auto' (config policy), true = kernel nest (bwrap, "
                                "auto-allowed writes only in configured workspace paths), false = host desktop shell "
                                "(Gatekeeper may prompt). Desktop co-pilot tasks outside the nest should use false."
                            ),
                            "oneOf": [
                                {"type": "boolean"},
                                {"type": "string", "enum": ["auto"]},
                            ],
                        },
                        "cwd": {
                            "type": "string",
                            "description": (
                                "Working directory for the command. For auto nest mode, set this to a path inside "
                                "the writable workspace when you intentionally want sandboxed execution."
                            ),
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": (
                    "Activate a living skill procedure or rubric by name, injecting its instructions into context "
                    "and dynamically unlocking its declared specialized tools for subsequent hops."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to load (e.g. 'desktop-gui', 'optimalx', 'browser-automation', 'invention-evaluator').",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "close_skill",
                "description": (
                    "Deactivate the currently active skill procedure and release its specialized tools, "
                    "returning EidosBot to base kernel mode."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Get the current local date and time from EidosBot's internal clock (no shell command needed).",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
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
                            "description": "Summary of actions taken and final result.",
                        }
                    },
                    "required": ["summary"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "share_file",
                "description": (
                    "Send a local file to the user's web app so they can view and download it there, "
                    "including on a phone. Call this for every document, note, PDF, spreadsheet, image, "
                    "or other file the user should review. The web app cannot open a raw disk path. "
                    "This tool is always available, even while another skill is active."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute or workspace path of the file to show in the web app.",
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional short caption shown next to the file in the web app.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": (
                    "List every available skill with its name, description, and tools it unlocks. "
                    "Call this when a task needs a specialized skill and you do not already know which "
                    "name to pass to load_skill. Covers built-in skills plus ~/.eidos/skills and .eidos/skills."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    ]

    SPECIALIZED_TOOLS: Dict[str, Dict[str, Any]] = {
        # --- Desktop GUI & Perception Tools ---
        "get_active_window": {
            "type": "function",
            "function": {
                "name": "get_active_window",
                "description": "Retrieve information about the currently focused desktop window (title, application class, geometry, PID).",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        "take_screenshot": {
            "type": "function",
            "function": {
                "name": "take_screenshot",
                "description": "Capture the current desktop screen to visually inspect UI elements or check application state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Reason for taking the screenshot.",
                        }
                    },
                },
            },
        },
        "click_element": {
            "type": "function",
            "function": {
                "name": "click_element",
                "description": "Locate an element visually by natural language description (e.g. 'Save button', 'Terminal tab', 'Search bar') and click it automatically via visual grounding.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_description": {
                            "type": "string",
                            "description": "Visual description of the UI element to find and click.",
                        },
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "default": "left",
                        },
                    },
                    "required": ["target_description"],
                },
            },
        },
        "click": {
            "type": "function",
            "function": {
                "name": "click",
                "description": "Click the mouse at specific (x, y) screen coordinates.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate on screen."},
                        "y": {"type": "integer", "description": "Y coordinate on screen."},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    },
                    "required": ["x", "y"],
                },
            },
        },
        "double_click": {
            "type": "function",
            "function": {
                "name": "double_click",
                "description": "Double click the left mouse button at (x, y).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate."},
                        "y": {"type": "integer", "description": "Y coordinate."},
                    },
                    "required": ["x", "y"],
                },
            },
        },
        "type_text": {
            "type": "function",
            "function": {
                "name": "type_text",
                "description": "Type text using the keyboard into the currently focused window or field.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to type."},
                    },
                    "required": ["text"],
                },
            },
        },
        "press_key": {
            "type": "function",
            "function": {
                "name": "press_key",
                "description": "Press a single keyboard key (e.g. 'enter', 'tab', 'esc', 'space', 'backspace').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name."},
                    },
                    "required": ["key"],
                },
            },
        },
        "hotkey": {
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
                            "description": "List of keys to press together.",
                        }
                    },
                    "required": ["keys"],
                },
            },
        },
        "speak_aloud": {
            "type": "function",
            "function": {
                "name": "speak_aloud",
                "description": "Speak a concise message or audible notification aloud to the user using the local Kokoro TTS engine.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The concise sentence or alert to speak aloud.",
                        }
                    },
                    "required": ["message"],
                },
            },
        },

        # --- Browser Automation Tools (Playwright) ---
        "browser_open": {
            "type": "function",
            "function": {
                "name": "browser_open",
                "description": "Launch headless Chromium (or reuse active session) and navigate to the target URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Target webpage URL to open.",
                        },
                        "headless": {
                            "type": "boolean",
                            "description": "Whether to run browser in headless background mode (default: true).",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        "browser_extract": {
            "type": "function",
            "function": {
                "name": "browser_extract",
                "description": "Extract dense, readable Markdown content from the active browser page or specific CSS selector using trafilatura.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "Optional CSS selector to extract only a specific element or container.",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum length of extracted Markdown content (default: 8000).",
                        },
                    },
                },
            },
        },
        "browser_click": {
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": "Click an interactive element on the active browser page using CSS selector or text matcher.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector or text matcher for the element to click (e.g. 'button#submit', 'a.next', 'text=Sign In').",
                        }
                    },
                    "required": ["selector"],
                },
            },
        },
        "browser_type": {
            "type": "function",
            "function": {
                "name": "browser_type",
                "description": "Type text into an input or textarea element on the active browser page.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector of the input field (e.g. 'input[name=q]', '#search-box').",
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to fill into the input field.",
                        },
                        "press_enter": {
                            "type": "boolean",
                            "description": "Whether to press Enter key after typing (default: false).",
                        },
                    },
                    "required": ["selector", "text"],
                },
            },
        },
        "browser_screenshot": {
            "type": "function",
            "function": {
                "name": "browser_screenshot",
                "description": "Capture a visual screenshot of the active browser webpage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "full_page": {
                            "type": "boolean",
                            "description": "Whether to capture the entire scrollable height or only the current viewport (default: false).",
                        }
                    },
                },
            },
        },
        "browser_evaluate": {
            "type": "function",
            "function": {
                "name": "browser_evaluate",
                "description": "Execute arbitrary JavaScript expression on the active browser page and return the evaluated result.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "JavaScript code or expression to evaluate (e.g. 'document.title', 'window.scrollBy(0, 500)').",
                        }
                    },
                    "required": ["script"],
                },
            },
        },
        "browser_close": {
            "type": "function",
            "function": {
                "name": "browser_close",
                "description": "Close the active browser session and release memory.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # --- Web Research & File Tools ---
        "web_search": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the live web using Brave / DuckDuckGo search. Returns relevant titles, URLs, and snippets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of results to return (default: 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        "web_fetch": {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch a webpage URL and extract clean, readable text content for research and analysis.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Full URL of webpage to fetch.",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters of text content to extract (default: 8000).",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        "download_file": {
            "type": "function",
            "function": {
                "name": "download_file",
                "description": "Download a file (e.g. PDF, patent, dataset, document, image) directly off the internet into the user's workspace downloads directory (~/EidosWorkspace/downloads).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Direct HTTP/HTTPS URL of the file to download.",
                        },
                        "filename": {
                            "type": "string",
                            "description": "Optional custom filename (e.g. 'patent_US20140216326A1.pdf'). If omitted, automatically derived from URL.",
                        },
                        "destination_dir": {
                            "type": "string",
                            "description": "Optional destination directory (defaults to '~/EidosWorkspace/downloads').",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        "open_in_browser": {
            "type": "function",
            "function": {
                "name": "open_in_browser",
                "description": "Open a web URL or local file directly on the user's desktop screen using their default web browser or desktop document viewer (e.g. to pull up a patent, webpage, or PDF alongside the user).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "The URL (http:// or https://) or local file path to open on the user's desktop.",
                        }
                    },
                    "required": ["target"],
                },
            },
        },
        # --- OptimalX Knowledge Bridge Tools ---
        "optimalx_action": {
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
                            "description": "Action to perform on OptimalX Desktop.",
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS selector when using query_dom or click_dom.",
                        },
                        "expression": {
                            "type": "string",
                            "description": "JavaScript expression when using evaluate_js.",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        "optimalx_search_notes": {
            "type": "function",
            "function": {
                "name": "optimalx_search_notes",
                "description": "Search personal notes and knowledge bases across OptimalX (synced from mobile and desktop). Uses semantic vector search when online, falling back to SQLite text search.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query or keywords.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of note matches to return (default: 5).",
                        },
                        "parent_folder": {
                            "type": "string",
                            "description": "Optional: Filter results to a specific parent folder name.",
                        },
                        "prefer_offline": {
                            "type": "boolean",
                            "description": "Optional: Force direct SQLite search bypassing online LLM server (default: false).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        "optimalx_read_note": {
            "type": "function",
            "function": {
                "name": "optimalx_read_note",
                "description": "Read the full markdown content and metadata of a specific OptimalX note.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subfolder_id": {
                            "type": "integer",
                            "description": "Subfolder ID containing the note.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional: Subfolder or note title if ID is unknown.",
                        },
                        "note_id": {
                            "type": "integer",
                            "description": "Optional: Direct note table ID.",
                        },
                    },
                },
            },
        },
        "optimalx_list_folders": {
            "type": "function",
            "function": {
                "name": "optimalx_list_folders",
                "description": "List hierarchical parent folders and contained subfolders in OptimalX to explore personal knowledge organization. Pass 'query' or 'parent_folder_id' to filter specifically (e.g. 'Inventions') and avoid dumping the entire knowledge tree.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Optional search term to filter parent folders or subfolders by name (e.g. 'Inventions', 'the list'). Highly recommended to keep context small.",
                        },
                        "parent_folder_id": {
                            "type": "integer",
                            "description": "Optional: Specific parent folder ID to list subfolders for.",
                        },
                        "include_subfolders": {
                            "type": "boolean",
                            "description": "Whether to include subfolder arrays within each parent folder (default: true).",
                        },
                    },
                },
            },
        },
        "optimalx_write_note": {
            "type": "function",
            "function": {
                "name": "optimalx_write_note",
                "description": "Write, append, or propose modifications to an OptimalX note with Diff Review safety. Empty notes write immediately; existing non-empty notes queue for human-in-the-loop Diff Review in the OptimalX desktop app.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Markdown content to write, append, or propose.",
                        },
                        "subfolder_id": {
                            "type": "integer",
                            "description": "Optional: ID of the target subfolder/note if known.",
                        },
                        "subfolder_title": {
                            "type": "string",
                            "description": "Title of the note or subfolder (used if subfolder_id is not provided).",
                        },
                        "parent_name": {
                            "type": "string",
                            "description": "Optional parent folder name (e.g. 'Inventions', 'Personal', 'Work'). Auto-created if not found.",
                        },
                        "parent_folder_id": {
                            "type": "integer",
                            "description": "Optional: Parent folder ID.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["auto", "direct_overwrite", "append", "propose_edit"],
                            "description": "Write mode: 'auto' (direct write if empty, queue if existing), 'direct_overwrite', 'append', or 'propose_edit' (default: 'auto').",
                        },
                        "allow_direct_append": {
                            "type": "boolean",
                            "description": "If true with mode='append', bypasses Diff Review to append directly (default: false).",
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional checkpoint description or version note.",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        "optimalx_read_notex_doc": {
            "type": "function",
            "function": {
                "name": "optimalx_read_notex_doc",
                "description": "Read a long-form markdown document from an OptimalX NoteX corpus project with optional line range slicing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subfolder_id": {
                            "type": "integer",
                            "description": "Subfolder ID of the NoteX project.",
                        },
                        "doc_path": {
                            "type": "string",
                            "description": "Relative document path within the NoteX project (e.g. 'Canon.md', 'Outline.md', 'Sections/Chapter 1.md').",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional: 1-indexed starting line number for sliced reading.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional: 1-indexed ending line number for sliced reading.",
                        },
                    },
                    "required": ["subfolder_id", "doc_path"],
                },
            },
        },
        "optimalx_list_notex_docs": {
            "type": "function",
            "function": {
                "name": "optimalx_list_notex_docs",
                "description": "List all markdown documents, chapters, and sections available in an OptimalX NoteX corpus project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subfolder_id": {
                            "type": "integer",
                            "description": "Subfolder ID of the NoteX project.",
                        }
                    },
                    "required": ["subfolder_id"],
                },
            },
        },

        # --- Batch Queue Runner Tools ---
        "init_batch_job": {
            "type": "function",
            "function": {
                "name": "init_batch_job",
                "description": "Initialize a domain-agnostic batch job queue (queue.json) from an item list, JSON string, or CSV/JSON file path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_name": {
                            "type": "string",
                            "description": "Human-readable label for the batch job.",
                        },
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill procedure assigned to guide execution for each item.",
                        },
                        "items_data": {
                            "description": "Items to process: can be a JSON array, raw text lines, or a filepath to a .json/.csv file.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Optional destination directory for queue.json, job.yaml manifest, and generated artifacts.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional high-level description of the batch task.",
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional project group name (e.g., 'Inventions_List').",
                        },
                        "phase": {
                            "type": "string",
                            "description": "Optional project phase name (e.g., 'phase_1_triage').",
                        },
                    },
                    "required": ["job_name", "skill_name", "items_data"],
                },
            },
        },
        "run_batch_job": {
            "type": "function",
            "function": {
                "name": "run_batch_job",
                "description": "Start continuous autonomous execution of a batch job queue (processes all pending items sequentially with pacing cool-downs). Can run in background or foreground.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or queue.json path. If omitted, runs the active batch job.",
                        },
                        "background": {
                            "type": "boolean",
                            "description": "Whether to run as a background worker thread (true, default) or block in foreground (false).",
                        },
                        "pause_between_items_sec": {
                            "type": "number",
                            "description": "Cool-down interval between items in seconds (default: 2.0s).",
                        },
                        "max_items": {
                            "type": "integer",
                            "description": "Optional limit on number of items to process in this run before stopping.",
                        },
                    },
                },
            },
        },
        "step_batch_job": {
            "type": "function",
            "function": {
                "name": "step_batch_job",
                "description": "Execute the next pending item in a batch job queue with fresh item context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or path to queue.json. If omitted, steps the active job.",
                        }
                    },
                },
            },
        },
        "get_job_status": {
            "type": "function",
            "function": {
                "name": "get_job_status",
                "description": "Inspect progress metrics, completed counts, and active items of a batch job queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or queue directory. If omitted, checks active job.",
                        }
                    },
                },
            },
        },
        "pause_batch_job": {
            "type": "function",
            "function": {
                "name": "pause_batch_job",
                "description": "Pause processing of an active batch job queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or queue directory.",
                        }
                    },
                },
            },
        },
        "resume_batch_job": {
            "type": "function",
            "function": {
                "name": "resume_batch_job",
                "description": "Resume execution of a paused batch job queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or queue directory.",
                        }
                    },
                },
            },
        },
        "generate_batch_summary": {
            "type": "function",
            "function": {
                "name": "generate_batch_summary",
                "description": "Generate or re-compile the ranked leaderboard summary (RANKING_SUMMARY.md, .csv, .json) for an evaluated batch job queue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Optional job ID or queue directory. If omitted, summarizes the active batch job.",
                        }
                    },
                },
            },
        },
        "load_job": {
            "type": "function",
            "function": {
                "name": "load_job",
                "description": "Enter Job Mode by loading an existing batch job by ID, directory path, or queue/manifest file. Automatically activates the job's assigned skill and scoped tools.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id_or_path": {
                            "type": "string",
                            "description": "Job ID (e.g. 'job_20261005_1234_abcd') or filesystem path to the job directory, queue.json, or job.yaml.",
                        },
                    },
                    "required": ["job_id_or_path"],
                },
            },
        },
        "close_job": {
            "type": "function",
            "function": {
                "name": "close_job",
                "description": "Exit Job Mode for the currently active batch job and return to base kernel mode.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # --- User Profile & Memory Tools ---
        "update_user_profile": {
            "type": "function",
            "function": {
                "name": "update_user_profile",
                "description": (
                    "Save or update a single atomic fact in the user's local profile "
                    "(~/.eidos/user_profile.yaml). Use only when the user explicitly asks to remember "
                    "a preference, path, or identity detail."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["user_identity", "preferences", "environment_facts"],
                            "description": "Profile section to update.",
                        },
                        "key": {
                            "type": "string",
                            "description": "Fact key within the category (e.g. primary_editor, projects_dir).",
                        },
                        "value": {
                            "type": "string",
                            "description": "Fact value to store.",
                        },
                    },
                    "required": ["category", "key", "value"],
                },
            },
        },
        "add_workflow_rule": {
            "type": "function",
            "function": {
                "name": "add_workflow_rule",
                "description": (
                    "Append a workflow guideline bullet to the user profile. "
                    "Use when the user asks Eidos to remember a recurring habit or rule."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rule": {
                            "type": "string",
                            "description": "Short, atomic workflow rule sentence.",
                        }
                    },
                    "required": ["rule"],
                },
            },
        },
        "remove_workflow_rule": {
            "type": "function",
            "function": {
                "name": "remove_workflow_rule",
                "description": "Remove a workflow guideline from the user profile by exact rule text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rule": {
                            "type": "string",
                            "description": "Exact workflow rule text to remove.",
                        }
                    },
                    "required": ["rule"],
                },
            },
        },
        "remove_user_fact": {
            "type": "function",
            "function": {
                "name": "remove_user_fact",
                "description": "Remove a key from user_identity, preferences, or environment_facts in the user profile.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["user_identity", "preferences", "environment_facts"],
                        },
                        "key": {"type": "string", "description": "Key to delete from the category."},
                    },
                    "required": ["category", "key"],
                },
            },
        },

        # --- Skills Authoring Tools ---
        "create_skill": {
            "type": "function",
            "function": {
                "name": "create_skill",
                "description": "Create a new living skill directory and SKILL.md document with YAML frontmatter, procedures, and templates.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short identifier for the skill (e.g. 'patent-prior-art', 'pr-reviewer').",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete markdown content for SKILL.md including optional YAML frontmatter.",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["global", "project"],
                            "description": "Where to store the skill ('global' = ~/.eidos/skills, 'project' = .eidos/skills). Defaults to global.",
                        },
                    },
                    "required": ["name", "content"],
                },
            },
        },
        "update_skill": {
            "type": "function",
            "function": {
                "name": "update_skill",
                "description": "Update the markdown contents or rubric of an existing skill.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the existing skill to update.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Updated markdown content.",
                        },
                    },
                    "required": ["name", "content"],
                },
            },
        },
    }

    @classmethod
    def get_base_tools(cls) -> List[Dict[str, Any]]:
        """Return a copy of the minimal base kernel tools."""
        return [dict(t) for t in cls.BASE_TOOLS]

    @classmethod
    def get_scoped_tools(cls, tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return base tools plus requested specialized tools in stable order.

        Args:
            tool_names: List of specialized tool function names to include.

        Returns:
            List of tool schemas matching OpenAI function calling format.
        """
        tools = [dict(t) for t in cls.BASE_TOOLS]
        if not tool_names:
            return tools

        seen = {t["function"]["name"] for t in tools}
        for name in tool_names:
            if name in cls.SPECIALIZED_TOOLS and name not in seen:
                tools.append(dict(cls.SPECIALIZED_TOOLS[name]))
                seen.add(name)
        return tools

    @classmethod
    def get_all_tools(cls) -> List[Dict[str, Any]]:
        """Return all tools (base + all specialized) for full catalog or backward compatibility."""
        tools = [dict(t) for t in cls.BASE_TOOLS]
        seen = {t["function"]["name"] for t in tools}
        for name, tool in cls.SPECIALIZED_TOOLS.items():
            if name not in seen:
                tools.append(dict(tool))
                seen.add(name)
        return tools

    @classmethod
    def get_tool(cls, name: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific tool schema by function name."""
        for tool in cls.BASE_TOOLS:
            if tool.get("function", {}).get("name") == name:
                return dict(tool)
        if name in cls.SPECIALIZED_TOOLS:
            return dict(cls.SPECIALIZED_TOOLS[name])
        return None

    @classmethod
    def list_specialized_tool_names(cls) -> List[str]:
        """Return a sorted list of all available specialized tool names."""
        return sorted(list(cls.SPECIALIZED_TOOLS.keys()))
