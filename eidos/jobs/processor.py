"""Fresh-context sub-agent processor for batch job queue items (~/.eidos/skills/)."""

import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from eidos.browser.manager import PlaywrightManager
from eidos.integrations.web_research import WebResearchEngine
from eidos.jobs.types import QueueItem
from eidos.models.base import ModelResponse
from eidos.models.router import ModelRouter

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract and parse a JSON dictionary from model output text."""
    if not text:
        return None

    cleaned = text.strip()

    # 1. Strip reasoning/thought tags
    cleaned = re.sub(r"<thought>[\s\S]*?</thought>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"<recall>[\s\S]*?</recall>", "", cleaned, flags=re.IGNORECASE).strip()

    # 2. Check for markdown code fence (```json ... ``` or ``` ... ```)
    code_fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if code_fence_match:
        candidate = code_fence_match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # 3. Direct JSON load attempt
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 4. Search for the outermost JSON object delimited by { and }
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = cleaned[start_idx : end_idx + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            # Try cleaning trailing commas before closing braces
            sanitized = re.sub(r",\s*([\}\]])", r"\1", candidate)
            try:
                parsed = json.loads(sanitized)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

    return None


class SkillJobProcessor:
    """Fresh-context sub-agent executor for batch queue items guided by SKILL.md.

    Lifecycle per item:
    1. Automated Prior Art & Market Discovery (WebResearchEngine + Playwright).
    2. Multi-Dimensional Rubric Scoring via ModelRouter in an isolated context session.
    3. Standardized Markdown Portfolio Generation & Checkpoint Payload Preparation.
    """

    def __init__(
        self,
        model_router: Optional[ModelRouter] = None,
        web_research_engine: Optional[WebResearchEngine] = None,
        browser_manager: Optional[PlaywrightManager] = None,
        optimalx_bridge: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.router = model_router or ModelRouter()
        self.web = web_research_engine or WebResearchEngine(model_router=self.router)
        self.browser = browser_manager or PlaywrightManager.get_instance()
        self.optimalx = optimalx_bridge
        self.config = config or {}

    def process_item(self, item: QueueItem, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute autonomous research and rubric evaluation for a single queue item.

        Args:
            item: The atomic QueueItem with domain data in item.input.
            context: Execution context containing skill_name, skill metadata, and output_dir.

        Returns:
            Structured result payload matching QueueItem.result schema.
        """
        skill_info = context.get("skill") or {}
        skill_name = context.get("skill_name") or skill_info.get("name", "generic-skill")
        output_dir = Path(os.path.abspath(os.path.expanduser(context.get("output_dir", "~/EidosWorkspace/jobs"))))

        logger.info("SkillJobProcessor executing item '%s' using skill '%s'", item.id, skill_name)

        # 1. Determine skill type (Invention evaluation vs generic procedure)
        skill_body = skill_info.get("body", "")
        is_invention_skill = (
            "invention" in skill_name.lower()
            or "prior art" in skill_body.lower()
            or "commercial rubric" in skill_body.lower()
            or ("technical feasibility" in skill_body.lower() and "margin potential" in skill_body.lower())
        )

        if is_invention_skill:
            return self._process_invention_item(item, context, skill_info, output_dir)
        else:
            return self._process_generic_item(item, context, skill_info, output_dir)

    # =========================================================================
    # Invention Evaluation & Prior Art Research Pipeline
    # =========================================================================

    def _retrieve_optimalx_reference_notes(
        self,
        title: str,
        description: str = "",
        category: str = "",
    ) -> List[Dict[str, Any]]:
        """Retrieve related personal notes or prior concepts from OptimalX."""
        if not self.optimalx:
            return []

        search_query = title.strip()
        if not search_query:
            return []

        try:
            results = []
            if "invention" in category.lower() or "cleantech" in category.lower() or "hardware" in category.lower():
                results = self.optimalx.search_notes(
                    query=search_query,
                    parent_filter="Inventions",
                    limit=2,
                )
            if not results:
                results = self.optimalx.search_notes(query=search_query, limit=2)
            return results or []
        except Exception as e:
            logger.debug("Failed to retrieve OptimalX reference notes for '%s': %s", title, e)
            return []

    def _process_invention_item(
        self,
        item: QueueItem,
        context: Dict[str, Any],
        skill_info: Dict[str, Any],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Execute 3-stage prior art research, 4-dimension scoring, and portfolio generation."""
        inp = item.input or {}
        item_id = item.id
        title = inp.get("title") or inp.get("name") or inp.get("concept") or inp.get("item_name") or item_id
        category = inp.get("category") or inp.get("domain") or "General Innovation"
        description = inp.get("description") or inp.get("text") or inp.get("summary") or inp.get("details") or ""
        target_audience = inp.get("target_audience") or inp.get("audience") or inp.get("demographic") or ""

        # Stage 0: OptimalX Personal Note Reference Lookup
        optimalx_notes = self._retrieve_optimalx_reference_notes(title, description, category)

        # Stage 1: Prior Art & Market Discovery
        research_data = self._conduct_prior_art_research(title, description, category)

        # Stage 2: 4-Dimension Rubric Scoring via Fresh-Context LLM Session
        evaluation = self._score_invention_rubric(
            item_id=item_id,
            title=title,
            category=category,
            description=description,
            target_audience=target_audience,
            other_input=inp,
            research_data=research_data,
            skill_info=skill_info,
            optimalx_notes=optimalx_notes,
        )

        # Stage 3: Portfolio Markdown Generation & Artifact Writing
        portfolios_dir = output_dir / "portfolios"
        portfolios_dir.mkdir(parents=True, exist_ok=True)
        portfolio_file = portfolios_dir / f"{item_id}.md"

        portfolio_md = self._render_portfolio_markdown(
            item_id=item_id,
            title=title,
            category=category,
            evaluation=evaluation,
            research_data=research_data,
            optimalx_notes=optimalx_notes,
        )

        with open(portfolio_file, "w", encoding="utf-8") as pf:
            pf.write(portfolio_md + "\n")

        relative_artifact = f"portfolios/{item_id}.md"

        total_score = evaluation.get("total_score", 0)
        scores = evaluation.get("scores", {})
        summary = evaluation.get(
            "summary",
            f"Evaluated {title} (Score: {total_score}/100 across 4 commercial dimensions).",
        )
        prior_art = evaluation.get("prior_art_found") or [r.get("title") for r in research_data.get("search_hits", [])[:3]]

        logger.info("Completed item %s (%s): score %d/100 -> %s", item_id, title, total_score, portfolio_file)

        opt_ref = [
            {
                "location": n.get("location") or n.get("title"),
                "snippet": n.get("snippet") or n.get("text") or n.get("summary"),
            }
            for n in optimalx_notes
        ] if optimalx_notes else []

        return {
            "summary": summary,
            "total_score": total_score,
            "scores": scores,
            "prior_art_found": prior_art,
            "artifacts": [str(portfolio_file)],
            "data": {
                "item_id": item_id,
                "title": title,
                "category": category,
                "scores": scores,
                "total_score": total_score,
                "portfolio_path": str(portfolio_file),
                "relative_artifact": relative_artifact,
                "milestones": evaluation.get("milestones", []),
                "optimalx_reference": opt_ref,
            },
        }

    def _conduct_prior_art_research(
        self,
        title: str,
        description: str,
        category: str,
    ) -> Dict[str, Any]:
        """Conduct targeted web search and Google Patents inspection for prior art."""
        # 1. Formulate 2-3 focused search queries
        clean_title = re.sub(r'["\']', "", title.strip())
        queries = [
            f'"{clean_title}" patent prior art',
            f"site:patents.google.com {clean_title}",
            f"commercial product alternatives {clean_title}",
        ]

        search_hits: List[Dict[str, Any]] = []
        seen_urls = set()

        for q in queries:
            try:
                hits = self.web.search(q, count=3)
                for h in hits:
                    u = h.get("url", "")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        search_hits.append(h)
            except Exception as e:
                logger.debug("Prior art search query '%s' encountered error: %s", q, e)

        # 2. Extract deep patent text from top Google Patents or relevant URLs
        deep_snippets: List[Dict[str, Any]] = []
        patent_candidates = [
            h for h in search_hits if "patents.google.com" in h.get("url", "").lower() or "patent" in h.get("url", "").lower()
        ]

        for cand in patent_candidates[:2]:
            url = cand.get("url")
            if not url:
                continue

            extracted_text = ""
            # Try headless Chromium Playwright extraction
            try:
                if self.browser:
                    nav_res = self.browser.navigate(url, headless=True, timeout=12.0)
                    if nav_res.get("success") or nav_res.get("status") in (200, None):
                        ext_res = self.browser.extract_markdown(max_chars=2000)
                        if ext_res.get("success") and ext_res.get("content"):
                            extracted_text = ext_res["content"].strip()
            except Exception as e:
                logger.debug("Playwright extraction failed on %s: %s", url, e)

            # Fallback to requests-based web_fetch if Playwright didn't return text
            if not extracted_text:
                try:
                    fetch_res = self.web.fetch_webpage(url, max_chars=2000)
                    if fetch_res.get("content"):
                        extracted_text = fetch_res["content"].strip()
                except Exception:
                    pass

            if extracted_text:
                deep_snippets.append({
                    "title": cand.get("title", "Prior Art Document"),
                    "url": url,
                    "snippet": extracted_text[:1500],
                })

        return {
            "queries_executed": queries,
            "search_hits": search_hits[:6],
            "deep_snippets": deep_snippets,
        }

    def _score_invention_rubric(
        self,
        item_id: str,
        title: str,
        category: str,
        description: str,
        target_audience: str,
        other_input: Dict[str, Any],
        research_data: Dict[str, Any],
        skill_info: Dict[str, Any],
        optimalx_notes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Query ModelRouter with fresh context to score Dimensions A, B, C, and D."""
        skill_body = skill_info.get("body", "")

        # Format research evidence for model prompt
        hits_text = "\n".join(
            [f"- **{h.get('title')}** ({h.get('url')}):\n  {h.get('snippet', '')}" for h in research_data.get("search_hits", [])]
        ) or "No direct web matches identified."

        deep_text = "\n\n".join(
            [f"### Deep Extract: {s.get('title')} ({s.get('url')}):\n{s.get('snippet')}" for s in research_data.get("deep_snippets", [])]
        ) or "None."

        system_prompt = (
            "You are an expert commercial viability evaluator and patent research analyst for early-stage invention concepts.\n"
            "Analyze the given concept strictly against the 4-dimension evaluation rubric provided below.\n\n"
            "--- SKILL SPECIFICATION & RUBRIC ---\n"
            f"{skill_body}\n"
            "------------------------------------\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Evaluate 4 core dimensions on an integer scale from 0 to 25:\n"
            "   - technical_feasibility (0-25): Manufacturing maturity, off-the-shelf components vs unproven science.\n"
            "   - market_demand (0-25): Pain point urgency, paying demographic clarity, solution necessity.\n"
            "   - novelty_and_fto (0-25): Freedom to operate, distinct mechanism vs prior art identified.\n"
            "   - margin_potential (0-25): Target BOM under 20-40% of retail price, margins viability.\n"
            "2. Total Score MUST equal the sum of the 4 dimensions (total_score = technical_feasibility + market_demand + novelty_and_fto + margin_potential).\n"
            "3. Output MUST be a single, valid JSON object with EXACTLY this structure:\n"
            "{\n"
            '  "summary": "1-2 sentence executive outcome summary with overall viability score",\n'
            '  "total_score": <integer 0-100>,\n'
            '  "scores": {\n'
            '    "technical_feasibility": <int 0-25>,\n'
            '    "market_demand": <int 0-25>,\n'
            '    "novelty_and_fto": <int 0-25>,\n'
            '    "margin_potential": <int 0-25>\n'
            "  },\n"
            '  "rubric_breakdown": {\n'
            '    "technical_feasibility": "<detailed rationale & evidence>",\n'
            '    "market_demand": "<target audience & pain intensity justification>",\n'
            '    "novelty_and_fto": "<prior art comparison & patentability assessment>",\n'
            '    "margin_potential": "<BOM estimation & retail margin rationale>"\n'
            "  },\n"
            '  "prior_art_found": [\n'
            '    "<Patent number or product name with 1-line relevance>"\n'
            "  ],\n"
            '  "milestones": [\n'
            '    "<Immediate next development milestone 1>",\n'
            '    "<Immediate next development milestone 2>",\n'
            '    "<Immediate next development milestone 3>"\n'
            "  ],\n"
            '  "markdown_portfolio": "<Optional full formatted markdown portfolio>"\n'
            "}\n"
            "Respond ONLY with valid JSON."
        )

        user_content = (
            f"Please evaluate this invention concept:\n\n"
            f"**Item ID:** {item_id}\n"
            f"**Title:** {title}\n"
            f"**Category:** {category}\n"
            f"**Description:** {description or 'No detailed description provided.'}\n"
            f"**Target Audience:** {target_audience or 'General market'}\n"
        )
        if other_input:
            extra_keys = {k: v for k, v in other_input.items() if k not in ("title", "category", "description", "target_audience", "id")}
            if extra_keys:
                user_content += f"**Additional Metadata:** {json.dumps(extra_keys, ensure_ascii=False)}\n"

        if optimalx_notes:
            notes_lines = []
            for n in optimalx_notes:
                loc = n.get("location") or n.get("title") or "Personal Note"
                snip = n.get("snippet") or n.get("text") or n.get("summary") or ""
                notes_lines.append(f"- **Note Location:** {loc}\n  {snip.strip()}")
            notes_text = "\n".join(notes_lines)
            user_content += (
                f"\n--- USER'S OPTIMALX NOTES & ORIGINAL PROJECT CONTEXT ---\n"
                f"{notes_text}\n"
                f"---------------------------------------------------------\n"
            )

        user_content += (
            f"\n--- BACKGROUND PRIOR ART & MARKET RESEARCH FINDINGS ---\n"
            f"Queries Executed: {', '.join(research_data.get('queries_executed', []))}\n\n"
            f"Search Results:\n{hits_text}\n\n"
            f"{deep_text}\n"
            f"--------------------------------------------------------\n\n"
            "Evaluate and return the required JSON evaluation object."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            response: ModelResponse = self.router.chat(messages=messages)
            raw_text = response.content or ""
            parsed = extract_json_from_text(raw_text)

            if parsed and isinstance(parsed, dict) and "scores" in parsed:
                return self._normalize_evaluation_dict(parsed, title)
            elif parsed and isinstance(parsed, dict) and "total_score" in parsed:
                return self._normalize_evaluation_dict(parsed, title)
            else:
                logger.warning("Model response did not contain valid JSON dictionary. Attempting heuristic parsing.")
                return self._heuristic_fallback_scoring(raw_text, title, research_data)
        except Exception as e:
            logger.error("Error invoking ModelRouter for invention scoring on item %s: %s", item_id, e)
            return self._heuristic_fallback_scoring("", title, research_data)

    def _normalize_evaluation_dict(self, data: Dict[str, Any], title: str) -> Dict[str, Any]:
        """Normalize score numbers, clamp bounds (0-25 per dimension), and ensure math consistency."""
        scores_raw = data.get("scores") or {}
        dim_a = int(max(0, min(25, scores_raw.get("technical_feasibility", 15))))
        dim_b = int(max(0, min(25, scores_raw.get("market_demand", 15))))
        dim_c = int(max(0, min(25, scores_raw.get("novelty_and_fto", 15))))
        dim_d = int(max(0, min(25, scores_raw.get("margin_potential", 15))))

        total_score = dim_a + dim_b + dim_c + dim_d
        scores = {
            "technical_feasibility": dim_a,
            "market_demand": dim_b,
            "novelty_and_fto": dim_c,
            "margin_potential": dim_d,
        }

        breakdown = data.get("rubric_breakdown") or {}
        summary = data.get("summary") or f"Commercial evaluation completed for '{title}' (Overall score: {total_score}/100)."
        prior_art = data.get("prior_art_found") or []
        milestones = data.get("milestones") or [
            "Build bench prototype or CAD feasibility model",
            "Conduct formal patent attorney freedom-to-operate review",
            "Quote component BOM with commercial suppliers",
        ]

        return {
            "summary": summary,
            "total_score": total_score,
            "scores": scores,
            "rubric_breakdown": {
                "technical_feasibility": breakdown.get("technical_feasibility", "Feasibility assessed based on off-the-shelf components."),
                "market_demand": breakdown.get("market_demand", "Assessed based on pain point urgency and target demographics."),
                "novelty_and_fto": breakdown.get("novelty_and_fto", "Assessed against active patent disclosures."),
                "margin_potential": breakdown.get("margin_potential", "Assessed based on estimated BOM and retail markups."),
            },
            "prior_art_found": prior_art,
            "milestones": milestones,
            "markdown_portfolio": data.get("markdown_portfolio"),
        }

    def _heuristic_fallback_scoring(self, text: str, title: str, research_data: Dict[str, Any]) -> Dict[str, Any]:
        """Heuristic fallback parser if model fails to emit pure JSON."""
        # Try extracting numbers via regex
        def find_num(pattern: str, default: int = 15) -> int:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    val = int(m.group(1))
                    return max(0, min(25, val))
                except Exception:
                    pass
            return default

        dim_a = find_num(r"technical[\w\s&/\-]*?[:\s\-]+(\d+)", 16)
        dim_b = find_num(r"market[\w\s&/\-]*?[:\s\-]+(\d+)", 15)
        dim_c = find_num(r"(?:novelty|freedom)[\w\s&/\-]*?[:\s\-]+(\d+)", 14)
        dim_d = find_num(r"(?:margin|cost)[\w\s&/\-]*?[:\s\-]+(\d+)", 15)

        total_score = dim_a + dim_b + dim_c + dim_d
        scores = {
            "technical_feasibility": dim_a,
            "market_demand": dim_b,
            "novelty_and_fto": dim_c,
            "margin_potential": dim_d,
        }

        prior_art = [h.get("title", "") for h in research_data.get("search_hits", [])[:3] if h.get("title")]

        return {
            "summary": f"Commercial viability evaluation for {title}: Score {total_score}/100.",
            "total_score": total_score,
            "scores": scores,
            "rubric_breakdown": {
                "technical_feasibility": f"Scored {dim_a}/25 on technical feasibility.",
                "market_demand": f"Scored {dim_b}/25 on market pain point demand.",
                "novelty_and_fto": f"Scored {dim_c}/25 on novelty vs prior art.",
                "margin_potential": f"Scored {dim_d}/25 on margin potential.",
            },
            "prior_art_found": prior_art,
            "milestones": [
                "Develop prototype bench validation",
                "Perform formal prior art patent search",
                "Perform supplier quoting and BOM optimization",
            ],
            "markdown_portfolio": None,
        }

    def _render_portfolio_markdown(
        self,
        item_id: str,
        title: str,
        category: str,
        evaluation: Dict[str, Any],
        research_data: Dict[str, Any],
        optimalx_notes: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Render a standardized portfolio markdown document adhering to SKILL.md template."""
        # Use model-generated portfolio if provided and well-formed
        custom_md = evaluation.get("markdown_portfolio")
        if custom_md and len(custom_md.strip()) > 150 and "#" in custom_md:
            return custom_md.strip()

        scores = evaluation.get("scores", {})
        total_score = evaluation.get("total_score", 0)
        breakdown = evaluation.get("rubric_breakdown", {})
        summary = evaluation.get("summary", "No executive summary provided.")
        prior_art = evaluation.get("prior_art_found") or []
        milestones = evaluation.get("milestones") or []

        prior_art_lines = "\n".join([f"- **{pa}**" for pa in prior_art]) if prior_art else "- No direct 1-to-1 prior art identified in search window."
        queries_conducted = ", ".join([f"`{q}`" for q in research_data.get("queries_executed", [])])
        milestones_lines = "\n".join([f"- [ ] {m}" for m in milestones])

        optimalx_section = ""
        if optimalx_notes:
            note_bullets = "\n".join(
                [f"- **Location:** {n.get('location', 'Personal Note')}\n  {n.get('snippet', '') or n.get('text', '') or n.get('summary', '')}" for n in optimalx_notes]
            )
            optimalx_section = f"\n## OptimalX Reference Notes\n{note_bullets}\n"

        return f"""# {title}
**Item ID:** {item_id}  
**Category:** {category}  
**Overall Viability Score:** {total_score}/100  

## Executive Summary
{summary}

## Rubric Breakdown
| Dimension | Score (0-25) | Rationale & Evidence |
|---|---|---|
| Technical Feasibility | {scores.get('technical_feasibility', 0)}/25 | {breakdown.get('technical_feasibility', 'N/A')} |
| Market Demand | {scores.get('market_demand', 0)}/25 | {breakdown.get('market_demand', 'N/A')} |
| Novelty & Freedom to Operate | {scores.get('novelty_and_fto', 0)}/25 | {breakdown.get('novelty_and_fto', 'N/A')} |
| Margin Potential | {scores.get('margin_potential', 0)}/25 | {breakdown.get('margin_potential', 'N/A')} |
{optimalx_section}
## Prior Art Findings
- **Keyword Searches Conducted:** {queries_conducted}
{prior_art_lines}

## Next Milestones
{milestones_lines}
"""

    # =========================================================================
    # Generic Procedural Execution (Domain-Agnostic Skills)
    # =========================================================================

    def _process_generic_item(
        self,
        item: QueueItem,
        context: Dict[str, Any],
        skill_info: Dict[str, Any],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Process an arbitrary item using generic SKILL.md guidelines."""
        skill_body = skill_info.get("body", "Execute procedural steps for the input item.")
        item_id = item.id
        inp = item.input or {}
        title = inp.get("title") or inp.get("name") or inp.get("item_name") or item_id

        optimalx_notes = self._retrieve_optimalx_reference_notes(title=str(title))

        system_prompt = (
            "You are an autonomous executor for batch procedural workflows.\n"
            "Follow the procedure, guidelines, and output format defined below:\n\n"
            "--- SKILL SPECIFICATION ---\n"
            f"{skill_body}\n"
            "---------------------------\n\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "summary": "1-sentence outcome summary",\n'
            '  "artifact_markdown": "Full markdown or text artifact to be saved to disk",\n'
            '  "data": {}\n'
            "}"
        )

        user_content = f"Item ID: {item_id}\nInput Data: {json.dumps(inp, ensure_ascii=False)}"
        if optimalx_notes:
            notes_text = "\n".join([f"- **Note:** {n.get('location', 'Personal Note')}\n  {n.get('snippet', '') or n.get('text', '') or n.get('summary', '')}" for n in optimalx_notes])
            user_content += (
                f"\n\n--- USER'S OPTIMALX NOTES & ORIGINAL PROJECT CONTEXT ---\n"
                f"{notes_text}\n"
                f"---------------------------------------------------------\n"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            response: ModelResponse = self.router.chat(messages=messages)
            parsed = extract_json_from_text(response.content) or {}
            summary = parsed.get("summary") or f"Processed item {item_id}"
            artifact_content = parsed.get("artifact_markdown") or response.content or f"# Output for {item_id}\n\nProcessed successfully."
            data_payload = parsed.get("data") or inp
            if optimalx_notes:
                data_payload["optimalx_reference"] = [
                    {
                        "location": n.get("location") or n.get("title"),
                        "snippet": n.get("snippet") or n.get("text") or n.get("summary"),
                    }
                    for n in optimalx_notes
                ]

            artifacts_dir = output_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            artifact_file = artifacts_dir / f"{item_id}.md"

            with open(artifact_file, "w", encoding="utf-8") as af:
                af.write(artifact_content + "\n")

            return {
                "summary": summary,
                "artifacts": [str(artifact_file)],
                "data": data_payload,
            }

            artifacts_dir = output_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            artifact_file = artifacts_dir / f"{item_id}.md"

            with open(artifact_file, "w", encoding="utf-8") as af:
                af.write(artifact_content + "\n")

            return {
                "summary": summary,
                "artifacts": [str(artifact_file)],
                "data": data_payload,
            }
        except Exception as e:
            logger.error("Failed generic processing for item %s: %s", item_id, e)
            return {
                "summary": f"Processed item {item_id} (fallback)",
                "artifacts": [],
                "data": inp,
            }
