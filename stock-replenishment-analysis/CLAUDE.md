# Stock Replenishment Analysis — agent index

Data-analysis project (deliverables, not code). BigQuery project: `agrostar-data`.

## Before writing any query or reusing any number

Read `TRANSFER_ANALYSIS_METHODOLOGY.md` — it holds the agreed conventions. Non-negotiables:

- **TO classification is by ORIGIN, not doc prefix**: Manufacturing TO = origin FC has
  `location_mst.location_type='Manufacturing'` OR `transfer_no LIKE 'VTO%'` (Panvel books plain UTOs!).
  System = `transfer_creation_reason LIKE 'System TO%'`. Manual = remaining UTOs.
- **Exclude cancelled** headers (`status='CANCELLED'`) and lines (`line_status='CANCELLED'`) everywhere;
  on the sales side also exclude returns (`is_return=1`).
- **`invoiced_transfer_report.weight` is in GRAMS** — divide by 1e6 for tonnes.
- **Cohort discipline**: report % only for fully-elapsed windows; never extrapolate censored cells.
- **B2B** = `InvoiceNo LIKE 'U%'`; clearance offer = `max_expiry_days > 0`.

## Standing caveats (repeat with any number)

- Serial/barcoded stock only — sell-through & ping-pong figures are lower bounds.
- TCI (tonne-km) distance constants cover ~69% of tonnage; Aurangabad↔Pune and factory lanes missing.
- FY26-27 is young; recent transfers are right-censored.

## File map

`README.md` = full index. Quick guide: methodology + caveats → `TRANSFER_ANALYSIS_METHODOLOGY.md`;
final FY26-27 report → `FY2026-27_Stock_Transfer_Report.html`; budget-balancing review → `01`–`04` + `queries.sql`;
B2B clearance/expiry → `b2b_clearance_expiry_analysis.sql`.

## Open commitment

**15 Aug 2026**: budget-balancing 30-day sell-through review; bar ~50% sold (Manual-BM 32%, System-UF 79%).
