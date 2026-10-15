"""Unit tests for EidosBot lean system prompt construction."""

from eidos.core.clock import EidosClock
from eidos.core.context import build_system_prompt


def test_build_system_prompt_structure_and_lean_size():
    """Verify lean base system prompt contains essential context without bloat."""
    active_win = {
        "title": "Visual Studio Code",
        "class_name": "code",
        "geometry": {"x": 100, "y": 200, "width": 1920, "height": 1080},
    }
    clock = EidosClock(timezone_name="America/Chicago")

    prompt = build_system_prompt(
        active_window_info=active_win,
        current_dir="/home/user/myproject",
        clock=clock,
        workspace_dir="~/CustomWorkspace",
    )

    # 1. Environment snapshot assertions
    assert "You are EidosBot" in prompt
    assert "Linux Mint (Cinnamon / X11)" in prompt
    assert "Current Date & Time:" in prompt
    assert "America/Chicago" in prompt
    assert "CustomWorkspace" in prompt
    assert 'Active Window: "Visual Studio Code" (Class: code)' in prompt
    assert "100,200 (1920x1080)" in prompt
    assert "Emergency Kill Switch: Active on Ctrl + Alt + Esc" in prompt

    # 2. Core behavioral principles assertions
    assert "1. DETERMINISTIC TIER FIRST:" in prompt
    assert "2. SKILL-DRIVEN CAPABILITY EXPANSION:" in prompt
    assert "list_skills" in prompt
    assert "load_skill" in prompt
    assert "close_skill" in prompt
    assert "3. CONCISE REASONING:" in prompt
    assert "4. TIME & INTERNAL CLOCK:" in prompt
    assert "5. EPISODIC LEARNING:" in prompt
    assert "6. CONSCIOUS ACTIVE RECALL:" in prompt
    assert "<recall>search keywords</recall>" in prompt
    assert "<recalled_memory>" in prompt
    assert "7. WEB APP FILE DELIVERY:" in prompt
    assert "share_file" in prompt
    assert "8. COMPLETION:" in prompt
    assert "finish_task" in prompt

    # 3. Bloat elimination assertions (verifying domain biases are pruned from base prompt)
    assert "15. OPTIMALX KNOWLEDGE ECOSYSTEM:" not in prompt
    assert "search and consult OptimalX first" not in prompt
    assert "10. DETERMINISTIC WEB AUTOMATION:" not in prompt
    assert "16. DATA EXTRACTION & FILE CONVERSION" not in prompt

    # 4. Lean prompt line count verification
    lines = prompt.strip().splitlines()
    assert len(lines) <= 60, f"Prompt is too large: {len(lines)} lines"


def test_build_system_prompt_with_user_profile_and_episodic_blocks():
    """Verify profile and episodic context blocks are cleanly injected."""
    user_profile = (
        "USER PROFILE & CONVENTIONS:\n"
        "- Name: Alex\n"
        "- Preferences: Prefer concise responses\n"
        "- Workflow Rules: Always run targeted tests"
    )
    episodic_ctx = (
        "<episodic_context>\n"
        "Past Experience - Golden Path Success:\n"
        "- Task: List disk usage\n"
        "- Resolution: execute_shell('df -h')\n"
        "</episodic_context>"
    )

    prompt = build_system_prompt(
        active_window_info={},
        current_dir="/workspace",
        user_profile_context=user_profile,
        episodic_context=episodic_ctx,
    )

    assert "USER PROFILE & CONVENTIONS:" in prompt
    assert "Prefer concise responses" in prompt
    assert "<episodic_context>" in prompt
    assert "Past Experience - Golden Path Success:" in prompt
    assert "execute_shell('df -h')" in prompt
