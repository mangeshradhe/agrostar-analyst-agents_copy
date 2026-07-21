# Benchmarks & side findings

## A. Full-month transfer-reason landscape (Apr–Jul 2026)

Manual budget movements = "Current BM" + "Coming BM". Hand-off visible: manual BM collapsed exactly as System TO went live (Jul 7).

| Month | Manual BM lines / qty | System TO Budget lines / qty | Other large reasons |
|---|---|---|---|
| Apr | 1,478 / 591,834 | — | System TO - UF case 3,480 / 221k; Debulking 1,913 / 107k |
| May | 1,307 / 777,752 | — | UF case 3,863 / 585k |
| Jun | 1,716 / 1,343,571 | — | UF case 3,654 / 377k; POS-STO 2,764 / 64k |
| Jul (to 8th) | 227 / 82,862 | **1,681 / 420,196** | UF case 1,201 / 162k; UF Fulfil 660 / 255k |

## B. First-10-days benchmark (units)

| Metric | Apr | May | Jun | Jul Manual (residual) | Jul System TO (2 days) |
|---|---:|---:|---:|---:|---:|
| Lines | 233 | 647 | 714 | 227 | 1,681 |
| TOs | 171 | 481 | 583 | 100 | 1,171 |
| Qty | 52,545 | 370,031 | 505,173 | 82,862 | 420,196 |
| SKUs | 86 | 170 | 180 | 143 | 354 |
| Lanes | 116 | 184 | 194 | 65 | 193 |
| Qty/line | 226 | 572 | 708 | 365 | 250 |
| Lines/TO | 1.36 | 1.35 | 1.22 | 2.27 | 1.44 |
| % lines killed | 3.9 | 4.6 | 8.3 | 31.3 | 12.1 |
| % qty killed | 5.7 | 10.9 | 16.9 | 15.1 | 10.6 |
| % lines RECEIVED (by Jul 8) | 96.1 | 95.4 | 91.6 | 15.9 | 0 (too early) |

## C. First-10-days benchmark (tonnes)

| Metric | Apr | May | Jun | Jul Manual | Jul System TO |
|---|---:|---:|---:|---:|---:|
| Tonnes generated | 110.0 | 410.1 | 426.9 | 98.9 | 455.9 |
| Tonnes/TO | 0.64 | 0.85 | 0.73 | 0.99 | 0.39 |
| Kg/line | 472 | 634 | 598 | 436 | 271 |
| % tonnes killed | 18.7 | 23.9 | 11.2 | 10.0 | 19.6 |
| % tonnes received (by Jul 8) | 81.3 | 76.1 | 88.8 | 44.8 | too early |

## D. Approved-only benchmark (first 10 days)

"Approved" for manual = not cancelled/rejected (manual never used the approval workflow); for System = `action_status='APPROVED'`.

| Metric | Apr | May | Jun | Jul Manual | Jul System TO |
|---|---:|---:|---:|---:|---:|
| Approved lines | 224 | 617 | 655 | 156 | 1,170 |
| Approved TOs | 164 | 458 | 544 | 92 | 1,170 (1 line = 1 TO) |
| Approved qty | 49,556 | 329,783 | 419,980 | 70,388 | 339,405 |
| Approved tonnes | 89.4 | 312.2 | 379.0 | 88.9 | 204.7 |
| Tonnes per approved TO | 0.55 | 0.68 | 0.70 | 0.97 | **0.17** |

## E. Generated vs approved tonnage (approval yield)

| Cohort | Generated (t) | Approved (t) | Yield | Pending (t) |
|---|---:|---:|---:|---:|
| Apr Manual | 110.0 | 89.4 | 81.3% | — |
| May Manual | 410.1 | 312.2 | 76.1% | — |
| Jun Manual | 426.9 | 379.0 | 88.8% | — |
| Jul Manual | 98.9 | 88.9 | 90.0% | — |
| Jul System TO | 455.9 | 204.7 | 44.9% raw / **79.1% on decided tonnage** | 197.0 |

Bounds: all pending approved → ~88% (June-like); all pending dies → 45%.
Committed July freight so far: 204.7 + 88.9 = **293.6 t — below June (379 t) and May (312 t)**.

## F. VTO document series (side finding)

`transfer_no LIKE 'VTO%'`, last 30 days (Jun 8 – Jul 8): **78 distinct TOs, 99 lines, ~242k units**, numbered `VTO-2425-###`.
- `transfer_reason` and `created_from` blank on all lines; approval workflow unused
- Statuses: RECEIVED 74 lines / 185,025 units; DISPATCHED 25 lines / 57,050 units
- Likely a separate document type flowing in from another system (FY 24-25 numbering series)
- **Risk: invisible to any reason-filtered reporting** — these are the blank-reason rows in the July reason breakdown (~120k units)

## G. Key field semantics learned (for future queries)

- `action_status`: human decision — APPROVED / REJECTED / blank (pending). Manual-era TOs: always blank.
- `transfer_status`: lifecycle — WAITING_FOR_APPROVAL → PROCESSING/PENDING → PICK CREATED → PICKED → INVOICED → DISPATCHED → RECEIVED; exits: REJECTED / CANCELLED / SYSTEM_CANCELLED. "REJECTED + blank action_status + qty 0" = system archival, not human rejection.
- `edited_transfer_qty`: workflow field — 0 until approval, then = transfer_qty. NOT a human edit record.
- `actual_transfer_qty`: = transfer_qty on approved/rejected; preserves original on archived lines.
- `budget_tci` = transfer_qty × distance (unit-km). NOT rupees.
- `to_stock_coverage_days`: written as 0 on all System TO lines (instrumentation gap).
- `transfer_type`: Inter-State / Inter-City (geography only). All System TOs: B2B→B2B, created_type=Budget, pick_type=LIFO.
- View `catalog_views.catalog_management_transferorders` = `select * from catalog_prod.catalog_management_transferorders`; the view's registered schema is stale (advertises from_* columns that no longer exist).
