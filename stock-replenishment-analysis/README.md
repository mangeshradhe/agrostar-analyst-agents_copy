# Stock Replenishment Analysis

Transfer-order and stock-movement analyses on BigQuery project `agrostar-data`. Two bodies of work so far:

1. **Budget-balancing feature review** (snapshot Jul 8, 2026) — the System TO – Budget Balancing feature (live Jul 7), which auto-creates stock-movement TOs between FCs. Files `01`–`04` + `queries.sql`.
2. **FY 2026-27 full transfer analysis** (Jul 13, 2026) — all transfer orders FY-to-date: classification, geography, sell-through, ping-pong waste, TCI. Report + methodology doc.

Shared conventions (classification rules, metric definitions, data caveats) live in `TRANSFER_ANALYSIS_METHODOLOGY.md` — read it before reusing any number or query from this folder.

## Files

| File | Contents |
|---|---|
| `TRANSFER_ANALYSIS_METHODOLOGY.md` | **Start here.** Source tables, TO classification (System/Manual/Manufacturing), geo tiers, metric definitions, exclusions, headline findings, data-quality issues, caveats. Companion to the HTML report |
| `FY2026-27_Stock_Transfer_Report.html` | Final FY26-27 transfer report (also published as Claude artifact + Slack canvas; links in methodology doc) |
| `01-system-to-budget-balancing-analysis.md` | Main budget-balancing report: verdict, scale, approval funnel, execution, network structure, manual benchmark, data-quality log, ranked recommendations |
| `02-approval-behavior-and-quantity-edits.md` | Deep-dive: approvers are binary (zero quantity edits); system re-allocation does the zeroing; unique SKU counts |
| `03-sku-and-lane-tonnage-generated-vs-approved.md` | Deep-dive: top SKUs & lanes by tonnage, generated vs approved; organic-manure veto pattern; LKO02 phantom freight |
| `04-benchmarks-and-side-findings.md` | All benchmark tables (Apr–Jun manual vs Jul system, first-10-days, units & tonnes, approved-only, yield), VTO series finding, field-semantics reference |
| `queries.sql` | Reusable BigQuery queries behind the budget-balancing tables |
| `b2b_clearance_expiry_analysis.sql` | Side analysis: B2B (InvoiceNo `U%`) clearance-offer split + near-expiry (<150d) billing by state, FY26-27 |

## Open commitment

**15 Aug 2026 — budget-balancing review**: measure 30-day sell-through of the first dispatch cohort (dispatches began ~30 Jun). Pre-agreed bar ~50% sold (benchmarks: Manual-BM 32%, System-UF 79%). Details in the methodology doc.

## Headline conclusions — budget balancing (as of Jul 8)

1. Core logic is sane: direction, product mix, lane footprint, approval yield (79% decided) and waste rate (10.6% qty) all match or beat the Apr–Jun manual benchmark; SKU coverage doubled (355 vs ~180).
2. Committed freight (294 t July-to-date incl. manual residual) is *below* recent manual months — the approval gate filters the ~30% over-generation back down.
3. Top fixes: re-allocation racing the approval queue (~10% of volume dies), shipment fragmentation (approved TOs are single-SKU ~175 kg vs manual ~700 kg), 197 t pending-approval backlog (~70% = one Bhumika drum SKU), missing freight-economics input (reviewers systematically veto bulky organic manure on long hauls).
4. Instrumentation gaps: `to_stock_coverage_days` written as 0 everywhere; `budget_tci` = qty×distance (not ₹); stale view schema; VTO document series with blank reasons bypasses reason-based reporting.

Report was also posted to Slack **#md-files-for-claude** (3-part thread, Jul 8).
