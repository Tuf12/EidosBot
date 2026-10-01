"""Pydantic data schemas for EidosBot memory, user profile, and trajectories."""

from datetime import datetime, timezone
import os
import platform
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def _default_user_identity() -> Dict[str, Any]:
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or "User"
    sys_name = platform.system() or "Linux"
    return {
        "name": username,
        "os": f"{sys_name} (X11)" if sys_name == "Linux" else sys_name,
    }


INTERACTION_STYLE_CHOICES: tuple[str, ...] = (
    "Concise, action-oriented, direct execution",
    "Detailed explanations with step summaries",
    "Minimal text — tools and results only",
    "Fun and witty EidosBot personality — clever quips and wordplay, still sharp and helpful",
)


def _default_preferences() -> Dict[str, Any]:
    return {
        "interaction_style": INTERACTION_STYLE_CHOICES[0],
        "tool_preference": "Deterministic shell over GUI clicking when possible",
    }


def _default_workflow_rules() -> List[str]:
    return [
        "Prefer read-only shell commands for file queries and inspection when faster than GUI clicks",
    ]


class UserProfile(BaseModel):
    """Declarative atomic facts and conventions regarding the user."""

    onboarding_complete: bool = False
    user_identity: Dict[str, Any] = Field(default_factory=_default_user_identity)
    preferences: Dict[str, Any] = Field(default_factory=_default_preferences)
    environment_facts: Dict[str, Any] = Field(default_factory=dict)
    workflow_rules: List[str] = Field(default_factory=_default_workflow_rules)

    def format_for_prompt(self, max_items_per_category: int = 5) -> str:
        """Render atomic facts into a compact, low-token text representation for system prompt."""
        lines: List[str] = ["USER PROFILE & CONVENTIONS:"]

        # Identity
        name = self.user_identity.get("name")
        os_info = self.user_identity.get("os")
        if name or os_info:
            parts = []
            if name:
                parts.append(f"User: {name}")
            if os_info:
                parts.append(f"OS: {os_info}")
            lines.append(f"- {', '.join(parts)}")

        # Preferences
        for k, v in list(self.preferences.items())[:max_items_per_category]:
            clean_k = k.replace("_", " ").title()
            lines.append(f"- {clean_k}: {v}")

        # Selected Environment Facts
        for k, v in list(self.environment_facts.items())[:max_items_per_category]:
            clean_k = k.replace("_", " ").title()
            if isinstance(v, list):
                v_str = ", ".join(str(item) for item in v)
            else:
                v_str = str(v)
            lines.append(f"- {clean_k}: {v_str}")

        # Workflow Rules
        if self.workflow_rules:
            lines.append("- Workflow Guidelines:")
            for rule in self.workflow_rules[:max_items_per_category]:
                lines.append(f"  • {rule}")

        return "\n".join(lines)


class StepRecord(BaseModel):
    """Detailed record of a single execution step within a task."""

    step: int
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    retrieved_context: List[str] = Field(default_factory=list)
    model_thinking: str = ""
    model_content: str = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    tool_observations: List[Dict[str, Any]] = Field(default_factory=list)
    duration_seconds: float = 0.0


class EpisodeReflection(BaseModel):
    """Post-task synthesis evaluating what went right, what went wrong, and lessons learned."""

    summary: str = ""
    pitfalls_avoided: str = ""
    lessons_learned: str = ""


class EpisodeMetadata(BaseModel):
    """Metadata describing the overall execution and evaluation of an episode."""

    outcome: str = "unknown"  # "success", "failed", "aborted", "max_steps"
    total_steps: int = 0
    user_intervened: bool = False
    critic_score: Optional[float] = None  # 1.0 (success) -> 0.0 (failure)
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0


class EpisodeRecord(BaseModel):
    """Complete multi-step trajectory record suitable for SFT and DPO dataset export."""

    episode_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str = ""
    task_goal: str
    metadata: EpisodeMetadata = Field(default_factory=EpisodeMetadata)
    trajectory: List[StepRecord] = Field(default_factory=list)
    reflection: Optional[EpisodeReflection] = None


class EpisodicSearchResult(BaseModel):
    """Result of a semantic similarity query against episodic memory."""

    episode_id: str
    task_goal: str
    outcome: str = "unknown"
    critic_score: Optional[float] = None
    similarity: float = 0.0
    summary: str = ""
    pitfalls_avoided: str = ""
    lessons_learned: str = ""
    key_actions: List[str] = Field(default_factory=list)
    timestamp: str = ""

