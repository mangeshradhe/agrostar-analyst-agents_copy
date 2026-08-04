-- ══════════════════════════════════════════════════════════════════════════════
-- Address Problem Farmers — Full Export, all 5 states (B2C order-based universe)
--
-- Reproduces the "Address Problem" bucket from b2c_order_serviceability_query.sql
-- (farmer universe = actual B2C orders, Apr 2023 -> date, not farmer_profile_master)
-- then attaches:
--   - EVERY distinct raw address per farmer (original casing preserved) — a
--     farmer with 3 different address-problem addresses gets 3 rows
--   - best-available GPS: each farmer's most-frequent rounded coordinate from
--     clevertap_views.app_launched (the "home point" approach from the notes),
--     NULL if they have no valid app-launch GPS at all (same GPS repeats
--     across a farmer's multiple address rows — it's farmer-level, not
--     address-level)
--
-- One row per farmer PER DISTINCT ADDRESS. Farmers with multiple problem
-- addresses appear multiple times, once per address.
-- ══════════════════════════════════════════════════════════════════════════════
WITH b2c_orders AS (
  SELECT o.sales_order_id, o.shipping_address_id, o.created_on
  FROM `agrostar-data.prod_db_views.order_management_order` o
  WHERE DATE(o.created_on) BETWEEN '2023-04-01' AND CURRENT_DATE('Asia/Kolkata')
    AND LOWER(o.initiating_source) NOT LIKE 'b2b%'                              -- B2C only
    AND o.unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
    AND o.status NOT IN ('MOB_APP_UNVERIFIED')
    AND o.status NOT LIKE 'edited%'
    AND o.unicommerce_status NOT LIKE 'edited%'
    AND o.shipping_address_id IS NOT NULL
    AND COALESCE(o.order_type,'') NOT IN ('COCO','STOCK_TRANSFER_ORDER','RETURN_ORDER','OFFLINE-ORDER')
),

-- ── Farmer universe + address pool (normalized, for matching) ──────────────────
farmer_orders AS (
  SELECT
    bo.sales_order_id, bo.created_on, sa.farmer_id,
    sa.village AS village_disp, sa.taluka AS taluka_disp,
    sa.district AS district_disp, sa.pin_code AS pincode_disp, sa.state AS state_disp,
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
all_addresses AS (
  SELECT DISTINCT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm
  FROM farmer_orders_5states
),

-- ── Village master ───────────────────────────────────────────────────────────
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

-- ── Tier 1: 5-field match ───────────────────────────────────────────────────
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

-- ── Tier 2: 4-field match, no pincode ───────────────────────────────────────
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

-- ── Tier 3: raw address fallback ─────────────────────────────────────────────
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

all_resolved AS (
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier1_resolved WHERE is_resolved = TRUE
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier2_resolved
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode FROM tier3_resolved
),

-- ── Active LMD coverage ─────────────────────────────────────────────────────
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

-- ── Representative state per farmer (best-tier address wins) ───────────────
farmer_state AS (
  SELECT farmer_id, state_norm FROM (
    SELECT farmer_id, state_norm,
      ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY tier ASC, state_norm ASC) AS rn
    FROM all_resolved
  ) WHERE rn = 1
),

-- ── 3-bucket serviceability per farmer (lifetime, across all their addresses) ─
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
),

-- ── Target list: only Address Problem farmers ───────────────────────────────
address_problem_farmers AS (
  SELECT fb.farmer_id, fs.state_norm
  FROM farmer_bucket fb
  JOIN farmer_state fs ON fs.farmer_id = fb.farmer_id
  WHERE fb.serviceability_bucket = 'Address Problem'
),

-- ── ALL distinct raw addresses per farmer (one row per unique address, not per farmer) ─
-- A farmer with 3 different address-problem addresses gets 3 rows here.
rep_address AS (
  SELECT farmer_id, village_disp, taluka_disp, district_disp, pincode_disp, state_disp
  FROM (
    SELECT fo.*,
      ROW_NUMBER() OVER (
        PARTITION BY fo.farmer_id, fo.village_raw, fo.taluka_raw, fo.district_raw, fo.pincode_raw, fo.state_norm
        ORDER BY fo.created_on DESC
      ) AS rn
    FROM farmer_orders_5states fo
    JOIN address_problem_farmers apf ON apf.farmer_id = fo.farmer_id
  ) WHERE rn = 1
),

-- ── Best-available GPS: most-frequent rounded coordinate per farmer ─────────
gps_candidates AS (
  SELECT
    SAFE_CAST(al.farmer_id AS INT64) AS farmer_id,          -- app_launched.farmer_id is STRING despite schema
    ROUND(SAFE_CAST(al.latitude AS FLOAT64), 4) AS lat_r,
    ROUND(SAFE_CAST(al.longitude AS FLOAT64), 4) AS lon_r
  FROM `agrostar-data.clevertap_views.app_launched` al
  JOIN address_problem_farmers apf ON apf.farmer_id = SAFE_CAST(al.farmer_id AS INT64)
  WHERE SAFE_CAST(al.latitude AS FLOAT64) IS NOT NULL AND SAFE_CAST(al.longitude AS FLOAT64) IS NOT NULL
    AND SAFE_CAST(al.latitude AS FLOAT64) != 0 AND SAFE_CAST(al.longitude AS FLOAT64) != 0
),
gps_home_point AS (
  SELECT farmer_id, lat_r AS latitude, lon_r AS longitude FROM (
    SELECT farmer_id, lat_r, lon_r,
      COUNT(*) AS hits,
      ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY COUNT(*) DESC) AS rn
    FROM gps_candidates
    GROUP BY farmer_id, lat_r, lon_r
  ) WHERE rn = 1
)

-- ── Final output ──────────────────────────────────────────────────────────────
SELECT
  apf.farmer_id,
  ra.village_disp   AS village,
  ra.taluka_disp    AS taluka,
  ra.district_disp  AS district,
  ra.state_disp     AS state,
  ra.pincode_disp   AS pincode,
  gp.latitude,
  gp.longitude
FROM address_problem_farmers apf
LEFT JOIN rep_address ra ON ra.farmer_id = apf.farmer_id
LEFT JOIN gps_home_point gp ON gp.farmer_id = apf.farmer_id
ORDER BY apf.state_norm, apf.farmer_id, ra.village_disp
