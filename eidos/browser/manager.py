"""Headless Chromium browser automation manager powered by Playwright and trafilatura."""

import asyncio
import base64
from datetime import datetime
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None
    BrowserContext = None
    Page = None
    async_playwright = None

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

logger = logging.getLogger(__name__)


class PlaywrightManager:
    """Manages headless browser lifecycle, page navigation, and DOM extraction.

    Runs Playwright inside a dedicated background worker thread with its own
    asyncio event loop. This ensures thread-safety across desktop HUD threads,
    CLI loops, and background jobs without Playwright thread affinity conflicts.
    """

    _instance: Optional["PlaywrightManager"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls, **kwargs) -> "PlaywrightManager":
        """Singleton accessor for PlaywrightManager."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """Close and reset singleton instance (useful for clean test isolation)."""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
                cls._instance = None

    def __init__(
        self,
        headless: bool = True,
        user_agent: Optional[str] = None,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        screenshot_dir: str = "~/.eidos/screenshots/browser",
    ):
        self.headless = headless
        self.user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 EidosBot/1.0"
        )
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.screenshot_dir = os.path.abspath(os.path.expanduser(screenshot_dir))

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._active_headless: Optional[bool] = None
        self._loop_lock = threading.Lock()

    def _ensure_loop(self):
        """Ensure the dedicated background event loop and thread are running."""
        with self._loop_lock:
            if self._loop is None or not self._loop.is_running():
                ready_event = threading.Event()

                def _thread_target():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._loop = loop
                    ready_event.set()
                    loop.run_forever()

                self._thread = threading.Thread(
                    target=_thread_target,
                    name="EidosPlaywrightWorker",
                    daemon=True,
                )
                self._thread.start()
                ready_event.wait(timeout=5.0)

    def _run_coro(self, coro, timeout: float = 35.0):
        """Execute a coroutine in the dedicated Playwright thread and block for result."""
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _async_start(self, headless: Optional[bool] = None):
        """Initialize Playwright and launch Chromium in dedicated thread."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright is not installed. Please run:\n"
                "pip install -r requirements.txt && playwright install chromium"
            )

        target_headless = self.headless if headless is None else headless

        # If browser already running with identical headless mode and page open, reuse
        if (
            self._browser is not None
            and self._page is not None
            and not self._page.is_closed()
            and self._active_headless == target_headless
        ):
            return

        # If mode changed or page closed, teardown existing browser first
        await self._async_close()

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=target_headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._active_headless = target_headless
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            user_agent=self.user_agent,
        )
        self._page = await self._context.new_page()

    async def _async_navigate(
        self,
        url: str,
        headless: Optional[bool] = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """Navigate active page to URL."""
        await self._async_start(headless=headless)

        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://", "file://", "about:")):
            clean_url = f"https://{clean_url}"

        try:
            response = await self._page.goto(clean_url, wait_until=wait_until, timeout=timeout_ms)
            title = await self._page.title()
            status_code = response.status if response else 200
            return {
                "success": True,
                "url": self._page.url,
                "title": title,
                "status": status_code,
            }
        except Exception as e:
            logger.warning("Failed to navigate to %s: %s", clean_url, e)
            return {
                "success": False,
                "url": clean_url,
                "error": str(e),
            }

    async def _async_extract_markdown(
        self,
        selector: Optional[str] = None,
        max_chars: int = 8000,
    ) -> Dict[str, Any]:
        """Extract clean Markdown from the active page or selector."""
        if not self._page or self._page.is_closed():
            return {
                "success": False,
                "error": "No active browser session. Call browser_open first.",
            }

        try:
            if selector:
                element = await self._page.query_selector(selector)
                if not element:
                    return {
                        "success": False,
                        "error": f"Element selector '{selector}' not found on active page.",
                    }
                html = await element.inner_html()
            else:
                html = await self._page.content()

            title = await self._page.title()
            current_url = self._page.url

            markdown_text = ""
            if TRAFILATURA_AVAILABLE and html:
                try:
                    markdown_text = trafilatura.extract(html, output_format="markdown") or ""
                except Exception as e:
                    logger.debug("Trafilatura extraction exception: %s", e)

            # Fallback if trafilatura extracts nothing (e.g. dynamic catalogs, short lists, forms)
            if not markdown_text.strip() and html:
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    for tag in soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    markdown_text = soup.get_text(separator="\n", strip=True)
                except Exception as e:
                    logger.debug("BeautifulSoup fallback extraction exception: %s", e)

            clean_md = markdown_text.strip()
            truncated_md = clean_md[:max_chars]

            return {
                "success": True,
                "url": current_url,
                "title": title,
                "content": truncated_md,
                "char_count": len(truncated_md),
                "truncated": len(clean_md) > max_chars,
            }
        except Exception as e:
            logger.warning("Content extraction failed: %s", e)
            return {"success": False, "error": str(e)}

    async def _async_click(
        self,
        selector: str,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        """Click element by CSS or text selector."""
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active browser session. Call browser_open first."}

        try:
            await self._page.click(selector, timeout=timeout_ms)
            # Short wait for any micro-DOM response
            await self._page.wait_for_timeout(200)
            title = await self._page.title()
            return {
                "success": True,
                "selector": selector,
                "url": self._page.url,
                "title": title,
            }
        except Exception as e:
            logger.warning("Browser click failed on '%s': %s", selector, e)
            return {"success": False, "selector": selector, "error": str(e)}

    async def _async_fill_input(
        self,
        selector: str,
        text: str,
        press_enter: bool = False,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        """Fill an input or textarea element with text and optionally press Enter."""
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active browser session. Call browser_open first."}

        try:
            await self._page.fill(selector, text, timeout=timeout_ms)
            if press_enter:
                await self._page.press(selector, "Enter")
                await self._page.wait_for_timeout(300)
            return {
                "success": True,
                "selector": selector,
                "text": text,
                "pressed_enter": press_enter,
                "url": self._page.url,
            }
        except Exception as e:
            logger.warning("Browser fill failed on '%s': %s", selector, e)
            return {"success": False, "selector": selector, "error": str(e)}

    async def _async_take_screenshot(
        self,
        full_page: bool = False,
        save_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture webpage screenshot as PNG and base64 preview."""
        if not self._page or self._page.is_closed():
            return {"status": "error", "error": "No active browser session. Call browser_open first."}

        try:
            screenshot_bytes = await self._page.screenshot(full_page=full_page)
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            # Determine storage location
            dest_file = save_path
            if not dest_file:
                os.makedirs(self.screenshot_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
                dest_file = os.path.join(self.screenshot_dir, f"browser_{ts}.png")

            try:
                Path(dest_file).parent.mkdir(parents=True, exist_ok=True)
                with open(dest_file, "wb") as f:
                    f.write(screenshot_bytes)
            except Exception as e:
                logger.debug("Could not write browser screenshot to %s: %s", dest_file, e)
                dest_file = None

            title = await self._page.title()
            return {
                "status": "success",
                "url": self._page.url,
                "title": title,
                "width": self.viewport_width,
                "height": self.viewport_height,
                "full_page": full_page,
                "saved_to": dest_file,
                "base64_preview": b64[:50] + "...",
            }
        except Exception as e:
            logger.warning("Browser screenshot failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def _async_evaluate_js(self, script: str) -> Dict[str, Any]:
        """Evaluate JavaScript expression in page context."""
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active browser session. Call browser_open first."}

        try:
            result = await self._page.evaluate(script)
            return {"success": True, "result": result}
        except Exception as e:
            logger.warning("Browser JS evaluation failed: %s", e)
            return {"success": False, "error": str(e)}

    async def _async_close(self) -> Dict[str, Any]:
        """Close page, context, browser, and playwright instance."""
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._active_headless = None
        return {"success": True, "message": "Browser closed successfully"}

    # =========================================================================
    # Synchronous Public Interface (Thread-safe from any caller thread)
    # =========================================================================

    def is_running(self) -> bool:
        """Return True if a browser page is currently active."""
        if not self._page:
            return False
        try:
            return not self._page.is_closed()
        except Exception:
            return False

    def navigate(
        self,
        url: str,
        headless: Optional[bool] = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """Launch (if needed) and navigate to URL."""
        return self._run_coro(
            self._async_navigate(
                url=url,
                headless=headless,
                wait_until=wait_until,
                timeout_ms=timeout_ms,
            ),
            timeout=float(timeout_ms) / 1000.0 + 10.0,
        )

    def extract_markdown(
        self,
        selector: Optional[str] = None,
        max_chars: int = 8000,
    ) -> Dict[str, Any]:
        """Extract cleaned Markdown text from the current page or specified element."""
        return self._run_coro(
            self._async_extract_markdown(selector=selector, max_chars=max_chars),
            timeout=20.0,
        )

    def click_element(
        self,
        selector: str,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        """Click element matching selector."""
        return self._run_coro(
            self._async_click(selector=selector, timeout_ms=timeout_ms),
            timeout=float(timeout_ms) / 1000.0 + 5.0,
        )

    def fill_input(
        self,
        selector: str,
        text: str,
        press_enter: bool = False,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        """Fill input element with text."""
        return self._run_coro(
            self._async_fill_input(
                selector=selector,
                text=text,
                press_enter=press_enter,
                timeout_ms=timeout_ms,
            ),
            timeout=float(timeout_ms) / 1000.0 + 5.0,
        )

    def take_page_screenshot(
        self,
        full_page: bool = False,
        save_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture webpage screenshot as base64 and optional file."""
        return self._run_coro(
            self._async_take_screenshot(full_page=full_page, save_path=save_path),
            timeout=20.0,
        )

    def evaluate_js(
        self,
        script: str,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        """Execute arbitrary JavaScript code on the active page."""
        return self._run_coro(
            self._async_evaluate_js(script=script),
            timeout=float(timeout_ms) / 1000.0 + 5.0,
        )

    def close(self) -> Dict[str, Any]:
        """Close browser instance and clean up resources."""
        return self._run_coro(self._async_close(), timeout=10.0)
