# EidosBot Episodic Memory Architecture Specifications

This directory contains the detailed engineering specifications for completing the full **Episodic Memory & Experiential Learning System** for EidosBot.

---

## Specification Index

| Document | Title | Component Target | Core Focus |
|---|---|---|---|
| [`SPEC_EPISODIC_REFLECTOR.md`](SPEC_EPISODIC_REFLECTOR.md) ([Plan](PLAN_EPISODIC_REFLECTOR.md)) | **Autonomous Post-Task Reflector & Critic** | `eidos/memory/reflector.py` | Asynchronous evaluation pass to extract nuanced lessons, detect hallucinated successes, and calibrate critic scores. |
| [`SPEC_EPISODIC_ENTITY_GRAPH.md`](SPEC_EPISODIC_ENTITY_GRAPH.md) ([Plan](PLAN_EPISODIC_ENTITY_GRAPH.md)) | **Entity Linking & Relational Knowledge Graph** | `eidos/memory/graph_store.py`, `eidos/memory/entity_extractor.py` | Deterministic entity extraction (paths, binaries, projects, devices) and embedded SQLite graph linking for hybrid Graph-Augmented Retrieval. |
| [`SPEC_EPISODIC_CONSOLIDATION.md`](SPEC_EPISODIC_CONSOLIDATION.md) | **Sleep-Time Consolidation, Generalization & Pruning** | `eidos/memory/consolidator.py` | Off-peak clustering of repetitive tasks into canonical golden recipes, deduplication, and habit promotion to Tier B profile rules and skills. |
| [`SPEC_EPISODIC_DYNAMIC_SCOPING.md`](SPEC_EPISODIC_DYNAMIC_SCOPING.md) | **Dynamic Task Scoping & Multi-Source Fusion** | `eidos/memory/scope_resolver.py` | Environmental context awareness (project boundaries, active skill, window class) and calibrated re-ranking across episodic, note, and chat stores. |

---

## Architectural Alignment with Cognitive Model

These 4 modules systematically complete the four cognitive capabilities required for true lifelong experiential learning:

1. **Perception & Critique (`Reflector`):** Converting raw experience into distilled wisdom and calibrated performance metrics.
2. **Associative Structure (`Entity Graph`):** Linking episodes to real-world objects, system tools, and workspace projects.
3. **Abstraction & Long-Term Consolidation (`Consolidator`):** Compacting memory during idle periods to prevent vector bloat and promote recurring patterns to instinctual procedural habits.
4. **Situated Relevance (`Dynamic Scoping`):** Filtering and boosting memories based on the agent's immediate physical and digital surroundings.
