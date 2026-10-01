# EidosBot Skills & Batch Job Runner Specification
## Domain-Agnostic Procedural Guides & State-Machine Queue Engine

This document defines the architectural design, generic data schemas, lifecycle, and execution engine for **EidosBot Skills and Batch Job Queues**.

---

## ⚠️ ARCHITECTURAL MANDATE: STRICT DOMAIN AGNOSTICISM

> **CRITICAL DESIGN CONSTRAINT FOR AGENTS & IMPLEMENTERS:**  
> The core subsystems (`eidos/skills/` and `eidos/jobs/`), Pydantic models, tool schemas, and queue runners **MUST REMAIN 100% DOMAIN-AGNOSTIC**.  
>  
> - **NEVER** hardcode fields like `score`, `invention`, `patent`, `portfolio`, or domain-specific scoring formulas into the queue schemas, classes, or database tables.
> - **ALL** domain-specific goals, evaluation rubrics, processing steps, and output structures belong exclusively inside user-created `SKILL.md` markdown files and runtime input payloads.
> - The batch job runner is a **universal queue processor** suitable for any repetitive task (e.g., auditing 500 server logs, converting 1,000 PDFs, triaging GitHub issues, refactoring codebases, or evaluating product ideas).

---

## 1. Executive Summary & Motivation

### 1.1. The Cold-Start Challenge
While **Episodic Memory** excels at retrieving past lessons from tasks EidosBot has already completed, it cannot guide the agent through tasks it has **never performed before**. Without structured procedural guidance:
- The agent guesses formatting, depth, and evaluation rubrics.
- Multi-step tasks drift in tone and quality between steps.
- Large multi-item workloads quickly blow past LLM context token limits or degrade after item #5.

### 1.2. The Solution: Living Skills & Durable Job Queues
1. **Generic Human & Bot Editable Skill Documents (`SKILL.md`):** A transparent, shared markdown workspace where the user and EidosBot collaborate on standard operating procedures, evaluation rubrics, quality bars, and output schemas for *any* domain.
2. **Generic State-Machine Batch Job Engine (`eidos/jobs/`):** A resilient item-by-item runner that decouples large projects (1 to 1,000+ items) into discrete, checkpointed steps with persistent state, progress tracking, and pause/resume capabilities.

---

## 2. The Cognitive Triad: Skills vs. Memory vs. Profile

To maintain clean system boundaries and prevent prompt contamination, EidosBot strictly isolates knowledge into three distinct stores:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 EIDOSBOT COGNITIVE TRIAD                                │
├─────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│ 👤 USER PROFILE         │ 📚 EPISODIC MEMORY            │ 🛠️ SKILLS & RUBRICS          │
│ (~/.eidos/user_profile) │ (~/.eidos/memory/vector_store)│ (~/.eidos/skills/<name>/)    │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ "WHO the user is"       │ "HOW it went in the past"     │ "WHAT to do & HOW to do it"  │
│                         │                               │                              │
│ • Personal conventions  │ • Autobiographical experience │ • Standard Operating Proc.   │
│ • Environment paths     │ • Lessons & pitfalls avoided  │ • Evaluation rubric & rules  │
│ • Voice & output style  │ • Vector similarity search    │ • Output schemas & templates │
│ • Static prompt inject  │ • Mid-stream <recall> tags    │ • Progressive load on demand │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

- **User Profile:** *"Alex prefers concise markdown summaries without conversational fluff."*
- **Episodic Memory:** *"In episode #42, web scraping timed out after 30 requests; wait 2s between queries."*
- **Skill / Rubric:** *"Here are the step-by-step instructions, evaluation rubric, and required output template for this specific procedure."*

---

## 3. Generic Skill Architecture (`~/.eidos/skills/`)

### 3.1. Directory Hierarchy
Skills can be installed globally or scoped to a specific project workspace:
```text
~/.eidos/skills/                    # Global user skills
├── document-summarizer/
│   ├── SKILL.md                    # Primary instructions and output format
│   └── templates/                  # Output templates
├── code-auditor/
│   ├── SKILL.md
│   └── scripts/                    # Optional supporting helper scripts
└── any-custom-skill/
    └── SKILL.md

~/MyProject/.eidos/skills/          # Project-specific workspace skills
```

### 3.2. Generic `SKILL.md` Specification
Every `SKILL.md` uses standard YAML frontmatter followed by markdown sections:

```markdown
---
name: generic-procedure-name
version: 1.0.0
description: Clear one-line summary of what this procedure achieves.
author: user & eidosbot
created_at: 2026-09-25
tags: [tag1, tag2]
tools_required: [execute_shell, browser_open] # Optional tools list
---

# [Skill Title]

## 1. Objective
Clear definition of the goal, expected inputs, and final deliverables.

## 2. Evaluation Rubric & Quality Criteria
- Objective rules, scoring scales, or acceptance bars for the output.
- Edge cases and what to avoid.

## 3. Step-by-Step Procedure
1. Step 1: Ingest and validate item data.
2. Step 2: Perform domain-specific research, analysis, or transformation.
3. Step 3: Format and write artifact output.

## 4. Output Template / Schema
Reference to standard output layout or markdown template.
```

### 3.3. Collaborative Co-Authoring Lifecycle
Skills are living documents co-authored between user and bot:
1. **User Prompt:** *"Eidos, I need to process a list of 500 customer bug reports and classify them by severity and root cause. Let's draft a skill."*
2. **Agent Proposal:** EidosBot generates a draft `SKILL.md` outlining the rubric, classification categories, and markdown report format.
3. **User Refinement:** The user edits or prompts adjustments (e.g., *"Add a category for regression bugs"*).
4. **Execution:** EidosBot loads the approved `SKILL.md` into the job runner.

---

## 4. Generic Batch Job State Machine (`eidos/jobs/`)

### 4.1. The Domain-Agnostic State Machine

```
┌────────────────────────────────────────────────────────────────────────┐
│                     GENERIC BATCH JOB STATE MACHINE                    │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   [ Input Items List ] ───▶ [ Initialize Queue: queue.json ]           │
│   (CSV, JSON, or list)             │                                   │
│                                    ▼                                   │
│                       ┌─────────────────────────┐                      │
│                       │   Select Next Pending   │ ◀──────────────┐     │
│                       └────────────┬────────────┘                │     │
│                                    ▼                             │     │
│                       ┌─────────────────────────┐                │     │
│                       │  Fresh Context Agent    │                │     │
│                       │  (Runs Skill Workflow)  │                │     │
│                       └────────────┬────────────┘                │     │
│                                    ▼                             │     │
│                       ┌─────────────────────────┐                │     │
│                       │ Save Result & Artifacts │                │     │
│                       └────────────┬────────────┘                │     │
│                                    ▼                             │     │
│                       ┌─────────────────────────┐                │     │
│                       │ Checkpoint: 'completed' │ ───────────────┘     │
│                       └────────────┬────────────┘   (Loop until done)  │
│                                    ▼                                   │
│                       [ Final Aggregation/Summary ]                    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.2. Generic Queue Schema (`queue.json`)
Notice that every field in `queue.json` is completely generic:

```json
{
  "job_id": "job_20260925_083000_a1b2",
  "job_name": "arbitrary_user_job_name",
  "skill_name": "name-of-assigned-skill",
  "created_at": "2026-09-25T08:30:00Z",
  "status": "in_progress",
  "total_items": 100,
  "completed_items": 15,
  "failed_items": 0,
  "skipped_items": 0,
  "settings": {
    "pause_between_items_sec": 2.0,
    "max_retries_per_item": 2,
    "notify_interval": 10
  },
  "items": [
    {
      "id": "item_001",
      "input": {
        "title": "Raw input string, file path, or arbitrary JSON object",
        "metadata": {}
      },
      "status": "completed",
      "result": {
        "summary": "One line outcome summary",
        "artifacts": ["path/to/generated_file.md"],
        "data": {}
      },
      "error": null,
      "started_at": "2026-09-25T08:30:05Z",
      "completed_at": "2026-09-25T08:30:25Z"
    },
    {
      "id": "item_002",
      "input": {
        "title": "Second item"
      },
      "status": "in_progress",
      "result": null,
      "error": null,
      "started_at": "2026-09-25T08:30:27Z",
      "completed_at": null
    },
    {
      "id": "item_003",
      "input": {
        "title": "Third item"
      },
      "status": "pending",
      "result": null,
      "error": null,
      "started_at": null,
      "completed_at": null
    }
  ]
}
```

### 4.3. Universal Operational Capabilities
- **Ephemeral Per-Item Context:** Each item runs in a fresh, isolated sub-session with the `SKILL.md` injected. Item #500 runs with the exact same pristine context window and reasoning quality as item #1.
- **Durable Checkpointing:** Progress is written to disk after every single item. If the system restarts, it resumes from the next `pending` item.
- **Generic Human-in-the-Loop Hooks:** Any item that produces ambiguous results or fails automated checks is marked `"status": "needs_review"`, triggering a notification via HUD or Telegram without halting the remaining queue.

---

## 5. Universal Tool Suite

EidosBot exposes generic tools for managing skills and batch jobs:

| Tool Name | Parameters | Description |
|---|---|---|
| `list_skills` | None | List all available skills and descriptions from `~/.eidos/skills/`. |
| `load_skill` | `skill_name` | Load a `SKILL.md` into the agent's current working context. |
| `create_skill` | `name`, `content` | Draft or create a new skill directory and `SKILL.md`. |
| `update_skill` | `name`, `diff_content` | Modify sections of an existing skill. |
| `init_batch_job` | `job_name`, `skill_name`, `items_data`, `output_dir` | Ingest arbitrary item list (CSV/JSON), create `queue.json`, and prepare workspace. |
| `step_batch_job` | `job_id` | Execute the next pending item in the queue using the assigned skill. |
| `get_job_status` | `job_id` | Return progress counts, percentage, and active item details. |
| `pause_batch_job` | `job_id` | Safely halt queue execution after the current item finishes. |
| `resume_batch_job` | `job_id` | Resume processing a paused queue. |

---

## 6. Appendix: Example Application Case Studies

To demonstrate how the **same generic engine** powers completely different workflows without changing any code:

### Case Study A: The 300 Inventions Evaluation (User Idea)
- **Skill:** `invention-evaluator/SKILL.md` (defines commercial viability rubric, patent search steps, and portfolio template).
- **Queue Input:** CSV containing 300 invention concepts.
- **Per-Item Action:** Headless Playwright search $\rightarrow$ viability scoring $\rightarrow$ generates `portfolios/<id>.md`.
- **Job Artifact:** `RANKING_SUMMARY.md` sorting items by viability score.

### Case Study B: Legacy Codebase Typing & Refactoring
- **Skill:** `type-annotator/SKILL.md` (defines rules for adding type hints, docstrings, and running `mypy`).
- **Queue Input:** List of 80 untyped Python legacy files.
- **Per-Item Action:** Read file $\rightarrow$ add Pydantic/type hints $\rightarrow$ run `mypy` in sandbox $\rightarrow$ save file.
- **Job Artifact:** `REFACTORING_REPORT.md` listing type coverage improvements.

### Case Study C: Security Log Anomaly Audit
- **Skill:** `log-auditor/SKILL.md` (defines regex patterns for intrusion detection and severity classifications).
- **Queue Input:** List of 200 daily Nginx access logs.
- **Per-Item Action:** Ingest log $\rightarrow$ extract anomalies $\rightarrow$ output `incidents/<date>.json`.
- **Job Artifact:** `SECURITY_AUDIT_SUMMARY.md` highlighting critical IP blocks.
