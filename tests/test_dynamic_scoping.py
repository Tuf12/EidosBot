"""Targeted unit tests for Dynamic Skill & Tool Scoping in AgentLoop (Roadmap Phase 12)."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock
import pytest

from eidos.core.loop import AgentLoop
from eidos.core.tool_registry import ToolRegistry
from eidos.memory.conversation_store import ConversationStore
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch


def test_loop_default_scoping_state():
    """Verify loop starts in base kernel mode with the universal tools and a lean prompt."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        assert agent.active_skill is None
        base_tools = agent._assemble_active_tools()
        assert len(base_tools) == 7
        base_tool_names = [t["function"]["name"] for t in base_tools]
        assert "execute_shell" in base_tool_names
        assert "load_skill" in base_tool_names
        assert "close_skill" in base_tool_names
        assert "get_current_time" in base_tool_names
        assert "finish_task" in base_tool_names
        assert "share_file" in base_tool_names
        assert "list_skills" in base_tool_names

        prompt = agent._assemble_system_prompt({"title": "Terminal", "class_name": "term"})
        assert "ACTIVE SKILL:" not in prompt


def test_loop_load_and_close_skill_dispatch():
    """Verify load_skill unlocks declared tools and close_skill releases them."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        # 1. Dispatch load_skill("desktop-gui")
        load_call = ToolCall(name="load_skill", arguments={"skill_name": "desktop-gui"})
        res, is_finish = agent._dispatch_tool(load_call)

        assert is_finish is False
        assert res["success"] is True
        assert res["status"] == "activated"
        assert res["skill"] == "desktop-gui"
        assert "take_screenshot" in res["tools_unlocked"]
        assert agent.active_skill is not None
        assert agent.active_skill["name"] == "desktop-gui"

        # Check prompt now includes active skill
        scoped_prompt = agent._assemble_system_prompt({"title": "GIMP", "class_name": "gimp"})
        assert "ACTIVE SKILL: desktop-gui" in scoped_prompt

        # Check scoped tools now include desktop-gui tools
        scoped_tools = agent._assemble_active_tools()
        scoped_names = [t["function"]["name"] for t in scoped_tools]
        assert len(scoped_tools) == 7 + len(res["tools_unlocked"])
        assert "share_file" in scoped_names
        assert "list_skills" in scoped_names
        assert "take_screenshot" in scoped_names
        assert "click_element" in scoped_names
        assert "hotkey" in scoped_names
        assert "execute_shell" in scoped_names

        # 2. Dispatch close_skill()
        close_call = ToolCall(name="close_skill", arguments={})
        c_res, c_is_finish = agent._dispatch_tool(close_call)

        assert c_is_finish is False
        assert c_res["success"] is True
        assert c_res["status"] == "closed"
        assert c_res["previous_skill"] == "desktop-gui"
        assert agent.active_skill is None

        # Verify prompt and tools dropped back to lean base
        lean_prompt = agent._assemble_system_prompt({"title": "Terminal", "class_name": "term"})
        assert "ACTIVE SKILL:" not in lean_prompt
        base_tools = agent._assemble_active_tools()
        assert len(base_tools) == 7


def test_loop_finish_task_resets_active_skill():
    """Verify finish_task automatically resets active skill."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        agent._dispatch_tool(ToolCall(name="load_skill", arguments={"skill_name": "optimalx"}))
        assert agent.active_skill is not None

        finish_res, is_finish = agent._dispatch_tool(
            ToolCall(name="finish_task", arguments={"summary": "Task complete"})
        )
        assert is_finish is True
        assert finish_res["status"] == "completed"
        assert agent.active_skill is None


def test_load_nonexistent_skill_error_handling():
    """Verify loading non-existent skill returns error and leaves state clean."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        res, is_finish = agent._dispatch_tool(
            ToolCall(name="load_skill", arguments={"skill_name": "non-existent-fantasy-skill"})
        )
        assert is_finish is False
        assert res["success"] is False
        assert "not found" in res["error"]
        assert agent.active_skill is None


def test_close_skill_when_none_active():
    """Verify closing skill when none active is a graceful no-op."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        res, is_finish = agent._dispatch_tool(
            ToolCall(name="close_skill", arguments={})
        )
        assert is_finish is False
        assert res["success"] is True
        assert "No skill is currently active" in res["message"]


def test_switching_active_skills():
    """Verify switching from skill A directly to skill B updates tools and prompt."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MagicMock(spec=BaseModelClient)

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        # 1. Load web-research
        res1, _ = agent._dispatch_tool(ToolCall(name="load_skill", arguments={"skill_name": "web-research"}))
        assert res1["success"] is True
        assert agent.active_skill["name"] == "web-research"
        web_tools = [t["function"]["name"] for t in agent._assemble_active_tools()]
        assert "web_search" in web_tools
        assert "share_file" in web_tools
        assert "list_skills" in web_tools
        assert "init_batch_job" not in web_tools

        # 2. Directly load batch-runner
        res2, _ = agent._dispatch_tool(ToolCall(name="load_skill", arguments={"skill_name": "batch-runner"}))
        assert res2["success"] is True
        assert agent.active_skill["name"] == "batch-runner"
        batch_tools = [t["function"]["name"] for t in agent._assemble_active_tools()]
        assert "init_batch_job" in batch_tools
        assert "share_file" in batch_tools
        assert "list_skills" in batch_tools
        assert "web_search" not in batch_tools


class DynamicScopingMockClient(BaseModelClient):
    """Mock model that tests dynamic tool expansion across turns."""

    def __init__(self):
        self.step = 0
        self.tools_seen_per_step = []

    def chat(self, messages, tools=None, model_override=None):
        self.step += 1
        tool_names = [t["function"]["name"] for t in (tools or [])]
        self.tools_seen_per_step.append(tool_names)

        if self.step == 1:
            # Model activates optimalx skill
            return ModelResponse(
                content="I need to consult the user's notes in OptimalX.",
                tool_calls=[ToolCall(name="load_skill", arguments={"skill_name": "optimalx"})],
            )
        elif self.step == 2:
            # Step 2: OptimalX tools should be present in tools!
            assert "optimalx_search_notes" in tool_names
            assert "optimalx_read_note" in tool_names
            assert "optimalx_write_note" in tool_names
            return ModelResponse(
                content="Found the note. Completing task.",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Successfully read OptimalX notes."})],
            )
        else:
            return ModelResponse(content="Done.")

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
        return ModelResponse(content="Visual state analyzed.")


def test_run_task_dynamic_scoping_flow():
    """End-to-end multi-step verification of dynamic scoping in run_task."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = DynamicScopingMockClient()
        conv_store = ConversationStore(db_path=":memory:")

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            conversation_store=conv_store,
        )

        res = agent.run_task("Check my invention ideas in OptimalX", max_steps=5, speak_summary=False)

        assert res["success"] is True
        assert res["steps_taken"] == 2
        assert "Successfully read OptimalX notes" in res["summary"]

        # Turn 1 only had the base tools, including share_file
        assert len(mock_model.tools_seen_per_step[0]) == 7
        assert "share_file" in mock_model.tools_seen_per_step[0]
        assert "list_skills" in mock_model.tools_seen_per_step[0]
        assert "optimalx_search_notes" not in mock_model.tools_seen_per_step[0]

        # Turn 2 had base tools + 7 optimalx tools
        assert len(mock_model.tools_seen_per_step[1]) == 14
        assert "optimalx_search_notes" in mock_model.tools_seen_per_step[1]

        # After task completion, active skill is reset
        assert agent.active_skill is None


def test_loop_init_batch_job_enters_job_mode():
    """Verify init_batch_job creates job.yaml, enters Job Mode, and unlocks scoped tools."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_allow = Path(tmp_dir) / "allow.json"
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=str(tmp_allow))
        gatekeeper = Gatekeeper(
            allowlist=allowlist,
            kill_switch=kill_switch,
            confirm_level_1_ui=False,
            confirm_level_2_shell=False,
        )
        mock_model = MagicMock(spec=BaseModelClient)

        jobs_output_dir = Path(tmp_dir) / "jobs" / "Inventions_List" / "phase_1_triage"

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
        )

        # 1. Initialize batch job
        init_call = ToolCall(
            name="init_batch_job",
            arguments={
                "job_name": "Inventions Phase 1",
                "skill_name": "batch-runner",
                "items_data": ["invention 1", "invention 2", "invention 3"],
                "output_dir": str(jobs_output_dir),
                "description": "Initial triage of 3 invention items",
                "project": "Inventions_List",
                "phase": "phase_1_triage",
            },
        )
        res, is_finish = agent._dispatch_tool(init_call)

        assert is_finish is False
        assert res.get("success") is True, f"init_batch_job failed with res={res}"
        assert res["job_mode"] == "activated"
        assert (jobs_output_dir / "queue.json").is_file()
        assert (jobs_output_dir / "job.yaml").is_file()

        # Check loop state
        assert agent.active_job is not None
        assert agent.active_job.get("job_id") == res["job_id"]
        assert agent.active_skill is not None
        assert agent.active_skill.get("name") == "batch-runner"

        # Check prompt contains lean ACTIVE JOB MODE packet
        prompt = agent._assemble_system_prompt({"title": "Terminal", "class_name": "term"})
        assert "ACTIVE JOB MODE: Inventions Phase 1" in prompt
        assert "Assigned Skill: batch-runner" in prompt
        assert "Progress: 0/3 items (0.0%)" in prompt

        # Check scoped tools contain batch tools + base tools
        tools = agent._assemble_active_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "init_batch_job" in tool_names
        assert "step_batch_job" in tool_names
        assert "load_job" in tool_names
        assert "close_job" in tool_names
        assert "execute_shell" in tool_names

        # 2. Close job
        close_res, _ = agent._dispatch_tool(ToolCall(name="close_job", arguments={}))
        assert close_res["success"] is True
        assert close_res["status"] == "closed"
        assert agent.active_job is None
        assert agent.active_skill is None

        # Verify dropped back to base kernel mode
        lean_prompt = agent._assemble_system_prompt({"title": "Terminal", "class_name": "term"})
        assert "ACTIVE JOB MODE:" not in lean_prompt
        assert len(agent._assemble_active_tools()) == 7

        # 3. Reload job via load_job
        load_res, _ = agent._dispatch_tool(ToolCall(name="load_job", arguments={"job_id": str(jobs_output_dir)}))
        assert load_res["success"] is True
        assert load_res["status"] == "activated"
        assert agent.active_job is not None
        assert agent.active_skill is not None
        assert agent.active_skill.get("name") == "batch-runner"

