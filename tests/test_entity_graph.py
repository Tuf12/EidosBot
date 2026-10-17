"""Unit tests for Entity Linking & Relational Knowledge Graph (EntityExtractor, GraphStore, and Hybrid GAR)."""

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

import pytest

from eidos.memory.entity_extractor import EntityExtractor
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.graph_store import GraphStore
from eidos.memory.types import (
    ENTITY_TAXONOMY,
    ENTITY_TYPE_COMMAND,
    ENTITY_TYPE_DEVICE,
    ENTITY_TYPE_DOMAIN,
    ENTITY_TYPE_PATH,
    ENTITY_TYPE_PROJECT,
    ENTITY_TYPE_WINDOW,
    VALID_ENTITY_TYPES,
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    ExtractedEntity,
    StepRecord,
)


def test_entity_taxonomy_and_schema_validation():
    """Verify entity taxonomy registry, VALID_ENTITY_TYPES, and ExtractedEntity type constraint."""
    from pydantic import ValidationError

    # Verify taxonomy registry has all six valid types with metadata
    assert len(VALID_ENTITY_TYPES) == 6
    assert set(VALID_ENTITY_TYPES) == set(ENTITY_TAXONOMY.keys())

    for etype in VALID_ENTITY_TYPES:
        info = ENTITY_TAXONOMY[etype]
        assert "description" in info and len(info["description"]) > 0
        assert "prefix" in info and len(info["prefix"]) > 0
        assert "examples" in info and len(info["examples"]) > 0

    # Verify valid ExtractedEntity creation
    entity = ExtractedEntity(
        entity_id="cmd:xrandr",
        entity_type=ENTITY_TYPE_COMMAND,
        name="xrandr",
        normalized_name="xrandr",
    )
    assert entity.entity_type == "command"

    # Verify that invalid entity_type raises ValidationError
    with pytest.raises(ValidationError):
        ExtractedEntity(
            entity_id="invalid:test",
            entity_type="invalid_type",  # type: ignore
            name="test",
            normalized_name="test",
        )


def test_entity_extractor_commands():
    """Verify EntityExtractor correctly isolates executed binaries from complex shell commands."""
    extractor = EntityExtractor()

    rec = EpisodeRecord(
        episode_id="ep_test_cmd",
        task_goal="Inspect and adjust monitor settings",
        trajectory=[
            StepRecord(
                step=1,
                tool_calls=[
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "sudo xrandr --query | grep -i 'connected primary' && echo 'done'"
                        },
                    },
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "FOO=1 nohup pactl set-sink-volume HDMI-0 50% || true"
                        },
                    },
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "bash -c 'find ~/Downloads -name \"*.png\" | sort'"
                        },
                    },
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "find /var/log -name '*.log' -exec rm -f {} + && xargs -0 gzip"
                        },
                    },
                ],
            )
        ],
    )

    entities = extractor.extract_from_episode(rec)
    cmd_entities = {e.name for e in entities if e.entity_type == ENTITY_TYPE_COMMAND}

    assert "xrandr" in cmd_entities
    assert "grep" in cmd_entities
    assert "pactl" in cmd_entities
    assert "find" in cmd_entities
    assert "sort" in cmd_entities
    assert "rm" in cmd_entities
    assert "gzip" in cmd_entities

    # Shell builtins, keywords, and wrappers should NOT be recorded as commands
    assert "echo" not in cmd_entities
    assert "sudo" not in cmd_entities
    assert "nohup" not in cmd_entities
    assert "true" not in cmd_entities
    assert "bash" not in cmd_entities
    assert "sh" not in cmd_entities
    assert "xargs" not in cmd_entities


def test_entity_extractor_paths_and_devices():
    """Verify extraction of file paths, display connectors, audio sinks, and active windows."""
    extractor = EntityExtractor()

    rec = EpisodeRecord(
        episode_id="ep_test_paths",
        task_goal="Update /etc/pulse/default.pa for HDMI-0 output and check /etc",
        trajectory=[
            StepRecord(
                step=1,
                state_snapshot={
                    "active_window_class": "Google-chrome",
                    "cwd": "/home/tuf/CursorProjects/OptimalX",
                },
                tool_calls=[
                    {
                        "name": "edit_file",
                        "arguments": {"path": "/etc/pulse/default.pa"},
                    },
                    {
                        "name": "write_file",
                        "arguments": {"path": "~/Downloads/report.json"},
                    },
                    {
                        "name": "web_fetch",
                        "arguments": {"url": "https://github.com/eidos/repo/issues/42"},
                    },
                ],
                tool_observations=[
                    {
                        "stdout": "Sink alsa_output.pci-0000_00_1f.3.analog-stereo switched to HDMI-0. Wrote log to /var/log/audio.log with pipewire pulse daemon."
                    }
                ],
            )
        ],
    )

    entities = extractor.extract_from_episode(rec)
    types_found = {e.entity_type for e in entities}

    assert ENTITY_TYPE_PATH in types_found
    assert ENTITY_TYPE_DEVICE in types_found
    assert ENTITY_TYPE_PROJECT in types_found
    assert ENTITY_TYPE_WINDOW in types_found
    assert ENTITY_TYPE_DOMAIN in types_found

    path_names = {e.name for e in entities if e.entity_type == ENTITY_TYPE_PATH}
    assert "/etc/pulse/default.pa" in path_names
    assert "~/Downloads/report.json" in path_names
    assert "/etc" in path_names  # Single component path in task_goal
    assert "/var/log/audio.log" in path_names  # Path extracted from tool_observations

    device_names = {e.name for e in entities if e.entity_type == ENTITY_TYPE_DEVICE}
    assert any("HDMI-0" in d for d in device_names)
    assert any("alsa_output" in d for d in device_names)
    # Ensure words like 'pulse' and 'pipewire' are NOT falsely tagged as hardware devices
    assert "pulse" not in device_names
    assert "pipewire" not in device_names

    project_names = {e.name for e in entities if e.entity_type == ENTITY_TYPE_PROJECT}
    assert "OptimalX" in project_names

    window_names = {e.name for e in entities if e.entity_type == ENTITY_TYPE_WINDOW}
    assert "Google-chrome" in window_names

    domain_names = {e.name for e in entities if e.entity_type == ENTITY_TYPE_DOMAIN}
    assert "github.com" in domain_names


def test_entity_extractor_from_query():
    """Verify entity extraction from explicit tags and natural language query text."""
    extractor = EntityExtractor()

    # 1. Explicit tags
    q1 = "device:HDMI-0 cmd:pactl project:OptimalX path:/etc/fstab"
    ents1 = extractor.extract_from_query(q1)
    ent_map1 = {e.entity_type: e.name for e in ents1}

    assert ent_map1.get(ENTITY_TYPE_DEVICE) == "HDMI-0"
    assert ent_map1.get(ENTITY_TYPE_COMMAND) == "pactl"
    assert ent_map1.get(ENTITY_TYPE_PROJECT) == "OptimalX"
    assert ent_map1.get(ENTITY_TYPE_PATH) == "/etc/fstab"

    # Explicit tags with aliases
    q_alias = "dev:DP-1 file:~/report.json proj:EidosBot app:Code url:https://example.com"
    ents_alias = extractor.extract_from_query(q_alias)
    ent_map_alias = {e.entity_type: e.name for e in ents_alias}
    assert ent_map_alias.get(ENTITY_TYPE_DEVICE) == "DP-1"
    assert ent_map_alias.get(ENTITY_TYPE_PATH) == "~/report.json"
    assert ent_map_alias.get(ENTITY_TYPE_PROJECT) == "EidosBot"
    assert ent_map_alias.get(ENTITY_TYPE_WINDOW) == "Code"
    assert ent_map_alias.get(ENTITY_TYPE_DOMAIN) == "https://example.com" or "example.com" in ent_map_alias.values()

    # 2. Implicit natural language
    q2 = "How do we lower brightness on HDMI-0 using xrandr in ~/Downloads?"
    ents2 = extractor.extract_from_query(q2)
    ent_map2 = {e.entity_type: e.name for e in ents2}

    assert ent_map2.get(ENTITY_TYPE_DEVICE) == "HDMI-0"
    assert ent_map2.get(ENTITY_TYPE_COMMAND) == "xrandr"
    assert ent_map2.get(ENTITY_TYPE_PATH) == "~/Downloads"


def test_entity_extractor_tool_calls_and_wrappers():
    """Verify extraction handles wrappers, share_file, and download_file cleanly."""
    extractor = EntityExtractor()

    rec = EpisodeRecord(
        episode_id="ep_wrappers",
        task_goal="Download and share dataset",
        trajectory=[
            StepRecord(
                step=1,
                tool_calls=[
                    {
                        "name": "execute_shell",
                        "arguments": {
                            "command": "env VAR=1 nice -n 10 /usr/bin/curl -LO https://cdn.example.org/data.tar.gz"
                        },
                    },
                    {
                        "name": "download_file",
                        "arguments": {
                            "url": "https://cdn.example.org/data.tar.gz",
                            "destination": "~/Downloads/data.tar.gz",
                        },
                    },
                    {
                        "name": "share_file",
                        "arguments": {"path": "~/Downloads/data.tar.gz"},
                    },
                ],
            )
        ],
    )

    entities = extractor.extract_from_episode(rec)
    cmd_entities = {e.name for e in entities if e.entity_type == ENTITY_TYPE_COMMAND}
    path_entities = {e.name for e in entities if e.entity_type == ENTITY_TYPE_PATH}
    domain_entities = {e.name for e in entities if e.entity_type == ENTITY_TYPE_DOMAIN}

    assert "curl" in cmd_entities
    assert "~/Downloads/data.tar.gz" in path_entities
    assert "cdn.example.org" in domain_entities


def test_graph_store_linking_and_queries():
    """Verify GraphStore creates tables, links entities, and performs relational queries."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)
        graph = store.graph_store

        assert graph.count_entities() == 0
        assert graph.count_links() == 0

        # Insert base episode row to satisfy foreign key constraint
        store._conn.execute(
            "INSERT INTO episodes (episode_id, task_goal, outcome, timestamp) VALUES (?, ?, ?, ?)",
            ("ep_001", "Query pactl on HDMI-0", "success", "2026-10-15T12:00:00Z"),
        )
        store._conn.commit()

        entities = [
            ExtractedEntity(
                entity_id="cmd:pactl",
                entity_type=ENTITY_TYPE_COMMAND,
                name="pactl",
                normalized_name="pactl",
                role="invoked_command",
            ),
            ExtractedEntity(
                entity_id="device:hdmi-0",
                entity_type=ENTITY_TYPE_DEVICE,
                name="HDMI-0",
                normalized_name="hdmi-0",
                role="hardware_device",
            ),
        ]

        graph.link_episode_entities("ep_001", entities, timestamp="2026-10-15T12:00:00Z")

        assert graph.count_entities() == 2
        assert graph.count_links() == 2

        # Query episodes by entity
        eps_pactl = graph.find_episodes_by_entity("cmd:pactl")
        assert "ep_001" in eps_pactl

        eps_device = graph.find_episodes_by_entity("hdmi-0", entity_type=ENTITY_TYPE_DEVICE)
        assert "ep_001" in eps_device

        # Get entities for episode
        fetched = graph.get_entities_for_episode("ep_001")
        assert len(fetched) == 2
        names = {f.name for f in fetched}
        assert "pactl" in names
        assert "HDMI-0" in names

        store.close()


def test_entity_cooccurrences_weights():
    """Verify that co-occurring entities build weighted graph edges."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)
        graph = store.graph_store

        # Insert base episode rows to satisfy foreign key constraint
        store._conn.execute(
            "INSERT INTO episodes (episode_id, task_goal, outcome, timestamp) VALUES (?, ?, ?, ?)",
            ("ep_audio_1", "Audio task 1", "success", "2026-10-15T12:00:00Z"),
        )
        store._conn.execute(
            "INSERT INTO episodes (episode_id, task_goal, outcome, timestamp) VALUES (?, ?, ?, ?)",
            ("ep_audio_2", "Audio task 2", "success", "2026-10-15T12:00:00Z"),
        )
        store._conn.commit()

        ents = [
            ExtractedEntity(
                entity_id="cmd:pactl",
                entity_type=ENTITY_TYPE_COMMAND,
                name="pactl",
                normalized_name="pactl",
            ),
            ExtractedEntity(
                entity_id="device:hdmi-0",
                entity_type=ENTITY_TYPE_DEVICE,
                name="HDMI-0",
                normalized_name="hdmi-0",
            ),
        ]

        # Link for two distinct episodes
        graph.link_episode_entities("ep_audio_1", ents)
        graph.link_episode_entities("ep_audio_2", ents)

        related = graph.get_related_entities("cmd:pactl")
        assert len(related) >= 1
        assert related[0]["entity_id"] == "device:hdmi-0"
        assert related[0]["weight"] == 2

        store.close()


test_entity_cooccurrences = test_entity_cooccurrences_weights


def test_cascade_deletion():
    """Verify that deleting an episode cascades and removes its entity links."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        rec = EpisodeRecord(
            episode_id="ep_delete_me",
            task_goal="Set sink volume",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[
                        {"name": "execute_shell", "arguments": {"command": "pactl set-sink-volume HDMI-0 50%"}}
                    ],
                )
            ],
        )

        store.index_episode(rec)
        assert store.count() == 1
        assert store.graph_store.count_links() >= 1

        # Delete episode
        deleted = store.delete_episode("ep_delete_me")
        assert deleted is True
        assert store.count() == 0
        assert store.graph_store.count_links() == 0

        store.close()


def test_hybrid_retrieval_ranking_advantage():
    """Verify that Hybrid Graph-Augmented Retrieval ranks an exact entity match over general vector similarity."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"

        # Embeddings return slight variations
        mock_emb = MagicMock()

        def mock_embed(text: str):
            # General vector similarity favors display brightness text over audio text
            t = text.lower()
            if "brightness" in t:
                return [0.8] * 768
            elif "audio" in t:
                return [0.4] * 768
            return [0.2] * 768

        mock_emb.embed_text.side_effect = mock_embed
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        # Episode 1: Vague task description ("General hardware configuration"), but exact pactl + HDMI-0 command
        ep1 = EpisodeRecord(
            episode_id="ep_exact_audio",
            task_goal="General hardware configuration and settings",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[
                        {
                            "name": "execute_shell",
                            "arguments": {"command": "pactl set-sink-volume HDMI-0 40%"},
                        }
                    ],
                )
            ],
            reflection=EpisodeReflection(summary="Set HDMI-0 audio volume to 40% via pactl."),
        )

        # Episode 2: Text goal mentions brightness and display on HDMI-0 (high vector similarity to query if query mentions HDMI-0)
        ep2 = EpisodeRecord(
            episode_id="ep_display_brightness",
            task_goal="Set screen display brightness for external monitor HDMI-0",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[
                        {
                            "name": "execute_shell",
                            "arguments": {"command": "xrandr --output HDMI-0 --brightness 0.75"},
                        }
                    ],
                )
            ],
            reflection=EpisodeReflection(summary="Screen brightness set with xrandr."),
        )

        store.index_episode(ep1)
        store.index_episode(ep2)

        # Query asks specifically for pactl volume on HDMI-0
        query = "How do we use pactl for audio volume on HDMI-0?"
        results = store.search(query, top_k=2)

        assert len(results) >= 1
        # Hybrid GAR should boost ep_exact_audio to rank #1 because it matched both 'pactl' and 'HDMI-0'!
        assert results[0].episode_id == "ep_exact_audio"
        assert len(results[0].matched_entities) > 0
        assert any("pactl" in m or "hdmi-0" in m for m in results[0].matched_entities)

        store.close()


def test_zero_entity_query_fallback():
    """Verify that queries without entities fall back cleanly to standard vector search."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.5] * 768
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        rec = EpisodeRecord(
            episode_id="ep_generic",
            task_goal="Review recent ideas and thoughts",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[StepRecord(step=1)],
        )
        store.index_episode(rec)

        results = store.search("Think about general concepts", top_k=1)
        assert len(results) == 1
        assert results[0].episode_id == "ep_generic"
        # Zero entities matched
        assert len(results[0].matched_entities) == 0

        store.close()


def test_backfill_entities():
    """Verify that backfill_entities scans episodes without links and populates the graph store."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768

        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        rec = EpisodeRecord(
            episode_id="ep_backfill",
            task_goal="Check disk space in /home/tuf",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "df -h /home/tuf"}}],
                )
            ],
        )

        store.index_episode(rec)
        assert store.graph_store.count_links() >= 1

        # Simulate missing links
        store.graph_store.delete_episode_links("ep_backfill")
        assert store.graph_store.count_links() == 0

        # Run backfill
        count = store.backfill_entities()
        assert count == 1
        assert store.graph_store.count_links() >= 1

        store.close()
