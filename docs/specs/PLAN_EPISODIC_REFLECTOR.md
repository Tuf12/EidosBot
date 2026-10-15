# Implementation Plan: Autonomous Post-Task Episodic Reflector & Critic

**Specification:** [`docs/specs/SPEC_EPISODIC_REFLECTOR.md`](SPEC_EPISODIC_REFLECTOR.md)  
**Target Module:** `eidos/memory/reflector.py`  
**Status:** Ready for Implementation  
**Estimated Complexity:** Medium (Self-contained module with background threading and schema extensions)

---

## 1. Objectives & Architectural Guardrails

### Primary Goal
Implement an asynchronous, post-task evaluation engine (`EpisodicReflector`) that audits agent trajectories, calculates continuous critic scores ($0.0 \dots 1.0$), verifies goal fulfillment against tool observations, extracts nuanced lessons and pitfalls, and updates both the trajectory log and episodic vector store.

### Non-Negotiable Guardrails
1. **Zero UI/Voice Latency:** The reflector must **never** block or delay user response delivery or Kokoro TTS playback. Evaluation runs entirely in a background daemon thread.
2. **Resource Throttling:** Must respect host resource constraints (preventing GPU thermal saturation or Cursor crashes). Queue is bounded (max 16 tasks), runs with yield delays, and gracefully falls back to deterministic heuristic evaluation when compute is busy or offline.
3. **Observation Context Truncation:** Shell outputs and tool observations must be safely truncated when constructing the evaluation prompt to cap context at ~3,500 tokens.
4. **Deterministic Heuristic Fallback:** If the LLM call times out, returns malformed JSON, or is disabled, the system computes rule-based reflection without throwing unhandled exceptions.

---

## 2. File Modification & Creation Inventory

| File | Change Type | Responsibility |
|---|---|---|
| `eidos/memory/types.py` | Modification | Extend `EpisodeReflection` and introduce `ReflectionEvaluation` Pydantic model. |
| `eidos/memory/trajectory_logger.py` | Modification | Add atomic `update_episode(record)` and `get_episode_by_id(episode_id)` methods. |
| `eidos/memory/episodic_store.py` | Modification | Add `update_episode_reflection()` helper for updating relational and vector records. |
| `eidos/memory/reflector.py` | **New File** | Complete implementation of `EpisodicReflector`, worker thread queue, LLM evaluator, and heuristic fallback. |
| `eidos/memory/__init__.py` | Modification | Export `EpisodicReflector` and `ReflectionEvaluation`. |
| `eidos/core/loop.py` | Modification | Initialize `self.reflector`, wire into `_complete_run()`, and handle graceful shutdown. |
| `config.example.yaml` | Modification | Add `memory.reflector` configuration block with sensible defaults. |
| `tests/test_reflector.py` | **New File** | Unit test suite covering evaluation parsing, heuristics, async queue, and re-indexing. |

---

## 3. Detailed Phase-by-Phase Implementation Plan

### Phase 1: Data Contracts & Schema Expansion (`eidos/memory/types.py`)

1. **Update `EpisodeReflection`**:
   - Add `goal_fulfilled: Optional[bool] = None`
   - Add `recommended_rules: List[str] = Field(default_factory=list)`
   - Retain backwards-compatibility for existing fields: `summary: str = ""`, `pitfalls_avoided: str = ""`, `lessons_learned: str = ""`.

2. **Define `ReflectionEvaluation`**:
   ```python
   class ReflectionEvaluation(BaseModel):
       goal_fulfilled: bool = Field(..., description="True if observations verify completion.")
       critic_score: float = Field(..., ge=0.0, le=1.0, description="Calibrated continuous score.")
       summary: str = Field(..., description="Concise synopsis of resolution.")
       pitfalls_avoided: str = Field(default="", description="Traps, dead ends, or avoided errors.")
       lessons_learned: str = Field(default="", description="Reusable heuristic for future sessions.")
       recommended_rules: List[str] = Field(default_factory=list, description="Candidate profile rules.")
   ```

---

### Phase 2: Trajectory Store Update Capabilities (`eidos/memory/trajectory_logger.py`)

1. **Implement `get_episode_by_id(episode_id: str) -> Optional[EpisodeRecord]`**:
   - Read `episodes.jsonl` from newest to oldest to fetch an episode by its identifier.

2. **Implement `update_episode(record: EpisodeRecord) -> bool`**:
   - Thread-safe update of an existing record in `~/.eidos/trajectories/episodes.jsonl`.
   - Reads existing lines, replaces the matching `episode_id` line with updated serialization, and writes atomically via a temporary file (`episodes.jsonl.tmp` -> replace) under `self._lock`.

---

### Phase 3: EpisodicStore Update Helpers (`eidos/memory/episodic_store.py`)

1. **Add `update_episode_reflection()`**:
   - Signature:
     ```python
     def update_episode_reflection(
         self,
         episode_id: str,
         reflection: EpisodeReflection,
         critic_score: float,
         goal_fulfilled: Optional[bool] = None,
     ) -> bool
     ```
   - Fetches the existing `EpisodeRecord` via `get_episode_record(episode_id)`.
   - Updates `record.reflection = reflection` and `record.metadata.critic_score = critic_score`.
   - If `goal_fulfilled is False` and `record.metadata.outcome == "success"`, updates `record.metadata.outcome = "failed_unverified"`.
   - Calls existing `self.index_episode(record, overwrite=True)`.

---

### Phase 4: Core Evaluator Engine (`eidos/memory/reflector.py`)

1. **Class Architecture**:
   - Constructor:
     ```python
     class EpisodicReflector:
         def __init__(
             self,
             model_client: Optional[BaseModelClient] = None,
             episodic_store: Optional[EpisodicStore] = None,
             trajectory_logger: Optional[TrajectoryLogger] = None,
             enabled: bool = True,
             async_workers: int = 1,
             max_queue_size: int = 16,
             model_name: Optional[str] = None,
             timeout_seconds: float = 30.0,
         ):
     ```
   - Holds an in-memory queue: `self._queue = queue.Queue(maxsize=max_queue_size)`.
   - Spawns a background worker thread `self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)`.

2. **Public API**:
   - `enqueue(record: EpisodeRecord) -> bool`: Thread-safe enqueue. If queue is full, logs warning and runs synchronous heuristic fallback without blocking.
   - `reflect_episode(record: EpisodeRecord, sync: bool = False) -> Optional[ReflectionEvaluation]`: Main evaluation entrypoint.
   - `start()` and `stop(timeout: float = 2.0)`: Worker thread lifecycle.
   - `wait_idle(timeout: float = 5.0)`: Helper for unit tests to wait for queued evaluations to finish.

3. **Prompt Construction & Observation Capping**:
   - Formats trajectory steps into a compact prompt string:
     - Captures step number, `model_thinking` (truncated to 200 chars), tool name and key arguments.
     - Captures tool observation exit code, stdout/stderr (capped at 400 chars per step to prevent context blowout).
   - Evaluator System Prompt uses the rubric defined in `docs/specs/SPEC_EPISODIC_REFLECTOR.md` Section 4.1.

4. **Robust Response Parsing**:
   - Extracts JSON substring using regex or `json.loads` if wrapped in markdown codeblocks.
   - Validates JSON into `ReflectionEvaluation`.
   - If JSON decoding or validation fails, logs debug message and triggers `_fallback_heuristic_eval()`.

5. **Deterministic Fallback Heuristic (`_fallback_heuristic_eval`)**:
   - Evaluates trajectory without any LLM call:
     - Inspects all tool observations: if any observation has `exit_code != 0`, `denied: True`, or `success: False`, and was never followed by a successful retry, checks whether the final step succeeded.
     - Calculates `critic_score`:
       - `1.0` if `record.metadata.outcome == "success"` and all steps had exit code 0.
       - `0.75` if `outcome == "success"` with 1-2 recovered dead ends.
       - `0.0` if `outcome in ("failed", "aborted")`.
       - `0.4` if outcome was `max_steps`.
     - Generates structured `ReflectionEvaluation` using existing `record.reflection` or `finish_task` summary.

6. **Applying Reflection**:
   - `_apply_reflection(episode_id, evaluation)`:
     - Updates `trajectory_logger.update_episode(record)`.
     - Calls `episodic_store.update_episode_reflection(...)`.
     - Emits logger confirmation: `"Reflected episode %s: score=%.2f, fulfilled=%s"`.

---

### Phase 5: AgentLoop & Configuration Wiring (`eidos/core/loop.py`, `config.example.yaml`)

1. **Configuration (`config.example.yaml`)**:
   ```yaml
   memory:
     enabled: true
     # ... existing settings ...
     reflector:
       enabled: true                 # Autonomous post-task critique
       async_eval: true              # Run evaluation in background daemon thread
       timeout_seconds: 30.0         # Evaluation LLM timeout
       model: ""                     # Optional specific model (defaults to router / ollama)
   ```

2. **`AgentLoop` Lifecycle Wiring**:
   - In `AgentLoop.__init__`:
     - Reads `mem_cfg.get("reflector", {})`.
     - Instantiates `EpisodicReflector` passing `self.model`, `self.episodic_store`, `self.trajectory_logger`.
     - Starts background worker if `enabled and async_eval`.
   - In `AgentLoop._complete_run()`:
     - `self._finish_and_index_episode(episode_id, ...)` persists the baseline record.
     - If `self.reflector and self.memory_enabled and rec`:
       - Calls `self.reflector.enqueue(rec)`.
     - Task finishes, TTS speaks, and web socket emits immediately without waiting for reflector.
   - In `AgentLoop.stop()` or cleanup:
     - Stops the reflector worker thread cleanly.

---

### Phase 6: Verification & Test Suite (`tests/test_reflector.py`)

Write thorough, fast-running unit tests:
1. `test_heuristic_evaluation_success()`: Tests deterministic evaluation on clean success trajectory.
2. `test_heuristic_evaluation_failure()`: Tests deterministic evaluation on failed trajectory with non-zero exit code.
3. `test_llm_evaluation_parsing()`: Mocks model client returning valid JSON, verifies `ReflectionEvaluation` fields.
4. `test_llm_evaluation_malformed_json_fallback()`: Mocks model client returning invalid text, verifies fallback engages cleanly.
5. `test_async_queue_and_episodic_store_update()`: Enqueues episode, waits idle, verifies `EpisodicStore` reflects updated `critic_score` and `lessons_learned`.
6. `test_trajectory_logger_update_episode()`: Verifies in-place update of `episodes.jsonl`.

---

## 4. Rollout Strategy & Step-by-Step Task Checklist

- [ ] **Step 1: Schemas (`eidos/memory/types.py`)**
  - Add `ReflectionEvaluation` model.
  - Extend `EpisodeReflection` model.
- [ ] **Step 2: Trajectory Logger Update (`eidos/memory/trajectory_logger.py`)**
  - Implement `update_episode()` and `get_episode_by_id()`.
- [ ] **Step 3: Episodic Store Updates (`eidos/memory/episodic_store.py`)**
  - Add `update_episode_reflection()` method.
- [ ] **Step 4: Reflector Implementation (`eidos/memory/reflector.py`)**
  - Implement `EpisodicReflector` with background queue, prompt builder, observation truncator, parser, heuristic fallback, and database persistence.
  - Export from `eidos/memory/__init__.py`.
- [ ] **Step 5: AgentLoop & Config Integration (`eidos/core/loop.py`, `config.example.yaml`)**
  - Wire reflector into `AgentLoop` initialization, completion hook, and shutdown.
- [ ] **Step 6: Unit Testing (`tests/test_reflector.py`)**
  - Write test suite; run via pytest (ensuring zero heavy back-to-back app instances).
- [ ] **Step 7: Verification**
  - Verify all unit tests pass with zero regressions.
