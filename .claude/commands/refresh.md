---
description: Sync this project's local checkout with the latest commits on origin — fetches, pulls (merge only, never rebase/force), and reports what changed, calling out anything that affects this session's own capabilities (CLAUDE.md, .claude/commands/*.md, skill scripts).
---

Run this whenever the requester wants the local repo brought up to date with what teammates have pushed (e.g. before relying on a skill/table-mapping that might have changed, or just to check for new capabilities).

## Step 1 — Check for local changes first

```bash
git status -sb
```

If there are **uncommitted changes to tracked files**, stop and tell the requester: they need to commit or stash those first (per this project's git safety rules — never auto-stash or discard without asking). Untracked files with no overlap with incoming changes are not a blocker; mention them but proceed.

## Step 2 — Fetch and see what's incoming

```bash
git fetch origin
git log HEAD..origin/$(git rev-parse --abbrev-ref HEAD) --oneline
```

- If this is empty and `git status -sb` already showed the branch even with `origin/...`, tell the requester the repo is already current and stop here.
- Otherwise, this is the list of incoming commits — worth a glance before merging so nothing pulled in is a surprise.

## Step 3 — Pull

```bash
git pull origin $(git rev-parse --abbrev-ref HEAD)
```

Plain merge only — never `--rebase`, never `-X ours`/`-X theirs`, never `--force`. If the pull reports a conflict, stop and surface it to the requester rather than resolving it unilaterally.

## Step 4 — Report

Tell the requester:
- The commits just pulled in (short log from Step 2).
- `git diff --stat HEAD@{1} HEAD` for what files changed.
- **Explicitly flag** if the pull touched `CLAUDE.md`, anything under `.claude/commands/`, or scripts backing a skill (e.g. `call_insights/`) — these change what this session itself knows/can do, so re-read the relevant file(s) if the requester's next ask might depend on them.
- If local has commits that are still unpushed to `origin` after the pull (i.e. `main` shows `ahead`), mention it — this command only pulls, it never pushes on its own.
