"""Embedded relational entity knowledge graph store using SQLite."""

from datetime import datetime, timezone
import json
import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from eidos.memory.types import ExtractedEntity

logger = logging.getLogger(__name__)


class GraphStore:
    """Embedded SQLite relational graph manager for episodic entities."""

    def __init__(self, conn: sqlite3.Connection, lock: Optional[Any] = None):
        self._conn = conn
        self._lock = lock if lock is not None else threading.RLock()

    def init_schema(self) -> None:
        """Create relational graph tables and indexes in episodic database."""
        with self._lock:
            # Ensure foreign key support is active
            self._conn.execute("PRAGMA foreign_keys = ON;")

            # 1. Master catalog of entities
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    metadata_json TEXT,
                    mention_count INTEGER DEFAULT 1,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name);"
            )

            # 2. Episode to Entity join table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episode_entities (
                    episode_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    frequency INTEGER DEFAULT 1,
                    PRIMARY KEY (episode_id, entity_id, role),
                    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_entities_entity ON episode_entities(entity_id);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_entities_episode ON episode_entities(episode_id);"
            )

            # 3. Entity Co-occurrence edges
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_cooccurrences (
                    entity_a TEXT NOT NULL,
                    entity_b TEXT NOT NULL,
                    weight INTEGER DEFAULT 1,
                    last_observed TEXT NOT NULL,
                    PRIMARY KEY (entity_a, entity_b),
                    FOREIGN KEY (entity_a) REFERENCES entities(entity_id) ON DELETE CASCADE,
                    FOREIGN KEY (entity_b) REFERENCES entities(entity_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cooccurrences_a ON entity_cooccurrences(entity_a);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cooccurrences_b ON entity_cooccurrences(entity_b);"
            )

            self._conn.commit()

    def link_episode_entities(
        self,
        episode_id: str,
        entities: List[ExtractedEntity],
        timestamp: str = "",
    ) -> None:
        """Persist entities, link to episode, and update co-occurrence edges."""
        if not episode_id or not entities:
            return

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        unique_entity_ids: Set[str] = set()

        with self._lock:
            try:
                self._conn.execute("PRAGMA foreign_keys = ON;")

                # 1. Upsert each entity in the entity catalog
                for entity in entities:
                    unique_entity_ids.add(entity.entity_id)
                    freq = entity.metadata.get("frequency", 1)
                    meta_json = json.dumps(entity.metadata) if entity.metadata else None

                    self._conn.execute(
                        """
                        INSERT INTO entities (
                            entity_id, entity_type, name, normalized_name,
                            metadata_json, mention_count, first_seen, last_seen
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(entity_id) DO UPDATE SET
                            mention_count = mention_count + excluded.mention_count,
                            last_seen = excluded.last_seen,
                            metadata_json = coalesce(excluded.metadata_json, metadata_json)
                        """,
                        (
                            entity.entity_id,
                            entity.entity_type,
                            entity.name,
                            entity.normalized_name,
                            meta_json,
                            freq,
                            ts,
                            ts,
                        ),
                    )

                    # 2. Upsert link into episode_entities
                    self._conn.execute(
                        """
                        INSERT INTO episode_entities (
                            episode_id, entity_id, role, frequency
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(episode_id, entity_id, role) DO UPDATE SET
                            frequency = excluded.frequency
                        """,
                        (
                            episode_id,
                            entity.entity_id,
                            entity.role or "associated",
                            freq,
                        ),
                    )

                # 3. Upsert pairwise co-occurrences
                sorted_ids = sorted(unique_entity_ids)
                for i in range(len(sorted_ids)):
                    for j in range(i + 1, len(sorted_ids)):
                        ea = sorted_ids[i]
                        eb = sorted_ids[j]
                        self._conn.execute(
                            """
                            INSERT INTO entity_cooccurrences (
                                entity_a, entity_b, weight, last_observed
                            ) VALUES (?, ?, 1, ?)
                            ON CONFLICT(entity_a, entity_b) DO UPDATE SET
                                weight = weight + 1,
                                last_observed = excluded.last_observed
                            """,
                            (ea, eb, ts),
                        )

                self._conn.commit()
                logger.debug(
                    "Linked %d entities for episode %s in graph store.", len(entities), episode_id
                )
            except Exception as e:
                logger.warning("Error linking entities for episode %s: %s", episode_id, e)
                try:
                    self._conn.rollback()
                except Exception:
                    pass

    def find_episodes_by_entity(
        self,
        entity_id_or_name: str,
        entity_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[str]:
        """Return episode IDs linked to a specific entity ID or normalized name."""
        cleaned = entity_id_or_name.strip().lower()
        if not cleaned:
            return []

        with self._lock:
            try:
                if entity_type:
                    cursor = self._conn.execute(
                        """
                        SELECT DISTINCT ee.episode_id
                        FROM episode_entities ee
                        JOIN entities e ON e.entity_id = ee.entity_id
                        WHERE (e.entity_id = ? OR e.normalized_name = ?)
                          AND e.entity_type = ?
                        LIMIT ?
                        """,
                        (cleaned, cleaned, entity_type, limit),
                    )
                else:
                    cursor = self._conn.execute(
                        """
                        SELECT DISTINCT ee.episode_id
                        FROM episode_entities ee
                        JOIN entities e ON e.entity_id = ee.entity_id
                        WHERE (e.entity_id = ? OR e.normalized_name = ?)
                        LIMIT ?
                        """,
                        (cleaned, cleaned, limit),
                    )
                return [row[0] for row in cursor.fetchall()]
            except Exception as e:
                logger.warning("Error finding episodes for entity '%s': %s", entity_id_or_name, e)
                return []

    def find_episodes_by_entities(
        self,
        entities: List[ExtractedEntity],
        limit: int = 50,
    ) -> Dict[str, Tuple[float, List[str]]]:
        """Compute entity overlap scores for matching episodes.

        Returns mapping: {episode_id: (overlap_ratio, [matched_entity_ids])}
        where overlap_ratio in (0.0, 1.0].
        """
        if not entities:
            return {}

        target_ids = {e.entity_id for e in entities}
        target_norm_names = {e.normalized_name for e in entities}
        num_targets = len(target_ids)
        if num_targets == 0:
            return {}

        placeholders = ",".join("?" for _ in target_ids)
        name_placeholders = ",".join("?" for _ in target_norm_names)
        params = list(target_ids) + list(target_norm_names)

        with self._lock:
            try:
                query = f"""
                SELECT ee.episode_id, ee.entity_id, e.normalized_name
                FROM episode_entities ee
                JOIN entities e ON e.entity_id = ee.entity_id
                WHERE ee.entity_id IN ({placeholders})
                   OR e.normalized_name IN ({name_placeholders})
                """
                cursor = self._conn.execute(query, params)
                rows = cursor.fetchall()
            except Exception as e:
                logger.warning("Error querying episode entity overlap: %s", e)
                return {}

        # Aggregate matched entities per episode
        episode_matches: Dict[str, Set[str]] = {}
        for row in rows:
            ep_id = row[0]
            ent_id = row[1]
            if ep_id not in episode_matches:
                episode_matches[ep_id] = set()
            episode_matches[ep_id].add(ent_id)

        results: Dict[str, Tuple[float, List[str]]] = {}
        for ep_id, matched in episode_matches.items():
            overlap_ratio = min(1.0, len(matched) / float(num_targets))
            results[ep_id] = (round(overlap_ratio, 4), sorted(list(matched)))

        return results

    def get_entities_for_episode(self, episode_id: str) -> List[ExtractedEntity]:
        """Fetch all linked entities for a specific episode."""
        if not episode_id:
            return []

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT e.entity_id, e.entity_type, e.name, e.normalized_name,
                           ee.role, ee.frequency, e.metadata_json
                    FROM episode_entities ee
                    JOIN entities e ON e.entity_id = ee.entity_id
                    WHERE ee.episode_id = ?
                    ORDER BY ee.frequency DESC
                    """,
                    (episode_id,),
                )
                rows = cursor.fetchall()
            except Exception as e:
                logger.warning("Error fetching entities for episode %s: %s", episode_id, e)
                return []

        entities: List[ExtractedEntity] = []
        for r in rows:
            meta = {}
            if r[6]:
                try:
                    meta = json.loads(r[6])
                except Exception:
                    pass
            meta["frequency"] = r[5]
            entities.append(
                ExtractedEntity(
                    entity_id=r[0],
                    entity_type=r[1],
                    name=r[2],
                    normalized_name=r[3],
                    role=r[4],
                    metadata=meta,
                )
            )
        return entities

    def get_related_entities(self, entity_id: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Find co-occurring entities for a given entity ordered by edge weight."""
        cleaned = entity_id.strip()
        if not cleaned:
            return []

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT
                        CASE WHEN entity_a = ? THEN entity_b ELSE entity_a END AS related_id,
                        weight, last_observed
                    FROM entity_cooccurrences
                    WHERE entity_a = ? OR entity_b = ?
                    ORDER BY weight DESC
                    LIMIT ?
                    """,
                    (cleaned, cleaned, cleaned, top_k),
                )
                rows = cursor.fetchall()
                return [
                    {"entity_id": r[0], "weight": r[1], "last_observed": r[2]}
                    for r in rows
                ]
            except Exception as e:
                logger.warning("Error fetching related entities for %s: %s", entity_id, e)
                return []

    def delete_episode_links(self, episode_id: str) -> bool:
        """Explicitly remove links for an episode from episode_entities."""
        if not episode_id:
            return False
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM episode_entities WHERE episode_id = ?", (episode_id,)
                )
                self._conn.commit()
                return cur.rowcount > 0
            except Exception as e:
                logger.warning("Error deleting entity links for episode %s: %s", episode_id, e)
                return False

    def count_entities(self) -> int:
        """Return total number of cataloged entities."""
        with self._lock:
            try:
                row = self._conn.execute("SELECT count(*) FROM entities").fetchone()
                return row[0] if row else 0
            except Exception:
                return 0

    def count_links(self) -> int:
        """Return total number of episode-entity link rows."""
        with self._lock:
            try:
                row = self._conn.execute("SELECT count(*) FROM episode_entities").fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
