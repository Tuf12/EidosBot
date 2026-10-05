"""Tests for EidosBot living Skills subsystem (~/.eidos/skills/)."""

import os
import tempfile
import pytest

from eidos.skills.manager import SkillManager, parse_frontmatter


def test_parse_frontmatter():
    raw = """---
name: code-reviewer
version: 1.2.0
description: Autonomous code review procedure
author: test-author
tags: [code, review]
---
# Code Reviewer
1. Check style
2. Verify safety
"""
    meta, body = parse_frontmatter(raw)
    assert meta["name"] == "code-reviewer"
    assert meta["version"] == "1.2.0"
    assert meta["author"] == "test-author"
    assert meta["tags"] == ["code", "review"]
    assert "# Code Reviewer" in body
    assert "Verify safety" in body


def test_parse_frontmatter_missing():
    raw = "# No Frontmatter\nJust plain instructions here."
    meta, body = parse_frontmatter(raw)
    assert meta == {}
    assert "No Frontmatter" in body


def test_skill_manager_lifecycle():
    with tempfile.TemporaryDirectory() as global_dir, tempfile.TemporaryDirectory() as proj_dir:
        manager = SkillManager(skills_dir=global_dir, project_skills_dir=proj_dir, include_builtin=False)

        # 1. Initially empty
        assert len(manager.list_skills()) == 0

        # 2. Create skill
        content = """---
name: log-analyzer
version: 1.0.0
description: Parse and identify anomalous server log entries
author: security-team
tags: [audit, logs]
---
# Log Analyzer
Inspect logs for 401 and 500 status codes.
"""
        create_res = manager.create_skill(name="log-analyzer", content=content)
        assert create_res["success"] is True
        assert os.path.exists(create_res["path"])

        # 3. List skills
        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "log-analyzer"
        assert skills[0]["version"] == "1.0.0"
        assert "Parse and identify" in skills[0]["description"]

        # 4. Load skill
        loaded = manager.load_skill("log-analyzer")
        assert loaded is not None
        assert loaded["name"] == "log-analyzer"
        assert loaded["metadata"]["author"] == "security-team"
        assert "Inspect logs" in loaded["body"]

        # 5. Update skill
        updated_content = content + "\n3. Alert Slack channel if threshold exceeded."
        up_res = manager.update_skill("log-analyzer", updated_content)
        assert up_res["success"] is True

        reloaded = manager.load_skill("log-analyzer")
        assert "Alert Slack channel" in reloaded["body"]

        # 6. Delete skill
        del_res = manager.delete_skill("log-analyzer")
        assert del_res["success"] is True
        assert len(manager.list_skills()) == 0


def test_skill_manager_project_override():
    with tempfile.TemporaryDirectory() as global_dir, tempfile.TemporaryDirectory() as proj_dir:
        manager = SkillManager(skills_dir=global_dir, project_skills_dir=proj_dir, include_builtin=False)

        # Global version of doc-gen
        manager.create_skill(
            name="doc-gen",
            content="---\nname: doc-gen\ndescription: Global doc generator\n---\n# Global",
            scope="global",
        )

        # Project version of doc-gen (overrides global)
        manager.create_skill(
            name="doc-gen",
            content="---\nname: doc-gen\ndescription: Project-specific doc generator\n---\n# Project",
            scope="project",
        )

        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0]["scope"] == "project"
        assert "Project-specific" in skills[0]["description"]

        loaded = manager.load_skill("doc-gen")
        assert loaded["scope"] == "project"
        assert "# Project" in loaded["body"]


def test_builtin_skills_discovery():
    """Verify all 7 system built-in skills are discovered with tools_required."""
    from eidos.core.tool_registry import ToolRegistry

    manager = SkillManager(skills_dir="/tmp/nonexistent_skills_dir", project_skills_dir="/tmp/nonexistent_proj_dir")
    skills = manager.list_skills()

    expected_builtins = {
        "desktop-gui",
        "browser-automation",
        "optimalx",
        "batch-runner",
        "web-research",
        "user-memory",
        "skills-manager",
    }
    discovered_names = {s["name"] for s in skills}
    assert expected_builtins.issubset(discovered_names)

    # Verify each builtin skill has valid tools_required registered in ToolRegistry
    valid_tool_names = set(ToolRegistry.list_specialized_tool_names()) | {
        t["function"]["name"] for t in ToolRegistry.BASE_TOOLS
    }

    for skill_name in expected_builtins:
        loaded = manager.load_skill(skill_name)
        assert loaded is not None, f"Failed to load builtin skill {skill_name}"
        assert loaded["scope"] == "builtin"
        assert len(loaded["tools_required"]) > 0, f"Skill {skill_name} has empty tools_required"
        for tool_name in loaded["tools_required"]:
            assert tool_name in valid_tool_names, f"Tool {tool_name} in {skill_name} not found in ToolRegistry"


def test_builtin_skill_deletion_protection():
    """Verify built-in system skills cannot be deleted."""
    manager = SkillManager()
    del_res = manager.delete_skill("desktop-gui")
    assert del_res["success"] is False
    assert "Cannot delete built-in system skill" in del_res["error"]

    # Verify skill still exists and loads
    loaded = manager.load_skill("desktop-gui")
    assert loaded is not None


def test_project_overrides_builtin_skill():
    """Verify project-level skill can override a built-in skill of the same name."""
    with tempfile.TemporaryDirectory() as global_dir, tempfile.TemporaryDirectory() as proj_dir:
        manager = SkillManager(skills_dir=global_dir, project_skills_dir=proj_dir)

        # Override desktop-gui at project level
        custom_content = (
            "---\n"
            "name: desktop-gui\n"
            "version: 2.0.0\n"
            "description: Custom project GUI procedure\n"
            "tools_required: [take_screenshot]\n"
            "---\n"
            "# Custom GUI Procedure\n"
        )
        create_res = manager.create_skill("desktop-gui", custom_content, scope="project")
        assert create_res["success"] is True

        loaded = manager.load_skill("desktop-gui")
        assert loaded is not None
        assert loaded["scope"] == "project"
        assert loaded["metadata"]["version"] == "2.0.0"
        assert loaded["tools_required"] == ["take_screenshot"]


def test_invention_evaluator_skill_accessible():
    """Verify that the bundled invention-evaluator demonstration skill is loaded properly."""
    manager = SkillManager()
    skill = manager.load_skill("invention-evaluator")
    assert skill is not None
    assert skill["name"] == "invention-evaluator"
    assert "commercial rubric" in skill["metadata"].get("description", "").lower()
    assert "Dimension A: Technical Feasibility" in skill["body"]
    assert "Dimension B: Market Demand" in skill["body"]
    assert "Dimension C: Novelty & Freedom to Operate" in skill["body"]
    assert "Dimension D: Cost-to-Produce vs. Margin Potential" in skill["body"]
