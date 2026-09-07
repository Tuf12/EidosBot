"""Web Research and Autonomous Browser Engine."""

import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import urllib.parse

from bs4 import BeautifulSoup
import requests

from eidos.models.router import ModelRouter

logger = logging.getLogger(__name__)

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class WebResearchEngine:
    """
    Web Research Engine for:
    1. Fast Web Search via Brave Search API (with automatic fallback to DuckDuckGo / SearXNG)
    2. Deep Web Page Content Fetching and Text Extraction
    3. Research Synthesis and Summarization using ModelRouter
    4. File / PDF downloading
    """

    def __init__(
        self,
        brave_api_key: Optional[str] = None,
        model_router: Optional[ModelRouter] = None,
        optimalx_config_path: str = "~/.config/optimalx-desktop/config.json",
    ):
        self.router = model_router or ModelRouter()
        self.brave_api_key = brave_api_key or self._discover_brave_key(optimalx_config_path)

    def _discover_brave_key(self, config_path: str) -> str:
        """Attempt to read Brave API key from env or OptimalX config."""
        env_key = os.environ.get("BRAVE_API_KEY", "").strip()
        if env_key:
            return env_key

        p = Path(os.path.expanduser(config_path))
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    key = data.get("brave", {}).get("apiKey", "").strip()
                    if key:
                        return key
            except Exception:
                pass
        return ""

    def search(self, query: str, count: int = 5) -> List[Dict[str, Any]]:
        """
        Execute web search. Returns list of dicts with title, url, and snippet.
        """
        q_clean = query.strip()
        if not q_clean:
            return []

        # 1. Primary: Brave Search API if key is available
        if self.brave_api_key:
            try:
                headers = {
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                }
                params = {"q": q_clean, "count": max(1, min(count, 15))}
                resp = requests.get(BRAVE_SEARCH_ENDPOINT, headers=headers, params=params, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    web_results = data.get("web", {}).get("results", [])
                    extracted = []
                    for item in web_results:
                        title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                        description = re.sub(r"<[^>]+>", "", item.get("description", ""))
                        url = item.get("url", "")
                        extracted.append({
                            "title": title,
                            "url": url,
                            "snippet": description,
                            "source": "brave"
                        })
                    if extracted:
                        return extracted
            except Exception as e:
                logger.warning("Brave search query failed (%s). Attempting fallback.", e)

        # 2. Fallback: DuckDuckGo Instant Answer API / HTML scrape
        return self._fallback_search(q_clean, count)

    def _fallback_search(self, query: str, count: int) -> List[Dict[str, Any]]:
        """Fallback search using DuckDuckGo Instant Answer API."""
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
            resp = requests.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                abstract = data.get("AbstractText", "")
                abstract_url = data.get("AbstractURL", "")
                if abstract and abstract_url:
                    results.append({
                        "title": data.get("Heading", query),
                        "url": abstract_url,
                        "snippet": abstract,
                        "source": "duckduckgo_instant"
                    })

                for topic in data.get("RelatedTopics", [])[:count]:
                    if "Text" in topic and "FirstURL" in topic:
                        results.append({
                            "title": topic["Text"][:60] + "...",
                            "url": topic["FirstURL"],
                            "snippet": topic["Text"],
                            "source": "duckduckgo_related"
                        })

                if results:
                    return results[:count]
        except Exception as e:
            logger.debug("Fallback search failed: %s", e)

        return []

    def fetch_webpage(self, url: str, max_chars: int = 12000) -> Dict[str, Any]:
        """
        Fetch HTML from URL, clean noise, and return title, clean text, and metadata.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=12.0)
            resp.raise_for_status()

            # Parse and clean HTML
            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove non-content elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "form"]):
                tag.decompose()

            # Extract title
            title = ""
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Extract text preserving paragraphs and headers
            lines = []
            for elem in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "article", "section"]):
                text = elem.get_text(separator=" ", strip=True)
                if text and len(text) > 15:
                    lines.append(text)

            full_text = "\n\n".join(lines)
            if not full_text:
                full_text = soup.get_text(separator="\n", strip=True)

            # Cap characters
            clean_text = full_text[:max_chars]

            return {
                "success": True,
                "title": title,
                "url": url,
                "text": clean_text,
                "length": len(clean_text),
                "status_code": resp.status_code,
            }
        except Exception as e:
            logger.error("Failed to fetch webpage %s: %s", url, e)
            return {
                "success": False,
                "error": str(e),
                "url": url,
                "text": "",
                "status_code": getattr(e, "response", None) and e.response.status_code or 500,
            }

    def research_topic(self, topic: str, max_pages: int = 3) -> Dict[str, Any]:
        """
        Conduct complete research flow:
        1. Search for topic
        2. Fetch top pages
        3. Synthesize findings using ModelRouter
        """
        search_results = self.search(topic, count=max_pages)
        if not search_results:
            return {
                "success": False,
                "topic": topic,
                "summary": "No web search results found for the requested topic.",
                "sources": []
            }

        fetched_content = []
        for r in search_results[:max_pages]:
            url = r["url"]
            page = self.fetch_webpage(url, max_chars=4000)
            if page["success"] and page["text"]:
                fetched_content.append(
                    f"### Source: {page['title']} ({url})\n{page['text'][:2500]}"
                )

        combined_text = "\n\n---\n\n".join(fetched_content)
        prompt = (
            f"You are conducting research on the topic: '{topic}'.\n\n"
            f"Here is information extracted from search results:\n\n{combined_text}\n\n"
            f"Provide a clear, accurate, and concise research summary answering or explaining the topic."
        )

        model_resp = self.router.chat(messages=[{"role": "user", "content": prompt}])
        return {
            "success": True,
            "topic": topic,
            "summary": model_resp.content,
            "sources": [r["url"] for r in search_results],
        }

    def download_file(self, url: str, destination_path: str) -> Dict[str, Any]:
        """Download file (e.g. PDF, image, dataset) to disk."""
        dest = Path(os.path.abspath(os.path.expanduser(destination_path)))
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            resp = requests.get(url, stream=True, timeout=20.0)
            resp.raise_for_status()

            total_bytes = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)

            return {
                "success": True,
                "path": str(dest),
                "bytes_downloaded": total_bytes,
                "url": url,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "url": url}
