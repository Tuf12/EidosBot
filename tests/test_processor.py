"""Unit and integration tests for SkillJobProcessor (eidos/jobs/processor.py)."""

import json
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
import pytest

from eidos.jobs.processor import SkillJobProcessor, extract_json_from_text
from eidos.jobs.runner import BatchJobRunner
from eidos.jobs.types import QueueItem
from eidos.models.base import BaseModelClient, ModelResponse
from eidos.skills.manager import SkillManager


class MockModelRouter(BaseModelClient):
    """Mock ModelRouter returning predetermined evaluation responses."""

    def __init__(self, response_text: str = ""):
        self.response_text = response_text
        self.last_messages = []

    def chat(self, messages, tools=None, model_override=None):
        self.last_messages = messages
        return ModelResponse(
            content=self.response_text,
            model="mock-model",
            raw={"content": self.response_text},
        )

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None):
        return ModelResponse(content="mock-vision")


class MockWebResearchEngine:
    """Mock WebResearchEngine returning fake search hits."""

    def __init__(self, search_results=None, webpage_content=""):
        self.search_results = search_results or [
            {
                "title": "US9999999B2 - Portable Solar Atmospheric Condenser",
                "url": "https://patents.google.com/patent/US9999999B2/en",
                "snippet": "Atmospheric water extraction utilizing solar thermal energy and peltier devices.",
                "source": "brave",
            },
            {
                "title": "Off-Grid Solar Purifier Commercial Review",
                "url": "https://example.com/commercial-review",
                "snippet": "Commercial water purifiers for camping and off-grid use.",
                "source": "brave",
            },
        ]
        self.webpage_content = webpage_content or "# US9999999B2 Patent Details\nDetailed claims on condenser coils."
        self.queries = []

    def search(self, query: str, count: int = 5):
        self.queries.append(query)
        return self.search_results

    def fetch_webpage(self, url: str, max_chars: int = 8000):
        return {"status": 200, "title": "Mock Patent", "content": self.webpage_content}


class MockBrowserManager:
    """Mock PlaywrightManager."""

    def __init__(self):
        self.navigated_urls = []

    def navigate(self, url: str, headless=True, timeout=30.0):
        self.navigated_urls.append(url)
        return {"success": True, "status": 200, "url": url, "title": "Google Patents Mock"}

    def extract_markdown(self, selector=None, max_chars=8000):
        return {
            "success": True,
            "title": "Google Patents Mock",
            "content": "# Extracted Google Patents Claims\nClaim 1: A solar powered condensation device...",
        }


# =============================================================================
# Unit Tests for JSON Extraction Helper
# =============================================================================

def test_extract_json_from_text_pure_json():
    raw = '{"total_score": 85, "summary": "Great invention"}'
    parsed = extract_json_from_text(raw)
    assert parsed == {"total_score": 85, "summary": "Great invention"}


def test_extract_json_from_text_with_code_fences():
    raw = """Here is the evaluation:
```json
{
  "summary": "Solid feasibility",
  "total_score": 78
}
```
Hope this helps!"""
    parsed = extract_json_from_text(raw)
    assert parsed["summary"] == "Solid feasibility"
    assert parsed["total_score"] == 78


def test_extract_json_from_text_with_thought_tags():
    raw = """<thought>
Let's analyze the 4 dimensions.
Technical feasibility is 22.
Market demand is 20.
</thought>
```json
{
  "summary": "High commercial potential",
  "total_score": 82
}
```"""
    parsed = extract_json_from_text(raw)
    assert parsed["total_score"] == 82


def test_extract_json_from_text_trailing_commas():
    raw = '{"total_score": 75, "scores": {"feasibility": 20, "demand": 18,},}'
    parsed = extract_json_from_text(raw)
    assert parsed is not None
    assert parsed["total_score"] == 75


def test_extract_json_from_text_invalid():
    assert extract_json_from_text("") is None
    assert extract_json_from_text("Just plain text with no json") is None


# =============================================================================
# Unit Tests for SkillJobProcessor (Invention Evaluation)
# =============================================================================

def test_skill_job_processor_invention_evaluation():
    mock_llm_json = json.dumps({
        "summary": "High commercial viability (86/100). Off-the-shelf peltier condensation with clear off-grid demand.",
        "total_score": 86,
        "scores": {
            "technical_feasibility": 23,
            "market_demand": 22,
            "novelty_and_fto": 20,
            "margin_potential": 21,
        },
        "rubric_breakdown": {
            "technical_feasibility": "Uses mature Peltier TEC1-12706 coolers and standard aluminum fins.",
            "market_demand": "Acute pain point for off-grid hikers needing daily potable water.",
            "novelty_and_fto": "Unique solar tracker integration differentiates from US9999999B2.",
            "margin_potential": "Estimated BOM is $35 against a $180 retail price point.",
        },
        "prior_art_found": [
            "US9999999B2 - Portable Solar Atmospheric Condenser",
            "Commercial DewHarvester Backpack Unit",
        ],
        "milestones": [
            "Build thermal bench prototype with 50W solar panel",
            "File provisional patent on dual-stage condensing chamber",
            "Obtain injection molding BOM quotes",
        ],
    })

    mock_router = MockModelRouter(response_text=mock_llm_json)
    mock_web = MockWebResearchEngine()
    mock_browser = MockBrowserManager()

    processor = SkillJobProcessor(
        model_router=mock_router,
        web_research_engine=mock_web,
        browser_manager=mock_browser,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        item = QueueItem(
            id="INV-001",
            input={
                "title": "Solar Dew Condenser",
                "category": "Cleantech",
                "description": "Atmospheric water extraction powered by portable solar collector with peltier array.",
                "target_audience": "Hikers and off-grid homesteaders",
            },
        )

        context = {
            "job_id": "test_job_001",
            "job_name": "Invention Evaluation",
            "skill_name": "invention-evaluator",
            "skill": {
                "name": "invention-evaluator",
                "body": "## 2. Evaluation Rubric (4 Dimensions, 25 Points Each)\nDimension A: Technical Feasibility\nDimension B: Market Demand\nDimension C: Novelty & Freedom to Operate\nDimension D: Cost-to-Produce vs. Margin Potential",
            },
            "output_dir": temp_dir,
        }

        result = processor.process_item(item, context)

        # 1. Verify result structure
        assert result["total_score"] == 86
        assert result["scores"]["technical_feasibility"] == 23
        assert result["scores"]["market_demand"] == 22
        assert result["scores"]["novelty_and_fto"] == 20
        assert result["scores"]["margin_potential"] == 21
        assert "High commercial viability" in result["summary"]
        assert len(result["prior_art_found"]) >= 2

        # 2. Verify portfolio markdown file generated
        portfolio_path = os.path.join(temp_dir, "portfolios", "INV-001.md")
        assert os.path.exists(portfolio_path)
        assert result["artifacts"] == [portfolio_path]

        with open(portfolio_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "# Solar Dew Condenser" in content
        assert "**Overall Viability Score:** 86/100" in content
        assert "Technical Feasibility | 23/25" in content
        assert "US9999999B2" in content
        assert "Build thermal bench prototype" in content

        # 3. Verify web and browser research was invoked
        assert len(mock_web.queries) >= 2
        assert len(mock_browser.navigated_urls) >= 1


def test_skill_job_processor_heuristic_fallback():
    """Verify processor handles non-JSON model output gracefully via heuristic parser."""
    non_json_text = """
    Here is my evaluation for the project:
    Technical Feasibility: 20
    Market Demand: 18
    Novelty & Freedom to Operate: 17
    Margin Potential: 19
    Overall this looks like a feasible project with solid margins.
    """

    mock_router = MockModelRouter(response_text=non_json_text)
    processor = SkillJobProcessor(
        model_router=mock_router,
        web_research_engine=MockWebResearchEngine(),
        browser_manager=MockBrowserManager(),
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        item = QueueItem(
            id="INV-002",
            input={"title": "Smart Irrigation Valve", "category": "AgTech"},
        )
        context = {
            "job_id": "test_job_002",
            "job_name": "Invention Evaluation",
            "skill_name": "invention-evaluator",
            "skill": {
                "name": "invention-evaluator",
                "body": "Dimension A: Technical Feasibility\nDimension B: Market Demand\nDimension C: Novelty\nDimension D: Margin Potential",
            },
            "output_dir": temp_dir,
        }

        result = processor.process_item(item, context)
        assert result["total_score"] == 74  # 20 + 18 + 17 + 19
        assert result["scores"]["technical_feasibility"] == 20
        assert os.path.exists(os.path.join(temp_dir, "portfolios", "INV-002.md"))


def test_skill_job_processor_generic_skill():
    """Verify processor behaves cleanly for generic non-invention procedural skills."""
    mock_json = json.dumps({
        "summary": "Summarized meeting notes into 2 action items.",
        "artifact_markdown": "# Summary\n- Action 1: Deploy database migration\n- Action 2: Monitor redis latency",
        "data": {"actions_count": 2},
    })

    mock_router = MockModelRouter(response_text=mock_json)
    processor = SkillJobProcessor(
        model_router=mock_router,
        web_research_engine=MockWebResearchEngine(),
        browser_manager=MockBrowserManager(),
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        item = QueueItem(
            id="task_001",
            input={"text": "Team meeting notes... Action items discussed."},
        )
        context = {
            "job_id": "test_job_003",
            "job_name": "Meeting Summaries",
            "skill_name": "meeting-summarizer",
            "skill": {
                "name": "meeting-summarizer",
                "body": "# Meeting Summarizer\nExtract action items and output summary markdown.",
            },
            "output_dir": temp_dir,
        }

        result = processor.process_item(item, context)
        assert "Summarized meeting notes" in result["summary"]
        assert len(result["artifacts"]) == 1
        artifact_path = result["artifacts"][0]
        assert os.path.exists(artifact_path)
        with open(artifact_path, "r", encoding="utf-8") as f:
            assert "Action 1: Deploy database migration" in f.read()


# =============================================================================
# Integration Test: BatchJobRunner with SkillJobProcessor
# =============================================================================

def test_batch_job_runner_integration_with_skill_job_processor():
    """Verify that BatchJobRunner automatically invokes SkillJobProcessor when stepping."""
    with tempfile.TemporaryDirectory() as temp_dir:
        skills_dir = os.path.join(temp_dir, "skills")
        jobs_dir = os.path.join(temp_dir, "jobs")

        skills = SkillManager(skills_dir=skills_dir)
        skills.create_skill(
            name="invention-evaluator",
            content="""---
name: invention-evaluator
description: Commercial viability rubric
---
# Invention Idea Commercial Evaluator
Dimension A: Technical Feasibility
Dimension B: Market Demand
Dimension C: Novelty & Freedom to Operate
Dimension D: Cost-to-Produce vs. Margin Potential
""",
        )

        mock_llm_json = json.dumps({
            "summary": "Viable prototype (80/100).",
            "total_score": 80,
            "scores": {
                "technical_feasibility": 20,
                "market_demand": 20,
                "novelty_and_fto": 20,
                "margin_potential": 20,
            },
            "rubric_breakdown": {
                "technical_feasibility": "Standard hardware components.",
                "market_demand": "Large consumer target audience.",
                "novelty_and_fto": "Clear differentiation from prior art.",
                "margin_potential": "Healthy gross margin expected.",
            },
            "prior_art_found": ["US1234567A - Prior Art"],
            "milestones": ["Build MVP", "User testing"],
        })

        mock_router = MockModelRouter(response_text=mock_llm_json)
        processor = SkillJobProcessor(
            model_router=mock_router,
            web_research_engine=MockWebResearchEngine(),
            browser_manager=MockBrowserManager(),
        )

        runner = BatchJobRunner(
            skills_manager=skills,
            default_jobs_dir=jobs_dir,
            default_processor=processor.process_item,
        )

        # Initialize job with 2 inventions
        inventions = [
            {"id": "INV-101", "title": "Self-Cleaning Solar Panel", "category": "Energy"},
            {"id": "INV-102", "title": "Haptic Navigation Cane", "category": "Assistive"},
        ]

        queue = runner.initialize_job(
            job_name="Invention Batch Test",
            skill_name="invention-evaluator",
            input_data=inventions,
        )

        assert queue.total_items == 2
        assert queue.completed_items == 0

        # Step 1: uses default_processor (SkillJobProcessor)
        res1 = runner.step_next()
        assert res1["success"] is True
        assert res1["item_id"] == "INV-101"
        assert res1["result"]["total_score"] == 80
        assert os.path.exists(res1["result"]["artifacts"][0])

        # Step 2: uses default_processor
        res2 = runner.step_next()
        assert res2["success"] is True
        assert res2["item_id"] == "INV-102"
        assert res2["completed_items"] == 2

        # Step 3: queue done
        res3 = runner.step_next()
        assert res3["done"] is True
        assert res3["status"] == "completed"


def test_skill_job_processor_with_optimalx_reference():
    """Verify SkillJobProcessor queries OptimalX notes and integrates them into rubric prompt and portfolio."""
    mock_optimalx = MagicMock()
    mock_optimalx.search_notes.return_value = [
        {
            "location": "Inventions > Solar Dew Condenser",
            "snippet": "User idea from 2024: Use dual peltier cells with copper heat pipes and hydrophobic coating.",
            "subfolder_id": 14,
        }
    ]

    mock_llm_json = json.dumps({
        "summary": "Strong prototype with user's hydrophobic condenser design (88/100).",
        "total_score": 88,
        "scores": {
            "technical_feasibility": 24,
            "market_demand": 21,
            "novelty_and_fto": 22,
            "margin_potential": 21,
        },
        "rubric_breakdown": {
            "technical_feasibility": "Hydrophobic coating enhances dropwise condensation.",
            "market_demand": "High interest among outdoor recreationists.",
            "novelty_and_fto": "Clear differentiation from passive solar stills.",
            "margin_potential": "Target BOM is $40.",
        },
        "prior_art_found": ["US8888888B1 - Passive Solar Still"],
        "milestones": ["Order hydrophobic-coated copper fins"],
    })

    mock_router = MockModelRouter(response_text=mock_llm_json)
    mock_web = MockWebResearchEngine()
    mock_browser = MockBrowserManager()

    processor = SkillJobProcessor(
        model_router=mock_router,
        web_research_engine=mock_web,
        browser_manager=mock_browser,
        optimalx_bridge=mock_optimalx,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        item = QueueItem(
            id="INV-999",
            input={
                "title": "Solar Dew Condenser",
                "category": "Cleantech",
                "description": "Extract water using solar powered thermoelectric chilling.",
            },
        )

        context = {
            "job_id": "job_opt_test",
            "skill_name": "invention-evaluator",
            "output_dir": temp_dir,
            "skill": {
                "name": "invention-evaluator",
                "body": "4-dimension commercial evaluation rubric.",
            },
        }

        result = processor.process_item(item, context)

        # 1. OptimalX was queried
        mock_optimalx.search_notes.assert_called()

        # 2. OptimalX reference attached to result data
        assert "optimalx_reference" in result["data"]
        assert len(result["data"]["optimalx_reference"]) == 1
        assert "Inventions > Solar Dew Condenser" in result["data"]["optimalx_reference"][0]["location"]

        # 3. Portfolio markdown file contains OptimalX Reference Notes
        portfolio_path = result["artifacts"][0]
        with open(portfolio_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "## OptimalX Reference Notes" in content
        assert "Inventions > Solar Dew Condenser" in content
        assert "hydrophobic coating" in content

        # 4. Router prompt included user's OptimalX context
        user_prompt = mock_router.last_messages[1]["content"]
        assert "--- USER'S OPTIMALX NOTES & ORIGINAL PROJECT CONTEXT ---" in user_prompt
        assert "hydrophobic coating" in user_prompt

