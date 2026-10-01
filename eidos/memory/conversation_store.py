"""SQLite-backed conversation and message persistence store for EidosBot (~/.eidos/eidos.db)."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)

DEFAULT_CONVERSATIONS_DB_PATH = Path.home() / ".eidos" / "eidos.db"


def _generate_title_from_prompt(prompt: str, max_chars: int = 42) -> str:
    """Generate a clean, human-readable conversation title from the first prompt."""
    if not prompt:
        return "New Chat"

    # Strip code block fences or extract inner code content
    cleaned_full = re.sub(r"```[a-zA-Z0-9_\-\+]*\n?(.*?)```", r"\1", prompt, flags=re.DOTALL)

    # Take the first non-empty line
    lines = [line.strip() for line in cleaned_full.splitlines() if line.strip()]
    first_line = lines[0] if lines else prompt.strip()

    # Strip leading bullet/markdown markers like '- ', '* ', '# '
    cleaned = re.sub(r"^[\-*#>\s]+", "", first_line)
    # Strip backticks
    cleaned = cleaned.replace("`", "").strip()
    if not cleaned:
        cleaned = first_line

    # Truncate at word boundary if possible
    if len(cleaned) <= max_chars:
        title = cleaned
    else:
        truncated = cleaned[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > 15:
            title = truncated[:last_space] + "..."
        else:
            title = truncated + "..."

    # Capitalize first letter
    return title[:1].upper() + title[1:] if title else "New Chat"


class ConversationStore:
    """
    Manages persistent SQLite storage of conversations and message history.
    Stores conversations, forks, and messages in ~/.eidos/eidos.db.
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(os.path.expanduser(str(db_path or DEFAULT_CONVERSATIONS_DB_PATH)))
        self._lock = threading.Lock()
        self._last_ts: int = 0
        self._ensure_storage()
        self._conn = sqlite3.connect(
            str(self.db_path) if self.db_path != ":memory:" else ":memory:",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_database()

    def _get_timestamp_ms(self) -> int:
        """Return strictly monotonic millisecond timestamp to prevent clock collisions."""
        now_ms = int(time.time() * 1000)
        if now_ms <= self._last_ts:
            now_ms = self._last_ts + 1
        self._last_ts = now_ms
        return now_ms

    def _ensure_storage(self) -> None:
        """Create parent directory if necessary."""
        if self.db_path != ":memory:" and isinstance(self.db_path, Path):
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error("Failed to create conversation database directory %s: %s", self.db_path.parent, e)

    def _init_database(self) -> None:
        """Create relational conversation and message tables matching eidos.db schema."""
        with self._lock:
            # Enable foreign keys and WAL journal mode
            try:
                self._conn.execute("PRAGMA foreign_keys = ON;")
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode = WAL;")
            except Exception as e:
                logger.debug("Could not set pragmas on conversation DB: %s", e)

            # 1. Conversation Table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS Conversation (
                    id TEXT PRIMARY KEY,
                    userId TEXT NOT NULL,
                    title TEXT NOT NULL,
                    modelConfig TEXT NOT NULL,
                    createdAt INTEGER NOT NULL,
                    updatedAt INTEGER NOT NULL
                );
                """
            )

            # 2. Message Table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS Message (
                    id TEXT PRIMARY KEY,
                    conversationId TEXT NOT NULL,
                    parentId TEXT,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    isMainThread INTEGER NOT NULL DEFAULT 1,
                    createdAt INTEGER NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (conversationId) REFERENCES Conversation(id) ON DELETE CASCADE,
                    FOREIGN KEY (parentId) REFERENCES Message(id) ON DELETE SET NULL
                );
                """
            )

            # 3. ConversationFork Table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ConversationFork (
                    id TEXT PRIMARY KEY,
                    conversationId TEXT NOT NULL,
                    forkPointMessageId TEXT NOT NULL,
                    selectedText TEXT NOT NULL,
                    selectionStart INTEGER NOT NULL,
                    selectionEnd INTEGER NOT NULL,
                    isActive INTEGER NOT NULL DEFAULT 1,
                    createdAt INTEGER NOT NULL,
                    FOREIGN KEY (conversationId) REFERENCES Conversation(id) ON DELETE CASCADE,
                    FOREIGN KEY (forkPointMessageId) REFERENCES Message(id) ON DELETE CASCADE
                );
                """
            )

            # 4. ForkMessage Table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ForkMessage (
                    id TEXT PRIMARY KEY,
                    forkId TEXT NOT NULL,
                    parentId TEXT,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    createdAt INTEGER NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (forkId) REFERENCES ConversationFork(id) ON DELETE CASCADE,
                    FOREIGN KEY (parentId) REFERENCES ForkMessage(id) ON DELETE SET NULL
                );
                """
            )

            # Indices
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_conversation ON Message(conversationId);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_parent ON Message(parentId);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fork_conversation ON ConversationFork(conversationId);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fork_message_fork ON ForkMessage(forkId);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fork_message_parent ON ForkMessage(parentId);"
            )
            self._conn.commit()

    def create_conversation(
        self,
        title: str = "New Chat",
        model_config: Optional[Dict[str, Any]] = None,
        user_id: str = "default_user",
    ) -> str:
        """Create a new conversation session and persist it to SQLite."""
        conv_id = str(uuid.uuid4())
        config_str = json.dumps(model_config or {"provider": "local", "model": "chatgpt-oss-20b"})

        with self._lock:
            now_ms = self._get_timestamp_ms()
            self._conn.execute(
                """
                INSERT INTO Conversation (id, userId, title, modelConfig, createdAt, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conv_id, user_id, title, config_str, now_ms, now_ms),
            )
            self._conn.commit()

        logger.debug("Created conversation %s ('%s')", conv_id, title)
        return conv_id

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve conversation metadata by ID."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, userId, title, modelConfig, createdAt, updatedAt
                FROM Conversation
                WHERE id = ?
                """,
                (conversation_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_most_recent_conversation(self) -> Optional[Dict[str, Any]]:
        """Retrieve the most recently active / updated conversation."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, userId, title, modelConfig, createdAt, updatedAt
                FROM Conversation
                ORDER BY updatedAt DESC, rowid DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_or_create_active_conversation_id(self, default_title: str = "New Chat") -> str:
        """
        Return the ID of the most recent conversation if available,
        otherwise create a new conversation and return its ID.
        """
        recent = self.get_most_recent_conversation()
        if recent and recent.get("id"):
            return recent["id"]
        return self.create_conversation(title=default_title)

    def list_conversations(
        self,
        limit: int = 200,
        search_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List conversations ordered by most recently updated.
        Includes total message count and preview of the latest message.
        """
        with self._lock:
            if search_query and search_query.strip():
                pattern = f"%{search_query.strip()}%"
                cursor = self._conn.execute(
                    """
                    SELECT c.id, c.userId, c.title, c.modelConfig, c.createdAt, c.updatedAt,
                           COUNT(m.id) AS message_count,
                           (SELECT content FROM Message WHERE conversationId = c.id ORDER BY createdAt DESC, rowid DESC LIMIT 1) AS last_preview
                    FROM Conversation c
                    LEFT JOIN Message m ON m.conversationId = c.id
                    WHERE c.title LIKE ? OR c.id IN (SELECT conversationId FROM Message WHERE content LIKE ?)
                    GROUP BY c.id
                    ORDER BY c.updatedAt DESC, c.rowid DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, limit),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT c.id, c.userId, c.title, c.modelConfig, c.createdAt, c.updatedAt,
                           COUNT(m.id) AS message_count,
                           (SELECT content FROM Message WHERE conversationId = c.id ORDER BY createdAt DESC, rowid DESC LIMIT 1) AS last_preview
                    FROM Conversation c
                    LEFT JOIN Message m ON m.conversationId = c.id
                    GROUP BY c.id
                    ORDER BY c.updatedAt DESC, c.rowid DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        """Update conversation title and bump updatedAt timestamp."""
        with self._lock:
            now_ms = self._get_timestamp_ms()
            self._conn.execute(
                """
                UPDATE Conversation
                SET title = ?, updatedAt = ?
                WHERE id = ?
                """,
                (title, now_ms, conversation_id),
            )
            self._conn.commit()

    def touch_conversation(self, conversation_id: str) -> None:
        """Bump updatedAt timestamp for a conversation."""
        with self._lock:
            now_ms = self._get_timestamp_ms()
            self._conn.execute(
                "UPDATE Conversation SET updatedAt = ? WHERE id = ?",
                (now_ms, conversation_id),
            )
            self._conn.commit()

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete conversation and all its messages."""
        with self._lock:
            # Delete child messages first (in case foreign key cascade is disabled)
            self._conn.execute("DELETE FROM Message WHERE conversationId = ?", (conversation_id,))
            self._conn.execute("DELETE FROM ConversationFork WHERE conversationId = ?", (conversation_id,))
            cursor = self._conn.execute("DELETE FROM Conversation WHERE id = ?", (conversation_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear_conversation_messages(self, conversation_id: str) -> None:
        """Remove all messages from a conversation without deleting the conversation container."""
        with self._lock:
            now_ms = self._get_timestamp_ms()
            self._conn.execute("DELETE FROM Message WHERE conversationId = ?", (conversation_id,))
            self._conn.execute("DELETE FROM ConversationFork WHERE conversationId = ?", (conversation_id,))
            self._conn.execute(
                "UPDATE Conversation SET updatedAt = ? WHERE id = ?",
                (now_ms, conversation_id),
            )
            self._conn.commit()

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Append a message to the active conversation.
        Normalizes role to uppercase (USER, ASSISTANT, ERROR, TOOL, THOUGHT, SYSTEM).
        If conversation title is still default 'New Chat' and role is USER,
        automatically derives a clean descriptive title from the prompt.
        """
        msg_id = str(uuid.uuid4())
        role_upper = (role or "ASSISTANT").upper()
        meta_str = json.dumps(metadata) if metadata else None

        with self._lock:
            now_ms = self._get_timestamp_ms()
            # Ensure conversation exists; if not, create it
            cursor = self._conn.execute(
                "SELECT title FROM Conversation WHERE id = ?",
                (conversation_id,),
            )
            row = cursor.fetchone()
            current_title = row["title"] if row else None

            if current_title is None:
                # Auto-create conversation container
                new_title = _generate_title_from_prompt(content) if role_upper == "USER" else "New Chat"
                self._conn.execute(
                    """
                    INSERT INTO Conversation (id, userId, title, modelConfig, createdAt, updatedAt)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (conversation_id, "default_user", new_title, "{}", now_ms, now_ms),
                )
            elif role_upper == "USER" and (current_title == "New Chat" or not current_title.strip()):
                # Auto-title conversation on first user turn
                new_title = _generate_title_from_prompt(content)
                self._conn.execute(
                    "UPDATE Conversation SET title = ?, updatedAt = ? WHERE id = ?",
                    (new_title, now_ms, conversation_id),
                )
            else:
                self._conn.execute(
                    "UPDATE Conversation SET updatedAt = ? WHERE id = ?",
                    (now_ms, conversation_id),
                )

            # Insert message
            self._conn.execute(
                """
                INSERT INTO Message (id, conversationId, parentId, content, role, isMainThread, createdAt, metadata)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (msg_id, conversation_id, parent_id, content, role_upper, now_ms, meta_str),
            )
            self._conn.commit()

        logger.debug("Appended %s message %s to conversation %s", role_upper, msg_id, conversation_id)
        return msg_id

    def get_messages(
        self,
        conversation_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all messages for a conversation ordered chronologically.
        """
        with self._lock:
            query = """
                SELECT id, conversationId, parentId, content, role, isMainThread, createdAt, metadata
                FROM Message
                WHERE conversationId = ?
                ORDER BY createdAt ASC
            """
            params: list = [conversation_id]
            if limit is not None and limit > 0:
                query += " LIMIT ?"
                params.append(limit)

            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for r in rows:
                item = dict(r)
                if item.get("metadata"):
                    try:
                        item["metadata"] = json.loads(item["metadata"])
                    except Exception:
                        pass
                result.append(item)
            return result

    def update_message(self, message_id: str, content: str) -> bool:
        """Update content of an existing message."""
        with self._lock:
            now_ms = self._get_timestamp_ms()
            cursor = self._conn.execute(
                """
                UPDATE Message
                SET content = ?
                WHERE id = ?
                """,
                (content, message_id),
            )
            self._conn.execute(
                """
                UPDATE Conversation
                SET updatedAt = ?
                WHERE id = (SELECT conversationId FROM Message WHERE id = ?)
                """,
                (now_ms, message_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_message(self, message_id: str) -> bool:
        """Delete a specific message by ID."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM Message WHERE id = ?", (message_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_messages_after(self, conversation_id: str, message_id: str, inclusive: bool = False) -> int:
        """Delete messages that were created after (or starting from) the given message in a conversation."""
        with self._lock:
            cursor = self._conn.execute("SELECT createdAt FROM Message WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            if not row:
                return 0
            target_ts = row["createdAt"]
            op = ">=" if inclusive else ">"
            del_cursor = self._conn.execute(
                f"DELETE FROM Message WHERE conversationId = ? AND createdAt {op} ?",
                (conversation_id, target_ts),
            )
            self._conn.commit()
            return del_cursor.rowcount

    def close(self) -> None:
        """Close SQLite database connection cleanly."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
