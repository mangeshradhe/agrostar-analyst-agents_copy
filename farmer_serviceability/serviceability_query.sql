-- ══════════════════════════════════════════════════════════════════════════════
-- Farmer Serviceability Check — 3-Tier VM resolution + active LMD coverage
-- See serviceability_logic.md in this folder for the full explanation.
--
-- CHANGE ONLY THESE TWO VALUES
-- state_name : 'maharashtra' | 'gujarat' | 'rajasthan' | 'madhya pradesh' | 'uttar pradesh'
-- farmer_type : 'transacting' | 'non_transacting' | 'all'
-- ══════════════════════════════════════════════════════════════════════════════
WITH filters AS (
  SELECT
    'rajasthan' AS state_name,
    'transacting' AS farmer_type
),

-- ── Farmer universe from profile master ───────────────────────────────────────
-- State filter here. Transacting = has a first_transaction_date.
-- Cohort year = first txn FY (transacting) or registration FY (non-transacting).
farmer_pool AS (
  SELECT
    fpm.farmer_id,
    CASE
      WHEN fpm.first_transaction_date IS NOT NULL THEN 'transacting'
      ELSE 'non_transacting'
    END AS farmer_type,
    CASE
      WHEN fpm.first_transaction_date IS NOT NULL THEN
        CONCAT('FY', CAST(
          IF(EXTRACT(MONTH FROM fpm.first_transaction_date) >= 4,
             EXTRACT(YEAR FROM fpm.first_transaction_date) + 1,
             EXTRACT(YEAR FROM fpm.first_transaction_date)) AS STRING))
      ELSE
        CONCAT('FY', CAST(
          IF(EXTRACT(MONTH FROM CAST(fpm.profile_created_date AS DATETIME)) >= 4,
             EXTRACT(YEAR FROM CAST(fpm.profile_created_date AS DATETIME)) + 1,
             EXTRACT(YEAR FROM CAST(fpm.profile_created_date AS DATETIME))) AS STRING))
    END AS cohort_fy,
    LOWER(TRIM(fpm.village)) AS profile_village,
    LOWER(TRIM(fpm.taluka)) AS profile_taluka,
    LOWER(TRIM(fpm.district)) AS profile_district,
    LOWER(TRIM(fpm.pin_code)) AS profile_pincode,
    CASE
      WHEN LOWER(TRIM(fpm.state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(fpm.state))
    END AS state_norm
  FROM `agrostar-data.dwh_views.farmer_profile_master` fpm
  CROSS JOIN filters f
  WHERE fpm.is_archived = 0
    AND CASE
      WHEN LOWER(TRIM(fpm.state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(fpm.state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(fpm.state))
    END = f.state_name
    AND (
      f.farmer_type = 'all'
      OR (f.farmer_type = 'transacting' AND fpm.first_transaction_date IS NOT NULL)
      OR (f.farmer_type = 'non_transacting' AND fpm.first_transaction_date IS NULL)
    )
),

-- ── Address pool ───────────────────────────────────────────────────────────────
-- Source 1: csr_shippingaddress via farmer_id FK — ALL saved addresses,
-- no is_archived filter (archived = hidden from UI, not "farmer moved").
-- Source 2: profile master address (registration address, independently entered).
-- UNION DISTINCT deduplicates identical address combos across both sources.
all_addresses AS (
  SELECT DISTINCT
    sa.farmer_id,
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
  FROM `agrostar-data.prod_db_views.csr_shippingaddress` sa
  INNER JOIN farmer_pool fp ON fp.farmer_id = sa.farmer_id
  WHERE sa.village IS NOT NULL
    AND LOWER(TRIM(sa.village)) NOT IN ('', 'na', 'n/a', 'nil', 'none', 'unknown')
    AND LENGTH(TRIM(sa.village)) >= 2

  UNION DISTINCT

  SELECT DISTINCT
    fp.farmer_id,
    fp.profile_village AS village_raw,
    fp.profile_taluka AS taluka_raw,
    fp.profile_district AS district_raw,
    fp.profile_pincode AS pincode_raw,
    fp.state_norm
  FROM farmer_pool fp
  WHERE fp.profile_village IS NOT NULL
    AND fp.profile_village NOT IN ('', 'na', 'n/a', 'nil', 'none', 'unknown')
    AND LENGTH(fp.profile_village) >= 2
),

-- ── Village master ─────────────────────────────────────────────────────────────
vm AS (
  SELECT
    id,
    LOWER(TRIM(village)) AS village_norm,
    LOWER(TRIM(taluka)) AS taluka_norm,
    LOWER(TRIM(district)) AS district_norm,
    LOWER(TRIM(pin_code)) AS pincode_norm,
    CASE
      WHEN LOWER(TRIM(state)) LIKE 'gujarat%' THEN 'gujarat'
      WHEN LOWER(TRIM(state)) LIKE 'maharash%' THEN 'maharashtra'
      WHEN LOWER(TRIM(state)) LIKE 'rajas%' THEN 'rajasthan'
      WHEN LOWER(TRIM(state)) LIKE 'madhya%' THEN 'madhya pradesh'
      WHEN LOWER(TRIM(state)) LIKE 'uttar%' THEN 'uttar pradesh'
      ELSE LOWER(TRIM(state))
    END AS state_norm,
    is_archived,
    replaced_by_id
  FROM `agrostar-data.static_tables_views.csr_villageaddress`
),

-- ── Tier 1: 5-field match (village + taluka + district + pincode + state) ──────
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
      ON v.village_norm = a.village_raw
      AND v.taluka_norm = a.taluka_raw
      AND v.district_norm = a.district_raw
      AND v.pincode_norm = a.pincode_raw
      AND v.state_norm = a.state_norm
  ) WHERE rn = 1
),
tier1_resolved AS (
  SELECT
    t1.farmer_id, t1.village_raw, t1.taluka_raw, t1.district_raw, t1.pincode_raw, t1.state_norm,
    1 AS tier,
    CASE
      WHEN t1.vm_id IS NULL THEN FALSE
      WHEN t1.is_archived = 0 THEN TRUE
      WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN TRUE
      ELSE FALSE
    END AS is_resolved,
    CASE
      WHEN t1.vm_id IS NULL THEN NULL
      WHEN t1.is_archived = 0 THEN vm1.village_norm
      WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN vm2.village_norm
      ELSE NULL
    END AS canonical_village,
    CASE
      WHEN t1.vm_id IS NULL THEN NULL
      WHEN t1.is_archived = 0 THEN vm1.pincode_norm
      WHEN t1.is_archived = 1 AND t1.replaced_by_id IS NOT NULL THEN vm2.pincode_norm
      ELSE NULL
    END AS canonical_pincode
  FROM tier1_match t1
  LEFT JOIN vm vm1 ON vm1.id = t1.vm_id AND t1.is_archived = 0
  LEFT JOIN vm vm2 ON vm2.id = t1.replaced_by_id
),

-- ── Tier 2: 4-field match, no pincode ─────────────────────────────────────────
-- Catches wrong/empty pincodes. VM supplies canonical pincode.
-- All VM matches kept — any coverage hit = Serviceable.
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
    ON v.village_norm = a.village_raw
    AND v.taluka_norm = a.taluka_raw
    AND v.district_norm = a.district_raw
    AND v.state_norm = a.state_norm
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

-- ── Tier 3: raw address fallback ──────────────────────────────────────────────
-- No VM match found. village_raw + pincode_raw checked directly against coverage.
tier3_candidates AS (
  SELECT DISTINCT tc.farmer_id, tc.village_raw, tc.taluka_raw, tc.district_raw, tc.pincode_raw, tc.state_norm
  FROM tier2_candidates tc
  LEFT JOIN tier2_resolved t2r
    ON t2r.farmer_id = tc.farmer_id
    AND t2r.village_raw = tc.village_raw
    AND t2r.taluka_raw = tc.taluka_raw
    AND t2r.district_raw = tc.district_raw
    AND t2r.pincode_raw = tc.pincode_raw
  WHERE t2r.farmer_id IS NULL
),
tier3_resolved AS (
  SELECT
    farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm,
    3 AS tier, FALSE AS is_resolved,
    village_raw AS canonical_village,
    pincode_raw AS canonical_pincode
  FROM tier3_candidates
),

-- ── Merge all tiers ────────────────────────────────────────────────────────────
all_resolved AS (
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode
  FROM tier1_resolved WHERE is_resolved = TRUE
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode
  FROM tier2_resolved
  UNION ALL
  SELECT farmer_id, village_raw, taluka_raw, district_raw, pincode_raw, state_norm, tier, canonical_village, canonical_pincode
  FROM tier3_resolved
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
    LOWER(TRIM(da.taluka)) AS taluka_norm,
    CONCAT(TRIM(ui.first_name), ' ', TRIM(ui.last_name)) AS lmd_partner_name
  FROM `agrostar-data.prod_agroex_db_views.assignment_deliverycoverage` dc
  JOIN `agrostar-data.prod_agroex_db_views.assignment_deliveryarea` da
    ON da.id = dc.delivery_area_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocationfranchisemapping` apl
    ON apl.id = da.pickuplocation_franchise_mapping_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_franchise` asf
    ON asf.id = apl.franchise_id
  JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocation` pl
    ON pl.id = apl.pickuplocation_id
  JOIN `agrostar-data.prod_db_views.delivery_franchise` df
    ON df.id = asf.franchise_id
  JOIN `agrostar-data.prod_db_views.delivery_userinformation` ui
    ON ui.username = df.user_info_id
  WHERE da.is_active = 1 AND dc.is_active = 1 AND asf.is_active = 1 AND pl.is_active = 1
),

-- ── Coverage hits ─────────────────────────────────────────────────────────────
coverage_hits AS (
  SELECT ar.farmer_id, sv.lmd_partner_name, 1 AS cov_priority, ar.tier
  FROM all_resolved ar
  JOIN svc_coverage sv
    ON sv.coverage_type = 'village'
    AND sv.village_norm = ar.canonical_village
    AND sv.pincode_norm = ar.canonical_pincode
  WHERE ar.canonical_village IS NOT NULL
    AND ar.canonical_village NOT IN ('', 'na', 'n/a', 'nil', 'none', 'unknown', 'not available')
    AND LENGTH(ar.canonical_village) >= 2

  UNION ALL

  SELECT ar.farmer_id, sv.lmd_partner_name, 2, ar.tier
  FROM all_resolved ar
  JOIN svc_coverage sv
    ON sv.coverage_type = 'taluka'
    AND sv.state_norm = ar.state_norm
    AND sv.district_norm = ar.district_raw
    AND sv.taluka_norm = ar.taluka_raw

  UNION ALL

  SELECT ar.farmer_id, sv.lmd_partner_name, 3, ar.tier
  FROM all_resolved ar
  JOIN svc_coverage sv
    ON sv.coverage_type = 'pincode'
    AND sv.state_norm = ar.state_norm
    AND sv.pincode_norm = ar.canonical_pincode
),

-- ── Best LMD per farmer ───────────────────────────────────────────────────────
best_lmd AS (
  SELECT farmer_id, lmd_partner_name FROM (
    SELECT *,
      ROW_NUMBER() OVER (
        PARTITION BY farmer_id
        ORDER BY cov_priority ASC, tier ASC, lmd_partner_name ASC
      ) AS rn
    FROM coverage_hits
  ) WHERE rn = 1
),

-- ── 3-bucket serviceability per farmer ────────────────────────────────────────
farmer_bucket AS (
  SELECT
    ar.farmer_id,
    CASE
      WHEN MAX(CASE WHEN ch.farmer_id IS NOT NULL THEN 1 ELSE 0 END) = 1 THEN 'Serviceable'
      WHEN MAX(CASE WHEN ar.tier IN (1, 2) THEN 1 ELSE 0 END) = 1 THEN 'Non Serviceable'
      ELSE 'Address Problem'
    END AS serviceability_bucket
  FROM all_resolved ar
  LEFT JOIN coverage_hits ch ON ch.farmer_id = ar.farmer_id
  GROUP BY ar.farmer_id
)

-- ── Final output ──────────────────────────────────────────────────────────────
-- Farmer-level detail. For an aggregated cohort table (farmer counts by
-- cohort_fy x farmer_type x bucket), swap in the commented block below.
SELECT
  fp.farmer_id,
  fp.farmer_type,
  CASE WHEN fb.serviceability_bucket = 'Serviceable' THEN 1 ELSE 0 END AS is_serviceable,
  COALESCE(fb.serviceability_bucket, 'Address Problem') AS serviceability_bucket,
  lmd.lmd_partner_name
FROM farmer_pool fp
LEFT JOIN farmer_bucket fb ON fb.farmer_id = fp.farmer_id
LEFT JOIN best_lmd lmd ON lmd.farmer_id = fp.farmer_id
ORDER BY fp.farmer_id

-- Aggregated cohort view instead of farmer-level rows:
-- SELECT
--   fp.cohort_fy,
--   fp.farmer_type,
--   COALESCE(fb.serviceability_bucket, 'Address Problem') AS serviceability_bucket,
--   COUNT(DISTINCT fp.farmer_id) AS farmer_count
-- FROM farmer_pool fp
-- LEFT JOIN farmer_bucket fb ON fb.farmer_id = fp.farmer_id
-- GROUP BY 1, 2, 3
