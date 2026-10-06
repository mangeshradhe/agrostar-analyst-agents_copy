# LMD Delivery Call Analysis

Analysis of calls between LMD (Last Mile Delivery) partners and farmers, recorded and
LLM-extracted into structured fields, joined to the order/package fulfilment record for a full
**Order Journey** view. Latest refresh: 2026-10-05 (28,534 calls / 12,650 orders; 9,058 orders with a matched sales call). Order list is paginated, 50 per page.

This is kept separate from the main signal-engine (sales call) analysis one directory up.
May be combined later — not yet.

## Start here

No METHODOLOGY.md yet — conventions will move there once there are agreed definitions
worth pinning (e.g. how "conflict" or "acceptance" gets defined at order level).

## Data sources

- `agrostar-data.llm_transcripts.lmd_call_audits` — one row per call. LLM-extracted fields:
  call outcome/sentiment, farmer acknowledgement/acceptance/reorder intent, payment mode,
  amount-mismatch flags, delivery location, 10-point call-quality audit (`audit_a1`..`audit_a10`),
  conflict detection + summary, next action. 3,580 successful rows as of 2026-09-27 (up from 756 a
  day earlier — this table grows fast, re-run `scripts/extract_lmd_data.py` before trusting a stale
  export).
- `agrostar-data.prod_db_views.delivery_shippingpackage` — package-level delivery record, current
  status per package. Gives `order_id` for the package in a call. Cached metadata says 0 rows —
  ignore that, it's live with 7.5M+ rows (see `CLAUDE.md`).
- `agrostar-data.prod_db_views.delivery_shippingpackagestatushistory` — full status-change history
  per package (the "digital events" in the order journey timeline). See `CLAUDE.md` for the
  `reason`-field caveat.

## Files

| File | Contents |
|---|---|
| `CLAUDE.md` | Agent index — query rules, join keys, caveats |
| `README.md` | This file |
| `01-call_landscape_overview.md` | Descriptive pass: volumes, outcomes, reason codes, audit checklist pass rates, price-mismatch and conflict-call deep reads, data quality notes |
| `02-sales_call_reading_plan.md` | PLAN (not yet executed): Claude reads every Mustard sales-call transcript one at a time (newest first, batches of 50), transcript-only, with a quote validator and incremental re-runs — to show what happened on the sales call per order |
| `sql/order_rollup.sql` | Package→order join + order-level call rollup starter query |
| `scripts/extract_lmd_data.py` | Pulls calls + full order/package + status-history data from BQ into `output/*.json`. Run this first to refresh. |
| `scripts/render_dashboard.py` | Splices `output/*.json` into `dashboard/template.html` to produce the dashboard |
| `dashboard/lmd_call_dashboard.html` | **Open this in a browser.** KPIs, an outcome×reason pivot (replaces the old separate outcome/reason/audit/sentiment charts), a **call-vs-digital execution-gap pivot** (did the promised action — delivered/hold/return — actually get logged the same IST day as the call?), a date filter, an "execution gap only" filter, and every order expandable into its full **Order Journey** — package status history and calls interleaved chronologically, calls carrying a clearly-labeled AI-inference block (summary/conflict/tags/audit checklist), a per-order evidence callout explaining the match/gap verdict, and a 45-minute causal-linking heuristic connecting calls to nearby status changes. Regenerate via `scripts/render_dashboard.py` — don't hand-edit the rendered file, edit `dashboard/template.html` |

## Headline conclusions (2026-09-27, n=3,580 calls / 2,326 orders)

Numbers below are a full re-run at 4.7x the original volume — several shifted meaningfully, not just
grew proportionally:

- **Order-level outcomes flipped:** on-hold is now the *largest* bucket (32.4%), ahead of confirmed
  (26.1%) — at the original n=755 read, confirmed had led (35% vs 28%). Follow-up required 28.4%,
  cancelled 10.7%, not-enough-data 2.4%.
- **Repeat-contact rate rose to 33.7%** (was 24%) — a third of orders now need more than one call.
- **Cancellations ≠ conflicts, still true at scale.** Conflict rate is 1.4% of calls (51/3,580, down
  from ~1.9% at the smaller n) — most cancelled calls remain calm farmer-initiated holds/returns, not
  disputes. Re-reading all 51 (not just 14 this time): **delivery-delay anger is now the single largest
  conflict theme (~32%)**, well ahead of route/serviceability refusal (~20%) which led at the smaller n.
  New this pass: a handful of conflicts are **agent-driven** (agent rude/dismissive to the farmer), a
  category invisible to the audit checklist's 99.7%-pass "polite" step. See the outcome×reason pivot in
  the dashboard for what drives cancellations (`PRODUCT_NOT_REQUIRED` 39%) vs. on-hold (`DELIVERY_TIMING_ISSUE`
  49%, `FARMER_UNAVAILABLE` 37%).
- **Audit-checklist correlation checked and answered — negative result.** A3/A6/A7 (order
  confirmation/location/queries-resolved) pass rates on an order's *first* call do **not** meaningfully
  predict whether it needs a repeat call; for A6/A7 the multi-call group actually scores slightly
  *higher*. Repeat calls track operational reasons (timing, farmer unavailable), not first-call
  quality — don't keep treating the audit checklist as a repeat-call predictor.
- **Price-quote desync remains rare (13/3,580, 0.36%) but is three distinct mechanisms, not one** —
  re-reading all 13 shows booking-vs-delivery-app price desync (6/13, the original finding, now with
  more evidence it's real and recurring), a missing-items short-payment case (1/13, a fulfillment issue
  not a pricing one), and wrong-quantity/wrong-product disputes that the flag also catches (5/13) — read
  the summary, don't trust the raw flag alone to mean "price changed."
- **Agent-initiated holds are a much bigger share than the original read suggested** — 27% of all
  actions taken (up from single digits at n=755); farmer-initiated actions are still the majority (70%)
  but agent-side holds deserve their own look now, not just the assumption that repeat contact is
  farmer behavior.
- **4.6% of orders have more than one package** — the journey view now covers every package on the
  order, not just the one that happened to get called.
- **Geography fields are still mostly blank** (~5.4% of calls) — not usable for regional cuts at the
  call level; the populated subset also has a casing bug (`GUJARAT` vs `Gujarat`), same pattern as the
  sentiment field.
- **New: 32% of orders show a call-vs-digital execution gap** (517 of 1,611 evaluable orders — the
  1,611 exclude orders whose last call was follow-up-required/not-enough-data, which have no clear
  expected digital action). The dashboard now cross-checks each order's last call outcome
  (confirmed/on-hold/cancelled) against its package status history: did the matching digital action
  (delivered / put on hold / return initiated) actually get logged the same day (IST, since that's what
  "today"/"tomorrow" means in these calls), get logged late, or never show up at all. Gap rate is
  similar across all three outcomes (33-40%) — **unvalidated against ground truth so far**, see Open
  commitments below before treating this as a confirmed partner-compliance number.
- Full detail: [01-call_landscape_overview.md](01-call_landscape_overview.md) — fully re-run at current
  volume (2026-09-27), not just the original n=755 pass.

## Open commitments

- [x] Audit-checklist / repeat-call correlation — checked at n=3,580, negative result (see above).
- [x] Price-mismatch and conflict rates re-checked at n=3,580, full qualitative re-read of all 13 + 51
  calls (see above and `01-call_landscape_overview.md`) — both refined into sub-mechanisms, not just
  recounted.
- [x] `01-call_landscape_overview.md` fully re-run at current volume — every table and both qualitative
  reads redone, not just the README headline numbers.
- New: spot-check a sample of the newly-added execution-gap "Not executed" orders against real-world
  delivery status — some of the 32% could be a system-update lag (driver delivered, app not touched)
  rather than a true non-delivery; don't quote 32% as confirmed partner non-compliance until checked.
- Investigate the agent-initiated-hold share and the agent-driven-hostility conflict cluster surfaced
  by the 01-overview re-run — both suggest the LMD-agent side of these calls needs its own closer look.
- Decide whether `PRODUCT_NOT_REQUIRED` reason calls should feed the main signal-engine (demand signal
  overlap) if/when the two projects are combined.
