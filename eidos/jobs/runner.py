"""Domain-agnostic state machine batch job runner (queue.json)."""

import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union
import uuid
import yaml

from eidos.jobs.summary import RankingSummaryGenerator
from eidos.jobs.types import JobQueue, JobSettings, QueueItem
from eidos.skills.manager import SkillManager

logger = logging.getLogger(__name__)


class BatchJobRunner:
    """Manages persistent batch queues (queue.json) with fresh context per item."""

    def __init__(
        self,
        skills_manager: Optional[SkillManager] = None,
        default_jobs_dir: Optional[str] = None,
        default_processor: Optional[Callable[[QueueItem, Dict[str, Any]], Dict[str, Any]]] = None,
        kill_switch: Optional[Any] = None,
        summary_generator: Optional[RankingSummaryGenerator] = None,
    ):
        self.skills = skills_manager or SkillManager()
        self.skills_manager = self.skills
        self.default_jobs_dir = Path(
            os.path.abspath(os.path.expanduser(default_jobs_dir or "~/EidosWorkspace/jobs"))
        )
        self.default_processor = default_processor
        self.summary_generator = summary_generator or RankingSummaryGenerator()
        self.active_job: Optional[JobQueue] = None
        self.milestone_listeners: List[Callable[[JobQueue, QueueItem], None]] = []

        # Background worker management & kill switch responsiveness
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._active_worker_job_id: Optional[str] = None
        self.kill_switch: Optional[Any] = None
        if kill_switch:
            self.set_kill_switch(kill_switch)

    def set_kill_switch(self, kill_switch: Any) -> None:
        """Register kill switch callback for emergency halts."""
        self.kill_switch = kill_switch
        if hasattr(kill_switch, "register_callback"):
            try:
                kill_switch.register_callback(self._on_kill_switch_triggered)
            except Exception as e:
                logger.debug("Could not register kill switch callback in BatchJobRunner: %s", e)

    def _on_kill_switch_triggered(self) -> None:
        """Emergency callback triggered when the kill switch is triggered."""
        logger.warning("Emergency kill switch fired: halting batch runner worker.")
        self._stop_event.set()
        if self._active_worker_job_id:
            try:
                self.pause(self._active_worker_job_id)
            except Exception as e:
                logger.debug("Failed to pause batch job on kill switch: %s", e)

    def is_worker_running(self) -> bool:
        """Return True if a background worker thread is actively executing."""
        with self._worker_lock:
            return self._worker_thread is not None and self._worker_thread.is_alive()

    def get_active_worker_job_id(self) -> Optional[str]:
        """Return the job_id currently being executed by the background worker, if any."""
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return self._active_worker_job_id
            return None

    def add_milestone_listener(self, listener: Callable[[JobQueue, QueueItem], None]) -> None:
        """Register a callback invoked when an item milestone or job completion occurs."""
        self.milestone_listeners.append(listener)

    def _slugify(self, text: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", text.strip().lower())
        return re.sub(r"_+", "_", clean).strip("_") or "job"

    def _find_nearest_group_yaml(self, job_dir: Path) -> Optional[Path]:
        """Walk upward from job_dir toward default_jobs_dir to find the nearest group.yaml."""
        try:
            curr = job_dir.resolve().parent
            stop_dir = self.default_jobs_dir.resolve().parent
            while curr and curr != stop_dir and curr != curr.parent:
                candidate = curr / "group.yaml"
                if candidate.is_file():
                    return candidate
                if curr == self.default_jobs_dir.resolve():
                    break
                curr = curr.parent
        except Exception:
            pass
        return None

    def _resolve_job_dir(self, target: str) -> Optional[Path]:
        """Resolve a path, folder name, or job ID to a job directory Path containing job.yaml or queue.json."""
        if not target or not isinstance(target, str):
            return None
        p = Path(os.path.abspath(os.path.expanduser(target)))
        if p.is_file() and p.name in ("job.yaml", "queue.json"):
            return p.parent
        if p.is_dir() and ((p / "job.yaml").is_file() or (p / "queue.json").is_file()):
            return p

        # Check directly in default_jobs_dir
        if self.default_jobs_dir.exists():
            cand = self.default_jobs_dir / target
            if cand.is_dir() and ((cand / "job.yaml").is_file() or (cand / "queue.json").is_file()):
                return cand
            if (cand / "job.yaml").is_file():
                return cand.parent

            # Search subdirectories under default_jobs_dir for folder match or job.yaml/queue.json
            for cand_yaml in self.default_jobs_dir.rglob("job.yaml"):
                parent_dir = cand_yaml.parent
                if target == parent_dir.name or target in str(parent_dir):
                    return parent_dir

            for cand_q in self.default_jobs_dir.rglob("queue.json"):
                parent_dir = cand_q.parent
                if target == parent_dir.name or target in str(parent_dir):
                    return parent_dir

        # Fallback check under parent workspace (e.g. ~/EidosWorkspace/documents/<target>)
        ws_parent = self.default_jobs_dir.parent
        if ws_parent.exists():
            cand_doc = ws_parent / "documents" / target
            if cand_doc.is_dir() and ((cand_doc / "job.yaml").is_file() or (cand_doc / "queue.json").is_file()):
                return cand_doc

        return None

    def _resolve_queue_file(self, target: str) -> Optional[Path]:
        """Resolve a path or job ID to a queue.json file Path."""
        p = Path(os.path.abspath(os.path.expanduser(target)))
        if p.is_file():
            if p.name == "queue.json":
                return p
            if p.name == "job.yaml" and (p.parent / "queue.json").is_file():
                return p.parent / "queue.json"
        if p.is_dir() and (p / "queue.json").is_file():
            return p / "queue.json"

        # Search inside default_jobs_dir
        if self.default_jobs_dir.exists():
            cand_dir = self.default_jobs_dir / target
            if (cand_dir / "queue.json").is_file():
                return cand_dir / "queue.json"
            # Try matching by prefix, folder name, or nested subdirectories
            for sub in self.default_jobs_dir.rglob("queue.json"):
                if target in str(sub.parent.name) or target in str(sub.parent):
                    return sub

        # Fallback check under parent workspace (e.g. ~/EidosWorkspace/documents/<target>)
        ws_parent = self.default_jobs_dir.parent
        if ws_parent.exists():
            cand_doc = ws_parent / "documents" / target / "queue.json"
            if cand_doc.is_file():
                return cand_doc

        return None

    def save_queue(self, queue: JobQueue, queue_path: Optional[Union[str, Path]] = None) -> None:
        """Atomically persist queue state to queue.json and job.yaml manifest."""
        dest_path = (
            Path(os.path.abspath(os.path.expanduser(queue_path)))
            if queue_path
            else Path(queue.output_dir) / "queue.json"
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        data = queue.model_dump()
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="queue_", suffix=".tmp", dir=str(dest_path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, dest_path)
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error("Failed to atomically save queue to %s: %s", dest_path, e)
            raise

        # Atomically save job.yaml manifest
        yaml_dest = dest_path.parent / "job.yaml"
        skill_meta = None
        if self.skills_manager and queue.skill_name:
            try:
                skill_meta = self.skills_manager.load_skill(queue.skill_name)
            except Exception:
                pass
        manifest_data = queue.to_manifest_dict(skill_meta=skill_meta)
        tmp_y_fd, tmp_y_path = tempfile.mkstemp(
            prefix="job_manifest_", suffix=".tmp", dir=str(dest_path.parent)
        )
        try:
            with os.fdopen(tmp_y_fd, "w", encoding="utf-8") as f:
                yaml.dump(manifest_data, f, sort_keys=False, allow_unicode=True)
            os.replace(tmp_y_path, yaml_dest)
        except Exception as e:
            if os.path.exists(tmp_y_path):
                os.remove(tmp_y_path)
            logger.warning("Failed to save job.yaml manifest to %s: %s", yaml_dest, e)

    def load_manifest(self, job_id_or_path: str) -> Optional[Dict[str, Any]]:
        """Load lean manifest dict from job.yaml or queue, inheriting from group.yaml if present."""
        job_dir = self._resolve_job_dir(job_id_or_path)
        if not job_dir:
            q_path = self._resolve_queue_file(job_id_or_path)
            if q_path:
                job_dir = q_path.parent
        if not job_dir:
            return None

        manifest_data: Optional[Dict[str, Any]] = None
        yaml_path = job_dir / "job.yaml"
        if yaml_path.is_file():
            try:
                with open(yaml_path, "r", encoding="utf-8") as yf:
                    data = yaml.safe_load(yf)
                    if isinstance(data, dict):
                        manifest_data = data
            except Exception as e:
                logger.debug("Failed reading job.yaml: %s", e)

        if manifest_data is None:
            queue = self.load_job(job_id_or_path)
            if queue:
                skill_meta = None
                if self.skills_manager and queue.skill_name:
                    try:
                        skill_meta = self.skills_manager.load_skill(queue.skill_name)
                    except Exception:
                        pass
                manifest_data = queue.to_manifest_dict(skill_meta=skill_meta)

        if not manifest_data:
            return None

        # Check for group.yaml inheritance (PROJECT_JOBS.md section 14)
        group_yaml = self._find_nearest_group_yaml(job_dir)
        if group_yaml and group_yaml.is_file():
            try:
                with open(group_yaml, "r", encoding="utf-8") as gf:
                    g_data = yaml.safe_load(gf)
                    if isinstance(g_data, dict):
                        inherited_skill = g_data.get("skill")
                        if inherited_skill:
                            if not manifest_data.get("skill") and not manifest_data.get("skill_name"):
                                manifest_data["skill_name"] = inherited_skill
                                manifest_data["skill"] = {"name": inherited_skill}
                            elif isinstance(manifest_data.get("skill"), dict) and not manifest_data["skill"].get("name"):
                                manifest_data["skill"]["name"] = inherited_skill
                        inherited_integration = g_data.get("integration")
                        if inherited_integration and not manifest_data.get("integration"):
                            manifest_data["integration"] = inherited_integration
            except Exception as e:
                logger.debug("Failed reading group.yaml from %s: %s", group_yaml, e)

        # Ensure directories dict includes standard paths
        dirs = manifest_data.setdefault("directories", {})
        dirs.setdefault("job_dir", str(job_dir))
        dirs.setdefault("output_dir", str(job_dir))
        dirs.setdefault("manifest_file", str(job_dir / "job.yaml"))
        if (job_dir / "notes").is_dir():
            dirs.setdefault("notes_dir", str(job_dir / "notes"))
        if (job_dir / "artifacts").is_dir():
            dirs.setdefault("artifacts_dir", str(job_dir / "artifacts"))
        if (job_dir / "queue.json").is_file():
            dirs.setdefault("queue_file", str(job_dir / "queue.json"))

        return manifest_data

    def load_job(self, job_id_or_path: str) -> Optional[JobQueue]:
        """Load an existing queue from queue.json path or standalone project job from job.yaml."""
        job_dir = self._resolve_job_dir(job_id_or_path)
        q_path = (job_dir / "queue.json") if job_dir else self._resolve_queue_file(job_id_or_path)

        if q_path and q_path.is_file():
            try:
                with open(q_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                queue = JobQueue.model_validate(data)
                self.active_job = queue

                # Ensure job.yaml manifest exists
                yaml_path = q_path.parent / "job.yaml"
                if not yaml_path.exists():
                    try:
                        with open(yaml_path, "w", encoding="utf-8") as yf:
                            yaml.dump(queue.to_manifest_dict(), yf, sort_keys=False, allow_unicode=True)
                    except Exception as ye:
                        logger.debug("Could not create job.yaml on load: %s", ye)
                return queue
            except Exception as e:
                logger.error("Failed to parse queue from %s: %s", q_path, e)
                return None

        # Standalone project job without batch queue (has job.yaml)
        if job_dir and (job_dir / "job.yaml").is_file():
            manifest = self.load_manifest(str(job_dir))
            if not manifest:
                return None
            job_id = manifest.get("job_id") or f"job_{job_dir.name}"
            job_name = manifest.get("name") or manifest.get("job_name") or job_dir.name
            skill_info = manifest.get("skill") or {}
            skill_name = (
                skill_info.get("name")
                if isinstance(skill_info, dict)
                else manifest.get("skill_name") or ""
            )
            if not skill_name and manifest.get("skills"):
                first_skill = manifest["skills"][0]
                skill_name = first_skill.get("name") if isinstance(first_skill, dict) else str(first_skill)

            desc = None
            if isinstance(manifest.get("identity"), dict):
                desc = manifest["identity"].get("summary")
            elif isinstance(manifest.get("description"), str):
                desc = manifest["description"]

            phase = None
            if isinstance(manifest.get("identity"), dict):
                phase = manifest["identity"].get("phase")
            elif isinstance(manifest.get("phase"), str):
                phase = manifest["phase"]

            queue = JobQueue(
                job_id=job_id,
                job_name=job_name,
                skill_name=skill_name or "",
                description=desc,
                project=manifest.get("project") or (job_dir.parent.name if job_dir.parent.name != "jobs" else None),
                phase=phase,
                output_dir=str(job_dir),
                status=manifest.get("status", "active"),
                total_items=0,
                completed_items=0,
            )
            self.active_job = queue
            return queue

        return None

    def initialize_job(
        self,
        job_name: str,
        skill_name: str,
        input_data: Union[List[Any], str, Dict[str, Any]],
        output_dir: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        project: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> JobQueue:
        """Initialize a new batch job queue with generic input items."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:4]
        job_id = f"job_{ts}_{short_id}"

        # Resolve output directory
        if output_dir:
            out_path = Path(os.path.abspath(os.path.expanduser(output_dir)))
        else:
            if "/" in job_name:
                parts = [self._slugify(part) for part in job_name.split("/") if part.strip()]
                out_path = self.default_jobs_dir / Path(*parts)
            else:
                slug = self._slugify(job_name)
                out_path = self.default_jobs_dir / f"{job_id}_{slug}"

        out_path.mkdir(parents=True, exist_ok=True)

        # Parse and normalize input items
        raw_items: List[Any] = []
        input_file_path: Optional[str] = None
        if isinstance(input_data, str):
            clean_str = input_data.strip()
            # File path check (.json or .csv)
            potential_file = Path(os.path.abspath(os.path.expanduser(clean_str)))
            if potential_file.is_file():
                input_file_path = str(potential_file)
                if potential_file.suffix.lower() == ".json":
                    with open(potential_file, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        raw_items = loaded if isinstance(loaded, list) else [loaded]
                elif potential_file.suffix.lower() == ".csv":
                    with open(potential_file, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        raw_items = list(reader)
                else:
                    with open(potential_file, "r", encoding="utf-8") as f:
                        raw_items = [line.strip() for line in f if line.strip()]
            else:
                # Try parsing as JSON string
                try:
                    loaded = json.loads(clean_str)
                    raw_items = loaded if isinstance(loaded, list) else [loaded]
                except Exception:
                    # Treat newline-delimited lines as items
                    raw_items = [line.strip() for line in clean_str.splitlines() if line.strip()]
        elif isinstance(input_data, list):
            raw_items = input_data
        elif isinstance(input_data, dict):
            raw_items = [input_data]

        queue_items: List[QueueItem] = []
        for i, item in enumerate(raw_items):
            item_id = f"item_{i+1:03d}"
            input_dict: Dict[str, Any] = {}

            if isinstance(item, dict):
                item_dict = dict(item)
                if "id" in item_dict:
                    item_id = str(item_dict.pop("id"))
                input_dict = item_dict
            else:
                input_dict = {"data": item}

            queue_items.append(QueueItem(id=item_id, input=input_dict, status="pending"))

        job_settings = JobSettings(**(settings or {}))

        now_iso = datetime.now(timezone.utc).isoformat()
        queue = JobQueue(
            job_id=job_id,
            job_name=job_name,
            skill_name=skill_name,
            description=description,
            project=project,
            phase=phase,
            input_file=input_file_path,
            created_at=now_iso,
            updated_at=now_iso,
            status="initialized",
            output_dir=str(out_path),
            total_items=len(queue_items),
            completed_items=0,
            failed_items=0,
            skipped_items=0,
            settings=job_settings,
            items=queue_items,
        )

        self.save_queue(queue)
        self.active_job = queue
        logger.info("Initialized batch job '%s' (%s) with %d items at %s", job_name, job_id, len(queue_items), out_path)
        return queue

    def get_status(self, job_id_or_path: Optional[str] = None) -> Dict[str, Any]:
        """Return comprehensive status metrics for a job."""
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"error": "No active batch job found. Specify a job_id or load an existing job."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"error": f"Job '{target}' could not be loaded."}

        active_item = None
        for it in queue.items:
            if it.status == "in_progress":
                active_item = {"id": it.id, "input": it.input, "started_at": it.started_at}
                break

        is_bg = self.is_worker_running() and (self._active_worker_job_id == queue.job_id)

        return {
            "job_id": queue.job_id,
            "job_name": queue.job_name,
            "skill_name": queue.skill_name,
            "status": queue.status,
            "total_items": queue.total_items,
            "completed_items": queue.completed_items,
            "failed_items": queue.failed_items,
            "pending_items": queue.get_pending_count(),
            "in_progress_items": queue.get_in_progress_count(),
            "progress_percent": queue.get_progress_percent(),
            "output_dir": queue.output_dir,
            "active_item": active_item,
            "is_running_in_background": is_bg,
        }

    def step_next(
        self,
        job_id_or_path: Optional[str] = None,
        processor: Optional[Callable[[QueueItem, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute the next pending item in the queue.

        Transitions item: pending -> in_progress -> completed / failed.
        Persists checkpoint after state changes.
        """
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"success": False, "error": "No active batch job found."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"success": False, "error": f"Job '{target}' could not be loaded."}

        if queue.status == "paused":
            return {
                "success": False,
                "status": "paused",
                "message": "Job is currently paused. Resume job before stepping.",
            }

        # Find next pending item
        pending_item = None
        for item in queue.items:
            if item.status == "pending":
                pending_item = item
                break

        if not pending_item:
            # Check if all items are resolved
            summary_info = None
            if queue.get_pending_count() == 0 and queue.get_in_progress_count() == 0:
                queue.status = "completed" if queue.failed_items == 0 else "completed_with_errors"
                queue.updated_at = datetime.now(timezone.utc).isoformat()
                self.save_queue(queue)
                try:
                    summary_info = self.summary_generator.generate(queue)
                except Exception as e:
                    logger.warning("Failed to auto-generate ranking summary for %s: %s", queue.job_id, e)
            return {
                "success": True,
                "done": True,
                "status": queue.status,
                "message": "All items in queue have been processed.",
                "progress_percent": queue.get_progress_percent(),
                "completed_items": queue.completed_items,
                "total_items": queue.total_items,
                "summary": summary_info,
            }

        # Transition item to in_progress
        pending_item.status = "in_progress"
        pending_item.started_at = datetime.now(timezone.utc).isoformat()
        queue.status = "in_progress"
        queue.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_queue(queue)

        # Prepare execution context
        skill_info = self.skills.load_skill(queue.skill_name)
        context = {
            "job_id": queue.job_id,
            "job_name": queue.job_name,
            "skill_name": queue.skill_name,
            "skill": skill_info,
            "output_dir": queue.output_dir,
        }

        # Process the item
        try:
            active_proc = processor or self.default_processor
            if active_proc:
                result_payload = active_proc(pending_item, context)
            else:
                # Default generic execution outcome
                result_payload = {
                    "summary": f"Processed item {pending_item.id}",
                    "artifacts": [],
                    "data": pending_item.input,
                }

            pending_item.status = "completed"
            pending_item.result = result_payload if isinstance(result_payload, dict) else {"summary": str(result_payload)}
            pending_item.error = None
            pending_item.completed_at = datetime.now(timezone.utc).isoformat()
            queue.completed_items += 1

        except Exception as e:
            logger.warning("Error processing queue item %s: %s", pending_item.id, e)
            pending_item.retries += 1
            pending_item.error = str(e)
            if pending_item.retries <= queue.settings.max_retries_per_item:
                pending_item.status = "pending"  # Eligible for retry
            else:
                pending_item.status = "failed"
                queue.failed_items += 1

        # Check completion condition
        if queue.get_pending_count() == 0 and queue.get_in_progress_count() == 0:
            queue.status = "completed" if queue.failed_items == 0 else "completed_with_errors"

        queue.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_queue(queue)

        # Broadcast progress milestone or job completion
        if self.milestone_listeners:
            notify_interval = queue.settings.notify_interval if queue.settings else 10
            is_milestone = (queue.completed_items > 0 and queue.completed_items % notify_interval == 0)
            is_finished = queue.status in ("completed", "completed_with_errors")
            if is_milestone or is_finished:
                for listener in list(self.milestone_listeners):
                    try:
                        listener(queue, pending_item)
                    except Exception as e:
                        logger.debug("Error in milestone listener: %s", e)

        return {
            "success": (pending_item.status == "completed"),
            "item_id": pending_item.id,
            "status": pending_item.status,
            "result": pending_item.result,
            "error": pending_item.error,
            "retries": pending_item.retries,
            "job_id": queue.job_id,
            "progress_percent": queue.get_progress_percent(),
            "completed_items": queue.completed_items,
            "total_items": queue.total_items,
        }

    def pause(self, job_id_or_path: Optional[str] = None) -> Dict[str, Any]:
        """Pause execution of the job."""
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"success": False, "error": "No active batch job to pause."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"success": False, "error": f"Job '{target}' could not be loaded."}

        # Signal background worker to halt after current item if it matches
        with self._worker_lock:
            if self._active_worker_job_id == queue.job_id:
                self._stop_event.set()

        queue.status = "paused"
        queue.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_queue(queue)
        logger.info("Paused batch job %s", queue.job_id)
        return {"success": True, "status": "paused", "job_id": queue.job_id}

    def resume(
        self,
        job_id_or_path: Optional[str] = None,
        run_in_background: bool = False,
    ) -> Dict[str, Any]:
        """Resume execution of a paused job."""
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"success": False, "error": "No active batch job to resume."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"success": False, "error": f"Job '{target}' could not be loaded."}

        queue.status = "in_progress"
        queue.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_queue(queue)
        logger.info("Resumed batch job %s", queue.job_id)

        if run_in_background:
            return self.run_in_background(job_id_or_path=queue.job_id)

        return {"success": True, "status": "in_progress", "job_id": queue.job_id}

    def generate_summary(self, job_id_or_path: Optional[str] = None) -> Dict[str, Any]:
        """Compile or regenerate ranking leaderboard summary for a batch job."""
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"success": False, "error": "No active batch job found to summarize."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"success": False, "error": f"Job '{target}' could not be loaded."}

        try:
            return self.summary_generator.generate(queue)
        except Exception as e:
            logger.error("Failed compiling ranking summary for %s: %s", queue.job_id, e)
            return {"success": False, "error": str(e)}

    def run_job(
        self,
        job_id_or_path: Optional[str] = None,
        processor: Optional[Callable[[QueueItem, Dict[str, Any]], Dict[str, Any]]] = None,
        pause_between_items_sec: Optional[float] = None,
        max_items: Optional[int] = None,
        kill_switch_checker: Optional[Callable[[], bool]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """Execute queue items sequentially until completion, pause, max_items, or stop signal.

        Args:
            job_id_or_path: Target job ID or queue.json path.
            processor: Optional per-item processor (defaults to self.default_processor).
            pause_between_items_sec: Delay between items in seconds (defaults to queue settings or 2.0s).
            max_items: Optional limit on number of items to process in this run.
            kill_switch_checker: Optional callable returning True if kill switch triggered.
            stop_event: Optional threading.Event signaling stop/cancellation.

        Returns:
            Dict summary of execution metrics and status.
        """
        target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
        if not target:
            return {"success": False, "error": "No active batch job to run."}

        queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
        if not queue:
            return {"success": False, "error": f"Job '{target}' could not be loaded."}

        if queue.get_pending_count() == 0 and queue.get_in_progress_count() == 0:
            return {
                "success": True,
                "done": True,
                "status": queue.status,
                "job_id": queue.job_id,
                "completed_items": queue.completed_items,
                "total_items": queue.total_items,
                "progress_percent": queue.get_progress_percent(),
                "message": "All items in batch job have already been processed.",
            }

        pause_sec = (
            pause_between_items_sec
            if pause_between_items_sec is not None
            else (queue.settings.pause_between_items_sec if queue.settings else 2.0)
        )

        items_processed_in_run = 0
        logger.info(
            "Starting continuous batch run for '%s' (%s) with %d pending items (pacing: %.1fs).",
            queue.job_name,
            queue.job_id,
            queue.get_pending_count(),
            pause_sec,
        )

        while True:
            # 1. Check emergency kill switch
            is_killed = False
            if kill_switch_checker and kill_switch_checker():
                is_killed = True
            elif self.kill_switch and hasattr(self.kill_switch, "is_triggered") and self.kill_switch.is_triggered():
                is_killed = True

            if is_killed:
                logger.warning("run_job halted by emergency kill switch on job %s", queue.job_id)
                self.pause(queue.job_id)
                return {
                    "success": False,
                    "aborted": True,
                    "reason": "kill_switch_triggered",
                    "job_id": queue.job_id,
                    "status": "paused",
                    "completed_items": queue.completed_items,
                    "total_items": queue.total_items,
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": "Execution halted by emergency kill switch.",
                }

            # 2. Check stop signals
            if (stop_event and stop_event.is_set()) or self._stop_event.is_set():
                logger.info("run_job halted by stop event on job %s", queue.job_id)
                self.pause(queue.job_id)
                return {
                    "success": True,
                    "paused": True,
                    "status": "paused",
                    "job_id": queue.job_id,
                    "completed_items": queue.completed_items,
                    "total_items": queue.total_items,
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": "Execution paused by request.",
                }

            # 3. Check if queue was paused externally
            if queue.status == "paused":
                logger.info("run_job observed paused status for job %s", queue.job_id)
                return {
                    "success": True,
                    "paused": True,
                    "status": "paused",
                    "job_id": queue.job_id,
                    "completed_items": queue.completed_items,
                    "total_items": queue.total_items,
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": "Job is currently paused.",
                }

            # 4. Check max_items limit
            if max_items is not None and items_processed_in_run >= max_items:
                return {
                    "success": True,
                    "done": False,
                    "status": queue.status,
                    "job_id": queue.job_id,
                    "completed_items": queue.completed_items,
                    "total_items": queue.total_items,
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": f"Processed {items_processed_in_run} items (hit limit of {max_items}).",
                }

            # 5. Step next item
            step_res = self.step_next(job_id_or_path=queue.job_id, processor=processor)

            if step_res.get("done"):
                summary_res = step_res.get("summary")
                if not summary_res:
                    try:
                        summary_res = self.generate_summary(queue.job_id)
                    except Exception as e:
                        logger.warning("Failed generating summary in run_job: %s", e)

                return {
                    "success": True,
                    "done": True,
                    "status": step_res.get("status", "completed"),
                    "job_id": queue.job_id,
                    "completed_items": step_res.get("completed_items", queue.completed_items),
                    "total_items": step_res.get("total_items", queue.total_items),
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": "All items in batch job have been processed.",
                    "summary": summary_res,
                }

            if not step_res.get("success") and step_res.get("status") == "paused":
                return {
                    "success": True,
                    "paused": True,
                    "status": "paused",
                    "job_id": queue.job_id,
                    "completed_items": queue.completed_items,
                    "total_items": queue.total_items,
                    "progress_percent": queue.get_progress_percent(),
                    "items_processed_in_run": items_processed_in_run,
                    "message": "Job is currently paused.",
                }

            items_processed_in_run += 1

            # 6. Interruptible cool-down sleep
            if pause_sec > 0:
                elapsed = 0.0
                slice_interval = 0.1
                while elapsed < pause_sec:
                    if (stop_event and stop_event.is_set()) or self._stop_event.is_set():
                        break
                    if (kill_switch_checker and kill_switch_checker()) or (
                        self.kill_switch and hasattr(self.kill_switch, "is_triggered") and self.kill_switch.is_triggered()
                    ):
                        break
                    time.sleep(min(slice_interval, pause_sec - elapsed))
                    elapsed += slice_interval

    def run_in_background(
        self,
        job_id_or_path: Optional[str] = None,
        processor: Optional[Callable[[QueueItem, Dict[str, Any]], Dict[str, Any]]] = None,
        pause_between_items_sec: Optional[float] = None,
        max_items: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Launch autonomous continuous batch processing in a dedicated background worker thread."""
        with self._worker_lock:
            if self.is_worker_running():
                return {
                    "success": False,
                    "error": f"A batch job is already running in the background (Job ID: {self._active_worker_job_id}).",
                    "active_job_id": self._active_worker_job_id,
                }

            target = job_id_or_path or (self.active_job.job_id if self.active_job else None)
            if not target:
                return {"success": False, "error": "No active batch job to run."}

            queue = self.load_job(target) if (not self.active_job or self.active_job.job_id != target) else self.active_job
            if not queue:
                return {"success": False, "error": f"Job '{target}' could not be loaded."}

            if queue.status == "paused":
                queue.status = "in_progress"
                queue.updated_at = datetime.now(timezone.utc).isoformat()
                self.save_queue(queue)

            self._stop_event.clear()
            self._active_worker_job_id = queue.job_id

            def _worker_target():
                try:
                    logger.info("Batch background worker started for job %s", queue.job_id)
                    self.run_job(
                        job_id_or_path=queue.job_id,
                        processor=processor,
                        pause_between_items_sec=pause_between_items_sec,
                        max_items=max_items,
                        stop_event=self._stop_event,
                    )
                except Exception as e:
                    logger.error("Error in batch background worker for %s: %s", queue.job_id, e)
                finally:
                    with self._worker_lock:
                        self._active_worker_job_id = None
                    logger.info("Batch background worker finished for job %s", queue.job_id)

            self._worker_thread = threading.Thread(
                target=_worker_target,
                name=f"BatchJobWorker-{queue.job_id}",
                daemon=True,
            )
            self._worker_thread.start()

            return {
                "success": True,
                "status": "running_in_background",
                "job_id": queue.job_id,
                "job_name": queue.job_name,
                "skill_name": queue.skill_name,
                "total_items": queue.total_items,
                "completed_items": queue.completed_items,
                "pending_items": queue.get_pending_count(),
                "progress_percent": queue.get_progress_percent(),
                "message": f"Started background batch worker for job '{queue.job_name}' ({queue.job_id}).",
            }

    def stop_background_worker(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Request the active background worker to stop cleanly."""
        self._stop_event.set()
        active_id = self._active_worker_job_id
        if active_id:
            try:
                self.pause(active_id)
            except Exception as e:
                logger.debug("Failed pausing job during worker stop: %s", e)

        worker = self._worker_thread
        if worker and worker.is_alive():
            worker.join(timeout=timeout)
            still_alive = worker.is_alive()
        else:
            still_alive = False

        with self._worker_lock:
            if not still_alive:
                self._worker_thread = None
                self._active_worker_job_id = None

        return {
            "success": True,
            "stopped": not still_alive,
            "job_id": active_id,
            "message": "Background worker stopped." if not still_alive else "Worker stop requested (finishing active item).",
        }
