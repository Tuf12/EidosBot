# Implementation Plan: Context-Aware Dynamic Task Scoping & Multi-Source Fusion

**Specification:** [`docs/specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md`](../specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md)  
**Related Systems:** [`docs/PROJECT_JOBS.md`](../PROJECT_JOBS.md), [`docs/EPISODIC_MEMORY.md`](../EPISODIC_MEMORY.md), [`docs/plans/PLAN_EPISODIC_ENTITY_GRAPH.md`](PLAN_EPISODIC_ENTITY_GRAPH.md)  
**Target Modules:** `eidos/memory/types.py`, `eidos/memory/scope_resolver.py`, `eidos/memory/episodic_store.py`, `eidos/memory/recall_stream.py`, `eidos/core/loop.py`, `eidos/integrations/optimalx.py`, `eidos/memory/__init__.py`  
**Status:** Ready for Implementation  
**Estimated Complexity:** Medium (Runtime desktop environment sensing, calibrated multi-factor re-ranking math, atomic SQLite schema migrations, and mid-stream multi-source XML containerization)

---

## 1. Objectives & Architectural Guardrails

### Primary Goal
Implement **Context-Aware Dynamic Task Scoping & Multi-Source Fusion** (`eidos/memory/scope_resolver.py`) to systematically eliminate cross-project command contamination and bridge disconnected memory silos. At runtime, ambient execution context (working directory, Git project name, focused GUI window class/title, active skill, primary `job_id`, and active `integration`) is resolved into a structured `TaskScope`. Episodic vector search applies calibrated re-ranking score boosts for open tasks, enforces strict cross-job boundaries for project jobs, permits shared-entity cross-job hits via the entity graph, and disambiguates multi-source recall (`<recalled_memory>`, `<recalled_notes>`, `<recalled_conversation>`).

### Non-Negotiable Guardrails
1. **PC Resource Protection & Zero Neural Overhead:** All scope resolution (Git root discovery, path traversal, window inspection) runs purely on CPU in $< 1\text{ ms}$ with **zero LLM tokens and zero GPU usage**. Scope detection results are cached to prevent redundant filesystem I/O.
2. **Strict User Concurrency & Stability (User Rule Compliance):** Testing and verification must rely exclusively on isolated, lightweight unit tests with in-memory SQLite (`:memory:`) or temporary directories. **Never run tests that boot the full app back-to-back**, preventing thermal throttling, high memory consumption, and Cursor IDE crashes.
3. **Calibrated Soft-Boost vs. Strict Job Boundary Isolation:**
   - **General Open Tasks:** Apply an additive re-ranking boost ($+0.25$ project, $+0.15$ cwd, $+0.10$ skill) to elevate relevant memories without blinding the model to universal system commands (e.g., `tar -xvf`, `pkill`).
   - **Project Jobs (`job_id` present):** Strict project boundary isolation applies. Pure vector candidate episodes belonging to a different `job_id` are **strictly dropped** (no backfilling with unrelated projects).
   - **Entity Graph Cross-Job Bridge:** An episode from a different job is retained **only if** the relational `GraphStore` confirms a shared real-world entity (e.g. shared device, identical path, or shared email/domain). When returned, it is clearly tagged with its source `job_id` and integration, and the foreign job's brief, documents, and tools are **never loaded**.
4. **Non-Breaking Database Migration:** Add nullable scope columns to the `episodes` table in `~/.eidos/memory/vector_store/episodic.db` using safe `ALTER TABLE` operations within `_ensure_episode_schema_migrations()`. Legacy episodes missing scope attributes remain searchable without error.
5. **Thread-Safe Relational Transactions:** All SQLite schema updates, inserts, and queries must execute under `EpisodicStore._lock`.
6. **Clean Multi-Source Disambiguation:** Mid-stream `<recall>` queries must output distinct, well-formed XML blocks (`<recalled_memory scope="...">`, `<recalled_notes project="...">`, `<recalled_conversation>`) preventing context confusion in the LLM.

---

## 2. File Modification & Creation Inventory

| File | Change Type | Responsibility |
|---|---|---|
| `eidos/memory/types.py` | Modification | Add `TaskScope` Pydantic model; extend `EpisodeMetadata`, `EpisodeRecord`, and `EpisodicSearchResult` with scope fields (`project_name`, `workspace_path`, `cwd`, `active_skill`, `window_class`, `window_title`, `job_id`, `integration`). |
| `eidos/memory/scope_resolver.py` | **New File** | Environmental context sensor: Git repository detection, workspace path resolution, window class extraction, active skill resolution, and `TaskScope` generation. |
| `eidos/memory/episodic_store.py` | Modification | Add schema migrations for scope columns and index; update `index_episode()` to record scope; implement scope-aware re-ranking, project job isolation, and cross-job graph sharing in `search()` and `retrieve_macro_context()`. |
| `eidos/memory/recall_stream.py` | Modification | Accept `TaskScope` in `RecallStreamInterceptor`; pass scope to `EpisodicStore.search()` and `OptimalXBridge.search_notes()`; format scoped XML containers (`<recalled_memory scope="...">`, `<recalled_notes project="...">`). |
| `eidos/core/loop.py` | Modification | Resolve `task_scope` at the beginning of `run_task()`; pass scope to macro retrieval; update `recall_interceptor.set_current_scope()`; tag completed trajectories with scope metadata in `_finish_and_index_episode()`. |
| `eidos/integrations/optimalx.py` | Modification | Support project-scoped note searches by accepting `parent_folder=scope.project_name` with graceful fallback to global notes. |
| `eidos/memory/__init__.py` | Modification | Export `TaskScope` and `ScopeResolver`. |
| `tests/test_dynamic_scoping.py` | **New File** | Isolated, lightweight unit tests for scope resolution, Git detection, scoped re-ranking, job boundary isolation, entity graph cross-recall, and XML recall formatting. |

---

## 3. Detailed Phase-by-Phase Implementation Plan

### Phase 1: Data Contracts & Schema Migration (`types.py` & `episodic_store.py`)

1. **Add `TaskScope` Schema in `eidos/memory/types.py`:**
   ```python
   class TaskScope(BaseModel):
       """Runtime environmental scope defining the context of an execution turn."""

       project_name: Optional[str] = None       # e.g., "OptimalX", "EidosBot"
       workspace_path: Optional[str] = None     # e.g., "/home/tuf/CursorProjects/OptimalX"
       cwd: str = ""                            # Current working directory
       active_skill: Optional[str] = None       # e.g., "browser-automation", "desktop-gui"
       window_class: Optional[str] = None       # e.g., "Code", "Google-chrome"
       window_title: Optional[str] = None       # e.g., "main.py - EidosBot"
       job_id: Optional[str] = None             # Primary project job id (e.g., "notes-app")
       integration: Optional[str] = None        # Outside app integration (e.g., "gmail", "optimalx")
   ```

2. **Extend `EpisodeMetadata` in `eidos/memory/types.py`:**
   Add scope tracking attributes to persist context across runs:
   - `project_name: Optional[str] = None`
   - `workspace_path: Optional[str] = None`
   - `cwd: str = ""`
   - `active_skill: Optional[str] = None`
   - `window_class: Optional[str] = None`
   - `window_title: Optional[str] = None`
   - `job_id: Optional[str] = None`
   - `integration: Optional[str] = None`

3. **Extend `EpisodicSearchResult` in `eidos/memory/types.py`:**
   Add scope fields to search results:
   - `project_name: Optional[str] = None`
   - `workspace_path: Optional[str] = None`
   - `active_skill: Optional[str] = None`
   - `window_class: Optional[str] = None`
   - `job_id: Optional[str] = None`
   - `integration: Optional[str] = None`
   - `scope_boost: float = 0.0`
   - `cross_job_shared: bool = False`

4. **Schema Migrations in `eidos/memory/episodic_store.py` (`_ensure_episode_schema_migrations`):**
   ```sql
   ALTER TABLE episodes ADD COLUMN project_name TEXT;
   ALTER TABLE episodes ADD COLUMN workspace_path TEXT;
   ALTER TABLE episodes ADD COLUMN active_skill TEXT;
   ALTER TABLE episodes ADD COLUMN window_class TEXT;
   ALTER TABLE episodes ADD COLUMN job_id TEXT;
   ALTER TABLE episodes ADD COLUMN integration TEXT;

   CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes(project_name, active_skill, job_id);
   ```

5. **Update `index_episode()` in `eidos/memory/episodic_store.py`:**
   - Extract scope fields from `record.metadata` (falling back to step observations if necessary).
   - Write `project_name`, `workspace_path`, `active_skill`, `window_class`, `job_id`, and `integration` columns in SQLite `INSERT INTO episodes`.

---

### Phase 2: Ambient Environmental Scope Resolver (`eidos/memory/scope_resolver.py`)

1. **Design `ScopeResolver` Utility Class:**
   ```python
   class ScopeResolver:
       """Detects and resolves environmental execution boundaries."""

       _git_root_cache: Dict[str, Optional[Path]] = {}

       @classmethod
       def detect_git_root(cls, path: str | Path) -> Optional[Path]:
           """Ascend directory tree to locate nearest .git root directory (cached)."""

       @classmethod
       def detect_project_name(cls, path: str | Path) -> Optional[str]:
           """Detect Git root or nearest top-level project directory name."""

       @classmethod
       def resolve_current_scope(
           cls,
           cwd: Optional[str] = None,
           active_window: Optional[Dict[str, Any]] = None,
           active_skill: Optional[Dict[str, Any]] = None,
           active_job: Optional[Dict[str, Any]] = None,
       ) -> TaskScope:
           """Construct TaskScope from runtime desktop sensors and active state."""
   ```

2. **Detection Logic & Robustness Rules:**
   - **Git Root Discovery:** Search upward from `cwd` for a `.git` folder or file (submodules/worktrees). Cache results by resolved path. If no `.git` is found, extract the folder name of `cwd`.
   - **Window Sensing:** Safely extract `wm_class` (e.g. `"Google-chrome"`, `"Code"`) and `title` from `active_window` dictionary. Gracefully handle `None` when window managers or X11/Wayland interfaces are unavailable.
   - **Active Skill Extraction:** Extract skill `name` from `active_skill` dictionary.
   - **Project Job & Integration:**
     - Extract `job_id` from `active_job` (e.g. `active_job.get("job_id")`).
     - Extract `integration` from `active_job` or active tools (e.g. `"gmail"`, `"optimalx"`).

---

### Phase 3: Calibrated Scoped Re-Ranking & Job Isolation (`eidos/memory/episodic_store.py`)

1. **Re-Ranking Formulation:**
   When searching with `scope: Optional[TaskScope]`:
   - Fetch top $K \times 4$ candidate episodes by dense vector similarity / hybrid GAR.
   - For each candidate:
     - Compute base score: $S_{base} = (1 - \lambda) S_{vec} + \lambda S_{graph}$
     - Calculate scope boost:
       $$\Delta_{scope} = \Delta_{project} + \Delta_{cwd} + \Delta_{skill} + \Delta_{job} + \Delta_{integration}$$
       Where:
       - $\Delta_{project} = +0.25$ if `scope.project_name` matches `candidate.project_name` (non-null).
       - $\Delta_{cwd} = +0.15$ if `scope.workspace_path` matches `candidate.workspace_path` (non-null).
       - $\Delta_{skill} = +0.10$ if `scope.active_skill` matches `candidate.active_skill` (non-null).
       - $\Delta_{job} = +0.25$ if `scope.job_id` matches `candidate.job_id` (non-null).
       - $\Delta_{integration} = +0.10$ if `scope.integration` matches `candidate.integration` (non-null).
     - Final score: $S_{final} = S_{base} + \Delta_{scope}$.

2. **Job Boundary Isolation & Cross-Job Graph Bridge:**
   - If `scope.job_id` is defined:
     - **Strict Vector Pruning:** Candidate episodes with a non-null `candidate.job_id != scope.job_id` are **pruned** if they are pure vector matches.
     - **Graph-Augmented Bridge:** If a foreign job episode has a non-zero entity graph overlap ($\text{matched\_entities} > 0$), retain the candidate, set `cross_job_shared = True`, and preserve `candidate.job_id` and `candidate.integration` for explicit labeling.
     - **Environment Pitfalls Exception:** Pitfalls with `candidate.job_id IS NULL` (general OS/environment gotchas) are retained as cautionary warnings.

3. **Store Signatures:**
   - Update `EpisodicStore.search()` to accept `scope: Optional[TaskScope] = None`.
   - Update `EpisodicStore.retrieve_macro_context()` to accept `scope: Optional[TaskScope] = None`.

---

### Phase 4: Multi-Source Context Fusion & Mid-Stream Recall (`recall_stream.py` & `optimalx.py`)

1. **Scope Propagation in `RecallStreamInterceptor`:**
   - Add `current_scope: Optional[TaskScope] = None` property to `RecallStreamInterceptor`.
   - Add method `set_current_scope(scope: Optional[TaskScope]) -> None`.
   - Update `lookup_memory(query, scope: Optional[TaskScope] = None)` to pass the active scope to `episodic_store.search()`.

2. **Project-Scoped OptimalX Notes Lookup:**
   - In `lookup_optimalx_notes(query, scope: Optional[TaskScope] = None)`:
     - If `scope and scope.project_name`: query OptimalX with `parent_folder=scope.project_name`.
     - If no notes return or no scope exists, fallback to global `parent_folder=None`.

3. **Disambiguated XML Formatting in `format_recalled_memory()`:**
   Output distinctly containerized XML blocks:
   ```xml
   <recalled_memory episode="ep_20261114_001" scope="OptimalX" job_id="notes-app">
   - Past Task: Run frontend dev server for OptimalX
   - Proven Tool Pattern: execute_shell(cd ~/CursorProjects/OptimalX/web && npm run dev)
   - Matched Entities: cmd:npm, path:package.json
   </recalled_memory>

   <recalled_notes project="OptimalX">
   [OptimalX Note: docs/architecture.md]
   "Web client runs on port 5173 with proxy configured to gateway port 8000."
   </recalled_notes>
   ```
   When a hit originates from another job via entity sharing:
   ```xml
   <recalled_memory episode="ep_20261020_003" scope="ExternalJob" job_id="gmail-sync" integration="gmail" cross_job="true">
   - Notice: Recalled via shared entity 'email:user@domain.com'
   - Proven Tool Pattern: execute_shell(sync_token_refresh.sh)
   </recalled_memory>
   ```

---

### Phase 5: AgentLoop Lifecycle Integration & Episode Tagging (`eidos/core/loop.py`)

1. **Runtime Scope Resolution at Task Start:**
   In `AgentLoop.run_task(user_goal)`:
   ```python
   active_win = self.windows.get_active_window_info()
   task_scope = ScopeResolver.resolve_current_scope(
       cwd=os.getcwd(),
       active_window=active_win,
       active_skill=self.active_skill,
       active_job=self.active_job,
   )
   ```

2. **Macro Context Retrieval with Scope:**
   - Pass `task_scope` into `self.episodic_store.retrieve_macro_context(user_goal, scope=task_scope)`.
   - Set `self.recall_interceptor.set_current_scope(task_scope)`.

3. **Trajectory & Metadata Tagging at Task Completion:**
   In `AgentLoop._finish_and_index_episode()`:
   - Attach `task_scope` fields (`project_name`, `workspace_path`, `cwd`, `active_skill`, `window_class`, `job_id`, `integration`) to `EpisodeMetadata`.
   - Pass completed record to `self.episodic_store.index_episode(rec)` ensuring newly stored trajectories are tagged immediately.

---

### Phase 6: Isolated Verification & Unit Test Suite (`tests/test_dynamic_scoping.py`)

Create a fast, isolated unit test suite using `tempfile.TemporaryDirectory()` and mock embeddings. **Zero full-app runs or heavy loops.**

1. **`test_scope_resolver_git_and_cwd()`:**
   - Creates a temporary directory structure mimicking a Git repo with subfolders.
   - Verifies `detect_git_root` finds the root and `detect_project_name` returns the repo name.
   - Verifies caching behaves correctly and non-git directories fall back to directory basenames.

2. **`test_scope_resolver_desktop_sensors()`:**
   - Tests `resolve_current_scope()` with various combinations of window dicts, skills, and job manifests.
   - Verifies graceful handling of empty/None dictionaries without exceptions.

3. **`test_scope_schema_migration()`:**
   - Initializes an episodic database and runs `_ensure_episode_schema_migrations()`.
   - Verifies `project_name`, `workspace_path`, `active_skill`, `window_class`, `job_id`, and `integration` columns exist.
   - Verifies index `idx_episodes_scope` is created.

4. **`test_scoped_reranking_boosts()`:**
   - Injects two episodes with identical task text (`"build project"`):
     - Episode 1: `project_name="OptimalX"`, `active_skill="web"`, command `npm run build`.
     - Episode 2: `project_name="EidosBot"`, `active_skill="python"`, command `pytest`.
   - Searches with `TaskScope(project_name="OptimalX", active_skill="web")`.
   - Asserts Episode 1 is ranked #1 due to the $+0.35$ scope boost.
   - Searches with `TaskScope(project_name="EidosBot")`.
   - Asserts Episode 2 is ranked #1.

5. **`test_job_isolation_and_cross_job_graph_sharing()`:**
   - Injects Episode A (`job_id="job_alpha"`) and Episode B (`job_id="job_beta"`).
   - Searches with `TaskScope(job_id="job_alpha")`.
   - Verifies Episode B (pure vector) is excluded.
   - Links a shared entity (e.g., `cmd:git`) between Episode B and the query.
   - Verifies Episode B is now returned with `cross_job_shared = True` and labeled `job_id="job_beta"`.

6. **`test_recall_stream_xml_containerization()`:**
   - Mocks `RecallStreamInterceptor` with a populated episodic hit and OptimalX note.
   - Verifies `format_recalled_memory()` renders separated XML blocks with correct `scope` attributes.

---

## 4. Rollout Strategy & Step-by-Step Task Checklist

- [ ] **Step 1: Data Contracts & Types (`eidos/memory/types.py`)**
  - Define `TaskScope` model.
  - Extend `EpisodeMetadata`, `EpisodeRecord`, and `EpisodicSearchResult` with scope fields.
- [ ] **Step 2: Database Schema Migration & Persistence (`eidos/memory/episodic_store.py`)**
  - Add scope columns and composite index in `_ensure_episode_schema_migrations()`.
  - Update `index_episode()` to persist scope fields into SQLite.
- [ ] **Step 3: Environmental Scope Resolver Engine (`eidos/memory/scope_resolver.py`)**
  - Implement `ScopeResolver` with cached Git detection and desktop sensor resolution.
  - Export `ScopeResolver` and `TaskScope` in `eidos/memory/__init__.py`.
- [ ] **Step 4: Scoped Re-Ranking & Job Boundary Filtering (`eidos/memory/episodic_store.py`)**
  - Update `search()` and `_search_numpy_fallback()` / `_search_sqlite_vec()` to calculate $\Delta_{scope}$.
  - Implement job boundary isolation and cross-job graph bridge filtering.
  - Update `retrieve_macro_context()` to accept and pass `TaskScope`.
- [ ] **Step 5: Multi-Source Recall & XML Containerization (`eidos/memory/recall_stream.py` & `optimalx.py`)**
  - Wire `TaskScope` through `RecallStreamInterceptor`.
  - Update `format_recalled_memory()` to emit scoped `<recalled_memory>` and `<recalled_notes>` tags.
  - Wire project-scoped queries into `OptimalXBridge.search_notes()`.
- [ ] **Step 6: AgentLoop Lifecycle Integration (`eidos/core/loop.py`)**
  - Resolve `task_scope` at the beginning of `run_task()`.
  - Pass scope to macro context and recall interceptor.
  - Tag completed trajectories in `_finish_and_index_episode()`.
- [ ] **Step 7: Isolated Test Suite & Verification (`tests/test_dynamic_scoping.py`)**
  - Run focused unit tests with `pytest tests/test_dynamic_scoping.py`.
  - Verify zero regressions across `tests/test_episodic_store.py` and `tests/test_entity_graph.py`.
