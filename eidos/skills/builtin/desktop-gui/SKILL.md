---
name: desktop-gui
version: 1.0.0
description: Interactive desktop GUI navigation, screen capture, visual grounding, and synthetic mouse/keyboard input on Linux Mint (X11).
author: eidos-core
tags: [desktop, gui, x11, visual-grounding, input]
tools_required:
  - get_active_window
  - take_screenshot
  - click_element
  - click
  - double_click
  - type_text
  - press_key
  - hotkey
  - speak_aloud
---

# Desktop GUI Navigation Procedure

## 1. Objective
Interact with graphical desktop applications (e.g. text editors, settings dialogs, terminal emulators, graphical utilities) via visual grounding and synthetic input on Linux Mint (X11).

## 2. Operating Principles
- **Verify Before Clicking:** First use `get_active_window` or `take_screenshot` to confirm the target application is focused and in the expected visual state.
- **Natural Language Grounding:** Prefer `click_element` with a descriptive target label (e.g. "Save button", "Search input", "Close tab") over guessing raw coordinates.
- **Coordinate Actions:** If `click_element` cannot locate the target or precise coordinates are known, use `click` or `double_click` with exact (x, y) coordinates.
- **Keyboard Input:** Ensure the target input field or window is focused before emitting `type_text` or `press_key`.
- **Keyboard Shortcuts:** Use `hotkey` with an array of keys (e.g. `["ctrl", "s"]`, `["ctrl", "alt", "t"]`, `["alt", "f4"]`) for standard desktop shortcuts.
- **Teardown:** When GUI interactions are finished, call `close_skill` to return to base kernel mode or `finish_task` with the final result.
