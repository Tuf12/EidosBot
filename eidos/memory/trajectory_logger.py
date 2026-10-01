"""Append-only structured trajectory logger for agent learning & LoRA training (~/.eidos/trajectories/episodes.jsonl)."""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from eidos.memory.types import (
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    StepRecord,
)

logger = logging.getLogger(__name__)

DEFAULT_TRAJECTORIES_PATH = Path.home() / ".eidos" / "trajectories" / "episodes.jsonl"


class TrajectoryLogger:
    """Records multi-step agent trajectories with thoughts, tool calls, and outcomes."""

    def __init__(self, storage_path: Optional[str | Path] = None):
        self.path = Path(os.path.expanduser(str(storage_path or DEFAULT_TRAJECTORIES_PATH)))
        self._lock = threading.Lock()
        self._active_episodes: Dict[str, Dict[str, Any]] = {}
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Create storage directories if necessary."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self.path.touch(exist_ok=True)
        except Exception as e:
            logger.error("Failed to create trajectory storage at %s: %s", self.path, e)

    def start_episode(self, task_goal: str, model_name: str = "") -> str:
        """Initialize an active in-memory episode tracking session."""
        now_dt = datetime.now(timezone.utc)
        ts_str = now_dt.strftime("%Y%m%d_%H%M%S")
        rand_id = uuid.uuid4().hex[:6]
        episode_id = f"ep_{ts_str}_{rand_id}"

        with self._lock:
            self._active_episodes[episode_id] = {
                "episode_id": episode_id,
                "timestamp": now_dt.isoformat(),
                "model": model_name,
                "task_goal": task_goal,
                "start_time_monotonic": time.monotonic(),
                "start_time_iso": now_dt.isoformat(),
                "steps": [],
            }

        logger.debug("Started trajectory tracking for episode %s", episode_id)
        return episode_id

    def log_step(self, episode_id: str, step_record: StepRecord) -> None:
        """Append an individual step to the in-memory trajectory for an episode."""
        with self._lock:
            episode = self._active_episodes.get(episode_id)
            if not episode:
                logger.warning("Attempted to log step to unknown or finished episode: %s", episode_id)
                return
            episode["steps"].append(step_record)

    def finish_episode(
        self,
        episode_id: str,
        outcome: str = "success",
        final_summary: str = "",
        user_intervened: bool = False,
        critic_score: Optional[float] = None,
        reflection: Optional[EpisodeReflection] = None,
    ) -> Optional[EpisodeRecord]:
        """Finalize the episode, calculate duration, and append to JSONL disk storage."""
        with self._lock:
            episode_data = self._active_episodes.pop(episode_id, None)

        if not episode_data:
            logger.warning("Episode %s not found in active tracking list.", episode_id)
            return None

        end_dt = datetime.now(timezone.utc)
        duration = max(0.0, time.monotonic() - episode_data["start_time_monotonic"])
        steps: List[StepRecord] = episode_data["steps"]

        # Default critic score heuristic if not provided
        if critic_score is None:
            if outcome == "success":
                critic_score = 1.0
            elif outcome in ("failed", "aborted"):
                critic_score = 0.0
            else:
                critic_score = 0.5

        metadata = EpisodeMetadata(
            outcome=outcome,
            total_steps=len(steps),
            user_intervened=user_intervened,
            critic_score=critic_score,
            start_time=episode_data["start_time_iso"],
            end_time=end_dt.isoformat(),
            duration_seconds=round(duration, 3),
        )

        record = EpisodeRecord(
            episode_id=episode_id,
            timestamp=episode_data["timestamp"],
            model=episode_data["model"],
            task_goal=episode_data["task_goal"],
            metadata=metadata,
            trajectory=steps,
            reflection=reflection,
        )

        self._append_to_disk(record)
        return record

    def _append_to_disk(self, record: EpisodeRecord) -> None:
        """Thread-safe append of a completed episode record to JSONL file."""
        try:
            self._ensure_storage()
            line = record.model_dump_json() + "\n"
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            logger.info(
                "Persisted episode %s (outcome=%s, steps=%d) to %s",
                record.episode_id,
                record.metadata.outcome,
                record.metadata.total_steps,
                self.path,
            )
        except Exception as e:
            logger.error("Failed to append episode %s to trajectory storage: %s", record.episode_id, e)

    def get_recent_episodes(self, limit: int = 10) -> List[EpisodeRecord]:
        """Read the most recent N completed episodes from disk."""
        if not self.path.exists():
            return []

        records: List[EpisodeRecord] = []
        try:
            with self._lock:
                with open(self.path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]

            for line in reversed(lines[-limit:]):
                try:
                    data = json.loads(line)
                    records.append(EpisodeRecord(**data))
                except Exception as parse_err:
                    logger.warning("Could not parse trajectory line: %s", parse_err)
        except Exception as e:
            logger.error("Failed to read recent episodes from %s: %s", self.path, e)

        return records

    def count_episodes(self) -> int:
        """Count total completed episodes saved in storage."""
        if not self.path.exists():
            return 0
        try:
            with self._lock:
                with open(self.path, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
        except Exception:
            return 0
