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
- **TO request approval/rejection** (`catalog_views.catalog_management_transferorders`, not
  `transfer_header`): Approved = `action_status='APPROVED'`. Rejected = `transfer_status='REJECTED'
  AND action_by != 'SYSTEM'` — SYSTEM rows are auto-archived/superseded recommendations, not real
  human decisions; excluding them flips Budget-balancing from ~30% to ~80-88% approved.

## Standing caveats (repeat with any number)

- Serial/barcoded stock only — sell-through & ping-pong figures are lower bounds.
- TCI (tonne-km) distance constants cover ~69% of tonnage; Aurangabad↔Pune and factory lanes missing.
- FY26-27 is young; recent transfers are right-censored.

## File map

`README.md` = full index. Quick guide: methodology + caveats → `TRANSFER_ANALYSIS_METHODOLOGY.md`;
final FY26-27 report → `FY2026-27_Stock_Transfer_Report.html`; budget-balancing review → `01`–`04` + `queries.sql`;
B2B clearance/expiry → `b2b_clearance_expiry_analysis.sql`; B2B UF% stock-out (in progress) → `06-b2b-uf-stockout-analysis.md` + `b2b_uf_stockout_queries.sql`;
relay/cancellation → `07-ping-pong-relay-and-cancellation-analysis.md` + `07_relay_and_cancellation_queries.sql`;
TO request approval/rejection (in progress) → `08-to-request-approval-rejection-analysis.md` + `08_to_approval_rejection_queries.sql`;
TO lead-time (AP/Telangana/Karnataka) → `09-to-lead-time-ap-tg-ka.md` + `09_to_lead_time_queries.sql`
(no TO-level picklist/gate-entry timestamp exists — "in-transit" blends road time + dock queue; use header RECEIVED `updated_on` to split out receipt→putaway wait).
**In progress (09)**: next = split Jul-Sep receipt→putaway rise by destination FC; rebuild stage table with Received stage.

## Open commitment

**15 Aug 2026**: budget-balancing 30-day sell-through review; bar ~50% sold (Manual-BM 32%, System-UF 79%).

**In progress**: B2B UF% stock-out analysis, scoped to CP/CN. Baseline set (14.57%, Apr'25-Jul'26).
Dashboard: https://claude.ai/code/artifact/e1261662-d97e-4e03-b80f-0b2ce82f4185 (ledger + SKU + facility views).
Open: verify Glyphosate/Paraquat supply constraint (regulatory?) vs distribution fix; explain LKO02/RJ02
reversal (were "improved", now biggest inventory decliners); redo FC×SKU verdict for full molecule list.

**In progress**: TO request approval/rejection analysis (UF/Budget × Intra-FC/Inter-City/Inter-State,
Jan'26-date), now incl. time-to-action (created_on→action_on, Approved-only). Open: explain Aug'26
Budget-balancing turnaround spike (median 61.6h vs 3.3h Jul, 12.8h Sep — check action_by/backlog);
UF Inter-State Jan dip (44.8% approved) unexplained; Sep numbers still censored. Details in
`08-to-request-approval-rejection-analysis.md`.
