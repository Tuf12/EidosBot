---
name: web-research
version: 1.0.0
description: Web search, article fetching, file downloads, sharing files with the web app, and opening URLs or local files in the user's desktop browser.
author: eidos-core
tags: [web, research, search, download, share, browser]
tools_required:
  - web_search
  - web_fetch
  - download_file
  - open_in_browser
  - share_file
---

# Web Research & Document Delivery Procedure

## 1. Objective
Perform live web searches, extract online article texts, download files directly to the workspace, open webpages/documents on the user's desktop screen, and register generated files with the web app.

## 2. Operating Principles
- **Search & Synthesis:** Use `web_search` for current query results across Brave / DuckDuckGo. Follow up with `web_fetch` on specific high-signal URLs to extract clean body text.
- **Downloading Files:** Use `download_file` with the source URL and destination filename to save PDFs, patents, or datasets directly into `~/EidosWorkspace/downloads/`.
- **Desktop Visuals:** When the user asks to "pull it up on my PC", "show me the patent", or "open this in my browser", use `open_in_browser` with the URL or local file path to launch it in their default desktop viewer.
- **Web App File Delivery:** The web gateway cannot access raw host paths on its own. Before finishing a task, invoke `share_file` for every document, markdown report, or dataset the user should view or download in the web UI.
- **Citations:** Include clickable markdown links in the format `[Page Title](https://...)` in your chat responses and task summaries.
