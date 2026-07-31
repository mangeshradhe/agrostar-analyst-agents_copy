-- Drill-down: SKU x state procured well above what actually sold (top 200 by excess).
-- Both sides use the same window, FYTD Apr-Jul 2026 (unlike drill_underselling, which
-- deliberately mixes windows to match the ROFO-vs-actual convention -- there's no such
-- mismatch here since procurement and sales are both actuals).
-- proc_qty = 3-door fresh inflow model (A_po_grn + B_mfg_transfer_in + C_adjustment_in),
-- see fresh_inflow_sku_fc.sql / fresh-stock-inflow-doors memory -- same exclusions apply
-- (plant intake, FC<->FC redistribution, NA/STSNP/BAD-GOOD).
-- location_mst.state carries the SAME non-standard codes as rofo_fy26 (AD/BH/CT) -- remap
-- before aggregating to state, or states silently split.
-- gap = proc_qty - sales_qty (positive = overbuying). LEFT JOIN sales so zero-sale SKUs
-- still surface. Sorted worst-first, top 200.
WITH plants AS (
  SELECT UPPER(TRIM(location_id)) loc
  FROM `agrostar-data.pristine_wms_prod_db.location_mst`
  WHERE location_type = 'Manufacturing'
),
state_remap AS (
  SELECT UPPER(TRIM(location_id)) fc,
         CASE UPPER(TRIM(state))
           WHEN 'AD' THEN 'AP' WHEN 'BH' THEN 'BR' WHEN 'CT' THEN 'CG'
           ELSE UPPER(TRIM(state)) END AS state
  FROM `agrostar-data.pristine_wms_prod_db.location_mst`
),
door_a AS (
  SELECT UPPER(TRIM(gl.item_no)) item_no, UPPER(TRIM(gh.location_code)) fc,
         SUM(IFNULL(gl.qty, 0) + IFNULL(gl.bad_qty, 0)) qty
  FROM `agrostar-data.pristine_wms_prod_db.grn_line_serial` gl
  JOIN `agrostar-data.pristine_wms_prod_db.grn_header` gh ON gl.grn_no = gh.grn_no
  WHERE DATE(gh.completed_on, 'Asia/Kolkata') BETWEEN '2026-04-01' AND '2026-07-31'
    AND UPPER(TRIM(gh.location_code)) NOT IN (SELECT loc FROM plants)
  GROUP BY 1, 2
),
door_b AS (
  SELECT UPPER(TRIM(rgl.item_no)) item_no, UPPER(TRIM(rg.location_code)) fc,
         SUM(IFNULL(rgl.qty, 0)) qty
  FROM `agrostar-data.pristine_wms_prod_db.return_grn_header` rg
  JOIN `agrostar-data.pristine_wms_prod_db.return_grn_line` rgl ON rg.grn_no = rgl.grn_no
  JOIN (SELECT DISTINCT transfer_no, from_location_code
        FROM `agrostar-data.pristine_wms_prod_db.transfer_header`) th
    ON rg.document_no = th.transfer_no
  WHERE DATE(rg.completed_on, 'Asia/Kolkata') BETWEEN '2026-04-01' AND '2026-07-31'
    AND LOWER(rg.document_type) = 'transfer order'
    AND IFNULL(rg.is_cancel_mark, 0) = 0
    AND UPPER(TRIM(rg.location_code)) NOT IN (SELECT loc FROM plants)
    AND (UPPER(TRIM(th.from_location_code)) IN (SELECT loc FROM plants) OR th.transfer_no LIKE 'VTO%')
  GROUP BY 1, 2
),
door_c AS (
  SELECT UPPER(TRIM(al.item_code)) item_no, UPPER(TRIM(ah.location_code)) fc,
         SUM(al.quantity) qty
  FROM `agrostar-data.pristine_wms_prod_db.adjustment_header` ah
  JOIN `agrostar-data.pristine_wms_prod_db.adjustment_line` al ON ah.adjustment_no = al.adjustment_no
  WHERE DATE(ah.completed_on, 'Asia/Kolkata') BETWEEN '2026-04-01' AND '2026-07-31'
    AND ah.approve_status = 'APPROVED' AND ah.adj_type IN ('PA', 'PAWOB')
    AND UPPER(TRIM(ah.location_code)) NOT IN (SELECT loc FROM plants)
  GROUP BY 1, 2
),
inflow AS (
  SELECT item_no, fc, qty FROM door_a
  UNION ALL SELECT item_no, fc, qty FROM door_b
  UNION ALL SELECT item_no, fc, qty FROM door_c
),
procurement AS (
  SELECT i.item_no, sr.state, SUM(i.qty) AS proc_qty
  FROM inflow i
  JOIN state_remap sr ON i.fc = sr.fc
  GROUP BY 1, 2
),
sales AS (
  SELECT UPPER(TRIM(ir.ItemSKU)) item_no, sr.state, SUM(IFNULL(ir.good_qty, 1)) AS sales_qty
  FROM `agrostar-data.pristine_wms_prod_db.invoiced_report` ir
  JOIN state_remap sr ON UPPER(TRIM(ir.Facilitycode)) = sr.fc
  WHERE DATE(ir.CreatedOn, 'Asia/Kolkata') BETWEEN '2026-04-01' AND '2026-07-31'
    AND ir.line_status != 'CANCELLED' AND IFNULL(ir.is_return, 0) = 0
  GROUP BY 1, 2
),
im AS (
  SELECT UPPER(TRIM(item_code)) item_no, ANY_VALUE(name) item_name, MAX(c.cogs) cogs
  FROM `agrostar-data.pristine_wms_prod_db.item_mst` im
  LEFT JOIN (SELECT UPPER(TRIM(sku_code)) sku, MAX(running_cogs) cogs
             FROM `agrostar-data.static_tables_views.average_weighted_cogs` GROUP BY 1) c
    ON UPPER(TRIM(im.item_code)) = c.sku
  GROUP BY 1
)
SELECT
  p.state,
  p.item_no AS sku,
  im.item_name,
  im.cogs,
  p.proc_qty,
  IFNULL(s.sales_qty, 0) AS sales_qty,
  p.proc_qty - IFNULL(s.sales_qty, 0) AS gap
FROM procurement p
LEFT JOIN sales s ON p.item_no = s.item_no AND p.state = s.state
LEFT JOIN im ON p.item_no = im.item_no
ORDER BY gap DESC
LIMIT 200;
