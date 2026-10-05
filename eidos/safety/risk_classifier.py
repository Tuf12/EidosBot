"""Risk classification for EidosBot actions."""

from enum import IntEnum
import os
import re
import shlex
from typing import List, Optional, Tuple


class RiskLevel(IntEnum):
    """Action risk classification levels."""
    LEVEL_0_PASSIVE = 0       # Read-only, inspection, query
    LEVEL_1_UI = 1            # Mouse movements, typing, GUI interactions
    LEVEL_2_SHELL = 2         # Shell commands, file writes, modifications (allowlistable)
    LEVEL_3_DESTRUCTIVE = 3   # Irreversible, destructive, root access (NEVER allowlistable)


# Shell commands that are strictly read-only / passive
READ_ONLY_COMMANDS = {
    "ls", "dir", "pwd", "date", "uptime", "whoami", "id", "uname",
    "cat", "head", "tail", "less", "more", "grep", "rg", "find",
    "echo", "printf", "which", "whereis", "type", "stat", "file",
    "df", "du", "free", "ps", "top", "htop", "env", "printenv",
    "git status", "git log", "git diff", "git branch", "git show"
}

# Destructive binaries or command stems
DESTRUCTIVE_COMMANDS = {
    "rm", "unlink", "shred", "rmdir",
    "dd", "mkfs", "fdisk", "parted", "gdisk", "wipefs",
    "shutdown", "reboot", "poweroff", "halt", "init",
    "sudo", "su", "doas", "pkexec",
    "mkfifo", "mknod"
}

# Dangerous patterns (regex)
DESTRUCTIVE_PATTERNS = [
    r"\brm\s+.*",                              # rm commands
    r"\bshred\b",                              # shredding files
    r"\bdd\s+.*if=.*of=.*",                    # dd disk writes
    r"\bmkfs(\.\w+)?\b",                       # filesystem creation
    r"\bchmod\s+.*-R\b",                       # recursive chmod
    r"\bchmod\s+-R\b",
    r"\bchown\s+.*-R\b",                       # recursive chown
    r"\bchown\s+-R\b",
    r"\b(shutdown|reboot|poweroff|halt)\b",    # power actions
    r"\bsystemctl\s+(stop|disable|mask|isolate|poweroff|reboot)\b",
    r"\b(kill|killall|pkill)\s+.*-9\b",        # SIGKILL
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", # fork bomb
    r">\s*/dev/sd[a-z]",                       # direct raw disk redirection
    r">\s*/dev/nvme\d+",
    r"\bcurl\b.*\|\s*(ba)?sh\b",               # pipe to shell
    r"\bwget\b.*\|\s*(ba)?sh\b",
    r"\bsudo\b",                               # privilege escalation
    r"\bsu\b"
]


class RiskClassifier:
    """Evaluates proposed actions and assigns risk levels."""

    @staticmethod
    def classify_action(action_type: str, details: dict) -> Tuple[RiskLevel, str]:
        """Classify an action and return (RiskLevel, explanation)."""
        action_type = action_type.lower().strip()

        if action_type in (
            "update_user_profile",
            "add_workflow_rule",
            "remove_workflow_rule",
            "remove_user_fact",
        ):
            return RiskLevel.LEVEL_1_UI, "Update local user profile memory"

        if action_type in (
            "screenshot", "take_screenshot", "get_window_info", "get_active_window",
            "get_current_time",
            "read_clipboard", "read_file", "list_directory",
            "web_search", "web_fetch", "speak", "speak_aloud",
            "browser_open", "browser_extract", "browser_screenshot", "browser_close",
            "list_skills", "load_skill", "get_job_status", "share_file",
            "optimalx_search_notes", "optimalx_read_note", "optimalx_list_folders",
            "optimalx_read_notex_doc", "optimalx_list_notex_docs", "optimalx_list_pending_reviews",
        ):
            return RiskLevel.LEVEL_0_PASSIVE, "Read-only inspection, vocal feedback, web research, or OptimalX query"

        if action_type in (
            "click", "double_click", "move_mouse", "scroll", "type_text",
            "hotkey", "drag", "click_element",
            "browser_click", "browser_type", "browser_evaluate",
            "create_skill", "update_skill",
            "init_batch_job", "run_batch_job", "step_batch_job", "pause_batch_job", "resume_batch_job",
            "generate_batch_summary",
            "open_in_browser",
            "optimalx_write_note", "optimalx_accept_pending_review", "optimalx_reject_pending_review",
        ):
            return RiskLevel.LEVEL_1_UI, "Desktop UI interaction, skill modification, batch queue control, browser launch, or OptimalX note change"

        if action_type == "optimalx_action":
            sub_action = details.get("action", "").lower()
            if sub_action in ("get_status", "get_llm_status", "focus", "query_dom"):
                return RiskLevel.LEVEL_0_PASSIVE, "OptimalX read-only query"
            return RiskLevel.LEVEL_1_UI, f"OptimalX action: {sub_action}"

        if action_type in ("shell", "bash", "execute_command", "run_script", "execute_shell"):
            command = details.get("command", "").strip()
            return RiskClassifier.classify_shell_command(command)

        if action_type in ("write_file", "delete_file", "append_file", "download_file"):
            if action_type == "delete_file":
                return RiskLevel.LEVEL_3_DESTRUCTIVE, "File deletion request"
            if action_type == "download_file":
                dest = details.get("destination_path") or details.get("destination_dir") or ""
                dest_abs = os.path.abspath(os.path.expanduser(dest)) if dest else ""
                workspace_abs = os.path.abspath(os.path.expanduser("~/EidosWorkspace"))
                if not dest_abs or dest_abs.startswith(workspace_abs) or "/Downloads" in dest_abs:
                    return RiskLevel.LEVEL_0_PASSIVE, "Download file into user workspace"
                return RiskLevel.LEVEL_1_UI, f"Download file to {dest}"
            return RiskLevel.LEVEL_2_SHELL, "Filesystem modification"

        # Default fallback: treat unknown actions as Level 2
        return RiskLevel.LEVEL_2_SHELL, f"Unknown action type '{action_type}'"

    @staticmethod
    def classify_shell_command(command: str) -> Tuple[RiskLevel, str]:
        """Analyze shell command string and return (RiskLevel, reason)."""
        cmd_clean = command.strip()
        if not cmd_clean:
            return RiskLevel.LEVEL_0_PASSIVE, "Empty command"

        # Check for Level 3 Destructive regex patterns
        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd_clean, re.IGNORECASE):
                return RiskLevel.LEVEL_3_DESTRUCTIVE, f"Matches destructive pattern: {pattern}"

        # Split tokens safely
        try:
            tokens = shlex.split(cmd_clean)
        except Exception:
            tokens = cmd_clean.split()

        if not tokens:
            return RiskLevel.LEVEL_0_PASSIVE, "Empty command"

        first_token = tokens[0].lower()
        # Strip path if full path supplied (/bin/rm -> rm)
        if "/" in first_token:
            first_token = first_token.split("/")[-1]

        if first_token in DESTRUCTIVE_COMMANDS:
            return RiskLevel.LEVEL_3_DESTRUCTIVE, f"Command '{first_token}' is classified as destructive"

        # Check if it's purely read-only (Level 0)
        # Check full command prefix
        for ro in READ_ONLY_COMMANDS:
            if cmd_clean == ro or cmd_clean.startswith(ro + " "):
                # Ensure no output redirection (> or >>)
                if ">" not in cmd_clean and "|" not in cmd_clean:
                    return RiskLevel.LEVEL_0_PASSIVE, f"Safe read-only command: {ro}"

        # Standard shell execution is Level 2
        return RiskLevel.LEVEL_2_SHELL, f"Standard shell execution: {first_token}"
