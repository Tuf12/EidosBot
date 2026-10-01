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
