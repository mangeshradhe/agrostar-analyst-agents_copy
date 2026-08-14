---
description: Summarize this project's scope and available commands for someone new (or just checking what's changed) — lists every command in .claude/commands/ with its actual current scope, pulled live rather than from a hardcoded list.
---

Give the requester a concise orientation to this project. Don't just recite `ONBOARDING.md` from memory — that file can drift out of date; **derive the command list live** so it's always accurate:

1. `ls .claude/commands/*.md` to get the current file list.
2. For each file, get its real scope in one pass:
   - If it has YAML frontmatter (`---` at the top), use the `description:` field.
   - Otherwise, read the first ~15 lines past the `## Pre-Approved Permissions` block — the role/scope line is right there (e.g. "You are a specialized analyst for **X** at Agrostar...").
   - If a command has an authorization gate restricting it to a specific person (like `drishti-daily.md`), note that restriction explicitly — don't silently omit it or silently list it as open to everyone.
3. Present as a table: command name, one-line scope, any restriction.
4. Below the table, remind them:
   - For ad-hoc questions not covered by a packaged command, they can just ask directly — `CLAUDE.md` drives BigQuery table selection and query conventions.
   - `/refresh` pulls latest changes from origin if they suspect this list is behind what's actually pushed.

Keep the whole response tight — a table plus 2-3 lines, not a full re-walk of `ONBOARDING.md`. If the requester seems to want the fuller onboarding narrative (context, example questions, "why" for each domain), point them to `ONBOARDING.md` directly rather than reproducing it here.
