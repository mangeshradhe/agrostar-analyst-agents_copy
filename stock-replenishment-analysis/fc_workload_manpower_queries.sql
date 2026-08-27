-- FC Workload & Manpower Analysis — GJ/MH/RJ/MP/UP, Apr-Jul FY25-26 vs FY26-27
-- Companion to 05-fc-workload-manpower-analysis.md. Read that file for definitions/caveats.
--
-- *** CRITICAL: join pick_line to pick_header_arc_main, NOT pick_header ***
-- pick_header stopped populating at real volume from Dec-2025 onward (dropped from ~25-29K
-- rows/month to <350/month) while pick_line kept accumulating normally. pick_header_arc_main
-- has the complete, correct data across the full range. Joining to pick_header for FY26-27
-- silently drops ~99% of rows and looks like picking volume collapsed -- it didn't; it's a
-- pipeline gap. Verified by comparing monthly row counts across pick_header vs
-- pick_header_arc_main vs pick_line for 2025-04 through 2026-08.

-- FC universe used throughout: location_type='Warehouse' in the 5 states (excludes Manufacturing
-- and COCO Stores, which also carry these state codes in location_mst).
-- WITH fc AS (
--   SELECT location_id, state_name FROM `pristine_wms_prod_db.location_mst`
--   WHERE location_type='Warehouse' AND state IN ('GJ','MH','RJ','MP','UP')
-- )

-- =========================================================================
-- 1. INBOUND: PO GRN + B2B/B2C Return GRN, units, by state x FC x month
-- =========================================================================
WITH fc AS (
  SELECT location_id, state AS state_code, state_name FROM `pristine_wms_prod_db.location_mst`
  WHERE location_type='Warehouse' AND state IN ('GJ','MH','RJ','MP','UP')
),
po_grn AS (
  SELECT gh.location_code AS loc, gh.created_on AS ts, gl.physical_qty AS qty
  FROM `pristine_wms_prod_db.grn_header` gh
  JOIN `pristine_wms_prod_db.grn_line` gl ON gh.grn_no = gl.grn_no
  WHERE gh.document_type = 'Purchase Order'
    AND ((gh.created_on BETWEEN '2025-04-01' AND '2025-07-31 23:59:59')
      OR (gh.created_on BETWEEN '2026-04-01' AND '2026-07-31 23:59:59'))
),
ret_grn AS (
  SELECT rh.location_code AS loc, rh.created_on AS ts, rh.Channal AS channel, rl.qty AS qty
  FROM `pristine_wms_prod_db.return_grn_header` rh
  JOIN `pristine_wms_prod_db.return_grn_line` rl ON rh.grn_no = rl.grn_no
  WHERE ((rh.created_on BETWEEN '2025-04-01' AND '2025-07-31 23:59:59')
      OR (rh.created_on BETWEEN '2026-04-01' AND '2026-07-31 23:59:59'))
),
combined AS (
  SELECT loc, ts, 'PO GRN' AS inbound_type, qty FROM po_grn
  UNION ALL
  SELECT loc, ts, CONCAT(IFNULL(channel,'Unknown'),' Return GRN') AS inbound_type, qty FROM ret_grn
)
SELECT fc.state_name, c.loc, c.inbound_type,
  CASE WHEN c.ts BETWEEN '2025-04-01' AND '2025-07-31 23:59:59' THEN 'FY25-26' ELSE 'FY26-27' END AS period,
  FORMAT_TIMESTAMP('%Y-%m', c.ts) AS ym,
  SUM(c.qty) AS units, COUNT(*) AS lines
FROM combined c JOIN fc ON c.loc = fc.location_id
GROUP BY 1,2,3,4,5
ORDER BY 1,2,3,4,5;

-- =========================================================================
-- 2. OUTBOUND: units picked against Sale Order / Transfer Order, by state x FC x month
--    (dispatch itself excluded -- that's first-mile, not FC ops)
-- =========================================================================
-- WITH fc AS (...)
SELECT fc.state_name, ph.location_code AS loc,
  CASE WHEN ph.source_document IN ('Sales Order') THEN 'Sale Order'
       WHEN ph.source_document LIKE '%ransfer%' THEN 'Transfer Order'
       ELSE ph.source_document END AS doc_type,
  CASE WHEN pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59' THEN 'FY25-26' ELSE 'FY26-27' END AS period,
  FORMAT_TIMESTAMP('%Y-%m', pl.pick_create_date) AS ym,
  SUM(pl.qty_picked) AS units_picked, COUNT(*) AS lines, COUNT(DISTINCT pl.pick_no) AS pick_tasks
FROM `pristine_wms_prod_db.pick_line` pl
JOIN `pristine_wms_prod_db.pick_header_arc_main` ph ON pl.pick_no = ph.pick_no  -- NOTE: arc_main, not pick_header
JOIN fc ON ph.location_code = fc.location_id
WHERE (pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59')
   OR (pl.pick_create_date BETWEEN '2026-04-01' AND '2026-07-31 23:59:59')
GROUP BY 1,2,3,4,5
ORDER BY 1,2,3,4,5;

-- =========================================================================
-- 3. EFFICIENCY: median/avg/P10/P90 units per active person-day, per persona
--    Picker = picking (assign_user) + consolidation (consolidation_person_id)
--    OQC/Biller = oqc_person_id, starts only after consolidation completes
--    Template below is for Picking; swap the person/date/qty columns for
--    Consolidation (consolidation_person_id / consolidation_date / consolidation_qty)
--    and OQC (oqc_person_id / oqc_date / oqc_good_qty+oqc_bad_qty+oqc_miss_qty).
--    IMPORTANT: this template groups by doc_type, which naturally separates Sale/Transfer
--    from stray "Other" volume (Purchase Return, RGP Transfer Order). If you instead pool
--    a state-level rollup WITHOUT the doc_type dimension, add an explicit
--    `WHERE ph.source_document IN ('Sales Order','transfer order','Transfer Order')`
--    filter -- verified negligible impact here (<=1 unit/cell) but don't assume that holds
--    at every cut.
-- =========================================================================
-- WITH fc AS (...)
WITH raw AS (
  SELECT fc.state_name, ph.location_code AS loc,
    CASE WHEN ph.source_document IN ('Sales Order') THEN 'Sale Order'
         WHEN ph.source_document LIKE '%ransfer%' THEN 'Transfer Order' ELSE 'Other' END AS doc_type,
    ph.assign_user AS person, DATE(pl.pick_create_date) AS pday,
    CASE WHEN pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59' THEN 'FY25-26' ELSE 'FY26-27' END AS period,
    pl.qty_picked AS qty
  FROM `pristine_wms_prod_db.pick_line` pl
  JOIN `pristine_wms_prod_db.pick_header_arc_main` ph ON pl.pick_no = ph.pick_no
  JOIN (SELECT location_id, state_name FROM `pristine_wms_prod_db.location_mst`
        WHERE location_type='Warehouse' AND state IN ('GJ','MH','RJ','MP','UP')) fc
    ON ph.location_code = fc.location_id
  WHERE ((pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59')
      OR (pl.pick_create_date BETWEEN '2026-04-01' AND '2026-07-31 23:59:59'))
    AND ph.assign_user IS NOT NULL AND pl.qty_picked > 0
),
person_day AS (
  SELECT state_name, loc, doc_type, person, pday, period, SUM(qty) AS units
  FROM raw GROUP BY 1,2,3,4,5,6
)
SELECT state_name, loc, doc_type, period,
  COUNT(DISTINCT person) AS active_pickers, COUNT(*) AS picker_days,
  ROUND(AVG(units),1) AS avg_units_per_day,
  APPROX_QUANTILES(units,100)[OFFSET(50)] AS median_units_per_day,
  APPROX_QUANTILES(units,100)[OFFSET(10)] AS p10_worst,
  APPROX_QUANTILES(units,100)[OFFSET(90)] AS p90_best
FROM person_day
GROUP BY 1,2,3,4
ORDER BY 1,2,3,4;

-- =========================================================================
-- 4. CYCLE TIME: median minutes per stage (pick, pick->consolidate, consolidate->OQC, total)
--    Filters out negative/absurd gaps (data-entry artifacts) before taking the median.
-- =========================================================================
-- WITH fc AS (...)
WITH raw AS (
  SELECT fc.state_name,
    CASE WHEN pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59' THEN 'FY25-26'
         WHEN pl.pick_create_date BETWEEN '2026-04-01' AND '2026-07-31 23:59:59' THEN 'FY26-27' END AS period,
    TIMESTAMP_DIFF(pl.picked_date, pl.pick_create_date, MINUTE) AS pick_mins,
    TIMESTAMP_DIFF(pl.consolidation_date, pl.picked_date, MINUTE) AS pick_to_consol_mins,
    TIMESTAMP_DIFF(pl.oqc_date, pl.consolidation_date, MINUTE) AS consol_to_oqc_mins,
    TIMESTAMP_DIFF(pl.oqc_date, pl.pick_create_date, MINUTE) AS total_mins
  FROM `pristine_wms_prod_db.pick_line` pl
  JOIN `pristine_wms_prod_db.pick_header_arc_main` ph ON pl.pick_no = ph.pick_no
  JOIN (SELECT location_id, state_name FROM `pristine_wms_prod_db.location_mst`
        WHERE location_type='Warehouse' AND state IN ('GJ','MH','RJ','MP','UP')) fc
    ON ph.location_code = fc.location_id
  WHERE ph.source_document IN ('Sales Order','transfer order','Transfer Order')
    AND ((pl.pick_create_date BETWEEN '2025-04-01' AND '2025-07-31 23:59:59')
      OR (pl.pick_create_date BETWEEN '2026-04-01' AND '2026-07-31 23:59:59'))
)
SELECT state_name, period, COUNT(*) lines,
  APPROX_QUANTILES(IF(pick_mins BETWEEN 0 AND 20000, pick_mins, NULL),100)[OFFSET(50)] AS median_pick_mins,
  APPROX_QUANTILES(IF(pick_to_consol_mins BETWEEN 0 AND 20000, pick_to_consol_mins, NULL),100)[OFFSET(50)] AS median_pick_to_consol_mins,
  APPROX_QUANTILES(IF(consol_to_oqc_mins BETWEEN 0 AND 20000, consol_to_oqc_mins, NULL),100)[OFFSET(50)] AS median_consol_to_oqc_mins,
  APPROX_QUANTILES(IF(total_mins BETWEEN 0 AND 40000, total_mins, NULL),100)[OFFSET(50)] AS median_total_mins
FROM raw
GROUP BY 1,2
ORDER BY 1,2;
