"""Core Sense -> Reason -> Confirm -> Act loop for EidosBot."""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from eidos.actions.mouse_keyboard import InputSimulator
from eidos.actions.shell import ShellExecutor
from eidos.browser.manager import PlaywrightManager
from eidos.core.clock import EidosClock
from eidos.core.context import EIDOS_TOOLS, build_system_prompt
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine
from eidos.jobs.runner import BatchJobRunner
from eidos.memory.conversation_store import ConversationStore
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.recall_stream import RecallStreamInterceptor
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import StepRecord
from eidos.memory.user_store import UserStore
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
from eidos.skills.manager import SkillManager
from eidos.voice.voice_manager import VoiceManager

logger = logging.getLogger(__name__)


class AgentLoop:
    """Orchestrates perception, reasoning, safety gatekeeping, and action dispatch."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        kill_switch: Optional[KillSwitch] = None,
        gatekeeper: Optional[Gatekeeper] = None,
        model_client: Optional[BaseModelClient] = None,
        voice_manager: Optional[VoiceManager] = None,
        user_store: Optional[UserStore] = None,
        trajectory_logger: Optional[TrajectoryLogger] = None,
        episodic_store: Optional[EpisodicStore] = None,
        conversation_store: Optional[ConversationStore] = None,
    ):
        self.config = config or {}
        self.kill_switch = kill_switch or KillSwitch()
        self.allowlist = AllowlistManager(
            storage_path=self.config.get("safety", {}).get("storage", {}).get("allowlist_file")
        )
        self.audit = AuditLogger(
            log_path=self.config.get("safety", {}).get("storage", {}).get("audit_log_file")
        )
        mem_cfg_early = self.config.get("memory", {})
        self.gatekeeper = gatekeeper or Gatekeeper(
            allowlist=self.allowlist,
            audit_logger=self.audit,
            kill_switch=self.kill_switch,
            confirm_level_1_ui=self.config.get("safety", {}).get("confirm_level_1_ui", False),
            confirm_level_2_shell=self.config.get("safety", {}).get("confirm_level_2_shell", True),
            confirm_profile_writes=mem_cfg_early.get("confirm_profile_writes", True),
        )

        local_cfg = self.config.get("models", {}).get("local", {})
        cloud_cfg = self.config.get("models", {}).get("cloud", {})
        pref = self.config.get("models", {}).get("provider_preference", "local")

        # Memory Subsystem (User Profile, Trajectory Recording, Episodic Store, & Conversation Persistence)
        mem_cfg = mem_cfg_early
        self.memory_enabled = mem_cfg.get("enabled", True)
        self.user_store = user_store or UserStore(
            storage_path=mem_cfg.get("user_profile_file")
        )
        self.trajectory_logger = trajectory_logger or TrajectoryLogger(
            storage_path=mem_cfg.get("trajectories_file")
        )
        base_ollama_url = local_cfg.get("base_url", "http://localhost:11434")
        emb_model = mem_cfg.get("embedding_model", "nomic-embed-text:latest")
        vector_db_path = mem_cfg.get("vector_store_path") or mem_cfg.get("episodic_db_path")
        self.episodic_store: Optional[EpisodicStore] = episodic_store or (
            EpisodicStore(
                db_path=vector_db_path,
                base_url=base_ollama_url,
                embedding_model=emb_model,
            )
            if self.memory_enabled
            else None
        )
        if episodic_store is None and self.memory_enabled and self.episodic_store:
            traj_f = mem_cfg.get("trajectories_file")
            if traj_f:
                try:
                    self.episodic_store.sync_from_trajectory_file(traj_f)
                except Exception as e:
                    logger.debug("Startup trajectory sync skipped: %s", e)
        conv_db_path = mem_cfg.get("conversations_db_path") or mem_cfg.get("conversation_db_path")
        self.conversation_store: Optional[ConversationStore] = conversation_store or (
            ConversationStore(db_path=conv_db_path) if self.memory_enabled else None
        )
        self.active_conversation_id: Optional[str] = None
        if self.conversation_store:
            self.active_conversation_id = self.conversation_store.get_or_create_active_conversation_id()

        self.ollama = OllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            reasoning_model=local_cfg.get("reasoning_model", "batiai/gemma4-26b:iq4"),
            vision_model=local_cfg.get("vision_model", "batiai/gemma4-12b:q6"),
            timeout=local_cfg.get("timeout_seconds", 60.0),
        )

        # Active Recall Interceptor (mid-stream <recall> query splicing)
        self.recall_interceptor: Optional[RecallStreamInterceptor] = None
        if self.memory_enabled and self.episodic_store:
            self.recall_interceptor = RecallStreamInterceptor(
                episodic_store=self.episodic_store,
                max_recalls_per_turn=int(mem_cfg.get("max_recalls_per_turn", 3)),
                min_similarity=float(mem_cfg.get("recall_min_similarity", 0.20)),
            )
            self.ollama.recall_interceptor = self.recall_interceptor
            self.ollama.on_recall_callback = self._on_recall_triggered

        self.chutes = ChutesClient(
            api_key=cloud_cfg.get("api_key"),
            base_url=cloud_cfg.get("base_url", "https://llm.chutes.ai/v1"),
            vision_model=cloud_cfg.get("vision_model", "Qwen/Qwen3.5-397B-A17B-TEE"),
            reasoning_model=cloud_cfg.get("reasoning_model", "moonshotai/Kimi-K2.6-TEE"),
            timeout=cloud_cfg.get("timeout_seconds", 90.0),
            stream=cloud_cfg.get("stream", True),
            recall_interceptor=self.recall_interceptor,
            on_recall_callback=self._on_recall_triggered if self.recall_interceptor else None,
        )

        self.router = ModelRouter(ollama_client=self.ollama, chutes_client=self.chutes, preference=pref)
        self.model = model_client or self.router
        if self.recall_interceptor:
            if hasattr(self.model, "recall_interceptor") and getattr(self.model, "recall_interceptor", None) is None:
                self.model.recall_interceptor = self.recall_interceptor
            if hasattr(self.model, "on_recall_callback"):
                self.model.on_recall_callback = self._on_recall_triggered

        self.screen = ScreenCapture(
            scale_factor=self.config.get("perception", {}).get("screen_scale_factor", 1.0)
        )
        self.windows = WindowDetector()
        clock_cfg = self.config.get("clock", {})
        self.clock = EidosClock(timezone_name=clock_cfg.get("timezone"))
        self.grounder = VisualGrounder(model_router=self.router)

        # Workspace directory configuration
        sandbox_cfg = self.config.get("sandbox", {})
        self.workspace_dir = os.path.abspath(
            os.path.expanduser(sandbox_cfg.get("default_workspace", "~/EidosWorkspace"))
        )
        self.downloads_dir = os.path.join(self.workspace_dir, "downloads")
        self.documents_dir = os.path.join(self.workspace_dir, "documents")
        try:
            os.makedirs(self.workspace_dir, exist_ok=True)
            os.makedirs(self.downloads_dir, exist_ok=True)
            os.makedirs(self.documents_dir, exist_ok=True)
            os.makedirs(os.path.join(self.workspace_dir, "jobs"), exist_ok=True)
            os.makedirs(os.path.join(self.workspace_dir, "skills"), exist_ok=True)
        except Exception as e:
            logger.warning("Could not pre-create workspace directories: %s", e)

        self.shell = ShellExecutor(
            gatekeeper=self.gatekeeper,
            sandbox_config=self.config.get("sandbox"),
        )
        self.inputs = InputSimulator(gatekeeper=self.gatekeeper)

        # Integrations
        opt_cfg = self.config.get("integrations", {}).get("optimalx", {})
        self.optimalx = OptimalXBridge(
            cdp_port=opt_cfg.get("cdp_port", 9222),
            window_detector=self.windows
        )
        self.web = WebResearchEngine(model_router=self.router)

        # Headless Browser Subsystem (Playwright)
        browser_cfg = self.config.get("browser", {})
        self.browser = PlaywrightManager.get_instance(
            headless=browser_cfg.get("headless", True),
            user_agent=browser_cfg.get("user_agent"),
            screenshot_dir=browser_cfg.get("screenshot_dir", "~/.eidos/screenshots/browser"),
        )

        # Skills & Batch Jobs Subsystem
        skills_cfg = self.config.get("skills", {})
        self.skills = SkillManager(
            skills_dir=skills_cfg.get("directory"),
            project_skills_dir=skills_cfg.get("project_directory"),
        )
        jobs_cfg = self.config.get("jobs", {})
        self.jobs = BatchJobRunner(
            skills_manager=self.skills,
            default_jobs_dir=jobs_cfg.get("default_output_dir"),
        )

        # Voice Subsystem
        voice_cfg = self.config.get("voice", {})
        self.voice = voice_manager or VoiceManager(
            python_path=voice_cfg.get("python_path"),
            voice_dir=voice_cfg.get("optimalx_voice_dir"),
            default_voice=voice_cfg.get("tts_voice", "bm_george"),
            default_speed=float(voice_cfg.get("tts_speed", 1.0)),
            device=voice_cfg.get("device", "cpu"),
            hotkey_str=voice_cfg.get("push_to_talk_hotkey"),
        )
        try:
            self.kill_switch.register_callback(self._on_kill_stop_voice)
        except Exception:
            logger.debug("Could not attach voice barge-in to kill switch.")

        # Callbacks & conversational memory
        self._stop_requested = threading.Event()
        self._callbacks: Dict[str, List[Any]] = {
            "on_task_start": [],
            "on_step": [],
            "on_thought": [],
            "on_recall": [],
            "on_tool_start": [],
            "on_tool_end": [],
            "on_response": [],
            "on_task_finish": [],
            "on_conversation_title_changed": [],
        }
        self.conversation_history: List[Dict[str, Any]] = []

        # Restore past turns from active conversation if persisted
        if self.conversation_store and self.active_conversation_id:
            past_messages = self.conversation_store.get_messages(self.active_conversation_id)
            for pm in past_messages:
                role = (pm.get("role") or "").lower()
                if role in ("user", "assistant"):
                    self.conversation_history.append({"role": role, "content": pm.get("content", "")})

    def register_callback(self, event_name: str, callback: Any):
        """Register an observer callback for execution events."""
        if event_name in self._callbacks:
            self._callbacks[event_name].append(callback)

    def _emit(self, event_name: str, *args, **kwargs):
        """Invoke all registered callbacks for an event."""
        for cb in self._callbacks.get(event_name, []):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                logger.warning("Error in callback %s: %s", event_name, e)

    def clear_history(self):
        """Clear conversational turn history and stored messages for active conversation."""
        self.conversation_history.clear()
        if self.conversation_store and self.active_conversation_id:
            self.conversation_store.clear_conversation_messages(self.active_conversation_id)

    def cancel_task(self) -> None:
        """Request graceful cancellation of the running agent task."""
        logger.info("AgentLoop task cancellation requested.")
        self._stop_requested.set()
        try:
            if self.voice:
                self.voice.stop_speaking()
        except Exception:
            pass

    def is_stop_requested(self) -> bool:
        """Return True if cancellation has been requested for the running task."""
        return self._stop_requested.is_set()

    def start_new_conversation(self, title: str = "New Chat") -> str:
        """Create a fresh conversation thread, reset context history, and set active."""
        if self.conversation_store:
            new_id = self.conversation_store.create_conversation(title=title)
            self.active_conversation_id = new_id
        else:
            self.active_conversation_id = None
        self.conversation_history.clear()
        self._emit("on_conversation_title_changed", title)
        return self.active_conversation_id or ""

    def switch_conversation(self, conversation_id: str) -> bool:
        """Switch active conversation, loading its prior turns into loop context."""
        if not self.conversation_store:
            return False
        conv = self.conversation_store.get_conversation(conversation_id)
        if not conv:
            return False
        self.active_conversation_id = conversation_id
        self.conversation_history.clear()
        messages = self.conversation_store.get_messages(conversation_id)
        for m in messages:
            role = (m.get("role") or "").lower()
            if role in ("user", "assistant"):
                self.conversation_history.append({"role": role, "content": m.get("content", "")})
        self._emit("on_conversation_title_changed", conv.get("title", "New Chat"))
        return True

    def rename_conversation(self, conversation_id: str, new_title: str) -> bool:
        """Rename an existing conversation."""
        if not self.conversation_store:
            return False
        self.conversation_store.update_conversation_title(conversation_id, new_title)
        if self.active_conversation_id == conversation_id:
            self._emit("on_conversation_title_changed", new_title)
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete conversation from persistence. If active, switch to next available."""
        if not self.conversation_store:
            return False
        deleted = self.conversation_store.delete_conversation(conversation_id)
        if self.active_conversation_id == conversation_id:
            recent = self.conversation_store.get_most_recent_conversation()
            if recent:
                self.switch_conversation(recent["id"])
            else:
                self.start_new_conversation()
        return deleted

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

        # Update Sandbox settings
        sandbox_cfg = new_config.get("sandbox")
        if sandbox_cfg is not None:
            self.shell.configure_sandbox(sandbox_cfg)

        # Update Browser settings
        browser_cfg = new_config.get("browser", {})
        if browser_cfg and hasattr(self, "browser") and self.browser:
            if "headless" in browser_cfg:
                self.browser.headless = bool(browser_cfg["headless"])
            if "user_agent" in browser_cfg:
                self.browser.user_agent = browser_cfg["user_agent"]
            if "screenshot_dir" in browser_cfg:
                self.browser.screenshot_dir = os.path.abspath(os.path.expanduser(browser_cfg["screenshot_dir"]))

        # Update Skills & Jobs settings
        skills_cfg = new_config.get("skills", {})
        if skills_cfg and hasattr(self, "skills") and self.skills:
            if "directory" in skills_cfg:
                from pathlib import Path
                self.skills.skills_dir = Path(os.path.abspath(os.path.expanduser(skills_cfg["directory"])))
        jobs_cfg = new_config.get("jobs", {})
        if jobs_cfg and hasattr(self, "jobs") and self.jobs:
            if "default_output_dir" in jobs_cfg:
                from pathlib import Path
                self.jobs.default_jobs_dir = Path(os.path.abspath(os.path.expanduser(jobs_cfg["default_output_dir"])))

        # Update Voice settings
        voice_cfg = new_config.get("voice", {})
        if voice_cfg:
            from eidos.voice.tts_service import resolve_speed
            from eidos.voice.voices import normalize_voice_id

            if "tts_voice" in voice_cfg:
                self.voice.tts.default_voice = normalize_voice_id(voice_cfg["tts_voice"])
            if "tts_speed" in voice_cfg:
                self.voice.tts.default_speed = resolve_speed(float(voice_cfg["tts_speed"]))

        # Update Memory settings
        mem_cfg = new_config.get("memory", {})
        if mem_cfg:
            self.memory_enabled = mem_cfg.get("enabled", self.memory_enabled)
            if "user_profile_file" in mem_cfg:
                self.user_store = UserStore(storage_path=mem_cfg["user_profile_file"])
            if "trajectories_file" in mem_cfg:
                self.trajectory_logger = TrajectoryLogger(storage_path=mem_cfg["trajectories_file"])
            if "vector_store_path" in mem_cfg or "episodic_db_path" in mem_cfg or "embedding_model" in mem_cfg:
                base_ollama_url = self.config.get("models", {}).get("local", {}).get("base_url", "http://localhost:11434")
                emb_model = mem_cfg.get("embedding_model", "nomic-embed-text:latest")
                db_p = mem_cfg.get("vector_store_path") or mem_cfg.get("episodic_db_path")
                self.episodic_store = EpisodicStore(
                    db_path=db_p,
                    base_url=base_ollama_url,
                    embedding_model=emb_model,
                )
            if self.recall_interceptor:
                self.recall_interceptor.episodic_store = self.episodic_store
            elif self.memory_enabled and self.episodic_store:
                self.recall_interceptor = RecallStreamInterceptor(
                    episodic_store=self.episodic_store,
                    max_recalls_per_turn=int(mem_cfg.get("max_recalls_per_turn", 3)),
                    min_similarity=float(mem_cfg.get("recall_min_similarity", 0.20)),
                )
                self.ollama.recall_interceptor = self.recall_interceptor
                self.ollama.on_recall_callback = self._on_recall_triggered
            if "confirm_profile_writes" in mem_cfg:
                self.gatekeeper.confirm_profile_writes = bool(mem_cfg["confirm_profile_writes"])
            if "conversations_db_path" in mem_cfg or "conversation_db_path" in mem_cfg:
                conv_db_p = mem_cfg.get("conversations_db_path") or mem_cfg.get("conversation_db_path")
                self.conversation_store = ConversationStore(db_path=conv_db_p)
                self.active_conversation_id = self.conversation_store.get_or_create_active_conversation_id()

        logger.info("AgentLoop successfully reconfigured with updated settings.")

    def _on_recall_triggered(self, query: str, memory_text: str):
        """Handle mid-stream conscious active recall event."""
        logger.info("[Active Recall]: Intercepted self-query '%s'", query)
        self._emit("on_recall", query, memory_text)
        short_query = query.strip()
        self._emit("on_thought", f"<recall>{short_query}</recall>\n{memory_text.strip()}")

    def _finish_and_index_episode(
        self,
        episode_id: Optional[str],
        outcome: str = "success",
        final_summary: str = "",
        user_intervened: bool = False,
    ):
        """Finalize trajectory logging and index into vector episodic store."""
        if not self.memory_enabled or not episode_id:
            return None
        try:
            rec = self.trajectory_logger.finish_episode(
                episode_id=episode_id,
                outcome=outcome,
                final_summary=final_summary,
                user_intervened=user_intervened,
            )
            if rec and self.episodic_store:
                self.episodic_store.index_episode(rec)
            return rec
        except Exception as e:
            logger.warning("Error completing trajectory / episodic indexing: %s", e)
            return None

    def _on_kill_stop_voice(self) -> None:
        """Stop TTS immediately when the emergency kill switch fires."""
        try:
            self.voice.stop_speaking()
        except Exception:
            pass

    def _should_auto_speak(self) -> bool:
        """Return True when voice output is enabled and auto-speak is on."""
        voice_cfg = self.config.get("voice", {})
        if not voice_cfg.get("enabled", True):
            return False
        return bool(voice_cfg.get("auto_speak_responses", True))

    def _speak_task_summary(self, text: str) -> None:
        """Speak a chat reply / task summary using progressive TTS clips."""
        clean_text = (text or "").strip()
        if not clean_text or not self._should_auto_speak():
            return
        tts = getattr(getattr(self.voice, "tts", None), "is_available", True)
        if tts is False:
            logger.warning("Auto-speak skipped: Kokoro TTS is not available.")
            return
        logger.info("Speaking chat response via progressive TTS.")
        speak_response = getattr(self.voice, "speak_response", None)
        if callable(speak_response):
            speak_response(clean_text)
        else:
            self.voice.speak(clean_text, blocking=False)

    def run_task(self, user_goal: str, max_steps: int = 15) -> Dict[str, Any]:
        """Run autonomous agent loop until goal achieved or max_steps reached."""
        logger.info("Starting EidosBot task: %s", user_goal)
        self._stop_requested.clear()
        self._emit("on_task_start", user_goal)
        try:
            self.voice.stop_speaking()
        except Exception:
            pass

        # Start trajectory logging
        episode_id: Optional[str] = None
        if self.memory_enabled:
            model_name = (
                getattr(self.model, "reasoning_model", "")
                or getattr(self.router, "active_reasoning_model", "")
                or "local"
            )
            episode_id = self.trajectory_logger.start_episode(task_goal=user_goal, model_name=model_name)

        if self.kill_switch.is_triggered():
            summary = "Task aborted: Global kill switch is currently active. Reset kill switch before starting."
            self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=summary)
            self._emit("on_task_finish", summary, False)
            return {
                "success": False,
                "summary": summary,
                "steps_taken": 0,
            }

        if self._stop_requested.is_set():
            summary = "Task stopped by user."
            self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=summary)
            self._emit("on_task_finish", summary, False)
            return {
                "success": False,
                "summary": summary,
                "steps_taken": 0,
            }

        # Macro retrieval of past experience (golden path + pitfall)
        episodic_ctx = ""
        if self.memory_enabled and self.episodic_store:
            try:
                episodic_ctx = self.episodic_store.retrieve_macro_context(user_goal)
                if episodic_ctx:
                    logger.info("Macro recall retrieved episodic context for task: %s", user_goal)
            except Exception as e:
                logger.warning("Failed to retrieve macro episodic context: %s", e)

        user_profile_ctx = self.user_store.get_prompt_context() if self.memory_enabled else None
        active_win = self.windows.get_active_window_info()
        system_prompt = build_system_prompt(
            active_win,
            os.getcwd(),
            clock=self.clock,
            user_profile_context=user_profile_ctx,
            episodic_context=episodic_ctx,
            workspace_dir=self.workspace_dir,
        )

        # Construct messages including past conversational turns
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        for msg in self.conversation_history[-16:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append(msg)

        messages.append({"role": "user", "content": f"User Goal: {user_goal}"})
        self.conversation_history.append({"role": "user", "content": user_goal})
        if self.conversation_store and self.active_conversation_id:
            old_conv = self.conversation_store.get_conversation(self.active_conversation_id)
            old_title = old_conv.get("title") if old_conv else None
            self.conversation_store.add_message(self.active_conversation_id, role="user", content=user_goal)
            new_conv = self.conversation_store.get_conversation(self.active_conversation_id)
            new_title = new_conv.get("title") if new_conv else None
            if old_title != new_title and new_title:
                self._emit("on_conversation_title_changed", new_title)

        step = 0
        task_summary = ""

        while step < max_steps:
            step += 1
            step_start_time = time.monotonic()
            logger.info("=== Cycle Step %d / %d ===", step, max_steps)
            self._emit("on_step", step, max_steps)

            # 1. Sense emergency kill switch or user cancellation
            if self.kill_switch.is_triggered():
                logger.warning("Agent loop stopped by kill switch at step %d", step)
                summary = f"Aborted by emergency kill switch at step {step}."
                self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=summary)
                self._emit("on_task_finish", summary, False)
                return {
                    "success": False,
                    "summary": summary,
                    "steps_taken": step,
                }
            if self._stop_requested.is_set():
                logger.warning("Agent loop stopped by user request at step %d", step)
                summary = "Task stopped by user."
                self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=summary)
                self._emit("on_task_finish", summary, False)
                return {
                    "success": False,
                    "summary": summary,
                    "steps_taken": step,
                }

            # Refresh system context (date/time, active window, user profile) each step
            active_win = self.windows.get_active_window_info()
            if self.memory_enabled:
                user_profile_ctx = self.user_store.get_prompt_context()
            messages[0]["content"] = build_system_prompt(
                active_win,
                os.getcwd(),
                clock=self.clock,
                user_profile_context=user_profile_ctx,
                episodic_context=episodic_ctx,
                workspace_dir=self.workspace_dir,
            )

            state_snapshot = {
                "active_window": active_win.get("title", "Unknown"),
                "active_window_class": active_win.get("class_name", "Unknown"),
                "cwd": os.getcwd(),
                "geometry": active_win.get("geometry", {}),
                "clock": self.clock.snapshot().to_dict(),
            }

            # 2. Reason: Call model
            response = self.model.chat(messages=messages, tools=EIDOS_TOOLS)

            if response.thinking:
                logger.info("[Thinking]: %s", response.thinking)
                self._emit("on_thought", response.thinking)

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
                    self._emit("on_response", task_summary)
                    self._speak_task_summary(task_summary)
                if self.memory_enabled and episode_id:
                    step_rec = StepRecord(
                        step=step,
                        state_snapshot=state_snapshot,
                        model_thinking=response.thinking or "",
                        model_content=response.content or "",
                        tool_calls=[],
                        tool_observations=[],
                        duration_seconds=round(max(0.0, time.monotonic() - step_start_time), 3),
                    )
                    self.trajectory_logger.log_step(episode_id, step_rec)
                break

            # 4. Dispatch Tool Calls
            task_finished = False
            executed_tools: List[Dict[str, Any]] = []
            executed_observations: List[Dict[str, Any]] = []

            for tc in response.tool_calls:
                if self._stop_requested.is_set() or self.kill_switch.is_triggered():
                    logger.warning("Agent loop stopped before tool '%s'", tc.name)
                    task_summary = "Task stopped by user."
                    task_finished = True
                    break
                executed_tools.append({"name": tc.name, "arguments": tc.arguments})
                self._emit("on_tool_start", tc.name, tc.arguments)
                tool_result, is_finish = self._dispatch_tool(tc)
                self._emit("on_tool_end", tc.name, tool_result)
                executed_observations.append(tool_result)
                if is_finish:
                    task_summary = tool_result.get("summary", "Task completed.")
                    task_finished = True
                    self._emit("on_response", task_summary)

                # Append tool result to conversation history
                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result),
                    "name": tc.name,
                })

            if self.memory_enabled and episode_id:
                step_rec = StepRecord(
                    step=step,
                    state_snapshot=state_snapshot,
                    model_thinking=response.thinking or "",
                    model_content=response.content or "",
                    tool_calls=executed_tools,
                    tool_observations=executed_observations,
                    duration_seconds=round(max(0.0, time.monotonic() - step_start_time), 3),
                )
                self.trajectory_logger.log_step(episode_id, step_rec)

            if task_finished:
                if self._stop_requested.is_set():
                    self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=task_summary)
                    self._emit("on_task_finish", task_summary, False)
                    return {
                        "success": False,
                        "summary": task_summary,
                        "steps_taken": step,
                    }
                self._finish_and_index_episode(episode_id, outcome="success", final_summary=task_summary)
                self._speak_task_summary(task_summary)
                if task_summary:
                    self.conversation_history.append({"role": "assistant", "content": task_summary})
                    if self.conversation_store and self.active_conversation_id:
                        self.conversation_store.add_message(self.active_conversation_id, role="assistant", content=task_summary)
                self._emit("on_task_finish", task_summary, True)
                return {
                    "success": True,
                    "summary": task_summary,
                    "steps_taken": step,
                }

            time.sleep(0.1)

        if task_summary:
            self.conversation_history.append({"role": "assistant", "content": task_summary})
            if self.conversation_store and self.active_conversation_id:
                self.conversation_store.add_message(self.active_conversation_id, role="assistant", content=task_summary)
        success = bool(task_summary)
        summary = task_summary or f"Reached max steps limit ({max_steps})."
        outcome = "success" if success else "max_steps"
        self._finish_and_index_episode(episode_id, outcome=outcome, final_summary=summary)
        if not success:
            self._speak_task_summary(summary)
        self._emit("on_task_finish", summary, success)
        return {
            "success": success,
            "summary": summary,
            "steps_taken": step,
        }

    def _dispatch_tool(self, tool_call: ToolCall) -> tuple[Dict[str, Any], bool]:
        """Dispatch a single tool call to the action or perception engine."""
        name = tool_call.name
        args = tool_call.arguments
        logger.info("Dispatching tool '%s' with args: %s", name, args)

        if name == "finish_task":
            return {"status": "completed", "summary": args.get("summary", "")}, True

        if name == "update_user_profile":
            category = args.get("category", "")
            key = args.get("key", "")
            value = args.get("value", "")
            if not category or not key:
                return {"success": False, "error": "category and key are required"}, False
            gate_details = {
                "command": f"Set profile {category}.{key} = {value}",
                "category": category,
                "key": key,
                "value": value,
            }
            allowed, reason = self.gatekeeper.evaluate("update_user_profile", gate_details)
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            try:
                self.user_store.set_fact(category, key, value)
                return {"success": True, "category": category, "key": key, "value": value}, False
            except ValueError as exc:
                return {"success": False, "error": str(exc)}, False

        if name == "add_workflow_rule":
            rule = (args.get("rule") or "").strip()
            if not rule:
                return {"success": False, "error": "rule text is required"}, False
            allowed, reason = self.gatekeeper.evaluate(
                "add_workflow_rule",
                {"command": f"Add workflow rule: {rule}", "rule": rule},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            self.user_store.add_rule(rule)
            return {"success": True, "rule": rule}, False

        if name == "remove_workflow_rule":
            rule = (args.get("rule") or "").strip()
            if not rule:
                return {"success": False, "error": "rule text is required"}, False
            allowed, reason = self.gatekeeper.evaluate(
                "remove_workflow_rule",
                {"command": f"Remove workflow rule: {rule}", "rule": rule},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            removed = self.user_store.remove_rule(rule)
            return {"success": removed, "rule": rule}, False

        if name == "remove_user_fact":
            category = args.get("category", "")
            key = args.get("key", "")
            if not category or not key:
                return {"success": False, "error": "category and key are required"}, False
            allowed, reason = self.gatekeeper.evaluate(
                "remove_user_fact",
                {"command": f"Remove profile {category}.{key}", "category": category, "key": key},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            removed = self.user_store.remove_fact(category, key)
            return {"success": removed, "category": category, "key": key}, False

        if name == "execute_shell":
            cmd = args.get("command", "")
            sandboxed = args.get("sandboxed", True)
            cwd = args.get("cwd") or self.workspace_dir
            res = self.shell.execute(cmd, cwd=cwd, sandboxed=sandboxed)
            return res, False

        if name == "get_active_window":
            win_info = self.windows.get_active_window_info()
            return win_info, False

        if name == "get_current_time":
            snapshot = self.clock.snapshot()
            return {"status": "success", **snapshot.to_dict()}, False

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

        # Browser Automation Subsystem (Playwright)
        if name == "browser_open":
            url = args.get("url", "")
            headless = args.get("headless", True)
            allowed, reason = self.gatekeeper.evaluate("browser_open", {"url": url, "headless": headless})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.navigate(url, headless=headless)
            return res, False

        if name == "browser_extract":
            selector = args.get("selector")
            max_chars = int(args.get("max_chars", 8000))
            allowed, reason = self.gatekeeper.evaluate("browser_extract", {"selector": selector, "max_chars": max_chars})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.extract_markdown(selector=selector, max_chars=max_chars)
            return res, False

        if name == "browser_click":
            selector = args.get("selector", "")
            allowed, reason = self.gatekeeper.evaluate("browser_click", {"selector": selector})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.click_element(selector=selector)
            return res, False

        if name == "browser_type":
            selector = args.get("selector", "")
            text = args.get("text", "")
            press_enter = bool(args.get("press_enter", False))
            allowed, reason = self.gatekeeper.evaluate("browser_type", {"selector": selector, "text": text, "press_enter": press_enter})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.fill_input(selector=selector, text=text, press_enter=press_enter)
            return res, False

        if name == "browser_screenshot":
            full_page = bool(args.get("full_page", False))
            allowed, reason = self.gatekeeper.evaluate("browser_screenshot", {"full_page": full_page})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.take_page_screenshot(full_page=full_page)
            return res, False

        if name == "browser_evaluate":
            script = args.get("script", "")
            allowed, reason = self.gatekeeper.evaluate("browser_evaluate", {"script": script})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.browser.evaluate_js(script=script)
            return res, False

        if name == "browser_close":
            res = self.browser.close()
            return res, False

        if name == "download_file":
            url = args.get("url", "")
            filename = args.get("filename")
            dest_dir = args.get("destination_dir") or self.downloads_dir
            allowed, reason = self.gatekeeper.evaluate(
                "download_file", {"url": url, "destination_dir": dest_dir, "filename": filename}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.web.download_file(url=url, destination_path=dest_dir, filename=filename)
            return res, False

        if name == "open_in_browser":
            target = args.get("target") or args.get("url", "")
            allowed, reason = self.gatekeeper.evaluate("open_in_browser", {"target": target})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self._open_in_desktop_browser(target)
            return res, False

        # Skills Management Subsystem
        if name == "list_skills":
            skills_list = self.skills.list_skills()
            return {"skills": skills_list, "count": len(skills_list)}, False

        if name == "load_skill":
            skill_name = args.get("skill_name", "")
            res = self.skills.load_skill(skill_name)
            if not res:
                return {"success": False, "error": f"Skill '{skill_name}' not found."}, False
            return {"success": True, **res}, False

        if name == "create_skill":
            skill_name = args.get("name", "")
            content = args.get("content", "")
            scope = args.get("scope", "global")
            allowed, reason = self.gatekeeper.evaluate("create_skill", {"name": skill_name, "scope": scope})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.skills.create_skill(name=skill_name, content=content, scope=scope)
            return res, False

        if name == "update_skill":
            skill_name = args.get("name", "")
            content = args.get("content", "")
            allowed, reason = self.gatekeeper.evaluate("update_skill", {"name": skill_name})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.skills.update_skill(name=skill_name, content=content)
            return res, False

        # Batch Job Queue Subsystem
        if name == "init_batch_job":
            job_name = args.get("job_name", "")
            skill_name = args.get("skill_name", "")
            items_data = args.get("items_data", [])
            output_dir = args.get("output_dir")
            allowed, reason = self.gatekeeper.evaluate(
                "init_batch_job",
                {"job_name": job_name, "skill_name": skill_name, "output_dir": output_dir},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            try:
                queue = self.jobs.initialize_job(
                    job_name=job_name,
                    skill_name=skill_name,
                    input_data=items_data,
                    output_dir=output_dir,
                )
                return {
                    "success": True,
                    "job_id": queue.job_id,
                    "job_name": queue.job_name,
                    "skill_name": queue.skill_name,
                    "total_items": queue.total_items,
                    "output_dir": queue.output_dir,
                    "status": queue.status,
                }, False
            except Exception as e:
                return {"success": False, "error": str(e)}, False

        if name == "step_batch_job":
            job_id = args.get("job_id")
            allowed, reason = self.gatekeeper.evaluate("step_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.step_next(job_id_or_path=job_id)
            return res, False

        if name == "get_job_status":
            job_id = args.get("job_id")
            res = self.jobs.get_status(job_id_or_path=job_id)
            return res, False

        if name == "pause_batch_job":
            job_id = args.get("job_id")
            allowed, reason = self.gatekeeper.evaluate("pause_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.pause(job_id_or_path=job_id)
            return res, False

        if name == "resume_batch_job":
            job_id = args.get("job_id")
            allowed, reason = self.gatekeeper.evaluate("resume_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.resume(job_id_or_path=job_id)
            return res, False

        if name == "speak_aloud":
            msg = args.get("message", "")
            if not msg:
                return {"status": "error", "error": "Empty message"}, False
            allowed, reason = self.gatekeeper.evaluate("speak_aloud", {"message": msg})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            success = self.voice.speak(msg, blocking=False)
            return {"status": "success" if success else "failed", "spoken": msg}, False

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

    def _open_in_desktop_browser(self, target: str) -> Dict[str, Any]:
        """Launch URL or file path in the user's default desktop browser or viewer."""
        import subprocess
        import webbrowser

        clean_target = (target or "").strip()
        if not clean_target:
            return {"success": False, "error": "No target URL or path provided"}

        # Check if local file
        expanded = os.path.abspath(os.path.expanduser(clean_target))
        if os.path.exists(expanded):
            try:
                subprocess.Popen(["xdg-open", expanded], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"success": True, "path": expanded, "type": "local_file", "opened": True}
            except Exception as e:
                return {"success": False, "error": f"Failed to open with xdg-open: {e}"}

        # Otherwise treat as URL
        if not clean_target.startswith(("http://", "https://", "file://")):
            clean_target = f"https://{clean_target}"

        try:
            subprocess.Popen(["xdg-open", clean_target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "url": clean_target, "type": "url", "opened": True}
        except Exception:
            try:
                opened = webbrowser.open(clean_target)
                return {"success": opened, "url": clean_target, "type": "url", "opened": opened}
            except Exception as e:
                return {"success": False, "error": f"Failed to open URL: {e}"}
