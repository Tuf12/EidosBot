# Specification: Sleep-Time Episodic Consolidation, Generalization & Pruning

**Document:** `docs/specs/SPEC_EPISODIC_CONSOLIDATION.md`  
**Status:** Draft / Specification  
**Component:** `eidos/memory/consolidator.py`  
**Related Systems:** `eidos/memory/episodic_store.py`, `eidos/memory/user_store.py`, `eidos/skills/manager.py`

---

## 1. Executive Summary & Problem Statement

### 1.1. Problem Statement
In continuous autonomous operation, an episodic memory store that only appends new records suffers from entropy and degradation:
1. **Redundancy & Vector Bloat:** If a user repeatedly asks EidosBot to *"organize ~/Downloads"* or *"check git status across repos"* 30 times over two months, 30 near-identical episodes are indexed. In vector search, the top 5 results are saturated by slight variations of the exact same task, crowding out other relevant memories.
2. **Obsolete & Superseded Knowledge:** An early episode might document a messy 7-step workaround using an outdated CLI tool. A later episode discovers a 1-step native flag that replaces the workaround. Without consolidation, both coexist, and the outdated workaround may be retrieved and regurgitated.
3. **No Generalization to Principles or Skills:** Human memory does not store raw episodic video logs forever; during sleep, the hippocampus and neocortex consolidate specific experiences into **semantic facts** and **procedural habits**. EidosBot currently lacks this consolidation phase.

### 1.2. Proposed Solution
Implement `EpisodicConsolidator` (`eidos/memory/consolidator.py`), a background "sleep-time" consolidation engine:
- **Idle / Off-Peak Triggering:** Executes strictly during idle periods (e.g., after 30 minutes of no user activity or via scheduled maintenance), consuming negligible CPU and zero GPU when the user is active.
- **Hierarchical Clustering:** Groups semantically similar episodes (cosine similarity $\ge 0.85$) into task clusters.
- **Canonical Recipe Synthesis:** Distills repetitive clusters into a single **Canonical Golden Episode**, aggregating all encountered pitfalls and edge cases into one high-signal entry.
- **Rule & Skill Promotion:** Automatically flags recurring procedural patterns ($\ge 3$ consistent executions) as candidates for:
  - **Tier B User Profile:** Promoted into atomic workflow guidelines in `~/.eidos/user_profile.yaml`.
  - **Living Skills:** Generated as template `SKILL.md` procedures in `~/.eidos/skills/`.
- **Soft Pruning & Archival:** Marks redundant, low-quality, or superseded episodes as `archived = 1` in SQLite, excluding them from prompt retrieval while preserving raw trajectory logs for offline LoRA dataset training.

---

## 2. Architecture & Consolidation Lifecycle

```
                    [ Trigger: Idle Interval (30m) or API ]
                                       │
                                       ▼
                       [ Lock Check & Activity Guard ]
                 (Aborts immediately if user prompt arrives)
                                       │
                                       ▼
                      [ Step 1: Trajectory Clustering ]
                   Group unarchived episodes by similarity
                   (Agglomerative clustering, threshold 0.85)
                                       │
                                       ▼
                     [ Step 2: Canonical Synthesis ]
              ┌────────────────────────┴────────────────────────┐
              ▼                                                 ▼
     [ Singletons (Unique) ]                          [ Clusters (N >= 2) ]
       Retain active index                            Synthesize Golden Recipe:
                                                      • Combine unique pitfalls
                                                      • Extract best action chain
                                                      • Mark superseded as archived
                                                                │
                                                                ▼
                                                [ Step 3: Promotion Engine ]
                                                Detect recurring habits (N >= 3)
                                                                │
                                       ┌────────────────────────┴────────────────────────┐
                                       ▼                                                 ▼
                             [ Candidate User Rule ]                           [ Candidate Skill ]
                             "Always pass --dry-run                            Create draft
                              to rsync before executing"                       ~/.eidos/skills/sync/
                                       │                                                 │
                                       ▼                                                 ▼
                              [ Staged for Review ]                            [ Staged for Review ]
                              (HUD Notification / GUI)                         (HUD Notification / GUI)
```

---

## 3. Database Schema Updates

Add lifecycle metadata columns to `episodes` table in `~/.eidos/memory/vector_store/episodic.db`:

```sql
-- Add archival, cluster, and canonical references
ALTER TABLE episodes ADD COLUMN is_canonical INTEGER DEFAULT 0;
ALTER TABLE episodes ADD COLUMN is_archived INTEGER DEFAULT 0;
ALTER TABLE episodes ADD COLUMN cluster_id TEXT;
ALTER TABLE episodes ADD COLUMN superseded_by TEXT;
ALTER TABLE episodes ADD COLUMN consolidation_score REAL DEFAULT 1.0;

CREATE INDEX IF NOT EXISTS idx_episodes_active ON episodes(is_archived, is_canonical);
CREATE INDEX IF NOT EXISTS idx_episodes_cluster ON episodes(cluster_id);
```

### Retrieval Filter Policy
`EpisodicStore.search()` and `retrieve_macro_context()` are updated with an explicit filter:
```sql
WHERE e.is_archived = 0
```
This guarantees that archived/superseded episodes never bloat prompt context windows.

---

## 4. Consolidation Engine Specification (`eidos/memory/consolidator.py`)

### 4.1. Core Class Interface
```python
class ConsolidationReport(BaseModel):
    clusters_found: int = 0
    episodes_evaluated: int = 0
    episodes_archived: int = 0
    canonical_synthesized: int = 0
    promoted_rules_proposed: List[str] = Field(default_factory=list)
    promoted_skills_proposed: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

class EpisodicConsolidator:
    """Offline background consolidation engine for episodic trajectories."""

    def __init__(
        self,
        episodic_store: EpisodicStore,
        user_store: Optional[UserStore] = None,
        similarity_threshold: float = 0.85,
        min_cluster_size: int = 2,
    ):
        ...

    def run_consolidation(self, force: bool = False) -> ConsolidationReport:
        """Execute full consolidation cycle over unarchived episodes."""
        ...

    def cluster_episodes(self) -> Dict[str, List[EpisodicSearchResult]]:
        """Group episodes into clusters using agglomerative cosine similarity."""
        ...

    def synthesize_canonical_episode(
        self,
        cluster_id: str,
        episodes: List[EpisodicSearchResult],
    ) -> EpisodeRecord:
        """Synthesize a single consolidated golden episode from multiple runs."""
        ...

    def detect_promotion_candidates(
        self,
        clusters: Dict[str, List[EpisodicSearchResult]],
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Identify high-frequency patterns suitable for user profile rules or SKILL.md."""
        ...
```

### 4.2. Synthesis Rules (Without LLM Dependency)
To keep consolidation lightweight and completely runnable on low-power background CPU:
1. **Action Path Selection:** Pick the shortest trajectory among the successful runs that encountered zero errors.
2. **Pitfall Aggregation:** Collect all unique dead ends from all runs in the cluster:
   $$\text{Combined Pitfalls} = \bigcup_{i} \text{Pitfalls}(E_i)$$
3. **Critic Calibration:** Assign the canonical record a composite score:
   $$\text{Score}_{canonical} = \min\left(1.0, \frac{1}{|C|} \sum_{E \in C} \text{CriticScore}(E) + 0.10\right)$$
4. **Archival:** Set `is_archived = 1` and `superseded_by = canonical_id` on all individual episodes in the cluster.

---

## 5. Promotion Mechanics (Tier B & Skills)

When a consolidated cluster has $\ge 3$ occurrences with consistent outcomes:
1. **Tier B Promotion Candidate:**
   - If the lesson represents a behavioral constraint (e.g. *"Confirm before deleting files outside ~/Downloads"* or *"Prefer ripgrep over grep for code search"*), propose it as an atomic rule.
   - Pushed to `UserStore` with `status: "pending_review"` or displayed in the Web HUD settings modal under *"Learned Habits"* for 1-click user approval.
2. **Skill Promotion Candidate:**
   - If the task represents a reusable multi-step workflow (e.g., *"Convert SVG to ICO at 4 resolutions"*), scaffold a draft `SKILL.md` under `~/.eidos/skills/drafts/<name>/SKILL.md`.

---

## 6. Resource Limits & Safety Guards

- **Strict User Concurrency Guard:** `run_consolidation()` checks `agent_loop.is_busy()`. If the user submits a prompt, consolidation yields immediately within 50 ms.
- **Execution Budget:** Capped at a maximum runtime of 30 seconds per run; limits processing to at most 100 episodes per cycle to prevent high disk I/O.
- **Non-Destructive Archival:** No raw trajectory data is ever deleted from `episodes.jsonl`. Archival is strictly a metadata flag inside SQLite.

---

## 7. Test Strategy & Verification

- **Unit Tests (`tests/test_consolidation.py`):**
  - Verify clustering on 5 synthetic episodes (3 duplicate git tasks, 2 distinct audio tasks).
  - Verify that `is_archived = 1` correctly removes superseded records from `EpisodicStore.search()`.
  - Verify canonical synthesis combines unique pitfalls into the golden record.
  - Verify interruption guard: ensure consolidation stops immediately when task flag fires.
