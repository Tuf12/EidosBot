"""Registry of files handed between EidosBot and the web app.

The WebSocket only announces that a file is ready. The bytes are served later
by GET /api/files/{id}. Uploads from the web app are saved into the workspace
inbox so the agent can read them with its normal file tools.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EXTRA_MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".py": "text/x-python",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".jsx": "text/plain",
    ".js": "text/plain",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".toml": "text/plain",
    ".rst": "text/plain",
    ".sh": "text/plain",
    ".txt": "text/plain",
}


def guess_mime(path: str) -> str:
    """Best-effort content type from the filename."""
    ext = Path(path).suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def content_disposition(filename: str, download: bool) -> str:
    """Content-Disposition header that keeps unicode names intact."""
    kind = "attachment" if download else "inline"
    raw = safe_filename(filename)
    ascii_name = raw.encode("ascii", "ignore").decode() or "file"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "")
    quoted = urllib.parse.quote(raw)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def safe_filename(name: str) -> str:
    """Strip directories and odd characters from an uploaded filename."""
    base = os.path.basename(name or "").replace("\x00", "").strip()
    base = re.sub(r"[^\w.\- ()]", "_", base).strip("._ ")
    return (base or "file")[:180]


class SharedFileRegistry:
    """Persisted list of files the web app is allowed to open, by id."""

    def __init__(
        self,
        index_path: Optional[str] = None,
        inbox_dir: Optional[str] = None,
        on_shared: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        default_index = Path.home() / ".eidos" / "shared_files.json"
        default_inbox = Path.home() / "EidosWorkspace" / "inbox"
        self._index_path = Path(os.path.expanduser(index_path)).resolve() if index_path else default_index
        self._inbox = Path(os.path.expanduser(inbox_dir)).resolve() if inbox_dir else default_inbox
        self._on_shared = on_shared
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}
        self._load()

    def register(
        self,
        path: str,
        *,
        direction: str = "bot",
        conversation_id: str = "",
        note: str = "",
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register an existing file and notify listeners the first time."""
        resolved = Path(os.path.abspath(os.path.expanduser(path or "")))
        if not str(path or "").strip():
            raise ValueError("No file path provided")
        if resolved.is_dir():
            raise ValueError(f"'{resolved}' is a directory. Share a file.")
        if not resolved.is_file():
            raise ValueError(f"File not found: {resolved}")
        if not os.access(resolved, os.R_OK):
            raise ValueError(f"File is not readable: {resolved}")

        conv = str(conversation_id or "")
        name = safe_filename(display_name or resolved.name)
        note_text = (note or "").strip()
        created = False

        with self._lock:
            existing = self._find_locked(str(resolved), conv)
            if existing:
                if note_text and note_text != existing.get("note"):
                    existing["note"] = note_text
                    self._save_locked()
                record = dict(existing)
            else:
                file_id = uuid.uuid4().hex[:12]
                record = {
                    "id": file_id,
                    "name": name,
                    "path": str(resolved),
                    "mime": guess_mime(name),
                    "size": resolved.stat().st_size,
                    "direction": "user" if direction == "user" else "bot",
                    "conversation_id": conv,
                    "note": note_text,
                    "created_at": time.time(),
                }
                self._items[file_id] = record
                self._save_locked()
                record = dict(record)
                created = True

        public = self.to_public(record)
        public["already_shared"] = not created
        if created and self._on_shared:
            try:
                self._on_shared(public)
            except Exception as exc:
                logger.warning("File share listener failed: %s", exc)
        return public

    def save_upload(
        self,
        filename: str,
        data: bytes,
        *,
        conversation_id: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """Write an uploaded file into the inbox and register it."""
        if not data:
            raise ValueError("Empty file")
        safe = safe_filename(filename)
        file_id = uuid.uuid4().hex[:12]
        self._inbox.mkdir(parents=True, exist_ok=True)
        dest = self._inbox / f"{file_id}_{safe}"
        dest.write_bytes(data)
        return self.register(
            str(dest),
            direction="user",
            conversation_id=conversation_id,
            note=note,
            display_name=safe,
        )

    def get(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Return one public record, or None."""
        with self._lock:
            record = self._items.get(file_id)
            if not record:
                return None
            snapshot = dict(record)
        return self.to_public(snapshot)

    def resolve_path(self, file_id: str) -> Optional[Path]:
        """Return the on-disk path for a registered file id."""
        with self._lock:
            record = self._items.get(file_id)
            if not record:
                return None
            return Path(record["path"])

    def list_records(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List shared files, optionally for one conversation."""
        with self._lock:
            items = [dict(rec) for rec in self._items.values()]
        if conversation_id is not None:
            want = str(conversation_id)
            items = [rec for rec in items if str(rec.get("conversation_id") or "") == want]
        items.sort(key=lambda rec: rec.get("created_at") or 0)
        return [self.to_public(rec) for rec in items]

    def to_public(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """JSON shape sent to the web app. Size is read live when the file is still there."""
        path = Path(record.get("path") or "")
        exists = path.is_file()
        size = record.get("size") or 0
        if exists:
            try:
                size = path.stat().st_size
            except OSError:
                exists = False
        return {
            "id": record.get("id"),
            "name": record.get("name") or path.name or "file",
            "path": str(path) if record.get("path") else "",
            "mime": record.get("mime") or "application/octet-stream",
            "size": int(size or 0),
            "direction": record.get("direction") or "bot",
            "note": record.get("note") or "",
            "conversation_id": record.get("conversation_id") or "",
            "created_at": record.get("created_at") or 0,
            "missing": not exists,
        }

    def _find_locked(self, path: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        for rec in self._items.values():
            if rec.get("path") == path and str(rec.get("conversation_id") or "") == conversation_id:
                return rec
        return None

    def _load(self) -> None:
        if not self._index_path.is_file():
            return
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            items = raw.get("files") if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for rec in items:
                    if isinstance(rec, dict) and rec.get("id") and rec.get("path"):
                        self._items[str(rec["id"])] = rec
        except Exception as exc:
            logger.warning("Could not read shared file index %s: %s", self._index_path, exc)

    def _save_locked(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"files": list(self._items.values())}
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._index_path)
