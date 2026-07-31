-- ══════════════════════════════════════════════════════════════════════════════
-- B2C Order-Based Farmer Serviceability — 3-Tier VM resolution + active LMD coverage
-- See b2c_order_serviceability_notes.md in this folder for full explanation,
-- results snapshot, and known data caveats.
--
-- Differs from serviceability_query.sql (the cohort-based version) in one key way:
-- the farmer universe here comes from ACTUAL B2C ORDERS in order_management_order
-- (Apr 2023 -> date), not from farmer_profile_master. Use this version when the
-- question is "of farmers who actually transacted B2C in this window, how many
-- are serviceable" rather than "of this registration/first-txn cohort...".
--
-- CHANGE THESE VALUES
-- date range: currently '2023-04-01' to CURRENT_DATE — edit both bounds as needed
-- order_type exclusions: COCO / STOCK_TRANSFER_ORDER / RETURN_ORDER / OFFLINE-ORDER
--   are excluded as "not a genuine customer order". These didn't exist at all in
--   this order set before FY2027 (Apr 2026) — see notes file for the FY-by-FY impact.
-- ══════════════════════════════════════════════════════════════════════════════
WITH b2c_orders AS (
  SELECT o.sales_order_id, o.shipping_address_id, o.grand_total
  FROM `agrostar-data.prod_db_views.order_management_order` o
  WHERE DATE(o.created_on) BETWEEN '2023-04-01' AND CURRENT_DATE('Asia/Kolkata')
    AND LOWER(o.initiating_source) NOT LIKE 'b2b%'                              -- B2C only (per dvs-analyst.md skill)
    AND o.unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
    AND o.status NOT IN ('MOB_APP_UNVERIFIED')
    AND o.status NOT LIKE 'edited%'
    AND o.unicommerce_status NOT LIKE 'edited%'
    AND o.shipping_address_id IS NOT NULL
    AND COALESCE(o.order_type,'') NOT IN ('COCO','STOCK_TRANSFER_ORDER','RETURN_ORDER','OFFLINE-ORDER')
),

-- ── Farmer universe + address pool from actual orders ──────────────────────────
-- One row per (order, resolved address). farmer_id comes via shipping_address_id,
-- NOT farmer_profile_master — this is deliberately "who actually ordered", not
-- "who is registered/first-transacted".
farmer_orders AS (
  SELECT
    bo.sales_order_id, bo.grand_total, sa.farmer_id,
    LOWER(TRIM(sa.village)) AS village_raw,
    LOWER(TRIM(sa.taluka)) AS taluka_raw,
    LOWER(TRIM(sa.district)) AS district_raw,
    LOWER(TRIM(sa.pin_code)) AS pincode_raw,
    CASE
      WHEN LOWER(TRIM(sa.state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(sa.state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(sa.state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(sa.state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(sa.state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(sa.state))
    END AS state_norm
  FROM b2c_orders bo
  JOIN `agrostar-data.prod_db_views.csr_shippingaddress` sa ON sa.id = bo.shipping_address_id
  WHERE sa.farmer_id IS NOT NULL
),
farmer_orders_5states AS (
  SELECT * FROM farmer_orders
  WHERE state_norm IN ('gujarat','maharashtra','rajasthan','madhya pradesh','uttar pradesh')
),
farmer_gmv AS (
  SELECT farmer_id, SUM(grand_total) AS total_gmv
  FROM farmer_orders_5states
  GROUP BY farmer_id
),
all_addresses AS (
  SELECT DISTINCT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm
  FROM farmer_orders_5states
),

-- ── Village master ─────────────────────────────────────────────────────────────
vm AS (
  SELECT
    id, LOWER(TRIM(village)) AS village_norm, LOWER(TRIM(taluka)) AS taluka_norm,
    LOWER(TRIM(district)) AS district_norm, LOWER(TRIM(pin_code)) AS pincode_norm,
    CASE
      WHEN LOWER(TRIM(state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(state))
    END AS state_norm,
    is_archived, replaced_by_id
  FROM `agrostar-data.static_tables_views.csr_villageaddress`
),

-- ── Tier 1: 5-field match ──────────────────────────────────────────────────────
tier1_match AS (
  SELECT * FROM (
    SELECT
      a.farmer_id, a.village_raw, a.taluka_raw, a.district_raw, a.pincode_raw, a.state_norm,
      v.id AS vm_id, v.is_archived, v.replaced_by_id,
      ROW_NUMBER() OVER (
        PARTITION BY a.farmer_id, a.village_raw, a.taluka_raw, a.district_raw, a.pincode_raw, a.state_norm
        ORDER BY
          CASE WHEN v.is_archived = 0 THEN 1
               WHEN v.is_archived = 1 AND v.replaced_by_id IS NOT NULL THEN 2
               ELSE 3 END ASC,
          v.id ASC NULLS LAST
      ) AS rn
    FROM all_addresses a
    LEFT JOIN vm v
      ON v.village_norm = a.village_raw AND v.taluka_norm = a.taluka_raw
      AND v.district_norm = a.district_raw AND v.pincode_norm = a.pincode_raw
      AND v.state_norm = a.state_norm
  ) WHERE rn = 1
),
tier1_resolved AS (
  SELECT
    t1.farmer_id, t1.village_raw, t1.taluka_raw, t1.district_raw, t1.pincode_raw, t1.state_norm,
    1 AS tier,
    CASE WHEN t1.vm_id IS NULL THEN FALSE
         WHEN t1.is_archived = 0 THEN TRUE
         WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN TRUE
         ELSE FALSE END AS is_resolved,
    CASE WHEN t1.vm_id IS NULL THEN NULL
         WHEN t1.is_archived = 0 THEN vm1.village_norm
         WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN vm2.village_norm
         ELSE NULL END AS canonical_village,
    CASE WHEN t1.vm_id IS NULL THEN NULL
         WHEN t1.is_archived = 0 THEN vm1.pincode_norm
         WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN vm2.pincode_norm
         ELSE NULL END AS canonical_pincode
  FROM tier1_match t1
  LEFT JOIN vm vm1 ON vm1.id = t1.vm_id AND t1.is_archived = 0
  LEFT JOIN vm vm2 ON vm2.id = t1.replaced_by_id
),

-- ── Tier 2: 4-field match, no pincode ──────────────────────────────────────────
tier2_candidates AS (
  SELECT DISTINCT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm
  FROM tier1_resolved WHERE is_resolved = FALSE
),
tier2_match AS (
  SELECT
    a.farmer_id, a.village_raw, a.taluka_raw, a.district_raw, a.pincode_raw, a.state_norm,
    v.id AS vm_id, v.is_archived, v.replaced_by_id
  FROM tier2_candidates a
  JOIN vm v
    ON v.village_norm = a.village_raw AND v.taluka_norm = a.taluka_raw
    AND v.district_norm = a.district_raw AND v.state_norm = a.state_norm
  WHERE (v.is_archived = 0 OR (v.is_archived = 1 AND v.replaced_by_id IS NOT NULL))
),
tier2_resolved AS (
  SELECT
    t2.farmer_id, t2.village_raw, t2.taluka_raw, t2.district_raw, t2.pincode_raw, t2.state_norm,
    2 AS tier, TRUE AS is_resolved,
    CASE WHEN t2.is_archived = 0 THEN vm1.village_norm ELSE vm2.village_norm END AS canonical_village,
    CASE WHEN t2.is_archived = 0 THEN vm1.pincode_norm ELSE vm2.pincode_norm END AS canonical_pincode
  FROM tier2_match t2
  LEFT JOIN vm vm1 ON vm1.id = t2.vm_id AND t2.is_archived = 0
  LEFT JOIN vm vm2 ON vm2.id = t2.replaced_by_id AND t2.is_archived = 1
),

-- ── Tier 3: raw address fallback ───────────────────────────────────────────────
tier3_candidates AS (
  SELECT DISTINCT tc.farmer_id, tc.village_raw, tc.taluka_raw, tc.district_raw, tc.pincode_raw, tc.state_norm
  FROM tier2_candidates tc
  LEFT JOIN tier2_resolved t2r
    ON t2r.farmer_id = tc.farmer_id AND t2r.village_raw = tc.village_raw
    AND t2r.taluka_raw = tc.taluka_raw AND t2r.district_raw = tc.district_raw AND t2r.pincode_raw = tc.pincode_raw
  WHERE t2r.farmer_id IS NULL
),
tier3_resolved AS (
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm,
    3 AS tier, FALSE AS is_resolved, village_raw AS canonical_village, pincode_raw AS canonical_pincode
  FROM tier3_candidates
),

-- ── Merge all tiers ─────────────────────────────────────────────────────────────
all_resolved AS (
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier1_resolved WHERE is_resolved = TRUE
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier2_resolved
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier3_resolved
),

-- ── Active LMD coverage ───────────────────────────────────────────────────────
svc_coverage AS (
  SELECT DISTINCT
    dc.coverage_type,
    LOWER(TRIM(dc.village)) AS village_norm,
    LOWER(TRIM(dc.pincode)) AS pincode_norm,
    CASE
      WHEN LOWER(TRIM(da.state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(da.state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(da.state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(da.state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(da.state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(da.state))
    END AS state_norm,
    LOWER(TRIM(da.district)) AS district_norm,
    LOWER(TRIM(da.taluka)) AS taluka_norm
  FROM `agrostar-data.prod_agroex_db_views.assignment_deliverycoverage` dc
  JOIN `agrostar-data.prod_agroex_db_views.assignment_deliveryarea` da ON da.id = dc.delivery_area_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocationfranchisemapping` apl ON apl.id = da.pickuplocation_franchise_mapping_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_franchise` asf ON asf.id = apl.franchise_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocation` pl ON pl.id = apl.pickuplocation_id
  WHERE da.is_active = 1 AND dc.is_active = 1 AND asf.is_active = 1 AND pl.is_active = 1
),
coverage_hits AS (
  SELECT DISTINCT ar.farmer_id
  FROM all_resolved ar
  JOIN svc_coverage sv ON sv.coverage_type = 'village' AND sv.village_norm = ar.canonical_village AND sv.pincode_norm = ar.canonical_pincode
  WHERE ar.canonical_village IS NOT NULL
    AND ar.canonical_village NOT IN ('', 'na', 'n/a', 'nil', 'none', 'unknown', 'not available')
    AND LENGTH(ar.canonical_village) >= 2
  UNION DISTINCT
  SELECT DISTINCT ar.farmer_id
  FROM all_resolved ar
  JOIN svc_coverage sv ON sv.coverage_type = 'taluka' AND sv.state_norm = ar.state_norm AND sv.district_norm = ar.district_raw AND sv.taluka_norm = ar.taluka_raw
  UNION DISTINCT
  SELECT DISTINCT ar.farmer_id
  FROM all_resolved ar
  JOIN svc_coverage sv ON sv.coverage_type = 'pincode' AND sv.state_norm = ar.state_norm AND sv.pincode_norm = ar.canonical_pincode
),

-- ── Representative state per farmer (best-tier address wins) ──────────────────
farmer_state AS (
  SELECT farmer_id, state_norm FROM (
    SELECT farmer_id, state_norm,
      ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY tier ASC, state_norm ASC) AS rn
    FROM all_resolved
  ) WHERE rn = 1
),

-- ── 3-bucket serviceability per farmer (lifetime, across all their addresses) ──
farmer_bucket AS (
  SELECT
    ar.farmer_id,
    CASE
      WHEN MAX(CASE WHEN ch.farmer_id IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'Serviceable'
      WHEN MAX(CASE WHEN ar.tier IN (1,2) THEN 1 ELSE 0 END) = 1 THEN 'Non Serviceable'
      ELSE 'Address Problem'
    END AS serviceability_bucket
  FROM all_resolved ar
  LEFT JOIN coverage_hits ch ON ch.farmer_id = ar.farmer_id
  GROUP BY ar.farmer_id
)

-- ── Final output: one row per farmer ───────────────────────────────────────────
SELECT
  fs.state_norm AS state,
  fb.serviceability_bucket,
  fb.farmer_id,
  fg.total_gmv
FROM farmer_bucket fb
JOIN farmer_state fs ON fs.farmer_id = fb.farmer_id
JOIN farmer_gmv fg ON fg.farmer_id = fb.farmer_id
ORDER BY fs.state_norm, fb.serviceability_bucket, fb.farmer_id

-- Aggregated summary instead of farmer-level rows:
-- SELECT fs.state_norm AS state, fb.serviceability_bucket,
--   COUNT(DISTINCT fb.farmer_id) AS farmer_count, ROUND(SUM(fg.total_gmv), 0) AS gmv
-- FROM farmer_bucket fb
-- JOIN farmer_state fs ON fs.farmer_id = fb.farmer_id
-- JOIN farmer_gmv fg ON fg.farmer_id = fb.farmer_id
-- GROUP BY 1, 2
