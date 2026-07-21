# FY 2026-27 Transfer Order Analysis — Methodology, Logic & Caveats

Prepared 13 Jul 2026. Companion to `FY2026-27_Stock_Transfer_Report.html` (same folder), the Claude artifact
(https://claude.ai/code/artifact/40332cf2-fdfb-400e-99c1-3f1203f7f3b5), and the Slack canvas in #md-files-for-claude
(https://agrostar.slack.com/docs/T0HQKNPGC/F0BGZSY8AC9).

## Source tables (project `agrostar-data`)
| Table | Role | Partition/filter |
|---|---|---|
| `pristine_wms_prod_db.transfer_header` | TO master: type, reason, from/to, status | `created_on > '2026-03-31'` |
| `pristine_wms_prod_db.invoiced_transfer_report` | Dispatched (invoiced) transfer lines, serial-level | `CreatedOn > '2026-03-31'` |
| `pristine_wms_prod_db.invoiced_report` | Sales invoices, serial-level → "consumption" | `CreatedOn > '2026-03-31'` |
| `pristine_wms_prod_db.location_mst` | city / state / location_type per FC | — |
| `pristine_wms_prod_db.item_mst` | SKU name, product_group (join on `item_code = ItemSKU`) | — |
| `catalog_views.catalog_management_transferorders` | CRM recommendation funnel (rejections, DRR/coverage snapshots) | `created_on > '2026-03-31'` |
| FC Distance CSVs (2 files, Downloads) | distance constants; base file + 22-Jun file adding MH01/MH02 | — |

Join key: `invoiced_transfer_report.DisplayOrderCode = transfer_header.transfer_no` (100% match rate FY26-27).

## Classification logic
- **Manufacturing TO**: origin FC has `location_mst.location_type='Manufacturing'` OR `transfer_no LIKE 'VTO%'`.
  Classify by ORIGIN, not doc prefix — Panvel (PNVL01, biggest factory ~544 t) and Sanand/Anand/Panchmahal book as ordinary UTOs.
  VTO-numbered docs come only from GJ06 (Karjan) and RJ06 (Bagru, launched Jul 2026).
- **System**: `transfer_creation_reason LIKE 'System TO%'` (UF case / Budget balancing / Adhoc), created from CRM.
- **Manual**: all other UTOs (Current BM, Coming BM, UF Fulfil, DRR Fulfil, Debulking, POS-STO, ADHOC, NE, Sale mvmnt, Repairing).
- **PRO** = purchase returns (`transfer_no LIKE 'PRO%'` / doc_type 'Purchase Return').

## Geography tiers (per company's own distance constants file)
- **Intra-FC** = both FCs in the same city (e.g., PNQ01↔PNQ02, MH01↔MH02) → distance constant 0, ~zero freight cost.
- **Inter-City** = different city, same state. **Inter-State** = different states.
- Implemented via `UPPER(city)` / `UPPER(state_name)` from `location_mst` (case varies, e.g., BALLARI).

## Exclusions applied everywhere
- Header `status='CANCELLED'` and line `line_status='CANCELLED'` excluded from all movement/funnel views; cancellations reported separately.
- Consumption (sales) side: exclude `IFNULL(is_return,0)=1` AND `line_status='CANCELLED'` (cancelled sales lines = 0.86% of lines; earlier published numbers move <1pt).

## Key metric definitions
- **Weight**: `invoiced_transfer_report.weight` is in GRAMS (per-unit quartiles 100g–50kg). Divide by 1e6 for tonnes.
- **TCI** (CFO metric) = travel distance × weight = tonne-km, using city-pair constants. Both CSVs combined cover ~69% of paid
  tonnage. STILL MISSING: Aurangabad↔Pune (biggest lane, 372 t) and ALL manufacturing sites. Also suspicious: the 22-Jun file's
  MH01/MH02 distances are identical to Pune's row for every destination (likely copy-paste).
- **Consumption / sell-through**: serial+SKU-level. First transfer per serial (min line CreatedOn) → first clean sale with
  `sale_ts >= transfer_ts`, matched on `serial_no AND ItemSKU`. Only barcoded serials counted (non-serialized stock is a blind spot;
  figures are lower bounds).
- **Cohort discipline**: report % only for fully-elapsed windows (e.g., 60-day numbers only for transfers ≤ 14 May; 12-week cohort
  table blanks unobserved weeks). Never extrapolate censored cells.
- **Revenue attribution**: pre-GST `TotalPrice` of the FIRST sale of each transferred serial. FY26-27: ₹80.2 Cr = 31% of all
  barcoded sales revenue (₹256.7 Cr serialized total).
- **Ping-pong (serial journeys)**: chain consecutive legs where leg2 origin = leg1 destination. Patterns: Bounce (A→B→A),
  Return-to-origin-city (A→B→A′ same city), Detour via other state, Hub-last-mile (leg2 intra-city; by design, not waste),
  Onward relay (A→B→C). Avoidable waste: bounce & return = both paid legs; detour/relay extra = km1+km2−km_direct.
  Timing buckets: same calendar month / ≤30 days crossing month / >30 days.

## Headline findings (FY26-27 through 13 Jul)
- 19,263 TOs, 5,301 t, 51 lakh units, ₹80.2 Cr revenue enabled. YoY (same period): orders +88%, weight +36%,
  Inter-State share 37%→46%, Intra-FC share 38%→20%.
- Same-window sell-through: System 68%/14d, 79%/30d; Manual-BM 16%/32%. Every system month-cohort ≥73% by week 4.
- Wasted TCI ≥1.88 lakh t-km (bounce 71.3k, city-return 15.3k, detour 5k, relay-extra 96.6k). 73% of bounces start with a
  manual push; UF engine also reverses ITSELF (10.5k serials, ~17-day gap) — cool-off rule is the fix. June = blowout month (75 t returned).
- 75% of Inter-State TOs weigh <200 kg → weekly lane consolidation is the top action.
- Factory stock status: Sanand 50% moved onward (good); Karjan 93% still parked (~54-day dwell); Panvel 63% parked.
- Budget balancing: all dispatches <14 days old as of 13 Jul — cannot be judged. **Pre-agreed review 15 Aug 2026**: first cohort
  completes 30 days; bar ~50% sold (context: Manual-BM 32%, System-UF 79%). Scale back if it misses; retire the worry if it clears.

## Data-quality issues found (fix at source)
1. Panvel + smaller Gujarat factories book dispatches as UTOs (should be VTO / Manufacturing TO).
2. Distance constants missing Aurangabad↔Pune and all factory lanes; MH rows appear copied from Pune.
3. Free-text facility codes in transfer_header: "idr02", "JBl02", "RJ01 " (trailing space), "Lko02", "Agrostarwarehouse".
4. Suspected serial re-barcoding at hubs (is_barcode_replacement) may break serial chains — VTO utilization metrics affected; unquantified.

## Caveats to repeat with any number
- FY is 3.5 months old; recent transfers right-censored. July = 13 days only.
- Serial-tracked (barcoded) stock only; ping-pong & utilization are lower bounds.
- TCI covers ~69% of paid tonnage (missing constants above) — wasted-km figures understate reality.
- Factory/VTO stock excluded from sell-through comparisons (sells only after onward hop).
- Slack bot token lacks `files:write` — cannot attach files to Slack via API until scope added.
