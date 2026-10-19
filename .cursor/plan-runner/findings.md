# Plan run findings

Plan: docs/plans/PLAN_EPISODIC_ENTITY_GRAPH.md
Started: 2026-10-16T13:35:00Z

## Phase 1: Phase 1: Data Contracts & Entity Taxonomy (`eidos/memory/types.py`)

### Discrepancies

- **`types.py` changes exceed Phase 1 scope:** The same diff adds `ReflectionEvaluation`, extends `EpisodeReflection` (`goal_fulfilled`, `recommended_rules`), and adds `EpisodicSearchResult.matched_entities`—belonging to episodic reflector / hybrid search phases, not the entity taxonomy step.
- **`VALID_ENTITY_TYPES` is unused:** Step 1 calls for entity type constants; `ENTITY_TYPE_*` and `VALID_ENTITY_TYPES` are defined in `eidos/memory/types.py` but nothing in the repo references `VALID_ENTITY_TYPES`, and `ExtractedEntity.entity_type` is an unconstrained `str` (no `Literal` / validator).
- **Taxonomy is constants-only:** Phase 1 lists category semantics and examples in the plan prose; implementation encodes the six types as string constants only (no enum, module docstring, or runtime taxonomy registry beyond that).

### User checks (deferred)

None.

## Phase 2: Phase 2: Deterministic Entity Extractor (`eidos/memory/entity_extractor.py`)

### Discrepancies

- **`bash -c` / `sh -c` stripping incomplete:** The plan treats these as wrappers to unwrap before isolating binaries. `_extract_commands_from_shell` recurses into the inner script but still tokenizes the outer segment and can record `bash`/`sh`/`zsh` as `invoked_command` because they are not in `WRAPPER_COMMANDS` (only `sudo`, `nohup`, `env`, `nice`, etc. are).
- **At most one command per shell segment:** After the first accepted binary in a segment, parsing stops (`break`). Chained utilities in one segment without `|`, `&&`, `;`, or `||` (e.g. `git commit`) only yield the leading binary; the plan’s wording suggests isolating all pipeline/chained executables (splitting handles pipes/chains, not multi-token simple commands).
- **Device regex broader than Phase 2 spec:** Plan lists `HDMI-[0-9]+`, `DP-[0-9]+`, `eDP-[0-9]+`, `VGA-[0-9]+`, and `alsa_output.*` / `bluez_sink.*`. `DEVICE_PATTERN` also matches `DVI`, hyphenless connector forms, `alsa_input`, `bluez_source`, and tokens like `auto_null`, `pulse`, and `pipewire`.
- **Absolute path regex stricter than plan:** Plan’s `/[a-zA-Z0-9_\-./]+` can match short absolute paths (e.g. `/etc`). `ABSOLUTE_PATH_PATTERN` requires at least two slash-separated components, so single-component absolutes are not picked up from free text (tool `path` args are still recorded directly).
- **Tool observation scope narrower than full episode text scan:** `extract_from_episode` runs paths/commands/domains on shell strings and tool args, but `tool_observations` stdout/stderr are scanned only for devices and domains—not paths or commands that appear only in command output.
- **Implicit query commands are allowlist-based:** `extract_from_query` infers commands only when tokens appear in `COMMON_SYSTEM_COMMANDS`, not from arbitrary shell-like tokens in conversational text (plan’s “known commands” is satisfied but arbitrary binaries in queries are missed).
- **Phase 2 deliverable is untracked:** `eidos/memory/entity_extractor.py` exists on disk but is not in `HEAD` (new/uncommitted file); wiring and tests reference it from other modified files.

### User checks (deferred)

None.

## Phase 3: Phase 3: Relational SQLite Graph Store (`eidos/memory/graph_store.py`)

### Discrepancies

- **Phase 3 deliverable is untracked:** `eidos/memory/graph_store.py` exists on disk but is not in `HEAD` (new/uncommitted file), same as Phase 2’s extractor.
- **`find_episodes_by_entities` return type vs plan:** Plan specifies `Dict[str, float]` (episode → overlap score). Implementation returns `Dict[str, Tuple[float, List[str]]]` (score plus matched entity IDs), which `EpisodicStore.search` already depends on for hybrid ranking metadata.
- **`limit` on `find_episodes_by_entities` is unused:** Signature includes `limit: int = 50` per the plan, but the method never applies it when building or trimming results.
- **Overlap denominator uses `entity_id` set size, not full query list:** `num_targets = len({e.entity_id for e in entities})` collapses duplicate IDs; the plan’s “query entities” count could differ if the caller passes repeated or role-split rows for the same ID.
- **Overlap numerator can cap at 1.0 via distinct episode entity rows:** Matches are distinct `ee.entity_id` values from SQL (`entity_id IN (...)` OR `normalized_name IN (...)`). Multiple catalog rows sharing a query `normalized_name` can inflate `len(matched)` before `min(1.0, …)`, yielding full overlap from a single query entity (edge case).
- **`mention_count` increments by metadata `frequency`, not strictly +1:** `ON CONFLICT` adds `excluded.mention_count`, seeded from `entity.metadata.get("frequency", 1)` on insert—aligned when frequency is always 1, but not identical to the plan’s “`mention_count + 1`” wording when frequency > 1.
- **`episode_entities` conflict replaces frequency:** `ON CONFLICT … DO UPDATE SET frequency = excluded.frequency` overwrites rather than accumulating mentions across re-links; re-indexing the same episode without clearing links can still bump `entities.mention_count` and co-occurrence `weight` again (graph store does not reset per-episode links).
- **Schema extras beyond plan SQL:** `init_schema` adds `idx_cooccurrences_a` / `idx_cooccurrences_b` on `entity_cooccurrences` (not in the plan snippet); harmless for behavior.
- **Extra APIs not listed in Phase 3:** `delete_episode_links`, `count_entities`, and `count_links` are implemented for tests/utilities but not named in the plan’s core operations list.
- **`find_episodes_by_entity` normalizes lookup input:** Both `entity_id` and `normalized_name` comparisons use `.strip().lower()`, which is fine for canonical lowercase IDs but differs from exact `entity_id` matching if IDs were ever stored with mixed case.

### User checks (deferred)

None.

## Phase 4: Phase 4: Hybrid Graph-Augmented Retrieval (`eidos/memory/episodic_store.py`)

### Discrepancies

- **Out-of-phase changes in the same diff:** `update_episode_reflection()` plus `EpisodeReflection` / `ReflectionEvaluation` imports belong to episodic reflector work, not Phase 4 hybrid GAR.
- **`threading.Lock` → `RLock`:** Not called out in Phase 4; required for reentrant `graph_store` calls under the same store lock during `index_episode` / linking.
- **`graph_store.init_schema()` runs before `episodes` table creation:** Phase 4 only says to call it in `_init_database()`; order differs from a literal “episodes first, then graph” reading (SQLite allows child FK DDL before the parent table exists).
- **Hybrid λ skipped when graph lookup is empty:** If `extract_from_query` finds entities but `find_episodes_by_entities` returns `{}`, `graph_matches` is falsy and search uses pure vector scores (`composite_sim = vec_sim`). Branch B in the plan still applies \((1-\lambda) S_{vec} + \lambda S_{graph}\) with \(S_{graph}=0\), which would scale all vector scores by \((1-\lambda)\) and can change ordering vs legacy search.
- **Re-index does not reset graph links:** `index_episode(..., overwrite=True)` always calls `link_episode_entities` without clearing prior `episode_entities` rows for that episode; combined with graph-store upsert semantics this can inflate `entities.mention_count` and co-occurrence weights on re-index (Phase 3 behavior, surfaced by the Phase 4 indexing hook).
- **`backfill_entities()` scope is “no rows in `episode_entities`”, not “missing entities”:** SQL uses `LEFT JOIN … WHERE ee.episode_id IS NULL`; episodes with partial links or zero extracted entities are skipped (zero-entity episodes stay unlinked and are re-selected on every backfill run).
- **`backfill_entities()` requires `full_record_json`:** Episodes without stored JSON are not backfilled; the plan does not state this prerequisite.
- **Candidate merge for graph-only episodes is sqlite-vec path only:** `_search_sqlite_vec` fetches extra graph-matched IDs and recomputes vector similarity from `embedding_blob`; numpy fallback already scans all episodes, so behavior diverges between backends only in implementation strategy (not plan text).
- **`find_episodes_by_entities(..., limit=50)` unused:** All graph-matched episode IDs can be merged into the sqlite-vec pool with no cap (inherits Phase 3 gap).
- **`clear()` extended beyond Phase 4:** Explicit `DELETE FROM entities` and `entity_cooccurrences` after purging episodes; plan Phase 4 does not describe `clear()` behavior.
- **`delete_episode`:** Relies on FK cascade for `episode_entities` only (no `graph_store` call); orphaned rows in `entities` / co-occurrence edges can remain—consistent with plan wording for join cleanup, not full graph GC.
- **`EpisodicSearchResult.similarity` reports composite score** when hybrid is active (and populates `matched_entities`); plan describes composite ranking but does not document the API field semantics change.
- **Unused import:** `ExtractedEntity` is imported in `episodic_store.py` but not referenced.
- **Indexing hook swallows graph errors:** Entity linking failures are logged and do not fail `index_episode`; plan does not specify failure policy.
- **Defensive `hasattr(self, "graph_store")` guards** on index/backfill paths; `graph_store` is always set in `_init_database()` on success.

### User checks (deferred)

None.

## Phase 5: Phase 5: Query Enhancement in Recall Stream (`eidos/memory/recall_stream.py`)

### Discrepancies

- **Scope beyond Phase 5 text:** `format_recalled_memory` adds a `- Matched Entities:` line (with `getattr(result, "matched_entities", None)`); the plan only calls for `lookup_memory` doc and sanitization, not spliced recall output changes.
- **`lookup_memory` doc vs behavior:** Docstring still leads with “semantic search” while hybrid GAR is delegated via `episodic_store.search()` when entities are present (later sentences mention GAR).
- **Sanitization is wrapper-only:** Quote/backtick stripping in `lookup_memory` and `extract_recall_query` preserves entity prefixes but does not parse or validate them in recall_stream (prefix handling is entirely downstream in `EntityExtractor` / `search`, which matches delegation but is looser than “recognize prefix filters” read as recall-layer logic).
- **Recall tests are mock-only and narrow:** `test_recall_stream_with_entity_graph_prefixes_and_formatting` asserts `search` is called with a sanitized `cmd:` query and formatting; it does not cover the plan’s `project:OptimalX audio` example, unquoted `<recall>…</recall>` prefix queries, or end-to-end hybrid ranking through the interceptor (Phase 6 / `test_entity_graph.py` covers store search, not recall wiring).
- **`lookup_optimalx_notes` unchanged:** No matching quote/backtick sanitization if called with a quoted query outside the `extract_recall_query` path (streaming recall uses already-sanitized `query` from extraction, so mid-stream path is fine).

### Resolution (2026-10-19)

- Added shared `sanitize_recall_query()` used by `lookup_memory`, `lookup_optimalx_notes`, and `extract_recall_query`; GAR docstring updated on `lookup_memory`.
- Plan Phase 5 updated to document sanitization, OptimalX parity, and optional `matched_entities` in `format_recalled_memory`.
- Tests: `test_sanitize_recall_query`, `project:OptimalX` extract/lookup cases, OptimalX sanitization, `test_execute_chat_recall_with_project_prefix` (real store + interceptor).

### User checks (deferred)

None.
