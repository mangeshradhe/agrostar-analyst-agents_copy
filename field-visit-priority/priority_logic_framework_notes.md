# Field Visit Priority Logic — Running Design Notes (SM/TM)

Consolidated decisions from the brainstorm so far. This is the file to read to get back up to speed — don't
re-derive from conversation history. Companion files: `data_access_ledger.md` (what's actually queryable),
`colleague_proposal_field_visit_copilot.md` (external input, not adopted wholesale), `panils_pog_query.md`
(POG mechanics + verified numbers), `field_dashboard_data_sources.md` / `panils_query_data_sources.md`
(existing prior-art queries).

## Model shape: 3 balanced axes + 1 hard constraint

The user's framing (2026-08-17): score every partner by balancing **what has happened** (retrospective),
**what the targets are** (forward benchmark), and **what the org is focused on right now** (intent) — plus
**capacity/cadence**, which is a hard ceiling on the output, not a fourth score to blend in.

### Axis 1 — What Happened (retrospective, partner-level)
- Revenue trend, OCP/DPD collections ageing — same shape as [[panils_query_data_sources]]'s `helth` CTE.
- Visit gap — `store_visits_v2 ∪ prod_db_views.visit`, deduped by (email, store, day).
- Returns/Complaints — **GAP, no ticketing table found** (same gap the colleague's doc flagged as unresolved
  and highest-priority; only `invoiced_report.is_return` exists, product-level only).
- POG risk (unsold-on-shelf) — see POG section below; corrected understanding as of this session.

### Axis 2 — Targets (territory-level, cascades down)
- `aop_target`/`one_view_rofo`-style targets are only available at territory × cluster × category × class
  grain — **no partner-level target exists, and none is needed**: territory-level gap + the span of
  partners under that node is sufficient (user's framing, confirmed workable).
- Two cascade options discussed: (a) flat urgency multiplier applied evenly to every partner under a lagging
  territory/SM/TM node, or (b) category-weighted allocation — since `aop_target` splits the gap by category
  (Seeds/CP/CN), a partner's own sales mix (already available from FY-wise revenue by `PL_NPL`/`class`)
  determines how much of *that specific* category shortfall they inherit. (b) is a real differentiator but
  more build effort; not yet decided which to implement for the deadline.
- **Unblocked and verified end-to-end (2026-08-17)**: turns out even simpler than planned —
  `optimized_reports_data.aop_offline_online_fy27` already has its own native `category_repo`/`class`
  columns, no `sku_cat_repo`/`item_mst`/`pl_product_class` join needed at all. Ran a real territory-level
  YTD target-vs-actual check: **83.0% overall attainment** (₹374.9 Cr target vs. ₹311.3 Cr actual,
  Apr-Aug 2026, 172 territories), with real underperformers surfaced (Tumkur 11.3%, Suryapet 11.9%,
  Jind 17.2%, Saharanpur 20.3%, Indore 21.6%). Full numbers, the lacs-scaling bug caught along the way, and
  the unresolved territory-name-mismatch caveat are in `data_access_ledger.md`.

### Axis 3 — Org Focus (day-of-week, verified against real data, not assumed)
Confirmed mechanistically linked across the week, not three independent facts:
| Day | Focus | Verified signal |
|---|---|---|
| Wed | Collections | Collection txn count/amount ~5-7x normal weekday |
| Thu | Onboarding lead pushed to "order-ready" (not order placement itself) | `zoho_leads.Final_sd_date` completions up to 10x normal weekday |
| Fri | Red Friday sales + first orders from Thursday's now-ready partners | Order count/revenue ~2.3-2.7x; first-order placements ~3-4x |
This is the actual mechanism for "what's the org focused on" — a calendar-driven reweighting, not a manual
campaign-weight knob. The scoring function needs a day-of-week parameter.

### Capacity/Cadence — hard ceiling, not a score
- Span of control, **verified** (not the quoted 30-40 SM / ~100 TM): SM median 23 (range 2-76 across 328
  reps), TM median 59 (range 13-181 across 130 reps). Real outliers exist — per-rep span needed, not a fleet
  constant.
- Required cadence (user-stated): SM visits each partner **2x/month minimum**, TM **1x/month minimum**.
- Achieved cadence among partner-months that got *any* visit: SM 3.42/month, TM 1.98/month — both already
  above the stated minimums. Suggests the real capacity problem may be **coverage** (some partners getting
  zero visits while others are over-visited), not raw under-frequency — not yet confirmed with a zero-visit
  coverage % pull.
- Working days: Mon-Sat per policy, Sunday + national holidays off. Actual data shows Sunday still runs
  ~15-18% of weekday visit volume — policy vs. practice gap. **No holiday-calendar table located yet** —
  needed for exact working-day math.

### POG (Product-On-Ground / unsold-on-shelf) — corrected twice this session, current understanding:
1. **Not a Seeds-only policy.** Configured per-SKU (`catalog_management_stocktrackingconfig.is_active`) —
   whichever SKUs are flagged, capture is **100% mandatory** for that partner, any category. The earlier
   read ("Seeds-focused practice") was wrong; corrected numbers below are a **compliance gap**, not a scope
   choice.
2. **Verified compliance rate** (captured ÷ total tracked partner-SKU rows, FY27-to-date): Seeds 75.4%,
   CP 16.6%, CN 1.6% — all against a 100% mandatory bar. CP/CN are badly out of compliance, which is a
   *stronger* visit-priority signal than "POG doesn't apply there."
3. **Can be closed via phone call, not only an in-person visit**, when the SM/TM is confident it doesn't
   need a physical check. So `is_captured=0` is a **task to close**, not automatically a consumer of scarce
   visit capacity — the priority logic needs a way to distinguish "needs a visit" from "can be closed by
   call," which isn't derivable from the data alone (it's rep judgment). Not yet resolved how this surfaces
   in the scoring/output — possibly a separate flagged task list rather than folded into the visit-ranking
   score.
4. Full numbers and the SQL mechanics are in `panils_pog_query.md`.

## Out of scope for now (user decision, 2026-08-17)
- **Returns/Complaints** — not important for deriving the framework right now. Dropped from active
  consideration; no further work on this axis unless revisited later.

## Resolved (2026-08-17)
- **Churned Recovery** — user's definition: an INACTIVE partner with outstanding balance still owed. Not a
  data gap at all — reuses the exact same OCP/pending-amount CTE as the Collections signal, just filtered to
  `okr_data_live.status='INACTIVE'` instead of `'ACTIVE'`. Real numbers: **3,831 INACTIVE partners, ₹37.23 Cr
  outstanding** — currently invisible to any existing scoring logic since inactive partners are excluded
  from ACTIVE-only visit lists. Treat as its own track (matching the colleague's doc's Active/Onboarding/
  Churned split), not blended into the Active partner score.
- **Territory-name mismatch** (Targets axis) — not a bug. One target row has `NULL` territory (filter it
  out); Jalna and Dhule genuinely have zero B2B actuals this FY under any spelling. See
  `data_access_ledger.md`.
- **Holiday calendar** — no table exists anywhere (checked `static_tables`/`static_tables_views`). India's
  fixed "National Holidays" (Republic Day, Independence Day, Gandhi Jayanti) = **3 in FY27**, general
  domain knowledge not sourced from BQ. Caveat: this is *not* the full non-working-day count — the larger
  festival/restricted-holiday calendar has no table and remains a real gap if precise capacity math needs it.

## Partially resolved — data exists, decision pending
- Onboarding "about-to-close" criterion: `zoho_leads.stage` has real values (`83_Pending for first Order`,
  `82_Pending for Limit Allocation`, `3 SD Pending or Incomplete Document`, etc.) — which stage(s) count as
  "about-to-close" is a threshold decision, not a missing-data problem.

## Colleague's doc — status as an input, not the design
`colleague_proposal_field_visit_copilot.md`: 3-track model (Active/Onboarding/Churned), daily pool of 20
(14/4/2 reserved slots), SM 7-day / TM 15-day suppression windows. Several of its own weights are
self-flagged "proposed, not signed off." Being used as one input to weigh, not adopted wholesale — our
Targets axis and day-of-week mechanism are not present in that doc at all.

## Not yet decided
- Flat vs. category-weighted target-gap cascade (Axis 2).
- How POG's call-vs-visit distinction surfaces in the output.
- Zero-visit coverage % — not yet pulled, would confirm/refute the "coverage not frequency" capacity read.
- Exact ranking/slot logic for the Churned Recovery track (colleague's doc precedent: 2 reserved daily
  slots, competes on its own ranking, not force-topped) — direction agreed, formula not yet built.

## Action items — done (2026-08-17)
- **POG rerun with `item_mst.category_code`**: done. Compliance rates essentially unchanged (Seeds 75.4%,
  CP 16.7%, CN 1.6% — only 1 row of ~45,000 shifted category vs. the original prefix-hack run). The original
  numbers in `panils_pog_query.md` are confirmed, no longer provisional.
- **Targets-axis build + verify**: done, see Axis 2 above and `data_access_ledger.md`.
