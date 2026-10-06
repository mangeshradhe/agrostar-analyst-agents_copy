# B2B Unfulfillable-Order (UF%) Stock-Out Analysis

Hypothesis under test: System TOs started ~Oct 2025 (System TO – Budget Balancing, see `01`).
Since B2B stock availability depends on the right stock reaching the right FC, the probability of
B2B stock-out should have decreased since then. Status: **in progress, working session with Darpan,
27 Aug 2026.**

## Data sources & method

- **B2B order universe** (Darpan's definition, distinct from the `InvoiceNo LIKE 'U%'` convention used
  elsewhere in this project — that one only works post-invoicing, which excludes orders cancelled
  before ever reaching invoicing, exactly the population this question needs):
  ```sql
  UPPER(initiating_source) LIKE 'B2B%'
  AND UPPER(order_type) NOT LIKE 'OFFLINE%'
  AND UPPER(order_type) NOT LIKE 'IPT%'
  AND UPPER(order_type) NOT LIKE 'RETURN%'
  AND UPPER(order_type) NOT LIKE 'COCO%'
  AND UPPER(order_type) NOT LIKE 'TRANSFER%'
  AND UPPER(status) NOT LIKE '%ERROR%' AND UPPER(status) NOT LIKE '%CANCEL%' AND UPPER(status) NOT LIKE '%EDIT%'
  AND UPPER(unicommerce_status) NOT LIKE '%ERROR%' AND UPPER(unicommerce_status) NOT LIKE '%CANCEL%' AND UPPER(unicommerce_status) NOT LIKE '%EDIT%'
  ```
  Source: `prod_db_views.order_management_order`. Note: this excludes `status LIKE '%CANCEL%'` orders
  from the universe — open question (never resolved with Darpan) whether that hides genuine
  stock-out-driven lost sales or just data artifacts (duplicate/edited/error rows).

- **Stock-out proxy** = order ever carried `UNFULFILLABLE_ORDER` in `prod_db_views.orderhold`
  (`crmHoldReasons` or `warehouseHoldReasons`, both comma-separated strings with inconsistent
  formatting — sometimes plain `A,B`, sometimes bracket-wrapped `[A, B]`; match with `REGEXP_CONTAINS`,
  not naive `SPLIT`). `orderhold` join key: `SAFE_CAST(orderId AS INT64) = sales_order_id`. Table is
  multiple rows per order (hold/release events), not 1:1 — dedupe to "ever held" per order.

- **Units**: `order_management_orderitem.quantity`, joined `order_id = sales_order_id`.
  `UF% = SUM(quantity where order ever UF-held) / SUM(quantity)`. Preferred over order-count % because
  order-level attribution double-counts across categories when an order spans multiple categories.

- **Category**: `pristine_wms_prod_db.item_mst.category_code` is the real coarse rollup
  (SEEDS/CP/HW/KIT/PKG/CN/MKT/FB/AH/...) — **not** `product_group`, which looked similar (had literal
  "CP"/"CN" values) but is a different, messier field mixing real categories with molecule/brand names.
  `item_mst.item_code` joins to `orderitem.item_sku`, 99.99% match rate.
  `item_mst.sub_product_group` = molecule/product name (e.g. `GLYPHOSATE 71% SG`) — the right grain
  for SKU-level triage. `item_mst.sub_sub_product_group` = **brand name** (DHANUKA, ROUNDUP,
  CONFIDOR...), a dead end for a "functional type" cut (no Herbicide/Insecticide/Fungicide field
  exists between category_code and molecule name).

- **Periods**: baseline Apr–Oct 2025, trend Nov 2025–Mar 2026, post Apr–Jul 2026. Aug 2026 excluded
  from period rollups (partial month, right-censored) — shown separately where relevant.

- **Blocked table**: `sku_details` / `sku_product_group_mapping` (Drive/Sheets-backed views) — access
  denied ("Permission denied while getting Drive credentials"). Used `item_mst` instead.

## Finding 1 — Aggregate (all B2B): hypothesis rejected

| Metric | Baseline (Apr–Oct '25) | Trend (Nov'25–Mar'26) | Post (Apr–Jul '26) |
|---|---|---|---|
| Order-level UF% | 20.2% | 26.8% | 33.7% |
| Unit-level UF% | 14.6% | 13.9% | 20.5% |
| Cancelled-order UF share | 30.6% | 25.2% | 40.7% |

Monthly order-level % rose from ~7% (Apr'25) to ~36% (Jul–Aug'26); YoY same-month comparison confirms
it's not seasonality (e.g. Apr: 7.2%→26.2%, Jul: 16.0%→36.7%). 3-month moving average shows two
plateaus (Oct–Dec'25, Feb–May'26) each followed by a fresh climb, never a sustained reversal.

**FC dose-response (all B2B, all categories):** every one of 15 major FCs got worse, regardless of
System TO tonnage received (`transfer_header.to_location_code`, `transfer_creation_reason LIKE
'System TO%'`). IDR02 got 746x more TO dose and still worsened 10pp. No FC showed the pattern you'd
expect if TOs were the dominant lever — reads as network-wide demand growth (~9x B2B order volume
over the window) outpacing total supply, not a TO-targeting failure specifically. Can't rule out the
`UNFULFILLABLE_ORDER` trigger logic itself having changed sensitivity over this window — unverified,
flag before reporting externally.

**Category cut (all B2B, `product_group`):** unlike the FC cut, categories diverge — some genuinely
improved (PADDY -20.6pp, WSF05234 -13.0pp, ADJUVANT -5.8pp), others worsened hard (BATTERY PU +24.9pp,
HERBICIDE +13.8pp, SEEDS +6.8pp off the highest baseline of any major category, 30.9%). SEEDS'
persistence at the top is consistent with its known non-barcoded/no-PO-trail tracking gap (see
[[fresh-stock-inflow-doors]] equivalent note — seeds move through PAWOB adjustments, no clean transfer
trail for TOs to act on).

**Mix-shift:** basket composition shifted away from INSECTICID (lower, moderate UF%, share fell
25.2%→18.8%) toward HERBICIDE+HERBICIDES combined (share rose 23.8%→29.5%, UF% also rose hard within
each). Compositional shift and within-category decline are stacking, not one masking the other.

## Finding 2 — CP+CN scope (Darpan can only act on these two categories)

CP+CN combined baseline (all SKUs, Apr'25–Jul'26, single number): **X = 16,384,329 units ordered,
Y = 2,387,997 UF units, UF% = 14.57%.** This is the number to track going forward.

| Category | Baseline | Trend | Post |
|---|---|---|---|
| CP | 12.1% | 12.8% | 19.2% |
| CN | 13.1% | 14.4% | 16.9% |

CP is 3x CN's volume and worsened almost 2x as much — primary focus.

**Molecule-level (CP):** `GLYPHOSATE 71% SG` baseline 12.1% (222K units) → post 54.2% (294K units,
volume still growing +32%). Absolute UF-unit increase for this one molecule = +132,565, against CP's
*total* net increase of +134,665 — **~98% of CP's entire deterioration traces to this one SKU family.**
`PARAQUAT DICHLORIDE 24% SL` / `PARAQUIT` show the same shape smaller scale (baseline ~3–11% →
post 37–73%). Both are herbicides with a history of registration/import scrutiny in India — **not
yet verified whether this is a supply-side (regulatory/vendor) constraint vs. a distribution problem**;
don't recommend a logistics fix until that's checked with procurement/category.

**CN has no single dominant offender** — Bio-fertiliser (24.1%→41.3%) and Adjuvant (10.2%→17.0%)
worsened, offset by genuine improvement in WSF05234, Humic/Fulvic Acid variants, Silicone Spreader,
Gibberellic Acid (each -6 to -13pp). Worth understanding what's different about how the improved CN
SKUs are being replenished vs. Bio-fertiliser/Adjuvant — may be directly transferable.

**CP+CN monthly trend:** CP hit its low (8.6%) in Oct'25, right at System TO launch, then climbed to
its series peak (25.0%) in Jul'26 — same month CP volume also peaked (1.46M units), consistent with
the Glyphosate story concentrating there. CN doesn't show the same Oct dip / Jul spike shape.

**CP+CN FC cut:** 13 of 15 FCs worsened. **Two improved — LKO02 (13.7%→10.7%) and RJ02
(11.6%→8.1%)** — the only counter-examples found in any cut so far. Not yet explained: could be
different Glyphosate/Paraquat handling at those FCs, or just a smaller share of those molecules in
their basket. **Open, next step.** JBL02 is worst on both ends (23.4%→41.2%).

## Corrections and additions (27 Aug 2026, later in the same working session)

- **UF% definition confirmed correct by Darpan**: the broad "ever tagged `UNFULFILLABLE_ORDER`" definition
  used throughout this doc is the one to keep using — not the stricter official one below.
- **Cross-checked against the business's own master query** (`~/Downloads/union Query (1).docx`,
  a `catalogservice`-adjacent BI query joining GRN/transfer/adjustment/sales/ROFO/current-inventory/current-UF).
  Confirmed correct and now the standard for inflow/outflow logic in this analysis:
  - Purchase GRN uses `grn_line_serial` (`qty + bad_qty`), not `grn_line.physical_qty`.
  - Transfer-in includes **all** `return_grn` rows with `document_type IN ('Transfer Order','RGP Transfer Order')`,
    any origin (not manufacturing-only — that scoping is for the separate "new stock into network" question,
    not a full location ledger).
  - Adjustments: `adj_type IN ('PAWOB','PA','TOI')`, `approve_status='APPROVED'`.
  - Full ledger = purchase_grn + transfer_in + adj_grn + sale_return (inflow) − transfer_out − sale_invoice (outflow).
  - The doc's own **"Current_UF" is a stricter, different metric** than what this analysis uses: currently
    `unicommerce_status='ON_HOLD'` (not ever-tagged) AND held qty exceeds current inventory at that facility.
    Not adopted here (see confirmation above) but worth knowing this discrepancy exists if these numbers are
    ever compared against an official dashboard.
  - ROFO's `channel` field is `'Offline'`/`'Online'` — **Offline = B2B**, Online = B2C. Every ROFO comparison
    must filter `channel='Offline'`; omitting it (as an early pass in this session did) inflates the budget
    denominator and produces a false "under budget" read.
- **Opening inventory**: `Console Ageing File (1).xlsx` (Apr 1 2026, GOOD+BAD, MH02+PNQ02 merged) =
  3,502,673 units for CP+CN. Full daily ledger built from this + BQ inflow/outflow: peaked 5,180,972 (Jun 30),
  down to 4,321,087 (Aug 27) — **8 straight weeks of decline**, coinciding with the Jul-Aug UF% spike.
  FC-level decliners for that 8-week window: IDR02 (-224K), LKO02 (-170K), RJ02 (-123K), MH02+PNQ02 (-84K).
  **LKO02 and RJ02 — the two FCs that looked "improved" in the baseline-vs-post comparison — are now the
  2nd and 3rd biggest inventory decliners.** That earlier "improvement" looks like it's reversing; not confirmed why.
- **Revenue check (Darpan's question — is this list the revenue drivers?): mostly no.** Glyphosate 71% SG
  (the #1 unit-damage SKU) is only 1.2% of CP+CN revenue (₹2.1cr of ₹170cr) — high volume, low value.
  Real revenue anchors are Biostimulant (6.0%), Bio-fertiliser (4.3%), Sodium Acifluorfen (4.1%). CP+CN has
  no 80/20 concentration — revenue spreads across 40+ molecules. Built a revenue-weighted damage ranking
  (₹ stuck in UF orders, baseline→post) alongside the unit-damage one; Bio-fertiliser tops it (+₹2.6cr).
- **Three-way verdict framework built**: per SKU (and per SKU×FC), classify July vs. plan into Sales overshot
  plan / Supply not as per plan / On plan / Sales under plan, using demand-vs-ROFO-budget and inflow-vs-demand
  ratios. Confirmed cases: Metribuzin & Paraquit & Fomesafen+Fluazifop = real supply shortfalls (procurement,
  not planning); Chlorantraniliprole 9.3%+Lambda, Glufosinate, Paraquat Dichloride, Bio-fertiliser, Pretilachlor
  = sales overshot an under-set ROFO. **Glyphosate itself is "neither" in July** — that month's flows look
  healthy; its crisis is a leftover deficit from earlier months, not a July problem.
- **Dashboard artifact** (supersedes two earlier separate artifacts):
  https://claude.ai/code/artifact/e1261662-d97e-4e03-b80f-0b2ce82f4185 — 3 tabs: Inventory Ledger (charts),
  SKU Drilldown (51 SKUs × month, network-wide, Apr-Aug), Facility & State View (same 51 SKUs × FC, July only —
  SKU×FC×month together exceeds the BQ tool's 3,000-row cap, hit this once, had to drop back to single-month
  for the facility grain).

## Open items / next steps (as of 27 Aug 2026)

**Still genuinely open:**
1. **Not yet verified with procurement/category**: whether Glyphosate/Paraquat face a registration or
   import constraint (both molecules have a history of regulatory scrutiny in India) — decides whether
   a distribution fix is even possible for them, or whether it's a pure supply-side wall.
2. **LKO02/RJ02 reversal unexplained** — they were the two improving FCs on the baseline-vs-post view,
   now the 2nd/3rd biggest inventory decliners in the last 8 weeks. Needs its own root-cause pass.
3. **FC×SKU verdict classification not yet done for the full molecule list** — only Glyphosate/Paraquat
   got the FC-level dose-response check early on. The dashboard's Facility & State View tab has the raw
   data to redo the 3-way verdict at FC grain for all 15 molecules; not yet synthesized into a finding.
4. **Coverage check missing**: what % of CP+CN's total UF-unit damage do these 15 molecules actually
   represent? Never computed — don't know if this list is "most of the problem" or a fraction of it.
5. Unresolved: does excluding `status LIKE '%CANCEL%'` from the order universe hide genuine
   stock-out-driven lost sales, or just data artifacts? Never confirmed with Darpan.
6. `HERBICIDE` / `HERBICIDES` near-duplicate labels in `product_group` should be normalized before
   any of this goes in front of anyone outside this working session.

**Resolved this session** (kept for the record, don't re-open without new evidence):
- CN's improved-SKU comparison (WSF05234, Humic/Fulvic vs. Bio-fertiliser/Adjuvant) — superseded by the
  molecule-level verdict framework above, which explains BIOFERTILISER's problem directly (sales overshot
  plan, not a replenishment-handling gap).
- Monthly CP+CN scorecard — now live in the dashboard artifact (SKU Drilldown tab, network-wide, monthly).

Global rule set this session (27 Aug 2026, `~/.claude/CLAUDE.md`): keep responses short, simple
English, across all projects — applies to how this analysis gets written up and reported too.
