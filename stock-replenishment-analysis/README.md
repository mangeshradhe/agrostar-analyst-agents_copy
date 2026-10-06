# Stock Replenishment Analysis

Transfer-order and stock-movement analyses on BigQuery project `agrostar-data`. Three bodies of work so far:

1. **Budget-balancing feature review** (snapshot Jul 8, 2026) — the System TO – Budget Balancing feature (live Jul 7), which auto-creates stock-movement TOs between FCs. Files `01`–`04` + `queries.sql`.
2. **FY 2026-27 full transfer analysis** (Jul 13, 2026) — all transfer orders FY-to-date: classification, geography, sell-through, ping-pong waste, TCI. Report + methodology doc.
3. **FC workload & manpower analysis** (Aug 26, 2026) — Inbound/Outbound workload growth and picker/OQC-biller efficiency for GJ/MH/RJ/MP/UP, Apr-Jul FY25-26 vs FY26-27, plus a facility-consolidation reality check (MH→PNQ real, JDH→RJ not clean, NGP→AKD pre-dates the window).
4. **B2B UF% stock-out analysis** (in progress, Aug 27, 2026) — testing whether System TOs reduced B2B stock-outs. Verdict so far: no, UF% rose network-wide; within Darpan's CP/CN scope, ~98% of CP's deterioration traces to one SKU (Glyphosate 71% SG). Baseline set (14.57%), still working through FC/molecule cuts.
5. **Ping-pong / quick-relay & order-cancellation analysis** (in progress, Sep 6, 2026) — how often received stock gets shipped out again before it sells, and why. Three live artifacts (TO Relay Ledger, Quick Stock-Lift Pairs, Cancellation-Driven Relay Funnel); confirmed one root cause: 47,855 units (12.3% of genuine cross-city relay volume) trace to a demand order getting cancelled after urgent stock was already rushed in.
6. **TO request approval/rejection analysis** (in progress, Sep 21, 2026) — of the transfer requests raised via the CRM recommendation funnel (UF case, Budget balancing), how many get approved vs rejected, by geography tier (Intra-FC/Inter-City/Inter-State), Jan 2026 to date. Key catch: ~16.9k "rejections" were actually the system auto-archiving stale recommendations, not human decisions — excluding those flips Budget-balancing from looking like it fails (26-42% approved) to actually strong (80-88% approved). Part 2 adds time-to-action (created→actioned, Approved-only): UF turns around fast (~90% within 24h every month); Budget-balancing is slow and unstable (median 3.3h→61.6h→12.8h Jul-Aug-Sep) — Aug spike unexplained, next thread to pull.
7. **TO lead-time analysis, AP/Telangana/Karnataka destinations** (Sep 28, 2026) — these 3 states complained inbound stock arrives late; broke the journey into approval → warehouse → dispatch → in-transit → putaway. Verdict: in-transit (dispatch→destination) is 4-6x every other stage combined (5-7 day median), and it got worse in lockstep with the Jul 2026 Budget-balancing volume ramp (5-6x more requests, same capacity). Rajasthan/Bihar/UP are the slowest origin lanes into all 3 states (7-12 days) regardless of raw distance — reads as a shipment-frequency issue on low-volume lanes, not pure distance. Session 2 (30 Sep) added pooled/source-state/FY26-27 tables, a single-TO trace, and received→putaway-created / putaway-created→completed for ALL destination FCs (HYD01, PNQ02, RPR01, JBL02 slowest to start putaway). Details in `09-to-lead-time-ap-tg-ka.md`.

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
| `05-fc-workload-manpower-analysis.md` | FC workload (Inbound/Outbound units) & picker/consolidation/OQC-biller efficiency by state, Apr-Jul YoY; the `pick_header` vs `pick_header_arc_main` data-quality catch; facility-consolidation reality check; multi-angle manpower judgment; follow-on section covers the **Fulfilment Pulse** dashboard (artifact + `~/Downloads/fulfilment-pulse.html`) — picker utilization, person-level concentration, and the manpower diagnosis 2×2 (result: 0/19 facilities show a genuine "Bottleneck" hiring case) |
| `fc_workload_manpower_queries.sql` | Reusable queries behind the FC workload/manpower analysis |
| `06-b2b-uf-stockout-analysis.md` | **In progress.** B2B UF% (unfulfillable-order) stock-out analysis: method, aggregate findings (hypothesis rejected), CP/CN-scoped findings (Glyphosate 71% SG ≈98% of CP's deterioration), open items |
| `b2b_uf_stockout_queries.sql` | Reusable queries behind the UF% stock-out analysis |
| `07-ping-pong-relay-and-cancellation-analysis.md` | Relay/cancellation deep-dive: quick-lift pairs, relay chains, cancellation-driven relay root cause |
| `07_relay_and_cancellation_queries.sql` | Reusable queries behind the relay/cancellation analysis |
| `08-to-request-approval-rejection-analysis.md` | **In progress.** TO request approval/rejection funnel: source table & definitions (incl. the SYSTEM-auto-reject exclusion), Month×Type×Tier tables, open items |
| `08_to_approval_rejection_queries.sql` | Reusable queries behind the approval/rejection analysis |
| `09-to-lead-time-ap-tg-ka.md` | TO lead-time breakdown (approval/warehouse/dispatch/in-transit/putaway) for AP/Telangana/Karnataka destinations; lane view by origin state; volume-ramp-vs-lead-time trend; data-quality gaps (no TO-level picklist or gate-entry timestamp exist) |
| `09_to_lead_time_queries.sql` | Reusable queries behind the lead-time analysis |

## Open commitment

**15 Aug 2026 — budget-balancing review**: measure 30-day sell-through of the first dispatch cohort (dispatches began ~30 Jun). Pre-agreed bar ~50% sold (benchmarks: Manual-BM 32%, System-UF 79%). Details in the methodology doc.

## Headline conclusions — budget balancing (as of Jul 8)

1. Core logic is sane: direction, product mix, lane footprint, approval yield (79% decided) and waste rate (10.6% qty) all match or beat the Apr–Jun manual benchmark; SKU coverage doubled (355 vs ~180).
2. Committed freight (294 t July-to-date incl. manual residual) is *below* recent manual months — the approval gate filters the ~30% over-generation back down.
3. Top fixes: re-allocation racing the approval queue (~10% of volume dies), shipment fragmentation (approved TOs are single-SKU ~175 kg vs manual ~700 kg), 197 t pending-approval backlog (~70% = one Bhumika drum SKU), missing freight-economics input (reviewers systematically veto bulky organic manure on long hauls).
4. Instrumentation gaps: `to_stock_coverage_days` written as 0 everywhere; `budget_tci` = qty×distance (not ₹); stale view schema; VTO document series with blank reasons bypasses reason-based reporting.

Report was also posted to Slack **#md-files-for-claude** (3-part thread, Jul 8).
