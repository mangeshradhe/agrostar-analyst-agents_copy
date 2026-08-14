---
description: Call-recording insights from genesys_db.disposition_data — either (a) cross-join call quality/disposition to farmer order outcomes (returns, delivery), or (b) qualitative synthesis of call text (ai_summary, entities) for any topic/question. Runs a BQ fetch (dedup + 60s-filter + B2C-farmer filter baked in) then either aggregate stats or sampled LLM synthesis, for any date range.
---

You are running the **call-insights** skill for this project. It has two modes — use one or both depending on what the requester wants:

- **Outcome mode**: does call quality/disposition relate to what happened to the order (returned vs. delivered)? Pure SQL/stats, full population, no LLM needed.
- **Qualitative mode**: what are farmers actually saying / agents actually doing, on some topic or question? Needs LLM synthesis of free text — must run on a **sample**, never the full population.

---

## Step 0 — Check data freshness

`genesys_db.disposition_data` lags behind real time (confirmed 2026-08-14: data available only through 2026-08-05, ~9 days stale). Before committing to a date range, run:

```sql
SELECT MAX(created_on) AS max_ts FROM `agrostar-data.genesys_db.disposition_data`
```

If the requester's range extends past `max_ts`, tell them upfront and clip the range — don't silently query a range that will return partial/zero data.

---

## Step 1 — Confirm inputs

Ask the requester for what's missing:

1. **Which mode(s)** — outcome cross-join, qualitative synthesis, or both.
2. **Duration** — date range. Accept natural language and convert to `YYYY-MM-DD`. Check against Step 0 first.
3. **For outcome mode**: what outcome to test against (current script supports delivered-vs-returned for CSR-booked orders; note to the requester if they want a different outcome — e.g. repeat purchase, upsell — the query will need extending).
4. **For qualitative mode**:
   - The **topic/question** to investigate (e.g. "what are farmers unhappy about", "how well is X being pitched").
   - Any **filter** to scope the population first — a regex against `ai_summary`, a disposition level, an agent, a call-score range, and the **channel** (`b2c` farmer calls / `b2b` Saathi-retailer calls / `any`). Write the regex yourself based on how farmers/agents would phrase it; keep it specific (overly broad nets dilute the signal).
   - **Sample size and method** (random / top-N / bottom-N by a column / stratified by a column). Do not skip this ask unless the filtered population is already small (<100). Rule of thumb carried over from this org's other call-analysis work: ~250 gives directional signal (~±8pt margin); ~600-800 tightens margins to ~±3-4pt and supports theme clustering. These are inherited defaults, not yet independently validated against this specific project's data — treat as a reasonable starting point, not gospel.
   - **Analysis engine — Claude (in-session) or Gemini.** Default to **Claude in-session**: read the fetched sample directly and synthesize, as this skill's own script/pipeline supports natively (see Step 3). Offer **Gemini** as an alternative only if the requester wants it — that means routing through a separate, already-existing Gemini-based call-analysis pipeline that lives in another project (`convindashboard`, currently under its `cottontrends/` folder — see that project's `program-insights` skill for current paths, since this is external infra this skill doesn't own and its location may change), with its own fetch/classify scripts (topic/pattern-scoped, checkpointed/resumable, requires `GOOGLE_API_KEY`) and a different fetch shape than this skill's `fetch_calls.py`/`sample_<label>.csv` — tell the requester it means running a separate pipeline, not just a flag flip here.

---

## Step 2 — Fetch via the script

```bash
cd "/Users/sunilj/My Drive/Work/claude/agrostar-analyst-agents" && \
python3 call_insights/fetch_calls.py \
  --from "<DATE_FROM>" --to "<DATE_TO>" \
  [--channel b2c|b2b|any] \
  [--pattern '<REGEX>'] [--disposition '<LEVEL_1>'] [--agent '<AGENT_ID>'] \
  [--min-score N] [--max-score N] \
  [--sample-size N --sample-method random|top|bottom|stratified [--sample-order-by call_score] [--stratify-by disposition_levels_level_1]] \
  [--join-orders] \
  --out-dir "<SCRATCHPAD_DIR>" --label "<SLUG>"
```

This script already handles the known data-quality gotchas — **do not hand-roll the query without them**:
- `disposition_data` has ~3-4x row duplication per call (same `meta_data_call_id`, identical rows) — deduped via `QUALIFY ROW_NUMBER()`.
- Calls under 60s are excluded (too short to carry real conversation).
- Channel filter (`--channel`, default `b2c`) matches `meta_data_client_id` against `csr_farmer.mobile_1/2/3` and classifies via `farmer_type`: `'Farmer'` (or `creation_source LIKE 'CSR%'/'APP%'`) = b2c; `LIKE '%b2b%'` (values seen: `'B2B Profile'`, `'B2B Partner'`, `creation_source = 'B2B'`) = b2b. Unmatched numbers are excluded from both — use `--channel any` to include everyone.
- `meta_data_client_id` is **plaintext** in this raw table (unlike `prod_db_views` fields) — joins to encrypted columns go through `` `agrostar-data.AEAD_encryption_keys.ENCRYPT`(meta_data_client_id) ``, never the reverse.

Outputs land in `--out-dir`:
- `outcome_<label>.csv` (if `--join-orders`) — order-level rows with `call_score`, `score_bucket`, `is_delivered`, `is_returned`. Structured, ready for direct aggregation.
- `sample_<label>.csv` (if `--sample-size`) — raw-text rows (`ai_summary`, `ai_insights_question/answer`, `custom_entities_*`, `meta_data_recording_url`, etc.) for LLM synthesis.
- `manifest_<label>.json` — filters applied + counts returned, for the record.

If a fetch returns 0 rows, check Step 0 (date range vs. data freshness) before assuming the filter is too narrow.

---

## Step 3 — Analyze

**Outcome mode:** `outcome_<label>.csv` is fully structured — load with pandas, group by `score_bucket`, compute return rate per bucket directly. No LLM step needed. Note the match rate (rows with non-null `call_score` — the join only catches CSR-booked orders that could be matched to a same-day, same-agent call).

**Qualitative mode, Claude engine (default):** read `sample_<label>.csv` and synthesize themes/sentiment/answer the requester's question directly against the raw text fields. Use structured columns (`custom_entities_*`, `lead_interest`, `disposition_levels_*`) for anything they cover directly (e.g. counting products discussed) rather than re-deriving them from free text by eye — read the free text (`ai_summary` etc.) for whatever the structured columns don't capture (sentiment/theme/narrative).
- For samples that fit in context (up to a few hundred rows), just read and classify directly — don't spin up subagents for something this size.
- For larger samples (500+), if delegating to subagents: **explicitly instruct each subagent not to spawn further sub-agents**, and keep batches to ~150-250 rows each. Nested self-delegation on a job like this has previously caused a session-limit crash and lost over 1,000 rows of work in this org's call-analysis tooling — don't repeat that failure mode.

**Qualitative mode, Gemini engine (if requested):** this means switching entirely to that separate Gemini-based pipeline in `convindashboard` (its own fetch + classify scripts, topic/pattern-scoped, checkpointed/resumable, needs `GOOGLE_API_KEY`) rather than continuing with this project's `fetch_calls.py` output — see that project's `program-insights` skill for the current folder/commands, since this skill doesn't own that pipeline and its location may change. Reach for this when the sample is large (500-800+) and resumable/checkpointed batch classification is worth the extra setup, or the requester specifically wants a report in that pipeline's existing format.
- `meta_data_recording_url` links may point to recordings that get purged after some days on the Genesys side (observed elsewhere in this org's call tooling as ~7 days) — this hasn't been independently re-verified for this project's pipeline, but treat old recording links as possibly dead, and tell the requester upfront if they're relying on being able to listen back.

**If running both modes together:** use outcome mode to find an interesting cohort (e.g. a score bucket or disposition with an outsized return rate), then re-run Step 2 in qualitative mode scoped to that cohort (`--min-score`/`--max-score`/`--disposition`) to get sampled "why" narrative color on top of the quantitative finding.

---

## Step 4 — Present results

- State the date range, filters, and (for qualitative mode) sample size/method used — so findings read as what they are: exhaustive for outcome mode, directional/indicative for qualitative mode.
- Outcome mode: bucket-level table + the standout finding.
- Qualitative mode: top themes with counts and a couple of representative quotes/summaries per theme.
- Offer to publish a polished report as an Artifact (load the `dataviz`/`artifact-design` skill) if the findings warrant a shareable deliverable, rather than just a chat table.
