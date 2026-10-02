"""Tests for CLI batch job commands in eidos/main.py."""

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from eidos.main import run_batch_cli
from eidos.safety.kill_switch import KillSwitch


class TestCliBatchJobCommands(unittest.TestCase):
    """Test CLI batch job launcher options."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.temp_dir.name)
        self.kill_switch = KillSwitch()

    def tearDown(self):
        self.kill_switch.stop()
        self.temp_dir.cleanup()

    def test_job_init_missing_items_returns_error(self):
        args = argparse.Namespace(
            job_init="Test Job",
            items=None,
            skill="invention-evaluator",
            output_dir=None,
            job_run=None,
            job_status=None,
            job_summary=None,
            pause_sec=2.0,
            max_items=None,
        )
        code = run_batch_cli(cfg={}, kill_switch=self.kill_switch, args=args)
        self.assertEqual(code, 1)

    def test_job_init_and_status(self):
        csv_file = self.workspace_dir / "test_inventions.csv"
        csv_file.write_text(
            "id,title,category,description\nINV-01,Test Widget,Tech,A cool gadget\n",
            encoding="utf-8",
        )
        jobs_dir = self.workspace_dir / "jobs"

        # 1. Initialize
        init_args = argparse.Namespace(
            job_init="CLI Test Job",
            items=str(csv_file),
            skill="invention-evaluator",
            output_dir=str(jobs_dir),
            job_run=None,
            job_status=None,
            job_summary=None,
            pause_sec=2.0,
            max_items=None,
        )
        code = run_batch_cli(cfg={"jobs": {"default_output_dir": str(jobs_dir)}}, kill_switch=self.kill_switch, args=init_args)
        self.assertEqual(code, 0)

        # 2. Check status
        status_args = argparse.Namespace(
            job_init=None,
            items=None,
            skill="invention-evaluator",
            output_dir=None,
            job_run=None,
            job_status="__ACTIVE__",
            job_summary=None,
            pause_sec=2.0,
            max_items=None,
        )
        code = run_batch_cli(cfg={"jobs": {"default_output_dir": str(jobs_dir)}}, kill_switch=self.kill_switch, args=status_args)
        self.assertEqual(code, 0)

    def test_job_run_and_summary(self):
        csv_file = self.workspace_dir / "inventions.csv"
        csv_file.write_text(
            "id,title,category\nINV-01,Invention One,Tech\nINV-02,Invention Two,Bio\n",
            encoding="utf-8",
        )
        jobs_dir = self.workspace_dir / "jobs"

        # Run with max_items=1
        args = argparse.Namespace(
            job_init="Run Job Test",
            items=str(csv_file),
            skill="invention-evaluator",
            output_dir=str(jobs_dir),
            job_run="__ACTIVE__",
            job_status=None,
            job_summary="__ACTIVE__",
            pause_sec=0.01,
            max_items=1,
        )

        with patch("eidos.jobs.processor.SkillJobProcessor.process_item") as mock_process:
            mock_process.return_value = {
                "summary": "Mock evaluated INV-01",
                "total_score": 85,
                "scores": {
                    "technical_feasibility": 22,
                    "market_demand": 21,
                    "novelty_and_fto": 22,
                    "margin_potential": 20,
                },
                "artifacts": [],
            }
            code = run_batch_cli(cfg={"jobs": {"default_output_dir": str(jobs_dir)}}, kill_switch=self.kill_switch, args=args)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
