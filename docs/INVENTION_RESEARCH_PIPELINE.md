# EidosBot Invention Research & Batch Evaluation Pipeline
## Autonomous Multi-Item Prior Art Research, Commercial Viability Scoring & Portfolio Generation

This specification details the end-to-end architecture, data schemas, sub-agent execution lifecycle, and aggregation mechanics for running automated research across large invention lists (e.g., 100 to 300+ concepts) using EidosBot.

---

## 1. Executive Summary & Design Principles

### 1.1. The Operational Challenge
Evaluating large batches of invention concepts introduces critical hurdles for typical LLM agents:
1. **Context Window Saturation & Amnesia:** Running 300 items in a single conversational thread blows past context limits within 5 to 10 items, leading to severe hallucination, degraded reasoning, and skipped criteria.
2. **Search Fatigue & Fragility:** Scraping Google Patents and commercial search engines manually or via screen-capture clicking is brittle, slow, and prone to UI blocking.
3. **Lack of Resilient State:** A network timeout, API failure, or process crash on item #142 should never lose progress or restart from item #1.

### 1.2. The Solution: Triad of Separation
To solve this, EidosBot strictly decouples execution into three distinct layers:
```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               INVENTION PIPELINE TRIAD                                 │
├─────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│ 🛠️ DOMAIN SKILL         │ 🤖 FRESH SUB-AGENT WORKER     │ ⚙️ RESILIENT QUEUE ENGINE    │
│ (~/.eidos/skills/)      │ (eidos/jobs/processor.py)     │ (eidos/jobs/runner.py)       │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ • 4-dimension rubric    │ • Ephemeral 0-history session │ • Atomic queue.json state    │
│ • Patent search rules   │ • Headless Chromium research  │ • Checkpoint per item        │
│ • Portfolio MD template │ • Structured JSON evaluation  │ • Pacing & retry mechanics   │
│ • Domain-specific       │ • Sandboxed portfolio write   │ • 100% domain-agnostic       │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

**Old system.** The triad above is the whole container for an invention run: a domain skill, a fresh worker, and a queue.

**Updated job concept.** The triad stays the execution engine. It attaches to a project job. The invention list is the batch-queue component. Portfolios, images, and source PDFs stay in that job's `artifacts/`. The job brief holds what this invention is and what it is not, and that brief is in the worker prompt ahead of prior-art hits. See [PROJECT_JOBS.md](PROJECT_JOBS.md).

**Updated job concept (groups).** An invention job lives under its group, for example `~/EidosWorkspace/jobs/Inventions/coil-lantern/`. The group file supplies the shared skill. Each invention keeps its own brief. See [PROJECT_JOBS.md](PROJECT_JOBS.md) section 14.

1. **Domain Skill (`invention-evaluator/SKILL.md`):** Contains the 4-dimension commercial rubric (0–100 score), search query blueprints, and standardized portfolio format.
2. **Fresh-Context Sub-Agent Processor (`eidos/jobs/processor.py`):** An isolated worker that processes exactly one item at a time with a pristine context window, using Brave Search and headless Chromium to gather evidence, score against the rubric, and generate a portfolio document.
3. **Resilient Batch Job Runner (`eidos/jobs/runner.py`):** A domain-agnostic state machine that tracks progress in `queue.json`, coordinates pacing and retries, handles pauses/cancellations, pushes Telegram milestones, and compiles a final ranked leaderboard (`RANKING_SUMMARY.md`).

---

## 2. Ingestion & Input Schemas

EidosBot ingests invention lists from CSV, JSON, or Markdown tables. The input data is parsed and normalized into domain-agnostic `QueueItem` objects stored in `queue.json`.

### 2.1. Supported Input Formats

#### Option A: CSV File (`inventions.csv`)
```csv
id,title,category,description,target_audience
INV-001,Solar Dew Condenser,Cleantech,"Atmospheric water extraction unit powered by portable solar collector with peltier cooling array.",Hikers & off-grid homesteaders
INV-002,Ultrasonic Anti-Fouling Hull Ring,Marine,"Ring-shaped ultrasonic transducer collar preventing barnacle adhesion on boat hulls.",Recreational boat owners & marinas
INV-003,Tactile Braille E-Reader,Accessibility,"Dynamic piezoelectric pin array displaying full-page refreshable Braille and tactile diagrams.",Visually impaired students & professionals
```

#### Option B: JSON File (`inventions.json`)
```json
[
  {
    "id": "INV-001",
    "title": "Solar Dew Condenser",
    "category": "Cleantech",
    "description": "Atmospheric water extraction unit powered by portable solar collector with peltier cooling array.",
    "target_audience": "Hikers & off-grid homesteaders"
  }
]
```

### 2.2. Normalized Item Schema in `queue.json`
Every item is ingested into a generic `QueueItem`:
```json
{
  "id": "INV-001",
  "input": {
    "title": "Solar Dew Condenser",
    "category": "Cleantech",
    "description": "Atmospheric water extraction unit powered by portable solar collector with peltier cooling array.",
    "target_audience": "Hikers & off-grid homesteaders"
  },
  "status": "pending",
  "result": null,
  "error": null,
  "retries": 0,
  "started_at": null,
  "completed_at": null
}
```

---

## 3. Sub-Agent Execution Lifecycle (`SkillJobProcessor`)

When an item transitions from `pending` to `in_progress`, the `SkillJobProcessor` runs an isolated, 3-stage research procedure. Item #300 executes with the exact same pristine reasoning capacity and token budget as item #1.

```
┌────────────────────────────────────────────────────────────────────────┐
│                   PER-ITEM RESEARCH LIFECYCLE (SUB-AGENT)              │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   [ QueueItem: INV-042 ] ──▶ [ Fresh Context Sub-Agent Created ]       │
│                                    │                                   │
│                                    ▼                                   │
│                       ┌─────────────────────────┐                      │
│                       │ Stage 1: Prior Art Rsch │                      │
│                       │ • Brave Search API      │                      │
│                       │ • Headless Chromium     │                      │
│                       │ • Google Patents scrape │                      │
│                       └────────────┬────────────┘                      │
│                                    ▼                                   │
│                       ┌─────────────────────────┐                      │
│                       │ Stage 2: Rubric Scoring │                      │
│                       │ • Technical Feasibility │                      │
│                       │ • Market Demand         │                      │
│                       │ • Novelty & Freedom     │                      │
│                       │ • Margin & BOM Ratio    │                      │
│                       └────────────┬────────────┘                      │
│                                    ▼                                   │
│                       ┌─────────────────────────┐                      │
│                       │ Stage 3: Artifact Gen   │                      │
│                       │ • Write Portfolio MD    │                      │
│                       │ • Record to queue.json  │                      │
│                       └─────────────────────────┘                      │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.1. Stage 1: Automated Prior Art & Market Discovery
1. **Query Construction:** Formulates 2–4 targeted search queries based on the core functional mechanism:
   - Query 1: `"<core mechanism>" patent prior art`
   - Query 2: `site:patents.google.com "<keywords>"`
   - Query 3: `commercial product "<function>" alternatives`
2. **Web Discovery via Brave API (`WebResearchEngine`):**
   - Retrieves top web and patent search hits with snippets and direct URLs.
3. **Deep Extraction via Playwright (`PlaywrightManager`):**
   - If a promising Google Patents URL or competitor page is identified, Playwright navigates headlessly and extracts clean Markdown via `trafilatura`.
   - Bypasses client-side rendering bottlenecks without opening visible browser windows.

### 3.2. Stage 2: 4-Dimension Rubric Scoring
The model evaluates the collected evidence against the 4 core dimensions defined in `invention-evaluator/SKILL.md` (0–25 points each, composite 0–100):

| Dimension | Points | Criteria & Quality Bar |
|---|---|---|
| **A: Technical Feasibility** | 0–25 | **20–25:** Built with mature off-the-shelf components, standard materials, standard tooling.<br>**10–19:** Requires custom engineering, proprietary firmware, or tight tolerances.<br>**0–9:** Violates physics, relies on undiscovered science or unproven speculative materials. |
| **B: Market Demand & Pain Point** | 0–25 | **20–25:** Solves an acute, urgent, frequent pain point for an identifiable demographic willing to pay.<br>**10–19:** Solves a moderate convenience or nice-to-have optimization.<br>**0–9:** Solution in search of a problem; minimal user willingness to change behavior. |
| **C: Novelty & Freedom to Operate** | 0–25 | **20–25:** Highly unique mechanism; no direct 1-to-1 prior art found in active patents.<br>**10–19:** Crowded field; close prior art exists but specific design improvements remain patentable.<br>**0–9:** Direct prior art or expired patents already disclose the exact claimed mechanism. |
| **D: Cost-to-Produce vs. Margin** | 0–25 | **20–25:** Bill of Materials (BOM) < 20% of target retail price (5x markup achievable).<br>**10–19:** BOM between 20%–40% of target price (viable retail/wholesale margins).<br>**0–9:** High BOM, low perceived value, or complex assembly makes profitable retail unlikely. |

### 3.3. Stage 3: Portfolio Generation & Checkpointing
1. **Portfolio Markdown:** Generated and saved to:
   `<output_dir>/portfolios/<item_id>.md`
2. **Queue Record Checkpoint:** Saved atomically to `queue.json`:
   ```json
   {
     "id": "INV-001",
     "status": "completed",
     "result": {
       "summary": "High commercial potential (84/100). Off-the-shelf peltier condensation with clear off-grid camper demand.",
       "total_score": 84,
       "scores": {
         "technical_feasibility": 23,
         "market_demand": 21,
         "novelty_and_fto": 18,
         "margin_potential": 22
       },
       "prior_art_found": [
         "US6863827B2 - Solar powered portable water purifier",
         "US20140366560A1 - Atmospheric water generation system"
       ],
       "artifacts": [
         "portfolios/INV-001.md"
       ]
     },
     "completed_at": "2026-10-01T22:15:30Z"
   }
   ```

---

## 4. Continuous Autonomous Batch Runner

To process batches of 100 to 300+ items without human intervention, EidosBot implements a dedicated background execution loop.

### 4.1. Execution Flow & Pacing
```python
def run_job(
    self,
    job_id_or_path: Optional[str] = None,
    processor: Optional[Callable] = None,
    pause_between_items_sec: float = 2.0,
) -> Dict[str, Any]:
    """Execute queue items sequentially until completion, pause, or kill signal."""
```
- **Pacing Delay (`pause_between_items_sec`, default: 2.0s):** Enforces cool-down intervals between items. This prevents Brave API rate limits, avoids tripping web scrape protections, and allows GPU thermal throttling to remain stable during 4+ hour runs.
- **Atomic Disk Checkpoints:** Progress is flushed to disk after every single item using atomic temporary file swaps (`queue.json.tmp` $\rightarrow$ `queue.json`).
- **Power Failure / Reboot Recovery:** If interrupted at item #78, re-running `step_batch_job` or `resume_batch_job` immediately picks up with item #79 with zero duplicate work.

### 4.2. Human-in-the-Loop & Ambiguity Handling
If an invention description is nonsensical, contradictory, or missing critical data:
1. The processor transitions the item to `"status": "needs_review"`.
2. The batch runner does **not** crash or abort the remaining 299 items; it increments `needs_review_count` and continues processing subsequent pending items.
3. If the Telegram Gateway is enabled, an interactive notification is sent:
   > ⚠️ **Invention Needs Review:** `INV-047: Quantum Perpetual Energy Case`  
   > *Description violates energy conservation laws without specifying mechanism.*  
   > `[Skip Item]` `[Clarify via Reply]` `[Force Evaluate]`
4. Responses received via Telegram or GUI chat resolve the item's status asynchronously.

---

## 5. Telemetry, Remote Control & Safety

### 5.1. Telegram & PWA Remote Telemetry
During unattended background runs, EidosBot pushes real-time milestone notifications:
- **Milestone Cadence:** Configurable via `JobSettings(notify_interval=10)` (every 10 items).
- **Push Payload:**
  ```text
  📊 Batch Job Milestone: 2026 Inventions Project
  Progress: 50 / 300 items (16.7%)
  Last Evaluated: INV-050 (Score: 78/100)
  Avg Score So Far: 64.2/100
  Elapsed: 1h 42m | Est. Remaining: 8h 20m
  Active Item: INV-051 (Robotic Gutter Cleaner)
  ```

### 5.2. Remote Commands & Safety Interventions
Users can control running batch jobs from anywhere:
- `/jobs` — View current progress, items completed, and active item.
- `/pause` — Gracefully pause after the current item finishes.
- `/resume` — Resume processing pending items.
- `/kill` (or host hotkey `<ctrl>+<alt>+<esc>`) — Instantly terminate execution, close headless browsers, and save the checkpoint.

---

## 6. Post-Job Leaderboard & Aggregation (`RANKING_SUMMARY.md`)

When all items in the queue reach a terminal state (`completed`, `failed`, or `skipped`), the runner automatically compiles `RANKING_SUMMARY.md` in the job directory.

### 6.1. Leaderboard Structure

```markdown
# 2026 Inventions Project — Commercial Viability Ranking Summary

**Total Inventions Evaluated:** 300  
**Completed Successfully:** 294  
**Needs Review / Incomplete:** 6  
**Average Viability Score:** 61.8 / 100  
**Generated On:** 2026-10-02 04:30 UTC  

---

## 🏆 Top Tier: High Commercial Viability (Scores 80–100)
*Recommended for immediate bench prototyping, formal prior art search, and provisional patent filing.*

| Rank | ID | Title | Category | Tech (25) | Market (25) | Novelty (25) | Margin (25) | Total (100) | Portfolio |
|---|---|---|---|---|---|---|---|---|---|
| 1 | INV-088 | Micro-Hydro Vortex Generator | Cleantech | 24 | 23 | 22 | 21 | **90** | [Portfolio](portfolios/INV-088.md) |
| 2 | INV-014 | Smart Orthotic Pressure Insole | MedTech | 21 | 24 | 22 | 21 | **88** | [Portfolio](portfolios/INV-014.md) |
| 3 | INV-001 | Solar Dew Condenser | Cleantech | 23 | 21 | 18 | 22 | **84** | [Portfolio](portfolios/INV-001.md) |

---

## 💡 Tier 2: Moderate Viability / Promising with Caveats (Scores 60–79)
*Strong potential, but requires addressing specific weaknesses (e.g., crowded patent space or high initial BOM).*

| Rank | ID | Title | Category | Tech (25) | Market (25) | Novelty (25) | Margin (25) | Total (100) | Portfolio |
|---|---|---|---|---|---|---|---|---|---|
| 28 | INV-102 | Modular Rooftop Wind Turbine | Cleantech | 18 | 19 | 15 | 16 | **68** | [Portfolio](portfolios/INV-102.md) |
| 29 | INV-050 | Magnetic Quick-Release Cleat | Sports | 22 | 14 | 16 | 15 | **67** | [Portfolio](portfolios/INV-050.md) |

---

## ⚠️ Tier 3: Low Viability / High Prior Art Saturation / Speculative (Scores < 60)
*Direct prior art disclosed, unviable retail margins, or weak consumer demand.*

| Rank | ID | Title | Category | Tech (25) | Market (25) | Novelty (25) | Margin (25) | Total (100) | Portfolio |
|---|---|---|---|---|---|---|---|---|---|
| 240 | INV-210 | Self-Heating Coffee Mug Lid | Consumer | 14 | 12 | 8 | 11 | **45** | [Portfolio](portfolios/INV-210.md) |
```

### 6.2. CSV & JSON Export
Alongside the markdown leaderboard, the runner exports:
- `RANKING_SUMMARY.csv` — Compatible with Excel, Google Sheets, and Notion.
- `RANKING_SUMMARY.json` — Machine-readable summary for downstream data pipelines.

---

## 7. User Control & Interaction Surfaces

Users can initiate, supervise, and review invention research across multiple interfaces:

### 7.1. Desktop HUD / Chat Interface
```text
User: "Eidos, let's evaluate my inventions list. Here is ~/EidosWorkspace/inventions.csv. Use the invention-evaluator skill and run the batch job in the background."

EidosBot: "Initialized batch job '2026_inventions' with 300 pending items. Output workspace configured at ~/EidosWorkspace/jobs/job_20261001_inventions/. Background research worker started with 2.0s pacing. I'll push milestone updates to your Telegram every 10 items."
```

### 7.2. CLI Execution
```bash
# Initialize and start execution immediately in background
python3 -m eidos.main --job-init "Inventions-2026" \
                      --skill "invention-evaluator" \
                      --items "~/EidosWorkspace/inventions.csv" \
                      --run

# Check status of running job
python3 -m eidos.main --job-status "Inventions-2026"
```

### 7.3. Mobile PWA & Telegram
- In the PWA chat: Live progress bar showing active item ID, score badge, and `<thought>` trace of the sub-agent's patent search.
- In Telegram: Interactive inline controls to pause, resume, or view quick score breakdowns on the go.

---

## 8. Implementation Blueprint & File Layout

To complete this system, the following concrete additions are specified:

```text
eidos/
├── jobs/
│   ├── __init__.py
│   ├── types.py            # Existing domain-agnostic queue schemas
│   ├── runner.py           # Enhance with run_job(), background thread, and ranking summary
│   ├── processor.py        # NEW: SkillJobProcessor (sub-agent execution & scoring)
│   └── summary.py          # NEW: Markdown & CSV ranking leaderboard generator
├── core/
│   ├── context.py          # Expose run_batch_job tool in EIDOS_TOOLS
│   └── loop.py             # Wire run_batch_job dispatcher to runner.run_job()
└── skills/
    └── ~/.eidos/skills/invention-evaluator/SKILL.md  # Already created & verified
```

### Detailed Component Roles:
1. **`eidos/jobs/processor.py` (`SkillJobProcessor`):**
   - Implements `process_item(item: QueueItem, context: Dict[str, Any]) -> Dict[str, Any]`.
   - Loads `SKILL.md` instructions and rubric.
   - Executes search queries via `WebResearchEngine` (Brave API) and `PlaywrightManager` (Google Patents).
   - Prompts `ModelRouter` with structured system prompt requiring JSON output for Dimensions A, B, C, D and justifications.
   - Formats and writes the individual markdown portfolio to disk inside the Bubblewrap sandbox.
2. **`eidos/jobs/summary.py` (`RankingSummaryGenerator`):**
   - Scans completed items in `queue.json`.
   - Parses scores and categorizes items into Tiers 1, 2, and 3.
   - Formats `RANKING_SUMMARY.md` and `RANKING_SUMMARY.csv`.
3. **`eidos/jobs/runner.py` Enhancements:**
   - Adds `run_job(job_id, processor, pause_sec)` for continuous sequential stepping.
   - Adds `run_background()` spawning execution in a dedicated daemon thread.
   - Triggers `RankingSummaryGenerator` when all items are resolved.
