"""Core Sense -> Reason -> Confirm -> Act loop for EidosBot."""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from eidos.actions.mouse_keyboard import InputSimulator
from eidos.actions.shell import ShellExecutor
from eidos.core.context import EIDOS_TOOLS, build_system_prompt
from eidos.integrations.cursor_ide import CursorAdapter
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine
from eidos.models.base import BaseModelClient, ToolCall
from eidos.models.chutes_client import ChutesClient
from eidos.models.ollama_client import OllamaClient
from eidos.models.router import ModelRouter
from eidos.perception.grounder import VisualGrounder
from eidos.perception.screen import ScreenCapture
from eidos.perception.window_info import WindowDetector
from eidos.safety.allowlist import AllowlistManager
from eidos.safety.audit import AuditLogger
from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class AgentLoop:
    """Orchestrates perception, reasoning, safety gatekeeping, and action dispatch."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        kill_switch: Optional[KillSwitch] = None,
        gatekeeper: Optional[Gatekeeper] = None,
        model_client: Optional[BaseModelClient] = None,
    ):
        self.config = config or {}
        self.kill_switch = kill_switch or KillSwitch()
        self.allowlist = AllowlistManager(
            storage_path=self.config.get("safety", {}).get("storage", {}).get("allowlist_file")
        )
        self.audit = AuditLogger(
            log_path=self.config.get("safety", {}).get("storage", {}).get("audit_log_file")
        )
        self.gatekeeper = gatekeeper or Gatekeeper(
            allowlist=self.allowlist,
            audit_logger=self.audit,
            kill_switch=self.kill_switch,
            confirm_level_1_ui=self.config.get("safety", {}).get("confirm_level_1_ui", False),
            confirm_level_2_shell=self.config.get("safety", {}).get("confirm_level_2_shell", True),
        )

        local_cfg = self.config.get("models", {}).get("local", {})
        cloud_cfg = self.config.get("models", {}).get("cloud", {})
        pref = self.config.get("models", {}).get("provider_preference", "local")

        self.ollama = OllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            reasoning_model=local_cfg.get("reasoning_model", "batiai/gemma4-26b:iq4"),
            vision_model=local_cfg.get("vision_model", "batiai/gemma4-12b:q6"),
            timeout=local_cfg.get("timeout_seconds", 60.0),
        )

        self.chutes = ChutesClient(
            api_key=cloud_cfg.get("api_key"),
            base_url=cloud_cfg.get("base_url", "https://llm.chutes.ai/v1"),
            vision_model=cloud_cfg.get("vision_model", "Qwen/Qwen3.5-397B-A17B-TEE"),
            fast_vision_model=cloud_cfg.get("fast_vision_model", "google/gemma-4-31B-turbo-TEE"),
            reasoning_model=cloud_cfg.get("reasoning_model", "moonshotai/Kimi-K2.6-TEE"),
            timeout=cloud_cfg.get("timeout_seconds", 90.0),
        )

        self.router = ModelRouter(ollama_client=self.ollama, chutes_client=self.chutes, preference=pref)
        self.model = model_client or self.router

        self.screen = ScreenCapture(
            scale_factor=self.config.get("perception", {}).get("screen_scale_factor", 1.0)
        )
        self.windows = WindowDetector()
        self.grounder = VisualGrounder(model_router=self.router)
        self.shell = ShellExecutor(gatekeeper=self.gatekeeper)
        self.inputs = InputSimulator(gatekeeper=self.gatekeeper)

        # Integrations
        opt_cfg = self.config.get("integrations", {}).get("optimalx", {})
        cur_cfg = self.config.get("integrations", {}).get("cursor", {})
        self.optimalx = OptimalXBridge(
            cdp_port=opt_cfg.get("cdp_port", 9222),
            window_detector=self.windows
        )
        self.cursor = CursorAdapter(
            binary_path=cur_cfg.get("binary_path", "cursor"),
            window_detector=self.windows,
            input_simulator=self.inputs
        )
        self.web = WebResearchEngine(model_router=self.router)

    def update_config(self, new_config: Dict[str, Any]):
        """Dynamically apply updated configuration to active agent components."""
        self.config = new_config

        # Update local Ollama settings
        local_cfg = new_config.get("models", {}).get("local", {})
        if local_cfg:
            self.ollama.base_url = local_cfg.get("base_url", self.ollama.base_url).rstrip("/")
            self.ollama.reasoning_model = local_cfg.get("reasoning_model", self.ollama.reasoning_model)
            self.ollama.vision_model = local_cfg.get("vision_model", self.ollama.vision_model)

        # Update cloud Chutes settings
        cloud_cfg = new_config.get("models", {}).get("cloud", {})
        if cloud_cfg:
            self.chutes.api_key = cloud_cfg.get("api_key", self.chutes.api_key)
            self.chutes.base_url = cloud_cfg.get("base_url", self.chutes.base_url).rstrip("/")
            self.chutes.vision_model = cloud_cfg.get("vision_model", self.chutes.vision_model)
            self.chutes.reasoning_model = cloud_cfg.get("reasoning_model", self.chutes.reasoning_model)

        # Update Router preference
        pref = new_config.get("models", {}).get("provider_preference")
        if pref:
            self.router.set_preference(pref)

        # Update Safety policy
        safety_cfg = new_config.get("safety", {})
        if safety_cfg:
            self.gatekeeper.confirm_level_1_ui = safety_cfg.get("confirm_level_1_ui", self.gatekeeper.confirm_level_1_ui)
            self.gatekeeper.confirm_level_2_shell = safety_cfg.get("confirm_level_2_shell", self.gatekeeper.confirm_level_2_shell)

        logger.info("AgentLoop successfully reconfigured with updated settings.")

    def run_task(self, user_goal: str, max_steps: int = 15) -> Dict[str, Any]:
        """Run autonomous agent loop until goal achieved or max_steps reached."""
        logger.info("Starting EidosBot task: %s", user_goal)

        if self.kill_switch.is_triggered():
            return {
                "success": False,
                "summary": "Task aborted: Global kill switch is currently active. Reset kill switch before starting.",
                "steps_taken": 0,
            }

        active_win = self.windows.get_active_window_info()
        system_prompt = build_system_prompt(active_win, os.getcwd())

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Goal: {user_goal}"},
        ]

        step = 0
        task_summary = ""

        while step < max_steps:
            step += 1
            logger.info("=== Cycle Step %d / %d ===", step, max_steps)

            # 1. Sense emergency kill switch
            if self.kill_switch.is_triggered():
                logger.warning("Agent loop stopped by kill switch at step %d", step)
                return {
                    "success": False,
                    "summary": f"Aborted by emergency kill switch at step {step}.",
                    "steps_taken": step,
                }

            # 2. Reason: Call model
            response = self.model.chat(messages=messages, tools=EIDOS_TOOLS)

            if response.thinking:
                logger.info("[Thinking]: %s", response.thinking)

            # Record assistant response
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": response.content}
            if response.has_tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for i, tc in enumerate(response.tool_calls)
                ]
            messages.append(assistant_msg)

            # 3. Check if done without tool calls
            if not response.has_tool_calls:
                if response.content:
                    task_summary = response.content
                break

            # 4. Dispatch Tool Calls
            task_finished = False
            for tc in response.tool_calls:
                tool_result, is_finish = self._dispatch_tool(tc)
                if is_finish:
                    task_summary = tool_result.get("summary", "Task completed.")
                    task_finished = True

                # Append tool result to conversation history
                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result),
                    "name": tc.name,
                })

            if task_finished:
                return {
                    "success": True,
                    "summary": task_summary,
                    "steps_taken": step,
                }

            time.sleep(0.1)

        return {
            "success": bool(task_summary),
            "summary": task_summary or f"Reached max steps limit ({max_steps}).",
            "steps_taken": step,
        }

    def _dispatch_tool(self, tool_call: ToolCall) -> tuple[Dict[str, Any], bool]:
        """Dispatch a single tool call to the action or perception engine."""
        name = tool_call.name
        args = tool_call.arguments
        logger.info("Dispatching tool '%s' with args: %s", name, args)

        if name == "finish_task":
            return {"status": "completed", "summary": args.get("summary", "")}, True

        if name == "execute_shell":
            cmd = args.get("command", "")
            res = self.shell.execute(cmd)
            return res, False

        if name == "get_active_window":
            win_info = self.windows.get_active_window_info()
            return win_info, False

        if name == "take_screenshot":
            try:
                img = self.screen.capture()
                b64 = ScreenCapture.to_base64(img, quality=75)
                # Return resolution and base64 preview
                return {
                    "status": "success",
                    "width": img.width,
                    "height": img.height,
                    "base64_preview": b64[:50] + "...",
                }, False
            except Exception as e:
                return {"status": "error", "error": str(e)}, False

        if name == "click_element":
            desc = args.get("target_description", "")
            button = args.get("button", "left")
            try:
                img = self.screen.capture()
                coords = self.grounder.ground(desc, img)
                if coords:
                    gx, gy = coords
                    res = self.inputs.click(x=gx, y=gy, button=button)
                    res["grounded_coords"] = [gx, gy]
                    return res, False
                else:
                    return {"success": False, "error": f"Visual grounder could not locate: '{desc}'"}, False
            except Exception as e:
                return {"success": False, "error": f"Visual grounding exception: {str(e)}"}, False

        if name == "optimalx_action":
            action = args.get("action", "").lower()
            allowed, reason = self.gatekeeper.evaluate("optimalx_action", {"action": action, **args})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            if action == "get_status":
                return self.optimalx.get_sync_status(), False
            elif action == "get_llm_status":
                return self.optimalx.get_llm_status(), False
            elif action == "focus":
                return {"focused": self.optimalx.focus_window()}, False
            elif action == "query_dom":
                selector = args.get("selector", "")
                return self.optimalx.query_dom_element(selector), False
            elif action == "click_dom":
                selector = args.get("selector", "")
                return self.optimalx.click_dom_element(selector), False
            elif action == "evaluate_js":
                expr = args.get("expression", "")
                return self.optimalx.evaluate_js_sync(expr), False
            else:
                return {"error": f"Unknown optimalx action: '{action}'"}, False

        if name == "cursor_action":
            action = args.get("action", "").lower()
            allowed, reason = self.gatekeeper.evaluate("cursor_action", {"action": action, **args})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            if action == "open_file":
                fp = args.get("file_path", "")
                line = args.get("line")
                col = args.get("col")
                return self.cursor.open_file(fp, line=line, col=col), False
            elif action == "open_workspace":
                fp = args.get("file_path", "")
                return self.cursor.open_workspace(fp), False
            elif action == "open_diff":
                fa = args.get("file_path", "")
                fb = args.get("file_b", "")
                return self.cursor.open_diff(fa, fb), False
            elif action == "command_palette":
                cmd_name = args.get("command_name", "")
                return self.cursor.execute_command_palette(cmd_name), False
            elif action == "focus":
                return {"focused": self.cursor.focus_window()}, False
            else:
                return {"error": f"Unknown cursor action: '{action}'"}, False

        if name == "web_search":
            query = args.get("query", "")
            count = int(args.get("count", 5))
            allowed, reason = self.gatekeeper.evaluate("web_search", {"query": query, "count": count})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            results = self.web.search(query, count=count)
            return {"query": query, "count": len(results), "results": results}, False

        if name == "web_fetch":
            url = args.get("url", "")
            max_chars = int(args.get("max_chars", 8000))
            allowed, reason = self.gatekeeper.evaluate("web_fetch", {"url": url, "max_chars": max_chars})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.web.fetch_webpage(url, max_chars=max_chars)
            return res, False

        if name == "click":
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            button = args.get("button", "left")
            res = self.inputs.click(x=x, y=y, button=button)
            return res, False

        if name == "double_click":
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            res = self.inputs.double_click(x=x, y=y)
            return res, False

        if name == "type_text":
            text = args.get("text", "")
            res = self.inputs.type_text(text)
            return res, False

        if name == "press_key":
            key = args.get("key", "")
            res = self.inputs.press_key(key)
            return res, False

        if name == "hotkey":
            keys = args.get("keys", [])
            res = self.inputs.hotkey(*keys)
            return res, False

        return {"error": f"Unknown tool name '{name}'"}, False
