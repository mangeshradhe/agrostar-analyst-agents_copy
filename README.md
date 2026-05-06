# Agrostar Analyst Agents

A shared repository of Claude Code analyst personas for Agrostar's BigQuery data warehouse.

Each analyst is a slash command (`/analyst-name`) that gives Claude deep domain knowledge about a specific Agrostar program or function — so anyone on the team can ask business questions in plain English and get accurate SQL + insights back.

---

## Analysts Available

| Command | Domain | Owner |
|---------|--------|-------|
| `/dvs-analyst` | DVS (Direct from Village Store) program — demand funnel, SLA tracking, LMD performance, store health | Program Team |

---

## How to Set Up Locally

1. **Clone the repo** into your working directory:
   ```bash
   git clone git@github.com:agrostar/<repo-name>.git
   cd <repo-name>
   ```

2. **Open Claude Code** from this folder:
   ```bash
   claude
   ```
   Claude Code will automatically pick up `CLAUDE.md` (base warehouse context) and all slash commands in `.claude/commands/`.

3. **Connect BigQuery MCP** — each team member needs to configure the BigQuery MCP connector in their own Claude Code settings. This is a one-time setup per machine. The file `.claude/settings.local.json` is **gitignored** — your MCP permissions stay local.

4. **Test it:**
   ```
   /dvs-analyst
   > What is the health of the DVS program for May 2026?
   ```

---

## How to Contribute

### Adding context to an existing analyst
1. Create a branch: `git checkout -b feat/dvs-add-<context-description>`
2. Edit the relevant `.claude/commands/<analyst>.md`
3. Test your change locally — run a few queries through the analyst and verify the output is correct
4. Open a PR with a short description of what context you added and why
5. One other person reviews and approves before merging to `main`

### Creating a new analyst
1. Create a branch: `git checkout -b feat/new-<analyst-name>-analyst`
2. Copy `.claude/commands/dvs-analyst.md` as a starting template
3. Replace DVS-specific content with your domain
4. Add a row to the table above in this README
5. PR + review before merge

### Branch naming convention
- `feat/dvs-add-restock-context` — new context or tables
- `fix/dvs-wrong-join-key` — correcting a data bug
- `docs/update-readme` — documentation only

---

## Repo Structure

```
.
├── README.md                        ← this file
├── CLAUDE.md                        ← base Agrostar warehouse analyst (auto-loaded by Claude Code)
├── .gitignore
└── .claude/
    └── commands/
        ├── dvs-analyst.md           ← /dvs-analyst slash command
        └── ...                      ← future analysts go here
```

---

## Important Rules

- **Never commit** `.claude/settings.local.json` — it's gitignored. It contains your personal MCP permissions.
- **Never push** real farmer PII, phone numbers, or order data into this repo.
- **Always test** a query change locally before raising a PR.
- This repo is **private** — do not make it public or share the URL outside the team.
