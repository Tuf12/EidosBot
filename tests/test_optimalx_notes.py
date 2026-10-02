"""Tests for OptimalX Note & Knowledge Base Integration (Phase 11.1)."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from eidos.integrations.optimalx import OptimalXBridge


@pytest.fixture
def temp_optimalx_db():
    """Create a temporary SQLite database initialized with OptimalX schema and sample data."""
    with tempfile.NamedTemporaryFile("w", suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create tables matching OptimalX schema
    cur.executescript("""
        CREATE TABLE parent_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER,
            is_system_folder INTEGER NOT NULL DEFAULT 0,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT
        );

        CREATE TABLE subfolders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_folder_id INTEGER NOT NULL REFERENCES parent_folders(id),
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER,
            is_system_subfolder INTEGER NOT NULL DEFAULT 0,
            project_summary TEXT,
            project_summary_updated_at INTEGER,
            target_platform TEXT NOT NULL DEFAULT 'mobile',
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT,
            notex_enabled INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subfolder_id INTEGER NOT NULL UNIQUE REFERENCES subfolders(id),
            content TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            deleted_at INTEGER,
            ai_locked INTEGER NOT NULL DEFAULT 0,
            ai_blind INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            summary_chunks_json TEXT,
            summary_updated_at INTEGER,
            summary_content_watermark INTEGER,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT,
            content_hash TEXT
        );

        CREATE TABLE content_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            content_blob TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            author TEXT NOT NULL,
            label TEXT,
            conversation_id INTEGER,
            created_at INTEGER NOT NULL,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT
        );

        CREATE TABLE content_patches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            from_checkpoint_id INTEGER NOT NULL REFERENCES content_checkpoints(id),
            to_checkpoint_id INTEGER NOT NULL REFERENCES content_checkpoints(id),
            unified_diff TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT
        );

        CREATE TABLE pending_change_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_type TEXT NOT NULL,
            scope_id INTEGER NOT NULL,
            conversation_id INTEGER,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT
        );

        CREATE TABLE pending_change_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_set_id INTEGER NOT NULL REFERENCES pending_change_sets(id),
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            base_checkpoint_id INTEGER REFERENCES content_checkpoints(id),
            proposed_content TEXT NOT NULL,
            proposed_hash TEXT NOT NULL,
            unified_diff TEXT,
            status TEXT NOT NULL,
            file_name TEXT,
            is_new_file INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            global_id TEXT NOT NULL UNIQUE,
            origin_device_id TEXT
        );
    """)

    # Populate Sample Data
    # 1. Parent Folders
    cur.execute("INSERT INTO parent_folders (id, name, created_at, updated_at, global_id) VALUES (1, 'Inventions', 1000, 1000, 'p1')")
    cur.execute("INSERT INTO parent_folders (id, name, created_at, updated_at, global_id) VALUES (2, 'Personal', 1000, 1000, 'p2')")
    cur.execute("INSERT INTO parent_folders (id, name, created_at, updated_at, global_id) VALUES (3, 'Work', 1000, 1000, 'p3')")

    # 2. Subfolders
    cur.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id) VALUES (10, 1, 'Autonomous Agent', 1000, 2000, 's10')")
    cur.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id) VALUES (11, 2, 'Workout Plan', 1000, 1500, 's11')")
    cur.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id) VALUES (12, 3, 'Secret Passwords', 1000, 1800, 's12')")
    cur.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id, deleted_at) VALUES (13, 3, 'Old Archived Note', 1000, 1100, 's13', 1700000)")
    cur.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id, notex_enabled) VALUES (14, 1, 'the list', 1000, 2500, 's14', 1)")

    # 3. Notes
    # Note 1: Public Inventions Note
    cur.execute("""
        INSERT INTO notes (id, subfolder_id, content, summary, created_at, updated_at, ai_locked, ai_blind, global_id, content_hash)
        VALUES (100, 10, 'Architecture design for autonomous agent using Kokoro TTS and Bubblewrap sandbox for Linux Mint.', 'Autonomous agent architecture with Kokoro and bwrap', 1000, 2000, 0, 0, 'n100', 'hash100')
    """)

    # Note 2: Public Personal Note
    cur.execute("""
        INSERT INTO notes (id, subfolder_id, content, summary, created_at, updated_at, ai_locked, ai_blind, global_id, content_hash)
        VALUES (101, 11, 'Leg day workout: squats 5x5, Romanian deadlifts 3x8, leg press 4x10.', 'Weekly leg day gym routine', 1000, 1500, 1, 0, 'n101', 'hash101')
    """)

    # Note 3: Strictly Private Note (ai_blind = 1)
    cur.execute("""
        INSERT INTO notes (id, subfolder_id, content, summary, created_at, updated_at, ai_locked, ai_blind, global_id, content_hash)
        VALUES (102, 12, 'Bank account pin: 9942, Master password: correct-horse-battery-staple', 'Banking credentials', 1000, 1800, 0, 1, 'n102', 'hash102')
    """)

    # Note 4: Soft-Deleted Note (deleted_at is set)
    cur.execute("""
        INSERT INTO notes (id, subfolder_id, content, summary, created_at, updated_at, deleted_at, ai_locked, ai_blind, global_id, content_hash)
        VALUES (103, 13, 'This note was deleted by the user last week.', 'Old deleted memo', 1000, 1100, 1700000, 0, 0, 'n103', 'hash103')
    """)

    # Note 5: Long Invention List
    cur.execute("""
        INSERT INTO notes (id, subfolder_id, content, summary, created_at, updated_at, ai_locked, ai_blind, global_id, content_hash)
        VALUES (104, 14, 'Item 1: Smart garden solar pump. Item 2: Autonomous lawn aerator. Item 3: Acoustic pipe leak detector.', 'List of 300 invention concepts', 1000, 2500, 0, 0, 'n104', 'hash104')
    """)

    conn.commit()
    conn.close()

    yield db_path

    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def temp_notex_tree():
    """Create a temporary NoteX corpus directory structure for project globalId 's14'."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_dir = Path(tmp_dir) / "s14"
        project_dir.mkdir(parents=True, exist_ok=True)
        meta_dir = project_dir / ".notex"
        meta_dir.mkdir(parents=True, exist_ok=True)

        # Write manifest.json
        manifest_data = {
            "schema": "notex_manifest/v1",
            "docs": [
                {"path": "Canon.md", "title": "Canon", "sortOrder": 10},
                {"path": "Outline.md", "title": "Outline", "sortOrder": 20},
                {"path": "Sections/Chapter 1.md", "title": "Chapter 1", "sortOrder": 30},
            ],
            "folders": [{"path": "Sections", "title": "Sections", "sortOrder": 1}],
            "activeDocPath": "Canon.md",
        }
        with open(meta_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)

        # Write Canon.md (multi-line for slicing test)
        canon_content = (
            "# Project Canon\n"
            "Line 2: Rule of Law\n"
            "Line 3: Core Technology\n"
            "Line 4: Sandbox Isolation\n"
            "Line 5: Final Directives\n"
        )
        with open(project_dir / "Canon.md", "w", encoding="utf-8") as f:
            f.write(canon_content)

        # Write Outline.md
        with open(project_dir / "Outline.md", "w", encoding="utf-8") as f:
            f.write("# Project Outline\n1. Introduction\n2. Implementation\n")

        # Write Sections/Chapter 1.md
        sections_dir = project_dir / "Sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        with open(sections_dir / "Chapter 1.md", "w", encoding="utf-8") as f:
            f.write("# Chapter 1\nDeep dive into autonomous reasoning.\n")

        yield tmp_dir


def test_optimalx_bridge_custom_paths(temp_optimalx_db):
    bridge = OptimalXBridge(
        db_path=temp_optimalx_db,
        notex_dir="/tmp/test_notex",
    )
    assert bridge.db_path == Path(temp_optimalx_db)
    assert bridge.notex_dir == Path("/tmp/test_notex")


def test_extract_snippet():
    text = "The quick brown fox jumps over the lazy dog. Local agent runs Kokoro TTS neural synthesis on Linux Mint."
    
    # Keyword match
    snippet = OptimalXBridge._extract_snippet(text, query="Kokoro", context_chars=20)
    assert "Kokoro TTS" in snippet
    
    # Missing keyword falls back to start
    snippet_fallback = OptimalXBridge._extract_snippet(text, query="Unicorn", context_chars=15)
    assert "The quick brown" in snippet_fallback

    # Wildcard
    snippet_wildcard = OptimalXBridge._extract_snippet(text, query="*", context_chars=15)
    assert "The quick brown" in snippet_wildcard

    # Empty content
    assert OptimalXBridge._extract_snippet("", query="anything") == ""


def test_search_notes_db_offline_keyword(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Search for Kokoro (in note 10 content)
    results = bridge.search_notes(query="Kokoro", prefer_offline=True)
    assert len(results) == 1
    assert results[0]["subfolder_id"] == 10
    assert results[0]["note_id"] == 100
    assert results[0]["location"] == "Inventions > Autonomous Agent"
    assert "Kokoro" in results[0]["text"]
    assert results[0]["source"] == "db_sqlite"


def test_search_notes_db_offline_title_match(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Title match should score highest
    results = bridge.search_notes(query="Workout Plan", prefer_offline=True)
    assert len(results) >= 1
    assert results[0]["subfolder_name"] == "Workout Plan"
    assert results[0]["score"] == 1.0


def test_search_notes_privacy_filters(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Note 102 has 'passwords' but is ai_blind = 1
    results = bridge.search_notes(query="passwords", prefer_offline=True)
    assert len(results) == 0

    # Note 103 has 'deleted' but deleted_at is set
    results_del = bridge.search_notes(query="deleted", prefer_offline=True)
    assert len(results_del) == 0


def test_search_notes_parent_folder_filter(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Search wildcard across all
    all_results = bridge.search_notes(query="*", limit=10, prefer_offline=True)
    assert len(all_results) == 3  # Note 10, Note 11, Note 14 (Note 12 is blind, Note 13 is deleted)

    # Limit to Inventions parent folder
    inv_results = bridge.search_notes(query="*", parent_folder="Inventions", prefer_offline=True)
    assert len(inv_results) == 2
    assert all(r["parent_name"] == "Inventions" for r in inv_results)


def test_read_note_by_subfolder_id(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    note = bridge.read_note(subfolder_id=10)
    assert note is not None
    assert "error" not in note
    assert note["title"] == "Autonomous Agent"
    assert note["parent_name"] == "Inventions"
    assert "Bubblewrap sandbox" in note["content"]
    assert note["ai_locked"] is False
    assert note["ai_blind"] is False
    assert note["word_count"] > 5


def test_read_note_by_title(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    note = bridge.read_note(title="Workout Plan")
    assert note is not None
    assert note["subfolder_id"] == 11
    assert note["ai_locked"] is True
    assert "squats 5x5" in note["content"]


def test_read_note_ai_blind_rejected(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Subfolder 12 is ai_blind = 1
    res = bridge.read_note(subfolder_id=12)
    assert res is not None
    assert "error" in res
    assert res["ai_blind"] is True
    assert "content" not in res  # Privacy: must never leak private note contents


def test_read_note_deleted_not_found(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Subfolder 13 is deleted
    res = bridge.read_note(subfolder_id=13)
    assert res is None

    # Non-existent subfolder
    assert bridge.read_note(subfolder_id=99999) is None


def test_search_notes_online_tier1():
    bridge = OptimalXBridge()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "chunks": [
            {
                "location": "Inventions > Smart Pump",
                "text": "Solar powered micro irrigation pump",
                "score": 0.89,
                "chunkType": "note",
                "startLine": 1,
                "endLine": 4,
                "objectId": 201,
                "subfolderId": 35,
            }
        ]
    }

    with patch("requests.post", return_value=mock_resp):
        results = bridge.search_notes(query="solar pump", limit=5, prefer_offline=False)
        assert len(results) == 1
        assert results[0]["source"] == "http_semantic"
        assert results[0]["score"] == 0.89
        assert results[0]["location"] == "Inventions > Smart Pump"


def test_search_notes_online_fallback_to_tier2(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Simulate network failure to port 7373
    with patch("requests.post", side_effect=Exception("Connection refused")):
        results = bridge.search_notes(query="Kokoro", limit=5, prefer_offline=False)
        assert len(results) == 1
        assert results[0]["source"] == "db_sqlite"
        assert results[0]["subfolder_id"] == 10


def test_live_optimalx_db_if_available():
    """Verify read-only inspection against live host database ~/OptimalX/data.db if it exists."""
    live_db = Path.home() / "OptimalX" / "data.db"
    if not live_db.exists():
        pytest.skip("Live OptimalX database not present at ~/OptimalX/data.db")

    bridge = OptimalXBridge(db_path=str(live_db))
    # Query wildcard to get recent notes
    notes = bridge.search_notes(query="*", limit=3, prefer_offline=True)
    assert isinstance(notes, list)
    if notes:
        first = notes[0]
        assert "location" in first
        assert "subfolder_id" in first
        assert first["source"] == "db_sqlite"

        # Read the first note
        full_note = bridge.read_note(subfolder_id=first["subfolder_id"])
        if full_note and "error" not in full_note:
            assert "content" in full_note
            assert "title" in full_note


def test_list_folders_all_with_subfolders(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)
    folders = bridge.list_folders(include_subfolders=True)

    assert len(folders) == 3
    # Check Inventions parent
    inv = next(f for f in folders if f["name"] == "Inventions")
    assert inv["subfolder_count"] == 2  # Autonomous Agent (s10) and the list (s14)
    sub_names = [s["name"] for s in inv["subfolders"]]
    assert "Autonomous Agent" in sub_names
    assert "the list" in sub_names

    # Check Work parent has soft-deleted folder excluded
    work = next(f for f in folders if f["name"] == "Work")
    assert work["subfolder_count"] == 1  # Secret Passwords (s12); s13 was deleted
    assert work["subfolders"][0]["name"] == "Secret Passwords"
    assert work["subfolders"][0]["is_private"] is True  # ai_blind=1 flag set


def test_list_folders_specific_parent(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)
    folders = bridge.list_folders(parent_folder_id=2, include_subfolders=True)

    assert len(folders) == 1
    assert folders[0]["name"] == "Personal"
    assert folders[0]["subfolder_count"] == 1
    assert folders[0]["subfolders"][0]["name"] == "Workout Plan"
    assert folders[0]["subfolders"][0]["ai_locked"] is True


def test_list_folders_without_subfolders(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)
    folders = bridge.list_folders(include_subfolders=False)

    assert len(folders) == 3
    for f in folders:
        assert "subfolders" not in f
        assert "subfolder_count" in f
        assert isinstance(f["subfolder_count"], int)


def test_list_folders_with_query_filter(temp_optimalx_db):
    bridge = OptimalXBridge(db_path=temp_optimalx_db)
    # Query matching parent name
    folders = bridge.list_folders(query="Inventions")
    assert len(folders) == 1
    assert folders[0]["name"] == "Inventions"

    # Query matching subfolder name
    folders_sub = bridge.list_folders(query="Workout")
    assert len(folders_sub) == 1
    assert folders_sub[0]["name"] == "Personal"
    assert any(s["name"] == "Workout Plan" for s in folders_sub[0]["subfolders"])


def test_list_notex_docs_with_manifest(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)
    res = bridge.list_notex_docs(subfolder_id=14)

    assert "error" not in res
    assert res["project_name"] == "the list"
    assert res["global_id"] == "s14"
    assert res["notex_enabled"] is True
    assert res["exists_on_disk"] is True
    assert len(res["docs"]) == 3
    assert res["docs"][0]["path"] == "Canon.md"
    assert res["active_doc_path"] == "Canon.md"


def test_list_notex_docs_fallback_scan(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)
    # Remove manifest.json to force filesystem scanning fallback
    manifest_file = Path(temp_notex_tree) / "s14" / ".notex" / "manifest.json"
    if manifest_file.exists():
        manifest_file.unlink()

    res = bridge.list_notex_docs(subfolder_id=14)
    assert "error" not in res
    assert res["exists_on_disk"] is True
    assert len(res["docs"]) == 3
    paths = [d["path"] for d in res["docs"]]
    assert "Canon.md" in paths
    assert "Outline.md" in paths
    assert "Sections/Chapter 1.md" in paths


def test_list_notex_docs_missing_or_disabled(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)

    # Subfolder 10 does not have a notex directory on disk
    res = bridge.list_notex_docs(subfolder_id=10)
    assert res.get("exists_on_disk") is False
    assert "not found on disk" in res.get("error", "")

    # Non-existent subfolder
    res_none = bridge.list_notex_docs(subfolder_id=9999)
    assert "not found" in res_none.get("error", "")


def test_read_notex_doc_full_and_sliced(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)

    # Full read
    full = bridge.read_notex_doc(subfolder_id=14, doc_path="Canon.md")
    assert "error" not in full
    assert full["project_name"] == "the list"
    assert full["total_lines"] == 5
    assert full["line_count"] == 5
    assert "Rule of Law" in full["content"]

    # Sliced read (lines 2 to 4)
    sliced = bridge.read_notex_doc(subfolder_id=14, doc_path="Canon.md", start_line=2, end_line=4)
    assert "error" not in sliced
    assert sliced["start_line"] == 2
    assert sliced["end_line"] == 4
    assert sliced["line_count"] == 3
    assert "Line 2: Rule of Law" in sliced["content"]
    assert "Line 4: Sandbox Isolation" in sliced["content"]
    assert "Project Canon" not in sliced["content"]
    assert "Line 5: Final Directives" not in sliced["content"]

    # Nested document read
    nested = bridge.read_notex_doc(subfolder_id=14, doc_path="Sections/Chapter 1.md")
    assert "error" not in nested
    assert "Deep dive into autonomous reasoning" in nested["content"]


def test_read_notex_doc_security_path_traversal(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)

    # Attempt ../ escape
    res = bridge.read_notex_doc(subfolder_id=14, doc_path="../../etc/passwd")
    assert "error" in res

    # Attempt .notex private access
    res_dot = bridge.read_notex_doc(subfolder_id=14, doc_path=".notex/manifest.json")
    assert "error" in res_dot


def test_read_notex_doc_non_markdown_blocked(temp_optimalx_db, temp_notex_tree):
    bridge = OptimalXBridge(db_path=temp_optimalx_db, notex_dir=temp_notex_tree)

    # Create dummy text file
    txt_file = Path(temp_notex_tree) / "s14" / "notes.txt"
    with open(txt_file, "w") as f:
        f.write("Secret data")

    res = bridge.read_notex_doc(subfolder_id=14, doc_path="notes.txt")
    assert "error" in res
    assert "markdown (.md)" in res["error"]


def test_live_optimalx_list_folders_and_notex_if_available():
    """Verify live host database folder hierarchy and NoteX reader."""
    live_db = Path.home() / "OptimalX" / "data.db"
    live_notex = Path.home() / "OptimalX" / "notex"
    if not live_db.exists() or not live_notex.exists():
        pytest.skip("Live OptimalX not available on host")

    bridge = OptimalXBridge(db_path=str(live_db), notex_dir=str(live_notex))

    # Test list_folders on host
    folders = bridge.list_folders(include_subfolders=True)
    assert len(folders) > 0
    inventions = next((f for f in folders if "invention" in f["name"].lower()), None)
    if inventions:
        assert inventions["subfolder_count"] >= 1

    # Test NoteX listing on subfolder 503 ('The list booklet')
    notex_docs = bridge.list_notex_docs(subfolder_id=503)
    if notex_docs.get("exists_on_disk"):
        assert len(notex_docs["docs"]) >= 1
        canon = next((d for d in notex_docs["docs"] if "canon" in d["path"].lower()), None)
        if canon:
            content = bridge.read_notex_doc(subfolder_id=503, doc_path=canon["path"])
            assert "error" not in content
            assert content["line_count"] > 0


# =============================================================================
# Phase 11.3: Safe Mutation & Diff Review Engine Tests
# =============================================================================

def test_write_note_empty_note_auto_apply(temp_optimalx_db):
    """Empty note writes auto-apply directly to disk with an initial checkpoint."""
    with sqlite3.connect(temp_optimalx_db) as conn:
        conn.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id) VALUES (15, 1, 'Empty Draft', 1000, 2000, 's15')")
        conn.execute("INSERT INTO notes (id, subfolder_id, content, created_at, updated_at, global_id, content_hash) VALUES (105, 15, '', 1000, 2000, 'n105', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')")
        conn.commit()

    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    content = "# Initial Draft\n\nThis is a brand new draft note."
    res = bridge.write_note(content=content, subfolder_id=15)

    assert res["status"] == "written"
    assert res["kind"] == "written"
    assert res["subfolder_id"] == 15
    assert res["subfolder_name"] == "Empty Draft"
    assert res["parent_name"] == "Inventions"
    assert res["checkpoint_id"] is not None

    # Verify notes table
    note = bridge.read_note(subfolder_id=15)
    assert note["content"] == content
    assert note["content_hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Verify content_checkpoints table
    cps = bridge.get_note_checkpoints(subfolder_id=15)
    assert len(cps) >= 1
    assert cps[-1]["content_hash"] == note["content_hash"]
    assert cps[-1]["author"] == "eidos"


def test_write_note_existing_content_diff_review_queue(temp_optimalx_db):
    """Writing to note with existing content queues a proposal into Diff Review without modifying disk."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    initial_note = bridge.read_note(subfolder_id=10)
    initial_content = initial_note["content"]

    proposed_addition = initial_content + "\n\nAdded Phase 11 specs for OptimalX bidirectional sync."
    res = bridge.write_note(content=proposed_addition, subfolder_id=10, mode="auto")

    assert res["status"] == "queued"
    assert res["kind"] == "queued"
    assert res["subfolder_id"] == 10
    assert res["subfolder_name"] == "Autonomous Agent"
    assert res["change_set_id"] is not None
    assert res["item_id"] is not None
    assert res["pending_count"] == 1
    assert "Added Phase 11" in res["unified_diff"]

    # Verify disk content was NOT modified
    after_note = bridge.read_note(subfolder_id=10)
    assert after_note["content"] == initial_content
    assert after_note["has_pending_review"] is True

    # Verify pending change item in database
    pending_items = bridge.list_pending_reviews(subfolder_id=10)
    assert len(pending_items) == 1
    item = pending_items[0]
    assert item["item_id"] == res["item_id"]
    assert item["status"] == "pending"
    assert item["proposed_hash"] == hashlib.sha256(proposed_addition.encode("utf-8")).hexdigest()
    assert "Added Phase 11" in item["unified_diff"]


def test_write_note_no_change_detected(temp_optimalx_db):
    """Writing identical content returns status='no_change' without creating pending items."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    current_note = bridge.read_note(subfolder_id=10)
    res = bridge.write_note(content=current_note["content"], subfolder_id=10)

    assert res["status"] == "no_change"
    assert res["kind"] == "no_change"
    assert len(bridge.list_pending_reviews(subfolder_id=10)) == 0


def test_write_note_ai_locked_rejection(temp_optimalx_db):
    """Notes with ai_locked=1 strictly reject modification attempts."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    res = bridge.write_note(content="New workout routine", subfolder_id=11)

    assert res["status"] == "locked"
    assert res["kind"] == "failed"
    assert "ai_locked=1" in res["error"]

    # Verify content untouched
    note = bridge.read_note(subfolder_id=11)
    assert "squats 5x5" in note["content"]


def test_write_note_ai_blind_rejection(temp_optimalx_db):
    """Notes with ai_blind=1 strictly reject modification attempts."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    res = bridge.write_note(content="Overwriting password note", subfolder_id=12)

    assert res["status"] == "private"
    assert res["kind"] == "failed"
    assert "ai_blind=1" in res["error"]


def test_write_note_create_new_folder_and_subfolder(temp_optimalx_db):
    """Auto-creates parent folder and subfolder if non-existent, then directly writes initial content."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    content = "# Project Daedalus\nAutonomous aerial mapping framework."
    res = bridge.write_note(
        content=content,
        subfolder_title="Project Daedalus",
        parent_name="Robotics Lab",
    )

    assert res["status"] == "written"
    assert res["subfolder_name"] == "Project Daedalus"
    assert res["parent_name"] == "Robotics Lab"

    # Verify folder hierarchy created
    folders = bridge.list_folders(include_subfolders=True)
    robotics = next((f for f in folders if f["name"] == "Robotics Lab"), None)
    assert robotics is not None
    assert len(robotics["subfolders"]) == 1
    assert robotics["subfolders"][0]["name"] == "Project Daedalus"

    # Verify note content
    new_subfolder_id = robotics["subfolders"][0]["id"]
    note = bridge.read_note(subfolder_id=new_subfolder_id)
    assert note["content"] == content
    assert note["content_hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_write_note_direct_overwrite_mode(temp_optimalx_db):
    """Direct overwrite mode bypasses Diff Review and updates existing note immediately."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    new_content = "# Completely Overwritten Architecture\nNew microkernel core."
    res = bridge.write_note(content=new_content, subfolder_id=10, mode="direct_overwrite")

    assert res["status"] == "written"
    assert res["kind"] == "written"

    # Verify content updated immediately
    note = bridge.read_note(subfolder_id=10)
    assert note["content"] == new_content

    # Verify patch recorded
    patches = bridge.get_note_patches(subfolder_id=10)
    assert len(patches) >= 1
    assert "Completely Overwritten" in patches[-1]["unified_diff"]


def test_write_note_append_mode(temp_optimalx_db):
    """Append mode queues proposal by default, or writes directly if allow_direct_append=True."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)
    initial_content = bridge.read_note(subfolder_id=10)["content"]

    # 1. Append with allow_direct_append=False -> queues review
    res_queued = bridge.write_note(
        content="Item 4: Quantum sensor",
        subfolder_id=10,
        mode="append",
        allow_direct_append=False,
    )
    assert res_queued["status"] == "queued"
    assert bridge.read_note(subfolder_id=10)["content"] == initial_content

    # Clean up review queue
    bridge.reject_pending_review(res_queued["item_id"])

    # 2. Append with allow_direct_append=True -> direct write
    res_direct = bridge.write_note(
        content="Item 4: Quantum sensor",
        subfolder_id=10,
        mode="append",
        allow_direct_append=True,
    )
    assert res_direct["status"] == "written"
    expected = f"{initial_content}\n\nItem 4: Quantum sensor"
    assert bridge.read_note(subfolder_id=10)["content"] == expected


def test_checkpoint_retention_pruning(temp_optimalx_db):
    """Checkpoints table prunes older revisions keeping sequence 0 baseline and max 5 items."""
    with sqlite3.connect(temp_optimalx_db) as conn:
        conn.execute("INSERT INTO subfolders (id, parent_folder_id, name, created_at, updated_at, global_id) VALUES (15, 1, 'Empty Draft', 1000, 2000, 's15')")
        conn.execute("INSERT INTO notes (id, subfolder_id, content, created_at, updated_at, global_id, content_hash) VALUES (105, 15, '', 1000, 2000, 'n105', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')")
        conn.commit()

    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # Perform 7 sequential overwrites
    for i in range(1, 8):
        bridge.write_note(
            content=f"Revision number {i}\nBody content {i}",
            subfolder_id=15,
            mode="direct_overwrite",
            label=f"Rev {i}",
        )

    cps = bridge.get_note_checkpoints(subfolder_id=15)
    assert len(cps) == 5

    # Sequence 0 baseline must be preserved
    assert cps[0]["sequence"] == 0
    # Sequences must be strictly increasing
    sequences = [c["sequence"] for c in cps]
    assert sequences == sorted(sequences)


def test_list_and_accept_pending_review(temp_optimalx_db):
    """Accepting a pending Diff Review item commits it to notes and updates review status."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    proposed = "Approved architecture upgrade with WebGPU support."
    queue_res = bridge.write_note(content=proposed, subfolder_id=10, mode="auto")
    item_id = queue_res["item_id"]

    # List pending reviews
    pending = bridge.list_pending_reviews(subfolder_id=10)
    assert len(pending) == 1
    assert pending[0]["item_id"] == item_id
    assert "WebGPU support" in pending[0]["unified_diff"]

    # Accept the proposal
    accept_res = bridge.accept_pending_review(item_id)
    assert accept_res["status"] == "accepted"
    assert accept_res["subfolder_id"] == 10

    # Verify disk content updated to proposed
    note = bridge.read_note(subfolder_id=10)
    assert note["content"] == proposed
    assert note["has_pending_review"] is False

    # Pending reviews queue should now be empty
    assert len(bridge.list_pending_reviews(subfolder_id=10)) == 0


def test_reject_pending_review(temp_optimalx_db):
    """Rejecting a pending Diff Review item leaves notes content unchanged and marks item rejected."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    initial_content = bridge.read_note(subfolder_id=10)["content"]
    proposed = "Unwanted proposal that user rejects."
    queue_res = bridge.write_note(content=proposed, subfolder_id=10, mode="auto")
    item_id = queue_res["item_id"]

    # Reject the proposal
    reject_res = bridge.reject_pending_review(item_id)
    assert reject_res["status"] == "rejected"

    # Verify disk content remained unchanged
    note = bridge.read_note(subfolder_id=10)
    assert note["content"] == initial_content
    assert note["has_pending_review"] is False

    # Review queue empty
    assert len(bridge.list_pending_reviews(subfolder_id=10)) == 0


def test_concurrent_modification_rejection_on_accept(temp_optimalx_db):
    """Accepting a proposal fails if note was modified behind the scenes (concurrent change check)."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    queue_res = bridge.write_note(
        content="Proposal based on initial note",
        subfolder_id=10,
        mode="auto",
    )
    item_id = queue_res["item_id"]

    # Simulate external user edit directly on disk note
    with sqlite3.connect(temp_optimalx_db) as conn:
        conn.execute(
            "UPDATE notes SET content = 'External user edit', content_hash = 'different_hash' WHERE subfolder_id = 10"
        )
        conn.commit()

    # Attempt to accept proposal
    res = bridge.accept_pending_review(item_id)
    assert res["status"] == "concurrent_change"
    assert "Concurrent modification detected" in res["error"]


# =============================================================================
# Phase 11.6: End-to-End Lifecycle & Regression Suite
# =============================================================================

def test_optimalx_full_pipeline_lifecycle_end_to_end(temp_optimalx_db):
    """Full lifecycle test covering search, read, NoteX, diff review, direct write, recall stream, and job processor."""
    bridge = OptimalXBridge(db_path=temp_optimalx_db)

    # 1. Search notes (offline fallback)
    results = bridge.search_notes(query="Autonomous Agent", prefer_offline=True)
    assert len(results) >= 1
    target = next(r for r in results if r["subfolder_id"] == 10)
    assert target["subfolder_name"] == "Autonomous Agent"
    assert "Inventions" in target["location"]

    # 2. Read full note content
    note_data = bridge.read_note(subfolder_id=10)
    assert note_data is not None
    assert "error" not in note_data
    assert "autonomous agent" in note_data["content"].lower()
    original_hash = note_data["content_hash"]

    # 3. Safe mutation on existing note -> triggers diff review queue
    proposed_content = note_data["content"] + "\n\nAdded: Milestone 11.6 Full Integration Passed."
    write_res = bridge.write_note(content=proposed_content, subfolder_id=10)
    assert write_res["status"] == "queued"
    assert write_res["kind"] == "queued"
    item_id = write_res["item_id"]

    # Verify pending review entry
    pending_items = bridge.list_pending_reviews(subfolder_id=10)
    assert len(pending_items) == 1
    assert pending_items[0]["item_id"] == item_id
    assert "+Added: Milestone 11.6 Full Integration Passed." in pending_items[0]["unified_diff"]

    # 4. Accept the pending review
    accept_res = bridge.accept_pending_review(item_id)
    assert accept_res["status"] == "accepted"

    # 5. Verify note updated on disk and hash changed
    updated_note = bridge.read_note(subfolder_id=10)
    assert updated_note["content"] == proposed_content
    assert updated_note["content_hash"] != original_hash
    assert updated_note["content_hash"] == hashlib.sha256(proposed_content.encode("utf-8")).hexdigest()

    # 6. Verify checkpoint created
    checkpoints = bridge.get_note_checkpoints(subfolder_id=10)
    assert len(checkpoints) >= 2
    assert checkpoints[-1]["content_hash"] == updated_note["content_hash"]

    # 7. Create a brand new note in a new subfolder -> auto-applies directly
    new_subfolder_res = bridge.write_note(
        content="# Quantum Photonic Router\nInitial project notes.",
        subfolder_title="Quantum Photonic Router",
        parent_name="Inventions",
    )
    assert new_subfolder_res["status"] == "written"
    assert new_subfolder_res["kind"] == "written"
    new_subfolder_id = new_subfolder_res["subfolder_id"]

    new_note = bridge.read_note(subfolder_id=new_subfolder_id)
    assert new_note is not None
    assert "Quantum Photonic Router" in new_note["content"]
    new_cps = bridge.get_note_checkpoints(subfolder_id=new_subfolder_id)
    assert len(new_cps) == 2
    assert new_cps[0]["sequence"] == 0
    assert new_cps[1]["sequence"] == 1
    assert new_cps[1]["content_hash"] == new_note["content_hash"]

    # 8. Privacy invariants: ai_blind note must remain completely invisible
    blind_res = bridge.search_notes(query="Bank account pin", prefer_offline=True)
    assert len(blind_res) == 0

    blind_read = bridge.read_note(subfolder_id=12)
    assert blind_read is not None
    assert blind_read.get("ai_blind") is True

    blind_write = bridge.write_note(content="Overwrite secret", subfolder_id=12)
    assert blind_write["status"] == "private"

    # 9. Read-only invariants: ai_locked note must reject write attempts
    locked_write = bridge.write_note(content="Overwrite locked notes", subfolder_id=11)
    assert locked_write["status"] == "locked"

    # 10. Soft-deleted invariants: soft-deleted notes are ignored
    deleted_read = bridge.read_note(subfolder_id=13)
    assert deleted_read is None



