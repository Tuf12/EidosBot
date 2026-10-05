---
name: optimalx
version: 1.0.0
description: Search, read, write, and navigate personal notes and knowledge bases in OptimalX (synced across Android Mobile and Linux Mint Desktop).
author: eidos-core
tags: [optimalx, notes, knowledge-base, notex, diff-review]
tools_required:
  - optimalx_search_notes
  - optimalx_read_note
  - optimalx_write_note
  - optimalx_list_folders
  - optimalx_read_notex_doc
  - optimalx_list_notex_docs
  - optimalx_action
---

# OptimalX Knowledge Ecosystem Procedure

## 1. Objective
Access the user's personal knowledge base, ideas, project notes, and long-form NoteX documents synchronized between Android Mobile and Linux Mint Desktop via SQLite WAL and the local Express bridge.

## 2. Operating Principles
- **Targeted Lookups:** Use `optimalx_search_notes` with concise semantic queries or keywords. Use `optimalx_list_folders` with a specific query (e.g. `'Inventions'`) rather than listing the entire knowledge tree.
- **Reading Notes & Documents:** Use `optimalx_read_note` for markdown note contents. For long project corpora in NoteX, use `optimalx_list_notex_docs` and `optimalx_read_notex_doc` with line ranges if the document is extensive.
- **Privacy Enforcement:** Respect protected notes with `ai_locked=1` or `ai_blind=1`. Do not attempt to bypass privacy flags.
- **Safe Mutation & Diff Review:**
  - Writing to a brand new or empty note auto-applies directly with SHA-256 version checkpoints.
  - Proposing edits or modifications to existing non-empty notes queues a diff proposal into OptimalX Diff Review for human review and approval in the desktop app.
  - Machine logs and batch runner outputs can use `allow_direct_append=True` with `mode='append'` when explicitly instructed.
- **Data Extraction:** When extracting large lists or 50+ items into CSV/JSON, do not dump hundreds of lines in chat. Read the note content and use `execute_shell` to parse and save the output directly into `~/EidosWorkspace/documents/<filename>.csv`. Then call `share_file` on that saved file so the user can open it in the web app, including on a phone.
