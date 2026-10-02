# OptimalX Bi-Directional Note & Knowledge Integration Specification

This document defines the architecture, data contracts, retrieval mechanics, mutation lifecycle, and safety boundaries for **EidosBot's integration with the OptimalX Knowledge and Notes Ecosystem** (spanning OptimalX Desktop and OptimalX Mobile).

---

## 1. Vision & System Purpose

The **OptimalX Integration** transforms EidosBot from an isolated task executor into a **personally anchored, knowledge-augmented assistant**.

### 1.1. The Personal Knowledge Problem
Standard desktop agents either:
1. Maintain their own isolated text notes or vector databases, creating fragmented silos separate from the user's daily mobile notes.
2. Rely on cloud-hosted note providers (Notion, Apple Notes, Google Keep), leaking private personal, medical, or business thoughts to third-party cloud infrastructure.
3. Lack safe human-in-the-loop safeguards when modifying user writing, leading to accidental overwrites or data loss.

### 1.2. The OptimalX Solution
Because **OptimalX** is the user's personally developed, local-first note-taking and knowledge platform (with synced Electron Desktop and Android Mobile apps):
- **Universal Synchronization:** Notes captured on an Android phone on the go sync back to the Linux Mint desktop via a private **Tailscale** WireGuard mesh.
- **Single Source of Truth:** All synchronized notes, folders, and summaries converge in a single local SQLite database on the desktop host (`~/OptimalX/data.db`).
- **Zero Cloud Exposure:** Both OptimalX and EidosBot operate 100% locally on the user's hardware (RTX 5060 Ti + Ollama/Kokoro/Parakeet).
- **Human-in-the-Loop Diff Review:** Modifications to existing notes are safeguarded by OptimalX's native Diff Review system, preventing autonomous agents from silently corrupting user prose.
- **Local Read-Only WAL Resilience:** EidosBot can read notes directly from SQLite even when OptimalX Desktop is not currently running.

---

## 2. High-Level Architecture & Network Topology

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 OPTIMALX + EIDOS MESH                                  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   📱 OPTIMALX MOBILE (Android)                                                         │
│   • Jetpack Compose Editor & Room DB                                                   │
│   • Tailscale IP: 100.x.y.z                                                            │
│        │                                                                               │
│        │ Encrypted WireGuard Sync (Push / Pull)                                        │
│        ▼                                                                               │
│   🖥️ LINUX MINT DESKTOP (Eidos Host & OptimalX Hub)                                    │
│   Tailscale IP: 100.85.20.14                                                           │
│   ┌────────────────────────────────────────────────────────────────────────────────┐   │
│   │ OptimalX Desktop 1.0 (Electron)                                                │   │
│   │ • Sync & LLM Server: http://127.0.0.1:7373                                      │   │
│   │   - POST /api/v1/sync/push (Mobile -> Desktop Sync)                            │   │
│   │   - POST /api/v1/sync/pull (Desktop -> Mobile Sync)                            │   │
│   │   - POST /api/v1/llm/search (Semantic Chunk Vector Search)                     │   │
│   │ • Diff Review UI: Pending Change Review Panel & Checkpoints                    │   │
│   └───────────────────────────────────────┬────────────────────────────────────────┘   │
│                                           │                                            │
│                                           ▼                                            │
│   ┌────────────────────────────────────────────────────────────────────────────────┐   │
│   │ Local Storage Layer (Single Source of Truth)                                   │   │
│   │ • SQLite: ~/OptimalX/data.db (WAL Mode: Readers never block writers)           │   │
│   │ • NoteX Markdown Corpus: ~/OptimalX/notex/<project_global_id>/*.md             │   │
│   │ • Auth & Preferences: ~/.config/optimalx-desktop/config.json                  │   │
│   └───────────────────────▲────────────────────────────────▲───────────────────────┘   │
│                           │                                │                           │
│              [Offline / Direct Query]             [Live Tool Execution]                │
│                           │                                │                           │
│   ┌───────────────────────┴────────────────────────────────┴───────────────────────┐   │
│   │ EIDOSBOT AGENT RUNTIME                                                         │   │
│   │                                                                                │   │
│   │  OptimalXBridge (`eidos/integrations/optimalx.py`)                             │   │
│   │  • Tier 1: Live HTTP Semantic Vector Search (Port 7373)                        │   │
│   │  • Tier 2: Direct SQLite Read-Only (mode=ro) WAL Reader & FTS                  │   │
│   │  • Tier 3: Diff Review Proposal & Safe Direct Write Applier                    │   │
│   │  • Tier 4: Chrome DevTools Protocol (CDP:9222) Visual / DOM Inspection         │   │
│   │                                                                                │   │
│   │  Safety Gatekeeper (`eidos/safety/gatekeeper.py`)                              │   │
│   │  • Read/Search: Level 0 (Auto-Allowed)                                         │   │
│   │  • Create/Edit: Level 1 (Diff Review Queued or User Approved)                  │   │
│   │                                                                                │   │
│   │  Episodic Memory RAG (`eidos/memory/recall_stream.py`)                         │   │
│   │  • Injects relevant personal notes during planning and <recall> thoughts       │   │
│   └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Storage Architecture & Schemas

### 3.1. SQLite Database: `~/OptimalX/data.db`
The database operates in Write-Ahead Logging mode (`PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;`). In WAL mode, SQLite supports concurrent read operations without locking writers, allowing EidosBot to execute high-speed queries while OptimalX or mobile sync is actively writing.

#### Relevant Tables & Fields:
```sql
-- Hierarchical Folder Structures
CREATE TABLE parent_folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  deleted_at INTEGER,
  is_system_folder INTEGER NOT NULL DEFAULT 0,
  global_id TEXT NOT NULL UNIQUE,
  origin_device_id TEXT
);

CREATE TABLE subfolders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_folder_id INTEGER NOT NULL REFERENCES parent_folders(id),
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  deleted_at INTEGER,
  is_system_subfolder INTEGER NOT NULL DEFAULT 0,
  project_summary TEXT,
  project_summary_updated_at INTEGER,
  target_platform TEXT NOT NULL DEFAULT 'mobile',
  global_id TEXT NOT NULL UNIQUE,
  origin_device_id TEXT,
  notex_enabled INTEGER NOT NULL DEFAULT 0
);

-- Core Notes Storage
CREATE TABLE notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subfolder_id INTEGER NOT NULL UNIQUE REFERENCES subfolders(id),
  content TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  ai_locked INTEGER NOT NULL DEFAULT 0,          -- Read-only flag: agent must never mutate
  ai_blind INTEGER NOT NULL DEFAULT 0,           -- Privacy flag: agent must never read or index
  summary TEXT,
  summary_chunks_json TEXT,
  summary_updated_at INTEGER,
  summary_content_watermark INTEGER,
  global_id TEXT NOT NULL UNIQUE,
  origin_device_id TEXT,
  content_hash TEXT                              -- SHA-256 hex string of UTF-8 content
);

-- Pre-computed Semantic Chunks (for Instant Vector Search)
CREATE TABLE semantic_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL,                     -- 'note', 'file', 'notex_doc'
  object_id INTEGER NOT NULL,
  parent_folder_id INTEGER,
  subfolder_id INTEGER,
  location TEXT,
  chunk_text TEXT NOT NULL,
  chunk_type TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  embedding_blob BLOB,
  updated_at INTEGER NOT NULL
);

-- Diff Review & Audit Queue
CREATE TABLE pending_change_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  change_set_id INTEGER NOT NULL,
  source_type TEXT NOT NULL,                     -- 'note', 'file'
  source_id INTEGER NOT NULL,                    -- subfolder_id
  proposed_content TEXT NOT NULL,
  status TEXT NOT NULL,                          -- 'pending', 'accepted', 'rejected'
  created_at INTEGER NOT NULL
);

CREATE TABLE content_checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  content_blob TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  author TEXT NOT NULL,                          -- 'user', 'eidos', 'sync'
  label TEXT,
  created_at INTEGER NOT NULL
);
```

### 3.2. NoteX Corpus Documents: `~/OptimalX/notex/`
For long-form writing, book ingests, and multi-file projects, OptimalX uses NoteX:
- Located at `~/OptimalX/notex/<subfolder_global_id>/`.
- Contains structured markdown files: `Canon.md`, `Outline.md`, and thematic folders with `.notex/manifest.json` and `.notex/activity.jsonl`.
- EidosBot can read and navigate NoteX document trees natively.

### 3.3. Configuration & Authentication: `~/.config/optimalx-desktop/config.json`
Contains connection metadata:
```json
{
  "deviceId": "desktop_71e896ba3015",
  "syncToken": "<bearer_token>",
  "syncPort": 7373,
  "models": {
    "embedding": "nomic-embed-text:latest"
  }
}
```

---

## 4. Privacy, Safety & Permissions Matrix

Privacy and non-destructive execution are core invariants:

| Flag / Operation | Rule | EidosBot Behavior |
|---|---|---|
| **`ai_blind = 1`** | **Strict Privacy** | Notes marked `ai_blind` are **completely filtered out** of all SQL queries, vector searches, and document listings. EidosBot will never observe or ingest private notes. |
| **`ai_locked = 1`** | **Strict Immutability** | Notes marked `ai_locked` can be read (if `ai_blind == 0`), but EidosBot is strictly barred from modifying, appending to, or proposing changes to them. |
| **`notes_read / search`** | **Level 0 (Passive)** | Auto-allowed without user prompt interruptions. |
| **`notes_create` (New Note)** | **Level 1 (Safe Action)** | When creating a new subfolder/note (or writing to an empty note), changes are auto-applied directly with a checkpoint, enabling batch runs to proceed without blocking. |
| **`notes_edit` (Existing Note)** | **Level 1 (Diff Review)** | Changes to existing non-empty notes are queued into `pending_change_items` for human-in-the-loop review in the OptimalX desktop UI, unless explicitly overridden for append-only logs. |

---

## 5. Dual-Tier Retrieval Engine

To ensure ultra-low latency and 100% offline reliability, EidosBot implements a hybrid two-tier retrieval strategy:

```
                          ┌───────────────────────────────┐
                          │     User / Agent Query        │
                          └──────────────┬────────────────┘
                                         │
                                         ▼
                          ┌───────────────────────────────┐
                          │  Is OptimalX Server Running?  │
                          │   (Port 7373 Health Check)    │
                          └──────┬─────────────────┬──────┘
                                 │ YES             │ NO / Closed
                                 ▼                 ▼
                  ┌──────────────────────┐  ┌──────────────────────┐
                  │    TIER 1: ONLINE    │  │   TIER 2: OFFLINE    │
                  │ Live Semantic Search │  │ Direct SQLite (WAL)  │
                  │   (Port 7373 HTTP)   │  │   (file:...mode=ro)  │
                  └──────────┬───────────┘  └──────────┬───────────┘
                             │                         │
                             │ Returns Cosine Matches  │ Returns Exact / LIKE Matches
                             └───────────┬─────────────┘
                                         │
                                         ▼
                          ┌───────────────────────────────┐
                          │ Filter `ai_blind == 0`        │
                          │ Filter `deleted_at IS NULL`   │
                          │ Return Formatted Note Chunks  │
                          └───────────────────────────────┘
```

### 5.1. Tier 1: Live Semantic Vector Search (`POST /api/v1/llm/search`)
When OptimalX Desktop is active:
1. `OptimalXBridge` queries `http://127.0.0.1:7373/api/v1/llm/search` with the Bearer token.
2. The endpoint uses the pre-computed embeddings inside `semantic_chunks` (generated via local `nomic-embed-text:latest`).
3. Returns top-$K$ matched snippets ranked by cosine similarity with precise line ranges, file paths, and folder titles.

### 5.2. Tier 2: Direct SQLite Read-Only WAL Reader
When OptimalX Desktop is closed:
1. `OptimalXBridge` connects directly to `file:/home/tuf/OptimalX/data.db?mode=ro`.
2. Executes optimized SQL index queries across `notes.content`, `notes.summary`, `subfolders.name`, and `parent_folders.name`.
3. Guarantees zero latency and instantaneous cold-start access without spawning external processes.

### 5.3. NoteX Document Reader
For projects enabled with NoteX (`subfolders.notex_enabled == 1`):
1. Resolves project path: `~/OptimalX/notex/<subfolder_global_id>/`.
2. Recursively traverses markdown files, parsing headings and frontmatter.
3. Allows targeted section reading via start/end line offsets.

---

## 6. The Mutation & Diff Review Lifecycle

When EidosBot creates or updates notes, it must maintain absolute consistency with OptimalX Desktop's revision engine and OptimalX Mobile's sync protocol.

### 6.1. Writing to Empty Notes (Auto-Apply)
If a note is brand new or its current content is blank:
1. Compute SHA-256 `content_hash` over the UTF-8 content bytes.
2. Record `content_checkpoints` entry (`author = 'eidos'`, `label = 'Created by EidosBot'`).
3. Set `origin_device_id = 'desktop_eidosbot'` and `updated_at = current_epoch_ms`.
4. Result: **Immediate direct write.** Batch workflows (like patent analysis or daily logs) complete uninterrupted.

### 6.2. Modifying Existing Notes (Human-in-the-Loop Diff Review)
If a note already contains user-written content:
1. Ensure user's working copy is committed to `content_checkpoints`.
2. Propose content via `pending_change_items` (`status = 'pending'`).
3. Return structured status to EidosBot:
   ```json
   {
     "status": "queued",
     "subfolder_id": 142,
     "note_title": "Workout Plan",
     "message": "Proposed updates queued in OptimalX Diff Review panel."
   }
   ```
4. EidosBot informs the user: *"I've drafted the revisions to your 'Workout Plan' note and queued them for your review in OptimalX."*
5. The user reviews the side-by-side visual diff in OptimalX Desktop and clicks **[Accept]** or **[Reject]**.

### 6.3. Synchronization Consistency with Mobile
Because EidosBot adheres to the exact field contracts:
- `content_hash` matches mobile SHA-256 expectations.
- `updated_at` triggers incremental sync detection.
- When the user taps **"Pull from Desktop"** (or auto-sync triggers over Tailscale), the Android phone pulls the updated notes and checkpoints seamlessly.

---

## 7. EidosBot Tool Interface & Cognitive Routing

### 7.1. Agent Tool Definitions (`eidos/core/context.py`)

```json
[
  {
    "type": "function",
    "function": {
      "name": "optimalx_search_notes",
      "description": "Search personal notes and knowledge bases across OptimalX (synced from mobile and desktop). Uses semantic vector search when online, falling back to SQL text search.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": { "type": "string", "description": "Natural language search query or keywords." },
          "limit": { "type": "integer", "description": "Maximum number of note chunks to return (default: 5)." },
          "parent_folder": { "type": "string", "description": "Optional: Filter results to a specific parent folder name." }
        },
        "required": ["query"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "optimalx_read_note",
      "description": "Read the full content and metadata of a specific OptimalX note.",
      "parameters": {
        "type": "object",
        "properties": {
          "subfolder_id": { "type": "integer", "description": "Subfolder ID containing the note." },
          "title": { "type": "string", "description": "Optional: Subfolder/note title if ID is unknown." }
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "optimalx_list_folders",
      "description": "List top-level parent folders and subfolders in OptimalX to discover project structure.",
      "parameters": {
        "type": "object",
        "properties": {
          "parent_folder_id": { "type": "integer", "description": "Optional: ID of parent folder to list subfolders for." }
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "optimalx_write_note",
      "description": "Create a new note or propose modifications to an existing note in OptimalX. Empty notes write immediately; existing notes queue for Diff Review.",
      "parameters": {
        "type": "object",
        "properties": {
          "parent_name": { "type": "string", "description": "Parent folder name (e.g. 'Inventions', 'Research')." },
          "subfolder_title": { "type": "string", "description": "Title of the note / subfolder." },
          "content": { "type": "string", "description": "Markdown content to write." },
          "mode": { "type": "string", "enum": ["create", "append", "propose_edit"], "description": "Write mode." }
        },
        "required": ["parent_name", "subfolder_title", "content"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "optimalx_read_notex_doc",
      "description": "Read a long-form markdown document from an OptimalX NoteX corpus project.",
      "parameters": {
        "type": "object",
        "properties": {
          "subfolder_id": { "type": "integer", "description": "NoteX subfolder ID." },
          "doc_path": { "type": "string", "description": "Relative document path (e.g. 'Canon.md', 'Outline.md')." }
        },
        "required": ["subfolder_id", "doc_path"]
      }
    }
  }
]
```

### 7.2. Episodic Recall Integration (`eidos/memory/recall_stream.py`)
During complex reasoning tasks, the cognitive loop checks if the topic relates to known user projects:
1. Mid-stream `<recall>` tags can search OptimalX notes in addition to past episodic trajectories.
2. Injects relevant personal context seamlessly into the reasoning stream:
   ```xml
   <recalled_notes>
   [OptimalX Note: Inventions / AI Agent Architecture]
   "Key user requirement: Local Kokoro TTS and bwrap sandboxing for zero cloud egress."
   </recalled_notes>
   ```

---

## 8. Implementation Phases & Milestones

### Phase 11.1: Core Read & Search Bridge (`eidos/integrations/optimalx.py`)
- Implement `search_notes(query, limit, parent_filter)`.
- Connect to HTTP endpoint `POST /api/v1/llm/search` with bearer token fallback.
- Implement direct SQLite `mode=ro` fallback querying `notes`, `subfolders`, `parent_folders`.
- Ensure strict exclusion of `deleted_at IS NOT NULL` and `ai_blind = 1`.

### Phase 11.2: Hierarchy Navigation & NoteX Corpus
- Implement `list_folders(parent_folder_id=None)` returning parent and child hierarchies.
- Implement `read_note(subfolder_id, title)`.
- Implement `read_notex_doc(subfolder_id, doc_path)` reading from `~/OptimalX/notex/`.

### Phase 11.3: Safe Mutation & Diff Review Engine
- Implement `write_note(parent_name, subfolder_title, content, mode)`:
  - Check for existing parent and subfolder (auto-create if missing).
  - Inspect existing note content:
    - If empty: apply direct write via SQLite WAL transaction, computing SHA-256 `content_hash`, UUID `global_id`, and creating checkpoint.
    - If non-empty: insert into `pending_change_items` for OptimalX Diff Review queue.
  - Return clear, actionable status for the LLM.

### Phase 11.4: Tool Registration & Gatekeeper Policies
- Add tool schemas to `eidos/core/context.py`.
- Route action dispatches in `eidos/core/loop.py`.
- Update `eidos/safety/risk_classifier.py` (Level 0 for reads, Level 1 for writes).

### Phase 11.5: Episodic Memory RAG & Batch Job Integration
- Extend `eidos/memory/recall_stream.py` to support cross-referencing OptimalX notes.
- Allow `eidos/jobs/processor.py` to pull reference notes during batch runs.

### Phase 11.6: Verification & Test Suite
- Comprehensive pytest suite in `tests/test_optimalx_notes.py`:
  - Test online search (mocked HTTP).
  - Test offline SQLite WAL queries with live `~/OptimalX/data.db` and fixture databases.
  - Test `ai_blind` privacy filtering.
  - Test new note auto-write vs existing note Diff Review queuing.
  - Verify sync compatibility (SHA-256 hash validation).
