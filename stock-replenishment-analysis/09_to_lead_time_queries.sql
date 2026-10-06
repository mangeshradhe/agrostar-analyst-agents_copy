-- TO lead-time analysis: Andhra Pradesh / Telangana / Karnataka destinations
-- See 09-to-lead-time-ap-tg-ka.md for method, caveats, findings.
-- Destinations: GNT01 (Guntur, AP), HYD01 (Hyderabad, Telangana), BAY01 (Ballari, Karnataka) —
-- the only 3 FCs in these states (checked against location_mst).

-- ============================================================================
-- Shared CTEs (repeated in each query below; pull into a view if this becomes
-- a recurring ask)
--   base : approved System TO requests to the 3 destinations, Jan'26-date
--   th   : WMS transfer_header, non-cancelled only (created_on ≈ approval action_on, confirmed)
--   inv  : invoice_transfer_header, invoice-created + dispatched timestamps (MIN/MAX per
--          transfer_no in case of multi-invoice splits)
--   put  : putaway_header, document_type='Transfer Order' only — destination receipt proxy
--          (no separate TO gate-entry/arrival timestamp exists; grn_header/gate_entry are
--          100% Purchase-Order-only, checked this session)
-- ============================================================================

-- Query 1: stage breakdown (median/P90 hours) by destination FC x TO type
WITH base AS (
  SELECT c.transfer_no, c.transfer_reason, c.from_facility, c.to_facility,
         c.created_on AS req_created, c.action_on AS approved_on
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status = 'APPROVED'
    AND c.transfer_reason LIKE 'System TO%'
    AND c.to_facility IN ('GNT01','BAY01','HYD01')
    AND c.created_on > '2026-01-01'
),
th AS (
  SELECT transfer_no FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
  WHERE status != 'CANCELLED'
),
inv AS (
  SELECT DisplayOrderCode AS transfer_no, MIN(CreatedOn) inv_created, MAX(DispatchedOn) dispatched_on
  FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header`
  GROUP BY DisplayOrderCode
),
put AS (
  SELECT document_no AS transfer_no, MIN(created_on) putaway_start, MAX(completed_on) putaway_complete
  FROM `agrostar-data.pristine_wms_prod_db.putaway_header`
  WHERE document_type = 'Transfer Order'
  GROUP BY document_no
),
joined AS (
  SELECT b.*, inv.inv_created, inv.dispatched_on, put.putaway_start, put.putaway_complete
  FROM base b
  JOIN th ON th.transfer_no = b.transfer_no
  LEFT JOIN inv ON inv.transfer_no = b.transfer_no
  LEFT JOIN put ON put.transfer_no = b.transfer_no
),
stg AS (
  SELECT *,
    TIMESTAMP_DIFF(approved_on, req_created, MINUTE)/60.0 AS approval_hrs,
    TIMESTAMP_DIFF(inv_created, approved_on, MINUTE)/60.0 AS warehouse_hrs,
    TIMESTAMP_DIFF(dispatched_on, inv_created, MINUTE)/60.0 AS dispatch_hold_hrs,
    TIMESTAMP_DIFF(putaway_start, dispatched_on, MINUTE)/60.0 AS transit_hrs,
    TIMESTAMP_DIFF(putaway_complete, putaway_start, MINUTE)/60.0 AS putaway_hrs,
    TIMESTAMP_DIFF(putaway_complete, req_created, MINUTE)/60.0 AS total_hrs
  FROM joined
)
SELECT
  to_facility,
  CASE WHEN transfer_reason LIKE '%UF%' THEN 'UF'
       WHEN transfer_reason LIKE '%Budget%' THEN 'Budget'
       ELSE 'Adhoc' END AS to_type,
  COUNT(*) N,
  COUNTIF(putaway_complete IS NOT NULL) N_complete,
  ROUND(APPROX_QUANTILES(approval_hrs,100)[OFFSET(50)],1) approval_med,
  ROUND(APPROX_QUANTILES(approval_hrs,100)[OFFSET(90)],1) approval_p90,
  ROUND(APPROX_QUANTILES(warehouse_hrs,100)[OFFSET(50)],1) warehouse_med,
  ROUND(APPROX_QUANTILES(warehouse_hrs,100)[OFFSET(90)],1) warehouse_p90,
  ROUND(APPROX_QUANTILES(dispatch_hold_hrs,100)[OFFSET(50)],1) dispatch_hold_med,
  ROUND(APPROX_QUANTILES(dispatch_hold_hrs,100)[OFFSET(90)],1) dispatch_hold_p90,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(50)],1) transit_med,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(90)],1) transit_p90,
  ROUND(APPROX_QUANTILES(putaway_hrs,100)[OFFSET(50)],1) putaway_med,
  ROUND(APPROX_QUANTILES(total_hrs,100)[OFFSET(50)],1) total_med,
  ROUND(APPROX_QUANTILES(total_hrs,100)[OFFSET(90)],1) total_p90
FROM stg
GROUP BY to_facility, to_type
ORDER BY to_facility, to_type;

-- Query 2: in-transit + total lead time by origin state x destination FC (lane view)
WITH base AS (
  SELECT c.transfer_no, c.transfer_reason, c.from_facility, c.to_facility,
         c.created_on AS req_created, c.action_on AS approved_on
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status = 'APPROVED'
    AND c.transfer_reason LIKE 'System TO%'
    AND c.to_facility IN ('GNT01','BAY01','HYD01')
    AND c.created_on > '2026-01-01'
),
th AS (
  SELECT transfer_no FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
  WHERE status != 'CANCELLED'
),
inv AS (
  SELECT DisplayOrderCode AS transfer_no, MIN(CreatedOn) inv_created, MAX(DispatchedOn) dispatched_on
  FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header`
  GROUP BY DisplayOrderCode
),
put AS (
  SELECT document_no AS transfer_no, MIN(created_on) putaway_start
  FROM `agrostar-data.pristine_wms_prod_db.putaway_header`
  WHERE document_type = 'Transfer Order'
  GROUP BY document_no
),
joined AS (
  SELECT b.*, inv.dispatched_on, put.putaway_start, lo.state_name AS from_state
  FROM base b
  JOIN th ON th.transfer_no = b.transfer_no
  LEFT JOIN inv ON inv.transfer_no = b.transfer_no
  LEFT JOIN put ON put.transfer_no = b.transfer_no
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` lo
    ON UPPER(TRIM(lo.location_id)) = UPPER(TRIM(b.from_facility))
),
stg AS (
  SELECT *,
    TIMESTAMP_DIFF(putaway_start, dispatched_on, MINUTE)/60.0 AS transit_hrs,
    TIMESTAMP_DIFF(putaway_start, req_created, MINUTE)/60.0 AS total_hrs
  FROM joined
  WHERE dispatched_on IS NOT NULL AND putaway_start IS NOT NULL
)
SELECT
  to_facility,
  UPPER(from_state) AS from_state,
  COUNT(*) N,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(50)]/24,1) transit_med_days,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(90)]/24,1) transit_p90_days,
  ROUND(APPROX_QUANTILES(total_hrs,100)[OFFSET(50)]/24,1) total_med_days
FROM stg
GROUP BY to_facility, from_state
HAVING N >= 10
ORDER BY to_facility, transit_med_days DESC;

-- Query 3: monthly trend — volume ramp vs. total lead time (cohort discipline: current
-- partial month will be censored, read N_complete / N to gauge how much)
WITH base AS (
  SELECT c.transfer_no, c.to_facility, c.created_on AS req_created
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status = 'APPROVED'
    AND c.transfer_reason LIKE 'System TO%'
    AND c.to_facility IN ('GNT01','BAY01','HYD01')
    AND c.created_on > '2026-01-01'
),
th AS (
  SELECT transfer_no FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
  WHERE status != 'CANCELLED'
),
inv AS (
  SELECT DisplayOrderCode AS transfer_no, MAX(DispatchedOn) dispatched_on
  FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header`
  GROUP BY DisplayOrderCode
),
put AS (
  SELECT document_no AS transfer_no, MIN(created_on) putaway_start
  FROM `agrostar-data.pristine_wms_prod_db.putaway_header`
  WHERE document_type = 'Transfer Order'
  GROUP BY document_no
),
joined AS (
  SELECT b.*, inv.dispatched_on, put.putaway_start
  FROM base b JOIN th ON th.transfer_no = b.transfer_no
  LEFT JOIN inv ON inv.transfer_no = b.transfer_no
  LEFT JOIN put ON put.transfer_no = b.transfer_no
)
SELECT
  FORMAT_TIMESTAMP('%Y-%m', req_created) mo,
  to_facility,
  COUNT(*) n,
  COUNTIF(putaway_start IS NOT NULL) n_complete,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(putaway_start,dispatched_on,MINUTE)/60.0,100)[OFFSET(50)]/24,1) transit_med_days,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(putaway_start,req_created,MINUTE)/60.0,100)[OFFSET(50)]/24,1) total_med_days
FROM joined
GROUP BY mo, to_facility
ORDER BY mo, to_facility;

-- Query 4: facility-level lane view (all 5 stages) for one destination FC.
-- Swap the to_facility filter for GNT01 / HYD01 / BAY01 as needed.
WITH base AS (
  SELECT c.transfer_no, c.transfer_reason, c.from_facility, c.to_facility,
         c.created_on AS req_created, c.action_on AS approved_on
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status = 'APPROVED'
    AND c.transfer_reason LIKE 'System TO%'
    AND c.to_facility = 'GNT01'  -- swap for HYD01 / BAY01
    AND c.created_on > '2026-01-01'
),
th AS (
  SELECT transfer_no FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
  WHERE status != 'CANCELLED'
),
inv AS (
  SELECT DisplayOrderCode AS transfer_no, MIN(CreatedOn) inv_created, MAX(DispatchedOn) dispatched_on
  FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header`
  GROUP BY DisplayOrderCode
),
put AS (
  SELECT document_no AS transfer_no, MIN(created_on) putaway_start, MAX(completed_on) putaway_complete
  FROM `agrostar-data.pristine_wms_prod_db.putaway_header`
  WHERE document_type = 'Transfer Order'
  GROUP BY document_no
),
joined AS (
  SELECT b.*, inv.inv_created, inv.dispatched_on, put.putaway_start, put.putaway_complete
  FROM base b
  JOIN th ON th.transfer_no = b.transfer_no
  LEFT JOIN inv ON inv.transfer_no = b.transfer_no
  LEFT JOIN put ON put.transfer_no = b.transfer_no
),
stg AS (
  SELECT *,
    TIMESTAMP_DIFF(approved_on, req_created, MINUTE)/60.0 AS approval_hrs,
    TIMESTAMP_DIFF(inv_created, approved_on, MINUTE)/60.0 AS warehouse_hrs,
    TIMESTAMP_DIFF(dispatched_on, inv_created, MINUTE)/60.0 AS dispatch_hold_hrs,
    TIMESTAMP_DIFF(putaway_start, dispatched_on, MINUTE)/60.0 AS transit_hrs,
    TIMESTAMP_DIFF(putaway_complete, req_created, MINUTE)/60.0 AS total_hrs
  FROM joined
)
SELECT
  from_facility,
  COUNT(*) N,
  COUNTIF(putaway_start IS NOT NULL) N_complete,
  ROUND(APPROX_QUANTILES(approval_hrs,100)[OFFSET(50)],1) approval_med,
  ROUND(APPROX_QUANTILES(approval_hrs,100)[OFFSET(90)],1) approval_p90,
  ROUND(APPROX_QUANTILES(warehouse_hrs,100)[OFFSET(50)],1) warehouse_med,
  ROUND(APPROX_QUANTILES(warehouse_hrs,100)[OFFSET(90)],1) warehouse_p90,
  ROUND(APPROX_QUANTILES(dispatch_hold_hrs,100)[OFFSET(50)],1) dispatch_hold_med,
  ROUND(APPROX_QUANTILES(dispatch_hold_hrs,100)[OFFSET(90)],1) dispatch_hold_p90,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(50)]/24,1) transit_med_days,
  ROUND(APPROX_QUANTILES(transit_hrs,100)[OFFSET(90)]/24,1) transit_p90_days,
  ROUND(APPROX_QUANTILES(total_hrs,100)[OFFSET(50)]/24,1) total_med_days,
  ROUND(APPROX_QUANTILES(total_hrs,100)[OFFSET(90)]/24,1) total_p90_days
FROM stg
GROUP BY from_facility
ORDER BY N DESC;

-- Query 5: September-only warehouse-stage cumulative-day distribution, for a fixed set of
-- origin facilities into all 3 south destinations. Cohort discipline: denominator is orders
-- that have reached invoicing so far, not all orders raised this month (un-invoiced ones are
-- still in flight and would bias the % up if included as "fast").
WITH base AS (
  SELECT c.transfer_no, c.from_facility, c.to_facility, c.created_on AS req_created, c.action_on AS approved_on
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status = 'APPROVED'
    AND c.transfer_reason LIKE 'System TO%'
    AND c.to_facility IN ('GNT01','BAY01','HYD01')
    AND c.from_facility IN ('PNQ02','AKD02','AHM02','RJ02')  -- swap as needed
    AND c.created_on >= '2026-09-01'  -- swap for whichever month
),
th AS (
  SELECT transfer_no FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
  WHERE status != 'CANCELLED'
),
inv AS (
  SELECT DisplayOrderCode AS transfer_no, MIN(CreatedOn) inv_created
  FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header`
  GROUP BY DisplayOrderCode
),
joined AS (
  SELECT b.*, inv.inv_created,
    TIMESTAMP_DIFF(inv.inv_created, b.approved_on, MINUTE)/60.0/24.0 AS warehouse_days
  FROM base b
  JOIN th ON th.transfer_no = b.transfer_no
  LEFT JOIN inv ON inv.transfer_no = b.transfer_no
)
SELECT
  from_facility,
  COUNT(*) N_total,
  COUNTIF(inv_created IS NOT NULL) N_invoiced,
  COUNTIF(warehouse_days <= 1) le_1,
  COUNTIF(warehouse_days <= 2) le_2,
  COUNTIF(warehouse_days <= 3) le_3,
  COUNTIF(warehouse_days <= 4) le_4,
  COUNTIF(warehouse_days <= 5) le_5,
  COUNTIF(warehouse_days <= 6) le_6,
  COUNTIF(warehouse_days <= 7) le_7,
  COUNTIF(warehouse_days <= 8) le_8,
  COUNTIF(warehouse_days <= 9) le_9,
  COUNTIF(warehouse_days <= 10) le_10,
  COUNTIF(warehouse_days > 10) gt_10
FROM joined
GROUP BY from_facility
ORDER BY N_total DESC;


-- ============================================================================
-- SESSION 2 (30 Sep 2026) QUERIES — all use the same base/th/inv/put CTE pattern as above.
-- Note: transfer_header column names are doc_type/from_location_code/to_location_code/status/created_on/updated_on
-- (NOT from_facility/to_facility). Timestamps in WMS tables are IST wall-clock stored as UTC.
-- ============================================================================

-- Query 6: pooled + per-source stage percentiles (P25/P50/P75/P90 for each transition).
--   Pooled: drop the group-by, use scope 'all'. By source FC: GROUP BY from_facility (base must select c.from_facility).
--   By source STATE: LEFT JOIN a loc CTE:
--     loc AS (SELECT UPPER(TRIM(location_id)) lid, ANY_VALUE(UPPER(TRIM(state_name))) st
--             FROM `agrostar-data.pristine_wms_prod_db.location_mst` GROUP BY 1)
--     ... LEFT JOIN loc ON loc.lid = UPPER(TRIM(b.from_facility))  -> GROUP BY st
--   FY26-27 only: change base filter to c.created_on >= '2026-04-01'.
--   Stage expressions (hours): approval = approved_on - req_created; warehouse = inv_created - approved_on;
--   dispatch hold = dispatched_on - inv_created; transit = putaway_start - dispatched_on; total = putaway_complete - req_created.
--   Use th AS (SELECT DISTINCT transfer_no ...) to avoid fan-out.

-- Query 7: single-TO trace (swap the TO number). Run all four and read timestamps side by side.
SELECT * FROM `agrostar-data.catalog_views.catalog_management_transferorders` WHERE transfer_no = 'UTO-2425-78771';
SELECT * FROM `agrostar-data.pristine_wms_prod_db.transfer_header` WHERE transfer_no = 'UTO-2425-78771';
SELECT * FROM `agrostar-data.pristine_wms_prod_db.invoice_transfer_header` WHERE DisplayOrderCode = 'UTO-2425-78771';
SELECT * FROM `agrostar-data.pristine_wms_prod_db.putaway_header` WHERE document_no = 'UTO-2425-78771';

-- Query 8: received -> putaway created, putaway created -> completed, by DESTINATION state and FC (all FCs, System TOs, FY26-27).
-- "Received" = transfer_header.updated_on where status='RECEIVED' (last-update time, not confirmed truck arrival).
WITH base AS (
  SELECT c.transfer_no, UPPER(TRIM(c.to_facility)) to_fc
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` c
  WHERE c.action_status='APPROVED' AND c.transfer_reason LIKE 'System TO%' AND c.created_on >= '2026-04-01'),
th AS (SELECT transfer_no, ANY_VALUE(status) status, MAX(updated_on) received_on
  FROM `agrostar-data.pristine_wms_prod_db.transfer_header` WHERE status != 'CANCELLED' GROUP BY transfer_no),
put AS (SELECT document_no AS transfer_no, MIN(created_on) put_created, MAX(completed_on) put_done
  FROM `agrostar-data.pristine_wms_prod_db.putaway_header` WHERE document_type='Transfer Order' GROUP BY document_no),
loc AS (SELECT UPPER(TRIM(location_id)) lid, ANY_VALUE(UPPER(TRIM(state_name))) st
  FROM `agrostar-data.pristine_wms_prod_db.location_mst` GROUP BY 1),
m AS (
  SELECT COALESCE(loc.st,'UNMAPPED') st, b.to_fc,
    TIMESTAMP_DIFF(put.put_created, th.received_on, MINUTE)/60.0 r2p,
    TIMESTAMP_DIFF(put.put_done, put.put_created, MINUTE)/60.0 p2c
  FROM base b JOIN th USING(transfer_no) JOIN put USING(transfer_no)
  LEFT JOIN loc ON loc.lid=b.to_fc
  WHERE th.status='RECEIVED'),
u AS (SELECT 'STATE' lvl, st scope, r2p, p2c FROM m UNION ALL SELECT 'FC', to_fc, r2p, p2c FROM m UNION ALL SELECT 'ALL','ALL', r2p, p2c FROM m),
g AS (SELECT lvl, scope, COUNT(*) n, COUNTIF(r2p<0) neg, COUNTIF(r2p>24) gt1d,
  APPROX_QUANTILES(r2p,100) a, APPROX_QUANTILES(p2c,100) b FROM u GROUP BY lvl, scope)
SELECT lvl, scope, n, neg, gt1d,
  ROUND(a[OFFSET(25)],1) r25, ROUND(a[OFFSET(50)],1) r50, ROUND(a[OFFSET(75)],1) r75, ROUND(a[OFFSET(90)],1) r90,
  ROUND(b[OFFSET(25)]*60,1) c25_min, ROUND(b[OFFSET(50)]*60,1) c50_min, ROUND(b[OFFSET(75)]*60,1) c75_min, ROUND(b[OFFSET(90)]*60,1) c90_min
FROM g WHERE n>=10 ORDER BY lvl, n DESC;
-- Monthly split: add FORMAT_TIMESTAMP('%Y-%m', c.created_on) mo to base and group by it (only the 3-south view was run so far).
