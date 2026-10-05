"""Domain-agnostic living Skill documents manager (~/.eidos/skills/<name>/SKILL.md)."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple
import yaml

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter delimited by '---' from markdown content.

    Returns:
        (metadata_dict, markdown_body)
    """
    content = content.strip()
    if not content.startswith("---"):
        return {}, content

    # Find the closing frontmatter delimiter
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_raw = parts[1].strip()
    body_raw = parts[2].strip()

    try:
        metadata = yaml.safe_load(frontmatter_raw)
        if isinstance(metadata, dict):
            return metadata, body_raw
        return {}, body_raw
    except Exception as e:
        logger.debug("Failed to parse YAML frontmatter: %s", e)
        return {}, content


class SkillManager:
    """Manages discovery, loading, authoring, and updating of generic SKILL.md procedures."""

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        project_skills_dir: Optional[str] = None,
        builtin_skills_dir: Optional[str] = None,
        include_builtin: bool = True,
    ):
        self.skills_dir = Path(os.path.abspath(os.path.expanduser(skills_dir or "~/.eidos/skills")))
        self.project_skills_dir = (
            Path(os.path.abspath(os.path.expanduser(project_skills_dir)))
            if project_skills_dir
            else Path(os.path.abspath(".eidos/skills"))
        )
        self.include_builtin = include_builtin
        if builtin_skills_dir is not None:
            self.builtin_skills_dir: Optional[Path] = (
                Path(os.path.abspath(os.path.expanduser(builtin_skills_dir)))
                if builtin_skills_dir
                else None
            )
        else:
            self.builtin_skills_dir = (
                Path(__file__).resolve().parent / "builtin" if include_builtin else None
            )
        self._ensure_directories()

    def _ensure_directories(self):
        """Ensure base skills directory exists."""
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug("Could not create skills directory %s: %s", self.skills_dir, e)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize a skill name to be safe for filenames and directories."""
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower())
        clean = re.sub(r"-+", "-", clean).strip("-")
        return clean or "unnamed-skill"

    def list_skills(self) -> List[Dict[str, Any]]:
        """List all discovered skills across builtin, global, and project skills directories."""
        discovered: Dict[str, Dict[str, Any]] = {}

        # 1. Search builtin system skills (lowest precedence)
        if self.builtin_skills_dir and self.builtin_skills_dir.exists():
            for entry in sorted(self.builtin_skills_dir.iterdir()):
                skill_path = None
                if entry.is_dir():
                    candidate = entry / "SKILL.md"
                    if candidate.exists() and candidate.is_file():
                        skill_path = candidate
                elif entry.is_file() and entry.suffix == ".md":
                    skill_path = entry

                if skill_path:
                    meta = self._inspect_skill_file(skill_path, scope="builtin")
                    if meta:
                        discovered[meta["name"]] = meta

        # 2. Search global user skills directory (~/.eidos/skills, overrides builtin if same name)
        if self.skills_dir.exists():
            for entry in sorted(self.skills_dir.iterdir()):
                skill_path = None
                if entry.is_dir():
                    candidate = entry / "SKILL.md"
                    if candidate.exists() and candidate.is_file():
                        skill_path = candidate
                elif entry.is_file() and entry.suffix == ".md":
                    skill_path = entry

                if skill_path:
                    meta = self._inspect_skill_file(skill_path, scope="global")
                    if meta:
                        discovered[meta["name"]] = meta

        # 3. Search project-level skills directory (overrides global and builtin if same name)
        if self.project_skills_dir.exists():
            for entry in sorted(self.project_skills_dir.iterdir()):
                skill_path = None
                if entry.is_dir():
                    candidate = entry / "SKILL.md"
                    if candidate.exists() and candidate.is_file():
                        skill_path = candidate
                elif entry.is_file() and entry.suffix == ".md":
                    skill_path = entry

                if skill_path:
                    meta = self._inspect_skill_file(skill_path, scope="project")
                    if meta:
                        discovered[meta["name"]] = meta

        return sorted(list(discovered.values()), key=lambda s: s["name"])

    def _inspect_skill_file(self, path: Path, scope: str = "global") -> Optional[Dict[str, Any]]:
        """Extract metadata summary from a SKILL.md file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            metadata, body = parse_frontmatter(content)
            inferred_name = metadata.get("name") or path.parent.name
            if inferred_name == "skills":
                inferred_name = path.stem

            description = metadata.get("description")
            if not description and body:
                # Extract first non-header line as fallback description
                for line in body.splitlines():
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#"):
                        description = cleaned[:160]
                        break

            return {
                "name": inferred_name,
                "version": str(metadata.get("version", "1.0.0")),
                "description": description or "No description provided.",
                "author": metadata.get("author", "user"),
                "tags": metadata.get("tags") or [],
                "tools_required": metadata.get("tools_required") or [],
                "path": str(path),
                "scope": scope,
            }
        except Exception as e:
            logger.warning("Error reading skill file %s: %s", path, e)
            return None

    def find_skill_path(self, name: str) -> Optional[Path]:
        """Resolve the path to a skill's SKILL.md by name."""
        clean_name = self._sanitize_name(name)

        # Check candidate exact locations in order of precedence: project -> global -> builtin
        candidates = [
            self.project_skills_dir / clean_name / "SKILL.md",
            self.project_skills_dir / f"{clean_name}.md",
            self.skills_dir / clean_name / "SKILL.md",
            self.skills_dir / f"{clean_name}.md",
        ]
        if self.builtin_skills_dir:
            candidates.extend([
                self.builtin_skills_dir / clean_name / "SKILL.md",
                self.builtin_skills_dir / f"{clean_name}.md",
            ])

        for cand in candidates:
            if cand.exists() and cand.is_file():
                return cand

        # Fallback case-insensitive match on directory names
        search_dirs = [self.project_skills_dir, self.skills_dir]
        if self.builtin_skills_dir:
            search_dirs.append(self.builtin_skills_dir)

        for base in search_dirs:
            if base.exists():
                for sub in base.iterdir():
                    if sub.name.lower() == clean_name:
                        skill_file = sub / "SKILL.md" if sub.is_dir() else sub
                        if skill_file.exists() and skill_file.is_file():
                            return skill_file

        return None

    def load_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Load skill content, metadata, and body by skill name."""
        path = self.find_skill_path(name)
        if not path:
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            metadata, body = parse_frontmatter(content)
            if str(path).startswith(str(self.project_skills_dir)):
                scope = "project"
            elif self.builtin_skills_dir and str(path).startswith(str(self.builtin_skills_dir)):
                scope = "builtin"
            elif str(path).startswith(str(self.skills_dir)):
                scope = "global"
            else:
                scope = "global"

            inferred_name = metadata.get("name") or path.parent.name

            return {
                "name": inferred_name,
                "metadata": metadata,
                "content": content,
                "body": body,
                "tools_required": metadata.get("tools_required") or [],
                "path": str(path),
                "scope": scope,
            }
        except Exception as e:
            logger.warning("Failed to load skill '%s' from %s: %s", name, path, e)
            return None

    def create_skill(
        self,
        name: str,
        content: str,
        scope: str = "global",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Create a new skill folder and SKILL.md file."""
        clean_name = self._sanitize_name(name)
        target_base = self.project_skills_dir if scope == "project" else self.skills_dir
        skill_dir = target_base / clean_name
        skill_file = skill_dir / "SKILL.md"

        if skill_file.exists() and not overwrite:
            return {
                "success": False,
                "error": f"Skill '{clean_name}' already exists at {skill_file}. Set overwrite=True to replace.",
                "path": str(skill_file),
            }

        # Normalize content with frontmatter if absent
        normalized_content = content.strip()
        metadata, _ = parse_frontmatter(normalized_content)
        if not metadata:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            header = (
                f"---\n"
                f"name: {clean_name}\n"
                f"version: 1.0.0\n"
                f"description: Skill procedure for {clean_name}\n"
                f"author: user & eidosbot\n"
                f"created_at: {today}\n"
                f"---\n\n"
            )
            normalized_content = header + normalized_content

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            with open(skill_file, "w", encoding="utf-8") as f:
                f.write(normalized_content + "\n")

            logger.info("Created skill '%s' at %s", clean_name, skill_file)
            return {
                "success": True,
                "name": clean_name,
                "path": str(skill_file),
                "scope": scope,
            }
        except Exception as e:
            logger.error("Failed to write skill file %s: %s", skill_file, e)
            return {
                "success": False,
                "name": clean_name,
                "error": f"Failed to create skill: {str(e)}",
            }

    def update_skill(self, name: str, content: str) -> Dict[str, Any]:
        """Update the content of an existing skill."""
        existing_path = self.find_skill_path(name)
        if not existing_path:
            return {
                "success": False,
                "error": f"Cannot update non-existent skill '{name}'. Use create_skill to add a new skill.",
            }

        try:
            with open(existing_path, "w", encoding="utf-8") as f:
                f.write(content.strip() + "\n")

            logger.info("Updated skill '%s' at %s", name, existing_path)
            return {
                "success": True,
                "name": name,
                "path": str(existing_path),
            }
        except Exception as e:
            logger.error("Failed to update skill %s: %s", existing_path, e)
            return {
                "success": False,
                "name": name,
                "error": f"Failed to update skill: {str(e)}",
            }

    def delete_skill(self, name: str) -> Dict[str, Any]:
        """Delete a skill folder or file."""
        existing_path = self.find_skill_path(name)
        if not existing_path:
            return {"success": False, "error": f"Skill '{name}' not found."}

        if self.builtin_skills_dir and str(existing_path).startswith(str(self.builtin_skills_dir)):
            return {
                "success": False,
                "error": f"Cannot delete built-in system skill '{name}'.",
            }

        try:
            target_dir = existing_path.parent
            if target_dir.name != "skills" and target_dir.is_dir():
                shutil.rmtree(target_dir)
            else:
                existing_path.unlink()

            logger.info("Deleted skill '%s'", name)
            return {"success": True, "name": name}
        except Exception as e:
            return {"success": False, "error": str(e)}
