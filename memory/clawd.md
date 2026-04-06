# Clawd Memory

Persistent preferences and long-lived context for the Telegram assistant.

## Preferences
- Add memories by asking Clawd to remember something for later or by setting a standing response preference.
- Todo list rule: When adding todos, find the latest task file. If it's less than 5 days old, add to that file. If it's 5+ days old, ask "want me to add to that last file or make a new one for today?" Don't create a new file every day - treat task files more like weekly rolling lists.
- Keep replies concise and straight to the point. No fluff.
- Don't glaze / no unnecessary compliments or praise.
- Todo list formatting: keep high-level items roughly equal in effort within their category. If a task is big, break it into sub-items (indented) so each sub-item is comparable effort to sibling items. Be extremely concise — no verbose descriptions.
- Task files use weekly format: tasks/WXX-YYMMDD.md where XX is ISO week number and YYMMDD is the Monday of that week. Weeks run Monday to Sunday. When creating a new week's file, carry over all unchecked items from the previous week.

## Projects
- Parameter Golf (openai/parameter-golf) competition tracker: vault note at projects/parameter-golf-tracker.md, cron job every 6h for updates.

## User
- GitHub username: IslamTayeb. gh CLI authenticated on EC2 with repo/org/workflow scopes.

## Email Filters
- suppress_topic: duke daily

## Vault Structure
- whoami/ directory in Obsidian vault: resume.md (LaTeX resume), behavioral.md (personal story/motivations), current.md (current courses, research, plans, side projects). Update current.md when Islam's situation changes.
- whoami/current.md is a living document with Islam's current courses, TA roles, research, summer plans, side projects, and direction. Check it when context matters. Update it proactively when his situation changes, but always tell him when doing so.
- Obsidian vault directory map: - apm-overflow/: blog posts (drafts at root, brainstorming/, complete/, scrapped/) - personal/reflections/: late-night reflections, messy thoughts - projects/: quick research and planning for personal projects. Used when Islam asks Clawd to research a specific topic before starting a project, or to track progress. Reference material before and during a project. - scratch/: one-off tasks, periodically cleared - tasks/: weekly to-do lists (WXX-YYMMDD format) - tech/companies/: essays for company applications (e.g. "why join us"), needs migration from Google Docs - whoami/: resume, behavioral story, current state (living doc) - world-breaking-ideas.md: side project ideas that aren't tied to active research. Add new ideas here when Islam brainstorms.

_Last updated: 2026-04-06 07:56 UTC_
