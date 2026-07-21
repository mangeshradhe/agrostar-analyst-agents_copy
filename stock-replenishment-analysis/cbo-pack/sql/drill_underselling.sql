-- Drill-down: SKU x state selling well below ROFO plan (top 200 by shortfall).
-- Budget window = ROFO's live rolling window (May-Sep 2026, per rofo_plan.sql); Sales window =
-- FYTD actuals (Apr-Jul 2026, per sales_sku_fc.sql). Windows are deliberately asymmetric --
-- same "plan so far" vs "sold so far" convention as the dashboard's top-line Budget/Actual
-- chart (Budget = full rolling plan, Actual = elapsed months only).
-- location_mst.state carries the SAME non-standard codes as rofo_fy26 (AD/BH/CT) -- remap
-- before joining sales (at FC grain) to budget (at state grain), or states silently split.
-- gap = sales_qty - budget_qty (negative = underselling). LEFT JOIN sales so zero-sale SKUs
-- still surface. Sorted worst-first, top 200.
WITH state_remap AS (
  SELECT UPPER(TRIM(location_id)) fc,
         CASE UPPER(TRIM(state))
           WHEN 'AD' THEN 'AP' WHEN 'BH' THEN 'BR' WHEN 'CT' THEN 'CG'
           ELSE UPPER(TRIM(state)) END AS state
  FROM `agrostar-data.pristine_wms_prod_db.location_mst`
),
budget AS (
  SELECT item_no, state, SUM(qty) AS budget_qty
  FROM (
    SELECT UPPER(TRIM(item_sku_code)) item_no,
           CASE UPPER(TRIM(state))
             WHEN 'AD' THEN 'AP' WHEN 'BH' THEN 'BR' WHEN 'CT' THEN 'CG'
             ELSE UPPER(TRIM(state)) END AS state,
           qty
    FROM `agrostar-data.bizfin_team.rofo_fy26`
    UNPIVOT (qty FOR month IN (may_qty, jun_qty, jul_qty, aug_qty, sep_qty))
    WHERE qty > 0
  )
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
  b.state,
  b.item_no AS sku,
  im.item_name,
  im.cogs,
  b.budget_qty,
  IFNULL(s.sales_qty, 0) AS sales_qty,
  IFNULL(s.sales_qty, 0) - b.budget_qty AS gap
FROM budget b
LEFT JOIN sales s ON b.item_no = s.item_no AND b.state = s.state
LEFT JOIN im ON b.item_no = im.item_no
ORDER BY gap ASC
LIMIT 200;
