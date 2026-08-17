# field-visit-priority

BigQuery-based field-visit priority engine for AgroStar's SM/TM (Sales Manager/Territory
Manager) reps — for each rep, which B2B/Saathi partner to prioritize visiting today, and why.
Built for the Sept 2026 hackathon (`agrostar-data.hackathone2026_dataset`), verified against
real BigQuery output throughout (2026-08-17).

**Start here:** `priority_framework_FINAL.md` for the model (3 tracks, weighted signals, capacity
ceiling), then `field_visit_priority_prototype.sql` for the implementation.

## Files

| File | Contents |
|---|---|
| `priority_framework_FINAL.md` | The definitive spec: 3 tracks (Active/Onboarding/Churned), each signal's weight + plain-English reason, day-of-week reweighting (Wed/Thu/Fri), capacity ceiling, explicit out-of-scope + still-open items. |
| `field_visit_priority_prototype.sql` | The actual deliverable — full query, all 3 tracks, score-breakdown output columns, capacity/suppression/day-of-week logic. Header comment documents known limitations and the full output schema. |
| `sql_build_verification_log.md` | Real bugs caught while building the SQL (category-taxonomy mismatch, a weighting bug in reason-selection, type mismatches, a join fan-out that produced 600,000+ bogus rows, menu-size overflow) plus the regression-test suite results. Read before trusting or modifying the query. |
| `priority_logic_framework_notes.md` | Working history/rationale behind the final spec — why each decision was made, what was tried and corrected. |
| `data_access_ledger.md` | Dataset accessibility ground truth: what's confirmed queryable, what's blocked (`pristine_wms_views` dataset-level permission gap and everything it cascades into), what has no data source at all. |
| `panils_pog_query.md` | POG (unsold-on-shelf/Product-On-Ground) mechanics reference + verified compliance numbers by category. |
| `colleague_proposal_field_visit_copilot.md` | A colleague's competing proposal, kept as one input weighed against — not the adopted design. |
| `field_dashboard_data_sources.md` / `panils_query_data_sources.md` | Original prior-art queries this whole build is grounded in. |
| `hackathon2026_dataset_context.md` | Table map + caveats for all 23 views in `hackathone2026_dataset`. |

## Headline conclusions

- **2026-08-17** — 3-track (Active/Churned/Onboarding), 4-signal (Collection 45% / Revenue
  Opportunity 25% / Visit Gap 15% / Targets 15%) percentile-blended scoring engine, with
  day-of-week reweighting (Wed collections, Thu onboarding-readiness, Fri Red Friday), a
  capacity ceiling derived from real span-of-control data (not an assumed flat number), and
  suppression windows matching stated cadence (SM 15d / TM 30d). Verified end-to-end against
  real BigQuery output: SM reps average ~12 recommendations/day, TM ~15/day — sane, non-degenerate
  list sizes.
- Every signal, weight, and table substitution was verified against actual query output before
  being treated as final (see `sql_build_verification_log.md`) — this query is intended to ship
  into the core application, not stay a one-off analysis.

## Open commitments

- Targets axis currently uses AOP only — ROFO-priority-with-AOP-fallback (per rolling forecast
  availability) is designed but not yet implemented; blocked on resolving a unit mismatch
  (`one_view_rofo`'s `Gross_rev_*` columns are actually volume, not revenue).
- Onboarding target-count source (the "10 new first-orders" concept) not yet located in any table.
- `pristine_wms_views` dataset-level BigQuery permission gap (blocks `sku_cat_repo`, `aop_target`,
  `one_view_rofo` when queried via their own internal joins) — workaround in place for tables
  queried directly, not yet resolved at the source.
- CSV output sample intentionally **not included in this push** (contains real partner names +
  financial data across ~5,000 rows) — run the SQL directly against `agrostar-data` to regenerate.
