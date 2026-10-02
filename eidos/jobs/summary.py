"""Leaderboard and ranking summary generator for batch jobs.

Aggregates completed evaluation items from queue.json into:
- RANKING_SUMMARY.md: Markdown tables categorized into Tier 1, Tier 2, and Tier 3.
- RANKING_SUMMARY.csv: Tabular export compatible with Excel, Google Sheets, Notion.
- RANKING_SUMMARY.json: Structured machine-readable metrics and rankings.
"""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from eidos.jobs.types import JobQueue, QueueItem

logger = logging.getLogger(__name__)


class RankingSummaryGenerator:
    """Generates tiered commercial viability rankings from evaluated batch job queues."""

    def __init__(self) -> None:
        pass

    def generate(
        self,
        queue: JobQueue,
        output_dir: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Aggregate queue items and export RANKING_SUMMARY md, csv, and json.

        Args:
            queue: The JobQueue object with processed items.
            output_dir: Optional output directory override (defaults to queue.output_dir).

        Returns:
            Dictionary with generation metrics and written file paths.
        """
        out_dir = Path(output_dir or queue.output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        scored_items: List[Dict[str, Any]] = []
        incomplete_items: List[Dict[str, Any]] = []

        for item in queue.items:
            extracted = self._extract_item_metrics(item, out_dir)
            if extracted["is_completed_scored"]:
                scored_items.append(extracted)
            else:
                incomplete_items.append(extracted)

        # Sort scored items by total_score descending, then ID ascending for determinism
        scored_items.sort(key=lambda x: (-x["total_score"], x["id"]))

        # Assign ranks
        for idx, item_data in enumerate(scored_items, start=1):
            item_data["rank"] = idx

        # Partition into tiers
        tier_1 = [it for it in scored_items if it["total_score"] >= 80]
        tier_2 = [it for it in scored_items if 60 <= it["total_score"] < 80]
        tier_3 = [it for it in scored_items if it["total_score"] < 60]

        total_count = len(queue.items)
        completed_count = len(scored_items)
        incomplete_count = len(incomplete_items)
        avg_score = (
            round(sum(it["total_score"] for it in scored_items) / completed_count, 1)
            if completed_count > 0
            else 0.0
        )
        timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # 1. Render Markdown
        md_content = self._render_markdown(
            job_name=queue.job_name,
            total_count=total_count,
            completed_count=completed_count,
            incomplete_count=incomplete_count,
            avg_score=avg_score,
            timestamp_str=timestamp_str,
            tier_1=tier_1,
            tier_2=tier_2,
            tier_3=tier_3,
            incomplete_items=incomplete_items,
        )

        md_path = out_dir / "RANKING_SUMMARY.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 2. Render CSV
        csv_path = out_dir / "RANKING_SUMMARY.csv"
        self._write_csv(
            csv_path=csv_path,
            scored_items=scored_items,
            incomplete_items=incomplete_items,
        )

        # 3. Render JSON
        json_path = out_dir / "RANKING_SUMMARY.json"
        self._write_json(
            json_path=json_path,
            queue=queue,
            total_count=total_count,
            completed_count=completed_count,
            incomplete_count=incomplete_count,
            avg_score=avg_score,
            timestamp_str=timestamp_str,
            tier_1=tier_1,
            tier_2=tier_2,
            tier_3=tier_3,
            incomplete_items=incomplete_items,
            scored_items=scored_items,
        )

        logger.info(
            "Compiled ranking leaderboard for '%s': %d evaluated, avg %.1f/100 -> %s",
            queue.job_name,
            completed_count,
            avg_score,
            md_path,
        )

        return {
            "success": True,
            "markdown_path": str(md_path),
            "csv_path": str(csv_path),
            "json_path": str(json_path),
            "total_items": total_count,
            "completed_items": completed_count,
            "incomplete_items": incomplete_count,
            "average_score": avg_score,
            "tier_1_count": len(tier_1),
            "tier_2_count": len(tier_2),
            "tier_3_count": len(tier_3),
            "needs_review_count": incomplete_count,
        }

    def _extract_item_metrics(self, item: QueueItem, out_dir: Path) -> Dict[str, Any]:
        """Extract standardized scoring and metadata from a queue item."""
        inp = item.input or {}
        res = item.result or {}
        res_data = res.get("data") if isinstance(res.get("data"), dict) else {}

        item_id = item.id
        title = inp.get("title") or res_data.get("title") or inp.get("name") or item_id
        category = inp.get("category") or res_data.get("category") or "General"

        # Sanitize for markdown tables (replace pipe characters)
        title = str(title).replace("|", "-").strip()
        category = str(category).replace("|", "-").strip()

        is_completed = (item.status == "completed") and bool(res)

        # Extract scores
        scores = res.get("scores") or res_data.get("scores") or {}
        total_score = res.get("total_score")
        if total_score is None:
            total_score = res_data.get("total_score")

        # Sub-scores
        tech_score = scores.get("technical_feasibility")
        market_score = scores.get("market_demand")
        novelty_score = scores.get("novelty_and_fto")
        margin_score = scores.get("margin_potential")

        # Fallback total score calculation
        if total_score is None:
            sub_vals = [v for v in [tech_score, market_score, novelty_score, margin_score] if isinstance(v, (int, float))]
            if sub_vals:
                total_score = int(sum(sub_vals))
            else:
                total_score = 0
        else:
            try:
                total_score = int(round(float(total_score)))
            except (ValueError, TypeError):
                total_score = 0

        # Determine portfolio link
        portfolio_rel = res_data.get("relative_artifact")
        if not portfolio_rel:
            artifacts = res.get("artifacts") or []
            if artifacts:
                try:
                    first_art = Path(artifacts[0])
                    if out_dir in first_art.parents or first_art.is_relative_to(out_dir):
                        portfolio_rel = str(first_art.relative_to(out_dir))
                    else:
                        portfolio_rel = f"portfolios/{item_id}.md"
                except Exception:
                    portfolio_rel = f"portfolios/{item_id}.md"
            else:
                portfolio_rel = f"portfolios/{item_id}.md"

        # Determine Tier
        if total_score >= 80:
            tier_label = "Tier 1"
        elif total_score >= 60:
            tier_label = "Tier 2"
        else:
            tier_label = "Tier 3"

        return {
            "id": item_id,
            "title": title,
            "category": category,
            "status": item.status,
            "error": item.error or "Processing incomplete",
            "is_completed_scored": is_completed,
            "tech_score": tech_score if tech_score is not None else "-",
            "market_score": market_score if market_score is not None else "-",
            "novelty_score": novelty_score if novelty_score is not None else "-",
            "margin_score": margin_score if margin_score is not None else "-",
            "total_score": total_score,
            "tier": tier_label,
            "portfolio_rel": portfolio_rel,
            "summary": res.get("summary", ""),
            "milestones": res_data.get("milestones", []),
            "prior_art": res.get("prior_art_found", []),
        }

    def _render_markdown(
        self,
        job_name: str,
        total_count: int,
        completed_count: int,
        incomplete_count: int,
        avg_score: float,
        timestamp_str: str,
        tier_1: List[Dict[str, Any]],
        tier_2: List[Dict[str, Any]],
        tier_3: List[Dict[str, Any]],
        incomplete_items: List[Dict[str, Any]],
    ) -> str:
        """Render the complete RANKING_SUMMARY.md document."""
        lines = [
            f"# {job_name} — Commercial Viability Ranking Summary",
            "",
            f"**Total Inventions Evaluated:** {total_count}  ",
            f"**Completed Successfully:** {completed_count}  ",
            f"**Needs Review / Incomplete:** {incomplete_count}  ",
            f"**Average Viability Score:** {avg_score:.1f} / 100  ",
            f"**Generated On:** {timestamp_str}  ",
            "",
            "---",
            "",
            "## 🏆 Top Tier: High Commercial Viability (Scores 80–100)",
            "*Recommended for immediate bench prototyping, formal prior art search, and provisional patent filing.*",
            "",
        ]

        if tier_1:
            lines.extend(self._render_table(tier_1))
        else:
            lines.append("*No inventions qualified for Tier 1 (Scores 80–100).*")
            lines.append("")

        lines.extend([
            "---",
            "",
            "## 💡 Tier 2: Moderate Viability / Promising with Caveats (Scores 60–79)",
            "*Strong potential, but requires addressing specific weaknesses (e.g., crowded patent space or high initial BOM).*",
            "",
        ])

        if tier_2:
            lines.extend(self._render_table(tier_2))
        else:
            lines.append("*No inventions in Tier 2 (Scores 60–79).*")
            lines.append("")

        lines.extend([
            "---",
            "",
            "## ⚠️ Tier 3: Low Viability / High Prior Art Saturation / Speculative (Scores < 60)",
            "*Direct prior art disclosed, unviable retail margins, or weak consumer demand.*",
            "",
        ])

        if tier_3:
            lines.extend(self._render_table(tier_3))
        else:
            lines.append("*No inventions in Tier 3 (Scores < 60).*")
            lines.append("")

        if incomplete_items:
            lines.extend([
                "---",
                "",
                "## ⚠️ Needs Review / Incomplete Items",
                "*The following items could not be evaluated or encountered errors during execution.*",
                "",
                "| ID | Title | Category | Status | Details / Error |",
                "|---|---|---|---|---|",
            ])
            for it in incomplete_items:
                err_clean = str(it["error"]).replace("|", "-").replace("\n", " ").strip()
                lines.append(f"| {it['id']} | {it['title']} | {it['category']} | {it['status']} | {err_clean} |")
            lines.append("")

        return "\n".join(lines).strip() + "\n"

    def _render_table(self, items: List[Dict[str, Any]]) -> List[str]:
        """Render a GitHub-flavored Markdown table for a tier of scored items."""
        rows = [
            "| Rank | ID | Title | Category | Tech (25) | Market (25) | Novelty (25) | Margin (25) | Total (100) | Portfolio |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for it in items:
            port_link = f"[Portfolio]({it['portfolio_rel']})"
            rows.append(
                f"| {it['rank']} | {it['id']} | {it['title']} | {it['category']} | "
                f"{it['tech_score']} | {it['market_score']} | {it['novelty_score']} | "
                f"{it['margin_score']} | **{it['total_score']}** | {port_link} |"
            )
        rows.append("")
        return rows

    def _write_csv(
        self,
        csv_path: Path,
        scored_items: List[Dict[str, Any]],
        incomplete_items: List[Dict[str, Any]],
    ) -> None:
        """Write tabular summary to CSV."""
        headers = [
            "Rank",
            "ID",
            "Title",
            "Category",
            "Tech_Score",
            "Market_Score",
            "Novelty_Score",
            "Margin_Score",
            "Total_Score",
            "Tier",
            "Status",
            "Portfolio_Path",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for it in scored_items:
                writer.writerow([
                    it["rank"],
                    it["id"],
                    it["title"],
                    it["category"],
                    it["tech_score"],
                    it["market_score"],
                    it["novelty_score"],
                    it["margin_score"],
                    it["total_score"],
                    it["tier"],
                    it["status"],
                    it["portfolio_rel"],
                ])

            for it in incomplete_items:
                writer.writerow([
                    "-",
                    it["id"],
                    it["title"],
                    it["category"],
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "Needs Review",
                    it["status"],
                    it.get("error", ""),
                ])

    def _write_json(
        self,
        json_path: Path,
        queue: JobQueue,
        total_count: int,
        completed_count: int,
        incomplete_count: int,
        avg_score: float,
        timestamp_str: str,
        tier_1: List[Dict[str, Any]],
        tier_2: List[Dict[str, Any]],
        tier_3: List[Dict[str, Any]],
        incomplete_items: List[Dict[str, Any]],
        scored_items: List[Dict[str, Any]],
    ) -> None:
        """Write machine-readable summary to JSON."""
        data = {
            "job_id": queue.job_id,
            "job_name": queue.job_name,
            "skill_name": queue.skill_name,
            "generated_at": timestamp_str,
            "stats": {
                "total_items": total_count,
                "completed_items": completed_count,
                "incomplete_items": incomplete_count,
                "average_viability_score": avg_score,
                "tier_1_count": len(tier_1),
                "tier_2_count": len(tier_2),
                "tier_3_count": len(tier_3),
            },
            "tiers": {
                "tier_1_high_viability": tier_1,
                "tier_2_moderate_viability": tier_2,
                "tier_3_low_viability": tier_3,
                "needs_review": incomplete_items,
            },
            "rankings": scored_items,
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
