-- TO Request Approval/Rejection Analysis — reusable queries
-- Source: catalog_views.catalog_management_transferorders (CRM approval-funnel table)
-- See 08-to-request-approval-rejection-analysis.md for definitions/caveats.

-- Approved   = action_status = 'APPROVED'
-- Rejected   = transfer_status = 'REJECTED' AND action_by != 'SYSTEM'
--              (action_by = 'SYSTEM' = auto-archived/superseded by the recommendation engine,
--              not a real human decision -- EXCLUDE these rows entirely, confirmed 21 Sep 2026.
--              Filter: WHERE IFNULL(action_by,'') != 'SYSTEM' -- add to every query below.)
-- Pending    = everything else (WAITING_FOR_APPROVAL / PENDING / LOCKED, not yet actioned)

-- 1) Month x Type (UF / Budget / Adhoc)
WITH base AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', created_on) AS mth,
    CASE transfer_reason
      WHEN 'System TO - UF case' THEN 'UF'
      WHEN 'System TO - Budget balancing' THEN 'Budget'
      WHEN 'System TO - Adhoc' THEN 'Adhoc'
    END AS reason_bucket,
    CASE
      WHEN action_status = 'APPROVED' THEN 'Approved'
      WHEN transfer_status = 'REJECTED' THEN 'Rejected'
      ELSE 'Pending/Other'
    END AS status_bucket
  FROM `agrostar-data.catalog_views.catalog_management_transferorders`
  WHERE created_on >= '2026-01-01'
    AND transfer_reason IN ('System TO - UF case','System TO - Budget balancing','System TO - Adhoc')
    AND IFNULL(action_by,'') != 'SYSTEM'
)
SELECT mth, reason_bucket,
  COUNT(*) AS requested,
  COUNTIF(status_bucket='Approved') AS approved,
  COUNTIF(status_bucket='Rejected') AS rejected,
  COUNTIF(status_bucket='Pending/Other') AS pending_other,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Approved'),COUNT(*))*100,1) AS approval_pct,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Rejected'),COUNT(*))*100,1) AS rejection_pct
FROM base
GROUP BY 1,2
ORDER BY 1,2;

-- 2) Month x Geography tier (all types combined)
-- Geo tiers: Intra-FC = same city, Inter-City = same state/different city, Inter-State = different state
-- (from_facility/to_facility -> location_mst.location_id join, 100% match rate)
WITH base AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', t.created_on) AS mth,
    CASE
      WHEN UPPER(TRIM(fl.city)) = UPPER(TRIM(tl.city)) THEN 'Intra-FC'
      WHEN UPPER(TRIM(fl.state_name)) = UPPER(TRIM(tl.state_name)) THEN 'Inter-City'
      ELSE 'Inter-State'
    END AS geo_tier,
    CASE
      WHEN t.action_status = 'APPROVED' THEN 'Approved'
      WHEN t.transfer_status = 'REJECTED' THEN 'Rejected'
      ELSE 'Pending/Other'
    END AS status_bucket
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` t
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` fl ON UPPER(TRIM(t.from_facility)) = UPPER(TRIM(fl.location_id))
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` tl ON UPPER(TRIM(t.to_facility)) = UPPER(TRIM(tl.location_id))
  WHERE t.created_on >= '2026-01-01'
    AND t.transfer_reason IN ('System TO - UF case','System TO - Budget balancing','System TO - Adhoc')
    AND IFNULL(t.action_by,'') != 'SYSTEM'
)
SELECT mth, geo_tier,
  COUNT(*) AS requested,
  COUNTIF(status_bucket='Approved') AS approved,
  COUNTIF(status_bucket='Rejected') AS rejected,
  COUNTIF(status_bucket='Pending/Other') AS pending_other,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Approved'),COUNT(*))*100,1) AS approval_pct,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Rejected'),COUNT(*))*100,1) AS rejection_pct
FROM base
GROUP BY 1,2
ORDER BY 1,2;

-- 3) Full grain: Month x Type x Geo tier (pivot-ready flat table; includes Adhoc)
WITH base AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', t.created_on) AS mth,
    CASE t.transfer_reason
      WHEN 'System TO - UF case' THEN 'UF'
      WHEN 'System TO - Budget balancing' THEN 'Budget'
      WHEN 'System TO - Adhoc' THEN 'Adhoc'
    END AS reason_bucket,
    CASE
      WHEN UPPER(TRIM(fl.city)) = UPPER(TRIM(tl.city)) THEN 'Intra-FC'
      WHEN UPPER(TRIM(fl.state_name)) = UPPER(TRIM(tl.state_name)) THEN 'Inter-City'
      ELSE 'Inter-State'
    END AS geo_tier,
    CASE
      WHEN t.action_status = 'APPROVED' THEN 'Approved'
      WHEN t.transfer_status = 'REJECTED' THEN 'Rejected'
      ELSE 'Pending/Other'
    END AS status_bucket
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` t
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` fl ON UPPER(TRIM(t.from_facility)) = UPPER(TRIM(fl.location_id))
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` tl ON UPPER(TRIM(t.to_facility)) = UPPER(TRIM(tl.location_id))
  WHERE t.created_on >= '2026-01-01'
    AND t.transfer_reason IN ('System TO - UF case','System TO - Budget balancing','System TO - Adhoc')
    AND IFNULL(t.action_by,'') != 'SYSTEM'
)
SELECT mth, reason_bucket, geo_tier,
  COUNT(*) AS requested,
  COUNTIF(status_bucket='Approved') AS approved,
  COUNTIF(status_bucket='Rejected') AS rejected,
  COUNTIF(status_bucket='Pending/Other') AS pending_other,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Approved'),COUNT(*))*100,1) AS approval_pct,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Rejected'),COUNT(*))*100,1) AS rejection_pct
FROM base
GROUP BY 1,2,3
-- for the "Type -> Tier -> Month" trend view (Adhoc excluded), add:
-- WHERE reason_bucket != 'Adhoc'
-- ORDER BY reason_bucket, geo_tier, mth
ORDER BY 1,2,3;

-- 4) Same as (3) but pre-grouped Type -> Tier -> Month (Adhoc excluded) -- the "one-go trend" cut
WITH base AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', t.created_on) AS mth,
    CASE t.transfer_reason
      WHEN 'System TO - UF case' THEN 'UF'
      WHEN 'System TO - Budget balancing' THEN 'Budget'
    END AS reason_bucket,
    CASE
      WHEN UPPER(TRIM(fl.city)) = UPPER(TRIM(tl.city)) THEN 'Intra-FC'
      WHEN UPPER(TRIM(fl.state_name)) = UPPER(TRIM(tl.state_name)) THEN 'Inter-City'
      ELSE 'Inter-State'
    END AS geo_tier,
    CASE
      WHEN t.action_status = 'APPROVED' THEN 'Approved'
      WHEN t.transfer_status = 'REJECTED' THEN 'Rejected'
      ELSE 'Pending/Other'
    END AS status_bucket
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` t
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` fl ON UPPER(TRIM(t.from_facility)) = UPPER(TRIM(fl.location_id))
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` tl ON UPPER(TRIM(t.to_facility)) = UPPER(TRIM(tl.location_id))
  WHERE t.created_on >= '2026-01-01'
    AND t.transfer_reason IN ('System TO - UF case','System TO - Budget balancing')
    AND IFNULL(t.action_by,'') != 'SYSTEM'
)
SELECT reason_bucket, geo_tier, mth,
  COUNT(*) AS requested,
  COUNTIF(status_bucket='Approved') AS approved,
  COUNTIF(status_bucket='Rejected') AS rejected,
  COUNTIF(status_bucket='Pending/Other') AS pending_other,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Approved'),COUNT(*))*100,1) AS approval_pct,
  ROUND(SAFE_DIVIDE(COUNTIF(status_bucket='Rejected'),COUNT(*))*100,1) AS rejection_pct
FROM base
GROUP BY 1,2,3
ORDER BY 1,2,3;

-- 5) Time-to-action (turnaround), APPROVED requests only (Rejected excluded on request, 21 Sep)
-- delta_hrs = action_on - created_on, in hours. Checked: no NULL action_on / no negative deltas
-- for action_status IN ('APPROVED','REJECTED') in this scope -- clean, no extra filtering needed.
WITH base AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', t.created_on) AS mth,
    CASE t.transfer_reason
      WHEN 'System TO - UF case' THEN 'UF'
      WHEN 'System TO - Budget balancing' THEN 'Budget'
    END AS reason_bucket,
    CASE
      WHEN UPPER(TRIM(fl.city)) = UPPER(TRIM(tl.city)) THEN 'Intra-FC'
      WHEN UPPER(TRIM(fl.state_name)) = UPPER(TRIM(tl.state_name)) THEN 'Inter-City'
      ELSE 'Inter-State'
    END AS geo_tier,
    TIMESTAMP_DIFF(t.action_on, t.created_on, MINUTE)/60.0 AS delta_hrs
  FROM `agrostar-data.catalog_views.catalog_management_transferorders` t
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` fl ON UPPER(TRIM(t.from_facility)) = UPPER(TRIM(fl.location_id))
  LEFT JOIN `agrostar-data.pristine_wms_prod_db.location_mst` tl ON UPPER(TRIM(t.to_facility)) = UPPER(TRIM(tl.location_id))
  WHERE t.created_on >= '2026-01-01'
    AND t.transfer_reason IN ('System TO - UF case','System TO - Budget balancing')
    AND IFNULL(t.action_by,'') != 'SYSTEM'
    AND t.action_status = 'APPROVED'
)
SELECT
  reason_bucket, geo_tier, mth,
  COUNT(*) AS n_approved,
  ROUND(APPROX_QUANTILES(delta_hrs,100)[OFFSET(50)],1) AS median_hrs,
  ROUND(APPROX_QUANTILES(delta_hrs,100)[OFFSET(90)],1) AS p90_hrs,
  ROUND(APPROX_QUANTILES(delta_hrs,100)[OFFSET(95)],1) AS p95_hrs,
  ROUND(AVG(delta_hrs),1) AS mean_hrs,
  ROUND(COUNTIF(delta_hrs<=1)/COUNT(*)*100,1) AS pct_1h,
  ROUND(COUNTIF(delta_hrs<=2)/COUNT(*)*100,1) AS pct_2h,
  ROUND(COUNTIF(delta_hrs<=3)/COUNT(*)*100,1) AS pct_3h,
  ROUND(COUNTIF(delta_hrs<=4)/COUNT(*)*100,1) AS pct_4h,
  ROUND(COUNTIF(delta_hrs<=5)/COUNT(*)*100,1) AS pct_5h,
  ROUND(COUNTIF(delta_hrs<=6)/COUNT(*)*100,1) AS pct_6h,
  ROUND(COUNTIF(delta_hrs<=7)/COUNT(*)*100,1) AS pct_7h,
  ROUND(COUNTIF(delta_hrs<=8)/COUNT(*)*100,1) AS pct_8h,
  ROUND(COUNTIF(delta_hrs<=9)/COUNT(*)*100,1) AS pct_9h,
  ROUND(COUNTIF(delta_hrs<=12)/COUNT(*)*100,1) AS pct_12h,
  ROUND(COUNTIF(delta_hrs<=24)/COUNT(*)*100,1) AS pct_24h,
  ROUND(COUNTIF(delta_hrs<=48)/COUNT(*)*100,1) AS pct_48h,
  ROUND(COUNTIF(delta_hrs<=72)/COUNT(*)*100,1) AS pct_72h,
  ROUND(COUNTIF(delta_hrs<=96)/COUNT(*)*100,1) AS pct_96h,
  ROUND(COUNTIF(delta_hrs<=120)/COUNT(*)*100,1) AS pct_120h
FROM base
GROUP BY 1,2,3
ORDER BY 1,2,3;
