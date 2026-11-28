"""Core Sense -> Reason -> Confirm -> Act loop for EidosBot."""

import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from eidos.actions.mouse_keyboard import InputSimulator
from eidos.actions.shell import ShellExecutor
from eidos.browser.manager import PlaywrightManager
from eidos.core.clock import EidosClock
from eidos.core.task_run import TaskRun, current_run
from eidos.core.context import EIDOS_TOOLS, build_system_prompt
from eidos.core.stall import StallTracker
from eidos.core.tool_registry import ToolRegistry
from eidos.integrations.mcp_client import MCPClientManager
from eidos.integrations.mcp_config import MCPConfigManager
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine
from eidos.jobs import BatchJobRunner, SkillJobProcessor
from eidos.memory.consolidator import EpisodicConsolidator, IdleConsolidationRunner
from eidos.memory.conversation_index import ConversationIndex
from eidos.memory.conversation_store import ConversationStore
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.recall_stream import RecallBinding, RecallStreamInterceptor, recall_binding
from eidos.memory.reflector import EpisodicReflector
from eidos.memory.scope_resolver import ScopeResolver
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import StepRecord, TaskScope
from eidos.memory.user_store import UserStore
from eidos.models.base import BaseModelClient, ToolCall
from eidos.models.chutes_client import ChutesClient
from eidos.models.meta_client import DEFAULT_META_MODEL, DEFAULT_REASONING_EFFORT, META_BASE_URL, MetaClient
from eidos.models.inflight import model_run_id
from eidos.models.ollama_client import OllamaClient
from eidos.models.compute_policy import ComputePolicy, compute_settings_from_config
from eidos.models.ollama_options import embedding_num_gpu, local_num_ctx
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

# Backstop for a loop the stall check does not recognize. Desktop work stops
# on a stall, on finish_task, or on the kill switch — not on a short step budget.
# Callers can still pass max_steps for an unattended turn (--steps, scheduled agent_turn).
DEFAULT_SAFETY_STEP_FUSE = 400


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
        reflector: Optional[EpisodicReflector] = None,
    ):
        self.config = config or {}
        self._task_runs: Dict[str, TaskRun] = {}
        self._task_runs_lock = threading.Lock()
        self._last_task_finished_time = time.monotonic()
        self.compute_policy = ComputePolicy.from_config(
            self.config,
            is_task_active=self.has_running_task,
        )
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
            confirm_level_2_shell=self.config.get("safety", {}).get("confirm_level_2_shell", False),
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
        emb_num_gpu = embedding_num_gpu(mem_cfg)
        vector_db_path = mem_cfg.get("vector_store_path") or mem_cfg.get("episodic_db_path")
        self.episodic_store: Optional[EpisodicStore] = episodic_store or (
            EpisodicStore(
                db_path=vector_db_path,
                base_url=base_ollama_url,
                embedding_model=emb_model,
                embedding_num_gpu=emb_num_gpu,
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
        self.conversation_index: Optional[ConversationIndex] = None
        self._conversation_backfill_started = False
        if self.memory_enabled and self.conversation_store and self.episodic_store:
            if vector_db_path:
                index_path = Path(os.path.expanduser(str(vector_db_path))).parent / "conversation_index.db"
            else:
                index_path = Path(self.episodic_store.db_path).parent / "conversation_index.db"
            embed_client = getattr(self.episodic_store, "embedding_client", None)
            dimension = getattr(embed_client, "dimension", None) or getattr(self.episodic_store, "dimension", 768)
            self.conversation_index = ConversationIndex(
                db_path=index_path,
                embedding_client=embed_client,
                dimension=int(dimension),
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

        # Model Context Protocol (MCP) integrations subsystem (PROJECT_JOBS.md Section 13)
        self.mcp_config = MCPConfigManager()
        self.mcp_client = MCPClientManager(config_manager=self.mcp_config)
        self.active_integration_focus: Optional[str] = None

        self.ollama = OllamaClient(
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
            reasoning_model=local_cfg.get("reasoning_model", "batiai/gemma4-26b:iq4"),
            vision_model=local_cfg.get("vision_model", "batiai/gemma4-12b:q6"),
            timeout=local_cfg.get("timeout_seconds", 60.0),
            num_ctx=local_num_ctx(local_cfg),
        )

        # Active Recall Interceptor (mid-stream <recall> query splicing)
        self.recall_interceptor: Optional[RecallStreamInterceptor] = None
        if (self.memory_enabled and self.episodic_store) or self.optimalx:
            self.recall_interceptor = RecallStreamInterceptor(
                episodic_store=self.episodic_store,
                optimalx_bridge=self.optimalx,
                max_recalls_per_turn=int(mem_cfg.get("max_recalls_per_turn", 3)),
                min_similarity=float(mem_cfg.get("recall_min_similarity", 0.20)),
                conversation_index=self.conversation_index,
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

        meta_cfg = self.config.get("models", {}).get("meta") or {}
        self.meta = MetaClient(
            api_key=meta_cfg.get("api_key") or None,
            base_url=meta_cfg.get("base_url", META_BASE_URL),
            vision_model=meta_cfg.get("vision_model", DEFAULT_META_MODEL),
            reasoning_model=meta_cfg.get("reasoning_model", DEFAULT_META_MODEL),
            reasoning_effort=meta_cfg.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
            timeout=float(meta_cfg.get("timeout_seconds", 300.0)),
            stream=meta_cfg.get("stream", True),
            recall_interceptor=self.recall_interceptor,
            on_recall_callback=self._on_recall_triggered if self.recall_interceptor else None,
        )

        self.router = ModelRouter(
            ollama_client=self.ollama,
            chutes_client=self.chutes,
            meta_client=self.meta,
            preference=pref,
            cloud_provider=cloud_cfg.get("provider", "chutes"),
            compute_policy=self.compute_policy,
        )
        self.model = model_client or self.router
        if self.recall_interceptor:
            if hasattr(self.model, "recall_interceptor") and getattr(self.model, "recall_interceptor", None) is None:
                self.model.recall_interceptor = self.recall_interceptor
            if hasattr(self.model, "on_recall_callback"):
                self.model.on_recall_callback = self._on_recall_triggered

        # Post-Task Autonomous Episodic Reflector
        refl_cfg = mem_cfg.get("reflector", {})
        self.reflector_enabled = refl_cfg.get("enabled", True)
        self.reflector: Optional[EpisodicReflector] = reflector
        if self.reflector is None and self.memory_enabled and self.reflector_enabled:
            self.reflector = EpisodicReflector(
                model_client=self.model,
                episodic_store=self.episodic_store,
                trajectory_logger=self.trajectory_logger,
                on_background_activity=self.notify_background_activity,
                enabled=self.reflector_enabled,
                model_name=refl_cfg.get("model"),
                timeout_seconds=float(refl_cfg.get("timeout_seconds", 30.0)),
                compute_policy=self.compute_policy,
                prefer_cloud_when_busy=bool(refl_cfg.get("prefer_cloud_when_busy", True)),
                idle_seconds=float(refl_cfg.get("idle_seconds", self.compute_policy.idle_seconds)),
            )

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
        self.shell.stop_checker = self.is_stop_requested
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
        self.active_job: Optional[Dict[str, Any]] = None
        self.current_task_scope: Optional[TaskScope] = None
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

        # Restore active primary job focus if persisted in focus.yaml (PROJECT_JOBS.md)
        try:
            persisted_primary = self.jobs.focus_manager.get_primary()
            if persisted_primary:
                primary_manifest = self.jobs.load_manifest(persisted_primary)
                if primary_manifest:
                    self.active_job = primary_manifest
                    logger.info(
                        "Restored primary job focus: '%s' (%s)",
                        primary_manifest.get("name") or primary_manifest.get("job_name", "job"),
                        primary_manifest.get("job_id", persisted_primary),
                    )
                    # Activate declared skill if any
                    sk_name = primary_manifest.get("skill_name")
                    if not sk_name and isinstance(primary_manifest.get("skill"), dict):
                        sk_name = primary_manifest["skill"].get("name")
                    elif primary_manifest.get("skills") and isinstance(primary_manifest["skills"], list):
                        first = primary_manifest["skills"][0]
                        sk_name = first.get("name") if isinstance(first, dict) else str(first)
                    if sk_name:
                        sk_res = self.skills.load_skill(sk_name)
                        if sk_res:
                            self.active_skill = sk_res
            if not self.active_job:
                persisted_int = self.jobs.focus_manager.get_integration_focus()
                if persisted_int:
                    self.active_integration_focus = persisted_int
                    logger.info("Restored standalone integration focus: '%s'", persisted_int)
        except Exception as e:
            logger.debug("Could not restore primary job or integration focus on init: %s", e)

        try:
            self._reconcile_startup_scope()
        except Exception as e:
            logger.debug("Could not bind startup job scope to the active conversation: %s", e)

        # Episodic Consolidation Subsystem
        cons_cfg = mem_cfg.get("consolidation", {})
        self.consolidation_enabled = cons_cfg.get("enabled", True)
        self.consolidator: Optional[EpisodicConsolidator] = None
        self.idle_consolidator: Optional[IdleConsolidationRunner] = None
        if self.memory_enabled and self.episodic_store:
            self.consolidator = EpisodicConsolidator(
                episodic_store=self.episodic_store,
                user_store=self.user_store,
                agent_loop=self,
                skill_manager=self.skills,
                similarity_threshold=float(cons_cfg.get("similarity_threshold", 0.85)),
                min_cluster_size=int(cons_cfg.get("min_cluster_size", 2)),
                max_episodes_per_run=int(cons_cfg.get("max_episodes_per_run", 100)),
                max_runtime_seconds=float(cons_cfg.get("max_runtime_seconds", 30.0)),
                propose_rules=bool(cons_cfg.get("propose_rules", True)),
                propose_skills=bool(cons_cfg.get("propose_skills", True)),
                on_activity=self.notify_background_activity,
            )
            if self.consolidation_enabled and bool(cons_cfg.get("auto_idle_runner", True)):
                self.idle_consolidator = IdleConsolidationRunner(
                    consolidator=self.consolidator,
                    idle_threshold_seconds=float(cons_cfg.get("idle_seconds", 1800.0)),
                    check_interval_seconds=float(cons_cfg.get("check_interval_seconds", 60.0)),
                    enabled=self.consolidation_enabled,
                )
                self.idle_consolidator.start()

        # Voice Subsystem
        voice_cfg = self.config.get("voice", {})
        self.voice = voice_manager or VoiceManager(
            python_path=voice_cfg.get("python_path"),
            voice_dir=voice_cfg.get("optimalx_voice_dir"),
            default_voice=voice_cfg.get("tts_voice", "bm_george"),
            default_speed=float(voice_cfg.get("tts_speed", 1.0)),
            device=voice_cfg.get("device", "cpu"),
            stt_device=voice_cfg.get("stt_device", "cpu"),
            hotkey_str=voice_cfg.get("push_to_talk_hotkey"),
        )
        self.voice.register_on_transcribed(
            lambda _text: self.compute_policy.touch_user_activity()
        )
        try:
            self.kill_switch.register_callback(self._on_kill_stop_voice)
        except Exception:
            logger.debug("Could not attach voice barge-in to kill switch.")

        # Callbacks & conversational memory
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
            "on_background_activity": [],
            "on_job_focus": [],
        }
        self.background_active: bool = False
        self.background_task: str = ""
        self.background_details: str = ""
        self.conversation_history: List[Dict[str, Any]] = []
        self.last_turn_trace: Dict[str, Any] = {}
        self.reload_conversation_history()
        self._start_conversation_backfill()

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

    def is_foreground_busy(self) -> bool:
        """True when agent, user activity, or GPU inference should block background local LLM."""
        return self.compute_policy.is_foreground_busy()

    def notify_background_activity(self, active: bool, task: str = "", details: str = ""):
        """Notify observers (gateway, UI) about background processing."""
        self.background_active = bool(active)
        self.background_task = task if active else ""
        self.background_details = details if active else ""
        self._emit("on_background_activity", self.background_active, self.background_task, self.background_details)

    def reload_conversation_history(self) -> None:
        """Replace in-memory turns with the active conversation's stored messages."""
        self._detach_running_history(self.active_conversation_id)
        self.conversation_history.clear()
        if not self.conversation_store or not self.active_conversation_id:
            return
        for message in self.conversation_store.get_messages(self.active_conversation_id):
            role = (message.get("role") or "").lower()
            if role in ("user", "assistant"):
                self.conversation_history.append(
                    {"role": role, "content": message.get("content", "")}
                )

    def clear_history(self):
        """Clear conversational turn history and stored messages for active conversation."""
        self._detach_running_history(self.active_conversation_id)
        conversation_id = self.active_conversation_id
        self._store_conversation_residue(conversation_id)
        self._drop_indexed_exchanges(conversation_id)
        self.conversation_history.clear()
        title = "New Chat"
        if self.conversation_store and conversation_id:
            self.conversation_store.clear_conversation_messages(conversation_id)
            conv = self.conversation_store.get_conversation(conversation_id)
            if conv:
                title = conv.get("title", "New Chat")
        self._emit("on_conversation_changed", conversation_id, title)

    def _model_clients(self):
        """Providers that can hold a cancel ticket, without listing one object twice."""
        seen = set()
        clients = []
        for client in (
            getattr(self, "model", None),
            getattr(self, "router", None),
            getattr(self, "ollama", None),
            getattr(self, "chutes", None),
            getattr(self, "meta", None),
        ):
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            clients.append(client)
        return clients

    def _abort_model(self, run_id: Optional[str] = None) -> None:
        """Close one run's model call, or every call when ``run_id`` is omitted."""
        for client in self._model_clients():
            abort = getattr(client, "abort", None)
            if not callable(abort):
                continue
            try:
                abort(run_id)
            except Exception:
                logger.debug("Model abort failed", exc_info=True)

    def _drop_model_run(self, run_id: str) -> None:
        for client in self._model_clients():
            drop = getattr(client, "drop_run", None)
            if not callable(drop):
                continue
            try:
                drop(run_id)
            except Exception:
                logger.debug("Model ticket drop failed", exc_info=True)

    def _reset_model_abort(self) -> None:
        """Clear stop flags on model clients before a new task."""
        seen = set()
        for client in (
            getattr(self, "model", None),
            getattr(self, "router", None),
            getattr(self, "ollama", None),
            getattr(self, "chutes", None),
        ):
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            reset = getattr(client, "reset_abort", None)
            if not callable(reset):
                continue
            try:
                reset()
            except Exception:
                logger.debug("Model abort reset failed", exc_info=True)

    def _live_runs(self) -> List[TaskRun]:
        with self._task_runs_lock:
            return [run for run in self._task_runs.values() if not run.finished]

    def has_running_task(self) -> bool:
        """True while any conversation still has a task that has not finished."""
        return bool(self._live_runs())

    @property
    def _task_active(self) -> bool:
        return self.has_running_task()

    def running_conversation_ids(self) -> List[str]:
        return [run.conversation_id for run in self._live_runs() if run.conversation_id]

    def conversation_is_running(self, conversation_id: Optional[str]) -> bool:
        if not conversation_id:
            return False
        return any(run.conversation_id == conversation_id for run in self._live_runs())

    def parallel_actions_allowed(self) -> bool:
        """Cloud API mode may overlap prompts, speech, and dictation."""
        router = getattr(self, "router", None)
        allowed = getattr(router, "parallel_actions_allowed", None)
        if not callable(allowed):
            return False
        try:
            return bool(allowed())
        except Exception:
            return False

    def local_machine_is_free(self) -> bool:
        """False when a local Ollama task should keep the next speech or prompt waiting."""
        return self.parallel_actions_allowed() or not self.has_running_task()

    def _runs_for_stop(self, conversation_id: Optional[str]) -> List[TaskRun]:
        runs = self._live_runs()
        if conversation_id:
            return [run for run in runs if run.conversation_id == conversation_id]
        active = self.active_conversation_id or ""
        matched = [run for run in runs if run.conversation_id == active]
        if matched:
            return matched
        if len(runs) == 1:
            return runs
        return runs

    def cancel_task(self, conversation_id: Optional[str] = None) -> None:
        """Stop one conversation's task. Speech keeps playing when a cloud API is selected."""
        logger.info("AgentLoop task cancellation requested.")
        for run in self._runs_for_stop(conversation_id):
            run.stop.set()
            self._abort_model(run.run_id)
        if not self.parallel_actions_allowed():
            self._stop_voice()
        try:
            if hasattr(self, "jobs") and self.jobs and self.jobs.is_worker_running():
                self.jobs.stop_background_worker(timeout=2.0)
        except Exception:
            pass

    def cancel_all_tasks(self) -> None:
        """Stop every task. The emergency kill switch uses this."""
        logger.info("AgentLoop cancelling every task.")
        for run in self._live_runs():
            run.stop.set()
        self._abort_model(None)
        self._stop_voice()
        try:
            if hasattr(self, "jobs") and self.jobs and self.jobs.is_worker_running():
                self.jobs.stop_background_worker(timeout=2.0)
        except Exception:
            pass

    def _stop_voice(self) -> None:
        try:
            if self.voice:
                self.voice.stop_speaking()
        except Exception:
            pass

    def is_stop_requested(self) -> bool:
        """Return True when the task on this thread, or the open chat's task, was stopped."""
        run = current_run.get()
        if run is not None:
            return run.stop.is_set()
        active = self.active_conversation_id or ""
        for item in self._live_runs():
            if item.conversation_id == active:
                return item.stop.is_set()
        return False

    def is_busy(self) -> bool:
        """Return True if an agent task or background batch job is actively running."""
        is_job = hasattr(self, "jobs") and self.jobs and self.jobs.is_worker_running()
        return self.has_running_task() or bool(is_job)

    def _mark_run_finished(self) -> None:
        run = current_run.get()
        if run is not None:
            run.finished = True

    def _detach_running_history(self, conversation_id: Optional[str]) -> None:
        """Give a live task its own transcript before the open chat is replaced."""
        if not conversation_id:
            return
        with self._task_runs_lock:
            runs = [
                run
                for run in self._task_runs.values()
                if run.conversation_id == conversation_id and run.history is self.conversation_history
            ]
        for run in runs:
            run.history = list(self.conversation_history)

    def _history_for_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        if not conversation_id or conversation_id == (self.active_conversation_id or ""):
            return self.conversation_history
        loaded: List[Dict[str, Any]] = []
        store = self.conversation_store
        if not store:
            return loaded
        try:
            messages = store.get_messages(conversation_id)
        except Exception:
            logger.debug("Could not load history for conversation %s", conversation_id, exc_info=True)
            return loaded
        if not isinstance(messages, list):
            return loaded
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = (message.get("role") or "").lower()
            if role in ("user", "assistant"):
                loaded.append({"role": role, "content": message.get("content", "")})
        return loaded

    def _bound_conversation_id(self) -> Optional[str]:
        run = current_run.get()
        if run is not None and run.conversation_id:
            return run.conversation_id
        return self.active_conversation_id

    def _bound_history(self) -> List[Dict[str, Any]]:
        run = current_run.get()
        if run is not None:
            return run.history
        return self.conversation_history

    def _bound_scope(self):
        run = current_run.get()
        if run is not None:
            return run.scope
        return self.current_task_scope

    def _set_bound_scope(self, scope: Any) -> None:
        run = current_run.get()
        if run is not None:
            run.scope = scope
        if run is None or run.conversation_id == (self.active_conversation_id or ""):
            self.current_task_scope = scope

    def _busy_result(self, kind: str) -> Dict[str, Any]:
        if kind == "local":
            summary = (
                "The local model is already working. Wait for it to finish, "
                "or switch the provider to a cloud API to run another prompt."
            )
        else:
            summary = "This conversation is already working on a prompt."
        return {"success": False, "busy": True, "summary": summary, "steps_taken": 0}

    def shutdown(self, timeout: float = 2.0) -> None:
        """Shutdown background worker threads including episodic reflector and consolidator."""
        if hasattr(self, "idle_consolidator") and self.idle_consolidator:
            try:
                self.idle_consolidator.stop(timeout=timeout)
            except Exception as e:
                logger.debug("Error stopping idle consolidator during loop shutdown: %s", e)
        if hasattr(self, "consolidator") and self.consolidator:
            try:
                self.consolidator.stop(timeout=timeout)
            except Exception as e:
                logger.debug("Error stopping consolidator during loop shutdown: %s", e)
        if hasattr(self, "reflector") and self.reflector:
            try:
                self.reflector.stop(timeout=timeout)
            except Exception as e:
                logger.debug("Error stopping reflector during loop shutdown: %s", e)

    def _skill_name_from_manifest(self, manifest: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return the first declared skill name on a job manifest."""
        if not isinstance(manifest, dict):
            return None
        skill_name = manifest.get("skill_name")
        if not skill_name and isinstance(manifest.get("skill"), dict):
            skill_name = manifest["skill"].get("name")
        elif isinstance(manifest.get("skills"), list) and manifest["skills"]:
            first = manifest["skills"][0]
            skill_name = first.get("name") if isinstance(first, dict) else str(first)
        if isinstance(skill_name, str) and skill_name.strip():
            return skill_name.strip()
        return None

    def _activate_manifest_skill(self, manifest: Optional[Dict[str, Any]]) -> None:
        """Load the job's declared skill, or clear the skill when the job has none."""
        self.active_skill = None
        skill_name = self._skill_name_from_manifest(manifest)
        skills = getattr(self, "skills", None)
        if skill_name and skills:
            loaded = skills.load_skill(skill_name)
            if loaded:
                self.active_skill = loaded

    def _write_live_focus(self, primary: Optional[str], integration: Optional[str]) -> None:
        """Mirror the active conversation's scope into focus.yaml without shuffling attached jobs."""
        jobs = getattr(self, "jobs", None)
        focus_manager = getattr(jobs, "focus_manager", None) if jobs else None
        if not focus_manager:
            return
        current = focus_manager.get_focus()
        focus_manager.save_focus(
            primary=primary,
            attached=current.get("attached") or [],
            integration_focus=None if primary else integration,
        )

    def _clear_live_scope(self) -> None:
        """Drop the in-memory job, skill, and integration focus for this conversation."""
        self.active_job = None
        self.active_skill = None
        self.active_integration_focus = None
        self._write_live_focus(None, None)

    def _restore_live_scope(self, job_id: Optional[str], integration: Optional[str]) -> None:
        """Load one conversation's saved job or integration into the live session."""
        self.active_job = None
        self.active_skill = None
        self.active_integration_focus = None
        loaded = None
        jobs = getattr(self, "jobs", None)
        if job_id and jobs:
            try:
                loaded = jobs.load_manifest(job_id)
            except Exception as e:
                logger.debug("Could not restore conversation job '%s': %s", job_id, e)
        if isinstance(loaded, dict):
            self.active_job = loaded
            self._activate_manifest_skill(loaded)
            self._write_live_focus(str(loaded.get("job_id") or job_id), None)
            return
        if integration:
            self.active_integration_focus = integration
            self._write_live_focus(None, integration)
            return
        self._write_live_focus(None, None)

    def _sync_recall_conversation(self) -> None:
        """Tell mid-thought recall which conversation and job scope to search."""
        interceptor = getattr(self, "recall_interceptor", None)
        if interceptor is None:
            return
        conversation_id = self._bound_conversation_id()
        job_id = None
        scope_bound = False
        store = getattr(self, "conversation_store", None)
        if store and conversation_id:
            try:
                scope = store.get_conversation_scope(conversation_id) or {}
                job_id = scope.get("job_id")
                scope_bound = bool(scope.get("bound"))
            except Exception as exc:
                logger.debug("Could not read conversation scope for recall: %s", exc)
        interceptor.set_conversation_ref(conversation_id, job_id, scope_bound)

    def _persist_scope_on_conversation(self, conversation_id: Optional[str]) -> None:
        """Save the live job or integration focus onto one conversation."""
        store = getattr(self, "conversation_store", None)
        if not conversation_id or not store:
            return
        job_id = None
        integration = None
        job = getattr(self, "active_job", None)
        if isinstance(job, dict):
            raw = job.get("job_id") or (job.get("directories") or {}).get("job_dir")
            if raw:
                job_id = str(raw)
        if not job_id:
            raw_integration = getattr(self, "active_integration_focus", None)
            if isinstance(raw_integration, str) and raw_integration.strip():
                integration = raw_integration.strip()
        try:
            store.set_conversation_scope(conversation_id, job_id, integration)
        except Exception as e:
            logger.debug("Could not store conversation scope for %s: %s", conversation_id, e)

    def _apply_stored_scope(self, conversation_id: Optional[str]) -> None:
        """Make the live session match the scope stored on a conversation."""
        store = getattr(self, "conversation_store", None)
        scope = None
        if conversation_id and store:
            try:
                scope = store.get_conversation_scope(conversation_id)
            except Exception as e:
                logger.debug("Could not read conversation scope for %s: %s", conversation_id, e)
        if scope and scope.get("bound"):
            self._restore_live_scope(scope.get("job_id"), scope.get("integration_focus"))
            return
        self._clear_live_scope()
        if conversation_id and store:
            try:
                store.set_conversation_scope(conversation_id, None, None)
            except Exception as e:
                logger.debug("Could not mark conversation %s unscoped: %s", conversation_id, e)

    def _conversation_has_messages(self, conversation_id: str) -> bool:
        store = getattr(self, "conversation_store", None)
        if not store:
            return False
        try:
            return bool(store.get_messages(conversation_id, limit=1))
        except Exception:
            return False

    def _reconcile_startup_scope(self) -> None:
        """Prefer the open conversation's saved scope over the last global focus.yaml pointer.

        A conversation that already has turns and has never stored a scope keeps the
        focus restored at startup. An empty conversation is a new chat: the previous
        job stays on the earlier conversation, and this one starts unscoped.
        """
        conversation_id = getattr(self, "active_conversation_id", None)
        store = getattr(self, "conversation_store", None)
        if not conversation_id or not store:
            return
        scope = store.get_conversation_scope(conversation_id)
        if scope and scope.get("bound"):
            self._restore_live_scope(scope.get("job_id"), scope.get("integration_focus"))
            return
        if self._conversation_has_messages(conversation_id):
            self._persist_scope_on_conversation(conversation_id)
            return

        if self.active_job or self.active_integration_focus:
            try:
                others = store.list_conversations(limit=20)
            except Exception:
                others = []
            for conv in others:
                other_id = conv.get("id") if isinstance(conv, dict) else None
                if not other_id or other_id == conversation_id:
                    continue
                try:
                    other_scope = store.get_conversation_scope(other_id)
                except Exception:
                    continue
                if other_scope and other_scope.get("bound"):
                    continue
                if not self._conversation_has_messages(other_id):
                    continue
                self._persist_scope_on_conversation(other_id)
                break
        self._clear_live_scope()
        try:
            store.set_conversation_scope(conversation_id, None, None)
        except Exception as e:
            logger.debug("Could not mark conversation %s unscoped at startup: %s", conversation_id, e)

    def start_new_conversation(self, title: str = "New Chat") -> str:
        """Create a fresh conversation with no job scope from the conversation being left."""
        left_id = getattr(self, "active_conversation_id", None)
        self._detach_running_history(left_id)
        self._persist_scope_on_conversation(left_id)
        self._store_conversation_residue(left_id)
        if self.conversation_store:
            new_id = self.conversation_store.create_conversation(title=title)
            self.active_conversation_id = new_id
            self.conversation_store.set_conversation_scope(new_id, None, None)
        else:
            self.active_conversation_id = None
        self._clear_live_scope()
        self.conversation_history.clear()
        self._emit("on_conversation_title_changed", title)
        self._emit("on_conversation_changed", self.active_conversation_id, title)
        self._emit("on_job_focus")
        self._sync_recall_conversation()
        return self.active_conversation_id or ""

    def switch_conversation(self, conversation_id: str) -> bool:
        """Switch active conversation, restoring that conversation's own job scope."""
        if not self.conversation_store:
            return False
        self._detach_running_history(self.active_conversation_id)
        conv = self.conversation_store.get_conversation(conversation_id)
        if not conv:
            return False
        if self.active_conversation_id != conversation_id:
            self._persist_scope_on_conversation(self.active_conversation_id)
            self.active_conversation_id = conversation_id
            self._apply_stored_scope(conversation_id)
            try:
                self.conversation_store.touch_conversation(conversation_id)
            except Exception as e:
                logger.debug("Could not touch conversation %s: %s", conversation_id, e)
            self._emit("on_job_focus")
        else:
            self.active_conversation_id = conversation_id
        self.reload_conversation_history()
        self._sync_recall_conversation()
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
        if self.conversation_index:
            try:
                self.conversation_index.delete_conversation(conversation_id)
            except Exception as exc:
                logger.warning("Could not drop conversation index rows for %s: %s", conversation_id, exc)
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
            self.ollama.num_ctx = local_num_ctx(local_cfg)
            self.ollama.clear_vision_capability_cache()

        # Update cloud Chutes settings
        cloud_cfg = new_config.get("models", {}).get("cloud", {})
        if cloud_cfg:
            self.chutes.api_key = cloud_cfg.get("api_key", self.chutes.api_key)
            self.chutes.base_url = cloud_cfg.get("base_url", self.chutes.base_url).rstrip("/")
            self.chutes.vision_model = cloud_cfg.get("vision_model", self.chutes.vision_model)
            self.chutes.reasoning_model = cloud_cfg.get("reasoning_model", self.chutes.reasoning_model)
            provider = cloud_cfg.get("provider")
            if provider and hasattr(self.router, "set_cloud_provider"):
                self.router.set_cloud_provider(provider)

        # Update Meta Muse settings
        meta_cfg = new_config.get("models", {}).get("meta")
        if isinstance(meta_cfg, dict) and hasattr(self, "meta"):
            self.meta.api_key = (meta_cfg.get("api_key") or "") or os.environ.get("MODEL_API_KEY", "")
            self.meta.base_url = (meta_cfg.get("base_url") or self.meta.base_url).rstrip("/")
            self.meta.vision_model = meta_cfg.get("vision_model", self.meta.vision_model)
            self.meta.reasoning_model = meta_cfg.get("reasoning_model", self.meta.reasoning_model)
            effort = meta_cfg.get("reasoning_effort")
            if effort:
                self.meta.reasoning_effort = str(effort).strip().lower()
            if "timeout_seconds" in meta_cfg:
                try:
                    self.meta.timeout = float(meta_cfg["timeout_seconds"])
                except (TypeError, ValueError):
                    pass

        # Update Router preference
        pref = new_config.get("models", {}).get("provider_preference")
        if pref:
            self.router.set_preference(pref)

        compute_cfg = new_config.get("models", {}).get("compute")
        if compute_cfg is not None or new_config.get("memory", {}).get("reflector"):
            self.compute_policy.apply_settings(compute_settings_from_config(new_config))
            if hasattr(self.router, "compute_policy"):
                self.router.compute_policy = self.compute_policy
            if self.reflector:
                refl_cfg = new_config.get("memory", {}).get("reflector", {})
                if "idle_seconds" in refl_cfg:
                    try:
                        self.reflector.idle_seconds = float(refl_cfg["idle_seconds"])
                    except (TypeError, ValueError):
                        pass
                if "prefer_cloud_when_busy" in refl_cfg:
                    self.reflector.prefer_cloud_when_busy = bool(refl_cfg["prefer_cloud_when_busy"])

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
            if "stt_device" in voice_cfg and hasattr(self.voice, "stt"):
                self.voice.stt.device = str(voice_cfg["stt_device"]).strip().lower()

        # Update Memory settings
        mem_cfg = new_config.get("memory", {})
        if mem_cfg:
            self.memory_enabled = mem_cfg.get("enabled", self.memory_enabled)
            if "user_profile_file" in mem_cfg:
                self.user_store = UserStore(storage_path=mem_cfg["user_profile_file"])
            if "trajectories_file" in mem_cfg:
                self.trajectory_logger = TrajectoryLogger(storage_path=mem_cfg["trajectories_file"])
            if (
                "vector_store_path" in mem_cfg
                or "episodic_db_path" in mem_cfg
                or "embedding_model" in mem_cfg
                or "embedding_num_gpu" in mem_cfg
            ):
                base_ollama_url = self.config.get("models", {}).get("local", {}).get("base_url", "http://localhost:11434")
                emb_model = mem_cfg.get("embedding_model", "nomic-embed-text:latest")
                db_p = mem_cfg.get("vector_store_path") or mem_cfg.get("episodic_db_path")
                self.episodic_store = EpisodicStore(
                    db_path=db_p,
                    base_url=base_ollama_url,
                    embedding_model=emb_model,
                    embedding_num_gpu=embedding_num_gpu(mem_cfg),
                )
            if self.recall_interceptor:
                self.recall_interceptor.episodic_store = self.episodic_store
                self.recall_interceptor.optimalx = self.optimalx
                self.recall_interceptor.conversation_index = self.conversation_index
            elif (self.memory_enabled and self.episodic_store) or self.optimalx:
                self.recall_interceptor = RecallStreamInterceptor(
                    episodic_store=self.episodic_store,
                    optimalx_bridge=self.optimalx,
                    max_recalls_per_turn=int(mem_cfg.get("max_recalls_per_turn", 3)),
                    min_similarity=float(mem_cfg.get("recall_min_similarity", 0.20)),
                    conversation_index=self.conversation_index,
                )
                self.ollama.recall_interceptor = self.recall_interceptor
                self.ollama.on_recall_callback = self._on_recall_triggered
            if "confirm_profile_writes" in mem_cfg:
                self.gatekeeper.confirm_profile_writes = bool(mem_cfg["confirm_profile_writes"])
            if "conversations_db_path" in mem_cfg or "conversation_db_path" in mem_cfg:
                conv_db_p = mem_cfg.get("conversations_db_path") or mem_cfg.get("conversation_db_path")
                self.conversation_store = ConversationStore(db_path=conv_db_p)
                self.active_conversation_id = self.conversation_store.get_or_create_active_conversation_id()
            refl_cfg = mem_cfg.get("reflector", {})
            if "enabled" in refl_cfg or "reflector" in mem_cfg:
                self.reflector_enabled = refl_cfg.get("enabled", self.reflector_enabled)
                if self.reflector:
                    self.reflector.enabled = self.reflector_enabled
                    self.reflector.episodic_store = self.episodic_store
                    self.reflector.trajectory_logger = self.trajectory_logger
                    if "model" in refl_cfg:
                        self.reflector.model_name = refl_cfg["model"]
                    if "timeout_seconds" in refl_cfg:
                        self.reflector.timeout_seconds = float(refl_cfg["timeout_seconds"])
                elif self.memory_enabled and self.reflector_enabled:
                    self.reflector = EpisodicReflector(
                        model_client=self.model,
                        episodic_store=self.episodic_store,
                        trajectory_logger=self.trajectory_logger,
                        on_background_activity=self.notify_background_activity,
                        enabled=self.reflector_enabled,
                        model_name=refl_cfg.get("model"),
                        timeout_seconds=float(refl_cfg.get("timeout_seconds", 30.0)),
                        compute_policy=self.compute_policy,
                        prefer_cloud_when_busy=bool(refl_cfg.get("prefer_cloud_when_busy", True)),
                        idle_seconds=float(refl_cfg.get("idle_seconds", self.compute_policy.idle_seconds)),
                    )

            cons_cfg = mem_cfg.get("consolidation", {})
            if "enabled" in cons_cfg or "consolidation" in mem_cfg:
                self.consolidation_enabled = cons_cfg.get("enabled", self.consolidation_enabled)
                if self.consolidator:
                    if "similarity_threshold" in cons_cfg:
                        self.consolidator.similarity_threshold = float(cons_cfg["similarity_threshold"])
                    if "min_cluster_size" in cons_cfg:
                        self.consolidator.min_cluster_size = int(cons_cfg["min_cluster_size"])
                    if "max_episodes_per_run" in cons_cfg:
                        self.consolidator.max_episodes_per_run = int(cons_cfg["max_episodes_per_run"])
                    if "max_runtime_seconds" in cons_cfg:
                        self.consolidator.max_runtime_seconds = float(cons_cfg["max_runtime_seconds"])
                    if "propose_rules" in cons_cfg:
                        self.consolidator.propose_rules = bool(cons_cfg["propose_rules"])
                    if "propose_skills" in cons_cfg:
                        self.consolidator.propose_skills = bool(cons_cfg["propose_skills"])
                elif self.memory_enabled and self.episodic_store:
                    self.consolidator = EpisodicConsolidator(
                        episodic_store=self.episodic_store,
                        user_store=self.user_store,
                        agent_loop=self,
                        skill_manager=self.skills,
                        similarity_threshold=float(cons_cfg.get("similarity_threshold", 0.85)),
                        min_cluster_size=int(cons_cfg.get("min_cluster_size", 2)),
                        max_episodes_per_run=int(cons_cfg.get("max_episodes_per_run", 100)),
                        max_runtime_seconds=float(cons_cfg.get("max_runtime_seconds", 30.0)),
                        propose_rules=bool(cons_cfg.get("propose_rules", True)),
                        propose_skills=bool(cons_cfg.get("propose_skills", True)),
                        on_activity=self.notify_background_activity,
                    )
                if self.idle_consolidator:
                    self.idle_consolidator.enabled = self.consolidation_enabled and bool(cons_cfg.get("auto_idle_runner", True))
                    if "idle_seconds" in cons_cfg:
                        self.idle_consolidator.idle_threshold_seconds = float(cons_cfg["idle_seconds"])
                    if "check_interval_seconds" in cons_cfg:
                        self.idle_consolidator.check_interval_seconds = float(cons_cfg["check_interval_seconds"])
                elif self.consolidation_enabled and self.consolidator and bool(cons_cfg.get("auto_idle_runner", True)):
                    self.idle_consolidator = IdleConsolidationRunner(
                        consolidator=self.consolidator,
                        idle_threshold_seconds=float(cons_cfg.get("idle_seconds", 1800.0)),
                        check_interval_seconds=float(cons_cfg.get("check_interval_seconds", 60.0)),
                        enabled=self.consolidation_enabled,
                    )
                    self.idle_consolidator.start()

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
        """Finalize trajectory logging and index into vector episodic store with TaskScope metadata."""
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
                scope = self._bound_scope()
                if scope:
                    rec.metadata.project_name = scope.project_name
                    rec.metadata.workspace_path = scope.workspace_path
                    rec.metadata.cwd = scope.cwd
                    rec.metadata.active_skill = scope.active_skill
                    rec.metadata.window_class = scope.window_class
                    rec.metadata.window_title = scope.window_title
                    rec.metadata.job_id = scope.job_id
                    rec.metadata.integration = scope.integration
                    try:
                        self.trajectory_logger.update_episode(rec)
                    except Exception as upd_err:
                        logger.debug("Failed to update trajectory file with scope metadata: %s", upd_err)
                self.episodic_store.index_episode(rec)
            return rec
        except Exception as e:
            logger.warning("Error completing trajectory / episodic indexing: %s", e)
            return None

    def _on_kill_stop_voice(self) -> None:
        """Stop TTS, cancel every task, and halt batch workers when emergency kill fires."""
        self.cancel_all_tasks()
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
        """Construct lean system prompt, appending active job context and active skill procedure if scoped."""
        prompt = build_system_prompt(
            active_win,
            os.getcwd(),
            clock=self.clock,
            user_profile_context=user_profile_ctx,
            episodic_context=episodic_ctx,
            workspace_dir=self.workspace_dir,
            sandbox_cfg=self.config.get("sandbox"),
            sandbox_writable_roots=self.shell.writable_roots(),
        )
        if self.active_job:
            job_id = self.active_job.get("job_id", "")
            job_name = self.active_job.get("name") or self.active_job.get("job_name", "")
            status = self.active_job.get("status", "active")
            dirs = self.active_job.get("directories", {})
            job_dir = dirs.get("job_dir") or dirs.get("output_dir", "")
            notes_dir = dirs.get("notes_dir") or (str(Path(job_dir) / "notes") if job_dir else "")
            artifacts_dir = dirs.get("artifacts_dir") or dirs.get("portfolios_dir", "")

            prompt += f"""

---
PRIMARY JOB: {job_name} ({job_id})
- Status: {status}
- Working Directory: {job_dir}
- Notes Directory: {notes_dir}
- Artifacts Directory: {artifacts_dir}
"""
            # Inject project brief details from job.yaml (PROJECT_JOBS.md)
            identity = self.active_job.get("identity")
            if isinstance(identity, dict):
                summary = identity.get("summary")
                goal = identity.get("goal")
                phase = identity.get("phase")
                if summary:
                    prompt += f"Summary: {summary}\n"
                if goal:
                    prompt += f"Goal: {goal}\n"
                if phase:
                    prompt += f"Phase: {phase}\n"

            boundary = self.active_job.get("boundary")
            if isinstance(boundary, dict):
                this_is = boundary.get("this_is")
                this_is_not = boundary.get("this_is_not")
                if this_is:
                    prompt += f"This is: {this_is}\n"
                if this_is_not:
                    prompt += f"This is not: {this_is_not}\n"
                for df in (boundary.get("distinct_from") or []):
                    if isinstance(df, dict):
                        df_n = df.get("name", "")
                        df_d = df.get("difference", "")
                        if df_n:
                            prompt += f"Distinct from: {df_n} — {df_d}\n"
                    elif isinstance(df, str) and df.strip():
                        prompt += f"Distinct from: {df.strip()}\n"

            decisions = self.active_job.get("decisions")
            if isinstance(decisions, list) and decisions:
                prompt += "Decisions in force:\n"
                for d in decisions[-5:]:
                    d_text = d.get("text") if isinstance(d, dict) else str(d)
                    prompt += f"- {d_text}\n"

            open_questions = self.active_job.get("open_questions")
            if isinstance(open_questions, list) and open_questions:
                prompt += "Open questions:\n"
                for q in open_questions[:5]:
                    prompt += f"- {q}\n"

            constraints = self.active_job.get("constraints")
            if isinstance(constraints, list) and constraints:
                prompt += "Constraints:\n"
                for c in constraints[:5]:
                    prompt += f"- {c}\n"

            skills_list = self.active_job.get("skills")
            if isinstance(skills_list, list) and skills_list:
                prompt += "Skills:\n"
                for s in skills_list:
                    if isinstance(s, dict):
                        s_name = s.get("name", "")
                        s_uw = s.get("use_when") or "Declared project skill"
                        prompt += f"- {s_name} — {s_uw}\n"
                    elif isinstance(s, str):
                        prompt += f"- {s}\n"
            elif self.active_job.get("skill_name"):
                prompt += f"Skills:\n- {self.active_job.get('skill_name')}\n"

            docs = self.active_job.get("documents")
            if isinstance(docs, list) and docs:
                prompt += "Documents (read only when the step matches):\n"
                for doc in docs[:6]:
                    if isinstance(doc, dict):
                        dp = doc.get("path", "")
                        dsumm = doc.get("summary", "")
                        drw = doc.get("read_when", "")
                        prompt += f"- {dp} ({dsumm}) [Read when: {drw}]\n"

            # Attached jobs status lines
            try:
                attached_ids = self.jobs.focus_manager.get_attached()
                active_worker_id = self.jobs.get_active_worker_job_id()
                if attached_ids or active_worker_id:
                    prompt += "Attached jobs (status only):\n"
                    seen_att = set()
                    if active_worker_id and active_worker_id != job_id:
                        seen_att.add(active_worker_id)
                        w_man = self.jobs.load_manifest(active_worker_id)
                        w_name = (w_man.get("name") or w_man.get("job_name") or active_worker_id) if w_man else active_worker_id
                        w_prog = w_man.get("progress", {}) if w_man else {}
                        prompt += f"- {w_name} — running, {w_prog.get('completed_items', 0)}/{w_prog.get('total_items', 0)}\n"
                    for att_id in attached_ids:
                        if att_id not in seen_att and att_id != job_id:
                            seen_att.add(att_id)
                            a_man = self.jobs.load_manifest(att_id)
                            a_name = (a_man.get("name") or a_man.get("job_name") or att_id) if a_man else att_id
                            a_stat = a_man.get("status", "idle") if a_man else "attached"
                            prompt += f"- {a_name} — {a_stat}\n"
            except Exception:
                pass

            # Batch queue progress if component attached
            prog = self.active_job.get("progress", {})
            total = prog.get("total_items", 0)
            done = prog.get("completed_items", 0)
            pct = prog.get("percent", 0.0)
            if total > 0:
                prompt += f"Batch Queue: {done}/{total} items ({pct}%)\n"
            if dirs.get("queue_file"):
                prompt += f"- Queue State: {dirs.get('queue_file')}\n"

            prompt += "If a similar memory or search result conflicts with this brief, follow the brief.\n---\n"
        elif not self.active_integration_focus:
            prompt += (
                "\n\n---\n"
                "CONVERSATION SCOPE: This conversation has no project job and no integration focus. "
                "Do not continue another conversation's job, brief, notes, or files. "
                "Stay in base desktop mode until this conversation is about a project. "
                "If the user asks to continue a specific project, call focus_job for that project "
                "before reading or writing its notes. If they start a new project, call create_job.\n"
                "---"
            )

        # Inject integration skills and tool catalogs (PROJECT_JOBS.md Section 13.2)
        active_ints: List[str] = []
        if self.active_job:
            active_ints = self.mcp_config.resolve_job_integrations(self.active_job)
        elif self.active_integration_focus:
            active_ints = [self.active_integration_focus]

        for int_name in active_ints:
            rec = self.mcp_config.get_integration(int_name)
            server_name = rec.server if rec else int_name
            if rec and rec.skill_body:
                prompt += f"\n\n---\nINTEGRATION SKILL: {rec.name}\n{rec.skill_body}\n---"
            try:
                _, catalog_text, has_many = self.mcp_client.get_scoped_tool_schemas(server_name)
                if has_many and catalog_text:
                    prompt += f"\n\n---\n{catalog_text}\n---"
            except Exception as e:
                logger.debug("Failed resolving MCP tool catalog for '%s': %s", server_name, e)

        if not self.active_job and self.active_integration_focus:
            prompt += f"\n\n---\nACTIVE INTEGRATION FOCUS: {self.active_integration_focus}\n---"

        if self.active_skill:
            skill_name = self.active_skill.get("name", "unnamed-skill")
            skill_body = (self.active_skill.get("body") or "").strip()
            prompt += f"\n\n---\nACTIVE SKILL: {skill_name}\n{skill_body}\n---"
        return prompt

    def _assemble_active_tools(self) -> List[Dict[str, Any]]:
        """Return base tools or scoped tools unlocked by active skill, active job, and MCP integrations."""
        req_tools: List[str] = []

        if self.active_job:
            # Batch tools unlocked when queue component is present or active
            if self.active_job.get("components", {}).get("batch_queue") or self.active_job.get("directories", {}).get("queue_file"):
                req_tools.extend([
                    "init_batch_job",
                    "run_batch_job",
                    "step_batch_job",
                    "get_job_status",
                    "pause_batch_job",
                    "resume_batch_job",
                    "generate_batch_summary",
                ])

            # Unlock declared skills for the primary job
            declared_skills = self.active_job.get("skills")
            if isinstance(declared_skills, list):
                for s in declared_skills:
                    s_name = s.get("name") if isinstance(s, dict) else str(s)
                    if s_name:
                        s_obj = self.skills.load_skill(s_name)
                        if s_obj:
                            for t in (s_obj.get("tools_required") or []):
                                if t not in req_tools:
                                    req_tools.append(t)
            elif self.active_job.get("skill_name"):
                s_obj = self.skills.load_skill(self.active_job["skill_name"])
                if s_obj:
                    for t in (s_obj.get("tools_required") or []):
                        if t not in req_tools:
                            req_tools.append(t)

        if self.active_skill:
            for t in (self.active_skill.get("tools_required") or []):
                if t not in req_tools:
                    req_tools.append(t)
        elif self.active_job:
            skill_info = self.active_job.get("skill")
            if isinstance(skill_info, dict):
                for t in (skill_info.get("tools_required") or []):
                    if t not in req_tools:
                        req_tools.append(t)

        # Inject MCP integration tools (PROJECT_JOBS.md Section 13.2)
        extra_mcp_tools: List[Dict[str, Any]] = []
        active_ints: List[str] = []
        if self.active_job:
            active_ints = self.mcp_config.resolve_job_integrations(self.active_job)
        elif self.active_integration_focus:
            active_ints = [self.active_integration_focus]

        for int_name in active_ints:
            rec = self.mcp_config.get_integration(int_name)
            server_name = rec.server if rec else int_name
            try:
                schemas, _, has_many = self.mcp_client.get_scoped_tool_schemas(server_name)
                if has_many:
                    if "call_mcp_tool" not in req_tools:
                        req_tools.append("call_mcp_tool")
                else:
                    for sc in schemas:
                        extra_mcp_tools.append(sc)
            except Exception as e:
                logger.debug("Failed discovering MCP tools for integration '%s': %s", int_name, e)

        base_or_scoped = ToolRegistry.get_scoped_tools(req_tools) if req_tools else ToolRegistry.get_base_tools()
        if extra_mcp_tools:
            seen_names = {t["function"]["name"] for t in base_or_scoped}
            for sc in extra_mcp_tools:
                s_name = sc["function"]["name"]
                if s_name not in seen_names:
                    base_or_scoped.append(sc)
                    seen_names.add(s_name)

        return base_or_scoped

    def _resolve_step_ceiling(self, max_steps: Optional[int]) -> tuple[int, bool]:
        """Return (ceiling, explicit).

        An explicit cap is the caller's max_steps (CLI --steps, a scheduled turn).
        Otherwise the ceiling is the safety fuse, and a stall ends the run first.
        """
        if max_steps is not None:
            try:
                value = int(max_steps)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value, True
        configured = self.config.get("agent", {}).get("safety_step_fuse", DEFAULT_SAFETY_STEP_FUSE)
        try:
            value = int(configured)
        except (TypeError, ValueError):
            value = DEFAULT_SAFETY_STEP_FUSE
        return value if value > 0 else DEFAULT_SAFETY_STEP_FUSE, False

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

    def _start_conversation_backfill(self) -> None:
        """Index up to 200 stored exchanges once per process. Embed failures stop the pass."""
        if self._conversation_backfill_started:
            return
        if not self.memory_enabled or self.conversation_index is None or self.conversation_store is None:
            return
        self._conversation_backfill_started = True
        threading.Thread(
            target=self._backfill_conversation_index,
            name="conversation-index-backfill",
            daemon=True,
        ).start()

    def _backfill_conversation_index(self) -> None:
        index = self.conversation_index
        store = self.conversation_store
        if index is None or store is None:
            return
        try:
            known = index.known_message_ids()
            missing = [item for item in store.iter_exchanges(None) if item.message_id not in known]
            missing.sort(key=lambda item: item.created_at, reverse=True)
            for exchange in missing[:200]:
                scope = store.get_conversation_scope(exchange.conversation_id) or {}
                conversation = store.get_conversation(exchange.conversation_id) or {}
                saved = index.upsert(
                    message_id=exchange.message_id,
                    conversation_id=exchange.conversation_id,
                    job_id=scope.get("job_id") or "",
                    scope_bound=bool(scope.get("bound")),
                    conversation_title=conversation.get("title") or "",
                    user_text=exchange.user_text,
                    assistant_text=exchange.assistant_text,
                    created_at=exchange.created_at,
                )
                if not saved:
                    logger.warning("Conversation index backfill stopped after an embed failure.")
                    return
        except Exception as exc:
            logger.warning("Conversation index backfill stopped: %s", exc)

    def _build_residue(self, conversation_id: str) -> str:
        """Clip the latest exchanges into a local residue. No messages yields an empty string."""
        store = self.conversation_store
        if store is None or not conversation_id:
            return ""
        exchanges = store.iter_exchanges(conversation_id)[-8:]
        if not exchanges:
            return ""
        lines: List[str] = []
        for exchange in exchanges:
            user = " ".join((exchange.user_text or "").split())[:200]
            assistant = " ".join((exchange.assistant_text or "").split())[:300]
            lines.append(f"User: {user}")
            lines.append(f"Assistant: {assistant}")
        return "\n".join(lines)[:1500]

    def _store_conversation_residue(self, conversation_id: Optional[str]) -> None:
        """Save a residue and index it. An empty build leaves any existing residue in place."""
        store = self.conversation_store
        if store is None or not conversation_id:
            return
        try:
            residue = self._build_residue(conversation_id)
            if not residue:
                return
            store.set_residue(conversation_id, residue)
            index = self.conversation_index
            if index is None:
                return
            scope = store.get_conversation_scope(conversation_id) or {}
            conversation = store.get_conversation(conversation_id) or {}
            created_at = max(item.created_at for item in store.iter_exchanges(conversation_id))
            index.upsert(
                message_id=f"residue:{conversation_id}",
                conversation_id=conversation_id,
                job_id=scope.get("job_id") or "",
                scope_bound=bool(scope.get("bound")),
                conversation_title=conversation.get("title") or "",
                user_text="Conversation residue",
                assistant_text=residue,
                created_at=created_at,
                is_residue=True,
            )
        except Exception as exc:
            logger.warning("Could not store conversation residue for %s: %s", conversation_id, exc)

    def _drop_indexed_exchanges(self, conversation_id: Optional[str]) -> None:
        """Remove indexed turns for a conversation and keep its residue row."""
        store = self.conversation_store
        index = self.conversation_index
        if store is None or index is None or not conversation_id:
            return
        try:
            message_ids = [item.message_id for item in store.iter_exchanges(conversation_id)]
            index.delete_messages(message_ids)
        except Exception as exc:
            logger.warning("Could not drop indexed exchanges for %s: %s", conversation_id, exc)

    @staticmethod
    def _conversation_tail(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep the last three user messages and three assistant messages, in order."""
        user_kept = 0
        assistant_kept = 0
        chosen: List[int] = []
        for index in range(len(history) - 1, -1, -1):
            role = (history[index].get("role") or "").lower()
            if role == "user" and user_kept < 3:
                chosen.append(index)
                user_kept += 1
            elif role == "assistant" and assistant_kept < 3:
                chosen.append(index)
                assistant_kept += 1
            if user_kept >= 3 and assistant_kept >= 3:
                break
        chosen.sort()
        return [history[index] for index in chosen]

    def _tail_assistant_message_ids(self, conversation_id: str) -> set:
        """Assistant message ids that correspond to the verbatim tail."""
        store = self.conversation_store
        if store is None or not conversation_id:
            return set()
        user_kept = 0
        assistant_kept = 0
        ids: set = set()
        for message in reversed(store.get_messages(conversation_id)):
            role = (message.get("role") or "").upper()
            if role == "USER" and user_kept < 3:
                user_kept += 1
            elif role == "ASSISTANT" and assistant_kept < 3:
                assistant_kept += 1
                message_id = message.get("id")
                if message_id:
                    ids.add(message_id)
            if user_kept >= 3 and assistant_kept >= 3:
                break
        return ids

    @staticmethod
    def _attach_conversation_context(system_prompt: str, block: str) -> str:
        """Place retrieved chat after the episodic block, otherwise at the end of the prompt."""
        text = (block or "").strip()
        if not text:
            return system_prompt
        marker = "</episodic_context>"
        if marker in system_prompt:
            return system_prompt.replace(marker, marker + "\n" + text, 1)
        return system_prompt.rstrip() + "\n" + text

    def _conversation_context_block(self, user_goal: str) -> str:
        """Search older exchanges for this goal. An embed miss leaves the tail as the only history."""
        index = self.conversation_index
        store = self.conversation_store
        conversation_id = self._bound_conversation_id()
        if index is None or store is None or not conversation_id:
            return ""
        try:
            scope = store.get_conversation_scope(conversation_id) or {}
            exclude = self._tail_assistant_message_ids(conversation_id)
            exclude.add(f"residue:{conversation_id}")
            mem_cfg = self.config.get("memory", {}) if isinstance(self.config, dict) else {}
            min_similarity = float(mem_cfg.get("recall_min_similarity", 0.20))
            hits = index.search(
                user_goal,
                conversation_id=conversation_id,
                job_id=scope.get("job_id"),
                scope_bound=bool(scope.get("bound")),
                top_k=3,
                min_similarity=min_similarity,
                exclude_message_ids=exclude,
            )
            residue = store.get_residue(conversation_id)
            return index.render_context(
                residue,
                hits,
                active_conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("Conversation retrieval skipped: %s", exc)
            return ""

    def _index_remembered_turn(self, assistant_id: str, content: str) -> None:
        """Index the exchange this assistant message closes. Failures stay off the task path."""
        index = self.conversation_index
        store = self.conversation_store
        conversation_id = self._bound_conversation_id()
        if index is None or store is None or not conversation_id or not assistant_id:
            return
        try:
            match = next(
                (item for item in store.iter_exchanges(conversation_id) if item.message_id == assistant_id),
                None,
            )
            if match is None:
                return
            scope = store.get_conversation_scope(conversation_id) or {}
            conversation = store.get_conversation(conversation_id) or {}
            index.upsert(
                message_id=assistant_id,
                conversation_id=conversation_id,
                job_id=scope.get("job_id") or "",
                scope_bound=bool(scope.get("bound")),
                conversation_title=conversation.get("title") or "",
                user_text=match.user_text,
                assistant_text=content,
                created_at=match.created_at,
            )
        except Exception as exc:
            logger.warning("Could not index conversation turn %s: %s", assistant_id, exc)

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
        trace = {
            "headline": headline,
            "detail": content,
            "thoughts": public_thoughts,
            "actions": public_actions,
            "outcome": outcome,
        }
        run = current_run.get()
        if run is not None:
            run.trace = trace
        if run is None or run.conversation_id == (self.active_conversation_id or ""):
            self.last_turn_trace = trace
        if not content:
            return
        self._bound_history().append({"role": "assistant", "content": content})
        conversation_id = self._bound_conversation_id()
        if self.conversation_store and conversation_id:
            assistant_id = self.conversation_store.add_message(
                conversation_id,
                role="assistant",
                content=content,
                metadata={
                    "headline": headline,
                    "thoughts": public_thoughts,
                    "actions": public_actions,
                    "outcome": outcome,
                },
            )
            self._index_remembered_turn(assistant_id, content)

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
        rec = self._finish_and_index_episode(episode_id, outcome=outcome, final_summary=headline)
        if self.reflector and self.memory_enabled and rec:
            try:
                self.reflector.enqueue(rec)
            except Exception as ref_err:
                logger.warning("Could not enqueue episode for reflection: %s", ref_err)
        if speak:
            self._speak_task_summary(headline, speak_summary=speak_summary)
        self._remember_turn(
            detail,
            headline=headline,
            thoughts=thoughts,
            actions=actions,
            outcome=outcome,
        )
        self._mark_run_finished()
        self._emit("on_task_finish", headline, success)
        stopped = outcome == "aborted" and "stopped by user" in headline.lower()
        trace = self._active_trace()
        return {
            "success": success,
            "stopped": stopped,
            "summary": headline,
            "detail": detail,
            "steps_taken": step,
            "thoughts": list(trace.get("thoughts") or []),
            "actions": list(trace.get("actions") or []),
        }

    def _active_trace(self) -> Dict[str, Any]:
        run = current_run.get()
        if run is not None and run.trace:
            return run.trace
        return self.last_turn_trace

    def _admit_run(self, conversation_id: str) -> tuple:
        """Reserve a run, or return a busy result when this machine or chat is taken."""
        run = TaskRun(
            run_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            stop=threading.Event(),
            history=self._history_for_conversation(conversation_id),
        )
        with self._task_runs_lock:
            live = [item for item in self._task_runs.values() if not item.finished]
            if not self.parallel_actions_allowed() and live:
                return None, self._busy_result("local")
            if conversation_id and any(item.conversation_id == conversation_id for item in live):
                return None, self._busy_result("conversation")
            if not conversation_id and live:
                return None, self._busy_result("conversation")
            self._task_runs[run.run_id] = run
        return run, None

    def run_task(
        self,
        user_goal: str,
        max_steps: Optional[int] = None,
        speak_summary: bool = True,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run until the goal is finished, the task stalls, or the step ceiling is hit.

        A cloud provider can host more than one of these. Local Ollama admits one.
        """
        step_ceiling, explicit_step_cap = self._resolve_step_ceiling(max_steps)
        conv_id = conversation_id or self.active_conversation_id or ""
        conv_id = str(conv_id).strip()
        run, busy = self._admit_run(conv_id)
        if busy is not None:
            logger.info("Task not started: %s", busy.get("summary"))
            return busy

        logger.info("Starting EidosBot task: %s", user_goal)
        run_token = current_run.set(run)
        model_token = model_run_id.set(run.run_id)
        recall_token = recall_binding.set(RecallBinding(conversation_id=conv_id))
        try:
            self.compute_policy.touch_user_activity()
            self._reset_model_abort()

            # Re-activate assigned skill if a job is in focus; otherwise clear standalone skill
            if self.active_job:
                skill_info = self.active_job.get("skill")
                skill_name = (
                    skill_info.get("name")
                    if isinstance(skill_info, dict)
                    else self.active_job.get("skill_name")
                )
                if not skill_name and self.active_job.get("skills"):
                    first_skill = self.active_job["skills"][0]
                    skill_name = first_skill.get("name") if isinstance(first_skill, dict) else str(first_skill)
                if skill_name:
                    self.active_skill = self.skills.load_skill(skill_name)
            else:
                self.active_skill = None
            self._emit("on_task_start", user_goal)
            if not self.parallel_actions_allowed():
                self._stop_voice()
            return self._run_task_body(
                user_goal,
                step_ceiling=step_ceiling,
                explicit_step_cap=explicit_step_cap,
                speak_summary=speak_summary,
            )
        finally:
            current_run.reset(run_token)
            model_run_id.reset(model_token)
            recall_binding.reset(recall_token)
            with self._task_runs_lock:
                self._task_runs.pop(run.run_id, None)
            self._drop_model_run(run.run_id)
            self._last_task_finished_time = time.monotonic()

    def _run_task_body(
        self,
        user_goal: str,
        *,
        step_ceiling: int,
        explicit_step_cap: bool,
        speak_summary: bool,
    ) -> Dict[str, Any]:
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
            self._mark_run_finished()
            self._emit("on_task_finish", summary, False)
            return {
                "success": False,
                "summary": summary,
                "steps_taken": 0,
            }

        if self.is_stop_requested():
            summary = "Task stopped by user."
            self._finish_and_index_episode(episode_id, outcome="aborted", final_summary=summary)
            self._mark_run_finished()
            self._emit("on_task_finish", summary, False)
            return {
                "success": False,
                "stopped": True,
                "summary": summary,
                "steps_taken": 0,
            }

        # Macro retrieval of past experience (golden path + pitfall)
        active_win = self.windows.get_active_window_info()
        self._set_bound_scope(ScopeResolver.resolve_current_scope(
            cwd=os.getcwd(),
            active_window=active_win,
            active_skill=self.active_skill,
            active_job=self.active_job,
        ))
        if self.recall_interceptor:
            self.recall_interceptor.set_current_scope(self._bound_scope())
            self._sync_recall_conversation()

        episodic_ctx = ""
        if self.memory_enabled and self.episodic_store:
            try:
                mem_cfg = self.config.get("memory", {}) if isinstance(self.config, dict) else {}
                macro_success = float(
                    mem_cfg.get(
                        "macro_recall_min_similarity_success",
                        mem_cfg.get("macro_min_similarity", 0.45),
                    )
                )
                macro_pitfall = float(
                    mem_cfg.get(
                        "macro_recall_min_similarity_pitfall",
                        mem_cfg.get("macro_min_similarity", 0.35),
                    )
                )
                episodic_ctx = self.episodic_store.retrieve_macro_context(
                    user_goal,
                    scope=self._bound_scope(),
                    min_similarity_success=macro_success,
                    min_similarity_pitfall=macro_pitfall,
                )
                if episodic_ctx:
                    logger.info("Macro recall retrieved episodic context for task: %s", user_goal)
            except Exception as e:
                logger.warning("Failed to retrieve macro episodic context: %s", e)

        user_profile_ctx = self.user_store.get_prompt_context() if self.memory_enabled else None
        system_prompt = self._assemble_system_prompt(
            active_win,
            user_profile_ctx=user_profile_ctx,
            episodic_ctx=episodic_ctx,
        )

        # Construct messages including a short verbatim tail and retrieved older turns
        conversation_block = self._conversation_context_block(user_goal)
        if conversation_block:
            system_prompt = self._attach_conversation_context(system_prompt, conversation_block)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        history = self._bound_history()
        for msg in self._conversation_tail(history):
            messages.append(msg)

        messages.append({"role": "user", "content": f"User Goal: {user_goal}"})
        history.append({"role": "user", "content": user_goal})
        bound_conversation = self._bound_conversation_id()
        if self.conversation_store and bound_conversation:
            old_conv = self.conversation_store.get_conversation(bound_conversation)
            old_title = old_conv.get("title") if old_conv else None
            self.conversation_store.add_message(bound_conversation, role="user", content=user_goal)
            new_conv = self.conversation_store.get_conversation(bound_conversation)
            new_title = new_conv.get("title") if new_conv else None
            if (
                old_title != new_title
                and new_title
                and bound_conversation == (self.active_conversation_id or "")
            ):
                self._emit("on_conversation_title_changed", new_title)

        step = 0
        task_summary = ""
        turn_thoughts: List[str] = []
        turn_actions: List[Dict[str, str]] = []
        stall = StallTracker()

        while step < step_ceiling:
            step += 1
            step_start_time = time.monotonic()
            logger.info("=== Cycle Step %d / %d ===", step, step_ceiling)
            self._emit("on_step", step, step_ceiling)

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
            if self.is_stop_requested():
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
            window_now = active_win
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
            if self.is_stop_requested() or self.kill_switch.is_triggered() or (
                isinstance(getattr(response, "raw", None), dict) and response.raw.get("cancelled")
            ):
                logger.warning("Agent loop stopped during model generation at step %d", step)
                killed = self.kill_switch.is_triggered()
                return self._complete_run(
                    episode_id=episode_id,
                    headline=(
                        f"Aborted by emergency kill switch at step {step}."
                        if killed
                        else "Task stopped by user."
                    ),
                    success=False,
                    outcome="aborted",
                    step=step,
                    speak=False,
                    speak_summary=speak_summary,
                    thoughts=turn_thoughts,
                    actions=turn_actions,
                    include_trace=bool(turn_thoughts or turn_actions),
                )

            if response.thinking:
                logger.info("[Thinking]: %s", response.thinking)
                self._emit("on_thought", response.thinking)
                turn_thoughts.append(response.thinking)

            # Record assistant response. Secret values are stripped before they enter history.
            self._redact_secret_tool_args(response.tool_calls)
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

                if is_model_error and step < step_ceiling:
                    logger.warning("Model communication error encountered on step %d: %s", step, response.content)
                    # Attempt recovery: aggressively compact context and retry with Chutes if available
                    self._compact_context_if_needed(messages, max_chars=24000)
                    router = getattr(self, "router", None)
                    if router is not None and hasattr(router, "configured_cloud_client"):
                        chutes_client = router.configured_cloud_client()
                    else:
                        chutes_client = getattr(router, "chutes", None)
                    if chutes_client and chutes_client.is_configured:
                        cloud_label = getattr(chutes_client, "service_name", "cloud")
                        logger.info("Retrying step %d with %s...", step, cloud_label)
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
                                self._redact_secret_tool_args(response.tool_calls)
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
                            logger.error("Step %d cloud retry also encountered error: %s", step, retry_exc)

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
            stall_stop = None
            stall_nudge = None

            for tc in response.tool_calls:
                if self.is_stop_requested() or self.kill_switch.is_triggered():
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
                else:
                    window_after = window_now
                    if tc.name in StallTracker.POINTER_ACTS:
                        window_after = self.windows.get_active_window_info()
                    verdict = stall.observe(
                        tc.name,
                        tc.arguments,
                        tool_result,
                        window_now,
                        window_after,
                    )
                    window_now = window_after
                    if verdict is not None:
                        if verdict.stop:
                            stall_stop = verdict
                        elif stall_nudge is None:
                            stall_nudge = verdict

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
                if stall_stop is not None:
                    break

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
                stopped = self.is_stop_requested() or self.kill_switch.is_triggered()
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

            if stall_stop is not None:
                logger.warning("Agent loop stalled at step %d: %s", step, stall_stop.detail)
                return self._complete_run(
                    episode_id=episode_id,
                    headline=stall_stop.stop_headline(),
                    success=False,
                    outcome="stalled",
                    step=step,
                    speak=True,
                    speak_summary=speak_summary,
                    thoughts=turn_thoughts,
                    actions=turn_actions,
                    include_trace=True,
                )

            if stall_nudge is not None:
                stall.commit_nudge(stall_nudge.signature)
                logger.info("Stall nudge at step %d: %s", step, stall_nudge.detail)
                messages.append({"role": "user", "content": stall_nudge.nudge_text()})

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
        elif step >= step_ceiling:
            if explicit_step_cap:
                headline = f"Reached the step limit ({step_ceiling}) before the task was finished."
            else:
                headline = (
                    f"Hit the safety step fuse ({step_ceiling}) before the task was finished."
                )
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

    def _redact_secret_tool_args(self, tool_calls: Optional[List[Any]]) -> None:
        """Drop set_secret values before tool calls are logged, shown, or stored."""
        for tc in tool_calls or []:
            if getattr(tc, "name", "") != "set_secret":
                continue
            arguments = getattr(tc, "arguments", None)
            if not isinstance(arguments, dict) or "values" not in arguments:
                continue
            raw_values = arguments.get("values")
            secret_values: Dict[str, str] = {}
            if isinstance(raw_values, dict):
                secret_values = {
                    str(key): "" if val is None else str(val)
                    for key, val in raw_values.items()
                }
            tc.secret_values = secret_values
            redacted = {key: val for key, val in arguments.items() if key != "values"}
            redacted["keys"] = sorted(secret_values)
            tc.arguments = redacted

    def _dispatch_tool(self, tool_call: ToolCall) -> tuple[Dict[str, Any], bool]:
        """Dispatch a single tool call to the action or perception engine."""
        self._redact_secret_tool_args([tool_call])
        name = tool_call.name
        args = tool_call.arguments
        logger.info("Dispatching tool '%s' with args: %s", name, args)

        if name == "finish_task":
            # Standalone skills without an active job reset on completion.
            # Active job focus survives the turn (PROJECT_JOBS.md Mandate 5).
            if not self.active_job:
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
            cwd_arg = args.get("cwd")
            sandboxed = self.shell.resolve_sandboxed(args.get("sandboxed"), cmd, cwd_arg)
            cwd = cwd_arg or self.workspace_dir
            res = self.shell.execute(cmd, cwd=cwd, sandboxed=sandboxed)
            if isinstance(res, dict):
                res["sandboxed_resolved"] = sandboxed
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
            description = args.get("description")
            project = args.get("project")
            phase = args.get("phase")
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
                    description=description,
                    project=project,
                    phase=phase,
                )
                # Automatically enter Job Mode and activate assigned skill
                manifest = self.jobs.load_manifest(str(queue.output_dir))
                self.active_job = manifest or queue.to_manifest_dict()
                self.active_integration_focus = None
                self.jobs.focus_manager.set_primary(queue.job_id)
                if queue.skill_name:
                    skill_res = self.skills.load_skill(queue.skill_name)
                    if skill_res:
                        self.active_skill = skill_res
                self._persist_scope_on_conversation(self.active_conversation_id)
                self._emit("on_job_focus")
                return {
                    "success": True,
                    "job_id": queue.job_id,
                    "job_name": queue.job_name,
                    "skill_name": queue.skill_name,
                    "total_items": queue.total_items,
                    "output_dir": queue.output_dir,
                    "status": queue.status,
                    "job_mode": "activated",
                    "manifest_file": str(Path(queue.output_dir) / "job.yaml"),
                }, False
            except Exception as e:
                return {"success": False, "error": str(e)}, False

        # Project Jobs & Scoped Working Memory Subsystem (PROJECT_JOBS.md)
        if name == "create_job":
            project_name = args.get("name", "")
            if not project_name:
                return {"success": False, "error": "Job 'name' is required."}, False
            summary = args.get("summary")
            goal = args.get("goal")
            this_is = args.get("this_is")
            this_is_not = args.get("this_is_not")
            skills = args.get("skills")
            output_dir = args.get("output_dir")

            allowed, reason = self.gatekeeper.evaluate(
                "create_job", {"name": project_name, "output_dir": output_dir}
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            try:
                brief = self.jobs.create_project_job(
                    name=project_name,
                    summary=summary,
                    goal=goal,
                    this_is=this_is,
                    this_is_not=this_is_not,
                    skills=skills if isinstance(skills, list) else None,
                    output_dir=output_dir,
                )
                self.active_job = brief.to_dict()
                self.active_integration_focus = None
                self.jobs.focus_manager.set_primary(brief.job_id)

                activated_skill = None
                if brief.skills:
                    first_sk = brief.skills[0].name
                    sk_res = self.skills.load_skill(first_sk)
                    if sk_res:
                        self.active_skill = sk_res
                        activated_skill = first_sk

                logger.info("Created and focused project job '%s' (%s)", brief.name, brief.job_id)
                self._persist_scope_on_conversation(self.active_conversation_id)
                self._emit("on_job_focus")
                return {
                    "success": True,
                    "job_id": brief.job_id,
                    "name": brief.name,
                    "status": brief.status,
                    "focus": "primary",
                    "job_dir": brief.directories.job_dir,
                    "notes_dir": brief.directories.notes_dir,
                    "artifacts_dir": brief.directories.artifacts_dir,
                    "manifest_file": brief.directories.manifest_file,
                    "activated_skill": activated_skill,
                    "message": f"Project job '{brief.name}' ({brief.job_id}) created and set as primary focus.",
                }, False
            except Exception as e:
                logger.error("Failed to create project job: %s", e)
                return {"success": False, "error": str(e)}, False

        if name in ("focus_job", "load_job"):
            job_target = args.get("job_id_or_path") or args.get("job_id") or args.get("job_path") or ""
            if not job_target:
                return {"success": False, "error": "job_id_or_path is required."}, False

            allowed, reason = self.gatekeeper.evaluate("focus_job", {"job_id": job_target})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            manifest = self.jobs.load_manifest(job_target)
            if not manifest:
                queue = self.jobs.load_job(job_target)
                if queue:
                    manifest = queue.to_manifest_dict()
                else:
                    return {"success": False, "error": f"Could not find or load job '{job_target}'."}, False

            self.active_job = manifest
            self.active_integration_focus = None
            job_id_val = str(manifest.get("job_id") or job_target)
            self.jobs.focus_manager.set_primary(job_id_val)

            # Activate declared skill if present
            skill_name = manifest.get("skill_name")
            if not skill_name and isinstance(manifest.get("skill"), dict):
                skill_name = manifest["skill"].get("name")
            elif manifest.get("skills") and isinstance(manifest["skills"], list):
                first_skill = manifest["skills"][0]
                skill_name = first_skill.get("name") if isinstance(first_skill, dict) else str(first_skill)

            if skill_name:
                skill_res = self.skills.load_skill(skill_name)
                if skill_res:
                    self.active_skill = skill_res

            job_title = str(manifest.get("name") or manifest.get("job_name") or "job")
            logger.info("Focused primary job '%s' (%s) with skill '%s'", job_title, job_id_val, skill_name)
            self._persist_scope_on_conversation(self.active_conversation_id)
            self._emit("on_job_focus")
            return {
                "success": True,
                "status": "activated",
                "focus": "primary",
                "job_id": job_id_val,
                "name": job_title,
                "assigned_skill": skill_name,
                "job_dir": manifest.get("directories", {}).get("job_dir"),
                "message": f"Primary focus switched to '{job_title}' ({job_id_val}).",
            }, False

        if name == "list_jobs":
            group = args.get("group")
            include_archived = bool(args.get("include_archived", False))
            allowed, reason = self.gatekeeper.evaluate("list_jobs", {"group": group})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            jobs_list = self.jobs.list_jobs(group=group, include_archived=include_archived)
            primary_id = self.jobs.focus_manager.get_primary()
            return {
                "success": True,
                "count": len(jobs_list),
                "primary": primary_id,
                "jobs": jobs_list,
            }, False

        if name == "update_job_brief":
            target = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
            if not target and self.active_job:
                target = self.active_job.get("directories", {}).get("job_dir")
            if not target:
                return {"success": False, "error": "No target job specified and no active primary job is focused."}, False

            allowed, reason = self.gatekeeper.evaluate("update_job_brief", {"job_id": target})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            res = self.jobs.update_brief(
                job_id_or_path=target,
                summary=args.get("summary"),
                goal=args.get("goal"),
                phase=args.get("phase"),
                this_is=args.get("this_is"),
                this_is_not=args.get("this_is_not"),
                add_distinct_from=args.get("add_distinct_from"),
                add_decision=args.get("add_decision"),
                add_open_question=args.get("add_open_question"),
                remove_open_question=args.get("remove_open_question"),
                add_constraint=args.get("add_constraint"),
                add_skill=args.get("add_skill"),
                add_document=args.get("add_document"),
            )
            # If target is active job, reload active_job manifest in memory
            if self.active_job:
                curr_id = self.active_job.get("job_id")
                curr_dir = self.active_job.get("directories", {}).get("job_dir")
                if target in (curr_id, curr_dir):
                    updated_manifest = self.jobs.load_manifest(target)
                    if updated_manifest:
                        self.active_job = updated_manifest
            self._emit("on_job_focus")
            return res, False

        if name == "read_job_document":
            doc_path = args.get("path", "")
            heading = args.get("heading")
            if not doc_path:
                return {"success": False, "error": "Document 'path' is required."}, False

            target = (self.active_job.get("job_id") or self.active_job.get("directories", {}).get("job_dir")) if self.active_job else None
            if not target:
                return {"success": False, "error": "No active primary job. Call focus_job or create_job before reading job documents."}, False

            allowed, reason = self.gatekeeper.evaluate("read_job_document", {"path": doc_path})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            res = self.jobs.read_document(target, relative_path=doc_path, heading=heading)
            return res, False

        if name == "close_job":
            if not self.active_job and not self.active_integration_focus:
                return {
                    "success": True,
                    "message": "No job or integration is currently focused. Operating in base desktop mode.",
                }, False
            prev_focus = (
                (self.active_job.get("name") or self.active_job.get("job_name", "job"))
                if self.active_job
                else f"Integration ({self.active_integration_focus})"
            )
            self.active_job = None
            self.active_skill = None
            self.active_integration_focus = None
            self.jobs.focus_manager.clear_all()
            logger.info("Closed focus on '%s'. Returned to base desktop mode.", prev_focus)
            self._persist_scope_on_conversation(self.active_conversation_id)
            self._emit("on_job_focus")
            return {
                "success": True,
                "status": "closed",
                "previous_focus": prev_focus,
                "message": f"Focus on '{prev_focus}' closed. Returned to base desktop mode.",
            }, False

        if name == "list_integrations":
            allowed, reason = self.gatekeeper.evaluate("list_integrations", {})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            items = self.mcp_config.list_integrations_summary()
            return {"success": True, "integrations": items, "count": len(items)}, False

        if name == "list_mcp_servers":
            allowed, reason = self.gatekeeper.evaluate("list_mcp_servers", {})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            servers = self.mcp_config.list_servers_public()
            return {"success": True, "servers": servers, "count": len(servers)}, False

        if name == "add_mcp_server":
            server_name = (args.get("name") or "").strip()
            if not server_name:
                return {"success": False, "error": "name is required."}, False
            if args.get("env") or args.get("headers"):
                return {
                    "success": False,
                    "error": "put tokens in set_secret and reference them with env_from or headers_from",
                }, False
            command = args.get("command") if "command" in args else None
            argv = args.get("args") if isinstance(args.get("args"), list) else None
            url = args.get("url") if "url" in args else None
            try:
                allow_key = self.mcp_config.launch_allowlist_key(server_name, command, argv, url)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}, False
            allowed, reason = self.gatekeeper.evaluate(
                "add_mcp_server",
                {"command": allow_key, "name": server_name},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            try:
                entry = self.mcp_config.upsert_server(
                    name=server_name,
                    command=command,
                    args=argv,
                    url=url,
                    cwd=args.get("cwd") if "cwd" in args else None,
                    env_from=args.get("env_from") if "env_from" in args else None,
                    headers_from=args.get("headers_from") if "headers_from" in args else None,
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}, False
            try:
                self.mcp_client.close_server(server_name)
            except Exception as exc:
                logger.debug("Could not reset MCP session '%s': %s", server_name, exc)
            return {"success": True, "server": entry}, False

        if name == "remove_mcp_server":
            server_name = (args.get("name") or "").strip()
            if not server_name:
                return {"success": False, "error": "name is required."}, False
            try:
                allow_key = f"remove_mcp_server:{server_name}"
            except Exception:
                allow_key = "remove_mcp_server"
            allowed, reason = self.gatekeeper.evaluate(
                "remove_mcp_server",
                {"command": allow_key, "name": server_name},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            try:
                removed = self.mcp_config.remove_server(server_name)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}, False
            try:
                self.mcp_client.close_server(server_name)
            except Exception:
                pass
            if not removed:
                return {"success": False, "error": f"No MCP server named '{server_name}'."}, False
            return {"success": True, "name": server_name, "removed": True}, False

        if name == "set_secret":
            secret_name = (args.get("name") or "").strip()
            if not secret_name:
                return {"success": False, "error": "name is required."}, False
            values = getattr(tool_call, "secret_values", None)
            if not isinstance(values, dict):
                raw_values = args.get("values") if isinstance(args.get("values"), dict) else {}
                values = {str(k): "" if v is None else str(v) for k, v in raw_values.items()}
            key_names = sorted(values)
            allowed, reason = self.gatekeeper.evaluate(
                "set_secret",
                {"command": f"set_secret:{secret_name}", "name": secret_name, "keys": key_names},
            )
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            try:
                saved_keys = self.mcp_config.secrets.set_values(secret_name, values)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}, False
            for entry in self.mcp_config.list_servers_public():
                refs = list(entry.get("env_from") or []) + list(entry.get("headers_from") or [])
                if secret_name in refs or entry.get("name") == secret_name:
                    try:
                        self.mcp_client.close_server(str(entry.get("name")))
                    except Exception:
                        pass
            return {"success": True, "name": secret_name, "keys": saved_keys}, False

        if name == "focus_integration":
            target_int = (args.get("name") or "").strip()
            if not target_int:
                return {"success": False, "error": "name is required."}, False

            allowed, reason = self.gatekeeper.evaluate("focus_integration", {"name": target_int})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            # Clear primary job; set standalone integration focus (PROJECT_JOBS.md Section 13.2)
            self.active_job = None
            self.active_skill = None
            self.active_integration_focus = target_int
            if hasattr(self, "jobs") and self.jobs:
                self.jobs.focus_manager.set_integration_focus(target_int)

            rec = self.mcp_config.get_integration(target_int)
            server_name = rec.server if rec else target_int
            tool_list: List[Dict[str, Any]] = []
            try:
                tool_list = self.mcp_client.list_tools(server_name)
            except Exception as e:
                logger.debug("Failed querying tools for focused integration '%s': %s", target_int, e)

            logger.info(
                "Focused standalone integration '%s' (server: '%s', tools: %d)",
                target_int,
                server_name,
                len(tool_list),
            )
            self._persist_scope_on_conversation(self.active_conversation_id)
            self._emit("on_job_focus")
            return {
                "success": True,
                "status": "activated",
                "focus": "integration",
                "name": target_int,
                "server": server_name,
                "tools_count": len(tool_list),
                "description": rec.description if rec else "",
                "message": f"Integration focus switched to '{target_int}' ({len(tool_list)} tools available).",
            }, False

        if name == "run_batch_job":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
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
                    stop_event=(current_run.get().stop if current_run.get() is not None else threading.Event()),
                )
            if job_id and self.active_job:
                updated_manifest = self.jobs.load_manifest(job_id)
                if updated_manifest:
                    self.active_job = updated_manifest
            return res, False

        if name == "step_batch_job":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
            allowed, reason = self.gatekeeper.evaluate("step_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.step_next(job_id_or_path=job_id)
            if job_id and self.active_job:
                updated_manifest = self.jobs.load_manifest(job_id)
                if updated_manifest:
                    self.active_job = updated_manifest
            return res, False

        if name == "get_job_status":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
            res = self.jobs.get_status(job_id_or_path=job_id)
            if job_id and self.active_job:
                updated_manifest = self.jobs.load_manifest(job_id)
                if updated_manifest:
                    self.active_job = updated_manifest
            return res, False

        if name == "pause_batch_job":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
            allowed, reason = self.gatekeeper.evaluate("pause_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.pause(job_id_or_path=job_id)
            if job_id and self.active_job:
                updated_manifest = self.jobs.load_manifest(job_id)
                if updated_manifest:
                    self.active_job = updated_manifest
            return res, False

        if name == "resume_batch_job":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
            allowed, reason = self.gatekeeper.evaluate("resume_batch_job", {"job_id": job_id})
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False
            res = self.jobs.resume(job_id_or_path=job_id)
            if job_id and self.active_job:
                updated_manifest = self.jobs.load_manifest(job_id)
                if updated_manifest:
                    self.active_job = updated_manifest
            return res, False

        if name == "generate_batch_summary":
            job_id = args.get("job_id") or (self.active_job.get("job_id") if self.active_job else None)
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

        # --- Model Context Protocol (MCP) Dynamic Tool Dispatch (PROJECT_JOBS.md Section 13) ---
        if name.startswith("mcp__"):
            parts = name.split("__", 2)
            if len(parts) < 3:
                return {"success": False, "error": f"Invalid MCP tool name: '{name}'"}, False
            server_name = parts[1]
            remote_tool = parts[2]

            annot = self.mcp_client.get_tool_annotation(server_name, remote_tool)
            gate_details = {
                "server": server_name,
                "tool": remote_tool,
                "mcp_target": name,
                "annotations": annot,
                "arguments": args,
            }
            allowed, reason = self.gatekeeper.evaluate(name, gate_details)
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            # Tag current task scope integration for episodic memory (PROJECT_JOBS.md Section 13.4)
            scope = self._bound_scope()
            if scope:
                scope.integration = server_name

            res = self.mcp_client.call_tool(server_name, remote_tool, args)
            return res, False

        if name == "call_mcp_tool":
            server_name = (args.get("server") or "").strip()
            remote_tool = (args.get("tool") or "").strip()
            tool_args = args.get("arguments") or {}
            if not isinstance(tool_args, dict):
                tool_args = {}
            if not server_name or not remote_tool:
                return {"success": False, "error": "server and tool are required."}, False

            annot = self.mcp_client.get_tool_annotation(server_name, remote_tool)
            gate_details = {
                "server": server_name,
                "tool": remote_tool,
                "mcp_target": f"mcp__{server_name}__{remote_tool}",
                "annotations": annot,
                "arguments": tool_args,
            }
            allowed, reason = self.gatekeeper.evaluate("call_mcp_tool", gate_details)
            if not allowed:
                return {"success": False, "error": f"Denied by safety policy: {reason}"}, False

            # Tag current task scope integration for episodic memory (PROJECT_JOBS.md Section 13.4)
            scope = self._bound_scope()
            if scope:
                scope.integration = server_name

            res = self.mcp_client.call_tool(server_name, remote_tool, tool_args)
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
