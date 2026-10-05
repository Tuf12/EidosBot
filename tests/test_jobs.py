"""Tests for EidosBot domain-agnostic Batch Job Runner (eidos/jobs/)."""

import csv
import json
import os
from pathlib import Path
import tempfile
import pytest

from eidos.jobs.runner import BatchJobRunner
from eidos.skills.manager import SkillManager


def test_batch_job_runner_3_items_lifecycle():
    """Verify Step 3 Task: 3 generic items transition from pending to completed in queue.json."""
    with tempfile.TemporaryDirectory() as skills_dir, tempfile.TemporaryDirectory() as jobs_dir:
        skills = SkillManager(skills_dir=skills_dir)
        skills.create_skill(
            name="note-summarizer",
            content="""---
name: note-summarizer
description: Summarize generic meeting notes
---
# Note Summarizer
Extract 1 key action item from the note text.
""",
        )

        runner = BatchJobRunner(skills_manager=skills, default_jobs_dir=jobs_dir)

        # 3 generic test items
        input_items = [
            {"id": "note_001", "text": "Discussion on database indexes. Decision: Add composite index on users(email, created_at)."},
            {"id": "note_002", "text": "Reviewed telemetry latency. Action: Increase buffer pool size to 4GB."},
            {"id": "note_003", "text": "UI polish sprint planning. Action: Refactor navigation sidebar responsive breakpoint."},
        ]

        out_dir = os.path.join(jobs_dir, "test_job_run")
        queue = runner.initialize_job(
            job_name="Q3 Notes Processing",
            skill_name="note-summarizer",
            input_data=input_items,
            output_dir=out_dir,
        )

        assert queue.total_items == 3
        assert queue.completed_items == 0
        assert queue.status == "initialized"
        assert os.path.exists(os.path.join(out_dir, "queue.json"))

        # Check queue.json on disk directly
        with open(os.path.join(out_dir, "queue.json"), "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert len(disk_data["items"]) == 3
        assert disk_data["items"][0]["status"] == "pending"

        # Define generic processor callback that creates an artifact file
        def mock_processor(item, context):
            text = item.input.get("text", "")
            action = text.split("Action:")[-1].split("Decision:")[-1].strip()
            artifact_file = os.path.join(context["output_dir"], f"{item.id}_summary.md")
            with open(artifact_file, "w", encoding="utf-8") as af:
                af.write(f"# Summary for {item.id}\nAction item: {action}\n")

            return {
                "summary": f"Identified action: {action[:40]}",
                "artifacts": [artifact_file],
                "data": {"extracted_action": action},
            }

        # Step 1
        res1 = runner.step_next(processor=mock_processor)
        assert res1["success"] is True
        assert res1["item_id"] == "note_001"
        assert res1["status"] == "completed"
        assert res1["completed_items"] == 1
        assert len(res1["result"]["artifacts"]) == 1
        assert os.path.exists(res1["result"]["artifacts"][0])

        # Step 2
        res2 = runner.step_next(processor=mock_processor)
        assert res2["success"] is True
        assert res2["item_id"] == "note_002"
        assert res2["completed_items"] == 2

        # Step 3
        res3 = runner.step_next(processor=mock_processor)
        assert res3["success"] is True
        assert res3["item_id"] == "note_003"
        assert res3["completed_items"] == 3

        # Step 4: All finished
        res_done = runner.step_next(processor=mock_processor)
        assert res_done["done"] is True
        assert res_done["status"] == "completed"

        # Verify disk checkpoint state
        with open(os.path.join(out_dir, "queue.json"), "r", encoding="utf-8") as f:
            final_disk = json.load(f)

        assert final_disk["status"] == "completed"
        assert final_disk["completed_items"] == 3
        assert final_disk["failed_items"] == 0
        for it in final_disk["items"]:
            assert it["status"] == "completed"
            assert it["result"] is not None
            assert len(it["result"]["artifacts"]) > 0


def test_batch_job_pause_and_resume():
    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)
        queue = runner.initialize_job(
            job_name="Pause Test",
            skill_name="generic-skill",
            input_data=["item A", "item B"],
            output_dir=os.path.join(jobs_dir, "pause_job"),
        )

        # Pause job
        pause_res = runner.pause()
        assert pause_res["success"] is True
        assert pause_res["status"] == "paused"

        # Stepping while paused is rejected
        step_attempt = runner.step_next()
        assert step_attempt["success"] is False
        assert step_attempt["status"] == "paused"

        # Resume job
        resume_res = runner.resume()
        assert resume_res["success"] is True
        assert resume_res["status"] == "in_progress"

        # Step succeeds after resume
        step_res = runner.step_next()
        assert step_res["success"] is True
        assert step_res["item_id"] == "item_001"


def test_batch_job_retries_and_failure():
    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)
        queue = runner.initialize_job(
            job_name="Retry Test",
            skill_name="generic-skill",
            input_data=["flakey_item"],
            output_dir=os.path.join(jobs_dir, "retry_job"),
            settings={"max_retries_per_item": 2},
        )

        attempts = 0

        def failing_processor(item, ctx):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError(f"Transient network glitch attempt {attempts}")
            return {"summary": "Succeeded on attempt 3"}

        # Attempt 1 -> fails, status resets to pending for retry
        res1 = runner.step_next(processor=failing_processor)
        assert res1["success"] is False
        assert res1["status"] == "pending"
        assert res1["retries"] == 1

        # Attempt 2 -> fails, status resets to pending for retry
        res2 = runner.step_next(processor=failing_processor)
        assert res2["success"] is False
        assert res2["status"] == "pending"
        assert res2["retries"] == 2

        # Attempt 3 -> succeeds
        res3 = runner.step_next(processor=failing_processor)
        assert res3["success"] is True
        assert res3["status"] == "completed"
        assert res3["result"]["summary"] == "Succeeded on attempt 3"


def test_batch_job_input_formats():
    """Verify loading from list of strings, JSON file, and CSV file."""
    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)

        # 1. CSV file input
        csv_file = os.path.join(jobs_dir, "items.csv")
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "title", "category"])
            writer.writeheader()
            writer.writerow({"id": "csv_1", "title": "Invention A", "category": "Tech"})
            writer.writerow({"id": "csv_2", "title": "Invention B", "category": "Consumer"})

        q_csv = runner.initialize_job("CSV Job", "test-skill", csv_file)
        assert q_csv.total_items == 2
        assert q_csv.items[0].id == "csv_1"
        assert q_csv.items[0].input["title"] == "Invention A"

        # 2. JSON file input
        json_file = os.path.join(jobs_dir, "items.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump([{"key": "val1"}, {"key": "val2"}, {"key": "val3"}], f)

        q_json = runner.initialize_job("JSON Job", "test-skill", json_file)
        assert q_json.total_items == 3
        assert q_json.items[1].input["key"] == "val2"


def test_agent_loop_skills_and_jobs_tool_dispatch():
    """Verify AgentLoop dispatches skills and batch job tools."""
    from eidos.core.loop import AgentLoop
    from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
    from eidos.safety.gatekeeper import Gatekeeper
    from eidos.safety.kill_switch import KillSwitch

    class MockJobModelClient(BaseModelClient):
        def chat(self, messages, tools=None, model_override=None):
            return ModelResponse(content='{"summary": "Processed item", "artifact_markdown": "# Done"}')

        def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
            return ModelResponse(content="ok")

    with tempfile.TemporaryDirectory() as skills_dir, tempfile.TemporaryDirectory() as jobs_dir:
        kill_switch = KillSwitch()
        gatekeeper = Gatekeeper(kill_switch=kill_switch)
        mock_model = MockJobModelClient()
        loop = AgentLoop(
            config={
                "skills": {"directory": skills_dir},
                "jobs": {"default_output_dir": jobs_dir},
            },
            model_client=mock_model,
            gatekeeper=gatekeeper,
            kill_switch=kill_switch,
        )
        if hasattr(loop, "job_processor") and loop.job_processor:
            loop.job_processor.router = mock_model

        # 1. create_skill
        skill_content = "---\nname: summary-skill\ndescription: Unit test skill\n---\n# Summary\nDo it."
        res, stop = loop._dispatch_tool(
            ToolCall(name="create_skill", arguments={"name": "summary-skill", "content": skill_content})
        )
        assert res["success"] is True

        # 2. list_skills
        res, stop = loop._dispatch_tool(ToolCall(name="list_skills", arguments={}))
        assert res["count"] >= 1
        skill_names = [s["name"] for s in res["skills"]]
        assert "summary-skill" in skill_names

        # 3. load_skill
        res, stop = loop._dispatch_tool(ToolCall(name="load_skill", arguments={"skill_name": "summary-skill"}))
        assert res["success"] is True
        assert res["name"] == "summary-skill"

        # 4. init_batch_job
        res, stop = loop._dispatch_tool(
            ToolCall(
                name="init_batch_job",
                arguments={
                    "job_name": "Agent Batch",
                    "skill_name": "summary-skill",
                    "items_data": ["alpha", "beta"],
                },
            )
        )
        assert res["success"] is True
        job_id = res["job_id"]
        assert res["total_items"] == 2

        # 5. step_batch_job
        res, stop = loop._dispatch_tool(ToolCall(name="step_batch_job", arguments={"job_id": job_id}))
        assert res["success"] is True
        assert res["completed_items"] == 1

        # 6. get_job_status
        res, stop = loop._dispatch_tool(ToolCall(name="get_job_status", arguments={"job_id": job_id}))
        assert res["completed_items"] == 1
        assert res["pending_items"] == 1
        assert res["progress_percent"] == 50.0

        # 7. pause & resume
        res, stop = loop._dispatch_tool(ToolCall(name="pause_batch_job", arguments={"job_id": job_id}))
        assert res["status"] == "paused"
        res, stop = loop._dispatch_tool(ToolCall(name="resume_batch_job", arguments={"job_id": job_id}))
        assert res["status"] == "in_progress"

        # 8. run_batch_job (foreground)
        res_run, stop = loop._dispatch_tool(
            ToolCall(
                name="run_batch_job",
                arguments={"job_id": job_id, "background": False, "pause_between_items_sec": 0.01},
            )
        )
        assert res_run["success"] is True
        assert res_run["done"] is True
        assert res_run["completed_items"] == 2


def test_batch_job_runner_run_job_continuous():
    """Verify run_job executes all pending items sequentially with pacing."""
    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)
        queue = runner.initialize_job(
            job_name="Continuous Run Test",
            skill_name="generic-skill",
            input_data=["item 1", "item 2", "item 3"],
            output_dir=os.path.join(jobs_dir, "continuous_run"),
        )
        assert queue.completed_items == 0

        res = runner.run_job(pause_between_items_sec=0.01)
        assert res["success"] is True
        assert res["done"] is True
        assert res["completed_items"] == 3
        assert res["total_items"] == 3

        st = runner.get_status()
        assert st["status"] == "completed"
        assert st["completed_items"] == 3
        assert st["pending_items"] == 0


def test_batch_job_runner_run_job_max_items():
    """Verify run_job respects max_items limit."""
    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)
        queue = runner.initialize_job(
            job_name="Max Items Test",
            skill_name="generic-skill",
            input_data=["A", "B", "C", "D"],
            output_dir=os.path.join(jobs_dir, "max_items_run"),
        )

        res = runner.run_job(pause_between_items_sec=0.01, max_items=2)
        assert res["success"] is True
        assert res["done"] is False
        assert res["completed_items"] == 2
        assert res["total_items"] == 4

        st = runner.get_status()
        assert st["completed_items"] == 2
        assert st["pending_items"] == 2


def test_batch_job_runner_run_job_kill_switch():
    """Verify run_job halts immediately when kill switch is triggered."""
    from eidos.safety.kill_switch import KillSwitch

    with tempfile.TemporaryDirectory() as jobs_dir:
        kill_switch = KillSwitch()
        runner = BatchJobRunner(default_jobs_dir=jobs_dir, kill_switch=kill_switch)
        queue = runner.initialize_job(
            job_name="Kill Switch Run Test",
            skill_name="generic-skill",
            input_data=["item 1", "item 2", "item 3"],
            output_dir=os.path.join(jobs_dir, "kill_run"),
        )

        # Trigger kill switch before or during run
        kill_switch.trigger()
        res = runner.run_job(pause_between_items_sec=0.01)

        assert res["success"] is False
        assert res["aborted"] is True
        assert res["reason"] == "kill_switch_triggered"
        assert res["status"] == "paused"


def test_batch_job_runner_background_worker_lifecycle():
    """Verify background worker launch, status polling, and clean stopping."""
    import time

    with tempfile.TemporaryDirectory() as jobs_dir:
        runner = BatchJobRunner(default_jobs_dir=jobs_dir)
        queue = runner.initialize_job(
            job_name="Background Worker Test",
            skill_name="generic-skill",
            input_data=[f"item_{i}" for i in range(10)],
            output_dir=os.path.join(jobs_dir, "bg_run"),
        )

        # Launch in background with 0.1s pacing
        bg_res = runner.run_in_background(pause_between_items_sec=0.1)
        assert bg_res["success"] is True
        assert bg_res["status"] == "running_in_background"
        assert runner.is_worker_running() is True
        assert runner.get_active_worker_job_id() == queue.job_id

        # Verify get_status reports is_running_in_background=True
        st = runner.get_status()
        assert st["is_running_in_background"] is True

        # Let it process 1-2 items
        time.sleep(0.3)

        # Stop worker
        stop_res = runner.stop_background_worker(timeout=3.0)
        assert stop_res["success"] is True
        assert runner.is_worker_running() is False

        st_after = runner.get_status()
        assert st_after["is_running_in_background"] is False
        assert st_after["completed_items"] >= 1

