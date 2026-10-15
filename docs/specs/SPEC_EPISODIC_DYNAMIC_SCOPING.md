# Specification: Context-Aware Dynamic Task Scoping & Multi-Source Cross-Referencing

**Document:** `docs/specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md`  
**Status:** Draft / Specification  
**Component:** `eidos/memory/scope_resolver.py`, `eidos/memory/recall_stream.py`  
**Related Systems:** `eidos/core/loop.py`, `eidos/memory/episodic_store.py`, `eidos/integrations/optimalx.py`, `eidos/memory/conversation_store.py`

---

## 1. Executive Summary & Problem Statement

### 1.1. Problem Statement
Currently, memory retrieval in EidosBot operates globally without situational boundary awareness:
1. **Cross-Project Contamination:** If the user is working inside `~/CursorProjects/OptimalX` (a TypeScript/React project) and asks to *"run the test suite"*, global vector retrieval might match an episode from `~/CursorProjects/EidosBot` (a Python project) and inject `execute_shell(pytest tests/)` as a "Proven Tool Pattern" instead of `execute_shell(npm test)`.
2. **Disconnected Context Silos:** EidosBot interacts with four separate memory stores:
   - **Active Conversational Turns:** In SQLite `eidos.db` (`ConversationStore`).
   - **Episodic Vector Trajectories:** In SQLite `episodic.db` (`EpisodicStore`).
   - **User Atomic Fact Profile:** In `~/.eidos/user_profile.yaml` (`UserStore`).
   - **Personal Project Notes & Knowledge Base:** In `OptimalX` (`OptimalXBridge`).
   These sources operate in isolation with no unified scoping mechanism. When a recall tag `<recall>query</recall>` fires, it queries `episodic_store` and `optimalx` independently, without factoring in the active project directory, active GUI window, or active skill.

### 1.2. Proposed Solution
Introduce **Context-Aware Dynamic Task Scoping & Multi-Source Cross-Referencing**:
- **Dynamic Scope Resolution (`ScopeResolver`):** Detects the ambient execution context at runtime:
  - Working directory (`cwd`) and Git root project name.
  - Active X11 window class and window title.
  - Currently loaded skill (`active_skill`).
- **Scope-Augmented Vector Ranking (Soft-Boost Re-ranking):** Rather than blindly pruning cross-project memories (which might contain universal shell tricks like `tar -xvf` or `pkill`), apply a calibrated **Scope Boost (+0.25 similarity)** to episodes matching the active project, directory, or skill.
- **Unified Multi-Source Context Fusion:** Coordinates mid-stream `<recall>` queries across Episodic Memory, OptimalX Notes, and recent Conversation History, returning clear, disambiguated XML blocks (`<recalled_memory>`, `<recalled_notes>`, `<recalled_conversation>`).

---

## 2. Architecture & Scoped Retrieval Flow

```
                      [ User Goal / Prompt Received ]
                                     │
                                     ▼
                        [ ScopeResolver.resolve() ]
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
      [ Workspace Scope ]     [ Skill Scope ]       [ Window Scope ]
      Project: OptimalX       Skill: desktop-gui    App: Google-chrome
      CWD: ~/Cursor.../web    Tools: click, type    Title: PR #42
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     ▼
                           [ Execution Scope ]
                       {
                         "project": "OptimalX",
                         "cwd": "~/CursorProjects/OptimalX/web",
                         "skill": "desktop-gui",
                         "window_class": "Google-chrome"
                       }
                                     │
                                     ▼
                         [ Scoped Vector Query ]
                    Retrieve top-K candidates from
                    EpisodicStore + Entity Graph
                                     │
                                     ▼
                    [ Calibrated Re-Ranking Boost ]
             • Exact Project Match:   +0.25 similarity
             • Exact CWD Match:       +0.15 similarity
             • Active Skill Match:    +0.10 similarity
                                     │
                                     ▼
                    [ Top Golden Path & Pitfall ]
                 Cleanly scoped to current environment
```

---

## 3. Scope Schema & Storage Tagging

### 3.1. Episode Metadata Scope Extension
Add scope columns to `episodes` table in `~/.eidos/memory/vector_store/episodic.db`:

```sql
ALTER TABLE episodes ADD COLUMN project_name TEXT;
ALTER TABLE episodes ADD COLUMN workspace_path TEXT;
ALTER TABLE episodes ADD COLUMN active_skill TEXT;
ALTER TABLE episodes ADD COLUMN window_class TEXT;

CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes(project_name, active_skill);
```

### 3.2. Scope Model (`eidos/memory/types.py`)
```python
class TaskScope(BaseModel):
    """Runtime environmental scope defining the context of an execution turn."""

    project_name: Optional[str] = None       # e.g., "OptimalX", "EidosBot"
    workspace_path: Optional[str] = None     # e.g., "/home/tuf/CursorProjects/OptimalX"
    cwd: str = ""                            # Current working directory
    active_skill: Optional[str] = None       # e.g., "browser-automation", "desktop-gui"
    window_class: Optional[str] = None       # e.g., "Code", "Google-chrome"
    window_title: Optional[str] = None       # e.g., "main.py - EidosBot"
```

---

## 4. Scope Resolver Specification (`eidos/memory/scope_resolver.py`)

```python
class ScopeResolver:
    """Detects and resolves environmental execution boundaries."""

    @staticmethod
    def resolve_current_scope(
        cwd: Optional[str] = None,
        active_window: Optional[Dict[str, Any]] = None,
        active_skill: Optional[Dict[str, Any]] = None,
    ) -> TaskScope:
        """Construct TaskScope from runtime desktop sensors."""
        ...

    @staticmethod
    def detect_project_name(path: str | Path) -> Optional[str]:
        """Detect Git root or nearest top-level project directory name."""
        ...
```

---

## 5. Scope-Aware Ranking & Multi-Source Fusion

### 5.1. Scoped Re-Ranking Formula
When querying `EpisodicStore.search(query, scope=task_scope)`:
1. Fetch top $K \times 4$ candidate episodes by dense vector similarity.
2. Compute **Scoped Relevance Score**:
   $$S_{final} = S_{vec} + \Delta_{project} + \Delta_{cwd} + \Delta_{skill}$$
   Where:
   - $\Delta_{project} = 0.25$ if `candidate.project_name == scope.project_name` (and `project_name` is non-null).
   - $\Delta_{cwd} = 0.15$ if `candidate.workspace_path == scope.workspace_path`.
   - $\Delta_{skill} = 0.10$ if `candidate.active_skill == scope.active_skill`.
3. Sort by $S_{final}$ descending and return the top $K$.

### 5.2. Multi-Source Active Recall Disambiguation
When `<recall>query</recall>` executes in `RecallStreamInterceptor`:
- Pass `task_scope` to `EpisodicStore` to ensure matching past episodes reflect the active project.
- Pass `task_scope.project_name` to `OptimalXBridge.search_notes()` to scope personal notes to the active project folder first before falling back to global notes.
- Format results into distinct, cleanly separated XML containers:

```xml
<recalled_memory episode="ep_20260910_opt_1" scope="OptimalX">
- Past Task: Run frontend dev server for OptimalX
- Proven Tool Pattern: execute_shell(cd ~/CursorProjects/OptimalX/web && npm run dev)
</recalled_memory>

<recalled_notes project="OptimalX">
- Location: docs/architecture.md
- Snippet: Web client runs on port 5173 with proxy configured to gateway port 8000.
</recalled_notes>
```

---

## 6. Integration Points

1. **`eidos/core/loop.py`:**
   - In `AgentLoop.run_task()`: Resolve `task_scope = ScopeResolver.resolve_current_scope(...)`.
   - Pass `task_scope` to `EpisodicStore.retrieve_macro_context(user_goal, scope=task_scope)`.
   - When finishing episode: tag `EpisodeRecord` with `task_scope` attributes.
2. **`eidos/memory/recall_stream.py`:**
   - Wire `task_scope` into mid-stream `<recall>` interception.
3. **`eidos/memory/episodic_store.py`:**
   - Accept optional `scope: Optional[TaskScope]` in `search()` and `retrieve_macro_context()`.

---

## 7. Test Strategy & Verification

- **Unit Tests (`tests/test_dynamic_scoping.py`):**
  - Verify `ScopeResolver.detect_project_name()` identifies git roots and workspace folders.
  - Scoped re-ranking test: create two similar tasks ("run build") from project A (npm) and project B (cargo). Verify that querying within project A boosts the npm episode to the top, while querying within project B boosts the cargo episode.
  - Multi-source disambiguation: verify `<recall>` correctly tags XML blocks with project scope and prevents cross-talk.
