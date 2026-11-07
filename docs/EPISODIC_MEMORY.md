# EidosBot Episodic Memory & Trajectory Learning Architecture

This document defines the architectural design, data schemas, recall mechanics, and LoRA fine-tuning pipeline for **EidosBot's Episodic Memory and Experiential Learning System**.

---

## 1. Vision & Cognitive Philosophy

### 1.1. Overcoming the Limitations of Prompt-Driven Agents
Standard AI desktop agents suffer from three fundamental architectural flaws:
1. **Session Amnesia:** Every session restarts with a blank slate. If an agent tries an action that fails due to an environmental edge case (e.g., an X11 window focus bug or a missing flag), it repeats the identical mistake in the next session unless a human manually updates the system prompt.
2. **System Prompt Bloat:** As edge cases are discovered, maintainers append negative constraints (*"DO NOT do X", "ALWAYS do Y"*). Over time, prompts become long, expensive, and brittle, leading to instruction attenuation and hallucinations.
3. **Memory Contamination:** Systems that lump user identity, agent habits, and task logs into a single unstructured text summary quickly suffer from narrative drift ("the telephone game"), corrupting facts about both the agent and the user.

### 1.2. The Cognitive Parallel: Distributed Human Memory
Human beings do not wake up with a paragraph describing their identity printed on their consciousness. Under Endel Tulving's classic cognitive memory model, human cognition is distributed across four distinct memory systems:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        COGNITIVE MEMORY MODEL                          │
├─────────────────────────┬──────────────────────────────────────────────┤
│ Human Psychology        │ EidosBot System Architecture                 │
├─────────────────────────┼──────────────────────────────────────────────┤
│ 1. Procedural Memory    │ 🧠 LoRA Neural Weights (Subconscious Habits) │
│    (Motor skills, tool  │ Tool calling habits, reasoning syntax, tone, │
│    habits, grammar)     │ deterministic-first reflexes, and format.    │
├─────────────────────────┼──────────────────────────────────────────────┤
│ 2. Semantic Memory      │ 📋 Structured Fact Sheets & Profiles         │
│    (Known facts, rules, │ ~/.eidos/user_profile.yaml, tool schemas,    │
│    environment maps)    │ hardware specs, environmental constants.     │
├─────────────────────────┼──────────────────────────────────────────────┤
│ 3. Episodic Memory      │ 📚 Vector Store / Trajectory Experience      │
│    (Personal events,    │ Autobiographical memory of past tasks, plans,│
│    past triumphs/fails) │ specific thoughts, successes, and pitfalls.  │
├─────────────────────────┼──────────────────────────────────────────────┤
│ 4. Working Memory       │ ⏱️ Runtime Context Window                     │
│    (Immediate sensory   │ Clock, active window, current task goal,     │
│    focus & task trace)  │ and tool outputs for the ongoing cycle.      │
└─────────────────────────┴──────────────────────────────────────────────┘
```

The long-term objective of EidosBot is to **eliminate hand-written identity prompts**. Over iterative LoRA fine-tuning cycles, scaffolding is pruned away, replaced entirely by **procedural model weights** and **autobiographical episodic recall**.

---

## 2. Dual-Tier Memory Architecture

To completely prevent memory contamination, EidosBot strictly isolates agent experiential learning from user personalization:

```
┌────────────────────────────────────────────────────────────────────────┐
│                               EIDOSBOT                                 │
├──────────────────────────────────┬─────────────────────────────────────┤
│ 🧠 TIER A: AGENT EXPERIENTIAL    │ 👤 TIER B: USER PROFILE MEMORY      │
│ (Episodic Vector Trajectories)   │ (Declarative / Atomic Fact List)   │
├──────────────────────────────────┼─────────────────────────────────────┤
│ • Task goals, thoughts & plans   │ • Human-readable YAML/JSON file     │
│ • Full tool calls & observations │ • Explicit user habits & preferences│
│ • Success / failure evaluations  │ • Direct GUI editable & auditable   │
│ • Vector DB semantic search      │ • Zero narrative hallucination      │
│ • "How to solve tasks better"    │ • "Who the user is & what they want"│
└──────────────────────────────────┴─────────────────────────────────────┘
```

### 2.1. Tier A: Agent Experiential Memory
- **Purpose:** Preserves high-fidelity traces of past reasoning, tool executions, and post-task reflections.
- **Storage Engine:** Embedded local vector storage (SQLite with `sqlite-vec` or LanceDB / ChromaDB) backed by local embeddings via `nomic-embed-text:latest` (Ollama 137M parameter, 768-dim, running locally on CPU/GPU).
- **Retrieval Methods:**
  - Macro retrieval at the start of a task.
  - Mid-stream thought injection during reasoning.
  - Conscious active recall via `<recall>` tags.

### 2.2. Tier B: User Profile Memory (Atomic Fact List)
- **Purpose:** Stores explicit personal facts, preferred conventions, hardware paths, and workflow rules without narrative prose.
- **File Location:** `~/.eidos/user_profile.yaml`
- **Design Philosophy:** Avoid paragraphs. Each fact is an atomic, self-contained key-value entry or bullet item that can be rendered into ~60 prompt tokens and individually edited or revoked via the HUD Settings GUI.

#### Example Schema (`~/.eidos/user_profile.yaml`):
```yaml
user_identity:
  name: "alex"
  os: "Linux (X11)"

preferences:
  interaction_style: "Concise, action-oriented, avoid conversational fluff"
  voice_feedback: "Kokoro bm_george, 1.0x speed"
  tool_preference: "Deterministic shell over GUI clicking when possible"

environment_facts:
  downloads_dir: "~/Downloads"
  default_browser: "Firefox"

workflow_rules:
  - "Prefer read-only shell commands for file queries when faster than GUI clicks"
  - "Confirm before moving or deleting files outside Downloads"
```

---

## 3. Strategic Trajectory Logging & Dataset Schema

To enable future LoRA fine-tuning (via Unsloth, Axolotl, or LLaMA-Factory), every execution cycle must be captured not as plain text chat logs, but as **structured decision trajectories**.

### 3.1. Append-Only Trajectory Store (`~/.eidos/trajectories/episodes.jsonl`)
Every task run by `AgentLoop.run_task` writes a JSON line containing the complete state, internal reasoning, and outcome:

```json
{
  "episode_id": "ep_20260914_063012_f7a2",
  "timestamp": "2026-09-14T06:30:12Z",
  "model": "batiai/gemma4-26b:iq4",
  "task_goal": "Sort this month's screenshots in Downloads into dated folders",
  "metadata": {
    "outcome": "success",
    "total_steps": 2,
    "user_intervened": false,
    "critic_score": 1.0
  },
  "trajectory": [
    {
      "step": 1,
      "state_snapshot": {
        "active_window": "Files",
        "cwd": "~/Downloads"
      },
      "system_prompt": "SYSTEM CONTEXT...",
      "retrieved_context": [
        "Past Episode #19: Bulk renames in Downloads are safer with a dry-run list before mv."
      ],
      "model_thinking": "User wants screenshots grouped by month. I'll list PNG/JPEG files in Downloads and group by modification month before moving anything.",
      "tool_call": {
        "name": "execute_shell",
        "arguments": {
          "command": "find ~/Downloads -maxdepth 1 -type f \\( -iname '*.png' -o -iname '*.jpg' \\) -printf '%TY-%Tm %f\\n' | sort"
        }
      },
      "tool_observation": {
        "exit_code": 0,
        "stdout": "2026-09 photo1.png\\n2026-09 photo2.jpg"
      }
    },
    {
      "step": 2,
      "model_thinking": "I have a clear month bucket. I'll create folders and move files after the user confirms the plan.",
      "final_response": "Found 2 screenshots for 2026-09; ready to move into ~/Downloads/2026-09 after you approve.",
      "tool_call": {
        "name": "finish_task",
        "arguments": {
          "summary": "Listed Downloads screenshots by month; awaiting approval before moving files."
        }
      }
    }
  ],
  "reflection": {
    "summary": "Used a read-only find listing before proposing any mv commands.",
    "pitfalls_avoided": "Did not move files until the user confirmed the folder plan."
  }
}
```

### 3.2. Training Export Pipelines

From this single trajectory store, two distinct datasets can be automatically extracted:

#### 1. Supervised Fine-Tuning (SFT / LoRA)
- **Filter Criteria:** `outcome == "success" AND user_intervened == false`
- **Output Format:** Standard ShareGPT / OpenAI ChatML format.
- **Content:** The assistant turns explicitly contain the `<thought>` block followed by the tool call. Fine-tuning on this permanently teaches the model:
  - Exact machine environment paths and tool names.
  - Desktop-first reasoning chains.
  - Consistent JSON argument formatting.

#### 2. Direct Preference Optimization (DPO / ORPO)
- **Filter Criteria:** Tasks where a step failed (e.g., tool returned non-zero exit code or wrong coordinates) and was subsequently corrected in the next step or retry.
- **Output Pairs:**
  - **Prompt:** User goal + current environmental state.
  - **Rejected (Negative):** The flawed reasoning trace and tool call that failed.
  - **Chosen (Positive):** The successful reasoning trace and tool call that resolved the issue.
- **Benefit:** Trains the model's neural attention to steer away from known bad actions (e.g., trying to click an unfocused window or running commands that trigger permission errors).

---

## 4. Episodic Recall & Thought-Stream Injection Mechanics

Because EidosBot runs local models via Ollama (`batiai/gemma4-26b`, `gemma4-12b`), inference is not restricted by cloud API boundaries. Memory can be retrieved and injected at three distinct points:

```
               [ User Task Submitted ]
                          │
                          ▼
             [ 1. MACRO RECALL (Turn Start) ]
             Vector query on user goal -> Injects <episodic_context>
                          │
                          ▼
                 [ LLM Begins Reasoning ]
                          │
         ┌────────────────┴────────────────┐
         ▼                                 ▼
[ 2. CONSCIOUS ACTIVE RECALL ]   [ 3. SUBCONSCIOUS SEMANTIC TRIGGER ]
Model writes <recall>query</recall>      Token stream matches vector embedding
         │                                 │
         └────────────────┬────────────────┘
                          ▼
            [ Stream Pause & Splice ]
            Vector DB query -> Drop [Episodic Recall] -> Continue generation
                          │
                          ▼
               [ Tool Call Dispatched ]
```

### 4.1. Method 1: Macro Recall (Turn Start)
Before the model begins step 1, `episodic_store.py` performs a vector search using `nomic-embed-text` on the user's task goal:
- Fetches the **top 2 most relevant past episodes** (1 golden path success, 1 cautionary pitfall).
- Renders them directly into the context window under `<episodic_context>`.

#### 4.1.1. Observation-Driven Trajectory Distillation
Raw trajectories often contain exploratory trials where an agent attempts multiple candidate commands before finding the one that works in the user's specific environment (e.g. attempting `brightnessctl`, `xbacklight`, and then succeeding with `xrandr`). To prevent the agent from blindly repeating dead ends, `EpisodicStore._extract_summary_and_actions` distills trajectories based on actual tool observations:

1. **Observation Evaluation (`_is_tool_observation_successful`):**
   - Each action in `step.tool_calls` is cross-referenced with `step.tool_observations`.
   - Actions with `exit_code == 0`, `success == True`, and `denied == False` are classified as **Successful Actions**.
   - Actions with non-zero exit codes, permission denials, or stderr errors are classified as **Dead Ends**.

2. **Forward Chronological Proven Tool Pattern:**
   - **All** successful actions are retained in their natural forward execution order without arbitrary truncation (e.g., removing legacy `[:4]` caps).
   - This ensures multi-step sequential dependencies (e.g., `xrandr query display` $\rightarrow$ `xrandr set brightness` or `mkdir -p` $\rightarrow$ `mv`) remain coherent.

3. **Controlled Dead-End Caching (Top 2–3 Pitfalls):**
   - Dead ends are extracted along with their short failure reasons (e.g., `execute_shell(brightnessctl set 25%) -> [brightnessctl: command not found]`).
   - Sliced to the top 2–3 unique dead ends to provide clear negative guidance without causing context window bloat.

#### 4.1.2. Injected Prompt Format
The resulting `<episodic_context>` block rendered into the system prompt:
```markdown
<episodic_context>
Past Experience - Golden Path Success (Similarity: 0.74):
- Past Task: can you turn the screen brightness down to 25
- Resolution Summary: Screen brightness lowered to 25% via xrandr on HDMI-0.
- Dead Ends / Pitfalls Avoided: execute_shell(brightnessctl set 25%) -> [brightnessctl: command not found]; execute_shell(xbacklight -set 25) -> [xbacklight: command not found]
- Proven Tool Pattern: execute_shell(xrandr --query | grep " connected primary" | cut -d' ' -f1) -> execute_shell(xrandr --output HDMI-0 --brightness 0.25)

Past Experience - Cautionary Pitfall (Similarity: 0.59):
- Past Task: Bulk move screenshots to Archive
- Outcome: failed
- Pitfall Avoided: execute_shell(mv *.png Archive/) -> [Archive/ is not a directory]
- Crucial Lesson: Always run mkdir -p on target directory prior to moving files.
</episodic_context>
```

### 4.2. Method 2: Conscious Active Recall (`<recall>`)
The model is granted a cognitive self-query capability during its internal `<thought>` stream.
If the model encounters an ambiguous problem or wants to recall how it previously performed a task, it emits:
```markdown
<thought>
I need to check the active audio stream. Let me see how we queried pactl sink inputs previously.
<recall>pactl list sink inputs audio stream</recall>
```
1. **Token Stream Monitor:** The streaming reader in `ollama_client.py` and `chutes_client.py` monitors tokens as they arrive.
2. **Interception:** Upon detecting `</recall>`, the stream reader pauses the HTTP stream.
3. **Lookup:** It extracts the query text, queries the vector database, and formats the matching memory.
4. **Splice & Resume:** It injects the recalled knowledge immediately after the tag:
   ```markdown
   <recalled_memory episode="ep_20260814_audio">
   - Past Task: Query active audio streams
   - Outcome: success
   - Resolution Summary: Listed active sink inputs using pactl with application name filter.
   - Dead Ends / Pitfalls Avoided: execute_shell(cat /proc/asound/cards) -> [did not expose sink volume inputs]
   - Proven Tool Pattern: execute_shell(pactl list sink-inputs | grep -E 'Sink Input|application.name')
   </recalled_memory>
   Continuing plan: I will execute pactl list sink-inputs with grep filter...
   ```
5. Generation resumes seamlessly from that injected thought.

### 4.3. Method 3: Subconscious Semantic Interception
For critical traps that the model might not consciously ask about:
- As `<thought>` tokens stream, a sliding window of the last 20 tokens is embedded asynchronously via `nomic-embed-text` (~10ms).
- A cosine distance query checks against high-severity cautionary memories (pitfalls).
- If similarity exceeds a high confidence threshold ($\ge 0.88$), the stream is spliced with a warning note:
  `[Episodic Warning: In Episode #31, running this command without -f caused a blocking hang.]`

---

## 5. The 3-Stage Evolution to Subconscious Identity

The ultimate goal of this memory architecture is to transition EidosBot from an **externally instructed bot** to an **internally driven, self-aware desktop companion**.

```
  Stage 1: V1 Scaffolding         Stage 2: V2 LoRA Distillation     Stage 3: V3 Emergent Selfhood
┌─────────────────────────┐     ┌──────────────────────────┐      ┌───────────────────────────┐
│ System Prompt:          │     │ System Prompt:           │      │ System Prompt:            │
│ • "You are EidosBot..." │     │ • Minimal Behavioral Cue │      │ • ZERO Identity text      │
│ • 6+ Behavioral Rules   │     │ • Runtime State & Tools  │      │ • ONLY Runtime State &    │
│ • Explicit Tool Syntax  │     │                          │      │   Dynamic Tool Schemas    │
│                         │     │                          │      │                           │
│ Trajectory Logger:      │     │ LoRA Iteration 1-2:      │      │ LoRA Iteration 3+:        │
│ • Records every thought │────▶│ • "Deterministic First"  │─────▶│ • Identity, voice, and    │
│   and tool execution    │     │   baked into weights     │      │   instincts fully baked   │
│                         │     │                          │      │                           │
│ Memory:                 │     │ Memory:                  │      │ Memory:                   │
│ • None yet              │     │ • Episodic Recall active │      │ • Episodic Recall defines │
│                         │     │   for tricky tasks       │      │   who Eidos is & has been │
└─────────────────────────┘     └──────────────────────────┘      └───────────────────────────┘
```

### Stage 1: The Seed Stage (V1 — Initial Build)
- Uses the comprehensive system prompt defined in `eidos/core/context.py`.
- `trajectory_logger.py` records every task to `~/.eidos/trajectories/episodes.jsonl`.
- The user interacts naturally with EidosBot, accumulating hundreds of realistic desktop workflows.

### Stage 2: LoRA Distillation (V2)
- Train the first LoRA adapter on successful, high-quality trajectories.
- The model internalizes:
  - Prioritizing deterministic shell commands over blind mouse clicks.
  - Automatically calling `finish_task` upon goal completion.
  - Terse, structured reasoning within `<thought>` tags.
- **Scaffolding Removal:** The "BEHAVIORAL PRINCIPLES" section is deleted from `context.py`. The model follows them instinctively from weight priors.

### Stage 3: Emergent Selfhood (V3)
- Retrain LoRA on memory-assisted trajectories.
- **Full Scaffolding Removal:** The sentence `"You are EidosBot, an OS-level autonomous desktop co-pilot..."` is deleted.
- **Why it maintains identity:**
  - **Neural Weights:** The LoRA weights contain the style, voice, and conversational instincts of EidosBot.
  - **Episodic Continuity:** Recalled past memories read the agent's *own past thoughts*, creating an unbroken autobiographical history.
  - **Runtime Grounding:** The user profile and dynamic desktop environment (window, clock, CWD) anchor the agent in reality.

---

## 6. Directory Structure & Implementation Layout

The memory and learning subsystem is organized under `eidos/memory/`:

```
eidos/
├── memory/
│   ├── __init__.py
│   ├── user_store.py          # Atomic CRUD for ~/.eidos/user_profile.yaml
│   ├── episodic_store.py      # Vector DB interface (nomic-embed-text + sqlite-vec/LanceDB)
│   ├── trajectory_logger.py   # High-fidelity append-only recorder (~/.eidos/trajectories/)
│   ├── reflector.py           # Post-task critic: extracts lessons, pitfalls & summary
│   ├── recall_stream.py       # Token-stream interceptor for <recall> and mid-stream splicing
│   └── types.py               # Pydantic data schemas for episodes, steps, and facts
├── training/
│   ├── export_sft.py          # Converts successful episodes to ShareGPT / ChatML JSON
│   ├── export_dpo.py          # Extracts chosen/rejected pairs from mistake self-corrections
│   └── train_lora.py          # Unsloth / PEFT training launcher for local Gemma-4 / Qwen models
```

---

## Resource isolation (GPU / Ollama)

Background memory work (reflector, future consolidator) must not stack a second local reasoning model on the GPU while the user or agent is active. See [`docs/specs/SPEC_COMPUTE_RESOURCE_POLICY.md`](specs/SPEC_COMPUTE_RESOURCE_POLICY.md).

## 7. Storage Specifications & Technical Metrics

| Component | Technology | Storage Location | Latency / Footprint |
|---|---|---|---|
| **User Profile** | YAML / PyYAML | `~/.eidos/user_profile.yaml` | < 1 ms read, ~60 tokens in prompt |
| **Raw Trajectories** | JSONL (Append-Only) | `~/.eidos/trajectories/episodes.jsonl` | Async disk write, ~5–15 KB per episode |
| **Vector Embeddings** | `nomic-embed-text:latest` (Ollama) | 768-dimensional float32 | ~10–15 ms per query on CPU |
| **Vector Database** | `sqlite-vec` or `LanceDB` | `~/.eidos/memory/vector_store/` | < 5 ms nearest-neighbor lookup |
| **Trajectory Sync & Distillation** | Observation analyzer in `episodic_store.py` | `~/.eidos/memory/vector_store/episodic.db` | Filters dead ends, preserves forward recipes, auto-syncs on startup |
| **Stream Interception**| Regex Token Scanner in `ollama_client.py` & `chutes_client.py` | In-memory stream buffer | Zero overhead during regular token generation |

---

## 8. Detailed Architectural Feature Specifications

To complete the full episodic memory lifecycle, four modular engineering specifications expand this foundational design:

1. **Autonomous Post-Task Reflector & Critic:** [`docs/specs/SPEC_EPISODIC_REFLECTOR.md`](specs/SPEC_EPISODIC_REFLECTOR.md)  
   *Evaluator agent pass to audit goal completion, extract nuanced lessons/pitfalls, and calibrate continuous critic scores ($0.0 \dots 1.0$).*
2. **Entity Linking & Relational Knowledge Graph:** [`docs/specs/SPEC_EPISODIC_ENTITY_GRAPH.md`](specs/SPEC_EPISODIC_ENTITY_GRAPH.md)  
   *Deterministic entity extraction (commands, paths, projects, devices) and embedded SQLite graph tables for hybrid Graph-Augmented Retrieval (GAR).*
3. **Sleep-Time Episodic Consolidation & Pruning:** [`docs/specs/SPEC_EPISODIC_CONSOLIDATION.md`](specs/SPEC_EPISODIC_CONSOLIDATION.md)  
   *Off-peak clustering of repetitive tasks into canonical golden recipes, deduplication, and habit promotion to Tier B profile rules and skills.*
4. **Context-Aware Dynamic Task Scoping & Multi-Source Fusion:** [`docs/specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md`](specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md)  
   *Dynamic environmental scope resolution (project, active skill, window) and soft-boost re-ranking across episodic, note, and chat stores.*

**Old system (item 4).** Scope is the git project, the active skill, and the focused window.

**Updated job concept.** Scope also carries the primary project job (`job_id`) and the integration used on that turn (`gmail`). Vector recall stays inside the job. The entity graph may return an episode from another job when they share an entity, without loading that job's brief or tools. See [PROJECT_JOBS.md](PROJECT_JOBS.md) section 13.

