"""Domain-agnostic state machine batch job runner (queue.json)."""

import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from eidos.jobs.types import JobQueue, JobSettings, QueueItem
from eidos.skills.manager import SkillManager

logger = logging.getLogger(__name__)


class BatchJobRunner:
    """Manages persistent batch queues (queue.json) with fresh context per item."""

    def __init__(
        self,
        skills_manager: Optional[SkillManager] = None,
        default_jobs_dir: Optional[str] = None,
    ):
        self.skills = skills_manager or SkillManager()
        self.default_jobs_dir = Path(
            os.path.abspath(os.path.expanduser(default_jobs_dir or "~/EidosWorkspace/jobs"))
        )
        self.active_job: Optional[JobQueue] = None
        self.milestone_listeners: List[Callable[[JobQueue, QueueItem], None]] = []

    def add_milestone_listener(self, listener: Callable[[JobQueue, QueueItem], None]) -> None:
        """Register a callback invoked when an item milestone or job completion occurs."""
        self.milestone_listeners.append(listener)

    def _slugify(self, text: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", text.strip().lower())
        return re.sub(r"_+", "_", clean).strip("_") or "job"

    def _resolve_queue_file(self, target: str) -> Optional[Path]:
        """Resolve a path or job ID to a queue.json file Path."""
        p = Path(os.path.abspath(os.path.expanduser(target)))
        if p.is_file() and p.name == "queue.json":
            return p
        if p.is_dir() and (p / "queue.json").is_file():
            return p / "queue.json"

        # Search inside default_jobs_dir
        if self.default_jobs_dir.exists():
            cand_dir = self.default_jobs_dir / target
            if (cand_dir / "queue.json").is_file():
                return cand_dir / "queue.json"
            # Try matching by prefix or full job_id
            for sub in self.default_jobs_dir.iterdir():
                if sub.is_dir() and target in sub.name and (sub / "queue.json").is_file():
                    return sub / "queue.json"

        return None

    def save_queue(self, queue: JobQueue, queue_path: Optional[Union[str, Path]] = None) -> None:
        """Atomically persist queue state to queue.json."""
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

    def load_job(self, job_id_or_path: str) -> Optional[JobQueue]:
        """Load an existing queue from queue.json path or job ID."""
        q_path = self._resolve_queue_file(job_id_or_path)
        if not q_path or not q_path.is_file():
            return None

        try:
            with open(q_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            queue = JobQueue.model_validate(data)
            self.active_job = queue
            return queue
        except Exception as e:
            logger.error("Failed to parse queue from %s: %s", q_path, e)
            return None

    def initialize_job(
        self,
        job_name: str,
        skill_name: str,
        input_data: Union[List[Any], str, Dict[str, Any]],
        output_dir: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> JobQueue:
        """Initialize a new batch job queue with generic input items."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:4]
        job_id = f"job_{ts}_{short_id}"

        # Resolve output directory
        if output_dir:
            out_path = Path(os.path.abspath(os.path.expanduser(output_dir)))
        else:
            slug = self._slugify(job_name)
            out_path = self.default_jobs_dir / f"{job_id}_{slug}"

        out_path.mkdir(parents=True, exist_ok=True)

        # Parse and normalize input items
        raw_items: List[Any] = []
        if isinstance(input_data, str):
            clean_str = input_data.strip()
            # File path check (.json or .csv)
            potential_file = Path(os.path.abspath(os.path.expanduser(clean_str)))
            if potential_file.is_file():
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
            if queue.get_pending_count() == 0 and queue.get_in_progress_count() == 0:
                queue.status = "completed" if queue.failed_items == 0 else "completed_with_errors"
                queue.updated_at = datetime.now(timezone.utc).isoformat()
                self.save_queue(queue)
            return {
                "success": True,
                "done": True,
                "status": queue.status,
                "message": "All items in queue have been processed.",
                "progress_percent": queue.get_progress_percent(),
                "completed_items": queue.completed_items,
                "total_items": queue.total_items,
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
            if processor:
                result_payload = processor(pending_item, context)
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

        queue.status = "paused"
        queue.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_queue(queue)
        logger.info("Paused batch job %s", queue.job_id)
        return {"success": True, "status": "paused", "job_id": queue.job_id}

    def resume(self, job_id_or_path: Optional[str] = None) -> Dict[str, Any]:
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
        return {"success": True, "status": "in_progress", "job_id": queue.job_id}
