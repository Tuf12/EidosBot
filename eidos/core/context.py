"""Context management and tool definitions for EidosBot."""

import os
from typing import Any, Dict, List, Optional

from eidos.core.clock import EidosClock

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
                        "description": "The exact shell command to run (e.g. 'ls -la', 'df -h', 'curl -sI https://example.com')."
                    },
                    "sandboxed": {
                        "type": "boolean",
                        "description": "Whether to execute inside the kernel sandbox (root & home read-only, workspace read-write). Defaults to true. Set to false only when host system changes or installs are explicitly required."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current local date and time from EidosBot's internal clock (no shell command needed).",
            "parameters": {
                "type": "object",
                "properties": {}
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
            "name": "browser_open",
            "description": "Launch headless Chromium (or reuse active session) and navigate to the target URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target webpage URL to open."
                    },
                    "headless": {
                        "type": "boolean",
                        "description": "Whether to run browser in headless background mode (default: true)."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "Extract dense, readable Markdown content from the active browser page or specific CSS selector using trafilatura.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Optional CSS selector to extract only a specific element or container."
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum length of extracted Markdown content (default: 8000)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an interactive element on the active browser page using CSS selector or text matcher.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector or text matcher for the element to click (e.g. 'button#submit', 'a.next', 'text=Sign In')."
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input or textarea element on the active browser page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector of the input field (e.g. 'input[name=q]', '#search-box')."
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to fill into the input field."
                    },
                    "press_enter": {
                        "type": "boolean",
                        "description": "Whether to press Enter key after typing (default: false)."
                    }
                },
                "required": ["selector", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a visual screenshot of the active browser webpage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_page": {
                        "type": "boolean",
                        "description": "Whether to capture the entire scrollable height or only the current viewport (default: false)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": "Execute arbitrary JavaScript expression on the active browser page and return the evaluated result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "JavaScript code or expression to evaluate (e.g. 'document.title', 'window.scrollBy(0, 500)')."
                    }
                },
                "required": ["script"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the active browser session and release memory.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "Download a file (e.g. PDF, patent, dataset, document, image) directly off the internet into the user's workspace downloads directory (~/EidosWorkspace/downloads).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct HTTP/HTTPS URL of the file to download."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename (e.g. 'patent_US20140216326A1.pdf'). If omitted, automatically derived from URL."
                    },
                    "destination_dir": {
                        "type": "string",
                        "description": "Optional destination directory (defaults to '~/EidosWorkspace/downloads')."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_in_browser",
            "description": "Open a web URL or local file directly on the user's desktop screen using their default web browser or desktop document viewer (e.g. to pull up a patent, webpage, or PDF alongside the user).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "The URL (http:// or https://) or local file path to open on the user's desktop."
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "speak_aloud",
            "description": "Speak a concise message or audible notification aloud to the user using the local Kokoro TTS engine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The concise sentence or alert to speak aloud."
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
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
    {
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
    {
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
    {
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
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "Discover all available human/bot co-authored skills and evaluation rubrics from ~/.eidos/skills/.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load a living SKILL.md procedure or evaluation rubric into context by skill name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill to load (e.g. 'invention-evaluator', 'code-auditor')."
                    }
                },
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": "Create a new living skill directory and SKILL.md document with YAML frontmatter, procedures, and templates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short identifier for the skill (e.g. 'patent-prior-art', 'pr-reviewer')."
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete markdown content for SKILL.md including optional YAML frontmatter."
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "project"],
                        "description": "Where to store the skill ('global' = ~/.eidos/skills, 'project' = .eidos/skills). Defaults to global."
                    }
                },
                "required": ["name", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_skill",
            "description": "Update the markdown contents or rubric of an existing skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the existing skill to update."
                    },
                    "content": {
                        "type": "string",
                        "description": "Updated markdown content."
                    }
                },
                "required": ["name", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "init_batch_job",
            "description": "Initialize a domain-agnostic batch job queue (queue.json) from an item list, JSON string, or CSV/JSON file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "Human-readable label for the batch job."
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill procedure assigned to guide execution for each item."
                    },
                    "items_data": {
                        "description": "Items to process: can be a JSON array, raw text lines, or a filepath to a .json/.csv file.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Optional destination directory for queue.json and generated artifacts."
                    }
                },
                "required": ["job_name", "skill_name", "items_data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_batch_job",
            "description": "Start continuous autonomous execution of a batch job queue (processes all pending items sequentially with pacing cool-downs). Can run in background or foreground.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or queue.json path. If omitted, runs the active batch job."
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Whether to run as a background worker thread (true, default) or block in foreground (false)."
                    },
                    "pause_between_items_sec": {
                        "type": "number",
                        "description": "Cool-down interval between items in seconds (default: 2.0s)."
                    },
                    "max_items": {
                        "type": "integer",
                        "description": "Optional limit on number of items to process in this run before stopping."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "step_batch_job",
            "description": "Execute the next pending item in a batch job queue with fresh item context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or path to queue.json. If omitted, steps the active job."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_status",
            "description": "Inspect progress metrics, completed counts, and active items of a batch job queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or queue directory. If omitted, checks active job."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pause_batch_job",
            "description": "Pause processing of an active batch job queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or queue directory."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resume_batch_job",
            "description": "Resume execution of a paused batch job queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or queue directory."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_batch_summary",
            "description": "Generate or re-compile the ranked leaderboard summary (RANKING_SUMMARY.md, .csv, .json) for an evaluated batch job queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Optional job ID or queue directory. If omitted, summarizes the active batch job."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_search_notes",
            "description": "Search personal notes and knowledge bases across OptimalX (synced from mobile and desktop). Uses semantic vector search when online, falling back to SQLite text search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query or keywords."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of note matches to return (default: 5)."
                    },
                    "parent_folder": {
                        "type": "string",
                        "description": "Optional: Filter results to a specific parent folder name."
                    },
                    "prefer_offline": {
                        "type": "boolean",
                        "description": "Optional: Force direct SQLite search bypassing online LLM server (default: false)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_read_note",
            "description": "Read the full markdown content and metadata of a specific OptimalX note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subfolder_id": {
                        "type": "integer",
                        "description": "Subfolder ID containing the note."
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional: Subfolder or note title if ID is unknown."
                    },
                    "note_id": {
                        "type": "integer",
                        "description": "Optional: Direct note table ID."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_list_folders",
            "description": "List hierarchical parent folders and contained subfolders in OptimalX to explore personal knowledge organization. Pass 'query' or 'parent_folder_id' to filter specifically (e.g. 'Inventions') and avoid dumping the entire knowledge tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term to filter parent folders or subfolders by name (e.g. 'Inventions', 'the list'). Highly recommended to keep context small."
                    },
                    "parent_folder_id": {
                        "type": "integer",
                        "description": "Optional: Specific parent folder ID to list subfolders for."
                    },
                    "include_subfolders": {
                        "type": "boolean",
                        "description": "Whether to include subfolder arrays within each parent folder (default: true)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_write_note",
            "description": "Write, append, or propose modifications to an OptimalX note with Diff Review safety. Empty notes write immediately; existing non-empty notes queue for human-in-the-loop Diff Review in the OptimalX desktop app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Markdown content to write, append, or propose."
                    },
                    "subfolder_id": {
                        "type": "integer",
                        "description": "Optional: ID of the target subfolder/note if known."
                    },
                    "subfolder_title": {
                        "type": "string",
                        "description": "Title of the note or subfolder (used if subfolder_id is not provided)."
                    },
                    "parent_name": {
                        "type": "string",
                        "description": "Optional parent folder name (e.g. 'Inventions', 'Personal', 'Work'). Auto-created if not found."
                    },
                    "parent_folder_id": {
                        "type": "integer",
                        "description": "Optional: Parent folder ID."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "direct_overwrite", "append", "propose_edit"],
                        "description": "Write mode: 'auto' (direct write if empty, queue if existing), 'direct_overwrite', 'append', or 'propose_edit' (default: 'auto')."
                    },
                    "allow_direct_append": {
                        "type": "boolean",
                        "description": "If true with mode='append', bypasses Diff Review to append directly (default: false)."
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional checkpoint description or version note."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_read_notex_doc",
            "description": "Read a long-form markdown document from an OptimalX NoteX corpus project with optional line range slicing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subfolder_id": {
                        "type": "integer",
                        "description": "Subfolder ID of the NoteX project."
                    },
                    "doc_path": {
                        "type": "string",
                        "description": "Relative document path within the NoteX project (e.g. 'Canon.md', 'Outline.md', 'Sections/Chapter 1.md')."
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional: 1-indexed starting line number for sliced reading."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional: 1-indexed ending line number for sliced reading."
                    }
                },
                "required": ["subfolder_id", "doc_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_list_notex_docs",
            "description": "List all markdown documents, chapters, and sections available in an OptimalX NoteX corpus project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subfolder_id": {
                        "type": "integer",
                        "description": "Subfolder ID of the NoteX project."
                    }
                },
                "required": ["subfolder_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_list_pending_reviews",
            "description": "List pending Diff Review items in OptimalX awaiting human approval or review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subfolder_id": {
                        "type": "integer",
                        "description": "Optional subfolder ID to filter pending items."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_accept_pending_review",
            "description": "Accept a pending Diff Review item and commit its changes directly to the OptimalX note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "integer",
                        "description": "ID of the pending review item to accept."
                    }
                },
                "required": ["item_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "optimalx_reject_pending_review",
            "description": "Reject a pending Diff Review item in OptimalX without modifying the note on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "integer",
                        "description": "ID of the pending review item to reject."
                    }
                },
                "required": ["item_id"]
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


def build_system_prompt(
    active_window_info: Dict[str, Any],
    current_dir: str,
    clock: Optional[EidosClock] = None,
    user_profile_context: Optional[str] = None,
    episodic_context: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> str:
    """Construct dynamic system prompt with real desktop context."""
    win_title = active_window_info.get("title", "Unknown")
    win_class = active_window_info.get("class_name", "Unknown")
    win_geom = active_window_info.get("geometry", {})
    internal_clock = clock or EidosClock()
    current_datetime = internal_clock.format_for_prompt()
    timezone = internal_clock.timezone_name

    ws_dir = os.path.abspath(os.path.expanduser(workspace_dir or "~/EidosWorkspace"))
    downloads_dir = os.path.join(ws_dir, "downloads")
    documents_dir = os.path.join(ws_dir, "documents")

    user_section = f"\n{user_profile_context.strip()}\n" if user_profile_context else ""
    episodic_section = f"\n{episodic_context.strip()}\n" if episodic_context else ""

    return f"""You are EidosBot, an OS-level autonomous desktop co-pilot running locally on Linux Mint (X11).

SYSTEM CONTEXT:
- OS: Linux Mint (Cinnamon / X11)
- Current Date & Time: {current_datetime} ({timezone})
- Workspace Directory: {ws_dir}
- Workspace Downloads: {downloads_dir}
- Workspace Documents: {documents_dir}
- Codebase Directory: {current_dir}
- Active Window: "{win_title}" (Class: {win_class})
- Window Position: {win_geom.get('x', 0)},{win_geom.get('y', 0)} ({win_geom.get('width', 0)}x{win_geom.get('height', 0)})
- Emergency Kill Switch: Active on Ctrl + Alt + Esc
{user_section}{episodic_section}
BEHAVIORAL PRINCIPLES:
1. DETERMINISTIC TIER FIRST: If a task can be solved directly via shell commands (e.g., listing files, checking disk space, fetching URLs, running user-provided scripts), use `execute_shell`. Never guess mouse clicks when a shell command is faster and 100% reliable.
2. ACCURATE GROUNDING: When interacting with the GUI, first use `take_screenshot` or `get_active_window` to understand the current visual state before clicking or typing.
3. CONCISE REASONING: Explain what you plan to do, then emit the appropriate tool call.
4. COMPLETION: Once the user's goal has been met, call `finish_task` with a concise summary. The summary is shown in chat and spoken aloud automatically.
5. VOICE: Chat replies and `finish_task` summaries are spoken aloud automatically. Use `speak_aloud` only for mid-task alerts. Do not call it for the final summary.
6. TIME: You have an internal clock. The current date and time are in SYSTEM CONTEXT above and stay up to date each step. Use `get_current_time` when you need a fresh timestamp; do not run shell `date` for this.
7. USER MEMORY: Persist user-stated preferences with `update_user_profile`, `add_workflow_rule`, or `remove_user_fact` only after the user asks to remember or forget something. Profile writes require user confirmation in the HUD.
8. EPISODIC LEARNING: If `<episodic_context>` is provided above, leverage proven tool patterns and heed cautionary pitfalls from past sessions before taking high-risk or multi-step actions.
9. CONSCIOUS ACTIVE RECALL: If you are uncertain about a command, flag, tool pattern, or past solution during your thought process, you can emit `<recall>search keywords</recall>`. The system will intercept your query in mid-thought, search your episodic memory, and inject `<recalled_memory>...</recalled_memory>` so you can continue your reasoning with verified experience.
10. DETERMINISTIC WEB AUTOMATION: For research, online forms, dynamic SPAs, and patent searches, prefer the headless browser tools (`browser_open`, `browser_extract`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_close`). They interact directly with the DOM and extract clean Markdown via trafilatura, without needing fragile screen coordinates.
11. SKILLS & BATCH QUEUES: For domain procedures, standardized rubrics, or repetitive multi-item projects (e.g. 300+ items), consult skills using `list_skills` and `load_skill`. Use `init_batch_job`, `run_batch_job`, `step_batch_job`, `get_job_status`, and `generate_batch_summary` to maintain durable progress via `queue.json` while running each item in an isolated context window.
12. WORKSPACE DIRECTORY & FILE STORAGE:
- Store all user documents, downloaded PDFs, patents, research notes, datasets, and generated files in your Workspace directory: `{ws_dir}` (use `{downloads_dir}` for downloads and `{documents_dir}` for notes/docs).
- NEVER store downloads, documents, or temporary task files inside the `{current_dir}` repository codebase directory.
13. SOURCE CITATIONS & CLICKABLE LINKS:
- When researching, browsing, or referencing web sources, always include clickable Markdown links in the format `[Page Title](https://...)` in your responses and final summary so the user can easily click to open the page directly.
14. DOWNLOADING & DESKTOP BROWSING:
- To download files from the internet (patents, PDFs, datasets, images), use the dedicated `download_file` tool.
- If the user asks to "pull it up on my PC", "show me the patent", or "open this in my browser", use `open_in_browser` to launch the URL or local file directly on their desktop.
15. OPTIMALX KNOWLEDGE ECOSYSTEM:
- You have direct, bi-directional access to the user's personal notes, ideas, and knowledge base synchronized across OptimalX Mobile (Android) and OptimalX Desktop via `optimalx_search_notes`, `optimalx_read_note`, `optimalx_list_folders`, `optimalx_write_note`, `optimalx_read_notex_doc`, and `optimalx_list_notex_docs`.
- When the user asks about their notes, projects, ideas, or personal plans, search and consult OptimalX first before assuming or creating duplicate external files.
- Notes with `ai_locked=1` or `ai_blind=1` are protected; do not attempt to bypass privacy flags.
- Writing to a brand new or empty note auto-applies directly with version checkpoints; writing or proposing edits to existing notes queues a diff proposal into OptimalX Diff Review for the user to review and approve in the desktop UI.
16. DATA EXTRACTION & FILE CONVERSION (CSV/JSON):
- When asked to extract large lists, notes, or datasets (e.g., extracting 50 to 300+ items from an OptimalX note or document into CSV or JSON), do NOT try to output hundreds of formatted rows through conversational chat text, which risks hitting output token limits or timing out.
- Instead, read the note or file metadata, and use `execute_shell` to run a concise Python script or command that parses the items and saves them directly to `{documents_dir}/<filename>.csv`.
"""
