---
name: obsidian-workflow
description: Use Clawd's Obsidian tools for dated tasks, persistent memory, note edits, research saves, and vault browsing.
---

# Obsidian Workflow

Use this skill when the request involves the user's Obsidian vault, task workflow, persistent memory, or markdown note edits.

## Durable Memory

- Persistent memory lives in `personal/clawd.md`.
- Only use the memory write tools when the user explicitly asks to remember or forget something for future conversations.
- When the user asks what Clawd remembers, read `personal/clawd.md` rather than paraphrasing from chat history.

## Task Notes

- Task notes live in `tasks/MMDDYY.md`.
- Resolve relative dates before acting:
  - `today`
  - `yesterday`
  - `tomorrow`
- If the target file already exists, append new todos to it.
- If the user references yesterday's list while it is currently March 10, 2026, that means `tasks/030926.md`.

## Note Editing

- Prefer editing the smallest relevant note instead of creating new files unnecessarily.
- Preserve existing markdown structure when appending or prepending.
- Use generic note tools for arbitrary `.md` files outside the dated task workflow.

## Research and Web

- Save research summaries under `research/<slug>.md`.
- Use web and paper search tools instead of fabricating summaries.
