"""OptimalX Desktop 1.0 Electron & API Bridge."""

from contextlib import contextmanager
import difflib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

import requests
import websockets

from eidos.integrations.notex_paths import resolve_notex_root
from eidos.perception.window_info import WindowDetector

logger = logging.getLogger(__name__)


class OptimalXBridge:
    """
    Bridge for interacting with OptimalX Desktop 1.0 & Knowledge Ecosystem via:
    1. Local HTTP Sync & LLM API (port 7373)
    2. Direct SQLite WAL database read-only reader (~/OptimalX/data.db)
    3. Chrome DevTools Protocol (CDP) WebSocket (port 9222)
    4. X11 Window Activation & Navigation
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        db_path: Optional[str] = None,
        notex_dir: Optional[str] = None,
        cdp_port: int = 9222,
        window_detector: Optional[WindowDetector] = None,
    ):
        self.config_path = Path(
            os.path.expanduser(config_path or "~/.config/optimalx-desktop/config.json")
        )
        self.db_path = Path(
            os.path.expanduser(db_path or "~/OptimalX/data.db")
        )
        self.notex_dir = Path(
            os.path.expanduser(notex_dir or "~/OptimalX/notex")
        )
        self.cdp_port = cdp_port
        self.windows = window_detector or WindowDetector()
        self._cached_config: Optional[Dict[str, Any]] = None

    def load_app_config(self) -> Dict[str, Any]:
        """Load OptimalX Desktop internal config (contains syncToken and port)."""
        if self._cached_config is not None:
            return self._cached_config

        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._cached_config = json.load(f)
                return self._cached_config
        except Exception as e:
            logger.warning("Failed to load OptimalX config from %s: %s", self.config_path, e)
            return {}

    @property
    def sync_port(self) -> int:
        return self.load_app_config().get("syncPort", 7373)

    @property
    def sync_token(self) -> str:
        return self.load_app_config().get("syncToken", "")

    def is_running(self) -> bool:
        """Check if OptimalX Desktop is running."""
        # 1. Check active or open windows via xprop/xdotool
        try:
            res = subprocess.run(
                ["xdotool", "search", "--class", "optimalx-desktop"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return True
        except Exception:
            pass

        # 2. Fallback: Check sync port
        try:
            resp = requests.get(f"http://127.0.0.1:{self.sync_port}/api/v1/sync/status", timeout=1.0)
            return resp.status_code in (200, 401, 403)
        except Exception:
            return False

    def get_window_id(self) -> Optional[int]:
        """Find the X11 window ID for OptimalX Desktop."""
        try:
            res = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", "optimalx-desktop"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                ids = res.stdout.strip().split()
                if ids:
                    return int(ids[0])
        except Exception as e:
            logger.debug("Failed to find OptimalX window ID: %s", e)
        return None

    def focus_window(self) -> bool:
        """Bring OptimalX Desktop window to the foreground."""
        wid = self.get_window_id()
        if wid:
            return self.windows.activate_window(wid)
        return False

    def get_sync_status(self) -> Dict[str, Any]:
        """Query OptimalX sync server for database statistics and counts."""
        token = self.sync_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"http://127.0.0.1:{self.sync_port}/api/v1/sync/status"

        try:
            resp = requests.get(url, headers=headers, timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("OptimalX get_sync_status error: %s", e)
            return {"error": str(e), "running": False}

    def get_llm_status(self) -> Dict[str, Any]:
        """Query OptimalX LLM server status."""
        token = self.sync_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"http://127.0.0.1:{self.sync_port}/api/v1/llm/status"

        try:
            resp = requests.get(url, headers=headers, timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("OptimalX get_llm_status error: %s", e)
            return {"error": str(e), "running": False}

    # =========================================================================
    # OptimalX Note & Knowledge Base Engine (Phase 11.1)
    # =========================================================================

    @contextmanager
    def _get_readonly_db(self):
        """Open a read-only WAL SQLite connection to OptimalX database."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"OptimalX database not found at {self.db_path}")
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _extract_snippet(content: str, query: str = "", context_chars: int = 150) -> str:
        """Extract a readable snippet centered around matching query keywords."""
        if not content:
            return ""
        stripped = content.strip()
        if not query or query == "*":
            if len(stripped) <= context_chars * 2:
                return stripped
            return stripped[: context_chars * 2] + "..."

        query_terms = [t for t in query.split() if len(t) > 1]
        best_idx = -1
        for term in (query_terms or [query]):
            idx = stripped.lower().find(term.lower())
            if idx != -1:
                best_idx = idx
                break

        if best_idx == -1:
            if len(stripped) <= context_chars * 2:
                return stripped
            return stripped[: context_chars * 2] + "..."

        start = max(0, best_idx - context_chars)
        end = min(len(stripped), best_idx + context_chars * 2)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(stripped) else ""
        return f"{prefix}{stripped[start:end].strip()}{suffix}"

    def search_notes(
        self,
        query: str,
        limit: int = 5,
        parent_folder: Optional[str] = None,
        parent_filter: Optional[str] = None,
        subfolder: Optional[str] = None,
        prefer_offline: bool = False,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """
        Search personal notes across OptimalX.
        Tier 1 (Online): Queries http://127.0.0.1:{sync_port}/api/v1/llm/search for semantic vector matches.
        Tier 2 (Offline / Fallback): Queries local SQLite WAL database directly.
        Enforces strict privacy filtering: ignores ai_blind=1 and deleted_at is not null.
        """
        effective_parent = parent_folder or parent_filter
        if not prefer_offline:
            online_results = self._search_notes_online(
                query=query,
                limit=limit,
                parent_folder=effective_parent,
                subfolder=subfolder,
            )
            if online_results:
                return online_results

        # Fallback to direct SQLite read-only search
        return self._search_notes_db(
            query=query,
            limit=limit,
            parent_folder=effective_parent,
            subfolder=subfolder,
        )

    def _search_notes_online(
        self,
        query: str,
        limit: int = 5,
        parent_folder: Optional[str] = None,
        subfolder: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Attempt semantic vector search via OptimalX Desktop Express server (port 7373)."""
        token = self.sync_token
        url = f"http://127.0.0.1:{self.sync_port}/api/v1/llm/search"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"query": query, "limit": limit * 2}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=2.5)
            if resp.status_code != 200:
                logger.debug("OptimalX online search returned status %s", resp.status_code)
                return []

            data = resp.json()
            chunks = data.get("chunks", [])
            if not chunks:
                return []

            results = []
            for chunk in chunks:
                loc = chunk.get("location") or ""
                if parent_folder and not loc.lower().startswith(parent_folder.lower()):
                    continue
                if subfolder and subfolder.lower() not in loc.lower():
                    continue

                results.append({
                    "location": loc,
                    "text": chunk.get("text", ""),
                    "score": chunk.get("score"),
                    "chunk_type": chunk.get("chunkType", "note"),
                    "start_line": chunk.get("startLine"),
                    "end_line": chunk.get("endLine"),
                    "subfolder_id": chunk.get("subfolderId"),
                    "note_id": chunk.get("objectId") if chunk.get("objectType") == "note" else None,
                    "source": "http_semantic",
                })
                if len(results) >= limit:
                    break

            return results
        except Exception as e:
            logger.debug("OptimalX online search not reachable or failed: %s", e)
            return []

    def _search_notes_db(
        self,
        query: str,
        limit: int = 5,
        parent_folder: Optional[str] = None,
        subfolder: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Direct read-only SQLite search across notes, subfolders, and parent folders."""
        if not self.db_path.exists():
            logger.debug("OptimalX DB does not exist at %s", self.db_path)
            return []

        try:
            with self._get_readonly_db() as conn:
                cur = conn.cursor()
                query_str = query.strip() if query else ""

                candidate_queries: List[Tuple[str, float]] = []
                if query_str and query_str != "*":
                    candidate_queries.append((query_str, 1.0))

                    # Check prefix pattern like 'item 114', 'note #42', 'idea 7'
                    m = re.match(
                        r"^(?:(?:item|note|entry|idea|line|number|no)\b\.?|#)\s*#?\s*([0-9a-zA-Z_-]+)",
                        query_str,
                        re.IGNORECASE,
                    )
                    if m:
                        id_term = m.group(1)
                        if id_term.lower() != query_str.lower():
                            candidate_queries.append((id_term, 0.85))

                    # Multi-word query fallback with stopwords removed
                    stopwords = {"item", "note", "the", "a", "an", "of", "in", "to", "for", "and", "or", "on", "at", "by", "is"}
                    words = [w for w in re.split(r"\s+", query_str) if len(w) > 1 and w.lower() not in stopwords]
                    if len(words) > 1:
                        for w in words:
                            if w.lower() != query_str.lower() and not any(w.lower() == cq[0].lower() for cq in candidate_queries):
                                candidate_queries.append((w, 0.7))
                else:
                    candidate_queries.append(("*", 0.5))

                seen_note_ids: Set[int] = set()
                scored_results: List[Dict[str, Any]] = []

                for term, term_weight in candidate_queries:
                    if len(scored_results) >= limit:
                        break

                    sql = """
                        SELECT
                            p.id AS parent_folder_id,
                            p.name AS parent_name,
                            s.id AS subfolder_id,
                            s.name AS subfolder_name,
                            s.notex_enabled,
                            s.project_summary,
                            n.id AS note_id,
                            n.content,
                            n.summary,
                            n.updated_at,
                            n.ai_locked,
                            n.global_id,
                            n.content_hash
                        FROM notes n
                        JOIN subfolders s ON n.subfolder_id = s.id
                        JOIN parent_folders p ON s.parent_folder_id = p.id
                        WHERE n.deleted_at IS NULL
                          AND n.ai_blind = 0
                          AND s.deleted_at IS NULL
                          AND p.deleted_at IS NULL
                    """
                    params: List[Any] = []

                    if parent_folder:
                        sql += " AND (p.name LIKE ? OR p.id = ?)"
                        params.extend([f"%{parent_folder}%", int(parent_folder) if str(parent_folder).isdigit() else -1])

                    if subfolder:
                        sql += " AND (s.name LIKE ? OR s.id = ?)"
                        params.extend([f"%{subfolder}%", int(subfolder) if str(subfolder).isdigit() else -1])

                    if term and term != "*":
                        like_q = f"%{term}%"
                        sql += """
                            AND (
                                s.name LIKE ?
                                OR p.name LIKE ?
                                OR n.content LIKE ?
                                OR n.summary LIKE ?
                                OR s.project_summary LIKE ?
                            )
                        """
                        params.extend([like_q, like_q, like_q, like_q, like_q])

                    sql += " ORDER BY n.updated_at DESC LIMIT ?"
                    params.append(limit * 3)

                    cur.execute(sql, params)
                    rows = cur.fetchall()

                    for row in rows:
                        nid = row["note_id"]
                        if nid in seen_note_ids:
                            continue
                        seen_note_ids.add(nid)

                        p_name = row["parent_name"]
                        s_name = row["subfolder_name"]
                        content = row["content"] or ""
                        summary = row["summary"] or ""
                        location = f"{p_name} > {s_name}"

                        relevance_score = 0.5 * term_weight
                        if term and term != "*":
                            t_lower = term.lower()
                            if t_lower == s_name.lower():
                                relevance_score = 1.0 * term_weight
                            elif t_lower in s_name.lower():
                                relevance_score = 0.9 * term_weight
                            elif summary and t_lower in summary.lower():
                                relevance_score = 0.8 * term_weight
                            elif t_lower in content.lower():
                                relevance_score = 0.7 * term_weight

                        snippet = self._extract_snippet(content, term if term != "*" else query_str)
                        scored_results.append({
                            "location": location,
                            "parent_name": p_name,
                            "parent_folder_id": row["parent_folder_id"],
                            "subfolder_name": s_name,
                            "subfolder_id": row["subfolder_id"],
                            "note_id": row["note_id"],
                            "text": snippet,
                            "summary": summary,
                            "word_count": len(content.split()),
                            "char_count": len(content),
                            "score": round(relevance_score, 2),
                            "chunk_type": "note",
                            "source": "db_sqlite",
                            "updated_at": row["updated_at"],
                            "ai_locked": bool(row["ai_locked"]),
                            "notex_enabled": bool(row["notex_enabled"]),
                        })

                scored_results.sort(key=lambda x: (x["score"], x["updated_at"]), reverse=True)
                return scored_results[:limit]
        except Exception as e:
            logger.error("OptimalX direct DB search failed: %s", e)
            return []

    def read_note(
        self,
        subfolder_id: Optional[int] = None,
        note_id: Optional[int] = None,
        title: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Read the full content and metadata of a specific OptimalX note.
        Strictly enforces ai_blind privacy: returns error dict if ai_blind=1.
        Returns None if note not found.
        """
        if not self.db_path.exists():
            logger.debug("OptimalX DB does not exist at %s", self.db_path)
            return None

        if subfolder_id is None and note_id is None and not title:
            return None

        try:
            with self._get_readonly_db() as conn:
                cur = conn.cursor()
                sql = """
                    SELECT
                        p.id AS parent_folder_id,
                        p.name AS parent_name,
                        s.id AS subfolder_id,
                        s.name AS subfolder_name,
                        s.notex_enabled,
                        s.project_summary,
                        n.id AS note_id,
                        n.content,
                        n.summary,
                        n.created_at,
                        n.updated_at,
                        n.ai_locked,
                        n.ai_blind,
                        n.global_id,
                        n.content_hash
                    FROM notes n
                    JOIN subfolders s ON n.subfolder_id = s.id
                    JOIN parent_folders p ON s.parent_folder_id = p.id
                    WHERE n.deleted_at IS NULL
                      AND s.deleted_at IS NULL
                      AND p.deleted_at IS NULL
                """
                params: List[Any] = []

                if subfolder_id is not None:
                    sql += " AND s.id = ?"
                    params.append(subfolder_id)
                elif note_id is not None:
                    sql += " AND n.id = ?"
                    params.append(note_id)
                elif title:
                    sql += " AND (s.name = ? OR s.name LIKE ?)"
                    params.extend([title, f"%{title}%"])

                sql += " LIMIT 1"
                cur.execute(sql, params)
                row = cur.fetchone()
                if not row:
                    return None

                # Privacy check: strictly deny ai_blind notes
                if row["ai_blind"] == 1:
                    logger.warning("Access denied: Note '%s' is marked ai_blind=1", row["subfolder_name"])
                    return {
                        "error": "Note is marked private (ai_blind=1) and cannot be accessed",
                        "ai_blind": True,
                        "subfolder_id": row["subfolder_id"],
                        "title": row["subfolder_name"],
                    }

                content = row["content"] or ""
                result = {
                    "subfolder_id": row["subfolder_id"],
                    "note_id": row["note_id"],
                    "parent_folder_id": row["parent_folder_id"],
                    "parent_name": row["parent_name"],
                    "location": f"{row['parent_name']} > {row['subfolder_name']}",
                    "title": row["subfolder_name"],
                    "content": content,
                    "summary": row["summary"] or "",
                    "word_count": len(content.split()),
                    "char_count": len(content),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "ai_locked": bool(row["ai_locked"]),
                    "ai_blind": False,
                    "global_id": row["global_id"],
                    "content_hash": row["content_hash"],
                    "notex_enabled": bool(row["notex_enabled"]),
                    "has_pending_review": False,
                }
                try:
                    cur.execute(
                        """
                        SELECT 1 FROM pending_change_items i
                        JOIN pending_change_sets s ON s.id = i.change_set_id
                        WHERE s.scope_type = 'subfolder' AND s.scope_id = ? AND s.status = 'open'
                          AND i.source_type = 'note' AND i.source_id = ? AND i.status = 'pending'
                        LIMIT 1
                        """,
                        (row["subfolder_id"], row["subfolder_id"]),
                    )
                    if cur.fetchone() is not None:
                        result["has_pending_review"] = True
                except sqlite3.OperationalError:
                    pass

                return result
        except Exception as e:
            logger.error("Failed to read OptimalX note: %s", e)
            return {"error": str(e)}

    def list_folders(
        self,
        parent_folder_id: Optional[int] = None,
        include_subfolders: bool = True,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List hierarchical parent folders and their contained subfolders in OptimalX.
        If parent_folder_id is provided, returns only that specific parent folder and its subfolders.
        If query is provided, filters parent folders or subfolders matching the search term.
        Respects privacy and soft-delete: excludes deleted parent folders and deleted subfolders.
        """
        if not self.db_path.exists():
            logger.debug("OptimalX DB does not exist at %s", self.db_path)
            return []

        clean_query = query.strip() if (query and str(query).strip()) else None

        try:
            with self._get_readonly_db() as conn:
                cur = conn.cursor()
                if parent_folder_id is not None:
                    p_sql = "SELECT id, name, sort_order, created_at, updated_at, global_id FROM parent_folders WHERE id = ? AND deleted_at IS NULL"
                    params: List[Any] = [parent_folder_id]
                    if clean_query:
                        p_sql += " AND name LIKE ?"
                        params.append(f"%{clean_query}%")
                    cur.execute(p_sql, params)
                elif clean_query:
                    p_sql = """
                        SELECT DISTINCT p.id, p.name, p.sort_order, p.created_at, p.updated_at, p.global_id
                        FROM parent_folders p
                        LEFT JOIN subfolders s ON s.parent_folder_id = p.id AND s.deleted_at IS NULL
                        WHERE p.deleted_at IS NULL
                          AND (p.name LIKE ? OR s.name LIKE ?)
                        ORDER BY p.sort_order ASC, p.name ASC
                    """
                    pat = f"%{clean_query}%"
                    cur.execute(p_sql, (pat, pat))
                else:
                    p_sql = "SELECT id, name, sort_order, created_at, updated_at, global_id FROM parent_folders WHERE deleted_at IS NULL ORDER BY sort_order ASC, name ASC"
                    cur.execute(p_sql)

                parent_rows = cur.fetchall()
                if not parent_rows:
                    return []

                results = []
                for p in parent_rows:
                    pid = p["id"]
                    folder_info = {
                        "id": pid,
                        "name": p["name"],
                        "sort_order": p["sort_order"],
                        "global_id": p["global_id"],
                        "updated_at": p["updated_at"],
                    }

                    if include_subfolders:
                        s_sql = """
                            SELECT
                                s.id AS subfolder_id,
                                s.name AS subfolder_name,
                                s.sort_order,
                                s.notex_enabled,
                                s.target_platform,
                                s.project_summary,
                                s.updated_at,
                                s.global_id,
                                n.id AS note_id,
                                n.summary AS note_summary,
                                n.ai_locked,
                                n.ai_blind,
                                LENGTH(n.content) AS content_length
                            FROM subfolders s
                            LEFT JOIN notes n ON n.subfolder_id = s.id AND n.deleted_at IS NULL
                            WHERE s.parent_folder_id = ? AND s.deleted_at IS NULL
                        """
                        s_params: List[Any] = [pid]
                        # If filtering by query and parent name doesn't match query, only return matching subfolders
                        if clean_query and not (p["name"] and clean_query.lower() in p["name"].lower()):
                            s_sql += " AND s.name LIKE ?"
                            s_params.append(f"%{clean_query}%")

                        s_sql += " ORDER BY s.sort_order ASC, s.name ASC"
                        cur.execute(s_sql, s_params)
                        subfolders = []
                        for s in cur.fetchall():
                            is_blind = bool(s["ai_blind"])
                            raw_sum = s["note_summary"] if not is_blind else None
                            if raw_sum and len(raw_sum) > 120:
                                raw_sum = raw_sum[:120] + "..."
                            subfolders.append({
                                "id": s["subfolder_id"],
                                "name": s["subfolder_name"],
                                "sort_order": s["sort_order"],
                                "notex_enabled": bool(s["notex_enabled"]),
                                "target_platform": s["target_platform"],
                                "note_id": s["note_id"],
                                "summary": raw_sum,
                                "ai_locked": bool(s["ai_locked"]),
                                "is_private": is_blind,
                                "content_length": s["content_length"] if not is_blind else 0,
                                "updated_at": s["updated_at"],
                                "global_id": s["global_id"],
                            })
                        folder_info["subfolder_count"] = len(subfolders)
                        folder_info["subfolders"] = subfolders
                    else:
                        c_sql = "SELECT COUNT(*) FROM subfolders WHERE parent_folder_id = ? AND deleted_at IS NULL"
                        cur.execute(c_sql, (pid,))
                        folder_info["subfolder_count"] = cur.fetchone()[0]

                    results.append(folder_info)

                return results
        except Exception as e:
            logger.error("Failed to list OptimalX folders: %s", e)
            return []

    def list_notex_docs(self, subfolder_id: int) -> Dict[str, Any]:
        """
        List markdown documents in a NoteX project.

        The corpus lives at ``notex/<Parent>/<Subfolder>/``. A project that has
        not been moved yet is still read from ``notex/<global_id>/``.
        """
        if not self.db_path.exists():
            return {"error": f"OptimalX database not found at {self.db_path}"}

        try:
            with self._get_readonly_db() as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT id, name, global_id, notex_enabled FROM subfolders WHERE id = ? AND deleted_at IS NULL",
                    (subfolder_id,)
                ).fetchone()

                if not row:
                    return {"error": f"Subfolder {subfolder_id} not found or deleted"}

                global_id = row["global_id"]
                name = row["name"]
                notex_enabled = bool(row["notex_enabled"])
                root = resolve_notex_root(self.notex_dir, conn, global_id)
            if not root.exists() or not root.is_dir():
                return {
                    "subfolder_id": subfolder_id,
                    "global_id": global_id,
                    "project_name": name,
                    "notex_enabled": notex_enabled,
                    "exists_on_disk": False,
                    "docs": [],
                    "folders": [],
                    "error": f"NoteX directory not found on disk at {root}",
                }

            manifest_path = root / ".notex" / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    return {
                        "subfolder_id": subfolder_id,
                        "global_id": global_id,
                        "project_name": name,
                        "notex_enabled": notex_enabled,
                        "root_path": str(root),
                        "exists_on_disk": True,
                        "docs": manifest.get("docs", []),
                        "folders": manifest.get("folders", []),
                        "active_doc_path": manifest.get("activeDocPath"),
                    }
                except Exception as e:
                    logger.warning("Failed to parse NoteX manifest for %s: %s", global_id, e)

            # Fallback scan of directory tree
            docs = []
            folders = set()
            for path in root.rglob("*.md"):
                rel = path.relative_to(root).as_posix()
                if rel.startswith(".notex/"):
                    continue
                if "/" in rel:
                    folders.add(rel.split("/")[0])
                docs.append({
                    "path": rel,
                    "title": path.stem,
                    "size_bytes": path.stat().st_size,
                })

            docs.sort(key=lambda d: d["path"])
            return {
                "subfolder_id": subfolder_id,
                "global_id": global_id,
                "project_name": name,
                "notex_enabled": notex_enabled,
                "root_path": str(root),
                "exists_on_disk": True,
                "docs": docs,
                "folders": [{"path": f, "title": f} for f in sorted(folders)],
                "active_doc_path": docs[0]["path"] if docs else None,
            }
        except Exception as e:
            logger.error("Failed to list NoteX docs for subfolder %s: %s", subfolder_id, e)
            return {"error": str(e)}

    def read_notex_doc(
        self,
        subfolder_id: int,
        doc_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Read a long-form markdown document from an OptimalX NoteX corpus project.
        Enforces strict path traversal sandboxing and line range slicing.
        """
        if not self.db_path.exists():
            return {"error": f"OptimalX database not found at {self.db_path}"}

        try:
            with self._get_readonly_db() as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT id, name, global_id, notex_enabled FROM subfolders WHERE id = ? AND deleted_at IS NULL",
                    (subfolder_id,)
                ).fetchone()

                if not row:
                    return {"error": f"Subfolder {subfolder_id} not found or deleted"}

                global_id = row["global_id"]
                project_name = row["name"]
                root = resolve_notex_root(self.notex_dir, conn, global_id).resolve()
            if not root.exists() or not root.is_dir():
                return {"error": f"NoteX directory not found on disk at {root}"}

            # Security sanitization of doc_path
            norm_doc = os.path.normpath(str(doc_path).replace("\\", "/")).lstrip("/")
            if norm_doc.startswith(".notex") or norm_doc.startswith(".."):
                return {"error": "Invalid NoteX document path"}

            file_path = (root / norm_doc).resolve()
            # Enforce that file_path is strictly within root
            try:
                file_path.relative_to(root)
            except ValueError:
                return {"error": "Path traversal attempt detected"}

            if not file_path.exists() or not file_path.is_file():
                return {"error": f"Document '{norm_doc}' not found in project '{project_name}'"}

            if file_path.suffix.lower() != ".md":
                return {"error": "Only markdown (.md) documents can be read from NoteX"}

            with open(file_path, "r", encoding="utf-8") as f:
                full_content = f.read()

            lines = full_content.splitlines(keepends=True)
            total_lines = len(lines)

            if start_line is not None or end_line is not None:
                s_idx = max(1, start_line) - 1 if start_line is not None else 0
                e_idx = min(total_lines, end_line) if end_line is not None else total_lines
                if s_idx > e_idx:
                    sliced_content = ""
                else:
                    sliced_content = "".join(lines[s_idx:e_idx])
                return {
                    "subfolder_id": subfolder_id,
                    "global_id": global_id,
                    "project_name": project_name,
                    "doc_path": norm_doc,
                    "content": sliced_content,
                    "start_line": s_idx + 1,
                    "end_line": e_idx,
                    "total_lines": total_lines,
                    "line_count": max(0, e_idx - s_idx),
                    "word_count": len(sliced_content.split()),
                    "char_count": len(sliced_content),
                }

            return {
                "subfolder_id": subfolder_id,
                "global_id": global_id,
                "project_name": project_name,
                "doc_path": norm_doc,
                "content": full_content,
                "start_line": 1,
                "end_line": total_lines,
                "total_lines": total_lines,
                "line_count": total_lines,
                "word_count": len(full_content.split()),
                "char_count": len(full_content),
            }
        except Exception as e:
            logger.error("Failed to read NoteX doc %s for subfolder %s: %s", doc_path, subfolder_id, e)
            return {"error": str(e)}

    # =========================================================================
    # Safe Mutation & Diff Review Engine (Phase 11.3)
    # =========================================================================

    @contextmanager
    def _get_writable_db(self):
        """Open a read-write WAL SQLite connection to OptimalX database with transaction safety."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"OptimalX database not found at {self.db_path}")
        conn = sqlite3.connect(str(self.db_path.resolve()), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @property
    def origin_device_id(self) -> str:
        """Device ID for synchronizing across OptimalX desktop/mobile ecosystem."""
        device_id = self.load_app_config().get("deviceId")
        return device_id or "desktop_eidosbot"

    @staticmethod
    def _compute_unified_diff(a: str, b: str, fromfile: str = "current", tofile: str = "proposed") -> str:
        """Generate unified diff text matching standard patch format."""
        a_lines = a.splitlines(keepends=True)
        b_lines = b.splitlines(keepends=True)
        if a_lines and not a_lines[-1].endswith("\n"):
            a_lines[-1] += "\n"
        if b_lines and not b_lines[-1].endswith("\n"):
            b_lines[-1] += "\n"
        return "".join(difflib.unified_diff(a_lines, b_lines, fromfile=fromfile, tofile=tofile))

    def _get_latest_checkpoint(self, cur: sqlite3.Cursor, source_type: str, source_id: int) -> Optional[sqlite3.Row]:
        cur.execute(
            """
            SELECT * FROM content_checkpoints
            WHERE source_type = ? AND source_id = ?
            ORDER BY sequence DESC, id DESC
            LIMIT 1
            """,
            (source_type, source_id),
        )
        return cur.fetchone()

    def _insert_checkpoint(
        self,
        cur: sqlite3.Cursor,
        source_type: str,
        source_id: int,
        sequence: int,
        content: str,
        author: str = "eidos",
        label: Optional[str] = None,
        conversation_id: Optional[int] = None,
    ) -> int:
        now = int(time.time() * 1000)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cur.execute(
            """
            INSERT INTO content_checkpoints (
                source_type, source_id, sequence, content_blob, content_hash,
                author, label, conversation_id, created_at, global_id, origin_device_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                source_id,
                sequence,
                content,
                content_hash,
                author,
                label,
                conversation_id,
                now,
                str(uuid.uuid4()),
                self.origin_device_id,
            ),
        )
        return cur.lastrowid

    def _prune_checkpoints(self, cur: sqlite3.Cursor, source_type: str, source_id: int, max_keep: int = 5) -> None:
        """Prune older checkpoints retaining sequence 0 baseline and the latest max_keep revisions."""
        cur.execute(
            "SELECT COUNT(*) FROM content_checkpoints WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
        count = cur.fetchone()[0]
        if count <= max_keep:
            return

        to_remove = count - max_keep
        cur.execute(
            """
            SELECT id FROM content_checkpoints
            WHERE source_type = ? AND source_id = ? AND sequence > 0
            ORDER BY sequence ASC, id ASC
            LIMIT ?
            """,
            (source_type, source_id, to_remove),
        )
        candidate_ids = [r[0] for r in cur.fetchall()]
        for cid in candidate_ids:
            cur.execute("DELETE FROM content_patches WHERE from_checkpoint_id = ? OR to_checkpoint_id = ?", (cid, cid))
            cur.execute("UPDATE pending_change_items SET base_checkpoint_id = NULL WHERE base_checkpoint_id = ?", (cid,))
            cur.execute("DELETE FROM content_checkpoints WHERE id = ?", (cid,))

    def _create_checkpoint(
        self,
        cur: sqlite3.Cursor,
        source_type: str,
        source_id: int,
        content: str,
        author: str = "eidos",
        label: Optional[str] = None,
        conversation_id: Optional[int] = None,
    ) -> int:
        prior = self._get_latest_checkpoint(cur, source_type, source_id)
        next_sequence = (prior["sequence"] + 1) if prior else 0
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cp_id = self._insert_checkpoint(
            cur,
            source_type=source_type,
            source_id=source_id,
            sequence=next_sequence,
            content=content,
            author=author,
            label=label,
            conversation_id=conversation_id,
        )

        if prior and prior["content_hash"] != new_hash:
            diff = self._compute_unified_diff(prior["content_blob"], content)
            if diff:
                now = int(time.time() * 1000)
                cur.execute(
                    """
                    INSERT INTO content_patches (
                        source_type, source_id, from_checkpoint_id, to_checkpoint_id,
                        unified_diff, created_at, global_id, origin_device_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_type,
                        source_id,
                        prior["id"],
                        cp_id,
                        diff,
                        now,
                        str(uuid.uuid4()),
                        self.origin_device_id,
                    ),
                )

        self._prune_checkpoints(cur, source_type, source_id, max_keep=5)
        return cp_id

    def _ensure_baseline_checkpoint(
        self,
        cur: sqlite3.Cursor,
        source_type: str,
        source_id: int,
        content: str,
        author: str = "system",
        label: str = "baseline",
    ) -> int:
        existing = self._get_latest_checkpoint(cur, source_type, source_id)
        if existing:
            return existing["id"]
        return self._insert_checkpoint(
            cur,
            source_type=source_type,
            source_id=source_id,
            sequence=0,
            content=content,
            author=author,
            label=label,
        )

    def _ensure_proposal_baseline(
        self,
        cur: sqlite3.Cursor,
        source_type: str,
        source_id: int,
        working_copy: str,
        conversation_id: Optional[int] = None,
    ) -> int:
        working_hash = hashlib.sha256(working_copy.encode("utf-8")).hexdigest()
        latest = self._get_latest_checkpoint(cur, source_type, source_id)
        if latest and latest["content_hash"] == working_hash:
            return latest["id"]
        return self._create_checkpoint(
            cur,
            source_type=source_type,
            source_id=source_id,
            content=working_copy,
            author="system",
            label="Proposal baseline",
            conversation_id=conversation_id,
        )

    def _apply_direct_note_write(
        self,
        cur: sqlite3.Cursor,
        subfolder_id: int,
        content: str,
        prior_content: str = "",
        author: str = "eidos",
        label: Optional[str] = None,
        conversation_id: Optional[int] = None,
    ) -> int:
        self._ensure_baseline_checkpoint(
            cur, source_type="note", source_id=subfolder_id, content=prior_content
        )
        now = int(time.time() * 1000)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cur.execute(
            """
            UPDATE notes
            SET content = ?, updated_at = ?, content_hash = ?, origin_device_id = ?
            WHERE subfolder_id = ?
            """,
            (content, now, content_hash, self.origin_device_id, subfolder_id),
        )
        cp_id = self._create_checkpoint(
            cur,
            source_type="note",
            source_id=subfolder_id,
            content=content,
            author=author,
            label=label or ("Note created" if not prior_content.strip() else "Note updated"),
            conversation_id=conversation_id,
        )
        return cp_id

    def _propose_note_diff(
        self,
        cur: sqlite3.Cursor,
        subfolder_id: int,
        disk_content: str,
        proposed_content: str,
        conversation_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        base_id = self._ensure_proposal_baseline(
            cur,
            source_type="note",
            source_id=subfolder_id,
            working_copy=disk_content,
            conversation_id=conversation_id,
        )

        cur.execute(
            """
            SELECT id FROM pending_change_sets
            WHERE scope_type = 'subfolder' AND scope_id = ? AND status = 'open'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (subfolder_id,),
        )
        row = cur.fetchone()
        if row:
            set_id = row[0]
        else:
            cur.execute(
                """
                INSERT INTO pending_change_sets (
                    scope_type, scope_id, conversation_id, status,
                    created_at, updated_at, global_id, origin_device_id
                ) VALUES ('subfolder', ?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    subfolder_id,
                    conversation_id,
                    now,
                    now,
                    str(uuid.uuid4()),
                    self.origin_device_id,
                ),
            )
            set_id = cur.lastrowid

        diff_str = self._compute_unified_diff(disk_content, proposed_content)
        proposed_hash = hashlib.sha256(proposed_content.encode("utf-8")).hexdigest()

        cur.execute(
            """
            INSERT INTO pending_change_items (
                change_set_id, source_type, source_id, base_checkpoint_id,
                proposed_content, proposed_hash, unified_diff, status,
                file_name, is_new_file, created_at, updated_at, global_id, origin_device_id
            ) VALUES (?, 'note', ?, ?, ?, ?, ?, 'pending', NULL, 0, ?, ?, ?, ?)
            """,
            (
                set_id,
                subfolder_id,
                base_id,
                proposed_content,
                proposed_hash,
                diff_str,
                now,
                now,
                str(uuid.uuid4()),
                self.origin_device_id,
            ),
        )
        item_id = cur.lastrowid

        cur.execute(
            "UPDATE pending_change_sets SET updated_at = ? WHERE id = ?",
            (now, set_id),
        )

        cur.execute(
            "SELECT COUNT(*) FROM pending_change_items WHERE change_set_id = ? AND status = 'pending'",
            (set_id,),
        )
        pending_count = cur.fetchone()[0]

        return {
            "set_id": set_id,
            "item_id": item_id,
            "pending_count": pending_count,
            "unified_diff": diff_str,
        }

    def write_note(
        self,
        content: str,
        subfolder_id: Optional[int] = None,
        subfolder_title: Optional[str] = None,
        parent_name: Optional[str] = None,
        parent_folder_id: Optional[int] = None,
        mode: str = "auto",
        allow_direct_append: bool = False,
        label: Optional[str] = None,
        conversation_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Write or propose changes to an OptimalX note with Diff Review safety.

        - Empty notes auto-apply directly to disk with an automatic checkpoint.
        - Existing notes with content queue a proposal into OptimalX Diff Review
          unless mode='direct_overwrite' or (mode='append' and allow_direct_append=True).
        - Notes with ai_locked=1 or ai_blind=1 are strictly protected from modification.

        Args:
            content: Markdown content to write, append, or propose.
            subfolder_id: Optional ID of the subfolder/note to write to.
            subfolder_title: Title of note/subfolder (used if subfolder_id not provided).
            parent_name: Name of parent folder (auto-created if not found).
            parent_folder_id: Optional parent folder ID.
            mode: Write mode ('auto', 'direct_overwrite', 'append', 'propose_edit').
            allow_direct_append: If True with mode='append', bypass Diff Review and write directly.
            label: Optional description for the content checkpoint.
            conversation_id: Optional conversation context ID.

        Returns:
            Dictionary with operation status, item ID, or error message.
        """
        if not isinstance(content, str):
            return {"error": "Content must be a string", "status": "failed", "kind": "failed"}

        if subfolder_id is None and not subfolder_title:
            return {
                "error": "Either subfolder_id or subfolder_title must be provided",
                "status": "failed",
                "kind": "failed",
            }

        try:
            with self._get_writable_db() as conn:
                cur = conn.cursor()

                # If subfolder_id not given, resolve or create parent folder and subfolder
                if subfolder_id is None:
                    p_id = parent_folder_id
                    p_name = parent_name
                    if p_id is not None:
                        cur.execute("SELECT id, name FROM parent_folders WHERE id = ? AND deleted_at IS NULL", (p_id,))
                        p_row = cur.fetchone()
                        if not p_row:
                            return {"error": f"Parent folder with id {p_id} not found", "status": "failed", "kind": "failed"}
                        p_name = p_row["name"]
                    elif p_name:
                        cur.execute("SELECT id, name FROM parent_folders WHERE LOWER(name) = LOWER(?) AND deleted_at IS NULL LIMIT 1", (p_name.strip(),))
                        p_row = cur.fetchone()
                        if p_row:
                            p_id = p_row["id"]
                            p_name = p_row["name"]
                        else:
                            now = int(time.time() * 1000)
                            cur.execute(
                                "INSERT INTO parent_folders (name, created_at, updated_at, sort_order, global_id, origin_device_id) VALUES (?, ?, ?, 0, ?, ?)",
                                (p_name.strip(), now, now, str(uuid.uuid4()), self.origin_device_id),
                            )
                            p_id = cur.lastrowid
                    else:
                        cur.execute("SELECT id, name FROM parent_folders WHERE deleted_at IS NULL ORDER BY sort_order ASC, id ASC LIMIT 1")
                        p_row = cur.fetchone()
                        if p_row:
                            p_id = p_row["id"]
                            p_name = p_row["name"]
                        else:
                            now = int(time.time() * 1000)
                            p_name = "General"
                            cur.execute(
                                "INSERT INTO parent_folders (name, created_at, updated_at, sort_order, global_id, origin_device_id) VALUES (?, ?, ?, 0, ?, ?)",
                                (p_name, now, now, str(uuid.uuid4()), self.origin_device_id),
                            )
                            p_id = cur.lastrowid

                    cur.execute(
                        "SELECT id, name FROM subfolders WHERE parent_folder_id = ? AND LOWER(name) = LOWER(?) AND deleted_at IS NULL LIMIT 1",
                        (p_id, subfolder_title.strip()),
                    )
                    s_row = cur.fetchone()
                    if s_row:
                        subfolder_id = s_row["id"]
                    else:
                        now = int(time.time() * 1000)
                        cur.execute(
                            "INSERT INTO subfolders (parent_folder_id, name, created_at, updated_at, sort_order, global_id, origin_device_id) VALUES (?, ?, ?, ?, 0, ?, ?)",
                            (p_id, subfolder_title.strip(), now, now, str(uuid.uuid4()), self.origin_device_id),
                        )
                        subfolder_id = cur.lastrowid
                        cur.execute(
                            "INSERT INTO notes (subfolder_id, content, created_at, updated_at, global_id, origin_device_id, content_hash) VALUES (?, '', ?, ?, ?, ?, ?)",
                            (subfolder_id, now, now, str(uuid.uuid4()), self.origin_device_id, hashlib.sha256(b"").hexdigest()),
                        )

                # Fetch resolved target
                cur.execute(
                    """
                    SELECT s.id AS subfolder_id, s.name AS subfolder_name,
                           p.id AS parent_id, p.name AS parent_name,
                           n.id AS note_id, n.content, n.ai_locked, n.ai_blind, n.content_hash
                    FROM subfolders s
                    JOIN parent_folders p ON p.id = s.parent_folder_id
                    LEFT JOIN notes n ON n.subfolder_id = s.id
                    WHERE s.id = ? AND s.deleted_at IS NULL AND p.deleted_at IS NULL
                    """,
                    (subfolder_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"error": f"Subfolder {subfolder_id} not found", "status": "failed", "kind": "failed"}

                subfolder_name = row["subfolder_name"]
                parent_name_val = row["parent_name"]

                if row["ai_locked"] == 1:
                    return {
                        "status": "locked",
                        "kind": "failed",
                        "subfolder_id": subfolder_id,
                        "subfolder_name": subfolder_name,
                        "parent_name": parent_name_val,
                        "error": f"Note '{subfolder_name}' is locked (ai_locked=1) and cannot be modified.",
                    }

                if row["ai_blind"] == 1:
                    return {
                        "status": "private",
                        "kind": "failed",
                        "subfolder_id": subfolder_id,
                        "subfolder_name": subfolder_name,
                        "parent_name": parent_name_val,
                        "error": f"Note '{subfolder_name}' is private (ai_blind=1) and cannot be accessed or modified.",
                    }

                if row["note_id"] is None:
                    now = int(time.time() * 1000)
                    cur.execute(
                        "INSERT INTO notes (subfolder_id, content, created_at, updated_at, global_id, origin_device_id, content_hash) VALUES (?, '', ?, ?, ?, ?, ?)",
                        (subfolder_id, now, now, str(uuid.uuid4()), self.origin_device_id, hashlib.sha256(b"").hexdigest()),
                    )
                    disk_content = ""
                else:
                    disk_content = row["content"] or ""

                if mode == "append":
                    if not disk_content.strip():
                        target_content = content
                    else:
                        target_content = f"{disk_content}\n\n{content}"
                else:
                    target_content = content

                # Check effective working copy in open pending change set
                cur.execute(
                    """
                    SELECT i.proposed_content
                    FROM pending_change_items i
                    JOIN pending_change_sets s ON s.id = i.change_set_id
                    WHERE s.scope_type = 'subfolder' AND s.scope_id = ? AND s.status = 'open'
                      AND i.source_type = 'note' AND i.source_id = ? AND i.status = 'pending'
                    ORDER BY i.id DESC LIMIT 1
                    """,
                    (subfolder_id, subfolder_id),
                )
                pending_row = cur.fetchone()
                has_open_pending = pending_row is not None
                effective_content = pending_row[0] if has_open_pending else disk_content

                if target_content == effective_content:
                    return {
                        "status": "no_change",
                        "kind": "no_change",
                        "subfolder_id": subfolder_id,
                        "subfolder_name": subfolder_name,
                        "parent_name": parent_name_val,
                        "message": "Proposed content matches working copy; no changes needed.",
                    }

                should_auto_apply = False
                if mode == "direct_overwrite":
                    should_auto_apply = True
                elif mode == "append" and allow_direct_append:
                    should_auto_apply = True
                elif mode == "auto":
                    if not disk_content.strip() and not has_open_pending:
                        should_auto_apply = True

                if should_auto_apply:
                    cp_id = self._apply_direct_note_write(
                        cur,
                        subfolder_id=subfolder_id,
                        content=target_content,
                        prior_content=disk_content,
                        label=label or ("Note created" if not disk_content.strip() else "Note updated"),
                        conversation_id=conversation_id,
                    )
                    # If direct overwrite superseded open pending items, resolve them
                    if has_open_pending and mode == "direct_overwrite":
                        now = int(time.time() * 1000)
                        cur.execute(
                            "UPDATE pending_change_items SET status = 'superseded', updated_at = ? WHERE source_type = 'note' AND source_id = ? AND status = 'pending'",
                            (now, subfolder_id),
                        )
                    return {
                        "status": "written",
                        "kind": "written",
                        "subfolder_id": subfolder_id,
                        "subfolder_name": subfolder_name,
                        "parent_name": parent_name_val,
                        "checkpoint_id": cp_id,
                        "content_length": len(target_content),
                        "word_count": len(target_content.split()),
                        "message": f"Successfully written note '{subfolder_name}' in folder '{parent_name_val}'.",
                    }
                else:
                    result = self._propose_note_diff(
                        cur,
                        subfolder_id=subfolder_id,
                        disk_content=effective_content,
                        proposed_content=target_content,
                        conversation_id=conversation_id,
                    )
                    return {
                        "status": "queued",
                        "kind": "queued",
                        "subfolder_id": subfolder_id,
                        "subfolder_name": subfolder_name,
                        "parent_name": parent_name_val,
                        "change_set_id": result["set_id"],
                        "item_id": result["item_id"],
                        "pending_count": result["pending_count"],
                        "unified_diff": result["unified_diff"],
                        "message": f"Proposed updates to '{subfolder_name}' queued in OptimalX Diff Review panel.",
                        "description": f"Diff Review required: Note '{subfolder_name}' already contains content. The proposed modification has been queued for human review in the OptimalX desktop app.",
                    }
        except Exception as e:
            logger.error("Failed to write note: %s", e)
            return {"error": str(e), "status": "failed", "kind": "failed"}

    def list_pending_reviews(self, subfolder_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List pending Diff Review items awaiting user review in OptimalX."""
        with self._get_readonly_db() as conn:
            cur = conn.cursor()
            query = """
                SELECT
                    i.id AS item_id,
                    i.change_set_id,
                    i.source_type,
                    i.source_id AS subfolder_id,
                    i.base_checkpoint_id,
                    i.proposed_content,
                    i.proposed_hash,
                    i.unified_diff,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    s.name AS subfolder_name,
                    p.name AS parent_name
                FROM pending_change_items i
                JOIN subfolders s ON s.id = i.source_id
                JOIN parent_folders p ON p.id = s.parent_folder_id
                WHERE i.status = 'pending' AND i.source_type = 'note'
            """
            params: List[Any] = []
            if subfolder_id is not None:
                query += " AND i.source_id = ?"
                params.append(subfolder_id)
            query += " ORDER BY i.created_at DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            results = []
            for r in rows:
                content = r["proposed_content"] or ""
                results.append({
                    "item_id": r["item_id"],
                    "change_set_id": r["change_set_id"],
                    "subfolder_id": r["subfolder_id"],
                    "subfolder_name": r["subfolder_name"],
                    "parent_name": r["parent_name"],
                    "base_checkpoint_id": r["base_checkpoint_id"],
                    "proposed_hash": r["proposed_hash"],
                    "unified_diff": r["unified_diff"],
                    "content_preview": (content[:200] + "...") if len(content) > 200 else content,
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return results

    def accept_pending_review(self, item_id: int) -> Dict[str, Any]:
        """Accept a pending Diff Review item and commit changes directly to notes table."""
        try:
            with self._get_writable_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM pending_change_items WHERE id = ?", (item_id,))
                item = cur.fetchone()
                if not item:
                    return {"status": "failed", "error": f"Item {item_id} not found"}
                if item["status"] != "pending":
                    return {"status": "failed", "error": f"Item {item_id} not pending (status={item['status']})"}

                subfolder_id = item["source_id"]
                cur.execute("SELECT * FROM notes WHERE subfolder_id = ?", (subfolder_id,))
                note = cur.fetchone()
                if not note:
                    return {"status": "failed", "error": f"Note not found for subfolder {subfolder_id}"}

                current_hash = hashlib.sha256((note["content"] or "").encode("utf-8")).hexdigest()
                if item["base_checkpoint_id"] is not None:
                    cur.execute("SELECT content_hash FROM content_checkpoints WHERE id = ?", (item["base_checkpoint_id"],))
                    base_cp = cur.fetchone()
                    if base_cp and base_cp["content_hash"] != current_hash:
                        return {
                            "status": "concurrent_change",
                            "error": "Concurrent modification detected. Note was modified after proposal was generated.",
                            "expected_hash": base_cp["content_hash"],
                            "actual_hash": current_hash,
                        }

                cp_id = self._apply_direct_note_write(
                    cur,
                    subfolder_id=subfolder_id,
                    content=item["proposed_content"],
                    prior_content=note["content"] or "",
                    label="Accepted proposal",
                )

                now = int(time.time() * 1000)
                cur.execute("UPDATE pending_change_items SET status = 'accepted', updated_at = ? WHERE id = ?", (now, item_id))

                set_id = item["change_set_id"]
                cur.execute("SELECT status FROM pending_change_items WHERE change_set_id = ?", (set_id,))
                all_statuses = [r["status"] for r in cur.fetchall()]
                if all(s != "pending" for s in all_statuses):
                    new_set_status = "partial" if any(s in ("rejected", "superseded") for s in all_statuses) else "accepted"
                    cur.execute("UPDATE pending_change_sets SET status = ?, updated_at = ? WHERE id = ?", (new_set_status, now, set_id))

                return {
                    "status": "accepted",
                    "item_id": item_id,
                    "subfolder_id": subfolder_id,
                    "checkpoint_id": cp_id,
                }
        except Exception as e:
            logger.error("Failed to accept pending review %s: %s", item_id, e)
            return {"status": "failed", "error": str(e)}

    def reject_pending_review(self, item_id: int) -> Dict[str, Any]:
        """Reject a pending Diff Review item without applying changes."""
        try:
            with self._get_writable_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM pending_change_items WHERE id = ?", (item_id,))
                item = cur.fetchone()
                if not item:
                    return {"status": "failed", "error": f"Item {item_id} not found"}
                if item["status"] != "pending":
                    return {"status": "failed", "error": f"Item {item_id} not pending (status={item['status']})"}

                now = int(time.time() * 1000)
                cur.execute("UPDATE pending_change_items SET status = 'rejected', updated_at = ? WHERE id = ?", (now, item_id))

                set_id = item["change_set_id"]
                cur.execute("SELECT status FROM pending_change_items WHERE change_set_id = ?", (set_id,))
                all_statuses = [r["status"] for r in cur.fetchall()]
                if all(s != "pending" for s in all_statuses):
                    new_set_status = "partial" if any(s == "accepted" for s in all_statuses) else "rejected"
                    cur.execute("UPDATE pending_change_sets SET status = ?, updated_at = ? WHERE id = ?", (new_set_status, now, set_id))

                return {
                    "status": "rejected",
                    "item_id": item_id,
                    "subfolder_id": item["source_id"],
                }
        except Exception as e:
            logger.error("Failed to reject pending review %s: %s", item_id, e)
            return {"status": "failed", "error": str(e)}

    def get_note_checkpoints(self, subfolder_id: int) -> List[Dict[str, Any]]:
        """Retrieve all checkpoints for a given note ordered by sequence."""
        with self._get_readonly_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, sequence, content_blob, content_hash, author, label, created_at, global_id, origin_device_id
                FROM content_checkpoints
                WHERE source_type = 'note' AND source_id = ?
                ORDER BY sequence ASC, id ASC
                """,
                (subfolder_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "sequence": r["sequence"],
                    "content_hash": r["content_hash"],
                    "author": r["author"],
                    "label": r["label"],
                    "created_at": r["created_at"],
                    "global_id": r["global_id"],
                    "origin_device_id": r["origin_device_id"],
                    "char_count": len(r["content_blob"] or ""),
                }
                for r in rows
            ]

    def get_note_patches(self, subfolder_id: int) -> List[Dict[str, Any]]:
        """Retrieve all patch diffs for a given note."""
        with self._get_readonly_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, from_checkpoint_id, to_checkpoint_id, unified_diff, created_at, global_id
                FROM content_patches
                WHERE source_type = 'note' AND source_id = ?
                ORDER BY id ASC
                """,
                (subfolder_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "from_checkpoint_id": r["from_checkpoint_id"],
                    "to_checkpoint_id": r["to_checkpoint_id"],
                    "unified_diff": r["unified_diff"],
                    "created_at": r["created_at"],
                    "global_id": r["global_id"],
                }
                for r in rows
            ]

    # =========================================================================
    # Chrome DevTools Protocol (CDP) WebSocket Engine
    # =========================================================================

    def is_cdp_available(self) -> bool:
        """Check if CDP remote debugging port is reachable."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

    def get_cdp_targets(self) -> List[Dict[str, Any]]:
        """List active CDP page and webview targets."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json", timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("Failed to list CDP targets: %s", e)
        return []

    def get_main_page_ws_url(self) -> Optional[str]:
        """Return the WebSocket debugger URL for the main OptimalX renderer."""
        targets = self.get_cdp_targets()
        for t in targets:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl")
                if ws_url:
                    return ws_url
        return None

    def evaluate_js_sync(self, expression: str, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Evaluate JavaScript inside the OptimalX Electron renderer via CDP WebSocket.
        Returns execution result or error dictionary.
        """
        import asyncio

        return asyncio.run(self.evaluate_js(expression, timeout=timeout))

    async def evaluate_js(self, expression: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Async evaluation of JavaScript via CDP WebSocket."""
        ws_url = self.get_main_page_ws_url()
        if not ws_url:
            return {
                "success": False,
                "error": f"CDP port {self.cdp_port} not reachable. Start OptimalX with --remote-debugging-port={self.cdp_port}",
            }

        req_id = int(time.time() * 1000) % 1000000
        payload = {
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }

        try:
            async with websockets.connect(ws_url) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    msg = json.loads(raw_msg)
                    if msg.get("id") == req_id:
                        result = msg.get("result", {})
                        if "exceptionDetails" in result:
                            return {
                                "success": False,
                                "error": result["exceptionDetails"].get("text", "JS Exception"),
                            }
                        return {
                            "success": True,
                            "value": result.get("result", {}).get("value"),
                        }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def query_dom_element(self, selector: str) -> Dict[str, Any]:
        """
        Find an element by CSS selector in OptimalX and return bounding rect and text.
        """
        js = f"""
        (() => {{
            const el = document.querySelector("{selector}");
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {{
                tag: el.tagName,
                id: el.id,
                className: el.className,
                innerText: (el.innerText || "").slice(0, 100),
                rect: {{
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                }}
            }};
        }})()
        """
        return self.evaluate_js_sync(js)

    def click_dom_element(self, selector: str) -> Dict[str, Any]:
        """Trigger native .click() on element in OptimalX DOM."""
        js = f"""
        (() => {{
            const el = document.querySelector("{selector}");
            if (!el) return {{ clicked: false, error: "Element not found" }};
            el.click();
            return {{ clicked: true }};
        }})()
        """
        return self.evaluate_js_sync(js)
