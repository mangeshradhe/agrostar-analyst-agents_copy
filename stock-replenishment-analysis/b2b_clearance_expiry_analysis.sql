-- B2B invoice analysis: clearance-sale offer split + near-expiry (<150 days) billing
-- FY 2026-27 (invoices created on/after 2026-04-01, Asia/Kolkata)
-- B2B definition: InvoiceNo starts with 'U' (per Darpan, 2026-07-09)
-- Clearance-sale offer: order_management_orderitem.max_expiry_days > 0
-- Join key: invoiced_report.DisplayOrderCode = YYMM || sales_order_id  -> SUBSTR(DisplayOrderCode, 5)
-- Note: invoiced_report is serial-level (1 row = 1 unit, good_qty = 1)

WITH inv AS (
  SELECT SAFE_CAST(SUBSTR(DisplayOrderCode,5) AS INT64) AS order_id, ItemSKU, InvoiceNo, good_qty,
         DATE(CreatedOn,'Asia/Kolkata') AS inv_date, expiry_date
  FROM `agrostar-data.pristine_wms_prod_db.invoiced_report`
  WHERE DATE(CreatedOn,'Asia/Kolkata') >= '2026-04-01' AND InvoiceNo LIKE 'U%'
),
oi AS (
  SELECT order_id, item_sku, MAX(IFNULL(max_expiry_days,0)) AS max_expiry_days
  FROM `agrostar-data.prod_db_views.order_management_orderitem`
  GROUP BY 1,2
),
ord AS (
  SELECT o.sales_order_id, IFNULL(NULLIF(INITCAP(TRIM(sa.state)),''),'Unknown') AS state
  FROM `agrostar-data.prod_db_views.order_management_order` o
  LEFT JOIN `agrostar-data.prod_db_views.csr_shippingaddress` sa ON o.shipping_address_id = sa.id
),
base AS (
  SELECT inv.*, IFNULL(ord.state,'Unknown') AS state,
         (IFNULL(oi.max_expiry_days,0) > 0) AS is_clearance
  FROM inv
  LEFT JOIN oi ON inv.order_id = oi.order_id AND inv.ItemSKU = oi.item_sku
  LEFT JOIN ord ON inv.order_id = ord.sales_order_id
)
SELECT
  IFNULL(state,'== TOTAL ==') AS state,
  COUNT(DISTINCT InvoiceNo) AS invoices,
  COUNT(DISTINCT order_id) AS orders,
  COUNT(DISTINCT IF(is_clearance, CONCAT(order_id,'|',ItemSKU), NULL)) AS clearance_order_items,
  COUNT(DISTINCT IF(NOT is_clearance, CONCAT(order_id,'|',ItemSKU), NULL)) AS non_clearance_order_items,
  SUM(IF(is_clearance, good_qty, 0)) AS clearance_units,
  SUM(IF(NOT is_clearance, good_qty, 0)) AS non_clearance_units,
  SUM(IF(NOT is_clearance AND expiry_date IS NOT NULL AND DATE_DIFF(expiry_date, inv_date, DAY) < 150, good_qty, 0)) AS nc_units_expiry_lt150d,
  SUM(IF(NOT is_clearance AND expiry_date IS NOT NULL, good_qty, 0)) AS nc_units_with_expiry_date,
  SUM(IF(NOT is_clearance AND expiry_date IS NULL, good_qty, 0)) AS nc_units_no_expiry_date
FROM base
GROUP BY ROLLUP(state)
ORDER BY non_clearance_units DESC;
