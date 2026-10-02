"""Tests for RankingSummaryGenerator (eidos/jobs/summary.py) and runner leaderboard integration."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from eidos.core.loop import AgentLoop, ToolCall
from eidos.jobs.runner import BatchJobRunner
from eidos.jobs.summary import RankingSummaryGenerator
from eidos.jobs.types import JobQueue, JobSettings, QueueItem


class TestRankingSummaryGenerator(unittest.TestCase):
    """Unit tests for the RankingSummaryGenerator."""

    def setUp(self):
        self.generator = RankingSummaryGenerator()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_queue_summary(self):
        queue = JobQueue(
            job_id="job_empty",
            job_name="Empty Inventions Job",
            skill_name="invention-evaluator",
            output_dir=str(self.out_dir),
            items=[],
        )
        res = self.generator.generate(queue)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_items"], 0)
        self.assertEqual(res["completed_items"], 0)
        self.assertEqual(res["average_score"], 0.0)

        # Check files exist
        md_file = self.out_dir / "RANKING_SUMMARY.md"
        csv_file = self.out_dir / "RANKING_SUMMARY.csv"
        json_file = self.out_dir / "RANKING_SUMMARY.json"

        self.assertTrue(md_file.exists())
        self.assertTrue(csv_file.exists())
        self.assertTrue(json_file.exists())

        md_text = md_file.read_text(encoding="utf-8")
        self.assertIn("Empty Inventions Job", md_text)
        self.assertIn("Total Inventions Evaluated:** 0", md_text)

    def test_tiered_ranking_and_file_formats(self):
        items = [
            QueueItem(
                id="INV-001",
                input={"title": "Micro-Hydro Vortex", "category": "Cleantech"},
                status="completed",
                result={
                    "total_score": 92,
                    "scores": {
                        "technical_feasibility": 24,
                        "market_demand": 24,
                        "novelty_and_fto": 22,
                        "margin_potential": 22,
                    },
                    "summary": "Outstanding cleantech micro-generator.",
                    "data": {
                        "relative_artifact": "portfolios/INV-001.md",
                    },
                },
            ),
            QueueItem(
                id="INV-002",
                input={"title": "Smart Orthotic Insole", "category": "MedTech"},
                status="completed",
                result={
                    "total_score": 75,
                    "scores": {
                        "technical_feasibility": 20,
                        "market_demand": 21,
                        "novelty_and_fto": 18,
                        "margin_potential": 16,
                    },
                    "summary": "Strong medical utility, moderate competition.",
                    "data": {
                        "relative_artifact": "portfolios/INV-002.md",
                    },
                },
            ),
            QueueItem(
                id="INV-003",
                input={"title": "Heated Coffee Lid", "category": "Consumer"},
                status="completed",
                result={
                    "total_score": 48,
                    "scores": {
                        "technical_feasibility": 14,
                        "market_demand": 12,
                        "novelty_and_fto": 10,
                        "margin_potential": 12,
                    },
                    "summary": "Saturated prior art and high BOM.",
                    "data": {
                        "relative_artifact": "portfolios/INV-003.md",
                    },
                },
            ),
            QueueItem(
                id="INV-004",
                input={"title": "Broken Concept | Dangerous", "category": "Aero | Space"},
                status="failed",
                error="Browser crashed during prior art search",
            ),
        ]

        queue = JobQueue(
            job_id="job_inventions_full",
            job_name="2026 Commercial Inventions Test",
            skill_name="invention-evaluator",
            output_dir=str(self.out_dir),
            items=items,
            total_items=4,
            completed_items=3,
            failed_items=1,
        )

        res = self.generator.generate(queue)

        self.assertTrue(res["success"])
        self.assertEqual(res["total_items"], 4)
        self.assertEqual(res["completed_items"], 3)
        self.assertEqual(res["tier_1_count"], 1)
        self.assertEqual(res["tier_2_count"], 1)
        self.assertEqual(res["tier_3_count"], 1)
        self.assertEqual(res["needs_review_count"], 1)
        # Average: (92 + 75 + 48) / 3 = 71.666... -> 71.7
        self.assertEqual(res["average_score"], 71.7)

        # 1. Verify Markdown file
        md_file = Path(res["markdown_path"])
        self.assertTrue(md_file.exists())
        md_text = md_file.read_text(encoding="utf-8")

        self.assertIn("# 2026 Commercial Inventions Test — Commercial Viability Ranking Summary", md_text)
        self.assertIn("**Average Viability Score:** 71.7 / 100", md_text)
        self.assertIn("## 🏆 Top Tier: High Commercial Viability (Scores 80–100)", md_text)
        self.assertIn("| 1 | INV-001 | Micro-Hydro Vortex | Cleantech | 24 | 24 | 22 | 22 | **92** | [Portfolio](portfolios/INV-001.md) |", md_text)
        self.assertIn("## 💡 Tier 2: Moderate Viability / Promising with Caveats (Scores 60–79)", md_text)
        self.assertIn("| 2 | INV-002 | Smart Orthotic Insole | MedTech | 20 | 21 | 18 | 16 | **75** | [Portfolio](portfolios/INV-002.md) |", md_text)
        self.assertIn("## ⚠️ Tier 3: Low Viability / High Prior Art Saturation / Speculative (Scores < 60)", md_text)
        self.assertIn("| 3 | INV-003 | Heated Coffee Lid | Consumer | 14 | 12 | 10 | 12 | **48** | [Portfolio](portfolios/INV-003.md) |", md_text)
        self.assertIn("## ⚠️ Needs Review / Incomplete Items", md_text)
        self.assertIn("Broken Concept - Dangerous", md_text)  # pipes sanitized

        # 2. Verify CSV file
        csv_file = Path(res["csv_path"])
        self.assertTrue(csv_file.exists())
        with open(csv_file, mode="r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
            self.assertEqual(reader[0], [
                "Rank", "ID", "Title", "Category", "Tech_Score", "Market_Score",
                "Novelty_Score", "Margin_Score", "Total_Score", "Tier", "Status", "Portfolio_Path",
            ])
            self.assertEqual(len(reader), 5)  # header + 3 scored + 1 failed
            # First item (Rank 1)
            self.assertEqual(reader[1][0], "1")
            self.assertEqual(reader[1][1], "INV-001")
            self.assertEqual(reader[1][8], "92")
            self.assertEqual(reader[1][9], "Tier 1")

            # Failed item
            self.assertEqual(reader[4][0], "-")
            self.assertEqual(reader[4][1], "INV-004")
            self.assertEqual(reader[4][9], "Needs Review")
            self.assertEqual(reader[4][10], "failed")

        # 3. Verify JSON file
        json_file = Path(res["json_path"])
        self.assertTrue(json_file.exists())
        with open(json_file, mode="r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["job_id"], "job_inventions_full")
            self.assertEqual(data["stats"]["average_viability_score"], 71.7)
            self.assertEqual(len(data["rankings"]), 3)
            self.assertEqual(data["rankings"][0]["id"], "INV-001")
            self.assertEqual(data["rankings"][0]["rank"], 1)
            self.assertEqual(len(data["tiers"]["tier_1_high_viability"]), 1)
            self.assertEqual(len(data["tiers"]["needs_review"]), 1)

    def test_fallback_score_summation(self):
        # When total_score is missing, sum sub-scores
        item = QueueItem(
            id="INV-999",
            input={"title": "Sum Test Invention"},
            status="completed",
            result={
                "scores": {
                    "technical_feasibility": 20,
                    "market_demand": 20,
                    "novelty_and_fto": 20,
                    "margin_potential": 22,
                }
            },
        )
        queue = JobQueue(
            job_id="job_sum",
            job_name="Sum Test",
            skill_name="invention-evaluator",
            output_dir=str(self.out_dir),
            items=[item],
        )
        res = self.generator.generate(queue)
        self.assertEqual(res["completed_items"], 1)
        self.assertEqual(res["average_score"], 82.0)
        self.assertEqual(res["tier_1_count"], 1)


class TestRunnerSummaryIntegration(unittest.TestCase):
    """Test BatchJobRunner auto-summary generation upon queue completion."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self.temp_dir.name)
        self.runner = BatchJobRunner(default_jobs_dir=str(self.jobs_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_auto_summary_on_completion(self):
        items_data = [
            {"id": "INV-01", "title": "Widget One", "category": "Tech"},
            {"id": "INV-02", "title": "Widget Two", "category": "Tech"},
        ]
        queue = self.runner.initialize_job(
            job_name="Auto Summary Test",
            skill_name="invention-evaluator",
            input_data=items_data,
        )

        # Mock processor that returns scores
        def mock_processor(item, ctx):
            score = 85 if item.id == "INV-01" else 65
            return {
                "summary": f"Evaluated {item.id}",
                "total_score": score,
                "scores": {
                    "technical_feasibility": 20,
                    "market_demand": 20,
                    "novelty_and_fto": score - 40,
                    "margin_potential": 20,
                },
                "artifacts": [],
            }

        # Run the full job
        run_res = self.runner.run_job(
            job_id_or_path=queue.job_id,
            processor=mock_processor,
            pause_between_items_sec=0.01,
        )

        self.assertTrue(run_res["success"])
        self.assertTrue(run_res["done"])
        self.assertIn("summary", run_res)
        summary = run_res["summary"]
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total_items"], 2)
        self.assertEqual(summary["completed_items"], 2)
        self.assertEqual(summary["average_score"], 75.0)

        # Verify summary files were created in job output dir
        out_dir = Path(queue.output_dir)
        self.assertTrue((out_dir / "RANKING_SUMMARY.md").exists())
        self.assertTrue((out_dir / "RANKING_SUMMARY.csv").exists())
        self.assertTrue((out_dir / "RANKING_SUMMARY.json").exists())

    def test_manual_generate_summary_method(self):
        queue = self.runner.initialize_job(
            job_name="Manual Summary Test",
            skill_name="invention-evaluator",
            input_data=[{"id": "INV-X", "title": "Test X"}],
        )
        res = self.runner.generate_summary(job_id_or_path=queue.job_id)
        self.assertTrue(res["success"])
        self.assertTrue(Path(res["markdown_path"]).exists())


class TestLoopSummaryTool(unittest.TestCase):
    """Test AgentLoop dispatch of generate_batch_summary tool."""

    def test_dispatch_generate_batch_summary(self):
        mock_jobs = MagicMock()
        mock_jobs.generate_summary.return_value = {
            "success": True,
            "markdown_path": "/fake/RANKING_SUMMARY.md",
            "average_score": 88.5,
        }

        loop = AgentLoop(
            config={},
            model_client=MagicMock(),
            gatekeeper=MagicMock(),
        )
        loop.gatekeeper.evaluate.return_value = (True, "Allowed")
        loop.jobs = mock_jobs

        tc = ToolCall(
            name="generate_batch_summary",
            arguments={"job_id": "job_xyz"},
        )

        res, is_finish = loop._dispatch_tool(tc)
        self.assertFalse(is_finish)
        self.assertTrue(res["success"])
        self.assertEqual(res["average_score"], 88.5)
        mock_jobs.generate_summary.assert_called_once_with(job_id_or_path="job_xyz")


if __name__ == "__main__":
    unittest.main()
