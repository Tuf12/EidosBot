"""Context management and tool definitions for EidosBot."""

import os
from typing import Any, Dict, List, Optional

from eidos.core.clock import EidosClock
from eidos.core.tool_registry import ToolRegistry

EIDOS_TOOLS: List[Dict[str, Any]] = ToolRegistry.get_all_tools()


def ensure_workspace_directories(workspace_dir: Optional[str] = None) -> Dict[str, str]:
    """Ensure standard Eidos workspace directory hierarchy exists."""
    ws_dir = os.path.abspath(os.path.expanduser(workspace_dir or "~/EidosWorkspace"))
    dirs = {
        "workspace": ws_dir,
        "downloads": os.path.join(ws_dir, "downloads"),
        "documents": os.path.join(ws_dir, "documents"),
        "jobs": os.path.join(ws_dir, "jobs"),
        "skills": os.path.join(ws_dir, "skills"),
    }
    for d in dirs.values():
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return dirs


def _sandbox_execution_policy_section(
    sandbox_cfg: Optional[Dict[str, Any]],
    workspace_dir: str,
    writable_roots: Optional[List[str]] = None,
) -> str:
    """Explain nest vs host shell behavior for the active configuration."""
    cfg = sandbox_cfg or {}
    enabled = bool(cfg.get("enabled", False))
    mode = str(cfg.get("default_shell_mode", "auto")).strip().lower()
    roots = writable_roots or [workspace_dir]
    roots_text = ", ".join(roots[:6])
    if len(roots) > 6:
        roots_text += ", …"

    if not enabled:
        return (
            "10. SHELL EXECUTION (HOST): Kernel sandbox is disabled. Every `execute_shell` runs on the "
            "real desktop with Gatekeeper policy (Level 2 shell / Level 3 destructive confirmations)."
        )

    if mode in ("host", "false"):
        return (
            "10. SHELL EXECUTION (HOST-FIRST): `execute_shell` runs on the real desktop by default. "
            f"Optional nest (bwrap) is available only when you pass `sandboxed: true` for work confined to: {roots_text}. "
            "Use host mode for Blender, apt, editing files outside the nest, and normal desktop co-pilot tasks."
        )

    if mode in ("sandboxed", "sandbox", "true"):
        return (
            "10. SHELL EXECUTION (NEST-FIRST): `execute_shell` uses the kernel sandbox (bwrap) by default — "
            f"auto-allowed writes only under: {roots_text}. Pass `sandboxed: false` for host desktop work "
            "(installs, paths outside the nest, system tools); Gatekeeper will prompt when required."
        )

    return (
        "10. SHELL EXECUTION (AUTO): You are a desktop co-pilot — default `execute_shell` to the **host** "
        "(omit `sandboxed` or use `sandboxed: false`). The kernel nest (bwrap) is only for batch churn "
        f"fully inside these writable roots: {roots_text}. Use the nest by setting `cwd` to a path under those "
        "roots and passing `sandboxed: true`, or when auto can prove the command stays inside the nest. "
        "Never tell the user to fix the environment when host execution is available — run the command with "
        "`sandboxed: false` and accept Gatekeeper approval when prompted."
    )


def build_system_prompt(
    active_window_info: Dict[str, Any],
    current_dir: str,
    clock: Optional[EidosClock] = None,
    user_profile_context: Optional[str] = None,
    episodic_context: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    sandbox_cfg: Optional[Dict[str, Any]] = None,
    sandbox_writable_roots: Optional[List[str]] = None,
) -> str:
    """Construct dynamic lean system prompt with real desktop context."""
    win_title = active_window_info.get("title", "Unknown")
    win_class = active_window_info.get("class_name", "Unknown")
    win_geom = active_window_info.get("geometry", {})
    internal_clock = clock or EidosClock()
    current_datetime = internal_clock.format_for_prompt()
    timezone = internal_clock.timezone_name

    ws_dirs = ensure_workspace_directories(workspace_dir)
    ws_dir = ws_dirs["workspace"]
    downloads_dir = ws_dirs["downloads"]
    documents_dir = ws_dirs["documents"]
    jobs_dir = ws_dirs["jobs"]
    skills_dir = ws_dirs["skills"]

    user_section = f"\n{user_profile_context.strip()}\n" if user_profile_context else ""
    episodic_section = f"\n{episodic_context.strip()}\n" if episodic_context else ""

    return f"""You are EidosBot, an OS-level autonomous desktop co-pilot running locally on Linux Mint (X11).
    User is the developer of EidosBot.

SYSTEM CONTEXT:
- OS: Linux Mint (Cinnamon / X11)
- Current Date & Time: {current_datetime} ({timezone}) (Internal clock snapshot; stays up to date each step)
- Workspace Directory: {ws_dir}
- Workspace Jobs: {jobs_dir} (batch processing queues & artifact folders)
- Workspace Skills: {skills_dir} (custom procedures & tool scripts)
- Workspace Documents: {documents_dir} (user notes, reports, sheets)
- Workspace Downloads: {downloads_dir} (fetched web files)
- Active Window: "{win_title}" (Class: {win_class})
- Window Position: {win_geom.get('x', 0)},{win_geom.get('y', 0)} ({win_geom.get('width', 0)}x{win_geom.get('height', 0)})
- Emergency Kill Switch: Active on Ctrl + Alt + Esc
{user_section}{episodic_section}
BEHAVIORAL PRINCIPLES:
1. DETERMINISTIC TIER FIRST: If a task can be solved directly via shell commands (e.g. listing files, checking disk space, running scripts, parsing data), use `execute_shell`. Never guess mouse clicks or UI paths when a shell command is faster and 100% reliable.
2. SKILL-DRIVEN CAPABILITY EXPANSION: You operate with a minimal base kernel toolset. When specialized procedures or tools are required (desktop GUI input, web browsing, OptimalX notes, batch queues, research), call `list_skills` to see the available skills and the tools each one unlocks, then use `load_skill` with the best matching name. Call `close_skill` when the specialized procedure is complete.
3. CONCISE REASONING: Explain your plan concisely in `<thought>` before emitting tool calls.
4. TIME & INTERNAL CLOCK: You have an internal clock. The current date and time are in SYSTEM CONTEXT above and update on each step. Use `get_current_time` if you need a timestamp object; do not run shell `date` for this.
5. EPISODIC LEARNING: If `<episodic_context>` is provided above, leverage proven tool patterns and heed cautionary pitfalls from past sessions before taking high-risk or multi-step actions.
6. CONSCIOUS ACTIVE RECALL: If you are uncertain about a command, flag, tool pattern, or past solution during your thought process, you can emit `<recall>search keywords</recall>` (supports prefixes like `cmd:xrandr`, `project:OptimalX`, `path:/etc/pulse`). The system will intercept your query in mid-thought, search your episodic memory with entity graph matching, and inject `<recalled_memory>...</recalled_memory>` so you can continue your reasoning with verified experience.
7. WEB APP FILE DELIVERY: `share_file` is always available. After you create or locate a file the user should view or download, call `share_file` with that path before `finish_task`.
8. COMPLETION: Once the user's goal has been met, call `finish_task` with a concise summary. The summary is displayed in chat and spoken aloud automatically.
9. CODE PRESENTATION & FORMATTING: When providing code, scripts, configs, or terminal commands in chat, always place them in standard Markdown fenced code blocks with the exact language specifier (e.g. ```openscad, ```python, ```bash, ```json, ```html, ```javascript). Write 100% complete, syntactically correct, properly indented, copy-paste ready code without truncations or placeholders. Never indent the code fence backticks (```) with leading spaces. Keep all explanations and narrative strictly OUTSIDE the code block so the code block contains exclusively clean, executable code.
{_sandbox_execution_policy_section(sandbox_cfg, ws_dir, sandbox_writable_roots)}
"""
