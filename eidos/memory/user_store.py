"""Manager for persistent, atomic user profile facts (~/.eidos/user_profile.yaml)."""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from eidos.memory.types import UserProfile

logger = logging.getLogger(__name__)

DEFAULT_USER_PROFILE_PATH = Path.home() / ".eidos" / "user_profile.yaml"


class UserStore:
    """Provides atomic CRUD and prompt formatting for the user profile."""

    def __init__(self, storage_path: Optional[str | Path] = None):
        self.path = Path(os.path.expanduser(str(storage_path or DEFAULT_USER_PROFILE_PATH)))
        self._cached_profile: Optional[UserProfile] = None
        self._last_mtime: float = 0.0
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Ensure storage directory and profile file exist with default template."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                logger.info("Initializing default user profile at %s", self.path)
                default_profile = UserProfile()
                # Personalize username if detectable from environment
                user_env = os.environ.get("USER") or os.environ.get("LOGNAME")
                if user_env:
                    default_profile.user_identity["name"] = user_env
                self.save(default_profile)
        except Exception as e:
            logger.error("Failed to initialize user profile storage: %s", e)

    def load(self, force_reload: bool = False) -> UserProfile:
        """Load and parse user profile YAML. Caches based on file modification time."""
        if not self.path.exists():
            self._ensure_file()

        try:
            current_mtime = self.path.stat().st_mtime
            if not force_reload and self._cached_profile is not None and current_mtime == self._last_mtime:
                return self._cached_profile

            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            profile = UserProfile(**data)
            self._cached_profile = profile
            self._last_mtime = current_mtime
            return profile
        except Exception as e:
            logger.warning("Error reading user profile from %s: %s. Using default profile.", self.path, e)
            if self._cached_profile is not None:
                return self._cached_profile
            return UserProfile()

    def save(self, profile: UserProfile) -> None:
        """Serialize UserProfile instance to YAML atomically."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = profile.model_dump()

            temp_path = self.path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            temp_path.replace(self.path)

            self._cached_profile = profile
            self._last_mtime = self.path.stat().st_mtime
            logger.debug("Successfully saved user profile to %s", self.path)
        except Exception as e:
            logger.error("Failed to save user profile to %s: %s", self.path, e)
            raise

    def needs_onboarding(self) -> bool:
        """True when first-run / profile wizard has not been completed or dismissed."""
        return not self.load().onboarding_complete

    def mark_onboarding_complete(self) -> None:
        """Mark profile onboarding as finished (wizard saved or user skipped)."""
        profile = self.load(force_reload=True)
        profile.onboarding_complete = True
        self.save(profile)

    def get_onboarding_instruction(self) -> str:
        """Short system-prompt addendum while onboarding is incomplete."""
        if not self.needs_onboarding():
            return ""
        return (
            "PROFILE ONBOARDING (incomplete): The user's local profile at ~/.eidos/user_profile.yaml "
            "is not fully set up. If they state a stable preference, path, or workflow rule, offer to "
            "save it using update_user_profile or add_workflow_rule after they agree. "
            "Do not invent or scan filesystem paths without explicit user consent."
        )

    def get_prompt_context(self) -> str:
        """Return formatted string suitable for injection into system prompt."""
        profile = self.load()
        parts = [profile.format_for_prompt()]
        onboarding = self.get_onboarding_instruction()
        if onboarding:
            parts.append(onboarding)
        return "\n\n".join(parts)

    def apply_onboarding(
        self,
        display_name: Optional[str] = None,
        projects_dir: Optional[str] = None,
        primary_editor: Optional[str] = None,
        interaction_style: Optional[str] = None,
        tool_preference: Optional[str] = None,
        mark_complete: bool = True,
    ) -> UserProfile:
        """Apply first-run wizard fields and optionally mark onboarding complete."""
        profile = self.load(force_reload=True)
        if display_name:
            profile.user_identity["name"] = display_name.strip()
        if projects_dir:
            profile.environment_facts["projects_dir"] = os.path.expanduser(projects_dir.strip())
        if primary_editor:
            profile.environment_facts["primary_editor"] = primary_editor.strip()
        if interaction_style:
            profile.preferences["interaction_style"] = interaction_style.strip()
        if tool_preference:
            profile.preferences["tool_preference"] = tool_preference.strip()
        if mark_complete:
            profile.onboarding_complete = True
        self.save(profile)
        return profile

    def set_fact(self, category: str, key: str, value: Any) -> None:
        """Set or update an individual fact in a profile category."""
        profile = self.load(force_reload=True)
        cat_map = {
            "identity": profile.user_identity,
            "user_identity": profile.user_identity,
            "preferences": profile.preferences,
            "environment": profile.environment_facts,
            "environment_facts": profile.environment_facts,
        }

        target = cat_map.get(category.lower())
        if target is None:
            raise ValueError(f"Unknown category '{category}'. Valid: {list(cat_map.keys())}")

        target[key] = value
        self.save(profile)

    def remove_fact(self, category: str, key: str) -> bool:
        """Remove a fact by key within a category."""
        profile = self.load(force_reload=True)
        cat_map = {
            "identity": profile.user_identity,
            "user_identity": profile.user_identity,
            "preferences": profile.preferences,
            "environment": profile.environment_facts,
            "environment_facts": profile.environment_facts,
        }

        target = cat_map.get(category.lower())
        if target is None or key not in target:
            return False

        del target[key]
        self.save(profile)
        return True

    def add_rule(self, rule: str) -> None:
        """Append a workflow guideline rule."""
        rule_clean = rule.strip()
        if not rule_clean:
            return
        profile = self.load(force_reload=True)
        if rule_clean not in profile.workflow_rules:
            profile.workflow_rules.append(rule_clean)
            self.save(profile)

    def remove_rule(self, rule_or_index: str | int) -> bool:
        """Remove a workflow guideline by exact string or 0-indexed position."""
        profile = self.load(force_reload=True)
        if isinstance(rule_or_index, int):
            if 0 <= rule_or_index < len(profile.workflow_rules):
                profile.workflow_rules.pop(rule_or_index)
                self.save(profile)
                return True
            return False

        if rule_or_index in profile.workflow_rules:
            profile.workflow_rules.remove(rule_or_index)
            self.save(profile)
            return True
        return False
