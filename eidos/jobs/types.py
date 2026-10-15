"""Generic domain-agnostic schemas for batch job queue state machine."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QueueItem(BaseModel):
    """Generic atomic item in a batch job queue.

    COMPLETELY DOMAIN-AGNOSTIC:
    All domain-specific fields live inside `input` and `result`.
    """

    id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"  # "pending", "in_progress", "completed", "failed", "skipped", "needs_review"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retries: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobSettings(BaseModel):
    """Runtime execution settings for a batch job."""

    pause_between_items_sec: float = 0.0
    max_retries_per_item: int = 2
    notify_interval: int = 10


class JobQueue(BaseModel):
    """Durable checkpointed state machine queue for batch jobs (queue.json)."""

    job_id: str
    job_name: str
    skill_name: str
    description: Optional[str] = None
    project: Optional[str] = None
    phase: Optional[str] = None
    input_file: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "initialized"  # "initialized", "in_progress", "paused", "completed", "failed"
    output_dir: str
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    settings: JobSettings = Field(default_factory=JobSettings)
    items: List[QueueItem] = Field(default_factory=list)

    def get_pending_count(self) -> int:
        return sum(1 for item in self.items if item.status == "pending")

    @property
    def pending_items(self) -> int:
        return self.get_pending_count()

    def get_in_progress_count(self) -> int:
        return sum(1 for item in self.items if item.status == "in_progress")

    def get_progress_percent(self) -> float:
        if self.total_items == 0:
            return 100.0 if self.status == "completed" else 0.0
        return round((self.completed_items / self.total_items) * 100.0, 1)

    def find_item(self, item_id: str) -> Optional[QueueItem]:
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def to_manifest_dict(self, skill_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate lean human-readable job manifest for job.yaml."""
        from pathlib import Path
        out_p = Path(self.output_dir)

        project = self.project
        phase = self.phase
        if not project and "/" in self.job_name:
            parts = [p.strip() for p in self.job_name.split("/") if p.strip()]
            if len(parts) >= 2:
                project = parts[0]
                phase = "/".join(parts[1:])
            elif len(parts) == 1:
                project = parts[0]

        skill_block: Dict[str, Any] = {"name": self.skill_name}
        if skill_meta:
            if skill_meta.get("description"):
                skill_block["description"] = skill_meta["description"]
            if skill_meta.get("tools_required"):
                skill_block["tools_required"] = skill_meta["tools_required"]

        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "name": self.job_name,
            "project": project or self.job_name,
            "phase": phase,
            "description": self.description or f"Batch processing job: {self.job_name}",
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "skill_name": self.skill_name,
            "skill": skill_block,
            "directories": {
                "project_root": str(out_p.parent) if phase else str(out_p),
                "job_dir": str(out_p),
                "output_dir": str(out_p),
                "artifacts_dir": str(out_p / "portfolios"),
                "portfolios_dir": str(out_p / "portfolios"),
                "input_file": self.input_file,
                "queue_file": str(out_p / "queue.json"),
                "manifest_file": str(out_p / "job.yaml"),
                "summary_file": str(out_p / "RANKING_SUMMARY.md"),
            },
            "progress": {
                "total_items": self.total_items,
                "completed_items": self.completed_items,
                "failed_items": self.failed_items,
                "skipped_items": self.skipped_items,
                "pending_items": self.get_pending_count(),
                "percent": self.get_progress_percent(),
            },
            "settings": self.settings.model_dump(),
        }

    def to_manifest_yaml(self, skill_meta: Optional[Dict[str, Any]] = None) -> str:
        """Serialize manifest dict to clean YAML string."""
        import yaml
        return yaml.dump(self.to_manifest_dict(skill_meta=skill_meta), sort_keys=False, allow_unicode=True)
