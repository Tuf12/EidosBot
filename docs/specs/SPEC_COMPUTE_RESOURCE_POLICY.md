# Specification: Compute Resource Policy (GPU / Ollama / Chutes)

**Status:** Active  
**Implementation:** [`eidos/models/compute_policy.py`](../../eidos/models/compute_policy.py), [`eidos/models/inference_slot.py`](../../eidos/models/inference_slot.py), [`eidos/models/router.py`](../../eidos/models/router.py)

## Goals

- At most **one local GPU-heavy inference job** at a time (Ollama LLM chat/vision, optional CUDA Parakeet).
- **No second local full reasoning model** while the agent or user is active.
- Background memory LLM (reflector, future consolidator) uses **Chutes when local compute is busy**, or waits for idle before local Ollama.
- CPU embeddings (`nomic-embed-text`, `embedding_num_gpu: 0`) may run alongside Gemma but must not open a second chat stream on Ollama during an active stream.

## Rules

1. **`inference_slot`** — All Ollama `chat` / `vision_chat` and CUDA Parakeet `transcribe` acquire the process-wide slot.
2. **`ComputePolicy`** — Tracks agent task active, last user activity, and slot holder. `allow_background_local_llm()` is false while foreground is busy.
3. **`ModelRouter`** — `priority="foreground"` (default) local-first; `chat_background()` prefers Chutes when `should_use_cloud_for_background()`. Skips local Ollama when slot held if `defer_local_when_slot_held`.
4. **Embeddings** — `keep_alive: 0` on embed requests; active recall closes the HTTP stream before `embed_text`.
5. **STT** — Default `voice.stt_device: cpu`; long audio chunked at **60s** (`stt_chunk.py`), model hard cap 240s.
6. **Forbidden** — Two large Ollama vision+reasoning weights (unload extra vision model in `ollama_client`); concurrent local 26B reflector + agent chat.

## Configuration

See `models.compute` and `memory.reflector` in [`config.example.yaml`](../../config.example.yaml).

## Related specs

- [`SPEC_EPISODIC_REFLECTOR.md`](SPEC_EPISODIC_REFLECTOR.md)
- [`SPEC_EPISODIC_ENTITY_GRAPH.md`](SPEC_EPISODIC_ENTITY_GRAPH.md)
- [`SPEC_EPISODIC_CONSOLIDATION.md`](SPEC_EPISODIC_CONSOLIDATION.md)
- [`SPEC_SCHEDULED_WAKE.md`](SPEC_SCHEDULED_WAKE.md) (background agent-turn timers on fire)
