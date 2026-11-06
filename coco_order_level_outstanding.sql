-- COCO / EBO order-level outstanding: one row per DELIVERED COCO order, RECONCILED or NOT_RECONCILED,
-- with store totals on every row. Ties to the app: store_unreconciled_total - store_paid_but_not_applied = store_outstanding.
-- Validated 2026-11-05: COCO_Aau 37292 - 4243 = 33049 (matches app). Companion to coco_store_outstanding.sql.
-- Tip: if pasting into a tool breaks the query, delete these comment lines first.

WITH stores AS (
  SELECT
    agroex_franchise_id,
    ANY_VALUE(CAST(reference_customer_id AS STRING)) AS store_id,
    ANY_VALUE(name)                                  AS store_name,
    ANY_VALUE(ancestor_institutions_name)            AS ebo_group
  FROM `agrostar-data.galaxy_views.institution`
  WHERE LOWER(ancestor_institutions_name) LIKE '% ebo %'
    AND reference_customer_id IS NOT NULL
    AND agroex_franchise_id IS NOT NULL
  GROUP BY agroex_franchise_id
),
pkgs AS (
  SELECT
    dsp.order_id,
    CAST(dsp.to_franchise_id AS STRING) AS franchise_id,
    STRING_AGG(DISTINCT dsp.code, ', ')  AS package_codes,
    COUNT(DISTINCT dsp.code)             AS packages,
    COUNTIF(dsp.reconciliation_status = 'to_be_paid') AS pkgs_to_be_paid
  FROM `agrostar-data.prod_db_views.delivery_shippingpackage` dsp
  JOIN stores s ON s.agroex_franchise_id = CAST(dsp.to_franchise_id AS STRING)
  GROUP BY 1, 2
),
orders AS (
  SELECT
    p.franchise_id,
    o.sales_order_id,
    o.unicommerce_id                              AS display_order_code,
    DATE(o.created_on, 'Asia/Kolkata')            AS order_date,
    o.owner_id                                    AS farmer_id,
    o.grand_total,
    IFNULL(o.cod_amount, 0)                       AS cod_due,
    IFNULL(o.online_paid_amount, 0)               AS online_due,
    IFNULL(o.cod_amount, 0) + IFNULL(o.online_paid_amount, 0) AS total_due,
    p.package_codes,
    p.packages,
    IF(p.pkgs_to_be_paid > 0, 'NOT_RECONCILED', 'RECONCILED') AS reconciliation
  FROM pkgs p
  JOIN `agrostar-data.prod_db_views.order_management_order` o
    ON p.order_id = CAST(o.unicommerce_id AS STRING)
  WHERE o.order_type = 'COCO'
    AND o.status = 'DELIVERED'
),
payments AS (
  SELECT
    CAST(delp.franchise_id AS STRING) AS franchise_id,
    SUM(delph.amount_settled)         AS total_paid
  FROM `agrostar-data.prod_db_views.delivery_payment` delp
  JOIN `agrostar-data.prod_db_views.delivery_lppostpaidtransaction` delph
    ON delp.lp_postpaid_transaction_id = delph.id
  JOIN stores s ON s.agroex_franchise_id = CAST(delp.franchise_id AS STRING)
  WHERE delp.creation_type NOT IN ('return', 'auto')
  GROUP BY 1
)
SELECT
  s.store_id,
  s.store_name,
  REPLACE(s.ebo_group, 'Agrostar EBO ', '') AS state,
  o.order_date,
  o.sales_order_id,
  o.display_order_code,
  o.farmer_id,
  o.grand_total,
  o.cod_due,
  o.online_due,
  o.total_due,
  o.reconciliation,
  IF(o.reconciliation = 'NOT_RECONCILED', o.total_due, 0) AS unreconciled_amount,
  o.package_codes,
  o.packages,
  SUM(o.total_due) OVER (PARTITION BY o.franchise_id) AS store_total_due,
  IFNULL(p.total_paid, 0)                             AS store_total_paid,
  SUM(o.total_due) OVER (PARTITION BY o.franchise_id) - IFNULL(p.total_paid, 0) AS store_outstanding,
  SUM(IF(o.reconciliation = 'NOT_RECONCILED', o.total_due, 0)) OVER (PARTITION BY o.franchise_id) AS store_unreconciled_total,
  SUM(IF(o.reconciliation = 'NOT_RECONCILED', o.total_due, 0)) OVER (PARTITION BY o.franchise_id)
    - (SUM(o.total_due) OVER (PARTITION BY o.franchise_id) - IFNULL(p.total_paid, 0)) AS store_paid_but_not_applied
FROM orders o
JOIN stores s ON s.agroex_franchise_id = o.franchise_id
LEFT JOIN payments p ON p.franchise_id = o.franchise_id
ORDER BY s.store_name, o.reconciliation, o.order_date DESC
