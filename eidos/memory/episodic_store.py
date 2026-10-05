"""Tier A: Local Episodic Vector Database using sqlite-vec with zero external daemon requirements."""

import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from eidos.memory.embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    OllamaEmbeddingClient,
)
from eidos.memory.types import EpisodeRecord, EpisodicSearchResult

logger = logging.getLogger(__name__)

DEFAULT_VECTOR_STORE_DIR = Path.home() / ".eidos" / "memory" / "vector_store"
DEFAULT_EPISODIC_DB_PATH = DEFAULT_VECTOR_STORE_DIR / "episodic.db"


class EpisodicStore:
    """Embedded vector database for agent experiential learning and episodic recall."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        embedding_client: Optional[OllamaEmbeddingClient] = None,
        base_url: str = "http://localhost:11434",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_EMBEDDING_DIM,
    ):
        self.db_path = Path(os.path.expanduser(str(db_path or DEFAULT_EPISODIC_DB_PATH)))
        self.dimension = dimension
        self._lock = threading.Lock()

        # Initialize embedding client
        if embedding_client is not None:
            self.embedding_client = embedding_client
        else:
            self.embedding_client = OllamaEmbeddingClient(
                base_url=base_url,
                model=embedding_model,
                dimension=dimension,
                fallback_to_dummy=True,
            )

        self._ensure_storage()
        self._conn: Optional[sqlite3.Connection] = None
        self._has_sqlite_vec = False
        self._init_database()

    def _ensure_storage(self) -> None:
        """Create storage directories if necessary."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create episodic vector store directory %s: %s", self.db_path.parent, e)

    def _init_database(self) -> None:
        """Connect to SQLite and initialize relational and vector virtual tables."""
        with self._lock:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

            # Attempt to load sqlite-vec extension
            try:
                import sqlite_vec

                self._conn.enable_load_extension(True)
                sqlite_vec.load(self._conn)
                self._conn.enable_load_extension(False)
                self._has_sqlite_vec = True
                logger.debug("Successfully loaded sqlite-vec extension in %s", self.db_path)
            except Exception as e:
                self._has_sqlite_vec = False
                logger.info(
                    "sqlite-vec extension not loaded (%s). Running with numpy cosine similarity fallback.", e
                )

            # Create base relational metadata table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    task_goal TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    critic_score REAL,
                    timestamp TEXT NOT NULL,
                    summary TEXT,
                    pitfalls_avoided TEXT,
                    lessons_learned TEXT,
                    key_actions TEXT,
                    embedding_text TEXT,
                    full_record_json TEXT,
                    embedding_blob BLOB,
                    created_at REAL
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome);"
            )

            # If sqlite-vec is available, create virtual vector table vec0
            if self._has_sqlite_vec:
                try:
                    self._conn.execute(
                        f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
                            episode_id TEXT PRIMARY KEY,
                            embedding FLOAT[{self.dimension}] distance_metric=cosine
                        );
                        """
                    )
                except Exception as e:
                    logger.warning("Failed to initialize vec_episodes virtual table: %s", e)
                    self._has_sqlite_vec = False

            self._conn.commit()

    @property
    def has_sqlite_vec(self) -> bool:
        """Return True if hardware-accelerated sqlite-vec virtual table is active."""
        return self._has_sqlite_vec

    def _is_tool_observation_successful(self, obs: Any) -> bool:
        """Evaluate whether a tool observation indicates a successful execution."""
        if not isinstance(obs, dict):
            return True
        if obs.get("exit_code") not in (None, 0):
            return False
        if obs.get("success") is False:
            return False
        if obs.get("denied") is True:
            return False
        if obs.get("error") and not obs.get("stdout") and obs.get("exit_code") != 0:
            return False
        return True

    def _extract_failure_reason(self, obs: Any) -> str:
        """Extract a short, high-signal failure reason from an observation."""
        if not isinstance(obs, dict):
            return "failed"
        if obs.get("denied"):
            return "denied by safety policy"
        err = obs.get("error") or obs.get("stderr") or ""
        if err:
            lines = [line.strip() for line in str(err).splitlines() if line.strip()]
            raw = lines[0] if lines else "command failed"
            # Remove bash/sh shell prefix like "/bin/bash: line 1: " or "bash: line 1: "
            cleaned = re.sub(r"^.*?bash:\s*(?:line \d+:\s*)?", "", raw).strip()
            return cleaned[:70] if cleaned else raw[:70]
        exit_code = obs.get("exit_code")
        if exit_code not in (None, 0):
            return f"exit code {exit_code}"
        return "failed"

    def _format_action_call(self, name: str, args: Dict[str, Any]) -> str:
        """Format a tool invocation into a concise representation."""
        if name == "execute_shell":
            cmd = args.get("command", "")
            if cmd:
                short_cmd = cmd.strip().splitlines()[0][:80]
                return f"execute_shell({short_cmd})"
        elif name in ("write_file", "edit_file"):
            p = args.get("path", "")
            return f"{name}({p})"
        elif name == "click_element":
            desc = args.get("description", "")
            return f"click_element({desc})"
        elif name == "download_file":
            u = args.get("url", "")
            return f"download_file({u})"
        elif name in ("web_search", "web_fetch"):
            q = args.get("query") or args.get("url", "")
            return f"{name}({q})"
        elif name and name != "finish_task":
            first_val = next((v for v in args.values() if isinstance(v, str)), "")
            if first_val:
                return f"{name}({first_val[:40]})"
            return name
        return ""

    def _extract_summary_and_actions(self, record: EpisodeRecord) -> Dict[str, Any]:
        """Extract reflection components, all successful actions in order, and dead-end failures."""
        summary = ""
        pitfalls_avoided = ""
        lessons_learned = ""

        if record.reflection:
            summary = record.reflection.summary or ""
            pitfalls_avoided = record.reflection.pitfalls_avoided or ""
            lessons_learned = record.reflection.lessons_learned or ""

        successful_actions: List[str] = []
        failed_actions: List[str] = []

        for step in record.trajectory:
            tool_calls = step.tool_calls or []
            tool_obs = step.tool_observations or []

            for i, tc in enumerate(tool_calls):
                name = tc.get("name", "")
                args = tc.get("arguments", {})

                if name == "finish_task":
                    if not summary:
                        summary = args.get("summary", "")
                    continue

                obs = tool_obs[i] if i < len(tool_obs) else {}
                action_str = self._format_action_call(name, args)
                if not action_str:
                    continue

                if self._is_tool_observation_successful(obs):
                    # Keep successful actions in proper forward chronological order
                    if not successful_actions or successful_actions[-1] != action_str:
                        successful_actions.append(action_str)
                else:
                    reason = self._extract_failure_reason(obs)
                    fail_str = f"{action_str} -> [{reason}]"
                    failed_actions.append(fail_str)

        # Synthesize fallback summary if not explicitly provided
        if not summary and record.trajectory:
            for step in reversed(record.trajectory):
                content = (step.model_content or "").strip()
                if content:
                    summary = content.splitlines()[0][:150]
                    break
        if not summary:
            summary = record.task_goal

        # Deduplicate failed actions and pick the top 2-3 to avoid prompt bloat
        unique_failed: List[str] = []
        for fa in failed_actions:
            if fa not in unique_failed:
                unique_failed.append(fa)
        top_failed = unique_failed[:3]

        if top_failed:
            dead_ends_text = "; ".join(top_failed)
            if pitfalls_avoided:
                if dead_ends_text not in pitfalls_avoided:
                    pitfalls_avoided = f"{pitfalls_avoided} (Dead ends: {dead_ends_text})"
            else:
                pitfalls_avoided = dead_ends_text

        # If successful actions exist, maintain all of them in forward order
        # If no actions succeeded (e.g. total failure), provide attempted actions so context is preserved
        if successful_actions:
            key_actions = successful_actions
        else:
            key_actions = [fa.split(" -> ")[0] for fa in unique_failed]

        return {
            "summary": summary,
            "pitfalls_avoided": pitfalls_avoided,
            "lessons_learned": lessons_learned,
            "key_actions": key_actions,
        }

    def _build_embedding_text(
        self,
        task_goal: str,
        outcome: str,
        summary: str,
        pitfalls_avoided: str,
        lessons_learned: str,
        key_actions: List[str],
    ) -> str:
        """Synthesize high-signal text representation for vector embedding."""
        parts = [f"Task: {task_goal}", f"Outcome: {outcome}"]
        if summary:
            parts.append(f"Summary: {summary}")
        if lessons_learned:
            parts.append(f"Lessons: {lessons_learned}")
        if pitfalls_avoided:
            parts.append(f"Pitfalls: {pitfalls_avoided}")
        if key_actions:
            parts.append("Actions: " + " -> ".join(key_actions[:10]))
        return "\n".join(parts)

    def _vector_to_blob(self, vector: List[float]) -> bytes:
        """Serialize float array to binary 32-bit float bytes."""
        return struct.pack(f"{len(vector)}f", *vector)

    def _blob_to_vector(self, blob: bytes) -> List[float]:
        """Deserialize 32-bit float bytes back to Python float list."""
        count = len(blob) // 4
        return list(struct.unpack(f"{count}f", blob))

    def index_episode(self, record: EpisodeRecord, overwrite: bool = True) -> bool:
        """Generate embedding and persist an EpisodeRecord to episodic vector database."""
        extracted = self._extract_summary_and_actions(record)
        summary = extracted["summary"]
        pitfalls_avoided = extracted["pitfalls_avoided"]
        lessons_learned = extracted["lessons_learned"]
        key_actions = extracted["key_actions"]

        outcome = record.metadata.outcome
        embedding_text = self._build_embedding_text(
            task_goal=record.task_goal,
            outcome=outcome,
            summary=summary,
            pitfalls_avoided=pitfalls_avoided,
            lessons_learned=lessons_learned,
            key_actions=key_actions,
        )

        try:
            vector = self.embedding_client.embed_text(embedding_text)
            if len(vector) != self.dimension:
                logger.error(
                    "Embedding dimension mismatch: expected %d, got %d", self.dimension, len(vector)
                )
                return False
        except Exception as e:
            logger.error("Failed to generate embedding for episode %s: %s", record.episode_id, e)
            return False

        blob = self._vector_to_blob(vector)
        record_json = record.model_dump_json()
        now_ts = time.time()

        with self._lock:
            try:
                # Check if episode exists
                cursor = self._conn.execute(
                    "SELECT episode_id FROM episodes WHERE episode_id = ?", (record.episode_id,)
                )
                exists = cursor.fetchone() is not None

                if exists and not overwrite:
                    logger.debug("Episode %s already indexed; skipping.", record.episode_id)
                    return False

                if exists:
                    # Update metadata table
                    self._conn.execute(
                        """
                        UPDATE episodes SET
                            task_goal = ?, outcome = ?, critic_score = ?, timestamp = ?,
                            summary = ?, pitfalls_avoided = ?, lessons_learned = ?,
                            key_actions = ?, embedding_text = ?, full_record_json = ?,
                            embedding_blob = ?, created_at = ?
                        WHERE episode_id = ?
                        """,
                        (
                            record.task_goal,
                            outcome,
                            record.metadata.critic_score,
                            record.timestamp,
                            summary,
                            pitfalls_avoided,
                            lessons_learned,
                            json.dumps(key_actions),
                            embedding_text,
                            record_json,
                            blob,
                            now_ts,
                            record.episode_id,
                        ),
                    )
                    if self._has_sqlite_vec:
                        try:
                            self._conn.execute(
                                "DELETE FROM vec_episodes WHERE episode_id = ?", (record.episode_id,)
                            )
                            self._conn.execute(
                                "INSERT INTO vec_episodes(episode_id, embedding) VALUES (?, ?)",
                                (record.episode_id, blob),
                            )
                        except Exception as ve:
                            logger.warning("Failed to update vec_episodes for %s: %s", record.episode_id, ve)
                else:
                    # Insert into relational table
                    self._conn.execute(
                        """
                        INSERT INTO episodes (
                            episode_id, task_goal, outcome, critic_score, timestamp,
                            summary, pitfalls_avoided, lessons_learned, key_actions,
                            embedding_text, full_record_json, embedding_blob, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.episode_id,
                            record.task_goal,
                            outcome,
                            record.metadata.critic_score,
                            record.timestamp,
                            summary,
                            pitfalls_avoided,
                            lessons_learned,
                            json.dumps(key_actions),
                            embedding_text,
                            record_json,
                            blob,
                            now_ts,
                        ),
                    )
                    if self._has_sqlite_vec:
                        try:
                            self._conn.execute(
                                "INSERT INTO vec_episodes(episode_id, embedding) VALUES (?, ?)",
                                (record.episode_id, blob),
                            )
                        except Exception as ve:
                            logger.warning("Failed to insert into vec_episodes for %s: %s", record.episode_id, ve)

                self._conn.commit()
                logger.info(
                    "Indexed episode %s (outcome=%s) in episodic store", record.episode_id, outcome
                )
                return True
            except Exception as e:
                logger.error("Failed to save episode %s to episodic store: %s", record.episode_id, e)
                return False

    def search(
        self,
        query: str,
        top_k: int = 5,
        outcome_filter: Optional[str] = None,
        min_similarity: float = 0.0,
    ) -> List[EpisodicSearchResult]:
        """Perform semantic similarity search against indexed episodic memories."""
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        try:
            query_vector = self.embedding_client.embed_text(cleaned_query)
        except Exception as e:
            logger.error("Failed to generate query embedding: %s", e)
            return []

        if self._has_sqlite_vec:
            return self._search_sqlite_vec(
                query_vector=query_vector,
                top_k=top_k,
                outcome_filter=outcome_filter,
                min_similarity=min_similarity,
            )
        else:
            return self._search_numpy_fallback(
                query_vector=query_vector,
                top_k=top_k,
                outcome_filter=outcome_filter,
                min_similarity=min_similarity,
            )

    def _search_sqlite_vec(
        self,
        query_vector: List[float],
        top_k: int,
        outcome_filter: Optional[str],
        min_similarity: float,
    ) -> List[EpisodicSearchResult]:
        """Execute nearest-neighbor vector search via sqlite-vec extension."""
        query_blob = self._vector_to_blob(query_vector)
        # Fetch larger k from vector index to allow post-filtering by outcome
        fetch_k = max(top_k * 6, 25)

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT v.episode_id, v.distance, e.task_goal, e.outcome,
                           e.critic_score, e.timestamp, e.summary, e.pitfalls_avoided,
                           e.lessons_learned, e.key_actions
                    FROM vec_episodes v
                    JOIN episodes e ON e.episode_id = v.episode_id
                    WHERE v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance ASC
                    """,
                    (query_blob, fetch_k),
                )
                rows = cursor.fetchall()
            except Exception as e:
                logger.warning("sqlite-vec query failed, falling back to numpy: %s", e)
                return self._search_numpy_fallback(query_vector, top_k, outcome_filter, min_similarity)

        results: List[EpisodicSearchResult] = []
        for r in rows:
            # sqlite-vec distance for cosine metric is 1 - cosine_similarity
            distance = float(r["distance"])
            similarity = max(0.0, min(1.0, 1.0 - distance))

            if similarity < min_similarity:
                continue

            outcome = r["outcome"]
            if outcome_filter is not None:
                if outcome_filter == "pitfall":
                    # Matches failed/aborted episodes OR episodes that have pitfalls avoided
                    has_pitfall = bool(r["pitfalls_avoided"] and r["pitfalls_avoided"].strip())
                    if outcome == "success" and not has_pitfall:
                        continue
                elif outcome != outcome_filter:
                    continue

            key_actions: List[str] = []
            if r["key_actions"]:
                try:
                    key_actions = json.loads(r["key_actions"])
                except Exception:
                    pass

            results.append(
                EpisodicSearchResult(
                    episode_id=r["episode_id"],
                    task_goal=r["task_goal"],
                    outcome=outcome,
                    critic_score=r["critic_score"],
                    similarity=round(similarity, 4),
                    summary=r["summary"] or "",
                    pitfalls_avoided=r["pitfalls_avoided"] or "",
                    lessons_learned=r["lessons_learned"] or "",
                    key_actions=key_actions,
                    timestamp=r["timestamp"] or "",
                )
            )

            if len(results) >= top_k:
                break

        return results

    def _search_numpy_fallback(
        self,
        query_vector: List[float],
        top_k: int,
        outcome_filter: Optional[str],
        min_similarity: float,
    ) -> List[EpisodicSearchResult]:
        """Pure-python/numpy cosine similarity calculation over stored BLOBs."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT episode_id, task_goal, outcome, critic_score, timestamp,
                       summary, pitfalls_avoided, lessons_learned, key_actions,
                       embedding_blob
                FROM episodes
                """
            )
            rows = cursor.fetchall()

        if not rows:
            return []

        q_vec = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return []

        candidates: List[tuple] = []
        for r in rows:
            blob = r["embedding_blob"]
            if not blob:
                continue

            vec = np.frombuffer(blob, dtype=np.float32)
            v_norm = np.linalg.norm(vec)
            if v_norm == 0:
                continue

            cos_sim = float(np.dot(q_vec, vec) / (q_norm * v_norm))
            similarity = max(0.0, min(1.0, cos_sim))

            if similarity < min_similarity:
                continue

            outcome = r["outcome"]
            if outcome_filter is not None:
                if outcome_filter == "pitfall":
                    has_pitfall = bool(r["pitfalls_avoided"] and r["pitfalls_avoided"].strip())
                    if outcome == "success" and not has_pitfall:
                        continue
                elif outcome != outcome_filter:
                    continue

            key_actions: List[str] = []
            if r["key_actions"]:
                try:
                    key_actions = json.loads(r["key_actions"])
                except Exception:
                    pass

            item = EpisodicSearchResult(
                episode_id=r["episode_id"],
                task_goal=r["task_goal"],
                outcome=outcome,
                critic_score=r["critic_score"],
                similarity=round(similarity, 4),
                summary=r["summary"] or "",
                pitfalls_avoided=r["pitfalls_avoided"] or "",
                lessons_learned=r["lessons_learned"] or "",
                key_actions=key_actions,
                timestamp=r["timestamp"] or "",
            )
            candidates.append((similarity, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in candidates[:top_k]]

    def retrieve_macro_context(
        self,
        task_goal: str,
        top_k_success: int = 1,
        top_k_pitfall: int = 1,
        min_similarity: float = 0.15,
    ) -> str:
        """Retrieve top golden path success and cautionary pitfall for macro context injection."""
        if not task_goal.strip():
            return ""

        successes = self.search(
            query=task_goal,
            top_k=top_k_success,
            outcome_filter="success",
            min_similarity=min_similarity,
        )
        success_ids = {s.episode_id for s in successes}

        raw_pitfalls = self.search(
            query=task_goal,
            top_k=top_k_pitfall + len(success_ids) * 2,
            outcome_filter="pitfall",
            min_similarity=min_similarity,
        )

        # Avoid showing the same episode in both sections
        filtered_pitfalls = [p for p in raw_pitfalls if p.episode_id not in success_ids][:top_k_pitfall]

        if not successes and not filtered_pitfalls:
            return ""

        sections: List[str] = ["<episodic_context>"]

        for s in successes:
            sections.append(f"Past Experience - Golden Path Success (Similarity: {s.similarity:.2f}):")
            sections.append(f"- Past Task: {s.task_goal}")
            if s.summary:
                sections.append(f"- Resolution Summary: {s.summary}")
            if s.pitfalls_avoided:
                sections.append(f"- Dead Ends / Pitfalls Avoided: {s.pitfalls_avoided}")
            if s.lessons_learned:
                sections.append(f"- Lessons: {s.lessons_learned}")
            if s.key_actions:
                actions_str = " -> ".join(s.key_actions)
                sections.append(f"- Proven Tool Pattern: {actions_str}")
            sections.append("")

        for p in filtered_pitfalls:
            sections.append(f"Past Experience - Cautionary Pitfall (Similarity: {p.similarity:.2f}):")
            sections.append(f"- Past Task: {p.task_goal}")
            sections.append(f"- Outcome: {p.outcome}")
            if p.pitfalls_avoided:
                sections.append(f"- Pitfall Avoided: {p.pitfalls_avoided}")
            elif p.summary:
                sections.append(f"- Issue Encountered: {p.summary}")
            if p.lessons_learned:
                sections.append(f"- Crucial Lesson: {p.lessons_learned}")
            if p.key_actions:
                actions_str = " -> ".join(p.key_actions)
                sections.append(f"- Actions Attempted: {actions_str}")
            sections.append("")

        sections.append("</episodic_context>")
        return "\n".join(sections).strip()

    def sync_from_trajectory_file(
        self,
        jsonl_path: Optional[str | Path] = None,
        reindex: bool = False,
    ) -> int:
        """Scan a trajectory JSONL file and index any unindexed (or all if reindex=True) episodes into the vector DB."""
        target_path = Path(
            os.path.expanduser(
                str(jsonl_path or Path.home() / ".eidos" / "trajectories" / "episodes.jsonl")
            )
        )
        if not target_path.exists():
            logger.info("Trajectory file %s does not exist; nothing to sync.", target_path)
            return 0

        # Collect existing episode IDs
        with self._lock:
            existing_ids = (
                {
                    row[0]
                    for row in self._conn.execute("SELECT episode_id FROM episodes").fetchall()
                }
                if not reindex
                else set()
            )

        new_records: List[EpisodeRecord] = []
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        rec = EpisodeRecord(**data)
                        if reindex or rec.episode_id not in existing_ids:
                            new_records.append(rec)
                    except Exception as err:
                        logger.warning("Failed to parse trajectory JSONL line: %s", err)
        except Exception as e:
            logger.error("Failed to read trajectory file %s: %s", target_path, e)
            return 0

        indexed_count = 0
        for rec in new_records:
            if self.index_episode(rec, overwrite=reindex):
                indexed_count += 1

        logger.info("Synced %d new episodes from %s to episodic store.", indexed_count, target_path)
        return indexed_count

    def get_episode_record(self, episode_id: str) -> Optional[EpisodeRecord]:
        """Fetch full original EpisodeRecord by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT full_record_json FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            if row and row["full_record_json"]:
                try:
                    return EpisodeRecord(**json.loads(row["full_record_json"]))
                except Exception:
                    pass
        return None

    def delete_episode(self, episode_id: str) -> bool:
        """Delete an episode from both relational and vector virtual tables."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM episodes WHERE episode_id = ?", (episode_id,))
            if self._has_sqlite_vec:
                try:
                    self._conn.execute("DELETE FROM vec_episodes WHERE episode_id = ?", (episode_id,))
                except Exception:
                    pass
            self._conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        """Return total number of indexed episodes in the store."""
        with self._lock:
            row = self._conn.execute("SELECT count(*) FROM episodes").fetchone()
            return row[0] if row else 0

    def clear(self) -> None:
        """Purge all episodes from the vector database."""
        with self._lock:
            self._conn.execute("DELETE FROM episodes")
            if self._has_sqlite_vec:
                try:
                    self._conn.execute("DELETE FROM vec_episodes")
                except Exception:
                    pass
            self._conn.commit()

    def close(self) -> None:
        """Safely close SQLite connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
