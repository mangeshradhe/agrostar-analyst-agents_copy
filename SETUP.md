# Agrostar Analyst Agents — Team Setup Guide

These are AI analysts that answer business questions directly from our BigQuery data warehouse.
Available analysts: `/dvs-analyst`, `/sales-analyst`, `/wms-analyst`, `/b2b-ledger`, `/saathi-app`

> **Updates are automatic.** Once you set this up, any improvements pushed to the repo are silently pulled at the start of each session. No action needed from you.

---

## Prerequisites (do once)

1. **Install Claude Code CLI**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```
   Then run `claude login` and sign in with your Anthropic account.

2. **Set up BigQuery MCP** — this gives Claude access to our data warehouse
   ```bash
   claude mcp add bigquery -- npx -y @modelcontextprotocol/server-bigquery --project-id agrostar-data
   ```
   When prompted, authenticate with your Agrostar Google account.

3. **Allow BigQuery queries in your local settings** — run this once to pre-approve the data tools:
   ```bash
   mkdir -p "$(pwd)/.claude"
   ```
   Then open `~/.claude/settings.json` and add your BigQuery MCP tool IDs to the allow list.
   *(Ask Darpan if you need help finding your MCP tool IDs — run `claude mcp list` to see them.)*

---

## Setup (5 minutes)

**Step 1 — Clone the repo**
```bash
git clone https://github.com/rudra124/agrostar-analyst-agents.git
cd agrostar-analyst-agents
```

**Step 2 — Open Claude Code in this folder**
```bash
claude
```

That's it. All analysts are now available as slash commands.

---

## Using an Analyst

```
/dvs-analyst
> What is the overall DVS program health for May 2026?

/sales-analyst
> Show me top 10 SKUs by revenue this month

/wms-analyst
> Which warehouses have the highest pending order backlog?
```

Ask in plain English. The analyst writes and runs the BigQuery SQL, then gives you the answer with insights.

---

## How Updates Work

Every time you start a session (first prompt of the day), the agents **silently pull the latest version** from GitHub. If Darpan improves an analyst — better SQL, new metrics, new business context — you get it automatically with no action required.

---

## Adding a New Analyst

When Darpan ships a new analyst (e.g. `/procurement-analyst`):
- It appears automatically at the start of your next session
- No setup required

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Slash command not found | Make sure you opened `claude` from inside the `agrostar-analyst-agents` folder |
| BigQuery permission denied | Re-run `claude mcp add bigquery ...` and re-authenticate |
| Getting stale answers | Run `git pull origin main` manually in the folder, then restart claude |
| MCP not connected | Run `claude mcp list` to verify the BigQuery MCP shows as connected |
