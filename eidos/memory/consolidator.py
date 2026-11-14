"""Sleep-time episodic consolidation, generalization, and pruning engine."""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.types import (
    CandidateRule,
    ClusterSummary,
    ConsolidationReport,
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    StepRecord,
)
from eidos.memory.user_store import UserStore

logger = logging.getLogger(__name__)

# Heuristic patterns for detecting reusable procedural guidelines in lessons/pitfalls
PROCEDURAL_RULE_PATTERNS = [
    re.compile(r"\b(always\s+[a-z0-9_\-\s]+)", re.IGNORECASE),
    re.compile(r"\b(prefer\s+[a-z0-9_\-\s]+over\s+[a-z0-9_\-\s]+)", re.IGNORECASE),
    re.compile(r"\b(confirm before\s+[a-z0-9_\-\s]+)", re.IGNORECASE),
    re.compile(r"\b(never\s+[a-z0-9_\-\s]+)", re.IGNORECASE),
    re.compile(r"\b(ensure\s+[a-z0-9_\-\s]+before\s+[a-z0-9_\-\s]+)", re.IGNORECASE),
    re.compile(r"\b(use\s+--[a-z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"\b(mkdir\s+-p\b[a-z0-9_\-\s]*)", re.IGNORECASE),
]


def _clean_slug(name: str) -> str:
    """Generate a clean URL/filename-safe slug from a task goal or title."""
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean or "task-skill"


def _extract_unique_items(texts: List[str], max_items: int = 4) -> List[str]:
    """Split and deduplicate semicolon- or newline-separated pitfall/lesson strings."""
    seen: Set[str] = set()
    result: List[str] = []
    for raw in texts:
        if not raw or not raw.strip():
            continue
        parts = re.split(r"[;\n•]+", raw)
        for part in parts:
            item = part.strip()
            if not item:
                continue
            norm = item.lower()
            if norm not in seen:
                seen.add(norm)
                result.append(item)
                if len(result) >= max_items:
                    return result
    return result


class EpisodicConsolidator:
    """Offline background consolidation engine for episodic trajectories."""

    def __init__(
        self,
        episodic_store: EpisodicStore,
        user_store: Optional[UserStore] = None,
        agent_loop: Optional[Any] = None,
        skill_manager: Optional[Any] = None,
        similarity_threshold: float = 0.85,
        min_cluster_size: int = 2,
        max_episodes_per_run: int = 100,
        max_runtime_seconds: float = 30.0,
        propose_rules: bool = True,
        propose_skills: bool = True,
        drafts_dir: Optional[str | Path] = None,
        on_activity: Optional[Callable[[bool, str, str], None]] = None,
    ):
        self.episodic_store = episodic_store
        self.user_store = user_store
        self.agent_loop = agent_loop
        self.skill_manager = skill_manager
        self.similarity_threshold = float(similarity_threshold)
        self.min_cluster_size = max(2, int(min_cluster_size))
        self.max_episodes_per_run = max(1, int(max_episodes_per_run))
        self.max_runtime_seconds = float(max_runtime_seconds)
        self.propose_rules = propose_rules
        self.propose_skills = propose_skills
        self.drafts_dir = Path(drafts_dir) if drafts_dir else None
        self.on_activity = on_activity
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal consolidation to halt at the next safe boundary."""
        self._stop_event.set()

    def should_interrupt(self, start_time: float) -> bool:
        """Check if consolidation should yield to foreground user activity or budget limits."""
        if self._stop_event.is_set():
            return True
        if (time.monotonic() - start_time) > self.max_runtime_seconds:
            return True
        if self.agent_loop is not None:
            # Check if agent loop is actively busy with a foreground user task
            if hasattr(self.agent_loop, "is_busy") and callable(self.agent_loop.is_busy):
                if self.agent_loop.is_busy():
                    return True
            elif getattr(self.agent_loop, "_task_active", False):
                return True
        return False

    def cluster_episodes(
        self,
        episodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group unarchived episodes into semantic clusters via pairwise cosine similarity.

        Returns:
            Dict mapping cluster_id -> list of episode dictionaries in that cluster.
        """
        if episodes is None:
            episodes = self.episodic_store.get_unarchived_episodes(limit=self.max_episodes_per_run)

        if not episodes:
            return {}

        candidates: List[Dict[str, Any]] = []
        vectors: List[np.ndarray] = []

        for ep in episodes:
            blob = ep.get("embedding_blob")
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm == 0 or np.isnan(norm):
                continue
            candidates.append(ep)
            vectors.append(vec / norm)

        if len(candidates) < self.min_cluster_size:
            return {}

        matrix_v = np.vstack(vectors)  # M x D
        sim_matrix = np.clip(np.dot(matrix_v, matrix_v.T), -1.0, 1.0)  # M x M

        # Sort candidate indices by critic_score descending so high quality runs act as centroids
        def _get_score(cand: Dict[str, Any]) -> float:
            score = cand.get("critic_score")
            return float(score) if score is not None else 0.5

        sorted_indices = sorted(
            range(len(candidates)),
            key=lambda idx: _get_score(candidates[idx]),
            reverse=True,
        )

        unassigned = set(sorted_indices)
        clusters: Dict[str, List[Dict[str, Any]]] = {}

        for centroid_idx in sorted_indices:
            if centroid_idx not in unassigned:
                continue

            matching_indices = [
                j for j in unassigned
                if sim_matrix[centroid_idx, j] >= self.similarity_threshold
            ]

            if len(matching_indices) >= self.min_cluster_size:
                centroid_ep = candidates[centroid_idx]
                member_ids = sorted([candidates[j]["episode_id"] for j in matching_indices])
                hash_input = f"{centroid_ep.get('episode_id', '')}:" + ":".join(member_ids)
                cluster_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:12]
                cluster_id = f"clust_{cluster_hash}"

                clusters[cluster_id] = [candidates[j] for j in matching_indices]
                unassigned.difference_update(matching_indices)
            else:
                unassigned.remove(centroid_idx)

        return clusters

    def summarize_clusters(
        self,
        clusters: Dict[str, List[Dict[str, Any]]],
    ) -> List[ClusterSummary]:
        """Generate ClusterSummary models with average pairwise similarity metrics."""
        summaries: List[ClusterSummary] = []
        for cluster_id, members in clusters.items():
            if not members:
                continue

            # Compute average pairwise similarity between members
            vectors = []
            for m in members:
                blob = m.get("embedding_blob")
                if blob:
                    v = np.frombuffer(blob, dtype=np.float32)
                    norm = np.linalg.norm(v)
                    if norm > 0:
                        vectors.append(v / norm)

            avg_sim = 1.0
            if len(vectors) > 1:
                mat = np.vstack(vectors)
                sims = np.dot(mat, mat.T)
                # Average upper triangle excluding diagonal
                tri_indices = np.triu_indices(len(vectors), k=1)
                avg_sim = float(np.mean(sims[tri_indices]))

            best_member = max(
                members,
                key=lambda m: float(m.get("critic_score") or 0.0),
            )

            summaries.append(
                ClusterSummary(
                    cluster_id=cluster_id,
                    task_goal_representative=best_member.get("task_goal", ""),
                    member_episode_ids=[m["episode_id"] for m in members],
                    average_similarity=round(avg_sim, 4),
                )
            )

        return summaries

    def synthesize_canonical_episode(
        self,
        cluster_id: str,
        members: List[Dict[str, Any]],
    ) -> EpisodeRecord:
        """Synthesize a single canonical golden episode from multiple redundant cluster runs."""
        if not members:
            raise ValueError(f"Cluster {cluster_id} contains no member episodes to synthesize.")

        # 1. Representative Task Goal: Best critic score run
        def _member_priority(m: Dict[str, Any]) -> Tuple[int, float, int]:
            is_success = 1 if m.get("outcome") == "success" else 0
            score = float(m.get("critic_score") or 0.0)
            goal_len = len(m.get("task_goal") or "")
            return (is_success, score, goal_len)

        best_member = max(members, key=_member_priority)
        representative_goal = best_member.get("task_goal", "")

        # 2. Golden Path Trajectory Selection:
        # Prefer successful runs; within successes, choose shortest step count with 0 failed observations
        success_members = [m for m in members if m.get("outcome") == "success"]
        candidate_pool = success_members if success_members else members

        best_record: Optional[EpisodeRecord] = None
        best_trajectory: List[StepRecord] = []
        min_steps = float("inf")

        for m in candidate_pool:
            ep_id = m.get("episode_id", "")
            rec = self.episodic_store.get_episode_record(ep_id)
            if not rec and m.get("full_record_json"):
                try:
                    rec = EpisodeRecord(**json.loads(m["full_record_json"]))
                except Exception:
                    pass

            if rec:
                steps_count = len(rec.trajectory) if rec.trajectory else (rec.metadata.total_steps or 1)
                # Check for observation failures
                failed_obs_count = 0
                for st in rec.trajectory:
                    for obs in st.tool_observations:
                        if not self.episodic_store._is_tool_observation_successful(obs):
                            failed_obs_count += 1

                # Prioritize zero-failure runs and shorter steps
                if failed_obs_count == 0 and steps_count < min_steps:
                    min_steps = steps_count
                    best_record = rec
                    best_trajectory = rec.trajectory
                elif best_record is None:
                    best_record = rec
                    best_trajectory = rec.trajectory

        if not best_record:
            # Fallback construct if full trajectory is missing
            best_record = EpisodeRecord(
                episode_id=best_member.get("episode_id", f"ep_fallback_{int(time.time())}"),
                task_goal=representative_goal,
                metadata=EpisodeMetadata(
                    outcome=best_member.get("outcome", "success"),
                    critic_score=float(best_member.get("critic_score") or 0.8),
                ),
            )
            best_trajectory = []

        # 3. Pitfall & Dead-End Aggregation
        pitfalls_list: List[str] = []
        for m in members:
            p = m.get("pitfalls_avoided")
            if p:
                pitfalls_list.append(p)
        combined_pitfalls = "; ".join(_extract_unique_items(pitfalls_list, max_items=4))

        # 4. Lessons Learned Synthesis
        lessons_list: List[str] = []
        for m in members:
            ll = m.get("lessons_learned")
            if ll:
                lessons_list.append(ll)
        combined_lessons = ". ".join(_extract_unique_items(lessons_list, max_items=3))
        if combined_lessons and not combined_lessons.endswith("."):
            combined_lessons += "."

        # 5. Consolidated Critic Score Calibration
        # Score = min(1.0, mean(scores) + 0.10)
        member_scores = [float(m.get("critic_score") or 0.5) for m in members]
        mean_score = float(np.mean(member_scores))
        canon_score = min(1.0, mean_score + 0.10)

        # 6. Build Synthetic Canonical Episode Record
        canon_ts = int(time.time())
        short_clust = cluster_id.replace("clust_", "")[:8]
        canonical_id = f"ep_canon_{short_clust}_{canon_ts}"

        canonical_record = EpisodeRecord(
            episode_id=canonical_id,
            task_goal=representative_goal,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=best_record.model or "consolidator",
            metadata=EpisodeMetadata(
                outcome="success" if success_members else best_member.get("outcome", "unknown"),
                total_steps=len(best_trajectory),
                critic_score=round(canon_score, 3),
                is_canonical=True,
                is_archived=False,
                cluster_id=cluster_id,
                consolidation_score=round(canon_score, 3),
            ),
            trajectory=best_trajectory,
            reflection=EpisodeReflection(
                summary=f"Consolidated golden recipe for: {representative_goal}",
                pitfalls_avoided=combined_pitfalls,
                lessons_learned=combined_lessons,
                goal_fulfilled=True if success_members else False,
            ),
        )

        return canonical_record

    def detect_promotion_candidates(
        self,
        cluster_id: str,
        members: List[Dict[str, Any]],
        canonical_record: EpisodeRecord,
    ) -> Tuple[List[CandidateRule], List[str]]:
        """Identify high-frequency patterns suitable for user profile rules or living SKILL.md documents."""
        promoted_rules: List[CandidateRule] = []
        promoted_skills: List[str] = []

        if len(members) < 3:
            return promoted_rules, promoted_skills

        # 1. Tier B Candidate Rule Promotion
        if self.propose_rules and self.user_store:
            # Check lessons and pitfalls for explicit procedural guidance
            text_sources = [
                canonical_record.reflection.lessons_learned if canonical_record.reflection else "",
                canonical_record.reflection.pitfalls_avoided if canonical_record.reflection else "",
            ]
            for m in members:
                text_sources.append(m.get("lessons_learned") or "")
                text_sources.append(m.get("pitfalls_avoided") or "")

            found_rule_texts: Set[str] = set()
            for text in text_sources:
                if not text:
                    continue
                for pat in PROCEDURAL_RULE_PATTERNS:
                    match = pat.search(text)
                    if match:
                        rule_cand = match.group(0).strip().rstrip(";.,")
                        if 10 <= len(rule_cand) <= 150:
                            # Capitalize first character
                            formatted_rule = rule_cand[0].upper() + rule_cand[1:]
                            found_rule_texts.add(formatted_rule)

            for rule_text in found_rule_texts:
                staged = self.user_store.add_candidate_rule(
                    rule=rule_text,
                    source_cluster_id=cluster_id,
                    occurrence_count=len(members),
                )
                if staged:
                    promoted_rules.append(
                        CandidateRule(
                            rule=rule_text,
                            source_cluster_id=cluster_id,
                            occurrence_count=len(members),
                        )
                    )

        # 2. Skill Scaffolding Candidate Promotion
        if self.propose_skills:
            # Multi-step (>=3 steps/actions) executed >= 3 times successfully
            success_count = sum(1 for m in members if m.get("outcome") == "success")
            steps_count = len(canonical_record.trajectory)
            if success_count >= 3 and steps_count >= 2:
                slug = _clean_slug(canonical_record.task_goal)
                existing_names: Set[str] = set()
                if self.skill_manager and hasattr(self.skill_manager, "list_skills"):
                    try:
                        for s in self.skill_manager.list_skills():
                            existing_names.add(s.get("name", "").lower())
                    except Exception as se:
                        logger.debug("Failed to list existing skills: %s", se)

                if slug not in existing_names:
                    scaffolded = self._scaffold_draft_skill(slug, canonical_record)
                    if scaffolded:
                        promoted_skills.append(slug)

        return promoted_rules, promoted_skills

    def _scaffold_draft_skill(self, slug: str, canonical_record: EpisodeRecord) -> bool:
        """Create template SKILL.md under ~/.eidos/skills/drafts/<slug>/SKILL.md."""
        try:
            if self.drafts_dir:
                base_drafts_dir = Path(self.drafts_dir)
            elif self.skill_manager and hasattr(self.skill_manager, "skills_dir"):
                base_drafts_dir = Path(self.skill_manager.skills_dir) / "drafts"
            else:
                base_drafts_dir = Path.home() / ".eidos" / "skills" / "drafts"

            drafts_dir = base_drafts_dir / slug
            drafts_dir.mkdir(parents=True, exist_ok=True)
            skill_file = drafts_dir / "SKILL.md"
            if skill_file.exists():
                return False

            actions_str = ""
            for step in canonical_record.trajectory:
                for tc in step.tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name") or tc.get("name")
                    if name:
                        actions_str += f"- `{name}`\n"

            body = f"""---
name: {slug}
version: 0.1.0
description: "Draft procedure derived from consolidated task: {canonical_record.task_goal}"
author: "eidos-consolidator"
tags: [auto-generated, draft, memory-promoted]
status: draft
---

# Draft Procedure: {canonical_record.task_goal}

## 1. Objective
Automate and standardize execution of: {canonical_record.task_goal}.

## 2. Proven Actions
{actions_str or "Deterministic command sequences identified by episodic consolidation."}

## 3. Pitfalls to Avoid
{canonical_record.reflection.pitfalls_avoided if canonical_record.reflection and canonical_record.reflection.pitfalls_avoided else "None recorded."}

## 4. Key Lessons
{canonical_record.reflection.lessons_learned if canonical_record.reflection and canonical_record.reflection.lessons_learned else "None recorded."}
"""
            skill_file.write_text(body, encoding="utf-8")
            logger.info("Scaffolded draft living skill at %s", skill_file)
            return True
        except Exception as e:
            logger.warning("Failed to scaffold draft skill for %s: %s", slug, e)
            return False

    def run_consolidation(self, force: bool = False) -> ConsolidationReport:
        """Execute full consolidation cycle over unarchived episodes."""
        start_time = time.monotonic()
        report = ConsolidationReport()

        with self._lock:
            if not force and self.should_interrupt(start_time):
                report.interrupted = True
                report.duration_seconds = round(time.monotonic() - start_time, 3)
                return report

            if self.on_activity:
                try:
                    self.on_activity(True, "Episodic Consolidation", "Clustering redundant memories...")
                except Exception:
                    pass

            try:
                # 1. Fetch unarchived episodes
                unarchived = self.episodic_store.get_unarchived_episodes(
                    limit=self.max_episodes_per_run
                )
                report.episodes_evaluated = len(unarchived)

                if len(unarchived) < self.min_cluster_size:
                    report.duration_seconds = round(time.monotonic() - start_time, 3)
                    return report

                # 2. Identify semantic clusters
                clusters = self.cluster_episodes(unarchived)
                report.clusters_found = len(clusters)

                # 3. Process each cluster
                for cluster_id, members in clusters.items():
                    if self.should_interrupt(start_time):
                        logger.info("Consolidation yielding to foreground user activity or budget.")
                        report.interrupted = True
                        break

                    # Synthesize canonical golden recipe
                    canonical_record = self.synthesize_canonical_episode(cluster_id, members)

                    # Index canonical record
                    indexed = self.episodic_store.index_episode(canonical_record, overwrite=True)
                    if not indexed:
                        logger.warning("Failed to index canonical episode %s", canonical_record.episode_id)
                        continue

                    # Archive superseded members
                    member_ids = [m["episode_id"] for m in members]
                    archived_count = self.episodic_store.archive_episodes(
                        member_ids,
                        cluster_id=cluster_id,
                        superseded_by=canonical_record.episode_id,
                    )
                    report.episodes_archived += archived_count
                    report.canonical_synthesized += 1

                    # 4. Check for habit / skill promotion
                    promoted_rules, promoted_skills = self.detect_promotion_candidates(
                        cluster_id, members, canonical_record
                    )
                    report.promoted_rules_proposed.extend(promoted_rules)
                    report.promoted_skills_proposed.extend(promoted_skills)

            except Exception as e:
                logger.error("Error during episodic consolidation: %s", e, exc_info=True)
            finally:
                if self.on_activity:
                    try:
                        self.on_activity(False, "Episodic Consolidation", "Consolidation complete.")
                    except Exception:
                        pass

        report.duration_seconds = round(time.monotonic() - start_time, 3)
        logger.info(
            "Consolidation cycle completed in %.2fs: %d clusters, %d synthesized, %d archived, %d rules, %d skills (interrupted=%s)",
            report.duration_seconds,
            report.clusters_found,
            report.canonical_synthesized,
            report.episodes_archived,
            len(report.promoted_rules_proposed),
            len(report.promoted_skills_proposed),
            report.interrupted,
        )
        return report


class IdleConsolidationRunner:
    """Background daemon thread that periodically triggers consolidation when system is idle."""

    def __init__(
        self,
        consolidator: EpisodicConsolidator,
        idle_threshold_seconds: float = 1800.0,
        check_interval_seconds: float = 60.0,
        enabled: bool = True,
    ):
        self.consolidator = consolidator
        self.idle_threshold_seconds = float(idle_threshold_seconds)
        self.check_interval_seconds = float(check_interval_seconds)
        self.enabled = enabled
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background idle watcher thread."""
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="idle-episodic-consolidator",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Idle consolidation runner started (idle threshold: %.0fs, interval: %.0fs)",
            self.idle_threshold_seconds,
            self.check_interval_seconds,
        )

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background runner."""
        self._stop_event.set()
        self.consolidator.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run_loop(self) -> None:
        """Poll for idle periods and trigger consolidation."""
        while not self._stop_event.is_set():
            # Wait for interval or stop event
            if self._stop_event.wait(timeout=self.check_interval_seconds):
                break

            if not self.enabled:
                continue

            # Determine idle duration from agent loop
            is_idle = True
            loop = self.consolidator.agent_loop
            if loop is not None:
                if hasattr(loop, "is_busy") and callable(loop.is_busy) and loop.is_busy():
                    is_idle = False
                elif getattr(loop, "_task_active", False):
                    is_idle = False
                else:
                    last_task_time = getattr(loop, "_last_task_finished_time", None)
                    if last_task_time is not None:
                        elapsed = time.monotonic() - last_task_time
                        if elapsed < self.idle_threshold_seconds:
                            is_idle = False

            if is_idle:
                try:
                    self.consolidator.run_consolidation()
                except Exception as e:
                    logger.debug("Background idle consolidation encountered error: %s", e)
