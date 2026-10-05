---
name: browser-automation
version: 1.0.0
description: Headless Chromium browser automation, clean DOM Markdown extraction via trafilatura, form interaction, and screenshot inspection.
author: eidos-core
tags: [web, browser, playwright, scraping, dom]
tools_required:
  - browser_open
  - browser_extract
  - browser_click
  - browser_type
  - browser_screenshot
  - browser_evaluate
  - browser_close
---

# Headless Browser Automation Procedure

## 1. Objective
Navigate websites, extract structured Markdown articles, interact with dynamic Single Page Applications (SPAs), fill forms, and take page screenshots using headless Playwright Chromium.

## 2. Operating Principles
- **Clean Content Extraction:** Prefer `browser_extract` to pull clean, readable Markdown from the DOM rather than taking full-page screenshots or guessing visual coordinates.
- **Navigation:** Use `browser_open` with the target URL. If JavaScript rendering requires time, follow up with `browser_extract` or `browser_evaluate`.
- **Form & Element Interaction:** Use `browser_type` to fill form fields and `browser_click` with CSS selectors (e.g. `'button[type=submit]'`, `'input[name=q]'`, `'text=Log In'`).
- **JavaScript Execution:** Use `browser_evaluate` for custom DOM queries, scrolling (`window.scrollBy(0, 500)`), or reading dynamic state.
- **Resource Cleanup:** Always invoke `browser_close` when browsing is completed to release browser memory and processes.
