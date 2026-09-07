"""OptimalX Desktop 1.0 Electron & API Bridge."""

import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import websockets

from eidos.perception.window_info import WindowDetector

logger = logging.getLogger(__name__)


class OptimalXBridge:
    """
    Bridge for interacting with OptimalX Desktop 1.0 via:
    1. Local HTTP Sync & LLM API (port 7373)
    2. Chrome DevTools Protocol (CDP) WebSocket (port 9222)
    3. X11 Window Activation & Navigation
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        cdp_port: int = 9222,
        window_detector: Optional[WindowDetector] = None,
    ):
        self.config_path = Path(
            os.path.expanduser(config_path or "~/.config/optimalx-desktop/config.json")
        )
        self.cdp_port = cdp_port
        self.windows = window_detector or WindowDetector()
        self._cached_config: Optional[Dict[str, Any]] = None

    def load_app_config(self) -> Dict[str, Any]:
        """Load OptimalX Desktop internal config (contains syncToken and port)."""
        if self._cached_config is not None:
            return self._cached_config

        if not self.config_path.exists():
            return {}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._cached_config = json.load(f)
                return self._cached_config
        except Exception as e:
            logger.warning("Failed to load OptimalX config from %s: %s", self.config_path, e)
            return {}

    @property
    def sync_port(self) -> int:
        return self.load_app_config().get("syncPort", 7373)

    @property
    def sync_token(self) -> str:
        return self.load_app_config().get("syncToken", "")

    def is_running(self) -> bool:
        """Check if OptimalX Desktop is running."""
        # 1. Check active or open windows via xprop/xdotool
        try:
            res = subprocess.run(
                ["xdotool", "search", "--class", "optimalx-desktop"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return True
        except Exception:
            pass

        # 2. Fallback: Check sync port
        try:
            resp = requests.get(f"http://127.0.0.1:{self.sync_port}/api/v1/sync/status", timeout=1.0)
            return resp.status_code in (200, 401, 403)
        except Exception:
            return False

    def get_window_id(self) -> Optional[int]:
        """Find the X11 window ID for OptimalX Desktop."""
        try:
            res = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", "optimalx-desktop"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                ids = res.stdout.strip().split()
                if ids:
                    return int(ids[0])
        except Exception as e:
            logger.debug("Failed to find OptimalX window ID: %s", e)
        return None

    def focus_window(self) -> bool:
        """Bring OptimalX Desktop window to the foreground."""
        wid = self.get_window_id()
        if wid:
            return self.windows.activate_window(wid)
        return False

    def get_sync_status(self) -> Dict[str, Any]:
        """Query OptimalX sync server for database statistics and counts."""
        token = self.sync_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"http://127.0.0.1:{self.sync_port}/api/v1/sync/status"

        try:
            resp = requests.get(url, headers=headers, timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("OptimalX get_sync_status error: %s", e)
            return {"error": str(e), "running": False}

    def get_llm_status(self) -> Dict[str, Any]:
        """Query OptimalX LLM server status."""
        token = self.sync_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"http://127.0.0.1:{self.sync_port}/api/v1/llm/status"

        try:
            resp = requests.get(url, headers=headers, timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("OptimalX get_llm_status error: %s", e)
            return {"error": str(e), "running": False}

    # =========================================================================
    # Chrome DevTools Protocol (CDP) WebSocket Engine
    # =========================================================================

    def is_cdp_available(self) -> bool:
        """Check if CDP remote debugging port is reachable."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

    def get_cdp_targets(self) -> List[Dict[str, Any]]:
        """List active CDP page and webview targets."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json", timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("Failed to list CDP targets: %s", e)
        return []

    def get_main_page_ws_url(self) -> Optional[str]:
        """Return the WebSocket debugger URL for the main OptimalX renderer."""
        targets = self.get_cdp_targets()
        for t in targets:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl")
                if ws_url:
                    return ws_url
        return None

    def evaluate_js_sync(self, expression: str, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Evaluate JavaScript inside the OptimalX Electron renderer via CDP WebSocket.
        Returns execution result or error dictionary.
        """
        import asyncio

        return asyncio.run(self.evaluate_js(expression, timeout=timeout))

    async def evaluate_js(self, expression: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Async evaluation of JavaScript via CDP WebSocket."""
        ws_url = self.get_main_page_ws_url()
        if not ws_url:
            return {
                "success": False,
                "error": f"CDP port {self.cdp_port} not reachable. Start OptimalX with --remote-debugging-port={self.cdp_port}",
            }

        req_id = int(time.time() * 1000) % 1000000
        payload = {
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }

        try:
            async with websockets.connect(ws_url) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    msg = json.loads(raw_msg)
                    if msg.get("id") == req_id:
                        result = msg.get("result", {})
                        if "exceptionDetails" in result:
                            return {
                                "success": False,
                                "error": result["exceptionDetails"].get("text", "JS Exception"),
                            }
                        return {
                            "success": True,
                            "value": result.get("result", {}).get("value"),
                        }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def query_dom_element(self, selector: str) -> Dict[str, Any]:
        """
        Find an element by CSS selector in OptimalX and return bounding rect and text.
        """
        js = f"""
        (() => {{
            const el = document.querySelector("{selector}");
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {{
                tag: el.tagName,
                id: el.id,
                className: el.className,
                innerText: (el.innerText || "").slice(0, 100),
                rect: {{
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                }}
            }};
        }})()
        """
        return self.evaluate_js_sync(js)

    def click_dom_element(self, selector: str) -> Dict[str, Any]:
        """Trigger native .click() on element in OptimalX DOM."""
        js = f"""
        (() => {{
            const el = document.querySelector("{selector}");
            if (!el) return {{ clicked: false, error: "Element not found" }};
            el.click();
            return {{ clicked: true }};
        }})()
        """
        return self.evaluate_js_sync(js)
