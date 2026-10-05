---
name: skills-manager
version: 1.0.0
description: Discover, author, inspect, and update living SKILL.md procedures and domain evaluation rubrics.
author: eidos-core
tags: [skills, rubrics, authoring, procedures]
tools_required:
  - list_skills
  - create_skill
  - update_skill
---

# Skills Authoring & Procedure Management

## 1. Objective
Collaboratively discover, create, and refine living `SKILL.md` procedures, domain rubrics, and frontmatter tool requirements.

## 2. Operating Principles
- **Discovery:** Use `list_skills` to inspect existing procedures, versions, descriptions, and required tools across builtin, global (`~/.eidos/skills`), and project (`.eidos/skills`) scopes.
- **Authoring Living Skills:** Use `create_skill` with YAML frontmatter (`name`, `version`, `description`, `author`, `tools_required: [...]`) and structured markdown sections:
  1. `# [Skill Title]`
  2. `## 1. Objective`
  3. `## 2. Operating Principles / Evaluation Rubric`
  4. `## 3. Step-by-Step Procedure`
  5. `## 4. Output Template / Schema`
- **Dynamic Tool Scoping:** Always declare the minimal set of tools in `tools_required: [...]` needed for that specific workflow so the agent receives only the tools it requires when the skill is loaded.
- **Refinement:** Use `update_skill` to modify or extend existing procedures based on user feedback or learned operational guidelines.
