---
name: user-memory
version: 1.0.0
description: Maintain persistent user habits, conventions, workflow guidelines, and environment facts in ~/.eidos/user_profile.yaml.
author: eidos-core
tags: [memory, profile, preferences, rules, conventions]
tools_required:
  - update_user_profile
  - add_workflow_rule
  - remove_workflow_rule
  - remove_user_fact
---

# User Profile & Habit Memory Procedure

## 1. Objective
Persist explicit user preferences, communication styles, coding conventions, project paths, and workflow rules across sessions in the atomic user profile (`~/.eidos/user_profile.yaml`).

## 2. Operating Principles
- **Explicit Instruction Only:** Only update user memory when the user explicitly asks to remember, forget, or update a fact or recurring rule (e.g. "Remember that I prefer...", "Always use...", "Forget my old editor path").
- **Atomic Facts:** Use `update_user_profile` to store single key/value pairs in one of the three standard categories: `user_identity`, `preferences`, or `environment_facts`.
- **Workflow Guidelines:** Use `add_workflow_rule` to store recurring procedural bullets (e.g. "Always run unit tests before proposing git commits").
- **Pruning & Deletion:** Use `remove_workflow_rule` or `remove_user_fact` when the user directs EidosBot to forget or unlearn an outdated preference.
- **Confirmation:** Profile writes require user confirmation in the HUD overlay or gateway. Explain what fact or rule will be saved before emitting the tool call.
