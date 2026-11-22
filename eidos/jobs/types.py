"""Generic domain-agnostic schemas for batch job queue state machine and durable project briefs."""

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import uuid
from pydantic import BaseModel, Field, model_validator


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
                "artifacts_dir": str(out_p / "artifacts"),
                "portfolios_dir": str(out_p / "artifacts"),
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


# ==============================================================================
# Phase 13.1: Durable Project Brief Schema (PROJECT_JOBS.md)
# ==============================================================================


class JobIdentity(BaseModel):
    """Identity block of a durable project brief."""

    summary: Optional[str] = None
    goal: Optional[str] = None
    phase: Optional[str] = "concept"


class DistinctFrom(BaseModel):
    """Anti-collapse anchor distinguishing this project from existing things."""

    name: str
    difference: str = ""

    @model_validator(mode="before")
    @classmethod
    def parse_distinct_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            for sep in (":", "—", "-"):
                if sep in data:
                    name, diff = data.split(sep, 1)
                    return {"name": name.strip(), "difference": diff.strip()}
            return {"name": data.strip(), "difference": ""}
        return data


class JobBoundary(BaseModel):
    """Boundary definition: what this project is, what it is not, and distinct alternatives."""

    this_is: Optional[str] = None
    this_is_not: Optional[str] = None
    distinct_from: List[DistinctFrom] = Field(default_factory=list)


class JobDecision(BaseModel):
    """Durable short decision entry for the brief."""

    id: str = Field(default_factory=lambda: f"d_{uuid.uuid4().hex[:4]}")
    text: str
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="before")
    @classmethod
    def parse_decision_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"id": f"d_{uuid.uuid4().hex[:4]}", "text": data.strip()}
        return data


class JobSkillRef(BaseModel):
    """Skill attached to a project brief."""

    name: str
    use_when: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def parse_skill_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data.strip()}
        return data


class JobIntegrationRef(BaseModel):
    """Outside integration attached to a project brief."""

    name: str
    use_when: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def parse_integration_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data.strip()}
        return data


class JobDocument(BaseModel):
    """Depth document referenced by the brief and indexed in notes/INDEX.md."""

    path: str
    role: Optional[str] = None
    summary: Optional[str] = None
    read_when: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def parse_document_item(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"path": data.strip()}
        return data


class JobProgress(BaseModel):
    """Execution progress counters written by the batch runner when a queue is attached."""

    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    pending_items: int = 0
    percent: float = 0.0


class ProjectDirectories(BaseModel):
    """Filesystem directory and manifest paths for the project workspace."""

    job_dir: str
    notes_dir: Optional[str] = None
    artifacts_dir: Optional[str] = None
    queue_file: Optional[str] = None
    manifest_file: Optional[str] = None
    project_root: Optional[str] = None
    output_dir: Optional[str] = None


class ProjectBrief(BaseModel):
    """Durable project brief schema for job.yaml (PROJECT_JOBS.md).

    Acts as the source of truth for what a project is and is not, keeping
    long-term memory, working notes, decisions, and attached components scoped.
    """

    job_id: str
    name: str
    title: Optional[str] = None
    status: str = "active"
    focus: str = "primary"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    identity: JobIdentity = Field(default_factory=JobIdentity)
    boundary: JobBoundary = Field(default_factory=JobBoundary)
    decisions: List[JobDecision] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    skills: List[JobSkillRef] = Field(default_factory=list)
    integrations: List[JobIntegrationRef] = Field(default_factory=list)
    components: Dict[str, Any] = Field(default_factory=dict)
    documents: List[JobDocument] = Field(default_factory=list)
    progress: Optional[JobProgress] = None
    directories: ProjectDirectories

    def to_dict(self) -> Dict[str, Any]:
        """Convert ProjectBrief to clean manifest dict matching PROJECT_JOBS.md."""
        res: Dict[str, Any] = {
            "job_id": self.job_id,
            "name": self.name,
            "job_name": self.name,  # legacy backward compatibility
            "status": self.status,
            "focus": self.focus,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "identity": self.identity.model_dump(exclude_none=True),
            "boundary": {
                "this_is": self.boundary.this_is,
                "this_is_not": self.boundary.this_is_not,
                "distinct_from": [df.model_dump() for df in self.boundary.distinct_from],
            },
            "decisions": [d.model_dump() for d in self.decisions],
            "open_questions": list(self.open_questions),
            "constraints": list(self.constraints),
            "skills": [s.model_dump(exclude_none=True) for s in self.skills],
            "integrations": [i.model_dump(exclude_none=True) for i in self.integrations],
            "components": dict(self.components),
            "documents": [doc.model_dump(exclude_none=True) for doc in self.documents],
            "directories": self.directories.model_dump(exclude_none=True),
        }
        if self.title:
            res["title"] = self.title
        # Backward compatibility for skill/skill_name
        if self.skills:
            res["skill_name"] = self.skills[0].name
            res["skill"] = {"name": self.skills[0].name}
        if self.progress is not None:
            res["progress"] = self.progress.model_dump()
        return res

    def to_yaml(self) -> str:
        """Serialize ProjectBrief to clean YAML string."""
        import yaml
        return yaml.dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectBrief":
        """Instantiate ProjectBrief from dict, handling legacy manifests gracefully."""
        d = dict(data)
        job_id = str(d.get("job_id") or f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
        name = str(d.get("name") or d.get("job_name") or d.get("title") or "Unnamed Job")

        # Directories
        raw_dirs = d.get("directories") or {}
        job_dir = raw_dirs.get("job_dir") or raw_dirs.get("output_dir") or d.get("output_dir") or "."
        notes_dir = raw_dirs.get("notes_dir") or str(Path(job_dir) / "notes")
        artifacts_dir = raw_dirs.get("artifacts_dir") or raw_dirs.get("portfolios_dir") or str(Path(job_dir) / "artifacts")
        queue_file = raw_dirs.get("queue_file")
        manifest_file = raw_dirs.get("manifest_file") or str(Path(job_dir) / "job.yaml")
        dirs_model = ProjectDirectories(
            job_dir=str(job_dir),
            notes_dir=str(notes_dir),
            artifacts_dir=str(artifacts_dir),
            queue_file=str(queue_file) if queue_file else None,
            manifest_file=str(manifest_file),
            project_root=raw_dirs.get("project_root"),
            output_dir=raw_dirs.get("output_dir"),
        )

        # Skills
        skills: List[JobSkillRef] = []
        if d.get("skills") and isinstance(d["skills"], list):
            for s in d["skills"]:
                if isinstance(s, dict) and "name" in s:
                    skills.append(JobSkillRef(**s))
                elif isinstance(s, str):
                    skills.append(JobSkillRef(name=s))
        elif d.get("skill_name"):
            skills.append(JobSkillRef(name=str(d["skill_name"])))
        elif d.get("skill") and isinstance(d["skill"], dict) and d["skill"].get("name"):
            skills.append(JobSkillRef(name=str(d["skill"]["name"])))

        # Identity
        raw_id = d.get("identity")
        identity = JobIdentity(**raw_id) if isinstance(raw_id, dict) else JobIdentity(
            summary=d.get("description") or d.get("summary"),
            phase=d.get("phase") or "concept",
        )

        # Boundary
        raw_b = d.get("boundary")
        boundary = JobBoundary(**raw_b) if isinstance(raw_b, dict) else JobBoundary()

        # Decisions (support list of dicts, strings, or legacy key_decisions)
        raw_dec = d.get("decisions") or d.get("key_decisions") or []
        decisions: List[JobDecision] = []
        if isinstance(raw_dec, list):
            for item in raw_dec:
                if isinstance(item, dict):
                    decisions.append(JobDecision(**item))
                elif isinstance(item, str):
                    decisions.append(JobDecision(text=item))

        # Documents
        raw_docs = d.get("documents") or []
        docs: List[JobDocument] = []
        if isinstance(raw_docs, list):
            for item in raw_docs:
                if isinstance(item, dict):
                    docs.append(JobDocument(**item))
                elif isinstance(item, str):
                    docs.append(JobDocument(path=item))

        # Progress
        raw_prog = d.get("progress")
        progress = JobProgress(**raw_prog) if isinstance(raw_prog, dict) else None

        return cls(
            job_id=job_id,
            name=name,
            title=d.get("title"),
            status=d.get("status", "active"),
            focus=d.get("focus", "primary"),
            created_at=str(d.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(d.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            identity=identity,
            boundary=boundary,
            decisions=decisions,
            open_questions=d.get("open_questions") or [],
            constraints=d.get("constraints") or [],
            skills=skills,
            integrations=[JobIntegrationRef(**i) if isinstance(i, dict) else JobIntegrationRef(name=str(i)) for i in (d.get("integrations") or [])],
            components=d.get("components") or {},
            documents=docs,
            progress=progress,
            directories=dirs_model,
        )

    @classmethod
    def from_queue(cls, queue: JobQueue, skill_meta: Optional[Dict[str, Any]] = None) -> "ProjectBrief":
        """Factory to initialize a durable brief from an initialized JobQueue."""
        out_p = Path(queue.output_dir)
        skills = [JobSkillRef(name=queue.skill_name)] if queue.skill_name else []
        return cls(
            job_id=queue.job_id,
            name=queue.job_name,
            status=queue.status,
            focus="primary",
            created_at=queue.created_at,
            updated_at=queue.updated_at,
            identity=JobIdentity(
                summary=queue.description or f"Batch processing job: {queue.job_name}",
                phase=queue.phase or "concept",
            ),
            skills=skills,
            components={"batch_queue": "queue.json"},
            progress=JobProgress(
                total_items=queue.total_items,
                completed_items=queue.completed_items,
                failed_items=queue.failed_items,
                skipped_items=queue.skipped_items,
                pending_items=queue.get_pending_count(),
                percent=queue.get_progress_percent(),
            ),
            directories=ProjectDirectories(
                job_dir=str(out_p),
                notes_dir=str(out_p / "notes"),
                artifacts_dir=str(out_p / "artifacts"),
                queue_file=str(out_p / "queue.json"),
                manifest_file=str(out_p / "job.yaml"),
                project_root=str(out_p.parent) if queue.phase else str(out_p),
                output_dir=str(out_p),
            ),
        )


def generate_notes_index(documents: List[JobDocument], job_name: str = "") -> str:
    """Generate markdown table content for notes/INDEX.md."""
    lines = [
        "# Notes Index",
        "",
        f"> Working notes and project documents for {job_name.strip() if job_name else 'this project'}.",
        "",
        "| Document | Role | Summary | Read When |",
        "| :--- | :--- | :--- | :--- |",
    ]
    if not documents:
        lines.append("*(No documents registered yet. Create `.md` files in this `notes/` directory and list them in `job.yaml` `documents`.)*")
    else:
        for doc in documents:
            p = doc.path
            display_name = p.split("/")[-1] if "/" in p else p
            role = doc.role or "note"
            summary = (doc.summary or "").replace("|", "-")
            read_when = (doc.read_when or "").replace("|", "-")
            lines.append(f"| [{p}]({display_name}) | {role} | {summary} | {read_when} |")
    lines.append("")
    return "\n".join(lines)


def sync_notes_index(
    job_dir: Union[str, Path],
    documents: Optional[List[JobDocument]] = None,
    job_name: str = "",
) -> Path:
    """Synchronize notes/INDEX.md table of contents with registered documents."""
    j_path = Path(os.path.abspath(os.path.expanduser(job_dir)))
    notes_dir = j_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    index_file = notes_dir / "INDEX.md"
    content = generate_notes_index(documents or [], job_name=job_name)
    with open(index_file, "w", encoding="utf-8") as f:
        f.write(content)
    return index_file
