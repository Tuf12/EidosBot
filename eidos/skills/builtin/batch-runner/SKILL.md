---
name: batch-runner
version: 1.0.0
description: Domain-agnostic state-machine batch queue management (queue.json) for multi-item workloads (10 to 1,000+ items) with fresh-context isolation.
author: eidos-core
tags: [batch, queue, state-machine, workflow, automation]
tools_required:
  - init_batch_job
  - run_batch_job
  - step_batch_job
  - get_job_status
  - pause_batch_job
  - resume_batch_job
  - generate_batch_summary
  - load_job
  - close_job
---

# State-Machine Batch Queue Runner Procedure

## 1. Objective
Initialize, execute, monitor, and summarize large-scale batch processing queues with persistent checkpointing in `queue.json` and fresh per-item context windows.

## 2. Operating Principles
- **Initialization:** Use `init_batch_job` with a descriptive `job_name`, assigned `skill_name`, input items (CSV/JSON file or data array), and destination workspace directory.
- **Execution:**
  - Use `run_batch_job` to process pending items sequentially. Set `background=True` (default) for non-blocking execution or `background=False` for synchronous completion.
  - Set `pause_between_items_sec` (e.g. 1.0–2.0s) to maintain thermal stability and respect model rate limits.
  - Use `step_batch_job` when testing or stepping a single item manually before launching full continuous execution.
- **Monitoring & Controls:**
  - Use `get_job_status` to inspect completed/pending counts and current item status.
  - Use `pause_batch_job` and `resume_batch_job` to safely halt or continue execution.
- **Summarization:** Use `generate_batch_summary` to compile final markdown rankings and CSV summaries upon queue completion. Then call `share_file` on those summary files so they appear in the web app.
