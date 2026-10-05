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
from eidos.core.tool_registry import ToolRegistry
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine
from eidos.jobs import BatchJobRunner, SkillJobProcessor
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

# Desktop tasks chain perception, shell, and GUI actions. 15 steps was
# exhausted before many of those tasks could finish.
DEFAULT_MAX_STEPS = 50


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

        self.windows = WindowDetector()

        # Integrations
        opt_cfg = self.config.get("integrations", {}).get("optimalx", {})
        self.optimalx = OptimalXBridge(
            config_path=opt_cfg.get("config_path"),
            db_path=opt_cfg.get("db_path"),
            notex_dir=opt_cfg.get("notex_dir"),
            cdp_port=opt_cfg.get("cdp_port", 9222),
            window_detector=self.windows,
        )

        self.ollama = OllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            reasoning_model=local_cfg.get("reasoning_model", "batiai/gemma4-26b:iq4"),
            vision_model=local_cfg.get("vision_model", "batiai/gemma4-12b:q6"),
            timeout=local_cfg.get("timeout_seconds", 60.0),
        )

        # Active Recall Interceptor (mid-stream <recall> query splicing)
        self.recall_interceptor: Optional[RecallStreamInterceptor] = None
        if (self.memory_enabled and self.episodic_store) or self.optimalx:
            self.recall_interceptor = RecallStreamInterceptor(
                episodic_store=self.episodic_store,
                optimalx_bridge=self.optimalx,
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
        self.active_skill: Optional[Dict[str, Any]] = None
        self.job_processor = SkillJobProcessor(
            model_router=self.router,
            web_research_engine=self.web,
            browser_manager=self.browser,
            optimalx_bridge=self.optimalx,
            config=self.config,
        )
        jobs_cfg = self.config.get("jobs", {})
        self.jobs = BatchJobRunner(
            skills_manager=self.skills,
            default_jobs_dir=jobs_cfg.get("default_output_dir"),
            default_processor=self.job_processor.process_item,
            kill_switch=self.kill_switch,
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
            "on_conversation_changed": [],
        }
        self.conversation_history: List[Dict[str, Any]] = []
        self.last_turn_trace: Dict[str, Any] = {}

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
        title = "New Chat"
        if self.conversation_store and self.active_conversation_id:
            self.conversation_store.clear_conversation_messages(self.active_conversation_id)
            conv = self.conversation_store.get_conversation(self.active_conversation_id)
            if conv:
                title = conv.get("title", "New Chat")
        self._emit("on_conversation_changed", self.active_conversation_id, title)

    def cancel_task(self) -> None:
        """Request graceful cancellation of the running agent task."""
        logger.info("AgentLoop task cancellation requested.")
        self._stop_requested.set()
        try:
            if self.voice:
                self.voice.stop_speaking()
        except Exception:
            pass
        try:
            if hasattr(self, "jobs") and self.jobs and self.jobs.is_worker_running():
                self.jobs.stop_background_worker(timeout=2.0)
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
        self._emit("on_conversation_changed", self.active_conversation_id, title)
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
        title = conv.get("title", "New Chat")
        self._emit("on_conversation_title_changed", title)
        self._emit("on_conversation_changed", conversation_id, title)
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
                self.recall_interceptor.optimalx = self.optimalx
            elif (self.memory_enabled and self.episodic_store) or self.optimalx:
                self.recall_interceptor = RecallStreamInterceptor(
                    episodic_store=self.episodic_store,
                    optimalx_bridge=self.optimalx,
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

        if "integrations" in new_config and "optimalx" in new_config["integrations"]:
            opt_cfg = new_config["integrations"]["optimalx"]
            self.optimalx = OptimalXBridge(
                config_path=opt_cfg.get("config_path"),
                db_path=opt_cfg.get("db_path"),
                notex_dir=opt_cfg.get("notex_dir"),
                cdp_port=opt_cfg.get("cdp_port", 9222),
                window_detector=self.windows,
            )
            if self.recall_interceptor:
                self.recall_interceptor.optimalx = self.optimalx
            if hasattr(self, "job_processor") and self.job_processor:
                self.job_processor.optimalx = self.optimalx

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
        """Stop TTS, request loop cancellation, and halt batch workers immediately when emergency kill fires."""
        self._stop_requested.set()
        try:
            if self.voice:
                self.voice.stop_speaking()
        except Exception:
            pass
        try:
            if hasattr(self, "jobs") and self.jobs:
                self.jobs._on_kill_switch_triggered()
        except Exception:
            pass

    def _should_auto_speak(self) -> bool:
        """Return True when voice output is enabled and auto-speak is on."""
        voice_cfg = self.config.get("voice", {})
        if not voice_cfg.get("enabled", True):
            return False
        return bool(voice_cfg.get("auto_speak_responses", True))

    def _speak_task_summary(self, text: str, speak_summary: bool = True) -> None:
        """Speak a chat reply / task summary using progressive TTS clips."""
        if not speak_summary:
            return
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

    def _compact_context_if_needed(self, messages: List[Dict[str, Any]], max_chars: int = 48000) -> None:
        """Prune older tool observations to prevent prompt context bloat on long multi-step tasks."""
        total_chars = sum(len(m.get("content") or "") for m in messages)
        if total_chars <= max_chars:
            return

        # Compact older tool messages (keep the most recent 2 tool messages untouched)
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_indices) > 2:
            older_tool_indices = tool_indices[:-2]
            for idx in older_tool_indices:
                content = messages[idx].get("content") or ""
                if len(content) > 1200:
                    tool_name = messages[idx].get("name", "tool")
                    messages[idx]["content"] = (
                        content[:600]
                        + f"\n... [Historical observation from {tool_name} pruned to conserve context ({len(content)} chars total)]"
                    )

    def _assemble_system_prompt(
        self,
        active_win: Dict[str, Any],
        user_profile_ctx: Optional[str] = None,
        episodic_ctx: Optional[str] = None,
    ) -> str:
        """Construct lean system prompt and append active skill procedure if scoped."""
        prompt = build_system_prompt(
            active_win,
            os.getcwd(),
            clock=self.clock,
            user_profile_context=user_profile_ctx,
            episodic_context=episodic_ctx,
            workspace_dir=self.workspace_dir,
        )
        if self.active_skill:
            skill_name = self.active_skill.get("name", "unnamed-skill")
            skill_body = (self.active_skill.get("body") or "").strip()
            prompt += f"\n\n---\nACTIVE SKILL: {skill_name}\n{skill_body}\n---"
        return prompt

    def _assemble_active_tools(self) -> List[Dict[str, Any]]:
        """Return base tools or scoped tools unlocked by active skill."""
        if self.active_skill:
            req_tools = self.active_skill.get("tools_required") or []
            return ToolRegistry.get_scoped_tools(req_tools)
        return ToolRegistry.get_base_tools()

    def _resolve_max_steps(self, max_steps: Optional[int]) -> int:
        """Use the caller's cap, otherwise agent.max_steps from config."""
        if max_steps is not None:
            try:
                value = int(max_steps)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        configured = self.config.get("agent", {}).get("max_steps", DEFAULT_MAX_STEPS)
        try:
            value = int(configured)
        except (TypeError, ValueError):
            value = DEFAULT_MAX_STEPS
        return value if value > 0 else DEFAULT_MAX_STEPS

    @staticmethod
    def _compact_trace_text(value: Any, limit: int) -> str:
        """Flatten a thought, argument payload, or tool result for the chat record."""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, default=str, ensure_ascii=False)
            except Exception:
                text = str(value)
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _trace_action(self, name: str, arguments: Any, result: Any) -> Dict[str, str]:
        return {
            "tool": name or "tool",
            "arguments": self._compact_trace_text(arguments, 240),
            "result": self._compact_trace_text(result, 400),
        }

    def _format_attempt_detail(
        self,
        headline: str,
        thoughts: List[str],
        actions: List[Dict[str, str]],
    ) -> str:
        """Build the assistant turn stored after an unfinished attempt.

        The next user message replays this text, so the model can see what it
        already thought and which tools it already ran.
        """
        shown_thoughts = [t for t in thoughts if (t or "").strip()][-12:]
        shown_actions = actions[-24:]
        lines = [
            headline.strip(),
            "",
            "This attempt already ran. Continue from the progress below instead of starting over.",
        ]
        if len(actions) > len(shown_actions):
            lines.append(
                f"(Showing the last {len(shown_actions)} of {len(actions)} actions.)"
            )
        if shown_thoughts:
            lines.append("")
            lines.append("Reasoning:")
            for index, thought in enumerate(shown_thoughts, 1):
                lines.append(f"{index}. {self._compact_trace_text(thought, 500)}")
        if shown_actions:
            lines.append("")
            lines.append("Actions already taken:")
            for index, action in enumerate(shown_actions, 1):
                lines.append(
                    f"{index}. {action.get('tool')} {action.get('arguments')} → {action.get('result')}"
                )
        if not shown_thoughts and not shown_actions:
            lines.append("")
            lines.append("No tool actions or reasoning notes were recorded before the attempt stopped.")
        text = "\n".join(lines).strip()
        if len(text) > 8000:
            text = text[:7900] + "\n… [earlier progress truncated]"
        return text

    def _remember_turn(
        self,
        content: str,
        *,
        headline: str,
        thoughts: List[str],
        actions: List[Dict[str, str]],
        outcome: str,
    ) -> None:
        """Save the assistant turn for the next model call and for chat history."""
        public_thoughts = [self._compact_trace_text(t, 500) for t in thoughts if (t or "").strip()][-12:]
        public_actions = list(actions[-24:])
        self.last_turn_trace = {
            "headline": headline,
            "detail": content,
            "thoughts": public_thoughts,
            "actions": public_actions,
            "outcome": outcome,
        }
        if not content:
            return
        self.conversation_history.append({"role": "assistant", "content": content})
        if self.conversation_store and self.active_conversation_id:
            self.conversation_store.add_message(
                self.active_conversation_id,
                role="assistant",
                content=content,
                metadata={
                    "headline": headline,
                    "thoughts": public_thoughts,
                    "actions": public_actions,
                    "outcome": outcome,
                },
            )

    def _complete_run(
        self,
        *,
        episode_id: Optional[str],
        headline: str,
        success: bool,
        outcome: str,
        step: int,
        speak: bool,
        speak_summary: bool,
        thoughts: List[str],
        actions: List[Dict[str, str]],
        include_trace: bool,
    ) -> Dict[str, Any]:
        """Finish a task, speak a short summary, and persist what was attempted."""
        headline = (headline or "").strip() or "Task ended."
        if include_trace and not success:
            detail = self._format_attempt_detail(headline, thoughts, actions)
        else:
            detail = headline
        self._finish_and_index_episode(episode_id, outcome=outcome, final_summary=headline)
        if speak:
            self._speak_task_summary(headline, speak_summary=speak_summary)
        self._remember_turn(
            detail,
            headline=headline,
            thoughts=thoughts,
            actions=actions,
            outcome=outcome,
        )
        self._emit("on_task_finish", headline, success)
        return {
            "success": success,
            "summary": headline,
            "detail": detail,
            "steps_taken": step,
            "thoughts": list(self.last_turn_trace.get("thoughts") or []),
            "actions": list(self.last_turn_trace.get("actions") or []),
        }

    def run_task(
        self,
        user_goal: str,
        max_steps: Optional[int] = None,
        speak_summary: bool = True,
    ) -> Dict[str, Any]:
        """Run autonomous agent loop until goal achieved or max_steps reached."""
        max_steps = self._resolve_max_steps(max_steps)
        logger.info("Starting EidosBot task: %s", user_goal)
        self._stop_requested.clear()
        self.active_skill = None
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
        system_prompt = self._assemble_system_prompt(
            active_win,
            user_profile_ctx=user_profile_ctx,
            episodic_ctx=episodic_ctx,
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
        turn_thoughts: List[str] = []
        turn_actions: List[Dict[str, str]] = []

        while step < max_steps:
            step += 1
            step_start_time = time.monotonic()
            logger.info("=== Cycle Step %d / %d ===", step, max_steps)
            self._emit("on_step", step, max_steps)

            # 1. Sense emergency kill switch or user cancellation
            if self.kill_switch.is_triggered():
                logger.warning("Agent loop stopped by kill switch at step %d", step)
                return self._complete_run(
                    episode_id=episode_id,
                    headline=f"Aborted by emergency kill switch at step {step}.",
                    success=False,
                    outcome="aborted",
                    step=step,
                    speak=False,
                    speak_summary=speak_summary,
                    thoughts=turn_thoughts,
                    actions=turn_actions,
                    include_trace=True,
                )
            if self._stop_requested.is_set():
                logger.warning("Agent loop stopped by user request at step %d", step)
                return self._complete_run(
                    episode_id=episode_id,
                    headline="Task stopped by user.",
                    success=False,
                    outcome="aborted",
                    step=step,
                    speak=False,
                    speak_summary=speak_summary,
                    thoughts=turn_thoughts,
                    actions=turn_actions,
                    include_trace=True,
                )

            # Refresh system context (date/time, active window, user profile, active skill) each step
            active_win = self.windows.get_active_window_info()
            if self.memory_enabled:
                user_profile_ctx = self.user_store.get_prompt_context()
            messages[0]["content"] = self._assemble_system_prompt(
                active_win,
                user_profile_ctx=user_profile_ctx,
                episodic_ctx=episodic_ctx,
            )

            state_snapshot = {
                "active_window": active_win.get("title", "Unknown"),
                "active_window_class": active_win.get("class_name", "Unknown"),
                "cwd": os.getcwd(),
                "geometry": active_win.get("geometry", {}),
                "clock": self.clock.snapshot().to_dict(),
                "active_skill": self.active_skill.get("name") if self.active_skill else None,
            }

            # Prune context if older tool outputs are accumulating
            self._compact_context_if_needed(messages, max_chars=48000)

            # Assemble scoped tools based on active skill
            active_tools = self._assemble_active_tools()

            # 2. Reason: Call model
            response = self.model.chat(messages=messages, tools=active_tools)

            if response.thinking:
                logger.info("[Thinking]: %s", response.thinking)
                self._emit("on_thought", response.thinking)
                turn_thoughts.append(response.thinking)

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

            # 3. Check if done without tool calls or encountered model error
            if not response.has_tool_calls:
                is_model_error = bool(
                    response.content
                    and (
                        response.content.startswith("Error communicating with")
                        or "429 Client Error" in response.content
                        or "Rate limit" in response.content
                        or "Read timed out" in response.content
                        or "timed out" in response.content.lower()
                    )
                )

                if is_model_error and step < max_steps:
                    logger.warning("Model communication error encountered on step %d: %s", step, response.content)
                    # Attempt recovery: aggressively compact context and retry with Chutes if available
                    self._compact_context_if_needed(messages, max_chars=24000)
                    chutes_client = getattr(getattr(self, "router", None), "chutes", None)
                    if chutes_client and chutes_client.is_configured:
                        logger.info("Retrying step %d with cloud model Chutes AI...", step)
                        # Pop the error assistant message from messages so we don't pollute context
                        if messages and messages[-1].get("role") == "assistant":
                            messages.pop()
                        try:
                            retry_response = chutes_client.chat(messages=messages, tools=active_tools)
                            if retry_response and (
                                retry_response.has_tool_calls
                                or (retry_response.content and not retry_response.content.startswith("Error communicating with"))
                            ):
                                response = retry_response
                                assistant_msg = {"role": "assistant", "content": response.content}
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
                                is_model_error = False
                                if response.thinking and (
                                    not turn_thoughts or turn_thoughts[-1] != response.thinking
                                ):
                                    logger.info("[Thinking]: %s", response.thinking)
                                    self._emit("on_thought", response.thinking)
                                    turn_thoughts.append(response.thinking)
                        except Exception as retry_exc:
                            logger.error("Step %d retry with Chutes also encountered error: %s", step, retry_exc)

                if not response.has_tool_calls:
                    if response.content:
                        task_summary = response.content
                        self._emit("on_response", task_summary)
                        self._speak_task_summary(task_summary, speak_summary=speak_summary)
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

            # Plans that ride along with a tool call are not a final answer, but they
            # are the reasoning the chat and the next turn need if this attempt stops.
            note = (response.content or "").strip()
            if note and note not in {item.strip() for item in turn_thoughts}:
                self._emit("on_thought", note)
                turn_thoughts.append(note)

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
                try:
                    tool_result, is_finish = self._dispatch_tool(tc)
                except Exception as exc:
                    logger.error("Exception dispatching tool '%s': %s", tc.name, exc, exc_info=True)
                    tool_result = {
                        "success": False,
                        "error": f"Tool '{tc.name}' execution failed with exception: {exc}",
                    }
                    is_finish = False
                self._emit("on_tool_end", tc.name, tool_result)
                executed_observations.append(tool_result)
                turn_actions.append(self._trace_action(tc.name, tc.arguments, tool_result))
                if is_finish:
                    task_summary = tool_result.get("summary", "Task completed.")
                    task_finished = True
                    self._emit("on_response", task_summary)

                # Append tool result to conversation history with observation capping
                raw_tool_content = json.dumps(tool_result)
                if len(raw_tool_content) > 20000:
                    truncated_notice = (
                        f"\n... [Output truncated: showing first 14,000 and last 3,000 of {len(raw_tool_content)} total characters. "
                        f"Tip: Use execute_shell with a python script to process full data directly into files.]"
                    )
                    capped_content = raw_tool_content[:14000] + truncated_notice + raw_tool_content[-3000:]
                else:
                    capped_content = raw_tool_content

                messages.append({
                    "role": "tool",
                    "content": capped_content,
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
                stopped = self._stop_requested.is_set() or self.kill_switch.is_triggered()
                return self._complete_run(
                    episode_id=episode_id,
                    headline=task_summary or ("Task stopped by user." if stopped else "Task completed."),
                    success=not stopped,
                    outcome="aborted" if stopped else "success",
                    step=step,
                    speak=not stopped,
                    speak_summary=speak_summary,
                    thoughts=turn_thoughts,
                    actions=turn_actions,
                    include_trace=stopped,
                )

            time.sleep(0.1)

        is_model_error = bool(
            task_summary
            and (
                task_summary.startswith("Error communicating with")
                or "429 Client Error" in task_summary
                or "Rate limit" in task_summary
            )
        )
        if task_summary and not is_model_error:
            return self._complete_run(
                episode_id=episode_id,
                headline=task_summary,
                success=True,
                outcome="success",
                step=step,
                speak=False,
                speak_summary=speak_summary,
                thoughts=turn_thoughts,
                actions=turn_actions,
                include_trace=False,
            )

        if is_model_error:
            headline = task_summary
            outcome = "failed"
        elif step >= max_steps:
            headline = f"Reached the step limit ({max_steps}) before the task was finished."
            outcome = "max_steps"
        else:
            headline = "The attempt stopped without a final answer."
            outcome = "failed"
        return self._complete_run(
            episode_id=episode_id,
            headline=headline,
            success=False,
            outcome=outcome,
            step=step,
            speak=not is_model_error,
            speak_summary=speak_summary,
            thoughts=turn_thoughts,
            actions=turn_actions,
            include_trace=True,
        )

    def _dispatch_tool(self, tool_call: ToolCall) -> tuple[Dict[str, Any], bool]:
        """Dispatch a single tool call to the action or perception engine."""
        name = tool_call.name
        args = tool_call.arguments
        logger.info("Dispatching tool '%s' with args: %s", name, args)

        if name == "finish_task":
            self.active_skill = None
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

        # OptimalX Knowledge Ecosystem & Safe Mutation Engine
        if name == "optimalx_search_notes":
            query = args.get("query", "")
            limit = int(args.get("limit", 5))
            parent_folder = args.get("parent_folder") or args.get("parent_filter")
            subfolder = args.get("subfolder")
            prefer_offline = bool(args.get("prefer_offline", False))
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_search_notes",
                {"query": query, "limit": limit, "parent_folder": parent_folder, "prefer_offline": prefer_offline}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            results = self.optimalx.search_notes(
                query=query,
                limit=limit,
                parent_folder=parent_folder,
                parent_filter=parent_folder,
                subfolder=subfolder,
                prefer_offline=prefer_offline,
            )
            return {"query": query, "count": len(results), "results": results}, False

        if name == "optimalx_read_note":
            subfolder_id = args.get("subfolder_id")
            title = args.get("title")
            note_id = args.get("note_id")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_read_note",
                {"subfolder_id": subfolder_id, "title": title, "note_id": note_id}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.read_note(
                subfolder_id=int(subfolder_id) if subfolder_id is not None else None,
                note_id=int(note_id) if note_id is not None else None,
                title=title,
            )
            if res is None:
                return {"error": "Note not found"}, False
            return res, False

        if name == "optimalx_list_folders":
            parent_folder_id = args.get("parent_folder_id")
            include_subfolders = bool(args.get("include_subfolders", True))
            query = args.get("query")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_list_folders",
                {"parent_folder_id": parent_folder_id, "include_subfolders": include_subfolders, "query": query}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.list_folders(
                parent_folder_id=int(parent_folder_id) if parent_folder_id is not None else None,
                include_subfolders=include_subfolders,
                query=query,
            )
            return {"count": len(res), "folders": res}, False

        if name == "optimalx_write_note":
            content = args.get("content", "")
            subfolder_id = args.get("subfolder_id")
            subfolder_title = args.get("subfolder_title")
            parent_name = args.get("parent_name")
            parent_folder_id = args.get("parent_folder_id")
            mode = args.get("mode", "auto")
            allow_direct_append = bool(args.get("allow_direct_append", False))
            label = args.get("label")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_write_note",
                {
                    "content": content[:100] + "..." if len(content) > 100 else content,
                    "subfolder_id": subfolder_id,
                    "subfolder_title": subfolder_title,
                    "parent_name": parent_name,
                    "mode": mode,
                }
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.write_note(
                content=content,
                subfolder_id=int(subfolder_id) if subfolder_id is not None else None,
                subfolder_title=subfolder_title,
                parent_name=parent_name,
                parent_folder_id=int(parent_folder_id) if parent_folder_id is not None else None,
                mode=mode,
                allow_direct_append=allow_direct_append,
                label=label,
                conversation_id=None,
            )
            return res, False

        if name == "optimalx_read_notex_doc":
            subfolder_id = args.get("subfolder_id")
            doc_path = args.get("doc_path", "")
            start_line = args.get("start_line")
            end_line = args.get("end_line")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_read_notex_doc",
                {"subfolder_id": subfolder_id, "doc_path": doc_path}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.read_notex_doc(
                subfolder_id=int(subfolder_id),
                doc_path=doc_path,
                start_line=int(start_line) if start_line is not None else None,
                end_line=int(end_line) if end_line is not None else None,
            )
            return res, False

        if name == "optimalx_list_notex_docs":
            subfolder_id = args.get("subfolder_id")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_list_notex_docs",
                {"subfolder_id": subfolder_id}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.list_notex_docs(subfolder_id=int(subfolder_id))
            return res, False

        if name == "optimalx_list_pending_reviews":
            subfolder_id = args.get("subfolder_id")
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_list_pending_reviews",
                {"subfolder_id": subfolder_id}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            items = self.optimalx.list_pending_reviews(
                subfolder_id=int(subfolder_id) if subfolder_id is not None else None
            )
            return {"count": len(items), "pending_reviews": items}, False

        if name == "optimalx_accept_pending_review":
            item_id = int(args.get("item_id", 0))
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_accept_pending_review",
                {"item_id": item_id}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.accept_pending_review(item_id=item_id)
            return res, False

        if name == "optimalx_reject_pending_review":
            item_id = int(args.get("item_id", 0))
            allowed, reason = self.gatekeeper.evaluate(
                "optimalx_reject_pending_review",
                {"item_id": item_id}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.optimalx.reject_pending_review(item_id=item_id)
            return res, False

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

        if name == "share_file":
            path = args.get("path") or args.get("target") or ""
            note = args.get("note") or args.get("caption") or ""
            allowed, reason = self.gatekeeper.evaluate("share_file", {"path": path, "note": note})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            sharer = getattr(self, "file_share", None)
            if sharer is None or not hasattr(sharer, "register"):
                return {
                    "success": False,
                    "error": "The web gateway is not running, so this file cannot be sent to the web app.",
                }, False
            try:
                rec = sharer.register(
                    path,
                    direction="bot",
                    conversation_id=str(self.active_conversation_id or ""),
                    note=str(note or ""),
                )
            except Exception as exc:
                return {"success": False, "error": str(exc)}, False
            return {
                "success": True,
                "already_shared": bool(rec.get("already_shared")),
                "id": rec.get("id"),
                "name": rec.get("name"),
                "path": rec.get("path"),
                "message": "The file is now in the web app for the user to view and download.",
            }, False

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
            self.active_skill = res
            tools_unlocked = res.get("tools_required") or []
            logger.info("Activated skill '%s' with %d scoped tools", res.get("name"), len(tools_unlocked))
            return {
                "success": True,
                "status": "activated",
                "name": res.get("name"),
                "skill": res.get("name"),
                "tools_unlocked": tools_unlocked,
                "message": f"Skill '{res.get('name')}' is now active with {len(tools_unlocked)} specialized tools unlocked.",
            }, False

        if name == "close_skill":
            if not self.active_skill:
                return {"success": True, "message": "No skill is currently active. Operating in base kernel mode."}, False
            prev_name = self.active_skill.get("name", "skill")
            self.active_skill = None
            logger.info("Closed active skill '%s'. Returned to base kernel mode.", prev_name)
            return {
                "success": True,
                "status": "closed",
                "previous_skill": prev_name,
                "message": f"Skill '{prev_name}' closed. Returned to base kernel mode.",
            }, False

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

        if name == "run_batch_job":
            job_id = args.get("job_id")
            background = args.get("background", True)
            if isinstance(background, str):
                background = background.lower() in ("true", "1", "yes")
            pause_sec = args.get("pause_between_items_sec")
            if pause_sec is not None:
                try:
                    pause_sec = float(pause_sec)
                except (ValueError, TypeError):
                    pause_sec = None
            max_items = args.get("max_items")
            if max_items is not None:
                try:
                    max_items = int(max_items)
                except (ValueError, TypeError):
                    max_items = None

            allowed, reason = self.gatekeeper.evaluate(
                "run_batch_job",
                {"job_id": job_id, "background": background, "pause_sec": pause_sec, "max_items": max_items}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            if background:
                res = self.jobs.run_in_background(
                    job_id_or_path=job_id,
                    pause_between_items_sec=pause_sec,
                    max_items=max_items,
                )
            else:
                res = self.jobs.run_job(
                    job_id_or_path=job_id,
                    pause_between_items_sec=pause_sec,
                    max_items=max_items,
                    kill_switch_checker=self.kill_switch.is_triggered,
                    stop_event=self._stop_requested,
                )
            return res, False

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

        if name == "generate_batch_summary":
            job_id = args.get("job_id")
            allowed, reason = self.gatekeeper.evaluate("generate_batch_summary", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.generate_summary(job_id_or_path=job_id)
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
