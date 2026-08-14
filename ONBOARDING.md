# Agrostar Analyst Agents — Start Here

This project turns Claude Code into a data analyst with direct, read-only access to Agrostar's BigQuery warehouse (`agrostar-data`), plus a set of packaged commands for specific business domains.

## Quick start

Just ask a question in plain English — e.g. "How many orders were cancelled in Maharashtra last month?" Claude reads `CLAUDE.md` in this repo, which maps your question to the right BigQuery table(s), writes the SQL, runs it, and gives you the numbers with a short insight. No setup needed beyond having this repo open in Claude Code — BigQuery read-only access, Python execution, and Slack are all pre-approved.

If you just want a refresher on what's available without asking a specific question yet, run **`/capabilities`**.

## Packaged commands (`/command-name`)

These are pre-built specialist agents for specific domains — each knows its own tables, KPIs, and conventions in depth.

| Command | Scope |
|---|---|
| `/sales-analyst` | Order-to-invoice lifecycle, B2B/B2C sales performance. Golden rule: sales = invoiced value, not order GMV. |
| `/dvs-analyst` | DVS (Direct from Village Store) — online orders fulfilled from Saathi stores via LMD partner delivery. |
| `/wms-analyst` | Warehouse Management System — inbound, storage, picking, packing, dispatch, returns inside Fulfillment Centres. |
| `/rf-analyst` | Retailer Financing — invoice discounting program (Agrostar + NBFC lenders) providing working capital to Saathi partners. |
| `/under-writing` | Saathi partner onboarding & underwriting — funnel, AIDR document reading, BRE credit decisions. |
| `/b2b-ledger` | B2B credit ledger — partner collections, settlements, credit limits, overdue/interest. |
| `/coco-analyst` | COCO (Company Owned Company Operated) stores — POS sales, inventory, fulfillment, reconciliation. |
| `/store-visit` | Field team visit activity, store coverage, collection follow-ups, expansion pipeline. |
| `/saathi-app` | Saathi-APP (B2B partner mobile app) behavioural events — login, browsing, ordering, payments, farmer leads. |
| `/product-guy` | Product/design thinking partner for Saathi APP features (currently: Field Visit Planner). |
| `/return-ai-analysis` | Predicting and preventing B2C order returns (RTO) — understanding delivery-failure causes, not just post-hoc return stats. |
| `/call-insights` | Call-recording insights from `genesys_db.disposition_data` — call quality vs. order outcomes, or sampled qualitative synthesis (sentiment, themes, products discussed) for B2C/B2B calls, any date range. |
| `/tech-newsletter` | Drafts the Tech & Product monthly newsletter (Gmail draft output). |
| `/drishti-daily` | ⚠️ Restricted to Darpan Pathar only — autonomous daily DVS program run. Will refuse for anyone else. |

New commands get added over time — run `/capabilities` for the live list with current descriptions, or `/refresh` first if you suspect this repo is behind what teammates have pushed.

## Keeping this up to date

- **`/refresh`** — pulls the latest commits from `origin` and tells you if anything changed that affects capabilities here (schema docs in `CLAUDE.md`, commands, skill scripts).
- **`CLAUDE.md`** — the source of truth for BigQuery table schemas, the intent→table mapping, and query conventions. Worth a skim if you're going to ask a lot of ad-hoc questions rather than use a packaged command.

## Example questions to try

- "What are the top 10 SKUs by revenue this month?"
- "How many new farmers registered last week?"
- "Which state has the highest return rate?"
- "Show me daily GMV trend for the last 30 days"
- "What's the B2B call sentiment and key products discussed in the last week?" (routes to `/call-insights`)
