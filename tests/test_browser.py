"""Tests for EidosBot Headless Browser Subsystem (Playwright & Trafilatura)."""

import os
import tempfile
import pytest

from eidos.browser.manager import PLAYWRIGHT_AVAILABLE, PlaywrightManager


@pytest.fixture(autouse=True)
def cleanup_browser():
    """Ensure PlaywrightManager singleton is reset before and after each test."""
    PlaywrightManager.reset_instance()
    yield
    PlaywrightManager.reset_instance()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_playwright_manager_lifecycle():
    """Verify PlaywrightManager properly launches, reports running state, and closes."""
    manager = PlaywrightManager()
    assert not manager.is_running()

    res = manager.navigate("https://example.com")
    assert res["success"] is True
    assert "Example Domain" in res["title"]
    assert manager.is_running()

    close_res = manager.close()
    assert close_res["success"] is True
    assert not manager.is_running()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_navigate_and_extract_wikipedia():
    """Verify navigating to Wikipedia extracts non-empty, dense Markdown content."""
    manager = PlaywrightManager()
    res = manager.navigate("https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert res["success"] is True
    assert "Python" in res["title"]

    extract_res = manager.extract_markdown(max_chars=4000)
    assert extract_res["success"] is True
    assert len(extract_res["content"]) > 100
    assert "Python" in extract_res["content"]
    assert extract_res["url"].startswith("https://en.wikipedia.org")

    manager.close()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_selector_extraction():
    """Verify extracting content from a specific DOM CSS selector."""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(
            """
            <html><body>
                <header><h1 id="main-header">Autonomous Browser System</h1></header>
                <div id="target-card">
                    <h2>Patent Prior Art Summary</h2>
                    <p>Magnetic quick-release bicycle fender with dual neodymium locks.</p>
                </div>
                <div id="ignored-ad">Commercial Advertisement</div>
            </body></html>
            """
        )
        f_path = f.name

    try:
        manager = PlaywrightManager()
        manager.navigate(f"file://{f_path}")

        card_extract = manager.extract_markdown(selector="#target-card")
        assert card_extract["success"] is True
        assert "Patent Prior Art Summary" in card_extract["content"]
        assert "Commercial Advertisement" not in card_extract["content"]

        header_extract = manager.extract_markdown(selector="#main-header")
        assert header_extract["success"] is True
        assert "Autonomous Browser System" in header_extract["content"]

        manager.close()
    finally:
        if os.path.exists(f_path):
            os.remove(f_path)


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_interactive_form_and_click():
    """Verify fill_input and click_element trigger DOM mutations and events."""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(
            """
            <html><body>
                <h1>Interactive Search Test</h1>
                <input id="search-input" type="text" />
                <button id="search-btn" onclick="
                    var val = document.getElementById('search-input').value;
                    var res = document.getElementById('search-results');
                    res.innerHTML = '<p class=hit>Found result for: ' + val + '</p>';
                ">Search</button>
                <div id="search-results">No queries executed</div>
            </body></html>
            """
        )
        f_path = f.name

    try:
        manager = PlaywrightManager()
        manager.navigate(f"file://{f_path}")

        # Fill input and click submit
        fill_res = manager.fill_input("#search-input", "neodymium magnetic clamp")
        assert fill_res["success"] is True

        click_res = manager.click_element("#search-btn")
        assert click_res["success"] is True

        # Verify updated DOM text
        results = manager.extract_markdown(selector="#search-results")
        assert results["success"] is True
        assert "Found result for: neodymium magnetic clamp" in results["content"]

        manager.close()
    finally:
        if os.path.exists(f_path):
            os.remove(f_path)


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_screenshot():
    """Verify capturing page screenshot and generating preview and disk file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        manager = PlaywrightManager(screenshot_dir=tmp_dir)
        manager.navigate("https://example.com")

        shot = manager.take_page_screenshot(full_page=False)
        assert shot["status"] == "success"
        assert shot["saved_to"] is not None
        assert os.path.exists(shot["saved_to"])
        assert shot["base64_preview"].startswith("data:") or len(shot["base64_preview"]) > 10

        manager.close()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_evaluate_js():
    """Verify arbitrary JavaScript execution on the active page."""
    manager = PlaywrightManager()
    manager.navigate("https://example.com")

    res = manager.evaluate_js("document.title + ' - evaluated'")
    assert res["success"] is True
    assert "Example Domain - evaluated" == res["result"]

    calc_res = manager.evaluate_js("21 * 2")
    assert calc_res["success"] is True
    assert calc_res["result"] == 42

    manager.close()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_browser_error_handling():
    """Verify graceful error reporting when page is not open or element not found."""
    manager = PlaywrightManager()
    # Not navigated yet
    res = manager.extract_markdown()
    assert res["success"] is False
    assert "no active browser session" in res["error"].lower()

    manager.navigate("https://example.com")
    missing_el = manager.extract_markdown(selector="#nonexistent-id-xyz")
    assert missing_el["success"] is False
    assert "not found" in missing_el["error"].lower()

    manager.close()


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright is not installed")
def test_agent_loop_browser_tools_dispatch():
    """Verify AgentLoop correctly routes browser tool calls to PlaywrightManager."""
    from eidos.core.loop import AgentLoop
    from eidos.models.base import ToolCall
    from eidos.safety.gatekeeper import Gatekeeper
    from eidos.safety.kill_switch import KillSwitch

    kill_switch = KillSwitch()
    gatekeeper = Gatekeeper(kill_switch=kill_switch)
    loop = AgentLoop(
        config={"browser": {"headless": True}},
        gatekeeper=gatekeeper,
        kill_switch=kill_switch,
    )

    # 1. browser_open
    res, stop = loop._dispatch_tool(ToolCall(name="browser_open", arguments={"url": "https://example.com"}))
    assert res["success"] is True
    assert "Example Domain" in res["title"]
    assert not stop

    # 2. browser_extract
    res, stop = loop._dispatch_tool(ToolCall(name="browser_extract", arguments={"max_chars": 500}))
    assert res["success"] is True
    assert "domain" in res["content"].lower()

    # 3. browser_evaluate
    res, stop = loop._dispatch_tool(ToolCall(name="browser_evaluate", arguments={"script": "100 + 23"}))
    assert res["success"] is True
    assert res["result"] == 123

    # 4. browser_close
    res, stop = loop._dispatch_tool(ToolCall(name="browser_close", arguments={}))
    assert res["success"] is True
