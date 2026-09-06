"""Tests for EidosBot Agent Loop."""

import tempfile
from eidos.core.loop import AgentLoop
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch


class MockModelClient(BaseModelClient):
    """Mock LLM client that returns scripted tool calls."""

    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None, model_override=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="I will check the active window.",
                tool_calls=[ToolCall(name="get_active_window", arguments={})]
            )
        elif self.step == 2:
            return ModelResponse(
                content="Now I will run a read-only shell command.",
                tool_calls=[ToolCall(name="execute_shell", arguments={"command": "echo 'Loop Test OK'"})]
            )
        else:
            return ModelResponse(
                content="I have completed the task.",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Agent loop test successfully finished."})]
            )

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
        return ModelResponse(content="Visual state analyzed.")


def test_agent_loop_execution():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockModelClient()

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model
        )

        res = agent.run_task("Perform loop verification", max_steps=5)
        assert res["success"] is True
        assert res["steps_taken"] == 3
        assert "Agent loop test successfully finished" in res["summary"]


def test_agent_loop_kill_switch():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockModelClient()

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model
        )

        # Trigger kill switch prior to running
        kill_switch.trigger()
        res = agent.run_task("Task that should be aborted", max_steps=5)
        assert res["success"] is False
        assert "kill switch" in res["summary"].lower()
