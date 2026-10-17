"""Unit tests for Tier A: Local Episodic Vector Database (EpisodicStore & Embeddings)."""

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from eidos.core.context import build_system_prompt
from eidos.core.loop import AgentLoop
from eidos.memory.embeddings import OllamaEmbeddingClient
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import (
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    ReflectionEvaluation,
    StepRecord,
    UserProfile,
)
from eidos.memory.user_store import UserStore
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall


def _sample_success_episode(episode_id: str = "ep_success_1") -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        timestamp="2026-09-14T10:00:00Z",
        model="batiai/gemma4-26b:iq4",
        task_goal="Sort screenshots in Downloads by month into subdirectories",
        metadata=EpisodeMetadata(outcome="success", critic_score=1.0, total_steps=2),
        trajectory=[
            StepRecord(
                step=1,
                model_thinking="I should inspect PNG files first with find before moving",
                tool_calls=[
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "find ~/Downloads -name '*.png' -printf '%TY-%Tm %f\\n' | sort"
                        },
                    }
                ],
                tool_observations=[{"exit_code": 0, "stdout": "2026-09 shot1.png\n"}],
            ),
            StepRecord(
                step=2,
                model_thinking="Done listing; completed successfully",
                tool_calls=[
                    {
                        "name": "finish_task",
                        "arguments": {"summary": "Grouped downloads screenshots by month."},
                    }
                ],
                tool_observations=[{"status": "completed"}],
            ),
        ],
        reflection=EpisodeReflection(
            summary="Used read-only find dry run before moving any files.",
            lessons_learned="Always inspect matching files before proposing bulk operations.",
            pitfalls_avoided="Did not blind mv into uncreated folders.",
        ),
    )


def _sample_pitfall_episode(episode_id: str = "ep_pitfall_1") -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        timestamp="2026-09-13T14:00:00Z",
        model="batiai/gemma4-26b:iq4",
        task_goal="Bulk move screenshots in Downloads to Archive folder",
        metadata=EpisodeMetadata(outcome="failed", critic_score=0.0, total_steps=1),
        trajectory=[
            StepRecord(
                step=1,
                model_thinking="Running bulk mv directly",
                tool_calls=[
                    {
                        "name": "execute_shell",
                        "arguments": {"command": "mv ~/Downloads/*.png ~/Downloads/Archive/"},
                    }
                ],
                tool_observations=[
                    {"exit_code": 1, "stderr": "mv: target 'Archive/' is not a directory"}
                ],
            )
        ],
        reflection=EpisodeReflection(
            summary="Bulk move failed because destination directory did not exist.",
            pitfalls_avoided="Need to create destination directory before moving.",
            lessons_learned="Always run mkdir -p on target directory prior to moving files.",
        ),
    )


def test_ollama_embedding_client_fallback():
    client = OllamaEmbeddingClient(base_url="http://invalid.host:99999", fallback_to_dummy=True)
    assert client.is_available() is False

    # Empty text returns zero vector
    zero_vec = client.embed_text("")
    assert len(zero_vec) == 768
    assert all(v == 0.0 for v in zero_vec)

    # Fallback deterministic pseudo-embedding
    vec1 = client.embed_text("sort screenshots in downloads")
    vec2 = client.embed_text("sort screenshots in downloads")
    vec3 = client.embed_text("completely different query about pactl audio")

    assert len(vec1) == 768
    assert vec1 == vec2  # Deterministic for same text
    assert vec1 != vec3  # Distinct for different text


def test_episodic_store_lifecycle_and_indexing():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        store = EpisodicStore(db_path=db_path)

        assert store.count() == 0

        # Index success episode
        rec_s = _sample_success_episode()
        assert store.index_episode(rec_s) is True
        assert store.count() == 1

        # Fetch record back
        fetched = store.get_episode_record(rec_s.episode_id)
        assert fetched is not None
        assert fetched.task_goal == rec_s.task_goal
        assert fetched.metadata.outcome == "success"

        # Index pitfall episode
        rec_p = _sample_pitfall_episode()
        assert store.index_episode(rec_p) is True
        assert store.count() == 2

        # Re-indexing with overwrite
        assert store.index_episode(rec_s, overwrite=True) is True
        assert store.count() == 2

        # Delete episode
        assert store.delete_episode(rec_p.episode_id) is True
        assert store.count() == 1
        assert store.delete_episode("non_existent") is False

        # Clear store
        store.clear()
        assert store.count() == 0
        store.close()


def test_semantic_search_and_macro_retrieval():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        store = EpisodicStore(db_path=db_path)

        store.index_episode(_sample_success_episode())
        store.index_episode(_sample_pitfall_episode())

        # Search without filter
        results = store.search("screenshots in Downloads", top_k=5)
        assert len(results) >= 1
        top_res = results[0]
        assert top_res.similarity > 0.0
        assert "screenshots" in top_res.task_goal.lower() or "downloads" in top_res.task_goal.lower()

        # Search with outcome filter = "success"
        success_results = store.search("screenshots", top_k=2, outcome_filter="success")
        assert all(r.outcome == "success" for r in success_results)

        # Search with outcome filter = "pitfall"
        pitfall_results = store.search("screenshots", top_k=2, outcome_filter="pitfall")
        assert len(pitfall_results) >= 1
        assert any(r.pitfalls_avoided != "" or r.outcome != "success" for r in pitfall_results)

        # Macro Context Retrieval
        macro_ctx = store.retrieve_macro_context("organize screenshots into dated folders")
        assert "<episodic_context>" in macro_ctx
        assert "</episodic_context>" in macro_ctx
        assert "Past Experience - Golden Path Success" in macro_ctx
        assert "Past Experience - Cautionary Pitfall" in macro_ctx
        assert "Used read-only find dry run" in macro_ctx
        assert "Always run mkdir -p" in macro_ctx

        store.close()


def test_numpy_fallback_search():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_fallback.db"
        store = EpisodicStore(db_path=db_path)
        # Force numpy fallback mode
        store._has_sqlite_vec = False

        store.index_episode(_sample_success_episode())
        store.index_episode(_sample_pitfall_episode())

        results = store.search("screenshots in Downloads", top_k=2)
        assert len(results) >= 1

        macro = store.retrieve_macro_context("organize screenshots")
        assert "<episodic_context>" in macro
        assert "Golden Path Success" in macro

        store.close()


def test_sync_from_trajectory_file():
    with tempfile.TemporaryDirectory() as td:
        jsonl_path = Path(td) / "episodes.jsonl"
        db_path = Path(td) / "test_sync.db"

        # Write sample episodes to JSONL
        rec1 = _sample_success_episode("ep_sync_1")
        rec2 = _sample_pitfall_episode("ep_sync_2")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(rec1.model_dump_json() + "\n")
            f.write(rec2.model_dump_json() + "\n")

        store = EpisodicStore(db_path=db_path)
        assert store.count() == 0

        synced = store.sync_from_trajectory_file(jsonl_path)
        assert synced == 2
        assert store.count() == 2

        # Second sync should add 0 new records
        synced_again = store.sync_from_trajectory_file(jsonl_path)
        assert synced_again == 0
        assert store.count() == 2

        store.close()


def test_build_system_prompt_with_episodic_context():
    episodic_block = (
        "<episodic_context>\n"
        "Past Experience - Golden Path Success:\n"
        "- Past Task: List audio sinks\n"
        "- Resolution Summary: pactl list sink-inputs\n"
        "</episodic_context>"
    )
    prompt = build_system_prompt(
        active_window_info={"title": "Terminal", "class_name": "x-terminal", "geometry": {}},
        current_dir="/home/user",
        episodic_context=episodic_block,
    )
    assert "<episodic_context>" in prompt
    assert "Past Experience - Golden Path Success:" in prompt
    assert "EPISODIC LEARNING:" in prompt


def test_agent_loop_macro_retrieval_and_auto_indexing():
    with tempfile.TemporaryDirectory() as td:
        prof_file = Path(td) / "user_profile.yaml"
        traj_file = Path(td) / "episodes.jsonl"
        vector_db = Path(td) / "episodic.db"

        user_store = UserStore(storage_path=prof_file)
        traj_logger = TrajectoryLogger(storage_path=traj_file)
        episodic_store = EpisodicStore(db_path=vector_db)

        # Seed episodic store with past experience
        past_rec = _sample_success_episode("ep_past_001")
        episodic_store.index_episode(past_rec)
        assert episodic_store.count() == 1

        # Track the system prompt passed to chat()
        captured_messages = []

        def mock_chat(messages, tools=None, **kwargs):
            captured_messages.extend(messages)
            return ModelResponse(
                content="",
                thinking="I recall from past experience to use execute_shell",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Successfully sorted."})],
            )

        mock_model = MagicMock(spec=BaseModelClient)
        mock_model.reasoning_model = "test-reasoner"
        mock_model.chat.side_effect = mock_chat

        mock_gk = MagicMock()
        mock_gk.evaluate.return_value = (True, "Allowed")

        loop = AgentLoop(
            config={"memory": {"enabled": True}},
            model_client=mock_model,
            gatekeeper=mock_gk,
            user_store=user_store,
            trajectory_logger=traj_logger,
            episodic_store=episodic_store,
        )

        res = loop.run_task("Sort screenshots in Downloads by date")
        assert res["success"] is True

        # 1. Verify macro retrieval injected episodic context into the system prompt!
        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        assert len(system_msgs) > 0
        system_content = system_msgs[0]["content"]
        assert "<episodic_context>" in system_content
        assert "Past Experience - Golden Path Success" in system_content

        # 2. Verify that completing the task auto-indexed the new episode into episodic_store!
        assert episodic_store.count() == 2
        assert traj_logger.count_episodes() == 1

        episodic_store.close()


def test_failed_and_successful_action_separation():
    """Verify that failed exploratory actions become dead ends and all successful actions are kept in order."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_separation.db"
        store = EpisodicStore(db_path=db_path)

        rec = EpisodeRecord(
            episode_id="ep_brightness_test",
            task_goal="can you turn the screen brightness down to 25",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0, total_steps=6),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "brightnessctl set 25%"}}],
                    tool_observations=[{"success": False, "exit_code": 127, "error": "/bin/bash: line 1: brightnessctl: command not found"}],
                ),
                StepRecord(
                    step=2,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "xrandr --get-output-name | head -n 1"}}],
                    tool_observations=[{"success": False, "exit_code": 1, "error": "xrandr: unrecognized option '--get-output-name'"}],
                ),
                StepRecord(
                    step=3,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "xrandr --query | grep ' connected primary' | cut -d' ' -f1"}}],
                    tool_observations=[{"success": True, "exit_code": 0, "stdout": "HDMI-0"}],
                ),
                StepRecord(
                    step=4,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "xbacklight -set 25"}}],
                    tool_observations=[{"success": False, "exit_code": 127, "error": "/bin/bash: line 1: xbacklight: command not found"}],
                ),
                StepRecord(
                    step=5,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "xrandr --output HDMI-0 --brightness 0.25"}}],
                    tool_observations=[{"success": True, "exit_code": 0, "stdout": ""}],
                ),
                StepRecord(
                    step=6,
                    tool_calls=[{"name": "finish_task", "arguments": {"summary": "Screen brightness lowered to 25%."}}],
                    tool_observations=[{"status": "completed"}],
                ),
            ],
        )

        assert store.index_episode(rec) is True

        # Check search result
        res = store.search("brightness", top_k=1)[0]
        assert res.outcome == "success"
        # key_actions must ONLY contain the successful actions in forward chronological order
        assert len(res.key_actions) == 2
        assert "execute_shell(xrandr --query | grep ' connected primary' | cut -d' ' -f1)" in res.key_actions[0]
        assert "execute_shell(xrandr --output HDMI-0 --brightness 0.25)" in res.key_actions[1]

        # pitfalls_avoided must contain the dead-end failures and their short reasons
        assert "brightnessctl: command not found" in res.pitfalls_avoided
        assert "xbacklight: command not found" in res.pitfalls_avoided

        # Check macro context presentation
        macro = store.retrieve_macro_context("turn screen brightness down")
        assert "Past Experience - Golden Path Success" in macro
        assert "Dead Ends / Pitfalls Avoided:" in macro
        assert "brightnessctl: command not found" in macro
        assert "Proven Tool Pattern:" in macro
        assert "xrandr --query" in macro
        assert "xrandr --output HDMI-0 --brightness 0.25" in macro

        store.close()


def test_sync_from_trajectory_file_reindex():
    """Verify that sync_from_trajectory_file with reindex=True updates existing records."""
    with tempfile.TemporaryDirectory() as td:
        jsonl_path = Path(td) / "episodes.jsonl"
        db_path = Path(td) / "test_reindex.db"

        rec1 = _sample_success_episode("ep_reindex_1")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(rec1.model_dump_json() + "\n")

        store = EpisodicStore(db_path=db_path)
        assert store.sync_from_trajectory_file(jsonl_path) == 1
        assert store.count() == 1

        # Reindex with reindex=True
        assert store.sync_from_trajectory_file(jsonl_path, reindex=True) == 1
        assert store.count() == 1

        store.close()


def test_update_episode_reflection():
    """Verify update_episode_reflection updates relational and vector storage."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_update_refl.db"
        store = EpisodicStore(db_path=db_path)

        rec = _sample_success_episode("ep_refl_test")
        store.index_episode(rec)
        assert store.count() == 1

        # Non-existent episode returns False
        assert store.update_episode_reflection("non_existent", EpisodeReflection(summary="none"), 0.5) is False

        # Update with EpisodeReflection and score
        new_refl = EpisodeReflection(
            summary="Updated resolution summary",
            pitfalls_avoided="Avoided direct moving",
            lessons_learned="Always verify destination path",
            goal_fulfilled=True,
        )
        assert store.update_episode_reflection(rec.episode_id, new_refl, critic_score=0.92) is True

        updated_rec = store.get_episode_record(rec.episode_id)
        assert updated_rec is not None
        assert updated_rec.metadata.critic_score == 0.92
        assert updated_rec.reflection.summary == "Updated resolution summary"
        assert updated_rec.reflection.lessons_learned == "Always verify destination path"

        # Search reflects updated summary and critic_score
        results = store.search("screenshots", top_k=1)
        assert len(results) == 1
        assert results[0].summary == "Updated resolution summary"
        assert results[0].critic_score == 0.92

        # Update with ReflectionEvaluation where goal_fulfilled is False
        eval_refl = ReflectionEvaluation(
            goal_fulfilled=False,
            critic_score=0.25,
            summary="Command failed to create files",
            pitfalls_avoided="Did not check exit code",
            lessons_learned="Verify output before claiming success",
            recommended_rules=["Confirm file creation with ls"],
        )
        assert store.update_episode_reflection(rec.episode_id, eval_refl) is True

        unverified_rec = store.get_episode_record(rec.episode_id)
        assert unverified_rec is not None
        assert unverified_rec.metadata.outcome == "failed_unverified"
        assert unverified_rec.metadata.critic_score == 0.25
        assert unverified_rec.reflection.summary == "Command failed to create files"
        assert unverified_rec.reflection.recommended_rules == ["Confirm file creation with ls"]

        store.close()

