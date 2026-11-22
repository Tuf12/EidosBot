"""Configuration and integration record manager for external MCP servers (PROJECT_JOBS.md Section 13)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
import yaml

logger = logging.getLogger(__name__)

DEFAULT_MCP_SERVERS_PATH = Path("~/.eidos/mcp.json").expanduser()
DEFAULT_INTEGRATIONS_DIR = Path("~/.eidos/integrations").expanduser()
DEFAULT_SECRETS_DIR = Path("~/.eidos/secrets").expanduser()

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _as_name_list(value: Any) -> List[str]:
    """Normalize a secret-file reference to a list of names."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        names: List[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in names:
                names.append(text)
        return names
    raise ValueError("secret reference must be a name or a list of names")


def validate_secret_name(name: str) -> str:
    """Return a safe secret or server name, or raise ValueError."""
    text = (name or "").strip()
    if not _NAME_RE.match(text) or "/" in text or ".." in text:
        raise ValueError(
            "name must be 1-64 characters of letters, numbers, '.', '_' or '-'"
        )
    return text


class MCPServerConfig(BaseModel):
    """Configuration for an individual Model Context Protocol server."""

    name: str = ""
    # Stdio transport fields
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None

    # HTTP / SSE transport fields
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)

    # Secret file names. Values live in ~/.eidos/secrets/<name>.env, not in this catalog.
    env_from: List[str] = Field(default_factory=list)
    headers_from: List[str] = Field(default_factory=list)

    # Transport type: "stdio", "sse", or "http"
    transport: str = "stdio"

    @field_validator("env_from", "headers_from", mode="before")
    @classmethod
    def _coerce_secret_refs(cls, value: Any) -> List[str]:
        return _as_name_list(value)

    def model_post_init(self, __context: Any) -> None:
        if self.url and not self.command:
            self.transport = "sse" if "sse" in self.url.lower() else "http"
        elif self.command:
            self.transport = "stdio"


class IntegrationRecord(BaseModel):
    """Outside application integration metadata and skill binding (~/.eidos/integrations/<name>/)."""

    name: str
    server: str
    description: str = ""
    use_when: str = ""
    skill_name: Optional[str] = None
    skill_path: Optional[str] = None
    skill_body: Optional[str] = None


class SecretStore:
    """Token files under ~/.eidos/secrets/. Values are never returned to the model."""

    def __init__(self, root: Optional[Path | str] = None):
        self.root = (
            Path(os.path.abspath(os.path.expanduser(root)))
            if root
            else DEFAULT_SECRETS_DIR
        )

    def path_for(self, name: str) -> Path:
        safe = validate_secret_name(name)
        return self.root / f"{safe}.env"

    def load(self, name: str) -> Dict[str, str]:
        """Read a secret file for process spawn. Missing files yield an empty mapping."""
        path = self.path_for(name)
        if not path.is_file():
            logger.warning("MCP secret file '%s' is missing", path.name)
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read MCP secret file '%s': %s", path.name, exc)
            return {}
        return _parse_env_text(text)

    def set_values(self, name: str, values: Dict[str, str], replace: bool = False) -> List[str]:
        """Merge keys into a mode-0600 env file. Empty values remove a key. Returns key names only."""
        cleaned: Dict[str, str] = {}
        for raw_key, raw_val in (values or {}).items():
            key = str(raw_key).strip()
            if not _ENV_KEY_RE.match(key):
                raise ValueError(f"invalid secret key '{key}'")
            cleaned[key] = "" if raw_val is None else str(raw_val)

        dest_preview = self.path_for(name)
        current: Dict[str, str] = {}
        if not replace and dest_preview.is_file():
            current = self.load(name)
        for key, val in cleaned.items():
            if val == "":
                current.pop(key, None)
            else:
                current[key] = val

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        dest = self.path_for(name)
        fd, tmp_path = tempfile.mkstemp(prefix=".sec_", dir=str(self.root))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for key in sorted(current):
                    handle.write(f"{key}={current[key]}\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, dest)
            os.chmod(dest, 0o600)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        return sorted(current)


def _parse_env_text(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if _ENV_KEY_RE.match(key):
            parsed[key] = val
    return parsed


class MCPConfigManager:
    """Manages MCP server specifications (~/.eidos/mcp.json) and integration records (~/.eidos/integrations/)."""

    def __init__(
        self,
        servers_file: Optional[Path | str] = None,
        integrations_dir: Optional[Path | str] = None,
        secrets_dir: Optional[Path | str] = None,
    ):
        self.servers_file = (
            Path(os.path.abspath(os.path.expanduser(servers_file)))
            if servers_file
            else DEFAULT_MCP_SERVERS_PATH
        )
        self.integrations_dir = (
            Path(os.path.abspath(os.path.expanduser(integrations_dir)))
            if integrations_dir
            else DEFAULT_INTEGRATIONS_DIR
        )
        self.secrets = SecretStore(secrets_dir)

    def load_servers(self) -> Dict[str, MCPServerConfig]:
        """Read and parse ~/.eidos/mcp.json server definitions.

        Supports standard Claude desktop / MCP JSON schema:
        {
          "mcpServers": {
             "gmail": { "url": "...", "headers": {...} },
             "local_tool": { "command": "...", "args": [...] }
          }
        }
        or direct mapping:
        {
          "gmail": { ... }
        }
        """
        if not self.servers_file.is_file():
            return {}

        try:
            with open(self.servers_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}

            raw_servers = data.get("mcpServers") if "mcpServers" in data else data
            if not isinstance(raw_servers, dict):
                return {}

            configs: Dict[str, MCPServerConfig] = {}
            for s_name, s_data in raw_servers.items():
                if not isinstance(s_data, dict):
                    continue
                try:
                    cfg = MCPServerConfig(name=str(s_name), **s_data)
                    configs[str(s_name)] = cfg
                except Exception as parse_err:
                    logger.warning("Error parsing MCP server config '%s': %s", s_name, parse_err)

            return configs
        except Exception as e:
            logger.warning("Failed to load MCP servers from %s: %s", self.servers_file, e)
            return {}

    def get_server_config(self, server_name: str) -> Optional[MCPServerConfig]:
        """Get configuration for a named MCP server."""
        servers = self.load_servers()
        return servers.get(server_name)

    def _load_raw_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Return the mcpServers mapping, or an empty catalog."""
        if not self.servers_file.is_file():
            return {}
        try:
            with open(self.servers_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.warning("Failed to read MCP catalog %s: %s", self.servers_file, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("mcpServers") if "mcpServers" in data else data
        if not isinstance(raw, dict):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def _write_catalog(self, servers: Dict[str, Dict[str, Any]]) -> None:
        self.servers_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mcpServers": servers}
        fd, tmp_path = tempfile.mkstemp(prefix="mcp_", suffix=".tmp", dir=str(self.servers_file.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.servers_file)
            os.chmod(self.servers_file, 0o600)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def list_servers_public(self) -> List[Dict[str, Any]]:
        """Catalog entries safe to show the model. Secret values are omitted."""
        public: List[Dict[str, Any]] = []
        for name, raw in self._load_raw_catalog().items():
            public.append(self._public_entry(name, raw))
        public.sort(key=lambda item: item["name"])
        return public

    @staticmethod
    def _public_entry(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": name,
            "command": raw.get("command"),
            "args": raw.get("args") or [],
            "url": raw.get("url"),
            "cwd": raw.get("cwd"),
            "env_from": _as_name_list(raw.get("env_from")),
            "headers_from": _as_name_list(raw.get("headers_from")),
            "has_inline_secrets": bool(raw.get("env") or raw.get("headers")),
        }

    def upsert_server(
        self,
        name: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        url: Optional[str] = None,
        cwd: Optional[str] = None,
        env_from: Any = None,
        headers_from: Any = None,
        env: Any = None,
        headers: Any = None,
    ) -> Dict[str, Any]:
        """Create or update a catalog entry. Inline env and headers are rejected."""
        if env:
            raise ValueError("put tokens in set_secret and reference them with env_from")
        if headers:
            raise ValueError("put tokens in set_secret and reference them with headers_from")

        safe = validate_secret_name(name)
        catalog = self._load_raw_catalog()
        existing = dict(catalog.get(safe) or {})
        entry: Dict[str, Any] = {}
        for kept in ("env", "headers"):
            if existing.get(kept):
                entry[kept] = existing[kept]

        cmd = (command if command is not None else existing.get("command") or "")
        cmd = str(cmd).strip() if cmd else ""
        remote = (url if url is not None else existing.get("url") or "")
        remote = str(remote).strip() if remote else ""
        if not cmd and not remote:
            raise ValueError("command or url is required")
        if cmd:
            entry["command"] = cmd
            entry["args"] = [str(a) for a in (args if args is not None else existing.get("args") or [])]
        elif "command" in existing and command is None:
            entry["command"] = existing["command"]
            entry["args"] = existing.get("args") or []
        if remote:
            entry["url"] = remote
        workdir = cwd if cwd is not None else existing.get("cwd")
        if workdir:
            entry["cwd"] = str(workdir)
        if env_from is not None:
            entry["env_from"] = [validate_secret_name(n) for n in _as_name_list(env_from)]
        elif existing.get("env_from"):
            entry["env_from"] = _as_name_list(existing.get("env_from"))
        if headers_from is not None:
            entry["headers_from"] = [validate_secret_name(n) for n in _as_name_list(headers_from)]
        elif existing.get("headers_from"):
            entry["headers_from"] = _as_name_list(existing.get("headers_from"))

        catalog[safe] = entry
        self._write_catalog(catalog)
        return self._public_entry(safe, entry)

    def remove_server(self, name: str) -> bool:
        """Remove a catalog entry. Secret files are left in place."""
        safe = validate_secret_name(name)
        catalog = self._load_raw_catalog()
        if safe not in catalog:
            return False
        del catalog[safe]
        self._write_catalog(catalog)
        return True

    def launch_allowlist_key(self, name: str, command: Optional[str], args: Optional[List[str]], url: Optional[str]) -> str:
        """Stable gatekeeper key for a server command or URL. Contains no secrets."""
        safe = validate_secret_name(name)
        if command and str(command).strip():
            parts = [str(command).strip()] + [str(a) for a in (args or [])]
            return "mcp-server:" + safe + ":" + " ".join(parts)
        if url and str(url).strip():
            return "mcp-server:" + safe + ":" + str(url).strip()
        existing = self._load_raw_catalog().get(safe) or {}
        if existing.get("command"):
            parts = [str(existing["command"])] + [str(a) for a in (existing.get("args") or [])]
            return "mcp-server:" + safe + ":" + " ".join(parts)
        if existing.get("url"):
            return "mcp-server:" + safe + ":" + str(existing["url"])
        return "mcp-server:" + safe

    def materialize_server(self, config: MCPServerConfig) -> MCPServerConfig:
        """Copy a server config with secret files applied. The copy is not written back."""
        runtime = config.model_copy(deep=True)
        env = dict(config.env or {})
        for secret_name in config.env_from or []:
            env.update(self.secrets.load(secret_name))
        runtime.env = env
        headers = dict(config.headers or {})
        for secret_name in config.headers_from or []:
            headers.update(self.secrets.load(secret_name))
        runtime.headers = headers
        return runtime

    def load_integrations(self) -> Dict[str, IntegrationRecord]:
        """Discover and load integration records from ~/.eidos/integrations/<name>/."""
        if not self.integrations_dir.is_dir():
            return {}

        integrations: Dict[str, IntegrationRecord] = {}
        try:
            for item in self.integrations_dir.iterdir():
                if not item.is_dir() or item.name.startswith("."):
                    continue

                yaml_path = item / "integration.yaml"
                if not yaml_path.is_file():
                    yaml_path = item / "integration.yml"
                if not yaml_path.is_file():
                    continue

                try:
                    with open(yaml_path, "r", encoding="utf-8") as yf:
                        ydata = yaml.safe_load(yf) or {}
                    if not isinstance(ydata, dict):
                        continue

                    name = str(ydata.get("name") or item.name).strip()
                    server = str(ydata.get("server") or name).strip()
                    desc = str(ydata.get("description") or "").strip()
                    use_when = str(ydata.get("use_when") or "").strip()

                    skill_path_obj = item / "SKILL.md"
                    skill_path_str: Optional[str] = None
                    skill_body_str: Optional[str] = None
                    if skill_path_obj.is_file():
                        skill_path_str = str(skill_path_obj)
                        try:
                            with open(skill_path_obj, "r", encoding="utf-8") as sf:
                                skill_body_str = sf.read().strip()
                        except Exception as se:
                            logger.debug("Failed reading %s: %s", skill_path_obj, se)

                    record = IntegrationRecord(
                        name=name,
                        server=server,
                        description=desc,
                        use_when=use_when,
                        skill_name=name,
                        skill_path=skill_path_str,
                        skill_body=skill_body_str,
                    )
                    integrations[name] = record
                except Exception as ie:
                    logger.warning("Failed parsing integration record at %s: %s", item, ie)
        except Exception as e:
            logger.warning("Failed reading integrations dir %s: %s", self.integrations_dir, e)

        return integrations

    def get_integration(self, name: str) -> Optional[IntegrationRecord]:
        """Fetch integration record by name."""
        return self.load_integrations().get(name)

    def list_integrations_summary(self) -> List[Dict[str, Any]]:
        """Return compact summary list of configured integrations for agent tools."""
        recs = self.load_integrations()
        return [
            {
                "name": r.name,
                "server": r.server,
                "description": r.description,
                "use_when": r.use_when,
            }
            for r in recs.values()
        ]

    @staticmethod
    def resolve_job_integrations(
        job_manifest: Optional[Dict[str, Any]],
        group_yaml_data: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Resolve unique integration names from inherited group.yaml plus job brief (Section 13.2 / 14)."""
        integrations: List[str] = []

        # 1. Inherited integration from nearest group.yaml
        if group_yaml_data and isinstance(group_yaml_data, dict):
            g_int = group_yaml_data.get("integration") or group_yaml_data.get("integrations")
            if isinstance(g_int, str) and g_int.strip():
                integrations.append(g_int.strip())
            elif isinstance(g_int, list):
                for item in g_int:
                    if isinstance(item, str) and item.strip():
                        integrations.append(item.strip())
                    elif isinstance(item, dict) and item.get("name"):
                        integrations.append(str(item["name"]).strip())

        # 2. Leaf brief integrations from job.yaml
        if job_manifest and isinstance(job_manifest, dict):
            leaf_ints = job_manifest.get("integrations") or job_manifest.get("integration")
            if isinstance(leaf_ints, str) and leaf_ints.strip():
                if leaf_ints.strip() not in integrations:
                    integrations.append(leaf_ints.strip())
            elif isinstance(leaf_ints, list):
                for item in leaf_ints:
                    val = ""
                    if isinstance(item, str) and item.strip():
                        val = item.strip()
                    elif isinstance(item, dict) and item.get("name"):
                        val = str(item["name"]).strip()
                    if val and val not in integrations:
                        integrations.append(val)

        return integrations
