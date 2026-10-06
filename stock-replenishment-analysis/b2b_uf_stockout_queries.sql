-- B2B UF% (unfulfillable-order) stock-out analysis — reusable queries
-- See 06-b2b-uf-stockout-analysis.md for method notes and findings.

-- Core building blocks, reused across every query below --------------------

-- 1) Valid B2B order universe (Darpan's definition; excludes cancelled/error/edited)
-- base_orders:
--   SELECT o.sales_order_id, o.warehouse AS fc, o.created_on
--   FROM `agrostar-data.prod_db_views.order_management_order` o
--   WHERE UPPER(o.initiating_source) LIKE 'B2B%'
--     AND UPPER(o.order_type) NOT LIKE 'OFFLINE%'
--     AND UPPER(o.order_type) NOT LIKE 'IPT%'
--     AND UPPER(o.order_type) NOT LIKE 'RETURN%'
--     AND UPPER(o.order_type) NOT LIKE 'COCO%'
--     AND UPPER(o.order_type) NOT LIKE 'TRANSFER%'
--     AND UPPER(o.status) NOT LIKE '%ERROR%' AND UPPER(o.status) NOT LIKE '%CANCEL%' AND UPPER(o.status) NOT LIKE '%EDIT%'
--     AND UPPER(o.unicommerce_status) NOT LIKE '%ERROR%' AND UPPER(o.unicommerce_status) NOT LIKE '%CANCEL%' AND UPPER(o.unicommerce_status) NOT LIKE '%EDIT%'

-- 2) Orders that ever hit an unfulfillable hold (orderhold is multi-row per order — dedupe)
-- uf_orders:
--   SELECT DISTINCT SAFE_CAST(orderId AS INT64) AS sales_order_id
--   FROM `agrostar-data.prod_db_views.orderhold`
--   WHERE REGEXP_CONTAINS(UPPER(COALESCE(crmHoldReasons,'')), r'UNFULFILLABLE_ORDER')
--      OR REGEXP_CONTAINS(UPPER(COALESCE(warehouseHoldReasons,'')), r'UNFULFILLABLE_ORDER')

-- CP+CN baseline: X units ordered, Y UF units, UF% (Apr'25-Jul'26, all SKUs) -----
WITH base_orders AS (
  SELECT o.sales_order_id
  FROM `agrostar-data.prod_db_views.order_management_order` o
  WHERE o.created_on >= '2025-04-01' AND o.created_on < '2026-08-01'
    AND UPPER(o.initiating_source) LIKE 'B2B%'
    AND UPPER(o.order_type) NOT LIKE 'OFFLINE%'
    AND UPPER(o.order_type) NOT LIKE 'IPT%'
    AND UPPER(o.order_type) NOT LIKE 'RETURN%'
    AND UPPER(o.order_type) NOT LIKE 'COCO%'
    AND UPPER(o.order_type) NOT LIKE 'TRANSFER%'
    AND UPPER(o.status) NOT LIKE '%ERROR%' AND UPPER(o.status) NOT LIKE '%CANCEL%' AND UPPER(o.status) NOT LIKE '%EDIT%'
    AND UPPER(o.unicommerce_status) NOT LIKE '%ERROR%' AND UPPER(o.unicommerce_status) NOT LIKE '%CANCEL%' AND UPPER(o.unicommerce_status) NOT LIKE '%EDIT%'
),
uf_orders AS (
  SELECT DISTINCT SAFE_CAST(orderId AS INT64) AS sales_order_id
  FROM `agrostar-data.prod_db_views.orderhold`
  WHERE REGEXP_CONTAINS(UPPER(COALESCE(crmHoldReasons,'')), r'UNFULFILLABLE_ORDER')
     OR REGEXP_CONTAINS(UPPER(COALESCE(warehouseHoldReasons,'')), r'UNFULFILLABLE_ORDER')
)
SELECT
  SUM(oi.quantity) AS X_units_ordered,
  SUM(IF(u.sales_order_id IS NOT NULL, oi.quantity, 0)) AS Y_units_uf,
  SAFE_DIVIDE(SUM(IF(u.sales_order_id IS NOT NULL, oi.quantity, 0)), SUM(oi.quantity)) AS uf_pct
FROM `agrostar-data.prod_db_views.order_management_orderitem` oi
JOIN base_orders b ON oi.order_id = b.sales_order_id
JOIN `agrostar-data.pristine_wms_prod_db.item_mst` m ON oi.item_sku = m.item_code
LEFT JOIN uf_orders u ON oi.order_id = u.sales_order_id
WHERE m.category_code IN ('CP','CN');
-- Result (27 Aug 2026): X=16,384,329  Y=2,387,997  UF%=14.57%  <- baseline to track against

-- Swap the WHERE clauses / add GROUP BY to reuse for:
--   - monthly trend: GROUP BY DATE_TRUNC(DATE(o.created_on), MONTH)
--   - per FC: GROUP BY o.warehouse (filter o.warehouse IS NOT NULL)
--   - per molecule: JOIN item_mst, GROUP BY m.sub_product_group (NOT sub_sub_product_group — that's brand, not molecule)
--   - category_code is the correct coarse rollup (CP/CN/SEEDS/HW/...) — do NOT use item_mst.product_group,
--     it looks similar but is a different, messier field.
