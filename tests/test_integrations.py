"""Tests for EidosBot application integrations (OptimalX, Cursor, Web Research)."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from eidos.core.loop import AgentLoop
from eidos.integrations.cursor_ide import CursorAdapter
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch


# =============================================================================
# OptimalX Desktop Tests
# =============================================================================

def test_optimalx_bridge_config_load():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"syncPort": 7890, "syncToken": "test_token_123"}, f)
        cfg_path = f.name

    try:
        bridge = OptimalXBridge(config_path=cfg_path, cdp_port=9222)
        cfg = bridge.load_app_config()
        assert cfg.get("syncPort") == 7890
        assert bridge.sync_port == 7890
        assert bridge.sync_token == "test_token_123"
    finally:
        os.remove(cfg_path)


def test_optimalx_bridge_status_live():
    # OptimalX Desktop is running on this machine with sync server on port 7373
    bridge = OptimalXBridge()
    # If running, verify live response format or fallback
    if bridge.is_running():
        status = bridge.get_sync_status()
        if "error" not in status:
            assert "deviceId" in status
            assert "tableCounts" in status


def test_optimalx_cdp_unavailable_handling():
    # Using an unlikely port (e.g. 59999) to verify clean error response
    bridge = OptimalXBridge(cdp_port=59999)
    assert not bridge.is_cdp_available()
    res = bridge.evaluate_js_sync("1 + 1")
    assert not res["success"]
    assert "not reachable" in res["error"]


# =============================================================================
# Cursor IDE Tests
# =============================================================================

def test_cursor_adapter_installed():
    adapter = CursorAdapter()
    assert adapter.is_installed()


def test_cursor_adapter_open_file_validation():
    adapter = CursorAdapter()
    # Non-existent file should return error
    res = adapter.open_file("/non/existent/path/file.py", line=10)
    assert not res["success"]
    assert "does not exist" in res["error"]

    # Existing file should return success
    this_file = os.path.abspath(__file__)
    with patch("subprocess.Popen") as mock_popen, patch.object(adapter, "focus_window", return_value=True):
        res = adapter.open_file(this_file, line=25, col=5)
        assert res["success"]
        assert f"{this_file}:25:5" in res["target"]
        mock_popen.assert_called_once()


def test_cursor_adapter_diff_command():
    adapter = CursorAdapter()
    this_file = os.path.abspath(__file__)
    with patch("subprocess.Popen") as mock_popen, patch.object(adapter, "focus_window", return_value=True):
        res = adapter.open_diff(this_file, this_file)
        assert res["success"]
        mock_popen.assert_called_once()


# =============================================================================
# Web Research Engine Tests
# =============================================================================

def test_web_research_key_discovery():
    engine = WebResearchEngine()
    # Check that it finds Brave API key from OptimalX config or env
    assert isinstance(engine.brave_api_key, str)


def test_web_research_fetch_and_clean_html():
    engine = WebResearchEngine()
    # Mocking response for fast, deterministic unit test
    fake_html = """
    <html>
      <head><title>EidosBot Documentation</title></head>
      <body>
        <nav><a href="#">Menu</a></nav>
        <h1>Welcome to EidosBot</h1>
        <p>EidosBot is a local-first OS-level autonomous agent for Linux.</p>
        <script>console.log('remove me');</script>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = fake_html
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        res = engine.fetch_webpage("https://example.com/eidos")
        assert res["success"]
        assert res["title"] == "EidosBot Documentation"
        assert "Welcome to EidosBot" in res["text"]
        assert "EidosBot is a local-first" in res["text"]
        # Script and footer should be stripped
        assert "console.log" not in res["text"]
        assert "Copyright" not in res["text"]


def test_web_research_search_results():
    engine = WebResearchEngine()
    # If Brave key is present, test live search with small query
    if engine.brave_api_key:
        results = engine.search("Linux Mint", count=2)
        assert len(results) > 0
        assert "title" in results[0]
        assert "url" in results[0]


# =============================================================================
# Agent Loop Tool Dispatch Tests for Phase 4
# =============================================================================

class MockPhase4Model(BaseModelClient):
    """Mock model that invokes Phase 4 integration tools."""

    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None, model_override=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="Checking web search",
                tool_calls=[ToolCall(name="web_search", arguments={"query": "test query", "count": 2})]
            )
        elif self.step == 2:
            return ModelResponse(
                content="Opening file in cursor",
                tool_calls=[ToolCall(name="cursor_action", arguments={"action": "open_file", "file_path": __file__, "line": 10})]
            )
        else:
            return ModelResponse(
                content="Task finished",
                tool_calls=[ToolCall(name="finish_task", arguments={"summary": "Phase 4 loop test complete"})]
            )

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None, dense_grounding=False):
        return ModelResponse(content="Visual state")


def test_agent_loop_phase4_tool_dispatch():
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp_allow:
        kill_switch = KillSwitch()
        allowlist = AllowlistManager(storage_path=tmp_allow.name)
        gatekeeper = Gatekeeper(allowlist=allowlist, kill_switch=kill_switch)
        mock_model = MockPhase4Model()

        agent = AgentLoop(
            kill_switch=kill_switch,
            gatekeeper=gatekeeper,
            model_client=mock_model
        )

        with patch("subprocess.Popen"), patch.object(agent.cursor, "focus_window", return_value=True):
            res = agent.run_task("Test phase 4 integrations", max_steps=5)
            assert res["success"] is True
            assert res["steps_taken"] == 3
            assert "Phase 4 loop test complete" in res["summary"]
