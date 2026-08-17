-- Field Visit Priority Prototype — SM & TM, per priority_framework_FINAL.md
-- Every CTE below has been individually verified against real BigQuery output during design
-- (see data_access_ledger.md / priority_logic_framework_notes.md for the verification trail).
-- Run daily. CURRENT_DATE() drives day-of-week reasons, month-to-date targets, and suppression state.
--
-- KNOWN LIMITATIONS (documented, not hidden):
--   1. Working-days-per-month uses a fixed 26-day approximation (Mon-Sat, minus ~avg holidays) —
--      no full holiday-calendar table exists (only 3 confirmed national holidays/year located).
--   2. Weights (45/25/15/15) are v1, Sales-Ops-sign-off-anchored but not cleanly backtested — see
--      priority_logic_framework_notes.md for the inconclusive backtest attempt and why.
--   3. Onboarding "about-to-close" threshold (stage LIKE '8%' OR 'Closed Won but Cheque Pending') is a
--      proposed default, not confirmed with the onboarding process owner.
--   4. Track slot-split (Active/Onboarding/Churned) uses each track's real relative population as the
--      split basis — data-derived per user instruction, not the colleague-doc's unvalidated 14/4/2.
--   5. Onboarding's "name" column is a numeric contact ID cast to STRING, not a real name —
--      zoho_leads.contact_name is typed INTEGER at source; no clean text-name column has been found yet.
--
-- OUTPUT SCHEMA (one row per rep x partner x track):
--   list_date       DATE   — the day this list was generated for (drives day-of-week reasons + MTD targets)
--   rep_email       STRING — filter on this to get one rep's list (an SM email or a TM email)
--   role            STRING — 'SM' or 'TM' — which hierarchy level this row belongs to
--   farmer_id       INT64  — partner ID (join key back to okr_data_live / the app's partner table)
--   name            STRING — partner display name (Active/Churned tracks only — see limitation #5 above)
--   territory       STRING — partner's territory
--   track           STRING — 'Active', 'Churned', or 'Onboarding' — which of the 3 tracks this row is from
--   score           FLOAT  — NOT comparable across tracks (Active is a 0-100 blended percentile score,
--                    Churned is a raw Rs outstanding amount, Onboarding is an internal ranking number) —
--                    use only to sort within the same track, never to compare Active vs Churned vs Onboarding
--   primary_reason  STRING — the ONE plain-English reason to show the rep — this is what the rep reads,
--                    not the score
--
--   -- Score breakdown (added on developer request) — populated for track='Active' ONLY. NULL for
--   -- Churned/Onboarding because those tracks don't blend multiple signals — see the header note on
--   -- each track's CTE for why. Each *_pctile column is 0-100: this partner's rank against every other
--   -- active partner on that ONE signal (see priority_framework_FINAL.md for why percentile, not raw
--   -- value). score = ROUND((45*collection_pctile + 25*revenue_pctile + 15*visitgap_pctile +
--   -- 15*target_pctile)/100 + day-of-week bonus, 2) — recompute this yourself from the columns below to
--   -- audit any row.
--   overdue_amount      FLOAT — raw Rs overdue (Active only)
--   pog_unsold_amount    FLOAT — raw Rs of billed-but-unconfirmed-sold stock (Active only)
--   collection_pctile   FLOAT — 0-100 rank on (overdue_amount + pog_unsold_amount) combined — 45% weight
--   revenue_decline_pct FLOAT — raw % purchases dropped, last 30d vs prior 30d (Active only, can be 0)
--   revenue_pctile      FLOAT — 0-100 rank on revenue_decline_pct — 25% weight
--   visitgap_pctile     FLOAT — 0-100 rank on days since last visit (raw days not re-exposed here — see
--                       daily_quota/menu_size section of the app for that; 9999 = never visited) — 15% weight
--   target_exposure     FLOAT — raw Rs exposure to this partner's territory/category monthly target gap
--   target_pctile       FLOAT — 0-100 rank on target_exposure — 15% weight
--
--   rank_in_track   INT64  — this partner's rank within (rep_email, track), 1 = highest priority
--   daily_quota     FLOAT  — this rep's strict "visits you must complete today" number (span x cadence /
--                    working days) — much smaller than menu_size, shown for context/reporting only
--   menu_size       INT64  — the size of the recommendation list this rep actually sees (daily_quota x5,
--                    floor 10) — total rows for this rep across all 3 tracks will not exceed this number
--
-- Refresh cadence: intended to run once daily (or on-demand) via BigQuery scheduled query / Cloud
-- Composer / whatever job runner the app backend uses — CURRENT_DATE() makes every run reflect that day's
-- state automatically (suppression, MTD targets, day-of-week reasons all shift with the run date).

WITH

-- ============================================================
-- ACTIVE TRACK
-- ============================================================

partner_active AS (
  SELECT * FROM (
    SELECT SAFE_CAST(farmer_id AS INT64) AS farmer_id, name, UPPER(TRIM(Territory)) AS territory,
      Cluster, LOWER(TRIM(sm)) AS sm, LOWER(TRIM(tm)) AS tm,
      ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY (cluster IS NULL), (territory IS NULL)) AS rn
    FROM `offline_team.okr_data_live`
    WHERE farmer_id IS NOT NULL AND name IS NOT NULL AND status = 'ACTIVE'
  ) WHERE rn = 1
),

-- Collection Recovery: overdue balance + worst DPD, netted through reconciliation
debits AS (
  SELECT cf.farmer_id, d.id, d.amount, IFNULL(d.interest_amount,0) AS interest_amount, d.due_date
  FROM `prod_db_views.wallet_creditwallettransaction` d
  JOIN `prod_db_views.csr_farmer` cf ON cf.user_id = d.wallet_user_id
  WHERE d.cancelled = 0 AND d.transaction_type = 0 AND d.reason_id NOT IN (2)
),
recon AS (
  SELECT reconciled_for_id, SUM(amount) AS reconciled_amount
  FROM `prod_db_views.wallet_creditwallettransactionreconciliation`
  WHERE cancelled = 0
  GROUP BY reconciled_for_id
),
collection AS (
  SELECT d.farmer_id,
    SUM(IF(DATE(d.due_date) < CURRENT_DATE(),
           GREATEST(d.amount + d.interest_amount - IFNULL(r.reconciled_amount,0), 0), 0)) AS overdue_amount,
    MAX(IF(DATE(d.due_date) < CURRENT_DATE()
           AND (d.amount + d.interest_amount - IFNULL(r.reconciled_amount,0)) > 0,
           DATE_DIFF(CURRENT_DATE(), DATE(d.due_date), DAY), 0)) AS max_dpd
  FROM debits d
  LEFT JOIN recon r ON r.reconciled_for_id = d.id
  GROUP BY d.farmer_id
),

-- POG risk (blended into Collection per user decision): unsold-on-shelf value for SKU-mandatory tracked items.
-- category via item_mst.category_code directly — verified more accurate than sku_cat_repo's prefix-derived
-- version, and avoids the pristine_wms_views permission gap (sku_cat_repo joins that dataset internally).
category_ref AS (
  SELECT item_code, category_code
  FROM `pristine_wms_prod_db.item_mst`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY item_code ORDER BY updated_on DESC) = 1
),
tracked_skus AS (
  SELECT DISTINCT c.sku_code, c.sales_start_date, c.sales_end_date, cr.category_code AS category
  FROM `catalog_views.catalog_management_stocktrackingconfig` c
  LEFT JOIN category_ref cr ON c.sku_code = cr.item_code
  WHERE c.is_active = TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY c.sku_code ORDER BY c.state) = 1
),
moq_ref AS (
  SELECT DISTINCT item_code, CASE WHEN moq = 0 OR moq IS NULL THEN 1 ELSE moq END AS moq
  FROM `pristine_wms_prod_db.item_mst`
),
last_stock_capture AS (
  SELECT partner_id AS farmer_id, sku_code, DATE(last_captured_on) AS last_capture_date
  FROM `prod_db_views.order_management_stocktracking`
  WHERE is_active = TRUE AND last_captured_on IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY partner_id, sku_code ORDER BY last_captured_on DESC) = 1
),
pog_billed AS (
  SELECT SAFE_CAST(opt.owner_id AS INT64) AS farmer_id, opt.Item_SKU_Code AS sku_code,
    SUM(opt.SKU_Revenue) AS gross_billed_amount,
    CASE WHEN MAX(ts.category) = 'SEEDS' THEN SUM(opt.qty)
         ELSE ROUND(SUM(opt.qty) / MAX(mo.moq)) END AS gross_billed_boxes
  FROM `optimized_reports_data.sale_return_b2c_b2b` opt
  JOIN tracked_skus ts ON opt.Item_SKU_Code = ts.sku_code
    AND DATE(opt.Invoice_Created) BETWEEN ts.sales_start_date AND ts.sales_end_date
  LEFT JOIN moq_ref mo ON mo.item_code = opt.Item_SKU_Code
  WHERE opt.Channel_Name = 'B2B' AND opt.SKU_Revenue >= 1 AND DATE(opt.Invoice_Created) >= '2026-04-01'
    AND opt.unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY 1, 2
),
pog_risk AS (
  SELECT pb.farmer_id,
    SUM(pb.gross_billed_amount * IF(lsc.last_capture_date IS NULL, 1, 0)) AS pog_uncaptured_risk_amount
  FROM pog_billed pb
  LEFT JOIN last_stock_capture lsc ON lsc.farmer_id = pb.farmer_id AND lsc.sku_code = pb.sku_code
  WHERE pb.gross_billed_boxes >= 1
  GROUP BY pb.farmer_id
),

-- Revenue Opportunity: 30-day trend. Flagged in design docs as the weakest-validated signal (backtest
-- showed mean-reversion, not momentum) — weighted lower (25%) than the colleague-doc's original 35%.
revenue_trend AS (
  SELECT SAFE_CAST(owner_id AS INT64) AS farmer_id,
    SUM(IF(DATE(Invoice_Created) > DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), SKU_Revenue, 0)) AS rev_last_30d,
    SUM(IF(DATE(Invoice_Created) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
                                      AND DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), SKU_Revenue, 0)) AS rev_prior_30d
  FROM `optimized_reports_data.sale_return_b2c_b2b`
  WHERE Channel_Name = 'B2B' AND unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY farmer_id
),

-- Visit Gap: dual-app dedup, 2-year lookback. Never-visited partners get 9999 (max urgency), not 0.
visits_dedup AS (
  SELECT * FROM (
    SELECT CAST(store_id AS STRING) AS store_id, DATE(date) AS visit_date,
      ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(email)), CAST(store_id AS STRING), DATE(date)
                          ORDER BY date_time DESC) AS rn
    FROM `offline_team.store_visits_v2`
    WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 730 DAY)
    UNION ALL
    SELECT CAST(storeId AS STRING) AS store_id, DATE(date) AS visit_date,
      ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(email)), CAST(storeId AS STRING), DATE(date)
                          ORDER BY updatedOn DESC) AS rn
    FROM `prod_db_views.visit`
    WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 730 DAY)
  ) WHERE rn = 1
),
last_visit AS (
  SELECT SAFE_CAST(store_id AS INT64) AS farmer_id, MAX(visit_date) AS last_visit_date
  FROM visits_dedup
  GROUP BY farmer_id
),

-- Targets/Monthly Category Gap: dynamic current-month unpivot (works any month, not hardcoded).
-- gross_* columns are in LACS — ×100000 applied. category_repo reclassified for WSF/NPK on the actuals
-- side to match the target table's own native WSF bucket (verified mismatch, fixed — see ledger).
aop_unpivoted AS (
  SELECT UPPER(TRIM(revised_territory)) AS territory, category_repo, month_data.Month,
    month_data.AOP_Target * 100000 AS target_amount
  FROM `optimized_reports_data.aop_offline_online_fy27` aop,
  UNNEST([
    STRUCT(DATE '2026-04-01' AS Month, aop.gross_apr_26 AS AOP_Target),
    STRUCT(DATE '2026-05-01' AS Month, aop.gross_may_26 AS AOP_Target),
    STRUCT(DATE '2026-06-01' AS Month, aop.gross_jun_26 AS AOP_Target),
    STRUCT(DATE '2026-07-01' AS Month, aop.gross_jul_26 AS AOP_Target),
    STRUCT(DATE '2026-08-01' AS Month, aop.gross_aug_26 AS AOP_Target),
    STRUCT(DATE '2026-09-01' AS Month, aop.gross_sep_26 AS AOP_Target),
    STRUCT(DATE '2026-10-01' AS Month, aop.gross_oct_26 AS AOP_Target),
    STRUCT(DATE '2026-11-01' AS Month, aop.gross_nov_26 AS AOP_Target),
    STRUCT(DATE '2026-12-01' AS Month, aop.gross_dec_26 AS AOP_Target),
    STRUCT(DATE '2027-01-01' AS Month, aop.gross_jan_27 AS AOP_Target),
    STRUCT(DATE '2027-02-01' AS Month, aop.gross_feb_27 AS AOP_Target),
    STRUCT(DATE '2027-03-01' AS Month, aop.gross_mar_27 AS AOP_Target)
  ]) AS month_data
  WHERE aop.sales_source = 'Offline' AND aop.revised_territory IS NOT NULL
),
territory_category_target AS (
  SELECT territory, category_repo, SUM(target_amount) AS month_target
  FROM aop_unpivoted
  WHERE Month = DATE_TRUNC(CURRENT_DATE(), MONTH)
  GROUP BY territory, category_repo
),
territory_category_actual AS (
  SELECT UPPER(TRIM(territory)) AS territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END AS category_repo,
    SUM(SKU_Revenue) AS mtd_actual
  FROM `optimized_reports_data.sale_return_b2c_b2b`
  WHERE Channel_Name = 'B2B' AND DATE(Invoice_Created) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
    AND unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END
),
territory_category_gap AS (
  SELECT t.territory, t.category_repo, t.month_target, IFNULL(a.mtd_actual,0) AS mtd_actual,
    GREATEST(1 - SAFE_DIVIDE(IFNULL(a.mtd_actual,0), t.month_target), 0) AS shortfall_pct
  FROM territory_category_target t
  LEFT JOIN territory_category_actual a ON a.territory = t.territory AND a.category_repo = t.category_repo
  WHERE t.month_target > 0
),
partner_category_mix AS (
  SELECT SAFE_CAST(owner_id AS INT64) AS farmer_id, UPPER(TRIM(territory)) AS territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END AS category_repo,
    SUM(SKU_Revenue) AS partner_category_revenue
  FROM `optimized_reports_data.sale_return_b2c_b2b`
  WHERE Channel_Name = 'B2B' AND DATE(Invoice_Created) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
    AND unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY farmer_id, territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END
),
partner_target_detail AS (
  SELECT pcm.farmer_id, pcm.territory, pcm.category_repo,
    tcg.shortfall_pct * pcm.partner_category_revenue AS contribution
  FROM partner_category_mix pcm
  JOIN territory_category_gap tcg ON tcg.territory = pcm.territory AND tcg.category_repo = pcm.category_repo
),
partner_target_score AS (
  SELECT farmer_id,
    SUM(contribution) AS weighted_shortfall_exposure,
    ARRAY_AGG(category_repo ORDER BY contribution DESC LIMIT 1)[OFFSET(0)] AS top_category
  FROM partner_target_detail
  GROUP BY farmer_id
),

active_scored AS (
  SELECT
    p.farmer_id, p.name, p.territory, p.sm, p.tm,
    IFNULL(c.overdue_amount,0) AS overdue_amount,
    IFNULL(c.max_dpd,0) AS max_dpd,
    IFNULL(pr.pog_uncaptured_risk_amount,0) AS pog_uncaptured_risk_amount,
    IFNULL(c.overdue_amount,0) + IFNULL(pr.pog_uncaptured_risk_amount,0) AS collection_risk_raw,
    GREATEST(IFNULL(SAFE_DIVIDE(rt.rev_prior_30d - rt.rev_last_30d, rt.rev_prior_30d),0), 0) AS revenue_decline_pct,
    IF(lv.last_visit_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), lv.last_visit_date, DAY)) AS days_since_last_visit,
    IFNULL(ts.weighted_shortfall_exposure,0) AS target_exposure,
    ts.top_category
  FROM partner_active p
  LEFT JOIN collection c ON c.farmer_id = p.farmer_id
  LEFT JOIN pog_risk pr ON pr.farmer_id = p.farmer_id
  LEFT JOIN revenue_trend rt ON rt.farmer_id = p.farmer_id
  LEFT JOIN last_visit lv ON lv.farmer_id = p.farmer_id
  LEFT JOIN partner_target_score ts ON ts.farmer_id = p.farmer_id
),
active_percentiled AS (
  SELECT *,
    PERCENT_RANK() OVER (ORDER BY collection_risk_raw ASC) * 100 AS collection_pctile,
    PERCENT_RANK() OVER (ORDER BY revenue_decline_pct ASC) * 100 AS revenue_pctile,
    PERCENT_RANK() OVER (ORDER BY days_since_last_visit ASC) * 100 AS visitgap_pctile,
    PERCENT_RANK() OVER (ORDER BY target_exposure ASC) * 100 AS target_pctile
  FROM active_scored
),
-- Day-of-week context (verified against real data): Wed=collections, Thu=onboarding-readiness (already
-- handled by the Onboarding track's own about-to-close-first ranking, no change needed here), Fri=Red
-- Friday sales push. BigQuery DAYOFWEEK: 1=Sun,2=Mon,3=Tue,4=Wed,5=Thu,6=Fri,7=Sat.
day_context AS (
  SELECT EXTRACT(DAYOFWEEK FROM CURRENT_DATE()) AS dow
),
active_final AS (
  SELECT
    farmer_id, name, territory, sm, tm, days_since_last_visit,
    -- Breakdown columns (added per developer request): the raw value AND the 0-100 rank for each of the
    -- 4 signals that feed the score, so the app/devs can audit or display "why" beyond the reason text.
    -- Only populated for the Active track — Churned's score IS the raw amount (nothing to break down)
    -- and Onboarding's ranking isn't a percentile blend (see those tracks' NULL placeholders below).
    overdue_amount,
    pog_uncaptured_risk_amount AS pog_unsold_amount,
    ROUND(collection_pctile, 2) AS collection_pctile,
    ROUND(revenue_decline_pct * 100, 2) AS revenue_decline_pct,
    ROUND(revenue_pctile, 2) AS revenue_pctile,
    ROUND(visitgap_pctile, 2) AS visitgap_pctile,
    ROUND(target_exposure, 2) AS target_exposure,
    ROUND(target_pctile, 2) AS target_pctile,
    -- Weights decide the SCORE. Reason is picked by raw percentile (most extreme signal for THIS
    -- partner), NOT weighted contribution — weighting the reason-pick was tested and produced a bug
    -- (Collection dominated 71% of reasons purely because it has the highest weight, even when another
    -- signal was far more extreme for that specific partner). Fixed before shipping.
    -- Day bonus is a modest, clearly-flagged v1 nudge (+5 on a 0-100 scale), not a weight restructure —
    -- keeps the validated percentile math intact while surfacing the day's operational focus.
    CAST((45*collection_pctile + 25*revenue_pctile + 15*visitgap_pctile + 15*target_pctile) / 100
      + CASE WHEN dc.dow = 4 AND overdue_amount > 0 THEN 5
             WHEN dc.dow = 6 AND revenue_decline_pct > 0 THEN 5
             ELSE 0 END AS FLOAT64) AS active_score,
    CASE
      WHEN collection_pctile >= revenue_pctile AND collection_pctile >= visitgap_pctile AND collection_pctile >= target_pctile THEN
        CONCAT(
          IF(pog_uncaptured_risk_amount > overdue_amount,
             CONCAT('Unsold stock worth ~Rs ', CAST(ROUND(pog_uncaptured_risk_amount) AS STRING),
                    ' from a past order hasn\'t been confirmed sold'),
             CONCAT('Rs ', CAST(ROUND(overdue_amount) AS STRING), ' overdue by ', CAST(max_dpd AS STRING), ' days')),
          IF(dc.dow = 4, ' - and it\'s collections day', ''))
      WHEN revenue_pctile >= visitgap_pctile AND revenue_pctile >= target_pctile THEN
        CONCAT('Purchases dropped ~', CAST(ROUND(revenue_decline_pct*100) AS STRING),
               '% in the last 30 days - worth checking in',
               IF(dc.dow = 6, ' - good day to convert this into a sale', ''))
      WHEN visitgap_pctile >= target_pctile THEN
        IF(days_since_last_visit >= 9999, 'Never visited',
           CONCAT('Not visited in ', CAST(days_since_last_visit AS STRING), ' days'))
      ELSE
        CONCAT(territory, ' is behind this month\'s ', IFNULL(top_category,'category'),
               ' target, and this partner is a major ', IFNULL(top_category,'category'), ' seller')
    END AS primary_reason,
    'Active' AS track
  FROM active_percentiled, day_context dc
),

-- ============================================================
-- CHURNED RECOVERY TRACK — verified 2026-08-17: 3,831 partners, Rs 37.23 Cr outstanding
-- ============================================================

partner_inactive AS (
  SELECT * FROM (
    SELECT SAFE_CAST(farmer_id AS INT64) AS farmer_id, name, UPPER(TRIM(Territory)) AS territory,
      LOWER(TRIM(sm)) AS sm, LOWER(TRIM(tm)) AS tm,
      ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY (cluster IS NULL), (territory IS NULL)) AS rn
    FROM `offline_team.okr_data_live`
    WHERE farmer_id IS NOT NULL AND name IS NOT NULL AND status = 'INACTIVE'
  ) WHERE rn = 1
),
churned_pending AS (
  SELECT d.farmer_id,
    SUM(GREATEST(d.amount + d.interest_amount - IFNULL(r.reconciled_amount,0), 0)) AS pending_amount
  FROM debits d
  LEFT JOIN recon r ON r.reconciled_for_id = d.id
  GROUP BY d.farmer_id
),
churned_final AS (
  SELECT
    pi.farmer_id, pi.name, pi.territory, pi.sm, pi.tm,
    CAST(NULL AS INT64) AS days_since_last_visit,   -- suppression only applies to the Active track
    -- Breakdown columns are NULL here on purpose: Churned's score IS the raw amount owed — there's only
    -- one signal, so there's nothing to break down the way Active's 4-signal blend needs to be.
    CAST(NULL AS FLOAT64) AS overdue_amount, CAST(NULL AS FLOAT64) AS pog_unsold_amount,
    CAST(NULL AS FLOAT64) AS collection_pctile, CAST(NULL AS FLOAT64) AS revenue_decline_pct,
    CAST(NULL AS FLOAT64) AS revenue_pctile, CAST(NULL AS FLOAT64) AS visitgap_pctile,
    CAST(NULL AS FLOAT64) AS target_exposure, CAST(NULL AS FLOAT64) AS target_pctile,
    CAST(cp.pending_amount AS FLOAT64) AS active_score,   -- own ranking, own scale — not compared to Active track
    CONCAT('Inactive, but still owes Rs ', CAST(ROUND(cp.pending_amount) AS STRING),
           ' - this is a recovery visit, not a sales visit') AS primary_reason,
    'Churned' AS track
  FROM partner_inactive pi
  JOIN churned_pending cp ON cp.farmer_id = pi.farmer_id
  WHERE cp.pending_amount >= 1
),

-- ============================================================
-- ONBOARDING TRACK — about-to-close threshold is a PROPOSED default, not yet confirmed
-- ============================================================

-- FIX (caught via test suite, not the earlier spot check): zoho_leads has up to 4 rows per
-- reference_customer_id (re-engaged/repeat leads) — undeduped, this produced 234 duplicate
-- (rep, track, partner) rows in the final output. Dedup to one row per partner, preferring the
-- about-to-close stage if any of that partner's lead rows has it.
onboarding_leads AS (
  SELECT
    reference_customer_id AS farmer_id,
    Contact_name, Lead_Owner, stage, Final_sd_date, territory,
    LOWER(TRIM(Lead_Owner)) AS lead_owner_email
  FROM `optimized_reports_data.zoho_leads`
  WHERE reference_customer_id IS NOT NULL
    AND (stage NOT LIKE '%Closed Won%' OR stage = 'Closed Won but Cheque Pending')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY reference_customer_id
    ORDER BY IF(stage LIKE '8%' OR stage = 'Closed Won but Cheque Pending', 0, 1), Final_sd_date DESC
  ) = 1
),
onboarding_visit_gap AS (
  SELECT ol.*,
    lv.last_visit_date,
    IF(lv.last_visit_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), lv.last_visit_date, DAY)) AS days_since_last_visit
  FROM onboarding_leads ol
  LEFT JOIN last_visit lv ON lv.farmer_id = ol.farmer_id
),
-- FIX (caught via row-count sanity check, not the dry run): okr_data_live is partner-grain, so joining a
-- lead owner's email directly against its sm/tm columns fans out to one row PER PARTNER that rep manages
-- (611,605 "onboarding" rows for SM instead of a few hundred). Deduped rep-directory lookup avoids this.
rep_directory AS (
  SELECT DISTINCT LOWER(TRIM(sm)) AS email, 'SM' AS role
  FROM `offline_team.okr_data_live`
  WHERE sm IS NOT NULL AND NOT STARTS_WITH(LOWER(TRIM(sm)), 'vacant')
  UNION DISTINCT
  SELECT DISTINCT LOWER(TRIM(tm)) AS email, 'TM' AS role
  FROM `offline_team.okr_data_live`
  WHERE tm IS NOT NULL AND NOT STARTS_WITH(LOWER(TRIM(tm)), 'vacant')
),
onboarding_rep_role AS (
  SELECT ov.*,
    IF(rd.role = 'SM', rd.email, NULL) AS sm_email_raw,
    IF(rd.role = 'TM', rd.email, NULL) AS tm_email_raw
  FROM onboarding_visit_gap ov
  LEFT JOIN rep_directory rd ON rd.email = ov.lead_owner_email
),
onboarding_final AS (
  SELECT
    -- KNOWN GAP: zoho_leads.contact_name is an INTEGER (contact ID), not a text name, despite the
    -- column name — caught via dry-run type check. Casting to STRING keeps this query correct, but
    -- the onboarding track shows a numeric ID here, not a real name, until a proper name lookup is added.
    farmer_id, CAST(Contact_name AS STRING) AS name, territory,
    LOWER(TRIM(sm_email_raw)) AS sm, LOWER(TRIM(tm_email_raw)) AS tm,
    CAST(NULL AS INT64) AS days_since_last_visit,   -- suppression only applies to the Active track
    -- Breakdown columns NULL here too: Onboarding's ranking is about-to-close-first then visit-gap
    -- tiebreak, not a percentile blend — the reason text already says everything there is to say.
    CAST(NULL AS FLOAT64) AS overdue_amount, CAST(NULL AS FLOAT64) AS pog_unsold_amount,
    CAST(NULL AS FLOAT64) AS collection_pctile, CAST(NULL AS FLOAT64) AS revenue_decline_pct,
    CAST(NULL AS FLOAT64) AS revenue_pctile, CAST(NULL AS FLOAT64) AS visitgap_pctile,
    CAST(NULL AS FLOAT64) AS target_exposure, CAST(NULL AS FLOAT64) AS target_pctile,
    -- about-to-close sorts first (score=2), else by visit gap (score=1 + normalized gap) — own ranking
    CAST(IF(stage LIKE '8%' OR stage = 'Closed Won but Cheque Pending', 1000, 0)
      + LEAST(days_since_last_visit, 999) AS FLOAT64) AS active_score,
    IF(stage LIKE '8%' OR stage = 'Closed Won but Cheque Pending',
       'Almost ready to become a customer - help push them over the line',
       'New partner, not yet onboarded - help them place their first order') AS primary_reason,
    'Onboarding' AS track
  FROM onboarding_rep_role
  WHERE sm_email_raw IS NOT NULL OR tm_email_raw IS NOT NULL
),

-- ============================================================
-- CAPACITY: real per-rep span of control x required cadence / working days.
-- Working-days uses a fixed 26/month approximation (see file header) — no full holiday calendar exists.
-- Two DISTINCT numbers, deliberately not conflated (verified 2026-08-17):
--   daily_quota = the strict "visits you must complete today" number (SM avg 2.4/day, TM avg 3.0/day —
--                 far smaller than the colleague-doc's flat "20/day", because that doc's number was never
--                 actually derived from real span-of-control x cadence math).
--   menu_size   = the recommendation-LIST size a rep actually sees (daily_quota x5, floor 10) — sized
--                 larger on purpose so the rep has route/geography flexibility on which top candidates to
--                 hit today. This 5x multiplier is a judgment call, not backtested — flagged as such.
-- ============================================================

rep_span AS (
  SELECT sm AS email, 'SM' AS role, COUNT(DISTINCT farmer_id) AS span
  FROM partner_active WHERE sm IS NOT NULL AND NOT STARTS_WITH(sm, 'vacant') GROUP BY sm
  UNION ALL
  SELECT tm AS email, 'TM' AS role, COUNT(DISTINCT farmer_id) AS span
  FROM partner_active WHERE tm IS NOT NULL AND NOT STARTS_WITH(tm, 'vacant') GROUP BY tm
),
rep_quota AS (
  SELECT email, role, span,
    CEIL(span * IF(role = 'SM', 2, 1) / 26.0) AS daily_quota,
    GREATEST(CAST(CEIL(span * IF(role = 'SM', 2, 1) / 26.0) * 5 AS INT64), 10) AS menu_size
  FROM rep_span
),

-- ============================================================
-- FINAL ASSEMBLY — one row per (rep, role, partner), both SM and TM views
-- ============================================================

all_rows AS (
  -- SELECT * is safe here: active_final / churned_final / onboarding_final are built with the exact same
  -- column list and order (including the breakdown columns) on purpose, so they union cleanly.
  SELECT * FROM active_final
  UNION ALL
  SELECT * FROM churned_final
  UNION ALL
  SELECT * FROM onboarding_final
),

-- Track slot split: proportional to each track's real fleet-wide population (data-derived per user
-- instruction, not the colleague-doc's unvalidated 14/4/2) — a defensible v1, not outcome-validated.
track_population AS (
  SELECT
    (SELECT COUNT(*) FROM active_final) AS active_pop,
    (SELECT COUNT(*) FROM churned_final) AS churned_pop,
    (SELECT COUNT(DISTINCT farmer_id) FROM onboarding_final) AS onboarding_pop
),
track_shares AS (
  SELECT
    SAFE_DIVIDE(active_pop, active_pop + churned_pop + onboarding_pop) AS active_share,
    SAFE_DIVIDE(churned_pop, active_pop + churned_pop + onboarding_pop) AS churned_share,
    SAFE_DIVIDE(onboarding_pop, active_pop + churned_pop + onboarding_pop) AS onboarding_share
  FROM track_population
),

-- FIX (caught via test suite): the original per-track cap used CEIL(...) + a floor of 1 on EACH track
-- independently, which can make the three caps sum to more than menu_size (confirmed: 180 reps
-- exceeded their menu_size). Fixed by FLOORing the first two tracks and giving the third the exact
-- remainder — this guarantees active_cap + churned_cap + onboarding_cap = menu_size always, no overflow
-- possible. Trade-off: a track can now legitimately get 0 slots if its true share rounds to zero (no
-- forced minimum-1) — accepted, since respecting the rep's real daily capacity matters more than
-- guaranteeing every track always appears.
track_caps AS (
  SELECT rq.email, rq.role, rq.daily_quota, rq.menu_size,
    CAST(FLOOR(rq.menu_size * ts.active_share) AS INT64) AS active_cap,
    CAST(FLOOR(rq.menu_size * ts.churned_share) AS INT64) AS churned_cap,
    rq.menu_size - CAST(FLOOR(rq.menu_size * ts.active_share) AS INT64)
                 - CAST(FLOOR(rq.menu_size * ts.churned_share) AS INT64) AS onboarding_cap
  FROM rep_quota rq
  CROSS JOIN track_shares ts
),
sm_view_ranked AS (
  SELECT ar.sm AS rep_email, 'SM' AS role, ar.farmer_id, ar.name, ar.territory, ar.track,
    ar.active_score AS score, ar.primary_reason,
    ar.overdue_amount, ar.pog_unsold_amount, ar.collection_pctile,
    ar.revenue_decline_pct, ar.revenue_pctile, ar.visitgap_pctile, ar.target_exposure, ar.target_pctile,
    ROW_NUMBER() OVER (PARTITION BY ar.sm, ar.track ORDER BY ar.active_score DESC) AS rank_in_track,
    tc.daily_quota, tc.menu_size,
    CASE ar.track WHEN 'Active' THEN tc.active_cap WHEN 'Churned' THEN tc.churned_cap ELSE tc.onboarding_cap END AS track_slot_cap
  FROM all_rows ar
  JOIN track_caps tc ON tc.email = ar.sm AND tc.role = 'SM'
  WHERE ar.sm IS NOT NULL AND NOT STARTS_WITH(ar.sm, 'vacant')
    -- Suppression: SM cadence window ~15 days (30 days / 2x-per-month requirement). Active track only —
    -- Churned/Onboarding have NULL days_since_last_visit so this condition is simply false for them.
    AND NOT (ar.track = 'Active' AND ar.days_since_last_visit < 15)
),
tm_view_ranked AS (
  SELECT ar.tm AS rep_email, 'TM' AS role, ar.farmer_id, ar.name, ar.territory, ar.track,
    ar.active_score AS score, ar.primary_reason,
    ar.overdue_amount, ar.pog_unsold_amount, ar.collection_pctile,
    ar.revenue_decline_pct, ar.revenue_pctile, ar.visitgap_pctile, ar.target_exposure, ar.target_pctile,
    ROW_NUMBER() OVER (PARTITION BY ar.tm, ar.track ORDER BY ar.active_score DESC) AS rank_in_track,
    tc.daily_quota, tc.menu_size,
    CASE ar.track WHEN 'Active' THEN tc.active_cap WHEN 'Churned' THEN tc.churned_cap ELSE tc.onboarding_cap END AS track_slot_cap
  FROM all_rows ar
  JOIN track_caps tc ON tc.email = ar.tm AND tc.role = 'TM'
  WHERE ar.tm IS NOT NULL AND NOT STARTS_WITH(ar.tm, 'vacant')
    -- Suppression: TM cadence window ~30 days (30 days / 1x-per-month minimum requirement).
    AND NOT (ar.track = 'Active' AND ar.days_since_last_visit < 30)
)

SELECT CURRENT_DATE() AS list_date, rep_email, role, farmer_id, name, territory, track, score, primary_reason,
  overdue_amount, pog_unsold_amount, collection_pctile, revenue_decline_pct, revenue_pctile,
  visitgap_pctile, target_exposure, target_pctile,
  rank_in_track, daily_quota, menu_size
FROM sm_view_ranked WHERE rank_in_track <= track_slot_cap
UNION ALL
SELECT CURRENT_DATE() AS list_date, rep_email, role, farmer_id, name, territory, track, score, primary_reason,
  overdue_amount, pog_unsold_amount, collection_pctile, revenue_decline_pct, revenue_pctile,
  visitgap_pctile, target_exposure, target_pctile,
  rank_in_track, daily_quota, menu_size
FROM tm_view_ranked WHERE rank_in_track <= track_slot_cap
ORDER BY rep_email, track, rank_in_track
