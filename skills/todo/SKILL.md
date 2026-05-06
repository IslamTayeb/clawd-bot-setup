---
name: todo
description: Split a raw Telegram /todo request into concise actionable Obsidian todos and save them to the current weekly task file.
user-invocable: true
---

# Todo Command

Use this skill when the user invokes `/todo <text>`.

Workflow:

- Treat the command text as raw capture, not as durable memory.
- Split it into short, concrete todo items. Prefer one clear action per item.
- Keep any explicit subtask structure by preserving leading indentation on subtask lines.
- Do not invent deadlines, priorities, or extra tasks.
- Call `add_todos` with the final item list. Omit `target_date` unless the user explicitly names a date.
- New todos are written to the current weekly task note, `tasks/W##-YYMMDD.md`.

For a command like:

```text
/todo prep for meeting: read paper, email Alice, and draft agenda
```

Call:

```json
{"items":["Read paper for meeting","Email Alice","Draft meeting agenda"]}
```
