"""Unit tests for EidosBot memory subsystem (UserProfile, UserStore, TrajectoryLogger)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from eidos.core.clock import EidosClock
from eidos.core.context import build_system_prompt
from eidos.core.loop import AgentLoop
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import (
    INTERACTION_STYLE_CHOICES,
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    StepRecord,
    UserProfile,
)
from eidos.memory.user_store import UserStore
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall


def test_interaction_style_choices_include_witty_personality():
    assert any("witty" in choice.lower() for choice in INTERACTION_STYLE_CHOICES)


def test_user_profile_defaults():
    profile = UserProfile()
    assert profile.user_identity.get("os")
    assert "interaction_style" in profile.preferences
    assert len(profile.workflow_rules) > 0
    assert profile.environment_facts == {}  # Clean slate by default

    prompt_text = profile.format_for_prompt()
    assert "USER PROFILE & CONVENTIONS:" in prompt_text
    assert "Workflow Guidelines:" in prompt_text
    assert "/home/" not in prompt_text


def test_user_store_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_file = Path(tmpdir) / "test_user_profile.yaml"
        store = UserStore(storage_path=storage_file)

        assert storage_file.exists()
        loaded = store.load()
        assert loaded.user_identity

        # Modify fact
        store.set_fact("preferences", "theme", "dracula_dark")
        reloaded = store.load(force_reload=True)
        assert reloaded.preferences.get("theme") == "dracula_dark"

        # Remove fact
        assert store.remove_fact("preferences", "theme") is True
        assert store.load(force_reload=True).preferences.get("theme") is None

        # Add and remove rule
        new_rule = "Never execute unverified sudo commands"
        store.add_rule(new_rule)
        assert new_rule in store.load(force_reload=True).workflow_rules

        assert store.remove_rule(new_rule) is True
        assert new_rule not in store.load(force_reload=True).workflow_rules


def test_build_system_prompt_with_user_profile():
    profile = UserProfile(
        preferences={"interaction_style": "Super concise test style"},
        workflow_rules=["Test rule 1"],
    )
    prompt = build_system_prompt(
        active_window_info={"title": "Test Window", "class_name": "test_class", "geometry": {}},
        current_dir="/tmp/test",
        clock=EidosClock(timezone_name="UTC"),
        user_profile_context=profile.format_for_prompt(),
    )

    assert "Super concise test style" in prompt
    assert "Test rule 1" in prompt
    assert "USER PROFILE & CONVENTIONS:" in prompt


def test_trajectory_logger_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "episodes.jsonl"
        logger = TrajectoryLogger(storage_path=log_file)

        assert logger.count_episodes() == 0

        # Start an episode
        ep_id = logger.start_episode(task_goal="Inspect directory files", model_name="test-model")
        assert ep_id.startswith("ep_")

        # Log a step
        step1 = StepRecord(
            step=1,
            state_snapshot={"active_window": "Terminal", "cwd": "/tmp"},
            model_thinking="I need to list files using execute_shell",
            model_content="",
            tool_calls=[{"name": "execute_shell", "arguments": {"command": "ls -la"}}],
            tool_observations=[{"exit_code": 0, "stdout": "total 0"}],
            duration_seconds=0.15,
        )
        logger.log_step(ep_id, step1)

        # Log completion step
        step2 = StepRecord(
            step=2,
            state_snapshot={"active_window": "Terminal", "cwd": "/tmp"},
            model_thinking="Task is done",
            model_content="Directory is empty.",
            tool_calls=[{"name": "finish_task", "arguments": {"summary": "Done."}}],
            tool_observations=[{"status": "completed"}],
            duration_seconds=0.08,
        )
        logger.log_step(ep_id, step2)

        # Finish episode
        reflection = EpisodeReflection(
            summary="Clean shell execution",
            pitfalls_avoided="Did not use UI clicks",
            lessons_learned="Shell execution was instant",
        )
        record = logger.finish_episode(
            episode_id=ep_id,
            outcome="success",
            final_summary="Finished listing files.",
            reflection=reflection,
        )

        assert record is not None
        assert record.episode_id == ep_id
        assert record.metadata.outcome == "success"
        assert record.metadata.total_steps == 2
        assert record.metadata.critic_score == 1.0
        assert record.reflection.summary == "Clean shell execution"
        assert logger.count_episodes() == 1

        # Check reading from disk
        recent = logger.get_recent_episodes(limit=5)
        assert len(recent) == 1
        assert recent[0].episode_id == ep_id
        assert len(recent[0].trajectory) == 2


def test_agent_loop_trajectory_recording():
    with tempfile.TemporaryDirectory() as tmpdir:
        prof_file = Path(tmpdir) / "user_profile.yaml"
        traj_file = Path(tmpdir) / "episodes.jsonl"

        user_store = UserStore(storage_path=prof_file)
        traj_logger = TrajectoryLogger(storage_path=traj_file)

        # Mock model that emits a tool call and finishes
        mock_model = MagicMock(spec=BaseModelClient)
        mock_model.reasoning_model = "test-reasoner"
        mock_model.chat.side_effect = [
            ModelResponse(
                content="",
                thinking="Step 1 thinking: call finish_task",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Task complete."})],
            )
        ]

        # Mock mock gatekeeper and windows to avoid OS dependencies in unit test
        mock_gk = MagicMock()
        mock_gk.evaluate.return_value = (True, "Allowed")

        loop = AgentLoop(
            config={"memory": {"enabled": True}},
            model_client=mock_model,
            gatekeeper=mock_gk,
            user_store=user_store,
            trajectory_logger=traj_logger,
        )

        res = loop.run_task("Test task for trajectory tracking")
        assert res["success"] is True
        assert res["summary"] == "Task complete."

        # Verify trajectory logger recorded the episode to disk
        assert traj_logger.count_episodes() == 1
        episodes = traj_logger.get_recent_episodes(limit=1)
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep.task_goal == "Test task for trajectory tracking"
        assert ep.metadata.outcome == "success"
        assert ep.metadata.total_steps == 1
        assert len(ep.trajectory) == 1
        assert ep.trajectory[0].model_thinking == "Step 1 thinking: call finish_task"
        assert len(ep.trajectory[0].tool_calls) == 1
        assert ep.trajectory[0].tool_calls[0]["name"] == "finish_task"


def test_onboarding_flag_and_prompt():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_file = Path(tmpdir) / "user_profile.yaml"
        store = UserStore(storage_path=storage_file)
        assert store.needs_onboarding() is True
        assert "PROFILE ONBOARDING" in store.get_prompt_context()

        store.mark_onboarding_complete()
        assert store.needs_onboarding() is False
        assert "PROFILE ONBOARDING" not in store.get_prompt_context()


def test_apply_onboarding_writes_facts():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_file = Path(tmpdir) / "user_profile.yaml"
        store = UserStore(storage_path=storage_file)
        store.apply_onboarding(
            display_name="alice",
            projects_dir="~/Projects",
            primary_editor="Neovim",
            mark_complete=True,
        )
        profile = store.load(force_reload=True)
        assert profile.onboarding_complete is True
        assert profile.user_identity["name"] == "alice"
        assert profile.environment_facts["projects_dir"] == os.path.expanduser("~/Projects")


def test_update_user_profile_tool_with_gatekeeper():
    with tempfile.TemporaryDirectory() as tmpdir:
        prof_file = Path(tmpdir) / "user_profile.yaml"
        traj_file = Path(tmpdir) / "episodes.jsonl"
        user_store = UserStore(storage_path=prof_file)
        traj_logger = TrajectoryLogger(storage_path=traj_file)

        mock_model = MagicMock(spec=BaseModelClient)
        mock_model.reasoning_model = "test"
        mock_model.chat.side_effect = [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        name="update_user_profile",
                        arguments={
                            "category": "preferences",
                            "key": "theme",
                            "value": "dracula",
                        },
                    )
                ],
            ),
            ModelResponse(
                content="",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Saved."})],
            ),
        ]

        mock_gk = MagicMock()
        mock_gk.evaluate.return_value = (True, "Approved")

        loop = AgentLoop(
            config={"memory": {"enabled": True, "confirm_profile_writes": True}},
            model_client=mock_model,
            gatekeeper=mock_gk,
            user_store=user_store,
            trajectory_logger=traj_logger,
        )

        res = loop.run_task("Remember my theme is dracula")
        assert res["success"] is True
        assert user_store.load(force_reload=True).preferences.get("theme") == "dracula"
        mock_gk.evaluate.assert_called()
