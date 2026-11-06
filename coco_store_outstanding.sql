-- COCO / EBO store-wise outstanding — matches the Saathi app "Hisaab → Total Outstanding".
-- Validated 2026-11-05: COCO_Aau (12511131) = ₹33,049, same as the app.
--
-- Outstanding = (COD + online amount of DELIVERED COCO orders)
--             − (QR + van + manual payments, incl. van rows not yet verified)
-- Returned orders and 'return' payment rows are left out on both sides ('auto' rows are ₹0).
-- NOTE: on COCO orders owner_id is the FARMER — orders are tied to a store via the
--       shipping package's to_franchise_id, not owner_id.

WITH stores AS (            -- one row per EBO store
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

store_orders AS (           -- order → store, via the package (one row per order)
  SELECT DISTINCT
    dsp.order_id,
    CAST(dsp.to_franchise_id AS STRING) AS franchise_id
  FROM `agrostar-data.prod_db_views.delivery_shippingpackage` dsp
  JOIN stores s ON s.agroex_franchise_id = CAST(dsp.to_franchise_id AS STRING)
),

liability AS (              -- what the store owes: delivered COCO orders
  SELECT
    so.franchise_id,
    COUNT(*)                                   AS delivered_orders,
    SUM(IFNULL(o.cod_amount, 0))               AS cod_due,
    SUM(IFNULL(o.online_paid_amount, 0))       AS online_due,
    SUM(IFNULL(o.cod_amount, 0) + IFNULL(o.online_paid_amount, 0)) AS total_due,
    MAX(DATE(o.created_on, 'Asia/Kolkata'))    AS last_order_date
  FROM store_orders so
  JOIN `agrostar-data.prod_db_views.order_management_order` o
    ON so.order_id = CAST(o.unicommerce_id AS STRING)
  WHERE o.order_type = 'COCO'
    AND o.status = 'DELIVERED'
  GROUP BY so.franchise_id
),

payments AS (               -- what the store has paid
  SELECT
    CAST(delp.franchise_id AS STRING) AS franchise_id,
    SUM(IF(delp.by_user = 'system' AND delp.creation_type IN ('manual', 'COCO-QR-Payment'),
           delph.amount_settled, 0))                                   AS qr_paid,
    SUM(IF(delp.creation_type = 'van', delph.amount_settled, 0))       AS van_paid,
    SUM(IF(delp.creation_type = 'manual' AND delp.by_user != 'system',
           delph.amount_settled, 0))                                   AS manual_paid,
    SUM(delph.amount_settled)                                          AS total_paid,
    SUM(IF(delp.status = 'created', delph.amount_settled, 0))          AS paid_not_yet_verified,
    MAX(DATE(delp.created_on, 'Asia/Kolkata'))                         AS last_payment_date
  FROM `agrostar-data.prod_db_views.delivery_payment` delp
  JOIN `agrostar-data.prod_db_views.delivery_lppostpaidtransaction` delph
    ON delp.lp_postpaid_transaction_id = delph.id
  JOIN stores s ON s.agroex_franchise_id = CAST(delp.franchise_id AS STRING)
  WHERE delp.creation_type NOT IN ('return', 'auto')     -- reversals / ₹0 rows, not payments
  GROUP BY 1
)

SELECT
  s.store_id,
  s.store_name,
  REPLACE(s.ebo_group, 'Agrostar EBO ', '')       AS state,
  IFNULL(l.delivered_orders, 0)                    AS delivered_orders,
  IFNULL(l.cod_due, 0)                             AS cod_due,
  IFNULL(l.online_due, 0)                          AS online_due,
  IFNULL(l.total_due, 0)                           AS total_due,
  IFNULL(p.qr_paid, 0)                             AS qr_paid,
  IFNULL(p.van_paid, 0)                            AS van_paid,
  IFNULL(p.manual_paid, 0)                         AS manual_paid,
  IFNULL(p.total_paid, 0)                          AS total_paid,
  IFNULL(p.paid_not_yet_verified, 0)               AS paid_not_yet_verified,
  IFNULL(l.total_due, 0) - IFNULL(p.total_paid, 0) AS outstanding,
  l.last_order_date,
  p.last_payment_date
FROM stores s
LEFT JOIN liability l ON l.franchise_id = s.agroex_franchise_id
LEFT JOIN payments  p ON p.franchise_id = s.agroex_franchise_id
WHERE l.franchise_id IS NOT NULL OR p.franchise_id IS NOT NULL   -- skip stores with no activity
ORDER BY outstanding DESC
