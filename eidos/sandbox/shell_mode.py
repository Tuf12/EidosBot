"""Resolve whether execute_shell runs in bwrap (nest) or on the host."""

import os
import re
from typing import List, Optional, Union

ShellModeRequest = Union[bool, str, None]

# Absolute or ~-prefixed paths embedded in a shell command (heuristic).
_PATH_TOKEN_RE = re.compile(
    r"""(?:^|[\s'"=<>(])(~(?:/[^\s'"`;|&<>$()\\]+)?|/(?:[^\s'"`;|&<>$()\\]+))"""
)

_HOST_SAFE_PREFIXES = ("/dev/", "/proc/", "/sys/")


def path_under_root(path: str, roots: List[str]) -> bool:
    """True if path is exactly root or a descendant of one of roots."""
    if not roots:
        return False
    expanded = os.path.abspath(os.path.expanduser(path))
    for root in roots:
        base = os.path.abspath(os.path.expanduser(root))
        if expanded == base or expanded.startswith(base + os.sep):
            return True
    return False


def command_references_outside_roots(command: str, roots: List[str]) -> bool:
    """Best-effort scan for filesystem paths outside writable roots."""
    for match in _PATH_TOKEN_RE.finditer(command):
        token = match.group(1)
        if not token:
            continue
        expanded = os.path.abspath(os.path.expanduser(token))
        if any(expanded.startswith(prefix) for prefix in _HOST_SAFE_PREFIXES):
            continue
        if os.path.isabs(expanded) or token.startswith("~"):
            if not path_under_root(expanded, roots):
                return True
    return False


def resolve_shell_sandboxed(
    requested: ShellModeRequest,
    *,
    command: str,
    cwd: Optional[str],
    writable_roots: List[str],
    sandbox_enabled: bool,
    default_shell_mode: str = "auto",
) -> bool:
    """
    Decide bwrap vs host execution.

    - explicit True/False from the model always wins
    - default_shell_mode: sandboxed | host | auto
    - auto: host when cwd is omitted (desktop agent); sandbox only when cwd and
      command paths stay inside writable_roots
    """
    if not sandbox_enabled:
        return False

    if requested is True:
        return True
    if requested is False:
        return False

    mode = (default_shell_mode or "auto").strip().lower()
    if mode in ("sandboxed", "sandbox", "true"):
        return True
    if mode in ("host", "false"):
        return False

    # auto
    if requested not in (None, "auto", ""):
        return False

    if not writable_roots:
        return False

    if cwd is None:
        return False

    work_dir = os.path.abspath(os.path.expanduser(cwd))
    if not path_under_root(work_dir, writable_roots):
        return False
    if command_references_outside_roots(command, writable_roots):
        return False
    return True
