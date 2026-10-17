"""Deterministic sub-millisecond entity extractor for episodic trajectories and queries."""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from eidos.memory.types import (
    ENTITY_TYPE_COMMAND,
    ENTITY_TYPE_DEVICE,
    ENTITY_TYPE_DOMAIN,
    ENTITY_TYPE_PATH,
    ENTITY_TYPE_PROJECT,
    ENTITY_TYPE_WINDOW,
    EpisodeRecord,
    ExtractedEntity,
)

# Common Linux wrappers to strip when isolating executed binaries
WRAPPER_COMMANDS: Set[str] = {
    "sudo",
    "nohup",
    "env",
    "nice",
    "xargs",
    "pkexec",
    "doas",
    "time",
    "exec",
    "chroot",
    "systemd-inhibit",
    "strace",
    "ltrace",
    "bash",
    "sh",
    "zsh",
}

# Shell builtins and control flow keywords to ignore
SHELL_BUILTINS: Set[str] = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "do",
    "done",
    "case",
    "esac",
    "in",
    "echo",
    "printf",
    "export",
    "set",
    "unset",
    "test",
    "true",
    "false",
    "return",
    "exit",
    "alias",
    "source",
    ".",
    "eval",
    "cd",
    "pwd",
    "read",
    "shift",
    "trap",
    "wait",
    "local",
    "break",
    "continue",
}

# High-frequency developer and system commands recognized in free text
COMMON_SYSTEM_COMMANDS: Set[str] = {
    "pactl",
    "pacmd",
    "alsamixer",
    "amixer",
    "wpctl",
    "pw-cli",
    "xrandr",
    "xdotool",
    "xclip",
    "xsel",
    "xset",
    "brightnessctl",
    "xbacklight",
    "git",
    "pytest",
    "python",
    "python3",
    "pip",
    "npm",
    "node",
    "yarn",
    "pnpm",
    "cargo",
    "rustc",
    "docker",
    "docker-compose",
    "kubectl",
    "systemctl",
    "journalctl",
    "ffmpeg",
    "ffprobe",
    "curl",
    "wget",
    "rsync",
    "tar",
    "find",
    "grep",
    "rg",
    "sed",
    "awk",
    "cut",
    "sort",
    "uniq",
    "cat",
    "head",
    "tail",
    "ls",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall",
    "pgrep",
    "ps",
    "top",
    "htop",
    "df",
    "du",
    "free",
    "uname",
    "ip",
    "nmcli",
    "ruff",
    "mypy",
    "black",
    "flake8",
    "gcc",
    "g++",
    "make",
    "cmake",
    "ninja",
    "gh",
    "ssh",
    "scp",
    "nano",
    "vim",
    "vi",
    "tmux",
    "screen",
    "zip",
    "unzip",
    "gzip",
    "gunzip",
    "wireplumber",
    "bluetoothctl",
}

# Regex patterns for paths, hardware devices, projects, and domains
DEVICE_PATTERN = re.compile(
    r"\b((?:HDMI|DP|eDP|VGA|DVI)-[0-9]+|(?:alsa_output|alsa_input|bluez_sink|bluez_source)\.[a-zA-Z0-9_\-\.]+)\b",
    re.IGNORECASE,
)

ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![:a-zA-Z0-9_\-\./])(/(?:etc|tmp|var|home|usr|opt|dev|proc|sys|root|bin|sbin)\b(?:/[a-zA-Z0-9_\-\.]+)*|/[a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)+)"
)

TILDE_PATH_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9_\-\./])(~[a-zA-Z0-9_\-\.]*(?:/[a-zA-Z0-9_\-\.]+)*)"
)

FILE_EXT_PATTERN = re.compile(
    r"\b([a-zA-Z0-9_\-\.]+\.(?:py|json|ya?ml|tsx?|jsx?|sh|bash|conf|cfg|ini|md|txt|sql|toml|lock|csv|html|css|xml|gz|tar|zip|png|jpe?g|svg|log))\b",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r"https?://([a-zA-Z0-9\-\.]+)(?::[0-9]+)?(?:/[^\s\"'>]*)?",
    re.IGNORECASE,
)

PROJECT_DIR_PATTERN = re.compile(
    r"(?:/CursorProjects/|/(?:projects|workspace|repos)/)([a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)

EXPLICIT_TAG_PATTERN = re.compile(
    r"\b(cmd|command|path|file|device|dev|project|proj|window|app|domain|url):([^\s\"'>]+)",
    re.IGNORECASE,
)


class EntityExtractor:
    """High-speed deterministic extractor for commands, paths, hardware devices, and domains."""

    def extract_from_episode(self, record: EpisodeRecord) -> List[ExtractedEntity]:
        """Extract all unique entities referenced within an EpisodeRecord."""
        entities_by_id: Dict[str, ExtractedEntity] = {}

        def record_entity(
            entity_type: str,
            name: str,
            role: str = "",
            metadata: Optional[Dict[str, Any]] = None,
        ) -> None:
            clean_name = name.strip()
            if not clean_name:
                return

            norm_name = clean_name.lower()
            prefix_map = {
                ENTITY_TYPE_COMMAND: "cmd",
                ENTITY_TYPE_PATH: "path",
                ENTITY_TYPE_DEVICE: "device",
                ENTITY_TYPE_PROJECT: "project",
                ENTITY_TYPE_WINDOW: "window",
                ENTITY_TYPE_DOMAIN: "domain",
            }
            prefix = prefix_map.get(entity_type, entity_type)
            entity_id = f"{prefix}:{norm_name}"

            if entity_id in entities_by_id:
                existing = entities_by_id[entity_id]
                existing.metadata["frequency"] = existing.metadata.get("frequency", 1) + 1
            else:
                meta = dict(metadata or {})
                meta["frequency"] = 1
                entities_by_id[entity_id] = ExtractedEntity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=clean_name,
                    normalized_name=norm_name,
                    role=role,
                    metadata=meta,
                )

        # 1. Inspect Task Goal for paths, devices, domains
        self._extract_from_text(record.task_goal, record_entity)

        # 2. Inspect Trajectory Steps
        for step in record.trajectory:
            # State snapshot: window & workspace CWD
            state = step.state_snapshot or {}
            win_class = state.get("active_window_class") or ""
            if win_class and win_class.lower() not in ("unknown", "none"):
                record_entity(ENTITY_TYPE_WINDOW, win_class, role="active_window")

            cwd = state.get("cwd") or ""
            if cwd:
                self._extract_paths(cwd, record_entity)
                self._extract_project(cwd, record_entity)

            # Tool Calls
            tool_calls = step.tool_calls or []
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                if not isinstance(args, dict):
                    continue

                if name == "execute_shell":
                    cmd_str = args.get("command", "")
                    if isinstance(cmd_str, str):
                        self._extract_commands_from_shell(cmd_str, record_entity)
                        self._extract_from_text(cmd_str, record_entity)

                elif name in ("write_file", "edit_file", "read_file", "share_file"):
                    p = args.get("path")
                    if isinstance(p, str):
                        record_entity(ENTITY_TYPE_PATH, p, role="target_file")
                        self._extract_project(p, record_entity)

                elif name == "download_file":
                    p = args.get("path") or args.get("destination")
                    if isinstance(p, str):
                        record_entity(ENTITY_TYPE_PATH, p, role="target_file")
                    u = args.get("url")
                    if isinstance(u, str):
                        self._extract_domains(u, record_entity)

                elif name in ("web_fetch", "web_search", "browser_open"):
                    u = args.get("url")
                    if isinstance(u, str):
                        self._extract_domains(u, record_entity)
                    q = args.get("query")
                    if isinstance(q, str):
                        self._extract_from_text(q, record_entity)

            # Tool Observations (look for devices, paths & domains reported in output)
            tool_obs = step.tool_observations or []
            for obs in tool_obs:
                if isinstance(obs, dict):
                    stdout = obs.get("stdout") or ""
                    stderr = obs.get("stderr") or ""
                    err = obs.get("error") or ""
                    comb = f"{stdout}\n{stderr}\n{err}"
                    # Truncate very large observation streams to safeguard regex performance
                    comb_snippet = comb[:10000] if len(comb) > 10000 else comb
                    self._extract_devices(comb_snippet, record_entity)
                    self._extract_domains(comb_snippet, record_entity)
                    self._extract_paths(comb_snippet, record_entity)

        return list(entities_by_id.values())

    def extract_from_query(self, query: str) -> List[ExtractedEntity]:
        """Extract explicit and implicit entities from a search / recall query."""
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return []

        entities_by_id: Dict[str, ExtractedEntity] = {}

        def record_entity(
            entity_type: str,
            name: str,
            role: str = "query_filter",
            metadata: Optional[Dict[str, Any]] = None,
        ) -> None:
            clean_name = name.strip()
            if not clean_name:
                return

            norm_name = clean_name.lower()
            prefix_map = {
                ENTITY_TYPE_COMMAND: "cmd",
                ENTITY_TYPE_PATH: "path",
                ENTITY_TYPE_DEVICE: "device",
                ENTITY_TYPE_PROJECT: "project",
                ENTITY_TYPE_WINDOW: "window",
                ENTITY_TYPE_DOMAIN: "domain",
            }
            prefix = prefix_map.get(entity_type, entity_type)
            entity_id = f"{prefix}:{norm_name}"

            if entity_id not in entities_by_id:
                meta = dict(metadata or {})
                meta["frequency"] = 1
                entities_by_id[entity_id] = ExtractedEntity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=clean_name,
                    normalized_name=norm_name,
                    role=role,
                    metadata=meta,
                )

        # 1. Parse explicit prefix tags (e.g., cmd:pactl, device:HDMI-0)
        tag_map = {
            "cmd": ENTITY_TYPE_COMMAND,
            "command": ENTITY_TYPE_COMMAND,
            "path": ENTITY_TYPE_PATH,
            "file": ENTITY_TYPE_PATH,
            "device": ENTITY_TYPE_DEVICE,
            "dev": ENTITY_TYPE_DEVICE,
            "project": ENTITY_TYPE_PROJECT,
            "proj": ENTITY_TYPE_PROJECT,
            "window": ENTITY_TYPE_WINDOW,
            "app": ENTITY_TYPE_WINDOW,
            "domain": ENTITY_TYPE_DOMAIN,
            "url": ENTITY_TYPE_DOMAIN,
        }
        for match in EXPLICIT_TAG_PATTERN.finditer(cleaned_query):
            tag_type = match.group(1).lower()
            val = match.group(2)
            if tag_type in tag_map:
                ent_type = tag_map[tag_type]
                if ent_type == ENTITY_TYPE_DOMAIN:
                    if val.startswith("http://") or val.startswith("https://"):
                        parsed = urlparse(val)
                        val = parsed.netloc or val
                    val = val.split("/")[0]
                record_entity(ent_type, val, role="explicit_tag")

        # Strip explicit tags for implicit extraction
        stripped_query = EXPLICIT_TAG_PATTERN.sub(" ", cleaned_query)

        # 2. Extract implicit paths, devices, domains, and known system commands
        self._extract_from_text(stripped_query, record_entity)

        # 3. Check for standalone command tokens in query
        words = re.findall(r"\b[a-zA-Z0-9_\-]+\b", stripped_query)
        for w in words:
            w_lower = w.lower()
            if w_lower in COMMON_SYSTEM_COMMANDS:
                record_entity(ENTITY_TYPE_COMMAND, w_lower, role="inferred_command")

        return list(entities_by_id.values())

    def _extract_from_text(self, text: str, record_entity: Any) -> None:
        """Helper to scan raw text for paths, devices, projects, and domains."""
        if not text:
            return
        self._extract_paths(text, record_entity)
        self._extract_devices(text, record_entity)
        self._extract_project(text, record_entity)
        self._extract_domains(text, record_entity)

    def _extract_commands_from_shell(self, cmd_line: str, record_entity: Any) -> None:
        """Parse shell command string into invoked binaries and utilities."""
        if not cmd_line:
            return

        # Handle command substitution / subshells: $(cmd) or `cmd`
        for sub_m in re.finditer(r"\$\(([\s\S]*?)\)|`([\s\S]*?)`", cmd_line):
            sub_cmd = sub_m.group(1) or sub_m.group(2)
            if sub_cmd and sub_cmd.strip():
                self._extract_commands_from_shell(sub_cmd, record_entity)

        # Split pipeline and chaining operators
        segments = re.split(r"(?:&&|\|\||;|\||\n)", cmd_line)
        for seg in segments:
            seg_clean = seg.strip()
            if not seg_clean:
                continue

            # Strip leading environment variable assignments: e.g. FOO=bar BAZ=1 cmd ...
            seg_clean = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]*\s+)+", "", seg_clean).strip()

            # Handle bash -c '...' or sh -c "..." recursion
            sh_c_match = re.search(r"\b(?:bash|sh|zsh)\s+-c\s+([\"'])([\s\S]*?)\1", seg_clean)
            if sh_c_match:
                inner_cmd = sh_c_match.group(2)
                self._extract_commands_from_shell(inner_cmd, record_entity)
                # Remove the bash -c '...' portion so outer segment does not record shell binary
                seg_clean = (seg_clean[:sh_c_match.start()] + seg_clean[sh_c_match.end():]).strip()
                if not seg_clean:
                    continue

            # Tokenize by whitespace
            tokens = seg_clean.split()
            if not tokens:
                continue

            # If find ... -exec cmd {} +, extract the executed utility
            if "-exec" in tokens:
                try:
                    exec_idx = tokens.index("-exec")
                    if exec_idx + 1 < len(tokens):
                        sub_bin = Path(tokens[exec_idx + 1]).name
                        if (
                            sub_bin
                            and sub_bin.lower() not in SHELL_BUILTINS
                            and sub_bin.lower() not in WRAPPER_COMMANDS
                            and not sub_bin.isdigit()
                            and len(sub_bin) >= 2
                            and re.match(r"^[a-zA-Z0-9_\-\.]+$", sub_bin)
                        ):
                            record_entity(ENTITY_TYPE_COMMAND, sub_bin, role="invoked_command")
                except Exception:
                    pass

            idx = 0
            while idx < len(tokens):
                tok = tokens[idx]

                # Skip environment variable assignments: e.g. FOO=bar, VAR=1
                if "=" in tok and not tok.startswith("-") and not tok.startswith("/"):
                    idx += 1
                    continue

                tok_clean = re.sub(r"^[^a-zA-Z0-9_\-\./]+|[^a-zA-Z0-9_\-\./]+$", "", tok)

                if not tok_clean:
                    idx += 1
                    continue

                tok_lower = tok_clean.lower()
                # If token is a wrapper command, advance to next token
                if tok_lower in WRAPPER_COMMANDS:
                    idx += 1
                    continue

                # If token is a flag/option for a wrapper (e.g. -n 10, -u user, -i, -c)
                if tok_clean.startswith("-"):
                    idx += 1
                    # If next token is an argument to this flag (e.g. 10), skip it too
                    if idx < len(tokens) and not tokens[idx].startswith("-"):
                        # If next token looks like an option argument rather than a binary path
                        if tokens[idx].isdigit() or tokens[idx] in ("root", "daemon", "user"):
                            idx += 1
                    continue

                # Strip directory prefixes: e.g. /usr/bin/pactl -> pactl, ./script.sh -> script.sh
                binary_name = Path(tok_clean).name

                # Filter out shell builtins and invalid names
                if (
                    binary_name
                    and binary_name.lower() not in SHELL_BUILTINS
                    and binary_name.lower() not in WRAPPER_COMMANDS
                    and not binary_name.isdigit()
                    and len(binary_name) >= 2
                    and re.match(r"^[a-zA-Z0-9_\-\.]+$", binary_name)
                ):
                    record_entity(ENTITY_TYPE_COMMAND, binary_name, role="invoked_command")
                    break
                idx += 1

    def _extract_paths(self, text: str, record_entity: Any) -> None:
        """Extract absolute paths, tilde paths, and filenames with extensions."""
        # Absolute paths
        for m in ABSOLUTE_PATH_PATTERN.finditer(text):
            p = m.group(1).rstrip(".,;:)'\"")
            if p not in ("/dev/null", "/dev/zero", "/dev/stdin", "/dev/stdout", "/dev/stderr") and len(p) > 2:
                record_entity(ENTITY_TYPE_PATH, p, role="referenced_path")

        # Tilde paths
        for m in TILDE_PATH_PATTERN.finditer(text):
            p = m.group(1).rstrip(".,;:)'\"")
            if len(p) >= 2:
                record_entity(ENTITY_TYPE_PATH, p, role="referenced_path")

        # Relative files with extensions
        for m in FILE_EXT_PATTERN.finditer(text):
            p = m.group(1).rstrip(".,;:)'\"")
            # Don't capture numbers like 1.0 or pure domain names
            if not re.match(r"^[0-9]+\.[0-9]+$", p):
                record_entity(ENTITY_TYPE_PATH, p, role="referenced_path")

    def _extract_devices(self, text: str, record_entity: Any) -> None:
        """Extract hardware displays and audio sinks/sources."""
        for m in DEVICE_PATTERN.finditer(text):
            dev = m.group(1).strip()
            record_entity(ENTITY_TYPE_DEVICE, dev, role="hardware_device")

    def _extract_project(self, text: str, record_entity: Any) -> None:
        """Extract workspace project names from path strings."""
        for m in PROJECT_DIR_PATTERN.finditer(text):
            proj = m.group(1).strip()
            if proj:
                record_entity(ENTITY_TYPE_PROJECT, proj, role="workspace_project")

    def _extract_domains(self, text: str, record_entity: Any) -> None:
        """Extract web domains from URLs."""
        for m in URL_PATTERN.finditer(text):
            host = m.group(1).strip()
            if host:
                record_entity(ENTITY_TYPE_DOMAIN, host, role="network_domain")
