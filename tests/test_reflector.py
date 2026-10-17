"""Unit tests for EpisodicReflector and background trajectory critique."""

import json
from pathlib import Path
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.reflector import EpisodicReflector
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import (
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    ReflectionEvaluation,
    StepRecord,
)
from eidos.models.base import BaseModelClient, ModelResponse


def _sample_success_record(episode_id: str = "ep_test_succ") -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        task_goal="List active directory contents",
        metadata=EpisodeMetadata(outcome="success", critic_score=1.0, total_steps=2),
        trajectory=[
            StepRecord(
                step=1,
                model_thinking="List files with ls",
                tool_calls=[{"name": "execute_shell", "arguments": {"command": "ls -la"}}],
                tool_observations=[{"exit_code": 0, "stdout": "total 4\nfile1.txt"}],
            ),
            StepRecord(
                step=2,
                model_thinking="Finish the task",
                tool_calls=[{"name": "finish_task", "arguments": {"summary": "Listed files."}}],
                tool_observations=[{"status": "completed"}],
            ),
        ],
        reflection=EpisodeReflection(
            summary="Listed directory contents",
            pitfalls_avoided="None",
            lessons_learned="Direct ls was fast",
        ),
    )


def _sample_failed_record(episode_id: str = "ep_test_fail") -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        task_goal="Move files to archive",
        metadata=EpisodeMetadata(outcome="failed", critic_score=0.0, total_steps=1),
        trajectory=[
            StepRecord(
                step=1,
                model_thinking="Direct move",
                tool_calls=[{"name": "execute_shell", "arguments": {"command": "mv *.txt /nonexistent/"}}],
                tool_observations=[{"exit_code": 1, "stderr": "No such file or directory"}],
            )
        ],
        reflection=EpisodeReflection(
            summary="Move failed",
            pitfalls_avoided="None",
            lessons_learned="Need to verify destination directory",
        ),
    )


def test_heuristic_eval_success():
    reflector = EpisodicReflector(enabled=False)
    rec = _sample_success_record()
    evaluation = reflector._fallback_heuristic_eval(rec)

    assert isinstance(evaluation, ReflectionEvaluation)
    assert evaluation.goal_fulfilled is True
    assert evaluation.critic_score >= 0.9
    assert "Listed directory contents" in evaluation.summary


def test_heuristic_eval_failure():
    reflector = EpisodicReflector(enabled=False)
    rec = _sample_failed_record()
    evaluation = reflector._fallback_heuristic_eval(rec)

    assert isinstance(evaluation, ReflectionEvaluation)
    assert evaluation.goal_fulfilled is False
    assert evaluation.critic_score == 0.0
    assert "execute_shell" in evaluation.pitfalls_avoided


def test_llm_eval_parsing_success():
    mock_model = MagicMock(spec=BaseModelClient)
    mock_model.chat.return_value = ModelResponse(
        content=json.dumps({
            "goal_fulfilled": True,
            "critic_score": 0.95,
            "summary": "Clean execution of ls -la.",
            "pitfalls_avoided": "Avoided unnecessary UI clicks.",
            "lessons_learned": "execute_shell is fastest for directory inspection.",
            "recommended_rules": ["Always prefer ls over GUI file manager."],
        })
    )

    reflector = EpisodicReflector(model_client=mock_model, enabled=False)
    rec = _sample_success_record()
    eval_res = reflector._evaluate_with_llm(rec)

    assert eval_res is not None
    assert eval_res.goal_fulfilled is True
    assert eval_res.critic_score == 0.95
    assert "Clean execution" in eval_res.summary
    assert eval_res.recommended_rules == ["Always prefer ls over GUI file manager."]


def test_llm_eval_markdown_json_parsing():
    mock_model = MagicMock(spec=BaseModelClient)
    mock_model.chat.return_value = ModelResponse(
        content="""Here is my evaluation:
```json
{
  "goal_fulfilled": true,
  "critic_score": 0.88,
  "summary": "Task completed successfully with single retry.",
  "pitfalls_avoided": "Permission error on first pass.",
  "lessons_learned": "Run with appropriate permissions.",
  "recommended_rules": []
}
```
"""
    )

    reflector = EpisodicReflector(model_client=mock_model, enabled=False)
    rec = _sample_success_record()
    eval_res = reflector._evaluate_with_llm(rec)

    assert eval_res is not None
    assert eval_res.goal_fulfilled is True
    assert eval_res.critic_score == 0.88
    assert "Permission error" in eval_res.pitfalls_avoided


def test_llm_eval_malformed_json_fallback():
    mock_model = MagicMock(spec=BaseModelClient)
    mock_model.chat.return_value = ModelResponse(
        content="Sorry, I cannot produce JSON output today."
    )

    reflector = EpisodicReflector(model_client=mock_model, enabled=False)
    rec = _sample_success_record()
    # _evaluate_record should catch the None and fall back to heuristic
    eval_res = reflector._evaluate_record(rec)

    assert eval_res is not None
    assert eval_res.goal_fulfilled is True
    assert eval_res.critic_score >= 0.8


def test_async_background_reflection_pipeline():
    with tempfile.TemporaryDirectory() as td:
        traj_path = Path(td) / "episodes.jsonl"
        db_path = Path(td) / "episodic.db"

        traj_logger = TrajectoryLogger(storage_path=traj_path)
        store = EpisodicStore(db_path=db_path)

        rec = _sample_success_record("ep_async_test_1")
        # Save baseline to disk and store
        traj_logger._append_to_disk(rec)
        store.index_episode(rec)
        assert store.count() == 1

        bg_events = []

        def on_bg(active: bool, task: str, details: str):
            bg_events.append((active, task, details))

        mock_model = MagicMock(spec=BaseModelClient)
        mock_model.chat.return_value = ModelResponse(
            content=json.dumps({
                "goal_fulfilled": True,
                "critic_score": 0.94,
                "summary": "Critique confirmed task was completely satisfied.",
                "pitfalls_avoided": "None",
                "lessons_learned": "Fast deterministic command",
                "recommended_rules": [],
            })
        )

        reflector = EpisodicReflector(
            model_client=mock_model,
            episodic_store=store,
            trajectory_logger=traj_logger,
            on_background_activity=on_bg,
            enabled=True,
            timeout_seconds=5.0,
        )

        try:
            assert reflector.enqueue(rec) is True

            # Wait for background thread to process
            assert reflector.wait_idle(timeout=4.0) is True

            # Check that background activity notifications were received
            assert len(bg_events) >= 2
            assert any(ev[0] is True and ev[1] == "Episodic Reflection" for ev in bg_events)
            assert any(ev[0] is False and ev[1] == "Episodic Reflection" for ev in bg_events)

            # Check that trajectory JSONL was updated
            updated_traj = traj_logger.get_episode_by_id(rec.episode_id)
            assert updated_traj is not None
            assert updated_traj.metadata.critic_score == 0.94
            assert "Critique confirmed" in updated_traj.reflection.summary

            # Check that vector & relational store was updated
            updated_store_rec = store.get_episode_record(rec.episode_id)
            assert updated_store_rec is not None
            assert updated_store_rec.metadata.critic_score == 0.94
            assert "Critique confirmed" in updated_store_rec.reflection.summary

        finally:
            reflector.stop(timeout=1.0)
            store.close()


def test_agent_loop_wires_and_enqueues_reflector():
    from eidos.core.loop import AgentLoop
    from eidos.memory.user_store import UserStore
    from eidos.models.base import ToolCall

    with tempfile.TemporaryDirectory() as td:
        prof_file = Path(td) / "user_profile.yaml"
        traj_file = Path(td) / "episodes.jsonl"
        db_file = Path(td) / "episodic.db"

        user_store = UserStore(storage_path=prof_file)
        traj_logger = TrajectoryLogger(storage_path=traj_file)
        store = EpisodicStore(db_path=db_file)

        mock_eval_model = MagicMock(spec=BaseModelClient)
        mock_eval_model.chat.return_value = ModelResponse(
            content=json.dumps({
                "goal_fulfilled": True,
                "critic_score": 0.96,
                "summary": "AgentLoop run evaluated successfully.",
                "pitfalls_avoided": "None",
                "lessons_learned": "Clean single-step task",
                "recommended_rules": [],
            })
        )

        reflector = EpisodicReflector(
            model_client=mock_eval_model,
            episodic_store=store,
            trajectory_logger=traj_logger,
            enabled=True,
            timeout_seconds=5.0,
        )

        mock_agent_model = MagicMock(spec=BaseModelClient)
        mock_agent_model.reasoning_model = "test-reasoner"
        mock_agent_model.chat.return_value = ModelResponse(
            content="",
            thinking="Executing task finish",
            tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Agent task done."})],
        )

        mock_gk = MagicMock()
        mock_gk.evaluate.return_value = (True, "Allowed")

        loop = AgentLoop(
            config={"memory": {"enabled": True, "reflector": {"enabled": True}}},
            model_client=mock_agent_model,
            gatekeeper=mock_gk,
            user_store=user_store,
            trajectory_logger=traj_logger,
            episodic_store=store,
            reflector=reflector,
        )

        try:
            res = loop.run_task("Perform automated check")
            assert res["success"] is True
            assert res["summary"] == "Agent task done."

            # Verify that reflector received the episode and processed it
            assert reflector.wait_idle(timeout=4.0) is True

            episodes = traj_logger.get_recent_episodes(limit=1)
            assert len(episodes) == 1
            reflected_ep = episodes[0]
            assert reflected_ep.metadata.critic_score == 0.96
            assert "AgentLoop run evaluated" in reflected_ep.reflection.summary

            store_rec = store.get_episode_record(reflected_ep.episode_id)
            assert store_rec is not None
            assert store_rec.metadata.critic_score == 0.96

        finally:
            loop.shutdown()
            store.close()

