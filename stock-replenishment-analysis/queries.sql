-- ============================================================
-- System TO – Budget Balancing analysis: key BigQuery queries
-- Project: agrostar-data | Dataset: catalog_views | Jul 2026
-- ============================================================

-- 1. Headline overview
SELECT
  COUNT(*) AS total_lines,
  COUNT(DISTINCT transfer_no) AS distinct_transfer_nos,
  COUNT(DISTINCT sku_code) AS distinct_skus,
  COUNT(DISTINCT from_facility) AS source_fcs,
  COUNT(DISTINCT to_facility) AS dest_fcs,
  SUM(transfer_qty) AS total_transfer_qty
FROM `agrostar-data.catalog_views.catalog_management_transferorders`
WHERE created_on > '2026-07-06' AND transfer_reason = 'System TO - Budget balancing';

-- 2. Status funnel (action_status = human decision; transfer_status = lifecycle)
SELECT transfer_status, action_status, COUNT(*) AS lines, SUM(transfer_qty) AS qty
FROM `agrostar-data.catalog_views.catalog_management_transferorders`
WHERE created_on > '2026-07-06' AND transfer_reason = 'System TO - Budget balancing'
GROUP BY 1,2 ORDER BY lines DESC;

-- 3. Lane table (all directed from->to pairs)
SELECT from_facility, from_state, to_facility, to_state,
  STRING_AGG(DISTINCT transfer_type, ', ') AS transfer_type,
  COUNT(*) AS lines, COUNT(DISTINCT transfer_no) AS tos,
  COUNT(DISTINCT sku_code) AS skus, SUM(transfer_qty) AS qty,
  MAX(distance) AS distance_km
FROM `agrostar-data.catalog_views.catalog_management_transferorders`
WHERE created_on > '2026-07-06' AND transfer_reason = 'System TO - Budget balancing'
GROUP BY 1,2,3,4 ORDER BY qty DESC;

-- 4. Net flow per FC
WITH t AS (
  SELECT * FROM `agrostar-data.catalog_views.catalog_management_transferorders`
  WHERE created_on > '2026-07-06' AND transfer_reason = 'System TO - Budget balancing'
),
outb AS (SELECT from_facility fc, from_state st, SUM(transfer_qty) out_qty FROM t GROUP BY 1,2),
inb  AS (SELECT to_facility fc, SUM(transfer_qty) in_qty FROM t GROUP BY 1)
SELECT o.fc, o.st AS state, o.out_qty, i.in_qty, i.in_qty - o.out_qty AS net_qty
FROM outb o FULL OUTER JOIN inb i USING (fc)
ORDER BY net_qty DESC;

-- 5. Tonnage per lane: generated vs approved
--    (weights: pristine_unicommerce_views.item_master.weight__gms_, MAX per product_code)
WITH wt AS (
  SELECT product_code, MAX(weight__gms_) AS wt_gms
  FROM `agrostar-data.pristine_unicommerce_views.item_master`
  WHERE weight__gms_ > 0 GROUP BY 1
)
SELECT o.from_facility, o.to_facility, MAX(o.distance) AS dist_km,
  COUNT(*) AS lines, COUNTIF(o.action_status='APPROVED') AS appr_lines,
  ROUND(SUM(o.transfer_qty * w.wt_gms)/1e6, 2) AS generated_tonnes,
  ROUND(SUM(IF(o.action_status='APPROVED', o.transfer_qty * w.wt_gms, 0))/1e6, 2) AS approved_tonnes,
  ROUND(100 * SAFE_DIVIDE(
    SUM(IF(o.action_status='APPROVED', o.transfer_qty * w.wt_gms, 0)),
    SUM(o.transfer_qty * w.wt_gms)), 0) AS pct_wt_approved
FROM `agrostar-data.catalog_views.catalog_management_transferorders` o
JOIN wt w ON o.sku_code = w.product_code
WHERE o.created_on > '2026-07-06' AND o.transfer_reason = 'System TO - Budget balancing'
GROUP BY 1,2 ORDER BY generated_tonnes DESC;

-- 6. Top SKUs: requested vs approved (qty + tonnes)
WITH wt AS (
  SELECT product_code, MAX(weight__gms_) AS wt_gms
  FROM `agrostar-data.pristine_unicommerce_views.item_master`
  WHERE weight__gms_ > 0 GROUP BY 1
)
SELECT o.sku_name,
  SUM(o.transfer_qty) AS requested_qty,
  ROUND(SUM(o.transfer_qty * w.wt_gms)/1e6, 2) AS requested_tonnes,
  SUM(IF(o.action_status='APPROVED', o.transfer_qty, 0)) AS approved_qty,
  ROUND(SUM(IF(o.action_status='APPROVED', o.transfer_qty * w.wt_gms, 0))/1e6, 2) AS approved_tonnes
FROM `agrostar-data.catalog_views.catalog_management_transferorders` o
JOIN wt w ON o.sku_code = w.product_code
WHERE o.created_on > '2026-07-06' AND o.transfer_reason = 'System TO - Budget balancing'
GROUP BY 1 ORDER BY requested_tonnes DESC;

-- 7. First-10-days benchmark: Manual BM (Apr-Jun) vs System TO (Jul)
WITH wt AS (
  SELECT product_code, MAX(weight__gms_) AS wt_gms
  FROM `agrostar-data.pristine_unicommerce_views.item_master`
  WHERE weight__gms_ > 0 GROUP BY 1
),
base AS (
  SELECT o.*, o.transfer_qty * w.wt_gms / 1e6 AS tonnes,
    CASE WHEN transfer_reason IN ('Current BM','Coming BM') THEN 'Manual BM' ELSE 'System TO' END AS cohort,
    FORMAT_DATE('%Y-%m', DATE(created_on,'Asia/Kolkata')) AS month,
    CASE WHEN transfer_reason IN ('Current BM','Coming BM')
         THEN transfer_status NOT IN ('CANCELLED','SYSTEM_CANCELLED','REJECTED')
         ELSE action_status = 'APPROVED' END AS is_approved
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` o
  LEFT JOIN wt w ON o.sku_code = w.product_code
  WHERE created_on >= '2026-04-01'
    AND transfer_reason IN ('Current BM','Coming BM','System TO - Budget balancing')
    AND EXTRACT(DAY FROM DATE(created_on,'Asia/Kolkata')) <= 10
)
SELECT month, cohort,
  COUNT(*) AS lines, COUNT(DISTINCT transfer_no) AS tos, SUM(transfer_qty) AS qty,
  ROUND(SUM(tonnes),1) AS generated_tonnes,
  ROUND(SUM(IF(is_approved, tonnes, 0)),1) AS approved_tonnes,
  ROUND(100*COUNTIF(transfer_status IN ('CANCELLED','SYSTEM_CANCELLED','REJECTED'))/COUNT(*),1) AS pct_lines_killed,
  ROUND(100*COUNTIF(transfer_status='RECEIVED')/COUNT(*),1) AS pct_received
FROM base GROUP BY 1,2 ORDER BY 1,2;

-- 8. Quantity-edit audit (proves approvals are binary, no trimming)
SELECT action_status, COUNT(*) AS lines,
  COUNTIF(actual_transfer_qty = transfer_qty) AS actual_eq_proposed,
  COUNTIF(actual_transfer_qty = 0) AS actual_zero,
  COUNTIF(actual_transfer_qty > 0 AND actual_transfer_qty < transfer_qty) AS actual_reduced,
  COUNTIF(actual_transfer_qty > transfer_qty) AS actual_increased
FROM `agrostar-data.catalog_views.catalog_management_transferorders`
WHERE created_on > '2026-07-06' AND transfer_reason = 'System TO - Budget balancing'
GROUP BY 1;

-- 9. VTO document series check
SELECT transfer_status, COUNT(*) AS lines, COUNT(DISTINCT transfer_no) AS tos, SUM(transfer_qty) AS qty
FROM `agrostar-data.catalog_views.catalog_management_transferorders`
WHERE created_on >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND STARTS_WITH(transfer_no, 'VTO')
GROUP BY 1;

-- Note: budget_tci = transfer_qty * distance (unit-km), NOT rupees.
-- Note: edited_transfer_qty is a workflow field (0 until approved, then = transfer_qty).
-- Note: to_stock_coverage_days is 0 on all System TO lines (instrumentation gap).
