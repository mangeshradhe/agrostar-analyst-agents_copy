-- DVS first action: Partner vs Field Agent, week on week (by order creation week, IST, Monday start)
-- Built 2026-11-03. Window: orders created 2026-08-03 .. 2026-11-01 (13 full weeks).
--
-- WHO acted  = first Saathi-app click on the order (CleverTap button_clicked, page 'Online Order'):
--              accept 'Confirm' on WAITING_FOR_PARTNER_APPROVAL, or 'Available' / 'Out of Stock'.
--              Field Agent if event_props_fieldagentmobilenumber is filled, else Partner.
--              Use the DATALAKE table, not saathi_clevertap_views (view schema drops fieldagentmobilenumber).
-- WHAT it was = first result in orderhistorymeta:
--              PACKED                         -> 'Available'
--              ON_HOLD with source USER_ACTION -> 'Re-stock' (Out of Stock)
--              System holds (CRM_AGROEX_PUSH_SCRIPT, REROUTE_SCRIPT) are NOT partner actions.
-- Why not only the 'Available'/'Out of Stock' buttons: the accept screen is where items are marked;
--   PACKED lands a median ~41s after the accept Confirm. The separate buttons cover only ~14% of orders.
-- Segments: Rerouted = has a row in orderreroutinglogs; DVS = store assigned, no reroute log.

WITH rr AS (
  SELECT DISTINCT SAFE_CAST(order_id AS INT64) oid
  FROM `agrostar-data.prod_db_views.order_management_orderreroutinglogs`
  WHERE SAFE_CAST(order_id AS INT64) IS NOT NULL),
ord AS (
  SELECT o.sales_order_id oid,
         DATE_TRUNC(DATE(o.created_on,'Asia/Kolkata'), WEEK(MONDAY)) wk,
         IF(rr.oid IS NOT NULL,'Rerouted','DVS') seg
  FROM `agrostar-data.prod_db_views.order_management_order` o
  LEFT JOIN rr ON rr.oid = o.sales_order_id
  WHERE DATE(o.created_on,'Asia/Kolkata') BETWEEN '2026-08-03' AND '2026-11-01'
    AND LOWER(IFNULL(o.initiating_source,'')) NOT LIKE 'b2b%'        -- B2C only
    AND LOWER(IFNULL(o.order_type,'')) NOT LIKE '%offline%'          -- drop Instant Discount
    AND IFNULL(o.order_type,'') != 'COCO'                            -- drop COCO stores
    AND IFNULL(o.unicommerce_status,'') NOT IN ('FUTURE ORDER','DISPUTED_ADDRESS')
    AND o.status != 'MOB_APP_UNVERIFIED' AND o.status NOT LIKE 'edited%'
    AND IFNULL(o.unicommerce_status,'') NOT LIKE 'edited%'
    -- cancelled orders kept on purpose: we care who acted, not final outcome
    AND (IFNULL(o.retail_store_code,'') != '' OR rr.oid IS NOT NULL)),
h AS (  -- first real partner outcome
  SELECT order_id oid,
         ARRAY_AGG(IF(status='PACKED','AV','RS') ORDER BY created_on LIMIT 1)[OFFSET(0)] act
  FROM `agrostar-data.prod_db_views.order_management_orderhistorymeta`
  WHERE DATE(created_on) >= '2026-08-02'
    AND (status = 'PACKED' OR (status = 'ON_HOLD' AND source = 'USER_ACTION'))
  GROUP BY 1),
ev AS (  -- first app click on the order
  SELECT SAFE_CAST(event_props_orderid AS INT64) oid,
         ARRAY_AGG(IF(IFNULL(event_props_fieldagentmobilenumber,'') != '','FA','P')
                   ORDER BY clevertap_time_stamp LIMIT 1)[OFFSET(0)] who
  FROM `agrostar-data.saathi_clevertap_datalake.button_clicked`
  WHERE DATE(clevertap_time_stamp) >= '2026-08-02'
    AND event_props_page = 'Online Order'
    AND (event_props_type IN ('Available','Out of Stock')
         OR (event_props_type = 'Confirm' AND event_props_order_status = 'WAITING_FOR_PARTNER_APPROVAL'))
  GROUP BY 1)
SELECT o.seg, o.wk,
  COUNT(*) orders,
  COUNT(h.oid) acted,
  COUNTIF(h.oid IS NOT NULL AND e.who='P')  by_partner,
  COUNTIF(h.oid IS NOT NULL AND e.who='FA') by_field_agent,
  COUNTIF(h.oid IS NOT NULL AND e.oid IS NULL) untracked,   -- acted in backend, no CleverTap click
  COUNTIF(h.act='AV' AND e.who='P')  partner_available,
  COUNTIF(h.act='RS' AND e.who='P')  partner_restock,
  COUNTIF(h.act='AV' AND e.who='FA') fa_available,
  COUNTIF(h.act='RS' AND e.who='FA') fa_restock
FROM ord o
LEFT JOIN h ON h.oid = o.oid
LEFT JOIN ev e ON e.oid = o.oid
GROUP BY 1, 2
ORDER BY 1, 2;
