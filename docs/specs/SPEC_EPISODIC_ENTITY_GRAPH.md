# Specification: Entity Linking & Semantic Knowledge Graph for Episodic Memory

**Document:** `docs/specs/SPEC_EPISODIC_ENTITY_GRAPH.md`  
**Status:** Draft / Specification  
**Component:** `eidos/memory/graph_store.py`, `eidos/memory/entity_extractor.py`  
**Related Systems:** `eidos/memory/episodic_store.py`, `eidos/memory/recall_stream.py`, `eidos/integrations/optimalx.py`  
**Resource policy:** [`SPEC_COMPUTE_RESOURCE_POLICY.md`](SPEC_COMPUTE_RESOURCE_POLICY.md) — no `embed_text` during an open Ollama chat stream; macro context uses one embed per task.

---

## 1. Executive Summary & Problem Statement

### 1.1. Problem Statement
Vector similarity search (dense embeddings via `nomic-embed-text`) is effective for fuzzy semantic intent (e.g., matching "dim display" to "lower screen brightness"). However, it suffers from two major limitations in an OS desktop assistant:
1. **Entity Blindness (Keyword & Path Fragility):** Dense embeddings often dilute exact keyword signals. If a user asks *"How did we set up the sink on HDMI-0?"*, vector similarity against task goals might score higher on general audio discussions while missing a specific past episode titled *"Switch default PulseAudio sink"*, because the critical entity `HDMI-0` was diluted in the vector space.
2. **Lack of Relational Traversal:** There is no mechanism to traverse connections across tasks. For example:
   - What other tasks touched `/home/tuf/CursorProjects/OptimalX`?
   - What commands are consistently associated with `Display: HDMI-0`?
   - What errors occur repeatedly when using `docker-compose` in workspace $X$?

### 1.2. Proposed Solution
Build an embedded **Relational Entity Knowledge Graph** directly within the existing SQLite database (`~/.eidos/memory/vector_store/episodic.db`), requiring **zero external graph databases** (e.g. Neo4j, RedisGraph).
- **Deterministic & High-Signal Entity Extraction:** Automatically extract entities (paths, binaries, project names, display outputs, web domains) from `tool_calls`, `tool_observations`, and task goals.
- **Bi-Directional Graph Storage:** Maintain SQLite relational graph tables linking `episodes <-> entities` and `entities <-> entities`.
- **Hybrid Graph-Augmented Retrieval (GAR):** When querying memory (macro or active `<recall>`), perform hybrid lookup combining dense vector cosine similarity with exact entity-overlap boosting.

---

## 2. Architecture & Graph Data Flow

```
                      [ Completed Episode Record ]
                                   │
                                   ▼
                   [ EntityExtractor.extract() ]
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     [ Path Entities ]    [ Command Entities ]  [ Hardware/Project ]
      ~/Downloads,        pactl, xrandr, git,   HDMI-0, Chromium,
      /etc/pulse/...       ffmpeg, docker        OptimalX, EidosBot
              │                    │                    │
              └────────────────────┬────────────────────┘
                                   ▼
                   [ GraphStore.link_episode() ]
             (Populates relational SQLite graph tables)
                                   │
                                   ▼
                 ~/.eidos/memory/vector_store/episodic.db
             ┌──────────────────────────────────────────────┐
             │  • episodes (relational metadata)            │
             │  • vec_episodes (sqlite-vec virtual table)   │
             │  • entities (entity catalog)                 │
             │  • episode_entities (occurrence links)       │
             │  • entity_cooccurrences (relation edges)     │
             └──────────────────────────────────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼                                             ▼
  [ Vector Nearest Neighbor ]                  [ Entity Traversal Query ]
  Find semantic similarity                    Find episodes with matching
  (task goal intent)                          entities (paths, tools, devices)
            │                                             │
            └──────────────────────┬──────────────────────┘
                                   ▼
                    [ Hybrid Ranking & Fusion ]
                     Score = α·Vector + β·Entity
```

---

## 3. SQLite Relational Graph Schema

All graph data lives inside the existing `episodic.db` to maintain single-file simplicity and atomic transactions:

```sql
-- Catalog of unique extracted entities
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,          -- e.g. "path:/etc/pulse/default.pa", "cmd:xrandr"
    entity_type TEXT NOT NULL,          -- "command", "path", "project", "device", "domain"
    name TEXT NOT NULL,                 -- Canonical name
    normalized_name TEXT NOT NULL,      -- Lowercased, stripped for indexing
    metadata_json TEXT,                 -- Arbitrary attributes (e.g. file extension, binary path)
    mention_count INTEGER DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name);

-- Join table linking episodes to entities
CREATE TABLE IF NOT EXISTS episode_entities (
    episode_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL,                 -- "target_file", "invoked_command", "workspace_project", "active_window"
    frequency INTEGER DEFAULT 1,
    PRIMARY KEY (episode_id, entity_id, role),
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ep_entities_entity ON episode_entities(entity_id);

-- Entity-to-Entity co-occurrence graph (e.g., cmd:pactl frequently co-occurs with device:HDMI-0)
CREATE TABLE IF NOT EXISTS entity_cooccurrences (
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    weight INTEGER DEFAULT 1,
    last_observed TEXT NOT NULL,
    PRIMARY KEY (entity_a, entity_b),
    FOREIGN KEY (entity_a) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (entity_b) REFERENCES entities(entity_id) ON DELETE CASCADE
);
```

---

## 4. Entity Extraction Pipeline (`eidos/memory/entity_extractor.py`)

### 4.1. Entity Taxonomy
| Entity Type | Identification Mechanism | Examples |
|---|---|---|
| `command` | First token of `execute_shell` command line (after resolving shell wrappers/env) | `xrandr`, `pactl`, `ffmpeg`, `pytest`, `git` |
| `path` | File arguments in tool calls (`write_file`, `edit_file`, file regex in shell) | `/home/tuf/EidosWorkspace`, `~/.eidos/config.yaml` |
| `project` | Git root detection or top-level workspace directory names | `EidosBot`, `OptimalX`, `InventionPipeline` |
| `device` | Detected display identifiers, audio sinks, hardware interfaces | `HDMI-0`, `DP-1`, `alsa_output.pci` |
| `window` | Active window class name from step state snapshot | `Google-chrome`, `Gnome-terminal`, `Files` |
| `domain` | URLs in `web_fetch`, `web_search`, `download_file`, or browser actions | `github.com`, `docs.python.org` |

### 4.2. Extraction Performance
- Entity extraction is **100% deterministic regex and structural parsing**, requiring **zero LLM tokens or neural inference latency** (< 2 ms per episode).

```python
class ExtractedEntity(BaseModel):
    entity_id: str
    entity_type: str  # "command", "path", "project", "device", "window", "domain"
    name: str
    normalized_name: str
    role: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class EntityExtractor:
    """Fast deterministic extractor pulling entities from EpisodeRecord trajectories."""

    def extract_from_episode(self, record: EpisodeRecord) -> List[ExtractedEntity]:
        ...
```

---

## 5. Hybrid Retrieval: Graph-Augmented Recall

When `EpisodicStore.search()` is executed (or `<recall>` is intercepted):

### 5.1. Scoring Algorithm
1. **Vector Score ($S_{vec} \in [0, 1]$):** Cosine similarity between query embedding and episode embedding.
2. **Entity Overlap Score ($S_{graph} \in [0, 1]$):**
   $$S_{graph} = \frac{|E_{query} \cap E_{episode}|}{\max(|E_{query}|, 1)}$$
   where $E_{query}$ are entities extracted directly from the user's recall query string.
3. **Composite Score:**
   $$S_{composite} = (1 - \lambda) \cdot S_{vec} + \lambda \cdot S_{graph}$$
   *(Default $\lambda = 0.35$ when entity matches exist, $\lambda = 0.0$ when no entities are mentioned).*

**Old system.** Hybrid ranking is global. A shared entity returns the episode no matter which project it belongs to, and nothing in this spec stops that episode from standing in for the current project.

**Updated job concept.** Entity types gain `job:<job_id>` and `integration:<name>` so an episode records where it happened. The cross-job walk uses a shared real-world entity (an email address, a path, a device), not the job id itself. When the active scope has a `job_id`, a graph hit from another job is returned labeled with that other job and its integration. The caller does not load the other job's brief, notes, artifacts, or MCP tools. Vector-only matches from another job stay excluded, as in [SPEC_EPISODIC_DYNAMIC_SCOPING.md](SPEC_EPISODIC_DYNAMIC_SCOPING.md) and [PROJECT_JOBS.md](../PROJECT_JOBS.md) section 13.4.

### 5.2. Relational Query APIs (`eidos/memory/graph_store.py`)
```python
class GraphStore:
    """Embedded SQLite relational graph manager for episodic entities."""

    def link_episode_entities(self, episode_id: str, entities: List[ExtractedEntity]) -> None:
        """Persist entities and link to episode in SQLite."""
        ...

    def find_episodes_by_entity(
        self,
        entity_name: str,
        entity_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[str]:
        """Return episode IDs associated with a specific entity."""
        ...

    def get_related_entities(
        self,
        entity_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return entities that frequently co-occur with the target entity."""
        ...
```

---

## 6. Integration Points

1. **`eidos/memory/episodic_store.py`:**
   - In `_init_database()`: Execute graph table creation.
   - In `index_episode()`: Invoke `EntityExtractor` and call `GraphStore.link_episode_entities()`.
   - In `search()`: If query contains discernible entities (commands or paths), compute hybrid entity-augmented ranking.
2. **`eidos/memory/recall_stream.py`:**
   - Allow query strings like `<recall>project:OptimalX audio</recall>` or automatic path matching to look up graph nodes directly.

---

## 7. Test Strategy & Verification

- **Unit Tests (`tests/test_entity_graph.py`):**
  - Extraction accuracy: Shell commands, file paths, and display names extracted cleanly from trajectory steps.
  - SQLite foreign key constraints and cascade deletions when an episode is removed.
  - Hybrid search test: verify that a query with a specific path (`/etc/fstab`) ranks the exact episode first, even if semantic vector similarity alone is moderate.
  - Zero regression on existing sqlite-vec and numpy search modes.
