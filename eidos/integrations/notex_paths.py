"""Desktop NoteX directory names.

Matches OptimalX Desktop `electron/files/directory-names.js`: the folder the user
typed is the directory name, `__chat_` storage stays on the UUID, and a UUID
directory is used only while the named directory is not on disk yet.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MAX_SEGMENT_LENGTH = 200
_CHAT_PREFIX = "__chat_"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_EDGE_DOTS = re.compile(r"^[\s.]+|[\s.]+$")
_REPEATED_SPACES = re.compile(r" {2,}")


def is_chat_storage_name(name: object) -> bool:
    return isinstance(name, str) and name.startswith(_CHAT_PREFIX)


def sanitize_segment(name: object, global_id: object) -> str:
    text = str(name or "").strip()
    text = text.replace("/", " - ").replace("\\", " - ")
    text = _CONTROL_CHARS.sub("", text)
    text = _REPEATED_SPACES.sub(" ", text)
    text = _EDGE_DOTS.sub("", text)
    if not text or text in {".", ".."}:
        return _fallback_segment(global_id)
    if len(text) > MAX_SEGMENT_LENGTH:
        text = _EDGE_DOTS.sub("", text[:MAX_SEGMENT_LENGTH])
        if not text or text in {".", ".."}:
            return _fallback_segment(global_id)
    return text


def _fallback_segment(global_id: object) -> str:
    fallback = str(global_id or "").strip()
    return fallback or "unnamed"


def _allocate(used: set[str], base: str) -> str:
    if base not in used:
        used.add(base)
        return base
    number = 2
    candidate = f"{base} {number}"
    while candidate in used:
        number += 1
        candidate = f"{base} {number}"
    used.add(candidate)
    return candidate


def assign_directory_names(rows: list[dict]) -> dict[str, dict[str, str]]:
    """Map a subfolder global id to `{parent_segment, subfolder_segment}`."""
    parents: dict[str, dict] = {}
    for row in rows:
        if is_chat_storage_name(row.get("subfolder_name")):
            continue
        parent_id = str(row.get("parent_global_id") or "")
        parent = parents.get(parent_id)
        if parent is None:
            parent = {
                "parent_global_id": parent_id,
                "parent_name": row.get("parent_name"),
                "parent_created_at": int(row.get("parent_created_at") or 0),
                "children": [],
            }
            parents[parent_id] = parent
        parent["children"].append(row)

    parent_list = sorted(
        parents.values(),
        key=lambda item: (item["parent_created_at"], item["parent_global_id"]),
    )
    used_parents: set[str] = set()
    parent_segments: dict[str, str] = {}
    for parent in parent_list:
        parent_segments[parent["parent_global_id"]] = _allocate(
            used_parents,
            sanitize_segment(parent["parent_name"], parent["parent_global_id"]),
        )

    assigned: dict[str, dict[str, str]] = {}
    for parent in parent_list:
        children = sorted(
            parent["children"],
            key=lambda item: (
                int(item.get("subfolder_created_at") or 0),
                str(item.get("subfolder_global_id") or item.get("global_id") or ""),
            ),
        )
        used_children: set[str] = set()
        for child in children:
            subfolder_id = str(child.get("subfolder_global_id") or child.get("global_id") or "")
            assigned[subfolder_id] = {
                "parent_segment": parent_segments[parent["parent_global_id"]],
                "subfolder_segment": _allocate(
                    used_children,
                    sanitize_segment(child.get("subfolder_name"), subfolder_id),
                ),
            }
    return assigned


def load_directory_name_rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        """
        SELECT
            s.global_id AS subfolder_global_id,
            s.name AS subfolder_name,
            s.created_at AS subfolder_created_at,
            p.name AS parent_name,
            p.created_at AS parent_created_at,
            p.global_id AS parent_global_id
        FROM subfolders s
        JOIN parent_folders p ON p.id = s.parent_folder_id
        WHERE s.deleted_at IS NULL AND p.deleted_at IS NULL
        """
    )
    return [dict(row) for row in cur.fetchall()]


def resolve_notex_root(notex_dir: Path, conn: sqlite3.Connection, global_id: str) -> Path:
    """Named `Parent/Subfolder` directory, or the UUID directory if that is what exists."""
    legacy = notex_dir / global_id
    assigned = assign_directory_names(load_directory_name_rows(conn))
    segments = assigned.get(global_id)
    if not segments:
        return legacy
    named = notex_dir / segments["parent_segment"] / segments["subfolder_segment"]
    if named.is_dir():
        return named
    if legacy.is_dir():
        return legacy
    return named
