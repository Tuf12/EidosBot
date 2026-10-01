# EidosBot Headless Browser Automation (Playwright & CDP)

This document defines the architecture, tool specifications, and integration design for **EidosBot's Headless Browser Automation Subsystem** powered by Playwright and Chrome DevTools Protocol (CDP).

---

## 1. Motivation: Why Replace Visual Clicking for the Web?

### 1.1. The Failure of Mouse/Keyboard Web Navigation
Attempting to automate a web browser by taking full desktop screenshots, calculating pixel coordinates, and sending synthetic mouse clicks (`pyautogui`/`pynput`) suffers from severe failure modes:
- **Resolution & Offset Drift:** Window resizing, zoom levels, and multi-monitor offsets easily misalign click targets.
- **Occlusion & Popups:** Cookie consent modals, dropdowns, and sticky headers block expected targets.
- **Latency:** Capturing the screen, encoding base64, querying a vision LLM, and generating click coordinates takes 2–5 seconds per click.
- **Zero Structural Insight:** The agent sees pixels, but cannot read the underlying DOM, hidden attributes, or text inside scrollable `<div>` containers.

### 1.2. The Limitations of Static Search APIs (Brave / DuckDuckGo)
While EidosBot includes `web_search` (Brave API) and `web_fetch` (`httpx`), these fail on modern dynamic websites:
- **Client-Side Rendering (SPAs):** Single Page Apps built with React, Vue, or Next.js return an empty HTML shell without executing client-side JavaScript.
- **Interactive Workflows:** Searching patent databases (Google Patents, Espacenet), filtering e-commerce catalogs (Amazon, Kickstarter), or navigating paginated results requires interacting with page state.

---

## 2. Playwright Subsystem Architecture (`eidos/browser/`)

By embedding **Playwright**, EidosBot operates directly on the browser's Document Object Model (DOM) and network stack:

```
┌────────────────────────────────────────────────────────────────────────┐
│                      EIDOS PLAYWRIGHT SUBSYSTEM                        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   Eidos Core Loop (Ollama / Local LLM)                                 │
│        │                                                               │
│        ├──▶ browser_open("https://patents.google.com/?q=bicycle+fender")│
│        ├──▶ browser_click("button#search")                             │
│        ├──▶ browser_extract(selector=".search-results", format="md")    │
│        └──▶ browser_screenshot()                                       │
│                                                                        │
│                                │                                       │
│                                ▼                                       │
│                   ┌─────────────────────────┐                          │
│                   │ PlaywrightManager       │                          │
│                   │ (Async Chromium Engine) │                          │
│                   └────────────┬────────────┘                          │
│                                │                                       │
│         ┌──────────────────────┴──────────────────────┐                │
│         ▼                                             ▼                │
│   Headless Mode (Default)                       Headed Mode (Visual)   │
│   • Runs silently in background                 • Opens visible Chrome │
│   • Zero desktop screen clutter                 • User can watch live  │
│   • Perfect for batch jobs                      • Useful for debugging │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.1. Key Advantages over Visual GUI Clicking
1. **100% Deterministic Locators:** Uses resilient CSS selectors (`page.locator("button:has-text('Search')")`), roles, and XPaths.
2. **Full JavaScript Execution:** Executes SPAs, waits for dynamic network calls to settle (`networkidle`), and evaluates custom JS.
3. **Clean Content Extraction:** Converts live page DOM into clean, token-efficient Markdown, stripping out ads, navigation menus, and boilerplate trackers.
4. **Session & Cookie Persistence:** Keeps authenticated states or search sessions across multiple steps without logging in repeatedly.

---

## 3. Browser Tool Specifications

EidosBot exposes a dedicated suite of browser tools to the LLM context:

### 3.1. Tool Definitions

| Tool Name | Parameters | Description |
|---|---|---|
| `browser_open` | `url`, `headless` (default: true) | Launches Chromium context (or reuses active tab) and navigates to the target URL. |
| `browser_extract` | `selector` (optional), `max_chars` (default: 8000) | Extracts clean, readable Markdown content from the current page or specific element. |
| `browser_click` | `selector` or `text` | Clicks an element deterministically using CSS selector or text matcher. |
| `browser_type` | `selector`, `text`, `press_enter` (bool) | Types text into an input field and optionally submits the form. |
| `browser_screenshot` | `full_page` (bool) | Captures a high-resolution PNG render of the webpage itself (not the desktop) for multimodal inspection. |
| `browser_evaluate` | `script` | Executes arbitrary JavaScript within the page context to query data or scroll. |
| `browser_close` | None | Terminates active browser instance and cleans up memory. |

### 3.2. Concrete Execution Example

When researching prior art for an invention:

```json
// Step 1: Open Google Patents
{
  "name": "browser_open",
  "arguments": {
    "url": "https://patents.google.com/?q=magnetic+quick+release+bicycle+fender"
  }
}

// Step 2: Extract top 3 search results
{
  "name": "browser_extract",
  "arguments": {
    "selector": "section#results",
    "max_chars": 4000
  }
}
```

The agent immediately receives structured patent titles, publication numbers, and abstract snippets—completely bypassing coordinate math or screen capture.

---

## 4. Technical Implementation Details (`eidos/browser/`)

### 4.1. `PlaywrightManager` Class (`eidos/browser/manager.py`)
```python
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import trafilatura

class PlaywrightManager:
    """Manages headless browser lifecycle, page navigation, and DOM extraction."""

    def __init__(self, headless: bool = True, user_agent: Optional[str] = None):
        self.headless = headless
        self.user_agent = user_agent
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self):
        if not self._playwright:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=self.user_agent
            )
            self._page = await self._context.new_page()

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        await self.start()
        response = await self._page.goto(url, wait_until=wait_until, timeout=30000)
        title = await self._page.title()
        return {
            "url": self._page.url,
            "title": title,
            "status": response.status if response else 200
        }

    async def extract_content(self, selector: Optional[str] = None) -> str:
        if not self._page:
            return "No active browser session."
        
        if selector:
            element = await self._page.query_selector(selector)
            if element:
                html = await element.inner_html()
            else:
                return f"Selector '{selector}' not found."
        else:
            html = await self._page.content()

        # Clean HTML into dense readable markdown using trafilatura
        extracted = trafilatura.extract(html, output_format="markdown") or ""
        return extracted[:8000]

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
```

---

## 5. Synergy with the 300 Inventions Batch Job

During large-scale batch tasks:
- **Headless Mode** operates silently in the background without stealing mouse focus or opening distracting windows on the user's desktop.
- **Rate-Limiting & Caching:** Playwright responses can be cached locally in `~/.eidos/cache/web/` so repeated queries for the same product category do not hit external servers repeatedly.
- **Zero Screen Clutter:** The user can continue coding, playing games, or writing documents on their Linux Mint desktop while EidosBot browses the web autonomously in the background.
