# EidosBot Skills & Batch Job Runner Specification
## Dynamic Capability Scoping, Procedural Guides & State-Machine Queue Engine

This document defines the architectural design, dynamic capability scoping, generic data schemas, lifecycle, and execution engine for **EidosBot Skills, Tool Scoping, and Batch Job Queues**.

**Old system.** In this document, a job is the batch queue (`queue.json`), and every specialized tool, including OptimalX, is a skill packaged inside EidosBot.

**Updated job concept.** A job is the project specified in [PROJECT_JOBS.md](PROJECT_JOBS.md): the brief, the `notes/` and `artifacts/` files (images, text, PDFs, spreadsheets, generated reports), and the focus that survives the turn. The batch queue is a component that project can attach. An outside app is an integration attached to that same job. The skill still says how to do the procedure. Tools for a new outside app come from that app's MCP server, and only while the integration is attached. OptimalX stays a built-in skill because OptimalX does not ship an MCP server.

---

## ⚠️ ARCHITECTURAL MANDATES

> **1. STRICT DOMAIN AGNOSTICISM:**  
> The core subsystems (`eidos/skills/` and `eidos/jobs/`), Pydantic models, tool schemas, and queue runners **MUST REMAIN 100% DOMAIN-AGNOSTIC**.  
> - **NEVER** hardcode domain-specific fields like `score`, `invention`, `patent`, `portfolio`, or domain-specific scoring formulas into core queue schemas or database tables.
> - **ALL** domain-specific goals, evaluation rubrics, processing steps, and output structures belong exclusively inside user-created `SKILL.md` markdown files and runtime input payloads.
> - The batch job runner is a **universal queue processor** suitable for any repetitive task (e.g., auditing 500 server logs, converting 1,000 PDFs, triaging GitHub issues, refactoring codebases, or evaluating product ideas).
>
> **2. DYNAMIC TOOL SCOPING (CAPABILITY GATING):**  
> EidosBot operates on a lean base kernel. Specialized tools and procedures **MUST NEVER** be dumped globally into every prompt step.  
> - **"Never give the agent a tool until it enters the room where that tool is needed."**
> - The base agent loop loads only **5 minimal kernel tools**.
> - All specialized capabilities (GUI navigation, browser automation, OptimalX notes, batch runners) are packaged into **Skills**.
> - Tools are unlocked dynamically on-demand when a skill is activated via `load_skill`, and dropped when closed with `close_skill` or task completion.

**Old system (mandate 2, last two bullets).** Every specialized capability is a skill whose tool schemas live in `ToolRegistry`, unlocked by `load_skill`.

**Updated job concept.** In-repo capabilities stay skills: desktop GUI, browser, web research, batch runner, user memory, skill authoring. An outside app's tools are not added to `SPECIALIZED_TOOLS`. They are discovered from that app's MCP server when the project job attaches the integration, or when that integration is the focus and no project job is open. The integration's `SKILL.md` is still the procedure. See [PROJECT_JOBS.md](PROJECT_JOBS.md) section 13.

---

## 1. Executive Summary & Problem Analysis

### 1.1. The Cold-Start Challenge
While **Episodic Memory** excels at retrieving past lessons from tasks EidosBot has already completed, it cannot guide the agent through tasks it has **never performed before**. Without structured procedural guidance:
- The agent guesses formatting, depth, and evaluation rubrics.
- Multi-step tasks drift in tone and quality between steps.
- Large multi-item workloads quickly blow past LLM context token limits or degrade after item #5.

### 1.2. The Root Cause of Agent Confusion & Context Bloat
In the monolithic implementation (`eidos/core/context.py` and `eidos/core/loop.py`), the agent suffered from:
1. **~1,050 lines of System Prompt** containing 16 detailed, conflicting behavioral principles (desktop GUI navigation, headless Playwright browser rules, OptimalX database synchronization rules, batch queue management, CSV/JSON extraction instructions, file sharing directives, voice conventions, etc.).
2. **49 Function Tool Schemas** loaded globally into `EIDOS_TOOLS` and transmitted to the LLM on **every single hop** of the agent loop (~3,500 to 5,000 tokens of strict JSON definitions per step).
3. **Severe Attention Dilution & OptimalX Bias:** Principle 15 explicitly instructed the agent to search OptimalX first for notes, ideas, or projects. Combined with 9 dedicated OptimalX tools in the global catalog, the model repeatedly queried OptimalX even when asked about local files or standard codebases.
4. **Hop Budget Starvation:** With a cap of `max_steps = 15`, wandering across 49 tools and granular GUI clicks quickly exhausted the step budget before accomplishing the goal.

### 1.3. The Solution: Dynamic Skills & Durable Job Queues
1. **Living Skill Documents (`SKILL.md`) as Capability Gates:** Standard operating procedures, evaluation rubrics, and frontmatter tool declarations (`tools_required: [...]`) that dynamically scope the LLM's active tools on demand.
2. **Lean Base Kernel:** A tight ~120-line system prompt and 7 base tools (including always-on `share_file` and `list_skills`) that protect attention heads, eliminate token bloat by ~90%, and save the 15-hop budget.
3. **Generic State-Machine Batch Job Engine (`eidos/jobs/`):** A resilient item-by-item runner that decouples large projects (1 to 1,000+ items) into discrete, checkpointed steps with persistent state, progress tracking, and pause/resume capabilities.

**Old system (item 3).** The batch engine is what "job" means.

**Updated job concept.** Item 3 is the batch-queue component. Project identity, the job folder, focus, and integrations are specified in [PROJECT_JOBS.md](PROJECT_JOBS.md).

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
│                         │                               │ • Dynamic tools required     │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

- **User Profile:** *"Alex prefers concise markdown summaries without conversational fluff."*
- **Episodic Memory:** *"In episode #42, web scraping timed out after 30 requests; wait 2s between queries."*
- **Skill / Rubric:** *"Here are the step-by-step instructions, evaluation rubric, and required tools for this specific procedure."*

**Old system.** The triad above is the whole cognitive split: profile, episodic memory, and skills.

**Updated job concept.** [PROJECT_JOBS.md](PROJECT_JOBS.md) section 2 adds the project brief (`job.yaml`) and the integration record (`~/.eidos/integrations/<name>/`). The brief says what this project is. The integration says how to use one outside app and which MCP server supplies its tools. Skills remain the procedures.

---

## 3. Dynamic Tool Scoping & The Lean Base Kernel

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 EIDOSBOT BASE KERNEL                                   │
│  • System Prompt: ~120 lines (System info, clock, workspace, 5 core principles)        │
│  • Core Tools: [execute_shell, load_skill, close_skill, get_current_time, finish_task] │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            │ Agent calls load_skill("<skill_name>")
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              DYNAMIC ACTIVE SKILL SCOPE                                │
│                                                                                        │
│   ┌───────────────────────────┐   ┌───────────────────────────┐   ┌────────────────┐   │
│   │   desktop-gui (Skill)     │   │     optimalx (Skill)      │   │  browser (Skl) │   │
│   │ • take_screenshot         │   │ • optimalx_search_notes   │   │ • browser_open │   │
│   │ • click_element           │   │ • optimalx_read_note      │   │ • browser_click│   │
│   │ • click / type_text       │   │ • optimalx_write_note     │   │ • browser_ext..│   │
│   │ • hotkey / press_key      │   │ • optimalx_list_folders   │   │ • browser_close│   │
│   └───────────────────────────┘   └───────────────────────────┘   └────────────────┘   │
│                                                                                        │
│   • SKILL.md body injected into prompt ONLY while skill is active                      │
│   • Specialized tools registered into active tool payload ONLY while skill is active   │
│   • On task completion or close_skill(): Specialized tools & prompt instructions drop  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1. The Lean Base System Prompt (~120 lines)
The base prompt contains no domain-specific rules, no Playwright recipes, and no OptimalX directives. It contains only:
1. **Identity & Environment Snapshot:**
   - Operating system (Linux Mint Cinnamon / X11).
   - Dynamic internal clock snapshot (Date, Time, Timezone).
   - Canonical workspace directory (`~/EidosWorkspace`) and Codebase path.
   - Emergency kill switch hotkey notification (`Ctrl + Alt + Esc`).
2. **Episodic & User Profile Memory Blocks:**
   - Injected `<episodic_context>` (macro lessons & pitfalls from past runs).
   - Injected user profile preferences (e.g. communication style, coding conventions).
3. **Core Behavioral Principles (Only 5):**
   - **Principle 1 (Deterministic Tier First):** Prefer direct, deterministic shell commands and scripts via `execute_shell`. Never guess mouse clicks or UI paths when bash/python can execute reliably in 1 step.
   - **Principle 2 (Skill-Driven Capability Expansion):** You have a minimal default toolset. For specialized tasks (GUI automation, browsing, notes, batch queues), use `load_skill` to activate the appropriate procedure and unlock its dedicated tools. Call `close_skill` when done.

**Old system (principle 2).** Specialized work is entered by `load_skill`, and that skill's compiled tools are the only expansion of the kernel.

**Updated job concept.** `load_skill` still opens in-repo procedures. A project job that is already focused also loads the MCP tools of the integrations named on its brief. `focus_integration` opens one outside app when no project job should be focused. Both still leave every other server's tools out of the prompt.
   - **Principle 3 (Concise Reasoning):** Explain your plan concisely in `<thought>` before dispatching tools.
   - **Principle 4 (Active Recall):** If uncertain about a command or past lesson, emit `<recall>query</recall>` to query memory.
   - **Principle 5 (Completion):** Call `finish_task` with a clear summary when the user's goal has been met.

### 3.2. Core Base Toolset (7 Tools)

| Tool Name | Purpose | Parameters |
| :--- | :--- | :--- |
| `execute_shell` | Run deterministic bash commands, python scripts, file system queries, and system tools in the sandbox or host. | `command: str`, `sandboxed: bool = True` |
| `load_skill` | Activate a domain skill, loading its `SKILL.md` into prompt context and unlocking its declared tools for subsequent hops. | `skill_name: str` |
| `close_skill` | Deactivate the currently active skill, unloading its instructions and specialized tools to return to base kernel mode. | *(none)* |
| `get_current_time` | Query the high-precision internal clock snapshot. | *(none)* |
| `finish_task` | Signal completion of the user's task and return the spoken/chat summary. | `summary: str` |
| `share_file` | Send a local file to the web app for viewing and download, including on a phone. Always available, even while another skill is active. | `path: str`, `note: str = ""` |
| `list_skills` | List available skills (name, description, tools unlocked) so the agent can choose one before `load_skill`. Always available. | *(none)* |

**Old system.** The table above is the complete base tool set.

**Updated job concept.** [PROJECT_JOBS.md](PROJECT_JOBS.md) adds project-focus tools to the base set: `create_job`, `focus_job`, `list_jobs`, `update_job_brief`, `close_job`, `read_job_document`, `list_integrations`, and `focus_integration`. Batch tools stay on the `batch-runner` skill. MCP tool schemas are not base tools. They appear only while the named integration is loaded.

---

## 4. Built-in System Skills Catalog & Scoped Tools

All specialized tools are mapped to dedicated skills and are only available when the corresponding skill is loaded.

### 4.1. Built-in Skills Catalog

#### 1. `desktop-gui`
- **Purpose:** Interactive GUI navigation, pixel perception, and synthetic input injection.
- **When Activated:** User asks to interact with an open desktop app (GIMP, LibreOffice, settings dialogs) or perform screen-grounded clicks/typing.
- **Scoped Tools:**
  - `take_screenshot` (Capture screen image for visual inspection)
  - `get_active_window` (Query focused window title, class, geometry, PID)
  - `click_element` (Locate UI element visually by natural language and click)
  - `click` (Click specific coordinates)
  - `double_click` (Double click coordinates)
  - `type_text` (Type keystrokes into focused field)
  - `press_key` (Press key: enter, esc, tab, etc.)
  - `hotkey` (Press combination: ctrl+c, alt+tab, etc.)

#### 2. `browser-automation`
- **Purpose:** Headless Chromium web automation, DOM extraction, and dynamic SPA interaction.
- **When Activated:** Web research involving JavaScript SPAs, interactive online forms, or deep web scraping.
- **Scoped Tools:**
  - `browser_open`
  - `browser_extract`
  - `browser_click`
  - `browser_type`
  - `browser_screenshot`
  - `browser_evaluate`
  - `browser_close`

#### 3. `optimalx`
- **Purpose:** Personal notes, knowledge base search, and safe NoteX document mutations.
- **When Activated:** User explicitly asks about OptimalX notes, personal ideas, or synced mobile folders.
- **Scoped Tools:**
  - `optimalx_search_notes` (Vector + SQLite FTS search)
  - `optimalx_read_note` (Read full note markdown)
  - `optimalx_write_note` (Direct write for new notes / queue diff for existing notes)
  - `optimalx_list_folders` (Inspect parent and subfolder hierarchy)
  - `optimalx_read_notex_doc` (Read long-form NoteX markdown documents)
  - `optimalx_list_notex_docs` (List NoteX project files)
  - `optimalx_action` (Direct CDP or status interaction with desktop app)
- **Pruned Deadweight (Removed entirely from agent):**
  - `optimalx_reject_pending_review` (Human review action only in desktop app)
  - `optimalx_accept_pending_review` (Human review action only in desktop app)
  - `optimalx_list_pending_reviews` (Exposed only via HUD/UI or CLI)

**Old system.** OptimalX is a built-in skill whose tools are hand-written in this repo and unlocked by `load_skill`.

**Updated job concept.** That skill stays, because OptimalX does not provide an MCP server. A project job lists it as an integration. New apps are not added as further built-in skills with schemas in `ToolRegistry`. They are external MCP servers, attached the way section 13 of [PROJECT_JOBS.md](PROJECT_JOBS.md) attaches Gmail.

#### 4. `batch-runner`
- **Purpose:** Large-scale repetitive workflows (10 to 1,000+ items) using state-machine queues.
- **When Activated:** Multi-item repetitive evaluations, conversions, or audits.
- **Scoped Tools:**
  - `init_batch_job`
  - `run_batch_job`
  - `step_batch_job`
  - `get_job_status`
  - `pause_batch_job`
  - `resume_batch_job`
  - `generate_batch_summary`

#### 5. `web-research`
- **Purpose:** Standard live web lookups, URL reading, file downloads, and desktop browser launching.
- **When Activated:** General information retrieval, downloading PDFs/patents, or opening URLs for the user.
- **Scoped Tools:**
  - `web_search` (Brave / DuckDuckGo search)
  - `web_fetch` (Trafilatura clean article fetch)
  - `download_file` (Download internet files to workspace)
  - `open_in_browser` (Launch URL or local file in user's desktop browser)
- **File delivery:** `share_file` is a base tool and stays available even when this skill is not loaded. The procedure still requires calling it before finishing so documents reach the web app.

#### 6. `user-memory`
- **Purpose:** Modifying persistent autobiographical profile facts and workflow rules.
- **When Activated:** User explicitly asks to remember or forget a preference, habit, or convention.
- **Scoped Tools:**
  - `update_user_profile`
  - `add_workflow_rule`
  - `remove_workflow_rule`
  - `remove_user_fact`

#### 7. `skills-manager`
- **Purpose:** Collaborative authoring and editing of living `SKILL.md` procedures.
- **When Activated:** Creating or modifying user skills.
- **Scoped Tools:**
  - `create_skill`
  - `update_skill`
- **Discovery:** `list_skills` is a base tool and stays available even when this skill is not loaded.

---

## 5. Generic Skill Architecture & `SKILL.md` Specification

### 5.1. Directory Hierarchy
Skills can be installed globally, scoped to a project workspace, or provided as built-in system skills:
```text
~/.eidos/skills/                    # Global user skills
├── document-summarizer/
│   ├── SKILL.md                    # Primary instructions, output format, tools_required
│   └── templates/                  # Output templates
├── code-auditor/
│   ├── SKILL.md
│   └── scripts/                    # Optional supporting helper scripts
└── any-custom-skill/
    └── SKILL.md

~/MyProject/.eidos/skills/          # Project-specific workspace skills

eidos/skills/builtin/               # Built-in system skills (desktop-gui, optimalx, etc.)
```

### 5.2. Generic `SKILL.md` Specification with Dynamic Tool Declaration
Every `SKILL.md` uses standard YAML frontmatter followed by markdown sections:

```markdown
---
name: desktop-gui
version: 1.0.0
description: Visual desktop GUI navigation and synthetic mouse/keyboard input.
author: user & eidosbot
created_at: 2026-09-25
tags: [gui, x11, desktop]
tools_required:
  - take_screenshot
  - get_active_window
  - click_element
  - click
  - double_click
  - type_text
  - press_key
  - hotkey
---

# Desktop GUI Navigation Procedure

## 1. Objective
Interact with graphical desktop applications on Linux Mint via visual grounding and input events.

## 2. Evaluation Rubric & Quality Criteria
- Always confirm active window title and class before typing or clicking.
- Verify click outcomes with a follow-up screenshot if the state transition is ambiguous.
- Never guess coordinates when `click_element` can ground the target visually.

## 3. Step-by-Step Procedure
1. Step 1: Use `get_active_window` or `take_screenshot` to verify UI state.
2. Step 2: Emit `click_element` with a descriptive target or type into focused input.
3. Step 3: Complete interaction and call `close_skill` or `finish_task`.

## 4. Output Template / Schema
(Optional markdown template or artifact format)
```

### 5.3. Collaborative Co-Authoring Lifecycle
Skills are living documents co-authored between user and bot:
1. **User Prompt:** *"Eidos, I need to process a list of 500 customer bug reports and classify them by severity and root cause. Let's draft a skill."*
2. **Agent Proposal:** EidosBot generates a draft `SKILL.md` outlining the rubric, classification categories, required tools, and markdown report format.
3. **User Refinement:** The user edits or prompts adjustments (e.g., *"Add a category for regression bugs and include web_search in tools_required"*).
4. **Execution:** EidosBot loads the approved `SKILL.md` into the job runner.

---

## 6. Dynamic Tool Registry & AgentLoop Lifecycle

### 6.1. The Tool Registry (`eidos/core/tool_registry.py`)
All tool function schemas are organized in a clean registry:
```python
class ToolRegistry:
    """Central catalog of all available tool definitions."""

    BASE_TOOLS: List[Dict[str, Any]] = [
        # execute_shell, load_skill, close_skill, get_current_time, finish_task
    ]

    SPECIALIZED_TOOLS: Dict[str, Dict[str, Any]] = {
        "take_screenshot": { ... },
        "click_element": { ... },
        "optimalx_search_notes": { ... },
        # ... all remaining tools mapped by function name
    }

    @classmethod
    def get_scoped_tools(cls, tool_names: List[str]) -> List[Dict[str, Any]]:
        """Return base tools + explicitly requested specialized tools."""
        scoped = list(cls.BASE_TOOLS)
        for name in tool_names:
            if name in cls.SPECIALIZED_TOOLS:
                scoped.append(cls.SPECIALIZED_TOOLS[name])
        return scoped
```

### 6.2. Active Skill State in `AgentLoop`
The loop tracks active skill state across steps:
```python
class AgentLoop:
    def __init__(self, ...):
        ...
        self.active_skill: Optional[Dict[str, Any]] = None  # Loaded skill metadata & body
```

On every cycle hop:
1. **Assemble Tools:**
   ```python
   if self.active_skill:
       required_names = self.active_skill.get("tools_required", [])
       active_tools = ToolRegistry.get_scoped_tools(required_names)
   else:
       active_tools = ToolRegistry.BASE_TOOLS  # Base tools, including share_file
   ```
2. **Assemble System Prompt:**
   - Injects lean base prompt (system context, clock, user profile, episodic memory).
   - If `self.active_skill` is set, appends:
     ```markdown
     ---
     ACTIVE SKILL: {self.active_skill['name']}
     {self.active_skill['body']}
     ---
     ```
3. **Dispatch Handling:**
   - Calling `load_skill(name)`: Loads `SKILL.md`, sets `self.active_skill = loaded_skill`, and unlocks declared tools for subsequent hops.
   - Calling `close_skill()`: Resets `self.active_skill = None`, returning to base kernel mode.
   - Calling `finish_task()`: Concludes task and automatically resets `self.active_skill = None`.

---

## 7. Preserving the 15-Hop Budget & Shell Determinism

### 7.1. Why 15 Hops Failed Under the Old Monolithic Model
- 49 tools meant the agent frequently tried speculative tools (e.g., trying to read an OptimalX note, realizing it wasn't there, checking the web, taking a screenshot, clicking the wrong coordinate).
- Each failed attempt consumed 1 of the precious 15 hops.
- By step 10, the agent was thrashing and reached `max_steps` without completion.

### 7.2. Why Dynamic Scoping Protects the Budget
1. **Targeted Sub-Contexts:** In base mode, the agent only has `execute_shell`. If asked to check disk space or find a file, it runs `find` or `df` via `execute_shell` in **Step 1** and calls `finish_task` in **Step 2**. **Total hops used: 2 / 15.**
2. **Explicit Intent to Specialize:** The agent cannot wander into GUI clicks or OptimalX searches without consciously emitting `load_skill`. This deliberate step enforces reasoning discipline.
3. **Shell-First Scripting:** When asked to process 100 items or extract CSV data, the lean prompt directs the agent to write a 10-line Python script executed via `execute_shell`, finishing in 2 hops rather than looping 100 times.

---

## 8. Generic Batch Job State Machine (`eidos/jobs/`)

The queue below is a component a project can attach when it has a list of repeated items. The project itself — the short `job.yaml` brief, depth notes, focus, and scope tags — is specified in [PROJECT_JOBS.md](PROJECT_JOBS.md).

### 8.1. The Domain-Agnostic State Machine

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

### 8.2. Generic Queue Schema (`queue.json`)
Every field in `queue.json` is completely generic:

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

### 8.3. Universal Operational Capabilities
- **Ephemeral Per-Item Context:** Each item runs in a fresh, isolated sub-session with the `SKILL.md` injected. Item #500 runs with the exact same pristine context window and reasoning quality as item #1.
- **Durable Checkpointing:** Progress is written to disk after every single item. If the system restarts, it resumes from the next `pending` item.
- **Generic Human-in-the-Loop Hooks:** Any item that produces ambiguous results or fails automated checks is marked `"status": "needs_review"`, triggering a notification via HUD or Telegram without halting the remaining queue.

---

## 9. Before vs. After Architectural Comparison

| Metric / Dimension | Monolithic Architecture (Old) | Dynamic Skill Scoping (New) | Improvement |
| :--- | :--- | :--- | :--- |
| **System Prompt Size** | ~1,050 lines | ~120 lines | **~88% reduction** |
| **Global Tools per Step** | 49 tools | 7 tools (base mode, including `share_file` and `list_skills`) | **~86% reduction** |
| **Tool Overhead per Step** | ~4,500 tokens | ~450 tokens | **~90% token savings** |
| **Tools during Desktop GUI** | 49 tools | 15 tools (7 base + 8 GUI) | **69% fewer distraction paths** |
| **Tools during OptimalX Note** | 49 tools | 14 tools (7 base + 7 OptX) | **71% fewer distraction paths** |
| **OptimalX Hallucination Bias** | Extreme (hardcoded in prompt) | Zero (only loaded on demand) | **100% eliminated** |
| **Hop Budget Utilization** | High thrashing; easily hits 15 cap | 2–5 hops for shell; 5–8 for GUI | **Saves 50–70% of steps** |
| **Extensibility for New Apps** | Add more hardcoded tools to `context.py` | Drop a new `SKILL.md` in `~/.eidos/skills/` | **Modular & user-customizable** |

---

## 10. Phased Migration Plan (Roadmap Phase 12)

1. [x] **Phase 12.1: Tool Registry Decoupling (`eidos/core/tool_registry.py`)**
   - Separate tool schemas into `BASE_TOOLS` (7 tools, including always-on `share_file` and `list_skills`) and `SPECIALIZED_TOOLS`.
   - Purge deadweight human-approval tools (`optimalx_reject_pending_review`, `optimalx_accept_pending_review`).
2. [x] **Phase 12.2: Built-in System Skills Catalog (`eidos/skills/builtin/`)**
   - Author standard `SKILL.md` packages for `desktop-gui`, `optimalx`, `browser-automation`, `batch-runner`, `web-research`, `user-memory`, `skills-manager`.
   - Update `SkillManager` to search builtin skills alongside global (`~/.eidos/skills/`) and project skills.
3. [x] **Phase 12.3: Lean Base System Prompt Construction (`eidos/core/context.py`)**
   - Shrink `build_system_prompt()` to ~50 lines containing only environment snapshot, episodic/profile blocks, and 7 core universal principles.
   - Completely remove Principle 15 (OptimalX bias).
4. [x] **Phase 12.4: Active Skill Scope Engine (`eidos/core/loop.py`)**
   - Wire `load_skill` and `close_skill` in `_dispatch_tool`.
   - Assemble `messages` and `tools` dynamically based on `active_skill` state.
5. [x] **Phase 12.5: Regression & Low-Resource Safety Verification**
   - Verify isolated unit tests (126 core regression tests passing in ~8s) without overloading local hardware.

---

## 11. Appendix: Example Application Case Studies

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
