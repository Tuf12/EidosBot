"""Vector index of completed chat exchanges for scoped conversation recall.

Embeddings live in a sqlite-vec table when the extension loads, and always in
an embedding BLOB so search can filter by job scope before ranking. sqlite-vec
applies its ``k`` limit before a SQL filter, which would hide an in-scope turn
that is not among the global nearest rows.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import struct
import threading
from typing import List, Optional, Sequence, Set

import numpy as np

from eidos.memory.embeddings import DEFAULT_EMBEDDING_DIM, OllamaEmbeddingClient
from eidos.memory.types import ConversationHit

logger = logging.getLogger(__name__)

DEFAULT_CONVERSATION_INDEX_DIR = Path.home() / ".eidos" / "memory" / "vector_store"
DEFAULT_CONVERSATION_INDEX_PATH = DEFAULT_CONVERSATION_INDEX_DIR / "conversation_index.db"


class ConversationIndex:
    """Scoped nearest-neighbor index over user/assistant exchanges."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        embedding_client: Optional[OllamaEmbeddingClient] = None,
        dimension: int = DEFAULT_EMBEDDING_DIM,
    ):
        self.db_path = Path(os.path.expanduser(str(db_path or DEFAULT_CONVERSATION_INDEX_PATH)))
        self.dimension = dimension
        self.embedding_client = embedding_client
        self._lock = threading.RLock()
        self._has_sqlite_vec = False
        self._ensure_storage()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_database()

    def _ensure_storage(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error("Failed to create conversation index directory %s: %s", self.db_path.parent, exc)

    def _init_database(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            try:
                import sqlite_vec

                self._conn.enable_load_extension(True)
                sqlite_vec.load(self._conn)
                self._conn.enable_load_extension(False)
                self._has_sqlite_vec = True
            except Exception as exc:
                self._has_sqlite_vec = False
                logger.info(
                    "sqlite-vec extension not loaded for conversation index (%s). "
                    "Ranking uses numpy cosine similarity.",
                    exc,
                )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    scope_bound INTEGER NOT NULL DEFAULT 0,
                    conversation_title TEXT NOT NULL DEFAULT '',
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    is_residue INTEGER NOT NULL DEFAULT 0,
                    embedding_blob BLOB
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conv_turns_scope
                ON conversation_turns(job_id, scope_bound, conversation_id);
                """
            )
            if self._has_sqlite_vec:
                try:
                    self._conn.execute(
                        f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS vec_conversation_turns USING vec0(
                            message_id TEXT PRIMARY KEY,
                            embedding FLOAT[{self.dimension}] distance_metric=cosine
                        );
                        """
                    )
                except Exception as exc:
                    logger.warning("Failed to initialize vec_conversation_turns: %s", exc)
                    self._has_sqlite_vec = False
            self._conn.commit()

    @staticmethod
    def content_hash(user_text: str, assistant_text: str, job_id: str) -> str:
        payload = f"{user_text}\n{assistant_text}\n{job_id}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _vector_to_blob(vector: Sequence[float]) -> bytes:
        return struct.pack(f"{len(vector)}f", *vector)

    def _embed(self, text: str) -> Optional[List[float]]:
        client = self.embedding_client
        if client is None:
            logger.warning("Conversation index has no embedding client.")
            return None
        try:
            vector = client.embed_text(text)
        except Exception as exc:
            logger.warning("Conversation index embed failed: %s", exc)
            return None
        if not vector or len(vector) != self.dimension:
            logger.warning(
                "Conversation index embed returned %s floats, expected %s.",
                len(vector) if vector else 0,
                self.dimension,
            )
            return None
        return vector

    def upsert(
        self,
        *,
        message_id: str,
        conversation_id: str,
        job_id: str = "",
        scope_bound: bool = False,
        conversation_title: str = "",
        user_text: str,
        assistant_text: str,
        created_at: int,
        is_residue: bool = False,
    ) -> bool:
        """Insert or replace one exchange. Skip the embed call when the hash is unchanged."""
        message_id = (message_id or "").strip()
        conversation_id = (conversation_id or "").strip()
        if not message_id or not conversation_id:
            return False
        job_id = (job_id or "").strip()
        user_text = user_text or ""
        assistant_text = assistant_text or ""
        digest = self.content_hash(user_text, assistant_text, job_id)

        with self._lock:
            existing = self._conn.execute(
                "SELECT content_hash FROM conversation_turns WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if existing is not None and existing["content_hash"] == digest:
                self._conn.execute(
                    """
                    UPDATE conversation_turns
                    SET conversation_id = ?, scope_bound = ?, conversation_title = ?,
                        created_at = ?, is_residue = ?
                    WHERE message_id = ?
                    """,
                    (
                        conversation_id,
                        1 if scope_bound else 0,
                        conversation_title or "",
                        int(created_at),
                        1 if is_residue else 0,
                        message_id,
                    ),
                )
                self._conn.commit()
                return True

        embed_text = f"User: {user_text}\nAssistant: {assistant_text}"
        vector = self._embed(embed_text)
        if vector is None:
            return False
        blob = self._vector_to_blob(vector)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO conversation_turns (
                    message_id, conversation_id, job_id, scope_bound, conversation_title,
                    user_text, assistant_text, created_at, content_hash, is_residue,
                    embedding_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    job_id = excluded.job_id,
                    scope_bound = excluded.scope_bound,
                    conversation_title = excluded.conversation_title,
                    user_text = excluded.user_text,
                    assistant_text = excluded.assistant_text,
                    created_at = excluded.created_at,
                    content_hash = excluded.content_hash,
                    is_residue = excluded.is_residue,
                    embedding_blob = excluded.embedding_blob
                """,
                (
                    message_id,
                    conversation_id,
                    job_id,
                    1 if scope_bound else 0,
                    conversation_title or "",
                    user_text,
                    assistant_text,
                    int(created_at),
                    digest,
                    1 if is_residue else 0,
                    blob,
                ),
            )
            if self._has_sqlite_vec:
                try:
                    self._conn.execute(
                        "DELETE FROM vec_conversation_turns WHERE message_id = ?",
                        (message_id,),
                    )
                    self._conn.execute(
                        "INSERT INTO vec_conversation_turns(message_id, embedding) VALUES (?, ?)",
                        (message_id, blob),
                    )
                except Exception as exc:
                    logger.warning("Failed to update vec_conversation_turns for %s: %s", message_id, exc)
            self._conn.commit()
        return True

    def known_message_ids(self) -> Set[str]:
        """Return every message id already stored in the index."""
        with self._lock:
            rows = self._conn.execute("SELECT message_id FROM conversation_turns").fetchall()
        return {row["message_id"] for row in rows}

    def delete_conversation(self, conversation_id: str) -> None:
        conversation_id = (conversation_id or "").strip()
        if not conversation_id:
            return
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_id FROM conversation_turns WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
            ids = [row["message_id"] for row in rows]
            self._conn.execute(
                "DELETE FROM conversation_turns WHERE conversation_id = ?",
                (conversation_id,),
            )
            self._delete_vectors(ids)
            self._conn.commit()

    def delete_messages(self, message_ids: Sequence[str]) -> None:
        ids = [mid.strip() for mid in message_ids if mid and mid.strip()]
        if not ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM conversation_turns WHERE message_id IN ({placeholders})",
                ids,
            )
            self._delete_vectors(ids)
            self._conn.commit()

    def _delete_vectors(self, message_ids: Sequence[str]) -> None:
        if not self._has_sqlite_vec or not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        try:
            self._conn.execute(
                f"DELETE FROM vec_conversation_turns WHERE message_id IN ({placeholders})",
                list(message_ids),
            )
        except Exception as exc:
            logger.warning("Failed to delete conversation vectors: %s", exc)

    def search(
        self,
        query: str,
        *,
        conversation_id: str,
        job_id: Optional[str],
        scope_bound: bool,
        top_k: int = 3,
        min_similarity: float = 0.20,
        exclude_message_ids: Optional[Set[str]] = None,
    ) -> List[ConversationHit]:
        """Return scoped hits at or above ``min_similarity``. An embed failure returns no hits."""
        cleaned = (query or "").strip()
        if not cleaned or top_k <= 0:
            return []
        vector = self._embed(cleaned)
        if vector is None:
            return []

        where, params = self._scope_filter(conversation_id, job_id, scope_bound)
        excluded = {mid for mid in (exclude_message_ids or set()) if mid}
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT message_id, conversation_id, conversation_title, job_id,
                       user_text, assistant_text, is_residue, embedding_blob
                FROM conversation_turns
                WHERE {where}
                """,
                params,
            ).fetchall()

        q_vec = np.array(vector, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm == 0:
            return []

        ranked: List[ConversationHit] = []
        for row in rows:
            message_id = row["message_id"]
            if message_id in excluded:
                continue
            blob = row["embedding_blob"]
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape[0] != q_vec.shape[0]:
                continue
            v_norm = float(np.linalg.norm(vec))
            if v_norm == 0:
                continue
            similarity = float(np.dot(q_vec, vec) / (q_norm * v_norm))
            similarity = max(-1.0, min(1.0, similarity))
            if similarity < min_similarity:
                continue
            ranked.append(
                ConversationHit(
                    message_id=message_id,
                    conversation_id=row["conversation_id"],
                    conversation_title=row["conversation_title"] or "",
                    job_id=row["job_id"] or "",
                    user_text=row["user_text"] or "",
                    assistant_text=row["assistant_text"] or "",
                    similarity=round(similarity, 4),
                    is_residue=bool(row["is_residue"]),
                )
            )
        ranked.sort(key=lambda hit: hit.similarity, reverse=True)
        return ranked[:top_k]

    @staticmethod
    def _scope_filter(
        conversation_id: str,
        job_id: Optional[str],
        scope_bound: bool,
    ) -> tuple[str, tuple]:
        if not scope_bound:
            return "conversation_id = ?", ((conversation_id or "").strip(),)
        job = (job_id or "").strip()
        if job:
            return "job_id = ?", (job,)
        return "scope_bound = 1 AND job_id = ''", ()

    def render_context(
        self,
        residue: str,
        hits: Sequence[ConversationHit],
        *,
        active_conversation_id: str = "",
    ) -> str:
        """Render the turn-start block. Empty residue and no hits produce an empty string."""
        residue_text = (residue or "").strip()
        hit_list = list(hits or [])
        if not residue_text and not hit_list:
            return ""

        lines = ["<conversation_context>"]
        if residue_text:
            lines.append("Residue:")
            for raw_line in residue_text.splitlines():
                text = raw_line.strip()
                if not text:
                    continue
                lines.append(text if text.startswith("- ") else f"- {text}")
        active_id = (active_conversation_id or "").strip()
        for hit in hit_list:
            lines.append(self._exchange_label(hit, active_id))
            lines.append(f"- User: {(hit.user_text or '').strip()}")
            lines.append(f"- Assistant: {(hit.assistant_text or '').strip()}")
        lines.append("</conversation_context>")
        return "\n".join(lines) + "\n"

    def render_recall(self, hit: ConversationHit) -> str:
        """Render one mid-thought chat hit."""
        title = (hit.conversation_title or "").strip() or "conversation"
        attrs = [f'conversation="{title}"']
        job_id = (hit.job_id or "").strip()
        if job_id:
            attrs.append(f'job_id="{job_id}"')
        lines = [
            f'<recalled_conversation {" ".join(attrs)}>',
            f"- User: {(hit.user_text or '').strip()}",
            f"- Assistant: {(hit.assistant_text or '').strip()}",
            "</recalled_conversation>",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _exchange_label(hit: ConversationHit, active_conversation_id: str) -> str:
        if active_conversation_id and hit.conversation_id == active_conversation_id:
            return "Earlier exchange (this conversation):"
        title = (hit.conversation_title or "").strip() or "conversation"
        job_id = (hit.job_id or "").strip()
        if job_id:
            return f'Earlier exchange ({job_id}, conversation "{title}"):'
        return f'Earlier exchange (conversation "{title}"):'
