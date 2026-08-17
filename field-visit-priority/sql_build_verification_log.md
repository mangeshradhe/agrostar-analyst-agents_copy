# SQL Build Verification Log — field_visit_priority_prototype.sql

Real bugs caught while building and testing the prototype query (2026-08-17), each found by running
real BigQuery output — not by inspection — per [[feedback_verify_with_actual_data]]. Kept here so the
"why does the code look like this" isn't lost.

## 1. WSF/CN category-taxonomy mismatch (Targets axis)
`aop_offline_online_fy27` (target side) carries `category_repo='WSF'` as its own native bucket, separate
from `CN`. `sale_return_b2c_b2b` (actuals side) has no such bucket — it lumps all WSF/NPK fertilizer SKUs
under `category_repo='CN'`. Undetected, this made WSF show a fake 100% shortfall (₹0 actual vs ₹49.2L
target) every month, while silently crediting CN with revenue it didn't actually explain.
**Fix**: reclassify the actuals side with the same `Product_group LIKE '%wsf%' OR '%npk%' → 'WSF'` rule
before joining to the target side. Verified fix: WSF shortfall corrected to a sane 49.9%, CN rose from a
falsely-flattered 49.6% to a real 64.4%.

## 2. Primary-reason selection used weighted contribution instead of raw percentile
First version picked each partner's displayed "reason" by comparing `weight × percentile` across the 4
signals. This just favors whichever signal has the highest weight (Collection, 45%) almost regardless of
how extreme that specific partner's actual number is — Collection won as the stated reason for 71% of all
partners, even ones with a far more extreme Visit Gap or Targets exposure. Weight should decide the
**score**; the **reason** should be whichever raw percentile is most extreme for that individual partner.
**Fix**: reason picked by comparing unweighted percentiles. Verified fix: reason distribution rebalanced to
Visit Gap 35% / Targets 24% / Collection 24% / Revenue 17% — no single reason dominating unrealistically.

## 3. `active_score` type mismatch across the three tracks broke UNION ALL
Active's score is a FLOAT64 (division result), Churned's is FLOAT64 (SUM of a FLOAT column), Onboarding's
was an untyped INT64 expression (`IF(...,1000,0) + LEAST(int_days, 999)`). BigQuery's `UNION ALL` requires
identical column types across all branches — caught at dry-run time, not at execution.
**Fix**: explicit `CAST(... AS FLOAT64)` on all three tracks' score expressions.

## 4. `zoho_leads.contact_name` is an INTEGER (a contact ID), not a text name
Despite the column name, `contact_name` is typed INTEGER in the source table (confirmed via
`get_table_info` earlier in the session) — it's an internal ID reference, not a display name. Using it
directly as `name` in the Onboarding track broke the same `UNION ALL` (STRING/STRING/INT64 mismatch) and,
even once cast, doesn't give a rep-readable name.
**Fix**: `CAST(Contact_name AS STRING)` keeps the query correct. **Known open gap**: the Onboarding track
currently shows a numeric ID where a name should be — needs a proper name lookup before this ships for real
rep use, flagged inline in the SQL comments.

## 5. Onboarding→rep-role join fanned out ~600,000x
`okr_data_live` is partner-grain (one row per partner, not per rep). Joining an onboarding lead's owner
email directly against `okr_data_live.sm`/`.tm` matched **every partner row that rep manages**, not once per
rep — a lead owned by an SM with 23 partners produced 23 duplicate rows for that one lead. Aggregate row
count exposed it immediately: 611,605 "Onboarding" rows for the SM view vs. 8,142 for Active (should be the
same order of magnitude, a few hundred to a few thousand, not 75x larger than Active).
**Fix**: built a deduped `rep_directory` (`DISTINCT email → role` from `sm`/`tm` columns) and joined leads
against that 1:1 lookup instead of the raw partner table. **Re-verified**: 6,377 SM rows / 4,866 TM rows
(287 distinct SM reps, 126 distinct TM reps, 5,284 distinct leads) — right order of magnitude next to
Active's 8,142/8,508, fan-out eliminated.

## Capacity, suppression, day-of-week, slot-split — added and verified 2026-08-17

- **daily_quota vs menu_size, deliberately distinct**: strict cadence math (span x required visits / 26
  working days) gives SM ~2.4/day, TM ~3.0/day on average — far below the colleague-doc's flat "20/day",
  which was never actually derived from real span-of-control math. `menu_size` (daily_quota x5, floor 10)
  is the recommendation-list size a rep actually sees, sized larger on purpose for route flexibility —
  flagged as a judgment call (the x5 multiplier), not backtested.
- **Suppression**: SM 15-day window, TM 30-day window (both derived from the stated cadence requirement,
  not the colleague-doc's baseline-derived 7/15-day windows), Active track only.
- **Track slot split**: proportional to each track's real fleet-wide population (Active/Churned/Onboarding),
  not the colleague-doc's unvalidated 14/4/2 — a defensible v1, not outcome-validated.
- **Day-of-week**: modest +5 score nudge on Wed (if overdue>0) and Fri (if revenue declining), plus a reason-
  text annotation ("- and it's collections day" / "- good day to convert this into a sale"). Deliberately
  additive, not a weight restructure, to keep the validated percentile math intact.

**Full pipeline verified end-to-end with real output**: SM reps average ~6 Active + ~2.5 Churned + ~3.3
Onboarding rows/day (~12 total); TM reps ~8.5 + 3.0 + 3.9 (~15 total) — real, actionable list sizes, not
degenerate. Spot-checked one real SM (`aashish.jain@agrostar.in`): Active track led by two POG unsold-stock
flags (₹3.68L, ₹2.09L), then visit-gap partners in correct descending order (16/47/84/28 days); Churned
track showed two real recovery cases (₹91,100 and ₹37,579 owed). Reason text reads correctly, ranking is
coherent.

## Test suite (2026-08-17) — 2 more real bugs caught, both fixed and reverified

A spot check on one rep isn't a test suite. Ran 5 explicit invariant checks against the full real output:

| # | Test | Before fix | After fix |
|---|---|---|---|
| 1 | No duplicate (rep, track, partner) rows | **234 violations** | 0 |
| 2 | No rep's total row count exceeds their `menu_size` | **180 reps violated** | 0 |
| 3 | No NULL/empty/malformed (`%null%`) reason text | 0 | 0 |
| 4 | No partner appears in both Active and Churned under the same rep | 0 | 0 |
| 5 | Onboarding about-to-close isn't degenerately 0% or 100% | 1,382 / 11,245 (12.3%) | — (sane, unchanged) |

**Bug #6 — onboarding duplicate leads (test 1's root cause)**: `zoho_leads` has up to 4 rows per
`reference_customer_id` (re-engaged/repeat leads) — never deduped, so a partner with multiple lead records
appeared multiple times in the same rep's Onboarding list. **Fix**: `QUALIFY ROW_NUMBER() OVER (PARTITION BY
reference_customer_id ORDER BY about-to-close-first, Final_sd_date DESC) = 1` in `onboarding_leads`.

**Bug #7 — menu-size overflow (test 2's root cause)**: the original per-track cap used
`GREATEST(CEIL(menu_size * share), 1)` independently on each of the 3 tracks — ceiling-rounding plus a
forced minimum of 1 on every track can make the three caps sum to more than `menu_size`. **Fix**: FLOOR the
first two tracks' caps (Active, Churned) and give Onboarding the exact remainder
(`menu_size - active_cap - churned_cap`) — guarantees the three always sum to exactly `menu_size`, no
overflow possible. Trade-off, accepted: a track can now legitimately get 0 slots if its true share rounds
to zero (no forced minimum-1 anymore) — respecting the rep's real daily capacity matters more than
guaranteeing every track always shows at least one row.

All 5 tests re-run after both fixes: **0 violations across the board.**

## Regression suite — after adding score-breakdown columns (2026-08-17)

Developer request: expose the raw value + percentile for each of Active's 4 signals as separate output
columns, so the app/devs can audit "why" beyond the reason text (see OUTPUT SCHEMA in the .sql file header).
Added `overdue_amount`, `pog_unsold_amount`, `collection_pctile`, `revenue_decline_pct`, `revenue_pctile`,
`visitgap_pctile`, `target_exposure`, `target_pctile` to all 3 tracks (real values for Active, NULL for
Churned/Onboarding — documented inline as intentional, not a gap).

**Tool note**: the first 4 attempts at re-running the full regression suite timed out. Root cause: each of
7 independent correlated subqueries (`SELECT ... FROM final_output WHERE ...`) forced BigQuery to
re-materialize the ENTIRE upstream pipeline separately, since CTEs aren't auto-cached across references.
Fixed by restructuring to scan `final_output` exactly once via window functions (`ROW_NUMBER()`,
`COUNT() OVER (...)`) feeding a single aggregate SELECT — slot-ms dropped from timeout-inducing to 8.98M,
completed on first try after the fix.

| Test | Result |
|---|---|
| 1. No duplicate (rep, track, partner) rows | 0 |
| 2. No rep's total rows exceed `menu_size` | 0 |
| 3. No NULL/empty/malformed reason text | 0 |
| 4. Farmer_id in both Active AND Churned under the same rep | 0 (see caveat below) |
| 5. Active rows have non-NULL breakdown columns | 0 violations (all populated) |
| 6. Non-Active rows have NULL breakdown columns | 0 violations (all correctly NULL) |
| 7. `score` matches hand-recomputed `45×collection_pctile + 25×revenue_pctile + 15×visitgap_pctile + 15×target_pctile)/100 + day bonus`, rounded to 2dp | 0 mismatches >0.01 |

**Test 4 false-positive caught and resolved**: the first version of this check used
`COUNT(DISTINCT track) OVER (PARTITION BY rep_email, farmer_id)`, which flags 197 rows — but that counts
across all 3 tracks, so it can't distinguish "impossible Active+Churned overlap" from "harmless
Active+Onboarding overlap" (a partner can be active AND still have an old unclosed lead record sitting in
`zoho_leads` — two separate systems, not required to stay in sync). Verified directly at the source:
`partner_active JOIN partner_inactive USING (farmer_id)` returns **0 rows** — the genuinely impossible
overlap cannot happen, confirming `okr_data_live.status` is exactly one value per farmer_id as it must be.
The 197 were real Active+Onboarding overlaps, not a defect.

## Verified working (real BigQuery output, not just dry-run type-checking)
- Active track: 11,362 partners scored, all 4 signal joins confirmed 1:1 (no fan-out), reason distribution
  sane after fix #2.
- Churned track: reuses the same debits/recon CTEs as Active, re-pointed at INACTIVE — consistent with the
  ₹37.23 Cr / 3,831-partner number verified earlier in the session.
- Targets axis: territory×category shortfall correct after fix #1; partner-level cascade (8,583 partners
  with real exposure, median ₹90,825) verified before assembly.
