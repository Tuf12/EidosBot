# EidosBot Project Jobs Specification
## Scoped Working Memory, Attachable Components & Focus Tags

This document defines how a **job** becomes the project EidosBot is working inside. A job is the scope that keeps the agent on the actual thing: an advertisement, an app, an invention, or any other project. The batch queue remains a component a job can attach. It is not the definition of a job.

The batch runner, queue schema, and per-item processor stay as specified in [SKILLS_AND_JOBS.md](SKILLS_AND_JOBS.md). This document changes what `job.yaml` is for, how the agent sees it, and how depth documents are read.

---

## Architectural mandates

> **1. The brief is the source of truth for what this project is.**  
> `job.yaml` is short, structured, and injected while the job is the primary focus. If a similar memory, search hit, or prior-art result disagrees with the brief, the brief wins.
>
> **2. Depth lives in files the brief points at.**  
> Longer explanation belongs in `.md` or `.txt` files inside the job directory. Those files are read by path and heading when a step needs them. They are not embedded into the global episodic vector store.
>
> **3. One primary focus.**  
> EidosBot has one primary job. That job's full brief is what the agent reasons from. Additional jobs may be open or running, and the user can see them, but their full briefs are not loaded at the same time. More simultaneous briefs make the agent less coherent.
>
> **4. Components attach. Domains do not.**  
> Skills, a batch queue, ad-variant runs, invention evaluation, and a later job-local document index are components a job can include. Core job schemas stay domain-agnostic. Rubrics, scores, and domain fields live in `SKILL.md` files and in the depth documents.
>
> **5. Focus survives the turn.**  
> Finishing a chat task does not close the job. The user and the agent close or switch focus on purpose.

---

## 1. Problem

Today `job.yaml` is regenerated from `queue.json` on every item save. It stores a name, one skill, directories, and progress counts. The chat loop clears that manifest at the start of every user message and again on `finish_task`. The next turn retrieves episodic memory by similarity to the latest sentence.

That retrieval is the failure mode this design exists to stop. An agent asked to invent something new slides toward the nearest thing it has seen, because similarity search rewards resemblance. The same slide happens on any project: a new ad becomes a generic ad, a new app becomes a familiar app. A global vector database makes this worse by offering every semantically nearby note at once.

A project needs a small file that states what this thing is, what it is not, which skills belong to it, and where the longer notes live.

---

## 2. Cognitive split

| Store | Question it answers | When it is used |
| :--- | :--- | :--- |
| User profile (`~/.eidos/user_profile.yaml`) | Who the user is | Every turn |
| Episodic memory (vector store) | How a past attempt went | Lessons tagged with this job, then generic pitfalls |
| Skills (`SKILL.md`) | How to perform a procedure | When the job lists that skill and the step needs it |
| **Job brief (`job.yaml`)** | **What this project is right now** | **Every turn while it is the primary focus** |
| Depth files (`.md`, `.txt`) | The long explanation | Read when the brief says this step needs that file |

The brief is not a fifth copy of the profile, and it is not a dump of embeddings. It is the boundary of the current project.

---

## 3. Directory layout

```
~/EidosWorkspace/jobs/<project>/
├── job.yaml                 # short brief; always injected for the primary job
├── queue.json               # present only when a batch component is attached
├── notes/
│   ├── INDEX.md             # table of contents for depth files
│   ├── concept.md           # what the thing is, in full
│   └── decisions.md         # history that no longer fits in the brief
└── artifacts/               # generated output (portfolios, variants, reports)
```

`queue.json` remains the checkpointed item list from [SKILLS_AND_JOBS.md](SKILLS_AND_JOBS.md). Saving the queue must not rewrite the brief. The runner may refresh the `progress` block inside `job.yaml`. It must leave identity, boundary, decisions, skills, and document pointers untouched.

---

## 4. `job.yaml` schema

The file stays direct. One-line fields. Lists of short entries. No pasted essays.

```yaml
job_id: job_20261010_083000_a1b2
name: Coil lantern
status: active                 # active | paused | completed | archived
focus: primary                 # primary | attached

identity:
  summary: A hand-cranked lantern whose coil stores a turn and releases it as light.
  goal: Hold the mechanical concept steady while the first prototype notes are written.
  phase: concept

boundary:
  this_is: A spring coil that is wound by hand and unwinds through a small generator.
  this_is_not: A battery lantern, a solar lantern, or a chemical glow stick.
  distinct_from:
    - name: Shake flashlight
      difference: Energy is stored in a wound coil, not in a sliding magnet.

decisions:
  - id: d1
    text: The crank winds the coil directly. No separate gearbox in the first prototype.
    at: 2026-10-10T13:00:00Z

open_questions:
  - How many turns the coil should hold before the release feels useful.

constraints:
  - No battery in the core design.

skills:
  - name: web-research
    use_when: Checking whether a mechanism already exists.
  - name: batch-runner
    use_when: Evaluating a list of mechanism variants one at a time.

components:
  batch_queue: queue.json      # omit the key when no queue is attached

documents:
  - path: notes/concept.md
    role: concept
    summary: Full mechanical description and the parts list.
    read_when: Changing the mechanism or explaining it to the user.

progress:                      # written by the runner; absent when there is no queue
  total_items: 0
  completed_items: 0
  failed_items: 0
  pending_items: 0
  percent: 0

directories:
  job_dir: /home/user/EidosWorkspace/jobs/coil-lantern
  notes_dir: /home/user/EidosWorkspace/jobs/coil-lantern/notes
  artifacts_dir: /home/user/EidosWorkspace/jobs/coil-lantern/artifacts
  queue_file: null
  manifest_file: /home/user/EidosWorkspace/jobs/coil-lantern/job.yaml
```

Rules for the brief:

- `identity.summary` and `boundary` are required once the project is real enough to name. A brand-new job may start with a name and a goal, then gain a boundary as soon as the user states what is new.
- `boundary.distinct_from` is the anti-collapse anchor. Workers and the chat agent both receive it before any similar example, search hit, or prior-art note.
- `decisions` is an append-only short log. When it grows past what belongs in the prompt, older entries move into `notes/decisions.md` and the brief keeps the ones still in force.
- `skills` is the set this project uses. Entering primary focus unlocks those skills' tools. The agent does not re-discover them from the global catalog on every turn.
- `documents` is the retrieval index. Each entry names a path, a one-line summary, and when to read it.

---

## 5. How depth files are used

Do not embed a job's `.md` and `.txt` files into the global episodic vector store. Similarity search is what pulls the agent off the new thing and onto the nearest familiar thing.

Use the files like this:

1. The primary brief is already in the prompt. It is small enough to read on every step.
2. `documents` (and `notes/INDEX.md`, which mirrors that list) is the table of contents.
3. When the current step matches a document's `read_when`, the agent or the item worker reads that file by path. If the brief names a heading, read that section.
4. The worker uses the file as source material for this step, then returns to the brief. It does not keep the whole file in context for later items.
5. Episodic recall answers "what went wrong last time we tried this," and only inside this job's tagged episodes. It does not answer "what is this project."

A job-local search index may be added later as a component, the same way a batch queue is added. It searches only that job's directory, and only after the index is not enough. It stays off until a project actually outgrows headings. It never writes into the global vector database.

`notes/INDEX.md` is for the human and for the agent when it is browsing. `job.yaml` `documents` is what the prompt trusts. If they disagree, the yaml wins, and the agent updates the index.

---

## 6. Components

A component is an ability attached to a job. The core job does not know what an invention score or an ad variant is.

| Component | What it adds | Where it lives |
| :--- | :--- | :--- |
| Skill | A procedure and its tools | `skills:` in the brief, body in `SKILL.md` |
| Batch queue | Item-by-item checkpointed work | `queue.json`, run by `BatchJobRunner` |
| Depth notes | Long project memory | `notes/*.md`, listed under `documents` |
| Job-local index | Search across this job's notes only | Future component; off by default |

Examples, all on the same engine:

- An invention project attaches `web-research` and, when a list of concepts exists, a batch queue whose items are evaluated by an invention skill. The brief's `boundary` is passed into each item before prior-art hits.
- An advertising project attaches a writing skill and, optionally, a queue of variants. The brief holds the offer, the audience, and what this campaign is not.
- An app project attaches the skills for the stack in use. Specs and screen notes live in `notes/`. There is no queue until there is a real list of repeated items.

`BatchJobRunner` is unchanged in role: it steps `queue.json` with a fresh context per item. Each item's context receives the parent brief's identity and boundary, then the assigned skill, then the item input.

---

## 7. Focus

### 7.1. Primary job

The agent has one primary job. While it is set:

- The full brief is appended to the system prompt on every step.
- The job's skills are the tools in scope, on top of the base kernel.
- New episodes are tagged with this `job_id`.
- `retrieve_macro_context` searches episodes with that tag first. Generic pitfalls from outside the job are included only when they are about the tool or the environment, not about a different project.
- The floating pill and the web app show the job name as the scope tag.

`run_task` loads this focus. It does not clear it. `finish_task` ends the chat turn. It does not clear the job. `close_job` and an explicit switch do.

### 7.2. Additional jobs

Additional jobs are allowed. They are attached, not co-equal.

- An attached job may be idle, or a background batch worker may be running it.
- The prompt receives one status line per attached job: name, status, and whether a worker is running. It does not receive that job's boundary, decisions, or documents.
- The pill and the web app show those jobs as secondary tags, visually quieter than the primary tag, and only while they are running or explicitly pinned.
- Switching primary focus is a deliberate tool call. The previous primary becomes attached or simply saved on disk. Its brief leaves the prompt.
- If the user starts talking about a different project, the agent says so and offers to switch. It does not silently load a second full brief.

The default is one job. Several running queues are a real case (a long invention batch in the background while the user talks through the primary project). Several full briefs in one prompt is not.

### 7.3. Where focus is stored

Focus is agent-wide, so the pill and the web app agree. It is stored beside the jobs, in a small pointer file such as `~/EidosWorkspace/jobs/focus.yaml`:

```yaml
primary: job_20261010_083000_a1b2
attached:
  - job_20261009_120000_cc11
```

Conversation history does not own focus. Switching chats does not switch jobs. The agent can still notice that the conversation and the primary job are about different work, and offer to switch.

---

## 8. What the agent sees

Base tools gain project focus, separate from the batch-runner skill:

- `create_job` creates a directory and a minimal `job.yaml`, and makes it primary.
- `focus_job` switches the primary job.
- `list_jobs` returns names, statuses, and which one is primary.
- `update_job_brief` patches identity, boundary, decisions, open questions, constraints, skills, or document pointers. It does not accept a free-form essay.
- `close_job` clears primary focus. Running workers are left running and stay visible as secondary tags until they finish or are paused.
- `read_job_document` reads one depth file, or one heading, from the primary job.

`init_batch_job`, `run_batch_job`, `step_batch_job`, `pause_batch_job`, `resume_batch_job`, and `generate_batch_summary` stay on the `batch-runner` skill. A job lists that skill when it has a queue.

Prompt block for the primary job, rebuilt from `job.yaml` each step:

```
PRIMARY JOB: Coil lantern (job_20261010_083000_a1b2)
This is: A spring coil that is wound by hand and unwinds through a small generator.
This is not: A battery lantern, a solar lantern, or a chemical glow stick.
Distinct from: Shake flashlight — energy is stored in a wound coil, not in a sliding magnet.
Goal: Hold the mechanical concept steady while the first prototype notes are written.
Phase: concept
Decisions in force:
- The crank winds the coil directly. No separate gearbox in the first prototype.
Open questions:
- How many turns the coil should hold before the release feels useful.
Skills:
- web-research — Checking whether a mechanism already exists.
- batch-runner — Evaluating a list of mechanism variants one at a time.
Documents (read only when the step matches):
- notes/concept.md — Full mechanical description and the parts list. Read when changing the mechanism.
Attached jobs (status only):
- Variant sweep — running, 40/120
If a similar memory or search result conflicts with this brief, follow the brief.
```

Item workers receive the same identity and boundary block at the top of their system prompt, ahead of skill text and ahead of search results. Prior art and similar examples are labeled as comparison material.

---

## 9. Scoped recall

When an episode is indexed, store `job_id` of the primary job at that time (empty when no job was focused).

`retrieve_macro_context` then:

1. Searches successes and pitfalls with that `job_id`.
2. Adds at most one unscoped pitfall when it is about a tool, command, or environment failure rather than another project's content.
3. Labels job hits as this project's experience and any unscoped hit as a general caution.

`<recall>` uses the same filter while a primary job is set. A recall that finds nothing inside the job says so, and does not backfill with the nearest other project.

OptimalX notes stay a skill the job can list. They are not searched automatically just because a project is open.

---

## 10. User-visible scope tags

The user should be able to see the scope without opening a file.

**Floating pill** (`eidos/ui/pill.py`): under the status line, a compact tag with the primary job name. Example: `Job · Coil lantern`. While other jobs are running, a second quieter line lists them, truncated, such as `Running · Variant sweep 40/120`. No primary job means no tag.

**Web app** (`web/src/components/Header.tsx`): a chip beside the conversation title for the primary job. Running or pinned attached jobs are smaller chips. The chip is a label, not a second conversation switcher. A later click target may open job details; the first version only displays state.

**Gateway:** broadcast `job_focus` whenever focus, brief name, or a worker's running state changes. Include the same payload on connect so a phone that opens mid-session sees the tags immediately. Telegram `/status` lists the primary job and any running jobs.

Payload:

```json
{
  "primary": {"job_id": "job_20261010_083000_a1b2", "name": "Coil lantern", "phase": "concept"},
  "running": [
    {"job_id": "job_20261009_120000_cc11", "name": "Variant sweep", "status": "in_progress", "completed_items": 40, "total_items": 120}
  ]
}
```

---

## 11. Implementation plan

Work in this order. Each phase is usable before the later ones.

### Phase 13.1 — Durable brief

- Add a project-brief model in `eidos/jobs/types.py` for identity, boundary, decisions, skills, documents, and components.
- Change `eidos/jobs/runner.py` so `save_queue` updates queue state and the `progress` block only. It must not regenerate the rest of `job.yaml`.
- Create `notes/INDEX.md` when a job is created, and keep `documents` and the index aligned on update.
- Tests in `tests/test_jobs.py`: saving a queue preserves boundary and decisions; progress counts still update.

### Phase 13.2 — Focus that survives the turn

- Add `focus.yaml` read/write on `BatchJobRunner` (or a small sibling in `eidos/jobs/`).
- Stop clearing `AgentLoop.active_job` at the start of `run_task` and inside `finish_task` (`eidos/core/loop.py`). `close_job` still clears it.
- Support one primary job and a list of attached job ids. Background workers count as running attached jobs even if they are not pinned.
- Tests: a second `run_task` still sees the same primary brief; `finish_task` does not drop it; `close_job` does.

### Phase 13.3 — Prompt and base tools

- Inject the primary brief, plus one status line per attached or running job, from `_assemble_system_prompt`.
- Add `create_job`, `focus_job`, `list_jobs`, `update_job_brief`, and `read_job_document` to the base tool set in `eidos/core/tool_registry.py`. Keep batch tools on `batch-runner`.
- Unlock the primary job's declared skills while it is focused.
- Add one behavioral line to `eidos/core/context.py`: when the user is continuing a project, stay inside the primary job; when they start a different project, offer to switch focus.
- Tests in `tests/test_context.py` and `tests/test_dynamic_scoping.py` for the prompt block and the base tool list.

### Phase 13.4 — Scoped recall

- Add optional `job_id` to episode records in `eidos/memory/types.py` and to the episodic store schema in `eidos/memory/episodic_store.py`.
- Tag episodes from the active primary job in `eidos/core/loop.py`.
- Filter `retrieve_macro_context` and `<recall>` as described in section 9.
- Tests in `tests/test_recall_stream.py`: a hit from another job is not returned as this project's experience.

### Phase 13.5 — Workers receive the brief

- Pass identity and boundary into `SkillJobProcessor.process_item` ahead of skill text and search results.
- Label prior-art and similar-product material as comparison only, in both the invention path and the generic path (`eidos/jobs/processor.py`).
- Tests: the processor prompt for an item contains `this_is_not` and `distinct_from` from the parent brief.

### Phase 13.6 — Scope tags

- Broadcast `job_focus` from `eidos/gateway/server.py` on focus changes, worker start/stop, and client connect.
- Show the primary tag and running-job tags on the floating pill (`eidos/ui/pill.py`).
- Show the same chips in `web/src/components/Header.tsx`, with types in `web/src/types/gateway.ts`.
- Extend Telegram `/status` to list primary plus running jobs.
- Verify the pill and the web header against a focused job, a second running job, and the empty state.

### Phase 13.7 — Regression

- Run the job, context, scoping, and recall tests. Do not boot the full app repeatedly.
- Confirm an existing `queue.json` from before this change still loads. Missing brief fields mean "no boundary yet," not a hard failure.

---

## 12. Out of scope for this pass

- A click-through job editor in the web app. Tags are display-only first.
- Embedding depth files, globally or per job.
- Automatic creation of a job for every chat. The agent creates or switches a job when the user is actually working a project.
- Changing the batch item state machine, pacing, or ranking summary format.
