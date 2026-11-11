-- Status Mismatch Alert — CRM vs WMS vs AgroEx (sale orders only)
-- Window: orders placed in the last 30 days, excluding today (IST).
--   FC   orders -> WMS + CRM + AgroEx
--   DVS  orders (order_type = 'STORE-ORDER') -> CRM + AgroEx
--   COCO orders (order_type = 'COCO')        -> CRM + WMS + AgroEx
-- Excluded: IPT_FORWARD_ORDER, IPT_RETURN_ORDER, STOCK_TRANSFER_ORDER, RETURN_ORDER, OFFLINE-ORDER
-- Sources: CRM = prod_db_views.order_management_order.status
--          AgroEx = prod_db_views.delivery_shippingpackage.delivery_status (order_id = unicommerce_id)
--          WMS = pristine_wms_prod_db.sale_order_header.order_status (DisplayOrderCode = unicommerce_id, "_1" split suffix stripped)
-- Every status is mapped to a common stage, and an order is flagged only when the systems sit at different stages:
--   1 NEW · 2 IN WAREHOUSE · 3 PACKED/RTS · 4 WITH LMD · 5 DELIVERED · 6 RETURN IN PROGRESS · 7 RETURNED · 9 CANCELLED
-- Expected lags are NOT flagged: WMS staying at DISPATCHED after delivery, CRM not tracking picking,
-- AgroEx cancellation_requested, same-stage sub-status differences (e.g. PICKED_BY_LMD vs hold),
-- and CRM RETURNED with WMS CANCELLED (AgroEx must still agree with CRM).
WITH o AS (
  SELECT sales_order_id, unicommerce_id, TRIM(status) AS crm_status, order_type, warehouse,
    DATETIME(created_on, 'Asia/Kolkata') AS created_ist,
    CASE WHEN order_type = 'COCO' THEN 'COCO' WHEN order_type = 'STORE-ORDER' THEN 'DVS' ELSE 'FC' END AS seg
  FROM `agrostar-data.prod_db_views.order_management_order`
  WHERE DATE(created_on, 'Asia/Kolkata') BETWEEN DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
                                             AND DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 1 DAY)
    AND COALESCE(order_type, '') NOT IN ('IPT_FORWARD_ORDER', 'IPT_RETURN_ORDER', 'STOCK_TRANSFER_ORDER', 'RETURN_ORDER', 'OFFLINE-ORDER')
),
o2 AS (
  SELECT o.*,
    CASE
      WHEN crm_status = 'CANCELLED' OR crm_status LIKE 'EDITED-%' THEN 9
      WHEN crm_status IN ('CREATED', 'WAITING_FOR_PARTNER_APPROVAL', 'ON_HOLD_FULFILLABLE', 'CONFIRM_ON_HOLD', 'ERROR',
                          'MOB_APP_UNVERIFIED', 'FUTURE ORDER', 'PENDING', 'PAYMENT FAILED', 'ONLINE_PAYMENT_PENDING')
           OR crm_status LIKE 'WAITING%' THEN 1
      WHEN crm_status IN ('PICKING', 'PICKED') THEN 2
      WHEN crm_status IN ('READY_TO_SHIP', 'PACKED', 'BILTY_UPLOAD_PENDING') THEN 3
      WHEN crm_status IN ('DISPATCHED', 'PICKED_BY_LMD', 'HOLD_BY_LMD') THEN 4
      WHEN crm_status IN ('DELIVERED', 'DELIVERED_AT_GODOWN', 'RECEIVED') THEN 5
      WHEN crm_status IN ('RETURN_ACKNOWLEDGED', 'RETURN_IN_TRANSIT', 'RETURNED_BY_LMD') THEN 6
      WHEN crm_status IN ('RETURNED', 'STORE_RETURN_ACKNOWLEDGED') THEN 7
      ELSE 0
    END AS crm_stage
  FROM o
),
pk AS (
  SELECT p.order_id, p.delivery_status,
    CASE
      WHEN p.delivery_status = 'archive' THEN 9
      WHEN p.delivery_status = 'hold_by_saathi' THEN 1
      WHEN p.delivery_status IN ('created', 'to_be_created') THEN IF(o2.seg = 'FC', 3, 1)
      WHEN p.delivery_status IN ('package_manifest_created', 'verified_by_ops', 'bilty_upload_pending') THEN 3
      WHEN p.delivery_status = 'in_transit' THEN IF(o2.seg = 'DVS', 3, 4)
      WHEN p.delivery_status IN ('verified', 'hold', 'return_requested', 'process_for_return', 'missing',
                                 'missing_in_transit', 'interfc_in_transit') THEN 4
      WHEN p.delivery_status = 'cancellation_requested' THEN 8
      WHEN p.delivery_status IN ('delivered', 'delivered_at_godown', 'received') THEN 5
      WHEN p.delivery_status IN ('return_in_transit', 'return_manifest_created', 'returned_by_lmd', 'missing_in_returns',
                                 'missing_in_return_intransit', 'misplaced_in_returns_by_lp') THEN 6
      WHEN p.delivery_status IN ('returned', 'returned_in_returnfc') THEN 7
      ELSE 0
    END AS stg
  FROM `agrostar-data.prod_db_views.delivery_shippingpackage` p
  JOIN o2 ON p.order_id = CAST(o2.unicommerce_id AS STRING)
  WHERE DATE(p.order_placed_date) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 37 DAY)
),
ax AS (  -- order-level AgroEx view; archived packages ignored when live ones exist
  SELECT order_id,
    STRING_AGG(DISTINCT delivery_status ORDER BY delivery_status) AS agroex_status,
    COUNTIF(stg <> 9) AS live_pk,
    MIN(IF(stg <> 9, stg, NULL)) AS ax_min,
    MAX(IF(stg <> 9, stg, NULL)) AS ax_max,
    MAX(IF(stg NOT IN (8, 9), stg, NULL)) AS ax_hi,
    LOGICAL_OR(stg = 8) AS ax_cancel_req
  FROM pk GROUP BY 1
),
w AS (
  SELECT uc,
    STRING_AGG(DISTINCT order_status ORDER BY order_status) AS wms_status,
    STRING_AGG(DISTINCT FacilityCode) AS facility,
    MIN(w_stg) AS w_min, MAX(w_stg) AS w_max, MAX(IF(w_stg <> 9, w_stg, NULL)) AS w_hi
  FROM (
    SELECT SAFE_CAST(SPLIT(DisplayOrderCode, '_')[OFFSET(0)] AS INT64) AS uc, order_status, FacilityCode,
      CASE order_status
        WHEN 'PENDING' THEN 1 WHEN 'PROCESSING' THEN 1 WHEN 'PICK CREATED' THEN 2 WHEN 'PICKED' THEN 2
        WHEN 'INVOICED' THEN 3 WHEN 'READY TO SHIP' THEN 3 WHEN 'DISPATCHED' THEN 4 WHEN 'DELIVERED' THEN 5
        WHEN 'RTO CREATED' THEN 6 WHEN 'RETURNED' THEN 7 WHEN 'CANCELLED' THEN 9 END AS w_stg
    FROM `agrostar-data.pristine_wms_prod_db.sale_order_header`
    WHERE DATE(created_on, 'Asia/Kolkata') >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 37 DAY)
  )
  GROUP BY uc
),
j AS (
  SELECT o2.*, ax.agroex_status, ax.live_pk, ax.ax_min, ax.ax_max, ax.ax_hi, ax.ax_cancel_req,
         w.wms_status, w.facility, w.w_min, w.w_max, w.w_hi
  FROM o2
  LEFT JOIN ax ON ax.order_id = CAST(o2.unicommerce_id AS STRING)
  LEFT JOIN w ON w.uc = o2.unicommerce_id
),
chk AS (
  SELECT j.*,
    -- CRM vs AgroEx (all segments)
    CASE
      WHEN crm_stage = 0 THEN FALSE
      WHEN agroex_status IS NULL THEN crm_stage IN (1, 2, 9)
      WHEN live_pk = 0 THEN crm_stage IN (1, 9)
      WHEN ax_cancel_req AND crm_stage IN (3, 4, 9) THEN TRUE
      WHEN crm_stage = 9 THEN FALSE
      WHEN crm_stage = 2 AND ax_min <= 3 THEN TRUE
      WHEN crm_stage BETWEEN ax_min AND ax_max THEN TRUE
      WHEN crm_stage = 4 AND ax_min = 6 THEN TRUE          -- CRM stays DISPATCHED until return acknowledged
      ELSE FALSE
    END AS crm_ax_ok,
    -- CRM vs WMS (FC + COCO only)
    CASE
      WHEN seg = 'DVS' THEN TRUE
      WHEN wms_status IS NULL THEN crm_stage IN (1, 9)
      WHEN crm_stage = 9 THEN w_max = 9
      WHEN crm_stage = 7 AND w_min = 9 THEN TRUE          -- CRM RETURNED + WMS CANCELLED: skipped by user (2026-11-11)
      WHEN w_min = 9 THEN FALSE
      WHEN crm_stage IN (1, 2) AND w_max <= 2 THEN TRUE   -- CRM does not track picking
      WHEN crm_stage BETWEEN w_min AND w_max THEN TRUE
      WHEN w_max = 4 AND crm_stage IN (5, 6) THEN TRUE    -- WMS stops at DISPATCHED
      ELSE FALSE
    END AS crm_wms_ok
  FROM j
),
flagged AS (
  SELECT chk.*,
    CASE
      WHEN crm_stage = 0 THEN '9. Unknown CRM status'
      WHEN crm_stage BETWEEN 1 AND 4 AND (live_pk = 0 OR (seg <> 'DVS' AND w_min = 9))
        THEN '1. Cancelled in WMS/AgroEx, still live in CRM'
      WHEN crm_stage = 9 THEN '2. Cancelled in CRM, still live in WMS/AgroEx'
      WHEN seg <> 'DVS' AND w_max = 7 AND crm_stage < 7 THEN '3. Returned in WMS, CRM not updated'
      WHEN agroex_status IS NULL AND crm_stage >= 3 THEN '4. No AgroEx package'
      WHEN seg <> 'DVS' AND wms_status IS NULL THEN '5. Not in WMS'
      WHEN crm_stage < GREATEST(COALESCE(ax_hi, 0), IF(seg = 'DVS', 0, COALESCE(w_hi, 0)))
        THEN '6. CRM behind (WMS/AgroEx moved ahead)'
      WHEN seg <> 'DVS' AND w_hi < crm_stage AND crm_stage = 7 THEN '8. Returned in CRM, WMS return not received'
      WHEN seg <> 'DVS' AND w_hi < crm_stage THEN '7. WMS behind'
      WHEN ax_hi < crm_stage THEN '7. AgroEx behind'
      ELSE '9. Other mismatch'
    END AS issue
  FROM chk
  WHERE NOT (crm_ax_ok AND crm_wms_ok)
)
SELECT issue, seg,
  CAST(unicommerce_id AS STRING) AS order_id,
  sales_order_id,
  COALESCE(facility, warehouse) AS facility,
  crm_status,
  COALESCE(agroex_status, '—') AS agroex_status,
  IF(seg = 'DVS', 'n/a', COALESCE(wms_status, '—')) AS wms_status,
  created_ist,
  DATE_DIFF(CURRENT_DATE('Asia/Kolkata'), DATE(created_ist), DAY) AS age_days
FROM flagged
ORDER BY issue, age_days DESC, order_id
