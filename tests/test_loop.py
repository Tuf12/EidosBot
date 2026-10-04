"""Tests for EidosBot Agent Loop."""

import tempfile
from unittest.mock import MagicMock
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


class ConversationalMockClient(BaseModelClient):
    def __init__(self):
        self.received_messages = []

    def chat(self, messages, tools=None, model_override=None):
        self.received_messages.append(list(messages))
        return ModelResponse(
            content=f"Response to: {messages[-1]['content']}",
            thinking="Step-by-step thinking for task"
        )

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
        return ModelResponse(content="Visual state")


def test_agent_loop_callbacks_and_conversational_history():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = ConversationalMockClient()

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            config={"memory": {"conversations_db_path": ":memory:"}},
        )

        thoughts = []
        responses = []
        finishes = []

        agent.register_callback("on_thought", lambda t: thoughts.append(t))
        agent.register_callback("on_response", lambda r: responses.append(r))
        agent.register_callback("on_task_finish", lambda s, succ: finishes.append((s, succ)))

        # Turn 1
        res1 = agent.run_task("Hello EidosBot")
        assert res1["success"] is True
        assert len(thoughts) == 1
        assert "Step-by-step thinking" in thoughts[0]
        assert len(responses) == 1
        assert "Response to: User Goal: Hello EidosBot" in responses[0]
        assert len(finishes) == 1

        # Check conversational history contains turn 1
        assert len(agent.conversation_history) == 2
        assert agent.conversation_history[0]["role"] == "user"
        assert agent.conversation_history[1]["role"] == "assistant"

        # Turn 2: verify multi-turn context is forwarded to model
        res2 = agent.run_task("What can you do?")
        assert res2["success"] is True
        # In turn 2, the model messages should have included the previous user and assistant messages
        last_call_messages = mock_model.received_messages[-1]
        roles = [m["role"] for m in last_call_messages]
        assert "system" in roles
        assert roles.count("user") == 2
        assert roles.count("assistant") == 1

        # Test clear history
        agent.clear_history()
        assert len(agent.conversation_history) == 0


def test_agent_loop_auto_speaks_finish_task_summary():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockModelClient()

        mock_voice = MagicMock()
        mock_voice.tts.is_available = True

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
            config={"voice": {"enabled": True, "auto_speak_responses": True}},
        )

        res = agent.run_task("Perform loop verification", max_steps=5)
        assert res["success"] is True
        mock_voice.speak_response.assert_called_once_with(
            "Agent loop test successfully finished.",
        )
        mock_voice.speak.assert_not_called()


def test_agent_loop_auto_speaks_text_only_completion():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = ConversationalMockClient()

        mock_voice = MagicMock()
        mock_voice.tts.is_available = True

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
            config={"voice": {"enabled": True, "auto_speak_responses": True}},
        )

        res = agent.run_task("Hello EidosBot")
        assert res["success"] is True
        mock_voice.speak_response.assert_called_once()
        assert "Response to:" in mock_voice.speak_response.call_args[0][0]
        mock_voice.speak.assert_not_called()


def test_agent_loop_skips_auto_speak_when_disabled():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockModelClient()

        mock_voice = MagicMock()
        mock_voice.tts.is_available = True

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
            config={"voice": {"enabled": True, "auto_speak_responses": False}},
        )

        agent.run_task("Perform loop verification", max_steps=5)
        mock_voice.speak.assert_not_called()
        mock_voice.speak_response.assert_not_called()


def test_agent_loop_skips_auto_speak_when_speak_summary_false():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockModelClient()

        mock_voice = MagicMock()
        mock_voice.tts.is_available = True

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
            config={"voice": {"enabled": True, "auto_speak_responses": True}},
        )

        # Explicitly pass speak_summary=False (as done by remote mobile gateway)
        res = agent.run_task("Perform loop verification", max_steps=5, speak_summary=False)
        assert res["success"] is True
        mock_voice.speak.assert_not_called()
        mock_voice.speak_response.assert_not_called()


def test_agent_loop_cancel_task():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)

        class SlowMockModel(BaseModelClient):
            def __init__(self):
                self.agent = None

            def chat(self, messages, tools=None, model_override=None):
                # When model is called, trigger cancel on agent
                if self.agent:
                    self.agent.cancel_task()
                return ModelResponse(
                    content="Thinking...",
                    tool_calls=[ToolCall(name="get_active_window", arguments={})]
                )

            def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
                return ModelResponse(content="")

        mock_model = SlowMockModel()
        mock_voice = MagicMock()

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
        )
        mock_model.agent = agent

        res = agent.run_task("Task to cancel", max_steps=5)
        assert res["success"] is False
        assert "stopped by user" in res["summary"].lower()


def test_agent_loop_optimalx_tool_dispatch():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)

        class OptimalXMockModel(BaseModelClient):
            def __init__(self):
                self.step = 0

            def chat(self, messages, tools=None, model_override=None):
                self.step += 1
                if self.step == 1:
                    return ModelResponse(
                        content="Searching notes",
                        tool_calls=[ToolCall(name="optimalx_search_notes", arguments={"query": "patent architecture"})]
                    )
                elif self.step == 2:
                    return ModelResponse(
                        content="Listing folders",
                        tool_calls=[ToolCall(name="optimalx_list_folders", arguments={})]
                    )
                elif self.step == 3:
                    return ModelResponse(
                        content="Writing note",
                        tool_calls=[ToolCall(name="optimalx_write_note", arguments={"content": "New ideas", "subfolder_title": "Idea 1"})]
                    )
                else:
                    return ModelResponse(
                        content="Finished note exploration",
                        tool_calls=[ToolCall(name="finish_task", arguments={"summary": "OptimalX notes handled successfully."})]
                    )

            def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
                return ModelResponse(content="")

        mock_model = OptimalXMockModel()
        mock_voice = MagicMock()
        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
        )

        agent.optimalx = MagicMock()
        agent.optimalx.search_notes.return_value = [{"title": "Patent 1", "snippet": "..."}]
        agent.optimalx.list_folders.return_value = [{"id": 1, "name": "Inventions", "subfolders": []}]
        agent.optimalx.write_note.return_value = {"status": "written", "kind": "written", "subfolder_id": 10}

        res = agent.run_task("Explore and write notes", max_steps=5)
        assert res["success"] is True
        agent.optimalx.search_notes.assert_called_once_with(
            query="patent architecture",
            limit=5,
            parent_folder=None,
            parent_filter=None,
            subfolder=None,
            prefer_offline=False,
        )
        agent.optimalx.list_folders.assert_called_once_with(
            parent_folder_id=None, include_subfolders=True, query=None
        )
        agent.optimalx.write_note.assert_called_once_with(
            content="New ideas",
            subfolder_id=None,
            subfolder_title="Idea 1",
            parent_name=None,
            parent_folder_id=None,
            mode="auto",
            allow_direct_append=False,
            label=None,
            conversation_id=None,
        )


def test_agent_loop_tool_exception_recovery():
    """Verify that an unexpected tool exception does not crash the loop and allows model recovery."""
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)

        class ErrorRecoveryMockModel(BaseModelClient):
            def __init__(self):
                self.step = 0

            def chat(self, messages, tools=None, model_override=None):
                self.step += 1
                if self.step == 1:
                    return ModelResponse(
                        content="Attempting search that will fail",
                        tool_calls=[ToolCall(name="optimalx_search_notes", arguments={"query": "item 114"})]
                    )
                else:
                    # Model inspects error in messages and finishes gracefully
                    last_tool_msg = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
                    assert last_tool_msg is not None
                    assert "Tool 'optimalx_search_notes' execution failed" in last_tool_msg["content"]
                    return ModelResponse(
                        content="Self-corrected after tool failure",
                        tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Handled tool error gracefully."})]
                    )

            def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
                return ModelResponse(content="")

        mock_model = ErrorRecoveryMockModel()
        mock_voice = MagicMock()
        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model,
            voice_manager=mock_voice,
        )

        agent.optimalx = MagicMock()
        # Simulate unexpected keyword argument or runtime crash in bridge
        agent.optimalx.search_notes.side_effect = TypeError("OptimalXBridge.search_notes() got an unexpected keyword argument 'parent_filter'")

        res = agent.run_task("Search for item 114", max_steps=5)
        assert res["success"] is True
        assert res["summary"] == "Handled tool error gracefully."

