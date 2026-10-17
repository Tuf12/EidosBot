# Implementation Plan: Entity Linking & Relational Knowledge Graph

**Specification:** [`docs/specs/SPEC_EPISODIC_ENTITY_GRAPH.md`](SPEC_EPISODIC_ENTITY_GRAPH.md)  
**Target Modules:** `eidos/memory/types.py`, `eidos/memory/entity_extractor.py`, `eidos/memory/graph_store.py`, `eidos/memory/episodic_store.py`  
**Status:** Ready for Implementation  
**Estimated Complexity:** Medium (Relational SQLite schema extensions, deterministic parsing, and hybrid re-ranking)

---

## 1. Objectives & Architectural Guardrails

### Primary Goal
Implement an embedded, zero-external-daemon **Relational Entity Knowledge Graph** inside `~/.eidos/memory/vector_store/episodic.db` with deterministic sub-millisecond entity extraction and **Hybrid Graph-Augmented Retrieval (GAR)**. This bridges the gap between fuzzy vector similarity (intent matching) and exact entity precision (commands, file paths, hardware displays, project boundaries).

### Non-Negotiable Guardrails
1. **Zero External Graph Daemons:** Must not require external servers (no Neo4j, RedisGraph, or separate network services). All graph tables live natively inside the existing SQLite `episodic.db`.
2. **Sub-Millisecond Deterministic Extraction:** Entity extraction from tool calls, observations, and environment snapshots must be 100% deterministic (structural AST and regex), consuming **zero LLM tokens** and taking $< 2\text{ ms}$ per episode.
3. **Non-Breaking Database Migration:** Fully backwards-compatible with existing `episodic.db` files via `CREATE TABLE IF NOT EXISTS` and safe foreign key constraints (`PRAGMA foreign_keys = ON;`).
4. **Zero Regression for Non-Entity Queries:** If a user or recall query contains no discernible entities, the retrieval engine defaults cleanly to $\lambda = 0.0$, behaving identically to pure vector search.
5. **Thread-Safe Relational Transactions:** All graph operations must execute safely under `EpisodicStore._lock`.

---

## 2. File Modification & Creation Inventory

| File | Change Type | Responsibility |
|---|---|---|
| `eidos/memory/types.py` | Modification | Add `ExtractedEntity` Pydantic model and entity taxonomy definitions. |
| `eidos/memory/entity_extractor.py` | **New File** | Deterministic extractor for commands, file paths, projects, hardware devices, windows, and web domains from trajectories and query strings. |
| `eidos/memory/graph_store.py` | **New File** | SQLite relational graph manager: schema creation, entity catalog upserts, episode-entity linkings, co-occurrence edge calculations, and relational queries. |
| `eidos/memory/episodic_store.py` | Modification | Integrate `GraphStore` and `EntityExtractor`, enable foreign keys, auto-link entities during `index_episode()`, support backfill, and implement hybrid scoring in `search()`. |
| `eidos/memory/__init__.py` | Modification | Export `ExtractedEntity`, `EntityExtractor`, and `GraphStore`. |
| `eidos/memory/recall_stream.py` | Modification | Support entity-prefixed queries (e.g. `project:OptimalX audio`) during mid-stream active recall. |
| `tests/test_entity_graph.py` | **New File** | Comprehensive test suite covering entity parsing, graph storage, co-occurrences, cascade deletions, and hybrid retrieval ranking. |

---

## 3. Detailed Phase-by-Phase Implementation Plan

### Phase 1: Data Contracts & Entity Taxonomy (`eidos/memory/types.py`)

1. **Entity Categories**:
   - `command`: Base binary/utility invoked via `execute_shell` (e.g., `xrandr`, `pactl`, `pytest`, `git`, `docker`).
   - `path`: File system paths referenced in tools or shell commands (e.g., `/etc/pulse/default.pa`, `~/.eidos/config.yaml`).
   - `project`: Root workspace or Git repository names (e.g., `OptimalX`, `EidosBot`).
   - `device`: Hardware output displays (`HDMI-0`, `DP-1`) or audio sinks/sources (`alsa_output.*`).
   - `window`: Active window class from desktop snapshots (`Google-chrome`, `Code`, `Gnome-terminal`).
   - `domain`: Network web domains from URLs (`github.com`, `docs.python.org`).

2. **Define `ExtractedEntity` Pydantic Schema**:
   ```python
   class ExtractedEntity(BaseModel):
       """Structured entity extracted from trajectory actions or queries."""
       entity_id: str               # e.g., "cmd:pactl", "path:/etc/fstab", "device:HDMI-0"
       entity_type: str             # "command", "path", "project", "device", "window", "domain"
       name: str                    # Canonical representation (e.g. "/etc/fstab")
       normalized_name: str         # Lowercased, stripped for indexing
       role: str = ""               # "invoked_command", "target_file", "active_window", etc.
       metadata: Dict[str, Any] = Field(default_factory=dict)
   ```

---

### Phase 2: Deterministic Entity Extractor (`eidos/memory/entity_extractor.py`)

1. **Class Design**:
   ```python
   class EntityExtractor:
       """High-speed deterministic extractor for commands, paths, hardware devices, and domains."""
       def extract_from_episode(self, record: EpisodeRecord) -> List[ExtractedEntity]: ...
       def extract_from_query(self, query: str) -> List[ExtractedEntity]: ...
   ```

2. **Extraction Mechanisms**:
   - **Command Parser**:
     - Strips wrapper commands: `sudo`, `nohup`, `env`, `nice`, `bash -c`, `sh -c`.
     - Strips environment variable prefixes: `FOO=bar cmd arg`.
     - Splits command pipelines (`|`, `&&`, `;`) and isolates executable binaries.
     - Ignores shell builtins/keywords (`if`, `then`, `done`, `echo`, `export`).
   - **Path Parser**:
     - Extracts explicit `path` arguments from `write_file`, `edit_file`, `read_file`, `download_file`, `share_file`.
     - Uses regex on shell commands matching absolute paths (`/[a-zA-Z0-9_\-./]+`), tilde paths (`~/[a-zA-Z0-9_\-./]+`), and files with common extensions (`.py`, `.json`, `.yaml`, `.ts`, `.sh`, `.conf`).
   - **Device Parser**:
     - Matches display identifiers: `\b(HDMI-[0-9]+|DP-[0-9]+|eDP-[0-9]+|VGA-[0-9]+)\b`.
     - Matches PulseAudio/ALSA sink/source patterns: `\b(alsa_output\.[a-zA-Z0-9_\-.]+|bluez_sink\.[a-zA-Z0-9_\-.]+)\b`.
   - **Window & Project Parser**:
     - Extracts `active_window_class` from `step.state_snapshot`.
     - Extracts project roots from `cwd` or path prefixes (e.g. `/CursorProjects/<project_name>`).
   - **Domain Parser**:
     - Extracts hostname from HTTP/HTTPS URLs in tool arguments or command strings.

3. **Query Extraction (`extract_from_query`)**:
   - Parses explicit tags if present: `project:OptimalX`, `cmd:pactl`, `path:/etc/fstab`, `device:HDMI-0`.
   - Runs lightweight regex to detect paths (`/` or `~`), known commands, and hardware tokens within standard conversational query text.

---

### Phase 3: Relational SQLite Graph Store (`eidos/memory/graph_store.py`)

1. **Schema Initialization (`init_schema`)**:
   Execute table creation on the shared SQLite connection:
   ```sql
   CREATE TABLE IF NOT EXISTS entities (
       entity_id TEXT PRIMARY KEY,
       entity_type TEXT NOT NULL,
       name TEXT NOT NULL,
       normalized_name TEXT NOT NULL,
       metadata_json TEXT,
       mention_count INTEGER DEFAULT 1,
       first_seen TEXT NOT NULL,
       last_seen TEXT NOT NULL
   );
   CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
   CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name);

   CREATE TABLE IF NOT EXISTS episode_entities (
       episode_id TEXT NOT NULL,
       entity_id TEXT NOT NULL,
       role TEXT NOT NULL,
       frequency INTEGER DEFAULT 1,
       PRIMARY KEY (episode_id, entity_id, role),
       FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
       FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
   );
   CREATE INDEX IF NOT EXISTS idx_ep_entities_entity ON episode_entities(entity_id);
   CREATE INDEX IF NOT EXISTS idx_ep_entities_episode ON episode_entities(episode_id);

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

2. **Core Operations**:
   - `link_episode_entities(episode_id: str, entities: List[ExtractedEntity], timestamp: str) -> None`:
     - Upserts each entity into `entities` (`mention_count = mention_count + 1`, updates `last_seen`).
     - Upserts into `episode_entities` with `frequency`.
     - Generates all unique pairs $(E_a, E_b)$ where $E_a < E_b$ and upserts `entity_cooccurrences` (`weight = weight + 1`).
   - `find_episodes_by_entity(entity_id_or_name: str, entity_type: Optional[str] = None, limit: int = 20) -> List[str]`:
     - Returns episode IDs directly linked to the target entity.
   - `find_episodes_by_entities(entities: List[ExtractedEntity], limit: int = 50) -> Dict[str, float]`:
     - Queries `episode_entities` across all query entities.
     - Computes overlap ratio for each matching episode:
       $$\text{Overlap}(ep) = \frac{|\text{Matched Entities}|}{|\text{Query Entities}|}$$
   - `get_entities_for_episode(episode_id: str) -> List[ExtractedEntity]`:
     - Retrieves all linked entities for a given episode.
   - `get_related_entities(entity_id: str, top_k: int = 5) -> List[Dict[str, Any]]`:
     - Queries `entity_cooccurrences` where $E = E_a \lor E = E_b$, sorted by `weight DESC`.

---

### Phase 4: Hybrid Graph-Augmented Retrieval (`eidos/memory/episodic_store.py`)

1. **Initialization & Lifecycle**:
   - Enable SQLite foreign key support: `self._conn.execute("PRAGMA foreign_keys = ON;")`.
   - Instantiate `self.graph_store = GraphStore(self._conn, self._lock)`.
   - Instantiate `self.entity_extractor = EntityExtractor()`.
   - Call `self.graph_store.init_schema()` in `_init_database()`.

2. **Indexing Hook**:
   - In `index_episode(record: EpisodeRecord, overwrite: bool = True)`:
     - After relational and vector indexing:
       ```python
       entities = self.entity_extractor.extract_from_episode(record)
       if entities:
           self.graph_store.link_episode_entities(record.episode_id, entities, timestamp=record.timestamp)
       ```
   - In `delete_episode(episode_id: str)`:
     - SQLite cascade cleanly removes `episode_entities` entries.

3. **Trajectory Sync Backfill**:
   - In `sync_from_trajectory_file(...)`, automatically extract and link entities as records are indexed.
   - Provide `backfill_entities() -> int` to parse existing episodes in the database and link missing entities.

4. **Hybrid Search Pipeline (`search`)**:
   - Extract entities from query: `query_entities = self.entity_extractor.extract_from_query(query)`.
   - **Branch A: No entities detected ($|E_{query}| = 0$):**
     - Pure vector search ($\lambda = 0.0$), maintaining 100% existing behavior and performance.
   - **Branch B: Entities detected ($|E_{query}| > 0$):**
     - Query `graph_store.find_episodes_by_entities(query_entities)` to retrieve candidate episodes with entity overlap scores $S_{graph} \in (0, 1]$.
     - Retrieve standard top vector candidates (via `sqlite-vec` or numpy fallback).
     - Merge candidate sets: ensure episodes with high entity overlap are included in the evaluation pool even if their initial vector rank was outside `top_k`.
     - Calculate composite score for each candidate:
       $$S_{composite} = (1 - \lambda) \cdot S_{vec} + \lambda \cdot S_{graph}$$
       *(Default $\lambda = 0.35$)*.
     - Filter by `min_similarity` and `outcome_filter`.
     - Sort by $S_{composite}$ descending and return top $k$ `EpisodicSearchResult` objects.

---

### Phase 5: Query Enhancement in Recall Stream (`eidos/memory/recall_stream.py`)

1. **Active Recall Integration**:
   - Because `lookup_memory(query)` delegates directly to `self.episodic_store.search(cleaned_query)`, it inherits Hybrid GAR automatically.
   - Update `lookup_memory` documentation and query sanitization to recognize prefix filters (e.g., `<recall>project:OptimalX audio setup</recall>` or `<recall>cmd:xrandr display brightness</recall>`).

---

### Phase 6: Verification & Test Suite (`tests/test_entity_graph.py`)

Write a comprehensive, standalone unit test suite running against an in-memory/tempfile SQLite instance (respecting the rule against running full-app tests):

1. `test_entity_extractor_commands()`: Verifies stripping wrappers (`sudo`, `bash -c`, env vars) and isolating commands (`pactl`, `git`, `xrandr`).
2. `test_entity_extractor_paths_and_devices()`: Tests extraction of file paths (`/etc/pulse/...`, `~/Downloads/...`) and display outputs (`HDMI-0`, `DP-1`).
3. `test_graph_store_linking_and_queries()`: Links synthetic entities, verifies `find_episodes_by_entity()` and `get_entities_for_episode()`.
4. `test_entity_cooccurrences()`: Links episodes with recurring pairs (`cmd:pactl` + `device:HDMI-0`), verifies co-occurrence edge weight increments.
5. `test_cascade_deletion()`: Deletes an episode from `EpisodicStore` and verifies foreign key cascade cleanly cleans `episode_entities`.
6. `test_hybrid_retrieval_ranking_advantage()`:
   - Episode 1: Vague task description ("Configure audio"), but exact command `pactl set-sink-volume HDMI-0 50%`.
   - Episode 2: Generic description mentioning "audio settings", but no HDMI-0 device.
   - Query: `"How do we configure volume on HDMI-0?"`
   - Verifies Hybrid GAR ranks Episode 1 first due to entity overlap boost.
7. `test_zero_entity_query_fallback()`: Verifies query with zero entities matches standard vector search behavior.

---

## 4. Rollout Strategy & Step-by-Step Task Checklist

- [x] **Step 1: Schemas (`eidos/memory/types.py`)**
  - Add `ExtractedEntity` model and entity type constants.
- [x] **Step 2: Deterministic Entity Extractor (`eidos/memory/entity_extractor.py`)**
  - Implement command, path, device, project, window, and domain parsers.
  - Implement query entity extractor with explicit prefix support (`cmd:`, `path:`, `project:`).
- [x] **Step 3: Relational Graph Store (`eidos/memory/graph_store.py`)**
  - Implement SQLite table initialization (`entities`, `episode_entities`, `entity_cooccurrences`).
  - Implement `link_episode_entities()`, co-occurrence weighting, and entity lookup queries.
- [x] **Step 4: Episodic Store Integration (`eidos/memory/episodic_store.py`)**
  - Wire `GraphStore` and `EntityExtractor` into `EpisodicStore`.
  - Enable `PRAGMA foreign_keys = ON;`.
  - Update `index_episode()`, `sync_from_trajectory_file()`, and `delete_episode()`.
  - Implement hybrid scoring and candidate merging in `search()`.
  - Add `backfill_entities()` utility.
- [x] **Step 5: Export & Subsystem Wiring (`eidos/memory/__init__.py`)**
  - Export new classes in `__init__.py`.
- [x] **Step 6: Recall Stream Integration (`eidos/memory/recall_stream.py`)**
  - Ensure prefix queries and entity recall work seamlessly.
- [x] **Step 7: Unit Testing & Verification (`tests/test_entity_graph.py`)**
  - Run focused unit tests with pytest; verify zero regressions in `tests/test_episodic_store.py`.
