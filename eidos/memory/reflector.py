"""Autonomous Post-Task Episodic Reflector & Critic (Tier A/B wisdom extractor)."""

import json
import logging
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import (
    EpisodeRecord,
    EpisodeReflection,
    ReflectionEvaluation,
)
from eidos.models.base import BaseModelClient, ModelResponse

logger = logging.getLogger(__name__)

EVALUATION_SYSTEM_PROMPT = """You are an expert autonomous agent supervisor and trajectory evaluator.
Your role is to critically analyze an agent execution trajectory against the user's initial goal and determine whether the task truly succeeded, evaluate execution efficiency, and extract reusable lessons.

EVALUATION RUBRIC:
1. Goal Fulfillment (0.0 - 0.5):
   - Did the tool outputs confirm the goal was achieved?
   - If the agent claimed success but tools errored out or did not produce results, mark goal_fulfilled = false.
2. Step Efficiency (0.0 - 0.3):
   - Did the agent use deterministic commands first?
   - Did it make circular or redundant calls?
   - Subtract 0.1 for every 2 unnecessary exploratory failures.
3. Safety & Hygiene (0.0 - 0.2):
   - Were files safely inspected before modification?
   - Were error outputs handled gracefully?

Respond strictly in valid JSON matching this schema:
{
  "goal_fulfilled": true,
  "critic_score": 0.90,
  "summary": "Concise 1-2 sentence synopsis of what was accomplished.",
  "pitfalls_avoided": "Specific traps or dead ends encountered or avoided.",
  "lessons_learned": "Reusable procedural heuristic for future sessions.",
  "recommended_rules": []
}"""


class EpisodicReflector:
    """Asynchronous post-task trajectory evaluator and critic."""

    def __init__(
        self,
        model_client: Optional[BaseModelClient] = None,
        episodic_store: Optional[EpisodicStore] = None,
        trajectory_logger: Optional[TrajectoryLogger] = None,
        on_background_activity: Optional[Callable[[bool, str, str], None]] = None,
        enabled: bool = True,
        async_workers: int = 1,
        max_queue_size: int = 16,
        model_name: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ):
        self.model_client = model_client
        self.episodic_store = episodic_store
        self.trajectory_logger = trajectory_logger
        self.on_background_activity = on_background_activity
        self.enabled = enabled
        self.async_workers = max(1, async_workers)
        self.max_queue_size = max_queue_size
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

        self._queue: queue.Queue[EpisodeRecord] = queue.Queue(maxsize=self.max_queue_size)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._is_evaluating = threading.Event()

        if self.enabled:
            self.start()

    def start(self) -> None:
        """Start the background worker thread for asynchronous trajectory reflection."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="EpisodicReflectorWorker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.debug("EpisodicReflector background worker started.")

    def stop(self, timeout: float = 2.0) -> None:
        """Signal background worker to stop and wait for completion."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        self._worker_thread = None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until all queued reflection tasks are completed."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._queue.empty() and not self._is_evaluating.is_set():
                return True
            time.sleep(0.05)
        return self._queue.empty() and not self._is_evaluating.is_set()

    def enqueue(self, record: EpisodeRecord) -> bool:
        """Enqueue an episode record for asynchronous background reflection."""
        if not self.enabled or not record or not record.episode_id:
            return False

        try:
            self._queue.put_nowait(record)
            logger.info("Enqueued episode %s for post-task reflection.", record.episode_id)
            return True
        except queue.Full:
            logger.warning(
                "Reflector queue is full (%d items). Running synchronous fallback for %s.",
                self.max_queue_size,
                record.episode_id,
            )
            # Evaluate with fallback heuristic without blocking the queue
            evaluation = self._fallback_heuristic_eval(record)
            self._apply_reflection(record, evaluation)
            return False

    def reflect_episode(
        self,
        record: EpisodeRecord,
        sync: bool = False,
    ) -> Optional[ReflectionEvaluation]:
        """Evaluate an episode trajectory either synchronously or via the background queue."""
        if not record or not record.episode_id:
            return None

        if sync or not self.enabled:
            evaluation = self._evaluate_record(record)
            self._apply_reflection(record, evaluation)
            return evaluation

        self.enqueue(record)
        return None

    def _worker_loop(self) -> None:
        """Continuous background worker processing enqueued episodes."""
        while not self._stop_event.is_set():
            try:
                record = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._is_evaluating.set()
            task_desc = f"Episode {record.episode_id}"
            details_desc = f"Analyzing: {record.task_goal[:35]}"

            if self.on_background_activity:
                try:
                    self.on_background_activity(True, "Episodic Reflection", details_desc)
                except Exception as cb_err:
                    logger.debug("Error notifying background activity start: %s", cb_err)

            try:
                evaluation = self._evaluate_record(record)
                self._apply_reflection(record, evaluation)
            except Exception as exc:
                logger.error("Unhandled error evaluating episode %s: %s", record.episode_id, exc, exc_info=True)
                fallback = self._fallback_heuristic_eval(record)
                self._apply_reflection(record, fallback)
            finally:
                self._queue.task_done()
                self._is_evaluating.clear()
                if self.on_background_activity:
                    try:
                        self.on_background_activity(False, "Episodic Reflection", "")
                    except Exception as cb_err:
                        logger.debug("Error notifying background activity stop: %s", cb_err)

            # Polite sleep between background tasks to prevent thermal and CPU saturation
            time.sleep(0.1)

    def _evaluate_record(self, record: EpisodeRecord) -> ReflectionEvaluation:
        """Evaluate a single record, trying LLM first, falling back to heuristic."""
        if self.model_client is not None:
            try:
                llm_eval = self._evaluate_with_llm(record)
                if llm_eval is not None:
                    return llm_eval
            except Exception as e:
                logger.warning(
                    "LLM reflection failed for episode %s: %s; falling back to heuristic.",
                    record.episode_id,
                    e,
                )

        return self._fallback_heuristic_eval(record)

    def _format_trajectory_for_eval(
        self,
        record: EpisodeRecord,
        max_step_obs_chars: int = 350,
        max_thinking_chars: int = 150,
        max_total_chars: int = 10000,
    ) -> str:
        """Format trajectory steps into a compact, low-token summary for LLM critique."""
        lines: List[str] = []
        for step in record.trajectory:
            s_num = step.step
            thinking = (step.model_thinking or "").strip()
            if thinking:
                short_thinking = thinking[:max_thinking_chars] + ("..." if len(thinking) > max_thinking_chars else "")
                lines.append(f"Step {s_num} Thinking: {short_thinking}")

            tool_calls = step.tool_calls or []
            tool_obs = step.tool_observations or []

            for idx, tc in enumerate(tool_calls):
                t_name = tc.get("name", "action")
                t_args = tc.get("arguments", {})
                args_str = json.dumps(t_args) if isinstance(t_args, dict) else str(t_args)
                if len(args_str) > 120:
                    args_str = args_str[:117] + "..."
                lines.append(f"  Action: {t_name}({args_str})")

                obs = tool_obs[idx] if idx < len(tool_obs) else {}
                if isinstance(obs, dict):
                    exit_code = obs.get("exit_code")
                    err = obs.get("error") or obs.get("stderr") or ""
                    out = obs.get("stdout") or obs.get("result") or obs.get("summary") or ""
                    parts = []
                    if exit_code is not None:
                        parts.append(f"exit_code={exit_code}")
                    if err:
                        err_str = str(err).strip()
                        if len(err_str) > max_step_obs_chars:
                            err_str = err_str[:max_step_obs_chars] + "..."
                        parts.append(f"error={err_str}")
                    if out:
                        out_str = str(out).strip()
                        if len(out_str) > max_step_obs_chars:
                            out_str = out_str[:max_step_obs_chars] + "..."
                        parts.append(f"output={out_str}")
                    obs_summary = ", ".join(parts) if parts else "completed"
                else:
                    obs_summary = str(obs)[:max_step_obs_chars]

                lines.append(f"  Observation: {obs_summary}")

        formatted = "\n".join(lines)
        if len(formatted) > max_total_chars:
            formatted = formatted[:max_total_chars] + "\n... [Remaining steps truncated for length]"
        return formatted

    def _evaluate_with_llm(self, record: EpisodeRecord) -> Optional[ReflectionEvaluation]:
        """Perform evaluation by prompting the model client and parsing JSON output."""
        if not self.model_client:
            return None

        trajectory_trace = self._format_trajectory_for_eval(record)
        user_prompt = (
            f"INPUT DATA:\n"
            f"- Task Goal: {record.task_goal}\n"
            f"- Declared Outcome: {record.metadata.outcome}\n"
            f"- Total Steps: {record.metadata.total_steps}\n"
            f"- Trajectory Trace:\n{trajectory_trace}\n\n"
            f"Critique this execution strictly in JSON format matching the schema."
        )

        messages = [
            {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        resp: ModelResponse = self.model_client.chat(
            messages=messages,
            tools=None,
            model_override=self.model_name,
        )

        raw_content = (resp.content or "").strip()
        if not raw_content:
            logger.debug("Model returned empty content during reflection for %s", record.episode_id)
            return None

        return self._parse_llm_json(raw_content)

    def _parse_llm_json(self, content: str) -> Optional[ReflectionEvaluation]:
        """Extract and validate JSON block into ReflectionEvaluation."""
        cleaned = content.strip()
        # Strip markdown fences if present
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if json_match:
            cleaned = json_match.group(1).strip()
        else:
            # Look for outermost braces
            brace_match = re.search(r"(\{[\s\S]*\})", cleaned)
            if brace_match:
                cleaned = brace_match.group(1).strip()

        try:
            data = json.loads(cleaned)
            return ReflectionEvaluation(
                goal_fulfilled=bool(data.get("goal_fulfilled", True)),
                critic_score=max(0.0, min(1.0, float(data.get("critic_score", 0.5)))),
                summary=str(data.get("summary", "")).strip(),
                pitfalls_avoided=str(data.get("pitfalls_avoided", "")).strip(),
                lessons_learned=str(data.get("lessons_learned", "")).strip(),
                recommended_rules=[str(r).strip() for r in data.get("recommended_rules", []) if str(r).strip()],
            )
        except Exception as e:
            logger.debug("Failed to parse reflection JSON: %s (Raw content: %s)", e, content[:150])
            return None

    def _fallback_heuristic_eval(self, record: EpisodeRecord) -> ReflectionEvaluation:
        """Deterministic heuristic fallback when LLM is unavailable or unparseable."""
        outcome = record.metadata.outcome.lower()
        total_steps = record.metadata.total_steps or len(record.trajectory)

        failed_actions: List[str] = []
        successful_actions: List[str] = []
        final_step_succeeded = False

        for step in record.trajectory:
            tool_calls = step.tool_calls or []
            tool_obs = step.tool_observations or []
            for i, tc in enumerate(tool_calls):
                name = tc.get("name", "")
                obs = tool_obs[i] if i < len(tool_obs) else {}

                is_fail = False
                if isinstance(obs, dict):
                    if obs.get("exit_code") not in (None, 0) or obs.get("success") is False or obs.get("denied") is True:
                        is_fail = True
                elif obs is False:
                    is_fail = True

                if is_fail:
                    failed_actions.append(name)
                elif name:
                    successful_actions.append(name)

        if record.trajectory:
            last_step = record.trajectory[-1]
            last_calls = last_step.tool_calls or []
            last_obs = last_step.tool_observations or []
            if any(tc.get("name") == "finish_task" for tc in last_calls):
                final_step_succeeded = True
            elif last_obs and isinstance(last_obs[-1], dict) and last_obs[-1].get("exit_code") in (None, 0):
                final_step_succeeded = True

        # Goal fulfillment determination
        if outcome == "success" and (final_step_succeeded or not failed_actions):
            goal_fulfilled = True
        elif outcome in ("failed", "aborted", "max_steps"):
            goal_fulfilled = False
        else:
            goal_fulfilled = final_step_succeeded

        # Critic score calculation
        if not goal_fulfilled:
            critic_score = 0.0 if outcome in ("failed", "aborted") else 0.3
        else:
            base_score = 1.0
            # Penalize for exploratory failures / dead-ends
            if failed_actions:
                base_score -= min(0.3, len(failed_actions) * 0.1)
            # Penalize if took too many steps
            if total_steps > 8:
                base_score -= 0.1
            critic_score = max(0.4, round(base_score, 2))

        # Resolution summary & lessons
        orig_refl = record.reflection
        summary = ""
        pitfalls = ""
        lessons = ""

        if orig_refl:
            summary = orig_refl.summary
            pitfalls = (
                ""
                if (orig_refl.pitfalls_avoided or "").strip().lower() in ("none", "")
                else orig_refl.pitfalls_avoided
            )
            lessons = (
                ""
                if (orig_refl.lessons_learned or "").strip().lower() in ("none", "")
                else orig_refl.lessons_learned
            )

        if not summary:
            summary = f"Executed {record.task_goal} ({'succeeded' if goal_fulfilled else 'ended with issues'})."

        if failed_actions and not pitfalls:
            unique_fails = list(dict.fromkeys(failed_actions))
            pitfalls = f"Encountered temporary errors with: {', '.join(unique_fails[:3])}"

        if not lessons:
            if goal_fulfilled:
                lessons = "Prefer deterministic single-pass tool invocation."
            else:
                lessons = "Check prerequisite files and arguments before executing bulk operations."

        return ReflectionEvaluation(
            goal_fulfilled=goal_fulfilled,
            critic_score=critic_score,
            summary=summary,
            pitfalls_avoided=pitfalls,
            lessons_learned=lessons,
            recommended_rules=[],
        )

    def _apply_reflection(
        self,
        record: EpisodeRecord,
        evaluation: ReflectionEvaluation,
    ) -> None:
        """Persist evaluation results to trajectory JSONL file and episodic vector store."""
        target_id = record.episode_id

        # Update in-memory record
        record.reflection = EpisodeReflection(
            summary=evaluation.summary,
            pitfalls_avoided=evaluation.pitfalls_avoided,
            lessons_learned=evaluation.lessons_learned,
            goal_fulfilled=evaluation.goal_fulfilled,
            recommended_rules=evaluation.recommended_rules,
        )
        record.metadata.critic_score = evaluation.critic_score
        if evaluation.goal_fulfilled is False and record.metadata.outcome == "success":
            record.metadata.outcome = "failed_unverified"

        # 1. Update Trajectory Logger (disk JSONL)
        if self.trajectory_logger:
            try:
                self.trajectory_logger.update_episode(record)
            except Exception as e:
                logger.warning("Failed to update episode %s in trajectory logger: %s", target_id, e)

        # 2. Update Episodic Store (relational & vector DB)
        if self.episodic_store:
            try:
                self.episodic_store.update_episode_reflection(
                    episode_id=target_id,
                    reflection=record.reflection,
                    critic_score=evaluation.critic_score,
                    goal_fulfilled=evaluation.goal_fulfilled,
                )
            except Exception as e:
                logger.warning("Failed to update episode %s in episodic store: %s", target_id, e)

        logger.info(
            "Completed reflection for episode %s: fulfilled=%s, critic_score=%.2f",
            target_id,
            evaluation.goal_fulfilled,
            evaluation.critic_score,
        )
