"""Unit tests for EidosBot dynamic ToolRegistry."""

import pytest
from eidos.core.tool_registry import ToolRegistry
from eidos.core.context import EIDOS_TOOLS


def test_base_tools_count_and_presence():
    """Verify the base kernel keeps share_file visible alongside the other universal tools."""
    base_tools = ToolRegistry.get_base_tools()
    assert len(base_tools) == 7

    names = [t["function"]["name"] for t in base_tools]
    assert "execute_shell" in names
    assert "load_skill" in names
    assert "close_skill" in names
    assert "get_current_time" in names
    assert "finish_task" in names
    assert "share_file" in names
    assert "list_skills" in names


def test_scoped_tools_default_and_expansion():
    """Verify tool scoping unlocks requested tools without duplicating base tools."""
    # When no specialized tools requested, returns base tools only
    default_scoped = ToolRegistry.get_scoped_tools()
    assert len(default_scoped) == 7

    # Requesting GUI tools unlocks them alongside base tools
    gui_scoped = ToolRegistry.get_scoped_tools(["take_screenshot", "click_element", "hotkey"])
    assert len(gui_scoped) == 10
    names = [t["function"]["name"] for t in gui_scoped]
    assert "take_screenshot" in names
    assert "click_element" in names
    assert "hotkey" in names
    assert "execute_shell" in names

    # Requesting a tool already in base does not create duplicates
    deduped = ToolRegistry.get_scoped_tools(["execute_shell", "browser_open", "share_file"])
    assert len(deduped) == 8
    assert names.count("execute_shell") == 1


def test_get_tool_resolution():
    """Verify tool schema lookup by function name."""
    shell_tool = ToolRegistry.get_tool("execute_shell")
    assert shell_tool is not None
    assert shell_tool["function"]["name"] == "execute_shell"

    optx_tool = ToolRegistry.get_tool("optimalx_search_notes")
    assert optx_tool is not None
    assert optx_tool["function"]["name"] == "optimalx_search_notes"

    unknown = ToolRegistry.get_tool("nonexistent_tool_xyz")
    assert unknown is None


def test_deadweight_tools_purged():
    """Verify deadweight human review tools are excluded from LLM function schemas."""
    specialized_names = ToolRegistry.list_specialized_tool_names()
    assert "optimalx_reject_pending_review" not in specialized_names
    assert "optimalx_accept_pending_review" not in specialized_names
    assert "optimalx_list_pending_reviews" not in specialized_names


def test_context_backward_compatibility():
    """Verify EIDOS_TOOLS export maintains backward compatibility."""
    assert len(EIDOS_TOOLS) >= 45
    all_names = [t["function"]["name"] for t in EIDOS_TOOLS]
    assert "execute_shell" in all_names
    assert "load_skill" in all_names
    assert "close_skill" in all_names
    assert "optimalx_write_note" in all_names
    assert "browser_open" in all_names
