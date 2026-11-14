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
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from eidos.memory.embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    OllamaEmbeddingClient,
)
from eidos.memory.entity_extractor import EntityExtractor
from eidos.memory.graph_store import GraphStore
from eidos.memory.types import (
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    EpisodicSearchResult,
    ReflectionEvaluation,
)

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
        embedding_num_gpu: Optional[int] = 0,
    ):
        self.db_path = Path(os.path.expanduser(str(db_path or DEFAULT_EPISODIC_DB_PATH)))
        self.dimension = dimension
        self._lock = threading.RLock()
        self.entity_extractor = EntityExtractor()

        # Initialize embedding client
        if embedding_client is not None:
            self.embedding_client = embedding_client
        else:
            self.embedding_client = OllamaEmbeddingClient(
                base_url=base_url,
                model=embedding_model,
                dimension=dimension,
                fallback_to_dummy=True,
                num_gpu=embedding_num_gpu,
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
        """Connect to SQLite and initialize relational, vector, and graph tables."""
        with self._lock:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")

            self.graph_store = GraphStore(self._conn, self._lock)

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
                    created_at REAL,
                    entity_link_attempted INTEGER DEFAULT 0,
                    is_canonical INTEGER DEFAULT 0,
                    is_archived INTEGER DEFAULT 0,
                    cluster_id TEXT,
                    superseded_by TEXT,
                    consolidation_score REAL DEFAULT 1.0
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome);"
            )

            # Existing databases predate consolidation columns. Add them before
            # indexing is_archived / cluster_id, or CREATE INDEX fails on startup.
            self._ensure_episode_schema_migrations()
            self.graph_store.init_schema()

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

    def _ensure_episode_schema_migrations(self) -> None:
        """Apply lightweight episodic-store schema migrations for existing databases."""
        try:
            cols = {
                row[1] for row in self._conn.execute("PRAGMA table_info(episodes)").fetchall()
            }
            if "entity_link_attempted" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN entity_link_attempted INTEGER DEFAULT 0"
                )
            if "is_canonical" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN is_canonical INTEGER DEFAULT 0"
                )
            if "is_archived" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN is_archived INTEGER DEFAULT 0"
                )
            if "cluster_id" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN cluster_id TEXT"
                )
            if "superseded_by" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN superseded_by TEXT"
                )
            if "consolidation_score" not in cols:
                self._conn.execute(
                    "ALTER TABLE episodes ADD COLUMN consolidation_score REAL DEFAULT 1.0"
                )

            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_active ON episodes(is_archived, is_canonical);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_cluster ON episodes(cluster_id);"
            )
            self._conn.commit()
        except Exception as e:
            logger.warning("Episode schema migration skipped: %s", e)

    def _set_entity_link_attempted(self, episode_id: str) -> None:
        """Mark that entity extraction/linking was attempted for this episode."""
        try:
            self._conn.execute(
                "UPDATE episodes SET entity_link_attempted = 1 WHERE episode_id = ?",
                (episode_id,),
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(
                "Failed to mark entity_link_attempted for episode %s: %s", episode_id, e
            )

    def _link_episode_entities_from_record(self, record: EpisodeRecord) -> None:
        """Extract entities from a record and sync graph links for the episode."""
        entities = self.entity_extractor.extract_from_episode(record)
        if entities:
            self.graph_store.link_episode_entities(
                record.episode_id, entities, timestamp=record.timestamp
            )
        else:
            self.graph_store.delete_episode_links(record.episode_id)

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

                is_canonical_val = int(bool(getattr(record.metadata, "is_canonical", False)))
                is_archived_val = int(bool(getattr(record.metadata, "is_archived", False)))
                cluster_id_val = getattr(record.metadata, "cluster_id", None)
                superseded_by_val = getattr(record.metadata, "superseded_by", None)
                raw_score = getattr(record.metadata, "consolidation_score", None)
                consolidation_score_val = float(raw_score) if raw_score is not None else 1.0

                if exists:
                    # Update metadata table
                    self._conn.execute(
                        """
                        UPDATE episodes SET
                            task_goal = ?, outcome = ?, critic_score = ?, timestamp = ?,
                            summary = ?, pitfalls_avoided = ?, lessons_learned = ?,
                            key_actions = ?, embedding_text = ?, full_record_json = ?,
                            embedding_blob = ?, created_at = ?, is_canonical = ?,
                            is_archived = ?, cluster_id = ?, superseded_by = ?,
                            consolidation_score = ?
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
                            is_canonical_val,
                            is_archived_val,
                            cluster_id_val,
                            superseded_by_val,
                            consolidation_score_val,
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
                            embedding_text, full_record_json, embedding_blob, created_at,
                            is_canonical, is_archived, cluster_id, superseded_by,
                            consolidation_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            is_canonical_val,
                            is_archived_val,
                            cluster_id_val,
                            superseded_by_val,
                            consolidation_score_val,
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

                try:
                    self._link_episode_entities_from_record(record)
                except Exception as ge:
                    logger.warning("Failed to link entities for episode %s: %s", record.episode_id, ge)
                finally:
                    self._set_entity_link_attempted(record.episode_id)

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
        entity_weight: float = 0.35,
        query_vector: Optional[List[float]] = None,
        include_archived: bool = False,
    ) -> List[EpisodicSearchResult]:
        """Hybrid semantic and graph-augmented search against indexed episodic memories.

        When the query yields one or more entities, ranking uses composite similarity
        ``(1 - entity_weight) * vector + entity_weight * graph_overlap`` even if no
        episodes match on the graph (graph overlap treated as 0). ``EpisodicSearchResult.similarity``
        reflects that composite score in hybrid mode, otherwise raw vector similarity.
        Archived duplicate episodes (is_archived = 1) are excluded by default unless include_archived=True.
        """
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        if query_vector is None:
            try:
                query_vector = self.embedding_client.embed_text(cleaned_query)
            except Exception as e:
                logger.error("Failed to generate query embedding: %s", e)
                return []

        query_entities = self.entity_extractor.extract_from_query(cleaned_query)
        graph_matches: Optional[Dict[str, Tuple[float, List[str]]]] = None
        if query_entities:
            graph_scores = self.graph_store.find_episodes_by_entities(query_entities)
            graph_matches = {
                ep_id: (
                    score,
                    self.graph_store.matched_query_entity_ids(ep_id, query_entities),
                )
                for ep_id, score in graph_scores.items()
            }

        if self._has_sqlite_vec:
            return self._search_sqlite_vec(
                query_vector=query_vector,
                top_k=top_k,
                outcome_filter=outcome_filter,
                min_similarity=min_similarity,
                graph_matches=graph_matches,
                entity_weight=entity_weight,
                include_archived=include_archived,
            )
        else:
            return self._search_numpy_fallback(
                query_vector=query_vector,
                top_k=top_k,
                outcome_filter=outcome_filter,
                min_similarity=min_similarity,
                graph_matches=graph_matches,
                entity_weight=entity_weight,
                include_archived=include_archived,
            )

    def _search_sqlite_vec(
        self,
        query_vector: List[float],
        top_k: int,
        outcome_filter: Optional[str],
        min_similarity: float,
        graph_matches: Optional[Dict[str, Tuple[float, List[str]]]] = None,
        entity_weight: float = 0.35,
        include_archived: bool = False,
    ) -> List[EpisodicSearchResult]:
        """Execute nearest-neighbor vector search via sqlite-vec extension with hybrid entity re-ranking."""
        query_blob = self._vector_to_blob(query_vector)
        # Fetch larger k from vector index to allow post-filtering and re-ranking
        fetch_k = max(top_k * 6, 30)

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT v.episode_id, v.distance, e.task_goal, e.outcome,
                           e.critic_score, e.timestamp, e.summary, e.pitfalls_avoided,
                           e.lessons_learned, e.key_actions, e.embedding_blob,
                           e.is_canonical, e.is_archived, e.cluster_id, e.superseded_by,
                           e.consolidation_score
                    FROM vec_episodes v
                    JOIN episodes e ON e.episode_id = v.episode_id
                    WHERE v.embedding MATCH ? AND k = ? AND (e.is_archived = 0 OR ?)
                    ORDER BY v.distance ASC
                    """,
                    (query_blob, fetch_k, 1 if include_archived else 0),
                )
                rows = cursor.fetchall()
            except Exception as e:
                logger.warning("sqlite-vec query failed, falling back to numpy: %s", e)
                return self._search_numpy_fallback(
                    query_vector,
                    top_k,
                    outcome_filter,
                    min_similarity,
                    graph_matches=graph_matches,
                    entity_weight=entity_weight,
                    include_archived=include_archived,
                )

        seen_ids = {r["episode_id"] for r in rows}
        extra_rows = []

        if graph_matches:
            missing_ids = [ep_id for ep_id in graph_matches if ep_id not in seen_ids]
            if missing_ids:
                placeholders = ",".join("?" for _ in missing_ids)
                with self._lock:
                    try:
                        extra_cursor = self._conn.execute(
                            f"""
                            SELECT episode_id, 0.0 as distance, task_goal, outcome,
                                   critic_score, timestamp, summary, pitfalls_avoided,
                                   lessons_learned, key_actions, embedding_blob,
                                   is_canonical, is_archived, cluster_id, superseded_by,
                                   consolidation_score
                            FROM episodes
                            WHERE episode_id IN ({placeholders}) AND (is_archived = 0 OR ?)
                            """,
                            list(missing_ids) + [1 if include_archived else 0],
                        )
                        extra_rows = extra_cursor.fetchall()
                    except Exception as extra_err:
                        logger.warning("Error fetching extra graph-matched episodes: %s", extra_err)

        all_rows = list(rows) + list(extra_rows)
        candidates: List[Tuple[float, EpisodicSearchResult]] = []
        q_vec = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)

        for r in all_rows:
            ep_id = r["episode_id"]
            if ep_id in seen_ids and "distance" in r.keys() and r["distance"] is not None:
                distance = float(r["distance"])
                vec_sim = max(0.0, min(1.0, 1.0 - distance))
            else:
                blob = r["embedding_blob"]
                if blob and q_norm > 0:
                    vec = np.frombuffer(blob, dtype=np.float32)
                    v_norm = np.linalg.norm(vec)
                    vec_sim = max(0.0, min(1.0, float(np.dot(q_vec, vec) / (q_norm * v_norm)))) if v_norm > 0 else 0.0
                else:
                    vec_sim = 0.0

            g_score, matched_ents = graph_matches.get(ep_id, (0.0, [])) if graph_matches is not None else (0.0, [])
            if graph_matches is not None:
                composite_sim = (1.0 - entity_weight) * vec_sim + entity_weight * g_score
            else:
                composite_sim = vec_sim

            if composite_sim < min_similarity:
                continue

            is_archived = bool(r["is_archived"]) if "is_archived" in r.keys() and r["is_archived"] is not None else False
            if not include_archived and is_archived:
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

            is_canonical = bool(r["is_canonical"]) if "is_canonical" in r.keys() and r["is_canonical"] is not None else False
            cluster_id = r["cluster_id"] if "cluster_id" in r.keys() else None
            superseded_by = r["superseded_by"] if "superseded_by" in r.keys() else None
            consolidation_score = float(r["consolidation_score"]) if "consolidation_score" in r.keys() and r["consolidation_score"] is not None else 1.0

            item = EpisodicSearchResult(
                episode_id=ep_id,
                task_goal=r["task_goal"],
                outcome=outcome,
                critic_score=r["critic_score"],
                similarity=round(composite_sim, 4),
                summary=r["summary"] or "",
                pitfalls_avoided=r["pitfalls_avoided"] or "",
                lessons_learned=r["lessons_learned"] or "",
                key_actions=key_actions,
                timestamp=r["timestamp"] or "",
                matched_entities=matched_ents,
                is_canonical=is_canonical,
                is_archived=is_archived,
                cluster_id=cluster_id,
                superseded_by=superseded_by,
                consolidation_score=consolidation_score,
            )
            candidates.append((composite_sim, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in candidates[:top_k]]

    def _search_numpy_fallback(
        self,
        query_vector: List[float],
        top_k: int,
        outcome_filter: Optional[str],
        min_similarity: float,
        graph_matches: Optional[Dict[str, Tuple[float, List[str]]]] = None,
        entity_weight: float = 0.35,
        include_archived: bool = False,
    ) -> List[EpisodicSearchResult]:
        """Pure-python/numpy cosine similarity calculation over stored BLOBs with hybrid entity re-ranking."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT episode_id, task_goal, outcome, critic_score, timestamp,
                       summary, pitfalls_avoided, lessons_learned, key_actions,
                       embedding_blob, is_canonical, is_archived, cluster_id,
                       superseded_by, consolidation_score
                FROM episodes
                WHERE (is_archived = 0 OR ?)
                """,
                (1 if include_archived else 0,),
            )
            rows = cursor.fetchall()

        if not rows:
            return []

        q_vec = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return []

        row_by_id = {r["episode_id"]: r for r in rows}
        if graph_matches:
            missing_ids = [ep_id for ep_id in graph_matches if ep_id not in row_by_id]
            if missing_ids:
                placeholders = ",".join("?" for _ in missing_ids)
                with self._lock:
                    extra_cursor = self._conn.execute(
                        f"""
                        SELECT episode_id, task_goal, outcome, critic_score, timestamp,
                               summary, pitfalls_avoided, lessons_learned, key_actions,
                               embedding_blob, is_canonical, is_archived, cluster_id,
                               superseded_by, consolidation_score
                        FROM episodes
                        WHERE episode_id IN ({placeholders}) AND (is_archived = 0 OR ?)
                        """,
                        list(missing_ids) + [1 if include_archived else 0],
                    )
                    for extra in extra_cursor.fetchall():
                        row_by_id[extra["episode_id"]] = extra

        candidates: List[tuple] = []
        for ep_id, r in row_by_id.items():
            blob = r["embedding_blob"]
            if blob and q_norm > 0:
                vec = np.frombuffer(blob, dtype=np.float32)
                v_norm = np.linalg.norm(vec)
                if v_norm == 0:
                    vec_sim = 0.0
                else:
                    cos_sim = float(np.dot(q_vec, vec) / (q_norm * v_norm))
                    vec_sim = max(0.0, min(1.0, cos_sim))
            else:
                vec_sim = 0.0

            g_score, matched_ents = graph_matches.get(ep_id, (0.0, [])) if graph_matches is not None else (0.0, [])
            if graph_matches is not None:
                composite_sim = (1.0 - entity_weight) * vec_sim + entity_weight * g_score
            else:
                composite_sim = vec_sim

            if composite_sim < min_similarity:
                continue

            is_archived = bool(r["is_archived"]) if "is_archived" in r.keys() and r["is_archived"] is not None else False
            if not include_archived and is_archived:
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

            is_canonical = bool(r["is_canonical"]) if "is_canonical" in r.keys() and r["is_canonical"] is not None else False
            cluster_id = r["cluster_id"] if "cluster_id" in r.keys() else None
            superseded_by = r["superseded_by"] if "superseded_by" in r.keys() else None
            consolidation_score = float(r["consolidation_score"]) if "consolidation_score" in r.keys() and r["consolidation_score"] is not None else 1.0

            item = EpisodicSearchResult(
                episode_id=ep_id,
                task_goal=r["task_goal"],
                outcome=outcome,
                critic_score=r["critic_score"],
                similarity=round(composite_sim, 4),
                summary=r["summary"] or "",
                pitfalls_avoided=r["pitfalls_avoided"] or "",
                lessons_learned=r["lessons_learned"] or "",
                key_actions=key_actions,
                timestamp=r["timestamp"] or "",
                matched_entities=matched_ents,
                is_canonical=is_canonical,
                is_archived=is_archived,
                cluster_id=cluster_id,
                superseded_by=superseded_by,
                consolidation_score=consolidation_score,
            )
            candidates.append((composite_sim, item))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in candidates[:top_k]]

    def retrieve_macro_context(
        self,
        task_goal: str,
        top_k_success: int = 1,
        top_k_pitfall: int = 1,
        min_similarity: float = 0.15,
        include_archived: bool = False,
    ) -> str:
        """Retrieve top golden path success and cautionary pitfall for macro context injection."""
        if not task_goal.strip():
            return ""

        try:
            shared_vector = self.embedding_client.embed_text(task_goal.strip())
        except Exception as e:
            logger.error("Failed to generate macro context embedding: %s", e)
            return ""

        successes = self.search(
            query=task_goal,
            top_k=top_k_success,
            outcome_filter="success",
            min_similarity=min_similarity,
            query_vector=shared_vector,
            include_archived=include_archived,
        )
        success_ids = {s.episode_id for s in successes}

        raw_pitfalls = self.search(
            query=task_goal,
            top_k=top_k_pitfall + len(success_ids) * 2,
            outcome_filter="pitfall",
            min_similarity=min_similarity,
            query_vector=shared_vector,
            include_archived=include_archived,
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

    def get_unarchived_episodes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch unarchived episodes for consolidation and clustering."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT episode_id, task_goal, outcome, critic_score, timestamp,
                       summary, pitfalls_avoided, lessons_learned, key_actions,
                       embedding_blob, full_record_json, is_canonical, is_archived,
                       cluster_id, superseded_by, consolidation_score
                FROM episodes
                WHERE COALESCE(is_archived, 0) = 0
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def archive_episodes(
        self,
        episode_ids: List[str],
        cluster_id: str,
        superseded_by: str,
    ) -> int:
        """Mark duplicate/superseded member episodes as archived in an atomic transaction."""
        if not episode_ids:
            return 0
        with self._lock:
            placeholders = ",".join("?" for _ in episode_ids)
            cursor = self._conn.execute(
                f"""
                UPDATE episodes
                SET is_archived = 1,
                    cluster_id = ?,
                    superseded_by = ?
                WHERE episode_id IN ({placeholders})
                """,
                [cluster_id, superseded_by] + list(episode_ids),
            )
            self._conn.commit()
            return cursor.rowcount

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

    def update_episode_reflection(
        self,
        episode_id: str,
        reflection: EpisodeReflection | ReflectionEvaluation | Dict[str, Any],
        critic_score: Optional[float] = None,
        goal_fulfilled: Optional[bool] = None,
    ) -> bool:
        """Update reflection, critic score, and outcome of an already-indexed episode."""
        if not episode_id:
            return False

        record = self.get_episode_record(episode_id)
        if not record:
            logger.warning("Episode %s not found in episodic store to update reflection.", episode_id)
            return False

        # Normalize reflection
        if isinstance(reflection, EpisodeReflection):
            rec_reflection = reflection
        elif hasattr(reflection, "summary"):
            rec_reflection = EpisodeReflection(
                summary=reflection.summary or "",
                pitfalls_avoided=getattr(reflection, "pitfalls_avoided", "") or "",
                lessons_learned=getattr(reflection, "lessons_learned", "") or "",
                goal_fulfilled=getattr(reflection, "goal_fulfilled", goal_fulfilled),
                recommended_rules=getattr(reflection, "recommended_rules", []) or [],
            )
        elif isinstance(reflection, dict):
            rec_reflection = EpisodeReflection(**reflection)
        else:
            rec_reflection = EpisodeReflection()

        record.reflection = rec_reflection

        # Resolve goal fulfillment
        resolved_goal_fulfilled = goal_fulfilled
        if resolved_goal_fulfilled is None and hasattr(rec_reflection, "goal_fulfilled"):
            resolved_goal_fulfilled = rec_reflection.goal_fulfilled

        if resolved_goal_fulfilled is False and record.metadata.outcome == "success":
            record.metadata.outcome = "failed_unverified"

        # Resolve critic score
        if critic_score is not None:
            record.metadata.critic_score = max(0.0, min(1.0, float(critic_score)))
        elif hasattr(reflection, "critic_score") and getattr(reflection, "critic_score") is not None:
            record.metadata.critic_score = max(0.0, min(1.0, float(reflection.critic_score)))

        return self.index_episode(record, overwrite=True)

    def delete_episode(self, episode_id: str) -> bool:
        """Delete an episode; episode_entities rows cascade, then prune orphan graph nodes."""
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            cur = self._conn.execute("DELETE FROM episodes WHERE episode_id = ?", (episode_id,))
            if self._has_sqlite_vec:
                try:
                    self._conn.execute("DELETE FROM vec_episodes WHERE episode_id = ?", (episode_id,))
                except Exception:
                    pass
            self._conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            try:
                self.graph_store.prune_orphan_entities()
            except Exception as e:
                logger.warning("Graph prune after delete_episode failed: %s", e)
        return deleted

    def backfill_entities(self) -> int:
        """Link entities for episodes that have not yet had an entity-index pass.

        Uses ``full_record_json`` when present; otherwise builds a minimal ``EpisodeRecord``
        from stored episode columns (trajectory may be empty). Episodes with zero extracted
        entities are still marked attempted so backfill does not loop forever.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT episode_id, full_record_json, timestamp, task_goal, outcome, critic_score,
                       COALESCE(entity_link_attempted, 0) AS link_attempted
                FROM episodes
                WHERE COALESCE(entity_link_attempted, 0) = 0
                   OR (
                        COALESCE(entity_link_attempted, 0) = 1
                        AND NOT EXISTS (
                            SELECT 1 FROM episode_entities ee
                            WHERE ee.episode_id = episodes.episode_id
                        )
                   )
                """
            )
            rows = cursor.fetchall()

        backfilled = 0
        for r in rows:
            ep_id = r[0]
            record_json = r[1]
            ts = r[2] or ""
            link_attempted = int(r[6] or 0)
            try:
                if record_json:
                    rec = EpisodeRecord(**json.loads(record_json))
                else:
                    rec = EpisodeRecord(
                        episode_id=ep_id,
                        task_goal=r[3] or "",
                        timestamp=ts,
                        metadata=EpisodeMetadata(
                            outcome=r[4] or "unknown",
                            critic_score=r[5],
                        ),
                    )
                entities = self.entity_extractor.extract_from_episode(rec)
                if not entities:
                    if link_attempted == 0:
                        self.graph_store.delete_episode_links(ep_id)
                        self._set_entity_link_attempted(ep_id)
                    continue
                self._link_episode_entities_from_record(rec)
                self._set_entity_link_attempted(ep_id)
                backfilled += 1
            except Exception as e:
                logger.warning("Failed to backfill entities for episode %s: %s", ep_id, e)

        return backfilled

    def count(self) -> int:
        """Return total number of indexed episodes in the store."""
        with self._lock:
            row = self._conn.execute("SELECT count(*) FROM episodes").fetchone()
            return row[0] if row else 0

    def clear(self) -> None:
        """Purge episodes, vectors, entity catalog, and co-occurrence edges."""
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("DELETE FROM episodes")
            self._conn.execute("DELETE FROM entities")
            self._conn.execute("DELETE FROM entity_cooccurrences")
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
