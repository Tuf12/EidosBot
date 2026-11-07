# Implementation Plan: Sleep-Time Episodic Consolidation, Generalization & Pruning

**Specification:** [`docs/specs/SPEC_EPISODIC_CONSOLIDATION.md`](../specs/SPEC_EPISODIC_CONSOLIDATION.md)  
**Target Modules:** `eidos/memory/types.py`, `eidos/memory/episodic_store.py`, `eidos/memory/consolidator.py`, `eidos/memory/user_store.py`, `eidos/core/loop.py`, `eidos/gateway/server.py`  
**Status:** Ready for Implementation  
**Estimated Complexity:** Medium-High (Vector clustering math, atomic SQLite updates, multi-episode recipe synthesis, and idle background concurrency)

---

## 1. Objectives & Architectural Guardrails

### Primary Goal
Implement `EpisodicConsolidator` (`eidos/memory/consolidator.py`) to systematically combat memory entropy, vector bloat, and retrieval degradation. During system idle periods (or via explicit on-demand triggering), semantically similar episodes ($\text{cosine similarity} \ge 0.85$) are grouped into clusters, distilled into a single **Canonical Golden Episode**, and redundant runs are softly archived (`is_archived = 1`). High-frequency patterns ($\ge 3$ consistent executions) are promoted to **Tier B User Profile Guidelines** or **Draft Living Skills**.

### Non-Negotiable Guardrails
1. **Strict User Concurrency & Instant Interruption:** Consolidation must **never block or delay foreground user interactions**. It checks `agent_loop.is_foreground_busy()` / [`SPEC_COMPUTE_RESOURCE_POLICY.md`](../specs/SPEC_COMPUTE_RESOURCE_POLICY.md) and yields within $< 50\text{ ms}$ if a new user prompt is submitted or cancellation is requested.
2. **Pure CPU & Zero Neural Inference Overhead:** Vector clustering operates entirely via pre-computed NumPy embeddings stored in SQLite (`embedding_blob`). Clustering, synthesis, and deduplication run on CPU in $< 500\text{ ms}$ with **zero GPU compute or LLM token usage**.
3. **PC Resource Protection (Thermal & Crash Prevention):** To respect the host PC and prevent Cursor IDE crashes:
   - Max execution time is strictly capped at **30 seconds** per run.
   - Batch evaluation is capped at at most **100 unarchived episodes** per cycle.
   - Heavy background I/O is avoided; all updates run in an atomic SQLite transaction.
4. **Non-Destructive Archival:** Raw trajectory JSONL logs in `~/.eidos/trajectories/episodes.jsonl` are **never altered or deleted**, preserving full dataset integrity for offline LoRA fine-tuning. Archival is strictly a metadata flag (`is_archived = 1`) in SQLite.
5. **Zero Regression for Active Search:** `EpisodicStore.search()` and `retrieve_macro_context()` default strictly to `WHERE is_archived = 0`, ensuring archived duplicates never dilute the prompt context window.
6. **Thread-Safe Relational Transactions:** All schema migrations and consolidation updates must execute safely under `EpisodicStore._lock`.

---

## 2. File Modification & Creation Inventory

| File | Change Type | Responsibility |
|---|---|---|
| `eidos/memory/types.py` | Modification | Add `ConsolidationReport`, `ClusterSummary`, `CandidateRule` schemas, and add consolidation fields (`is_canonical`, `is_archived`, `cluster_id`, `superseded_by`, `consolidation_score`) to search and episode models. |
| `eidos/memory/episodic_store.py` | Modification | Add schema migrations for consolidation columns and indexes; update `search()` and `retrieve_macro_context()` to filter `is_archived = 0`; add `get_unarchived_episodes()` and `archive_episodes()`. |
| `eidos/memory/consolidator.py` | **New File** | Core consolidation engine: NumPy cosine similarity clustering, canonical golden recipe synthesis, pitfall aggregation, habit promotion, and idle execution management. |
| `eidos/memory/user_store.py` | Modification | Support staging and approving candidate workflow rules (`candidate_rules`) from consolidation. |
| `eidos/memory/__init__.py` | Modification | Export `EpisodicConsolidator`, `ConsolidationReport`, and new consolidation data models. |
| `eidos/core/loop.py` | Modification | Add `is_busy()` status tracking, record `last_task_completion_time`, and initialize optional background consolidation idle watcher. |
| `eidos/gateway/server.py` | Modification | Expose `POST /api/memory/consolidate` REST endpoint and broadcast `memory_consolidated` event over WebSockets. |
| `config.example.yaml` | Modification | Document configuration options under `memory.consolidation`. |
| `tests/test_consolidation.py` | **New File** | Lightweight, isolated unit tests for clustering, canonical synthesis, search filtering, rule promotion, and interruption safety without full-app startup. |

---

## 3. Detailed Phase-by-Phase Implementation Plan

### Phase 1: Data Contracts & Schema Migration (`types.py` & `episodic_store.py`)

1. **New Schema Definitions in `eidos/memory/types.py`:**
   ```python
   class CandidateRule(BaseModel):
       """Procedural rule or convention proposed by consolidation for Tier B review."""
       rule: str
       source_cluster_id: str
       occurrence_count: int
       detected_at: str = Field(
           default_factory=lambda: datetime.now(timezone.utc).isoformat()
       )

   class ClusterSummary(BaseModel):
       """Summary of an identified cluster of duplicate/similar episodes."""
       cluster_id: str
       task_goal_representative: str
       member_episode_ids: List[str]
       average_similarity: float
       canonical_episode_id: Optional[str] = None

   class ConsolidationReport(BaseModel):
       """Execution metrics returned by EpisodicConsolidator."""
       clusters_found: int = 0
       episodes_evaluated: int = 0
       episodes_archived: int = 0
       canonical_synthesized: int = 0
       promoted_rules_proposed: List[CandidateRule] = Field(default_factory=list)
       promoted_skills_proposed: List[str] = Field(default_factory=list)
       duration_seconds: float = 0.0
       interrupted: bool = False
   ```

2. **Extend `EpisodicSearchResult` in `eidos/memory/types.py`:**
   Add lifecycle tracking attributes:
   - `is_canonical: bool = False`
   - `is_archived: bool = False`
   - `cluster_id: Optional[str] = None`
   - `superseded_by: Optional[str] = None`
   - `consolidation_score: float = 1.0`

3. **Extend `UserProfile` in `eidos/memory/types.py`:**
   Add `candidate_rules: List[Dict[str, Any]] = Field(default_factory=list)` to stage suggested habits without polluting the active prompt context until user-approved.

4. **Schema Migrations in `eidos/memory/episodic_store.py` (`_ensure_episode_schema_migrations`):**
   ```sql
   ALTER TABLE episodes ADD COLUMN is_canonical INTEGER DEFAULT 0;
   ALTER TABLE episodes ADD COLUMN is_archived INTEGER DEFAULT 0;
   ALTER TABLE episodes ADD COLUMN cluster_id TEXT;
   ALTER TABLE episodes ADD COLUMN superseded_by TEXT;
   ALTER TABLE episodes ADD COLUMN consolidation_score REAL DEFAULT 1.0;

   CREATE INDEX IF NOT EXISTS idx_episodes_active ON episodes(is_archived, is_canonical);
   CREATE INDEX IF NOT EXISTS idx_episodes_cluster ON episodes(cluster_id);
   ```

5. **Search & Retrieval Filtering in `EpisodicStore`:**
   - In `_search_sqlite_vec()`: update query to:
     ```sql
     JOIN episodes e ON e.episode_id = v.episode_id
     WHERE v.embedding MATCH ? AND k = ? AND (e.is_archived = 0 OR ?)
     ```
   - In `_search_numpy_fallback()`: filter rows where `is_archived == 1` unless `include_archived=True`.
   - In `retrieve_macro_context()`: pass `include_archived=False`.
   - Add store helper methods:
     - `get_unarchived_episodes(limit: int = 100) -> List[Dict[str, Any]]`: fetches unarchived rows including `embedding_blob`, `full_record_json`, `critic_score`, and actions.
     - `archive_episodes(episode_ids: List[str], cluster_id: str, superseded_by: str) -> int`: updates rows in an atomic transaction.

---

### Phase 2: Mathematical Clustering Engine (`eidos/memory/consolidator.py`)

1. **Vector Matrix Construction:**
   - Query unarchived episodes ($N \le 100$) from SQLite.
   - For each episode with a valid 768-dim `embedding_blob`, extract float32 vector using `np.frombuffer(blob, dtype=np.float32)`.
   - Normalize vectors: $\hat{\mathbf{v}} = \frac{\mathbf{v}}{\|\mathbf{v}\|_2}$.
   - Form an $M \times 768$ matrix $\mathbf{V}$, where $M$ is the number of valid embedded candidates.

2. **Pairwise Cosine Similarity Matrix:**
   - Calculate full similarity matrix in a single BLAS dot product:
     $$\mathbf{S} = \mathbf{V} \mathbf{V}^T \in \mathbb{R}^{M \times M}$$
   - Computation time for $100 \times 100$: $< 2\text{ ms}$ on modern CPU.

3. **Greedy Leader Clustering Algorithm ($\text{threshold} = 0.85$):**
   - Maintain an unassigned set $U = \{0, 1, \dots, M-1\}$.
   - Sort candidates by `critic_score` descending (so highest-quality trajectories serve as cluster centroids).
   - While $U$ is not empty:
     - Pick the best candidate $c \in U$ as centroid.
     - Find all $j \in U$ where $\mathbf{S}_{c, j} \ge \text{threshold}$.
     - If $|M_{cluster}| \ge \text{min\_cluster\_size}$ (default 2):
       - Create cluster $C$ with members $\{j\}$.
       - Remove members from $U$.
     - Else:
       - Candidate is a singleton (unique task). Remove $c$ from $U$.
   - Returns partitioned dictionary: `Dict[str, List[Dict[str, Any]]]`.

---

### Phase 3: Canonical Golden Recipe Synthesis (`eidos/memory/consolidator.py`)

For each cluster $C = \{E_1, E_2, \dots, E_k\}$ where $k \ge 2$:

1. **Representative Task Goal:**
   - Pick the `task_goal` of the member with the highest `critic_score` and lowest error count.

2. **Action Path Selection (The "Golden Path"):**
   - Filter cluster members to successful runs (`outcome == "success"`). If none succeeded, take all runs.
   - Select the trajectory with the minimum step count that achieved success with zero failed tool observations.
   - Format actions into forward chronological sequence:
     $$\text{Pattern}_{canon} = \text{Action}_1 \rightarrow \text{Action}_2 \rightarrow \dots \rightarrow \text{Action}_m$$

3. **Pitfall & Dead-End Aggregation:**
   - Collect and parse `pitfalls_avoided` across all $k$ episodes in the cluster.
   - Deduplicate individual dead-end strings (e.g. `execute_shell(brightnessctl ...) -> [command not found]`).
   - Retain the top 3-4 most critical dead ends to provide maximum negative guidance without context bloat:
     $$\text{Pitfalls}_{canon} = \bigcup_{i=1}^k \text{Pitfalls}(E_i)$$

4. **Lessons Learned Synthesis:**
   - Extract unique non-empty `lessons_learned` from members.
   - Deduplicate sentences and combine them concisely.

5. **Consolidated Critic Score Calibration:**
   - A cluster with multiple verified successful executions warrants higher confidence than an unverified one-off run:
     $$\text{CriticScore}_{canon} = \min\left(1.0, \frac{1}{|C|} \sum_{E \in C} \text{CriticScore}(E) + 0.10\right)$$

6. **Persistence & Archival Handshake:**
   - Generate canonical episode ID: `ep_canon_<cluster_id[:8]>_<timestamp>`.
   - Build a synthetic `EpisodeRecord` marked with `is_canonical = 1`, `metadata.critic_score = CriticScore_{canon}`.
   - Index the canonical episode via `episodic_store.index_episode(canonical_record)`.
   - Update original cluster members in SQLite:
     ```sql
     UPDATE episodes
     SET is_archived = 1,
         cluster_id = :cluster_id,
         superseded_by = :canonical_id
     WHERE episode_id IN (:member_ids);
     ```

---

### Phase 4: Habit Promotion Engine (Tier B Profile & Skills)

When a consolidated cluster exhibits $\ge 3$ consistent executions:

1. **Tier B User Profile Promotion (`CandidateRule`):**
   - Analyze aggregated pitfalls and lessons learned for explicit procedural heuristics (e.g., matching regexes for `"always.*"`, `"prefer.*"`, `"confirm before.*"`, `"--dry-run"`, `"mkdir -p"`).
   - If a candidate rule is detected:
     - Check if it already exists in `user_profile.workflow_rules` or `user_profile.candidate_rules`.
     - If new, stage it in `user_store.add_candidate_rule(rule, source_cluster_id=cluster_id)`.
     - Record in `ConsolidationReport.promoted_rules_proposed`.

2. **Skill Scaffolding Candidate (`skills/manager.py`):**
   - If the task is multi-step ($\ge 3$ distinct tool calls) and executed $\ge 3$ times successfully:
     - Derive a clean slug: e.g. `"convert-svg-to-ico"`, `"backup-downloads"`.
     - Check if skill already exists in `SkillManager.list_skills()`.
     - If absent and `propose_skills` is enabled:
       - Scaffold template `SKILL.md` under `~/.eidos/skills/drafts/<slug>/SKILL.md` with frontmatter, objectives, proven tool sequences, and pitfalls.
       - Record in `ConsolidationReport.promoted_skills_proposed`.

---

### Phase 5: Idle Watcher & Concurrency Guards (`loop.py`, `consolidator.py`, `server.py`)

1. **Concurrency Guard in `AgentLoop` (`eidos/core/loop.py`):**
   - Add `self._is_busy = threading.Event()` (set at start of `run_task()`, cleared in `_complete_run()`).
   - Add method `is_busy() -> bool`:
     ```python
     def is_busy(self) -> bool:
         return self._is_busy.is_set() or (hasattr(self, "jobs") and self.jobs.is_worker_running())
     ```
   - Track `self._last_task_finished_time = time.monotonic()`.

2. **Interruption Check in `EpisodicConsolidator`:**
   - Before evaluating each cluster and before indexing/archival transactions:
     ```python
     if self.should_interrupt():
         logger.info("Consolidation yielding to foreground user activity.")
         report.interrupted = True
         break
     ```
   - `should_interrupt()` checks:
     - `self.agent_loop.is_busy()`
     - `time.monotonic() - start_time > max_runtime_seconds` (30s)
     - `self._stop_event.is_set()`

3. **Background Idle Runner (`IdleConsolidationRunner`):**
   - Optional daemon thread running inside `EpisodicConsolidator`:
     - Wakes every 60 seconds.
     - Checks if `time.monotonic() - last_task_finished_time >= idle_threshold_seconds` (default 1800s / 30m).
     - If idle and not busy, runs `run_consolidation()`.
     - Can be enabled/disabled via `config.yaml` (`memory.consolidation.enabled`).

4. **Gateway REST Endpoint (`eidos/gateway/server.py`):**
   - Expose `POST /api/memory/consolidate` (accepts `{ "force": true }`).
   - Runs consolidation in thread pool and returns `ConsolidationReport.model_dump()`.
   - Broadcasts WebSocket event `memory_consolidated` to notify connected web/mobile UIs.

---

### Phase 6: Unit Testing & Verification (`tests/test_consolidation.py`)

Create a fast, isolated unit test suite using an in-memory SQLite database (`:memory:`) or `tempfile.TemporaryDirectory()`. **Zero full-app runs or heavy loops.**

1. **`test_schema_migration_columns()`:**
   - Verifies `is_canonical`, `is_archived`, `cluster_id`, `superseded_by`, and `consolidation_score` columns and indexes are created automatically.
2. **`test_clustering_grouping_similarity()`:**
   - Injects 4 synthetic episodes:
     - 2 duplicate tasks ("Sort downloads folder by month") with cosine similarity $\approx 0.95$.
     - 1 unrelated task ("Check audio sinks").
     - 1 slightly different task ("Find large files in downloads").
   - Asserts the 2 duplicate tasks form a cluster while singletons remain unclustered.
3. **`test_canonical_synthesis_and_pitfall_aggregation()`:**
   - Injects Cluster Member A with pitfall `"mv target not found"` and Cluster Member B with pitfall `"permission denied on subfolder"`.
   - Runs consolidation.
   - Verifies canonical episode combines both unique pitfalls and has `is_canonical = 1`.
   - Verifies original episodes have `is_archived = 1` and `superseded_by = canonical_id`.
4. **`test_search_excludes_archived_records()`:**
   - Asserts `EpisodicStore.search()` and `retrieve_macro_context()` return only the active canonical episode and exclude archived members.
5. **`test_concurrency_interruption()`:**
   - Mocks `agent_loop.is_busy() = True` after cluster 1; verifies loop aborts cleanly and returns `interrupted = True` in $< 50\text{ ms}$.
6. **`test_candidate_rule_staging()`:**
   - Injects 3 episodes with a recurring heuristic (`"Always check find before bulk delete"`).
   - Verifies `candidate_rules` is populated in `UserStore` without modifying `workflow_rules` directly.

---

## 4. Rollout Strategy & Step-by-Step Task Checklist

- [ ] **Step 1: Data Models & Schema (`eidos/memory/types.py`)**
  - Add `CandidateRule`, `ClusterSummary`, `ConsolidationReport`.
  - Update `EpisodicSearchResult` and `UserProfile` with consolidation fields.
- [ ] **Step 2: Database Migration & Store Support (`eidos/memory/episodic_store.py`)**
  - Update `_ensure_episode_schema_migrations()` with consolidation columns and indexes.
  - Add `is_archived = 0` filtering to `_search_sqlite_vec()`, `_search_numpy_fallback()`, and `retrieve_macro_context()`.
  - Add `get_unarchived_episodes()` and `archive_episodes()` methods.
- [ ] **Step 3: UserStore Candidate Rules Support (`eidos/memory/user_store.py`)**
  - Add `add_candidate_rule()`, `approve_candidate_rule()`, and `reject_candidate_rule()`.
- [ ] **Step 4: Consolidation Engine Implementation (`eidos/memory/consolidator.py`)**
  - Implement `EpisodicConsolidator` class.
  - Implement BLAS NumPy vector similarity clustering.
  - Implement `synthesize_canonical_episode()` with pitfall/lesson aggregation and score boosting.
  - Implement habit promotion for candidate rules and draft skills.
  - Implement `IdleConsolidationRunner` background thread with interruption guards.
- [ ] **Step 5: AgentLoop & Subsystem Integration (`eidos/core/loop.py`, `eidos/memory/__init__.py`)**
  - Add `is_busy()` and idle tracking in `AgentLoop`.
  - Wire `EpisodicConsolidator` into `AgentLoop` lifecycle.
  - Export classes in `eidos/memory/__init__.py`.
- [ ] **Step 6: Gateway API & Configuration (`eidos/gateway/server.py`, `config.example.yaml`)**
  - Add `POST /api/memory/consolidate` endpoint.
  - Document `memory.consolidation` block in `config.example.yaml`.
- [ ] **Step 7: Verification & Unit Tests (`tests/test_consolidation.py`)**
  - Run focused unit tests with `pytest tests/test_consolidation.py`.
  - Verify zero regressions across `tests/test_episodic_store.py` and `tests/test_entity_graph.py`.
