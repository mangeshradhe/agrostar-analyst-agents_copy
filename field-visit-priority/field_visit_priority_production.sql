-- ============================================================
-- FIELD VISIT PRIORITY — PRODUCTION QUERY (COMPLETE)
-- ============================================================
-- Architecture:
--   STEP 1 → Build weekly pool of 36 partners per rep
--             Active 24 (top by score) + Churned 6 (top by OCP) + Onboarding 6
--   STEP 2 → EV routing splits all 36 into VISIT or CALL
--   STEP 3 → VISIT partners → geo-cluster → best group of 4 today
--   STEP 4 → CALL partners → top 2 by score (only when visit group = 4)
--
-- Weekly slot allocation per rep:
--   Active    : 24 slots — top 24 active partners by blended score
--   Churned   :  6 slots — top 6 INACTIVE partners by pending OCP amount
--   Onboarding:  6 slots — top 6 leads by about-to-close stage + visit gap
--   Total     : 36 per rep per week
--
-- Daily output: 4 visits (geo-clustered) + 2 calls (score-ranked)
-- Run: daily via BigQuery scheduled query or Cloud Composer
-- ============================================================

WITH

-- ── 0. EMPLOYEE CONTACTS ─────────────────────────────────────
employee_contacts AS (
  SELECT
    LOWER(TRIM(Email_Id)) AS email,
    TRIM(Contact_Number)  AS mobile_number,
    TRIM(Full_Name)       AS full_name
  FROM `agrostar-data.offline_team.OKR_EMPLOYEE_CONTACT`
  WHERE Active_Inactive = 'Active' AND Email_Id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(Email_Id)) ORDER BY Email_Id) = 1
),

-- ── 1. PARTNER MASTER ────────────────────────────────────────
partner_master AS (
  SELECT
    SAFE_CAST(farmer_id AS INT64) AS farmer_id,
    name AS partner_name,
    UPPER(TRIM(state)) AS state, UPPER(TRIM(district)) AS district,
    UPPER(TRIM(territory)) AS territory, UPPER(TRIM(cluster)) AS cluster,
    LOWER(TRIM(sm)) AS sm_email, LOWER(TRIM(tm)) AS tm_email,
    LOWER(TRIM(sh)) AS sh_email, LOWER(TRIM(cm)) AS cm_email
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status = 'ACTIVE' AND farmer_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY (cluster IS NULL), (territory IS NULL)) = 1
),

-- ── 2. INSTITUTION ───────────────────────────────────────────
institution AS (
  SELECT
    reference_customer_id AS farmer_id,
    reference_customer_id AS reference_customer_id,
    CAST(contacts_mobile_number AS STRING) AS mobile_number,
    latitude AS lat, longitude AS lng,
    LOWER(TRIM(address_pincode)) AS addr_pincode,
    LOWER(TRIM(address_district)) AS addr_district,
    LOWER(TRIM(address_state)) AS addr_state,
    SAFE_CAST(totalCreditLimit AS FLOAT64) AS credit_limit
  FROM `agrostar-data.replica_galaxy_views.institution`
  WHERE archive = FALSE AND status = 'ACTIVE'
    AND business_type IN ('Proprietorship', 'Partnership')
    AND reference_customer_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) = 1
),

-- ── 3. USER ID ───────────────────────────────────────────────
user_ids AS (
  SELECT farmer_id, user_id
  FROM `agrostar-data.prod_db_views.csr_farmer`
  WHERE farmer_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY farmer_id) = 1
),

-- ── 4. OCP SIGNALS ───────────────────────────────────────────
recon AS (
  SELECT reconciled_for_id, SUM(amount) AS reconciled_amount
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransactionreconciliation`
  WHERE cancelled = 0 GROUP BY reconciled_for_id
),
ocp_signals AS (
  SELECT cf.farmer_id,
    SUM(IF(DATE(d.due_date) < CURRENT_DATE(),
        GREATEST(d.amount + IFNULL(d.interest_amount,0) - IFNULL(r.reconciled_amount,0), 0), 0)) AS ocp_amount,
    MAX(IF(DATE(d.due_date) < CURRENT_DATE()
        AND (d.amount + IFNULL(d.interest_amount,0) - IFNULL(r.reconciled_amount,0)) > 0,
        DATE_DIFF(CURRENT_DATE(), DATE(d.due_date), DAY), 0)) AS max_dpd
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` d
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = d.wallet_user_id
  LEFT JOIN recon r ON r.reconciled_for_id = d.id
  WHERE d.cancelled = 0 AND d.transaction_type = 0 AND d.reason_id NOT IN (2)
  GROUP BY cf.farmer_id
),

-- ── 5. PAYMENT SIGNALS ───────────────────────────────────────
payment_signals AS (
  SELECT cf.farmer_id,
    COUNT(DISTINCT DATE_TRUNC(DATE(d.created_on), MONTH)) AS payment_months_6m,
    MIN(DATE_DIFF(CURRENT_DATE(), DATE(d.created_on), DAY)) AS days_since_last_payment
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` d
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = d.wallet_user_id
  WHERE d.cancelled = 0 AND d.transaction_type = 1
    AND DATE(d.created_on) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
  GROUP BY cf.farmer_id
),

-- ── 6. POG RISK ──────────────────────────────────────────────
category_ref AS (
  SELECT item_code, category_code
  FROM `agrostar-data.pristine_wms_prod_db.item_mst`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY item_code ORDER BY updated_on DESC) = 1
),
tracked_skus AS (
  SELECT DISTINCT c.sku_code, c.sales_start_date, c.sales_end_date, cr.category_code AS category
  FROM `agrostar-data.catalog_views.catalog_management_stocktrackingconfig` c
  LEFT JOIN category_ref cr ON c.sku_code = cr.item_code
  WHERE c.is_active = TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY c.sku_code ORDER BY c.state) = 1
),
moq_ref AS (
  SELECT DISTINCT item_code, CASE WHEN moq = 0 OR moq IS NULL THEN 1 ELSE moq END AS moq
  FROM `agrostar-data.pristine_wms_prod_db.item_mst`
),
last_stock_capture AS (
  SELECT partner_id AS farmer_id, sku_code, DATE(last_captured_on) AS last_capture_date
  FROM `agrostar-data.prod_db_views.order_management_stocktracking`
  WHERE is_active = TRUE AND last_captured_on IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY partner_id, sku_code ORDER BY last_captured_on DESC) = 1
),
pog_billed AS (
  SELECT SAFE_CAST(opt.owner_id AS INT64) AS farmer_id, opt.Item_SKU_Code AS sku_code,
    SUM(opt.SKU_Revenue) AS gross_billed_amount,
    CASE WHEN MAX(ts.category) = 'SEEDS' THEN SUM(opt.qty)
         ELSE ROUND(SUM(opt.qty) / MAX(mo.moq)) END AS gross_billed_boxes
  FROM `agrostar-data.optimized_reports_data.sale_return_b2c_b2b` opt
  JOIN tracked_skus ts ON opt.Item_SKU_Code = ts.sku_code
    AND DATE(opt.Invoice_Created) BETWEEN ts.sales_start_date AND ts.sales_end_date
  LEFT JOIN moq_ref mo ON mo.item_code = opt.Item_SKU_Code
  WHERE opt.Channel_Name = 'B2B' AND opt.SKU_Revenue >= 1
    AND DATE(opt.Invoice_Created) >= '2026-04-01'
    AND opt.unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY 1, 2
),
pog_risk AS (
  SELECT pb.farmer_id,
    SUM(pb.gross_billed_amount * IF(lsc.last_capture_date IS NULL, 1, 0)) AS pog_amount
  FROM pog_billed pb
  LEFT JOIN last_stock_capture lsc ON lsc.farmer_id = pb.farmer_id AND lsc.sku_code = pb.sku_code
  WHERE pb.gross_billed_boxes >= 1
  GROUP BY pb.farmer_id
),

-- ── 7. REVENUE SIGNALS ───────────────────────────────────────
revenue_signals AS (
  SELECT
    SAFE_CAST(owner_id AS INT64) AS farmer_id,
    SUM(IF(DATE(Invoice_Created) BETWEEN '2026-04-01' AND CURRENT_DATE(), SKU_Revenue, 0)) AS rev_ytd,
    SUM(IF(DATE(Invoice_Created) BETWEEN '2025-04-01' AND '2025-08-31', SKU_Revenue, 0)) AS rev_ly,
    MAX(DATE(Invoice_Created)) AS last_order_date,
    COUNT(DISTINCT IF(DATE(Invoice_Created) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY),
      DATE_TRUNC(DATE(Invoice_Created), MONTH), NULL)) AS order_months_6m
  FROM `agrostar-data.optimized_reports_data.sale_return_b2c_b2b`
  WHERE Channel_Name = 'B2B' AND unicommerce_status NOT LIKE '%RETURN%'
    AND DATE(Invoice_Created) >= DATE_SUB(CURRENT_DATE(), INTERVAL 730 DAY)
    AND SAFE_CAST(owner_id AS INT64) IN (SELECT farmer_id FROM partner_master)
  GROUP BY farmer_id
),

-- ── 8. DUAL-APP VISIT DEDUP ───────────────────────────────────
visits_dedup AS (
  SELECT SAFE_CAST(store_id AS INT64) AS farmer_id,
    LOWER(TRIM(sm)) AS sm_email, LOWER(TRIM(tm)) AS tm_email,
    DATE(date) AS visit_date
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 730 DAY)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY LOWER(TRIM(COALESCE(sm, tm, email))), SAFE_CAST(store_id AS INT64), DATE(date)
    ORDER BY date_time DESC
  ) = 1
  UNION ALL
  SELECT SAFE_CAST(storeId AS INT64),
    LOWER(TRIM(sm)), LOWER(TRIM(tm)), DATE(date)
  FROM `agrostar-data.prod_db_views.visit`
  WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 730 DAY)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY LOWER(TRIM(COALESCE(sm, tm, email))), SAFE_CAST(storeId AS INT64), DATE(date)
    ORDER BY updatedOn DESC
  ) = 1
),
last_visit_sm  AS (SELECT farmer_id, sm_email AS rep_email, MAX(visit_date) AS last_visit_date FROM visits_dedup WHERE sm_email IS NOT NULL GROUP BY 1, 2),
last_visit_tm  AS (SELECT farmer_id, tm_email AS rep_email, MAX(visit_date) AS last_visit_date FROM visits_dedup WHERE tm_email IS NOT NULL GROUP BY 1, 2),
last_visit_any AS (SELECT farmer_id, MAX(visit_date) AS last_visit_date FROM visits_dedup GROUP BY 1),

-- ── 9. VISIT-PROXIMATE ORDER RATE ────────────────────────────
order_proximity AS (
  SELECT SAFE_CAST(b.owner_id AS INT64) AS farmer_id,
    COUNT(DISTINCT DATE(b.Invoice_Created)) AS total_order_days,
    COUNTIF(v.visit_date IS NOT NULL
      AND DATE(b.Invoice_Created) BETWEEN v.visit_date AND DATE_ADD(v.visit_date, INTERVAL 7 DAY)) AS proximate_order_days
  FROM `agrostar-data.optimized_reports_data.sale_return_b2c_b2b` b
  LEFT JOIN (SELECT SAFE_CAST(store_id AS INT64) AS fid, DATE(date) AS visit_date
             FROM `agrostar-data.offline_team.store_visits_v2`
             WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)) v
    ON v.fid = SAFE_CAST(b.owner_id AS INT64)
  WHERE b.Channel_Name = 'B2B' AND b.unicommerce_status NOT LIKE '%RETURN%'
    AND DATE(b.Invoice_Created) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
    AND SAFE_CAST(b.owner_id AS INT64) IN (SELECT farmer_id FROM partner_master)
  GROUP BY farmer_id
),

-- ── 10. TERRITORY TARGET (DYNAMIC AOP + CATEGORY SPLIT) ──────
aop_unpivoted AS (
  SELECT UPPER(TRIM(revised_territory)) AS territory, category_repo,
    month_data.month_dt, month_data.aop_target * 100000 AS target_amount
  FROM `agrostar-data.optimized_reports_data.aop_offline_online_fy27` aop,
  UNNEST([
    STRUCT(DATE '2026-04-01' AS month_dt, aop.gross_apr_26 AS aop_target),
    STRUCT(DATE '2026-05-01' AS month_dt, aop.gross_may_26 AS aop_target),
    STRUCT(DATE '2026-06-01' AS month_dt, aop.gross_jun_26 AS aop_target),
    STRUCT(DATE '2026-07-01' AS month_dt, aop.gross_jul_26 AS aop_target),
    STRUCT(DATE '2026-08-01' AS month_dt, aop.gross_aug_26 AS aop_target),
    STRUCT(DATE '2026-09-01' AS month_dt, aop.gross_sep_26 AS aop_target),
    STRUCT(DATE '2026-10-01' AS month_dt, aop.gross_oct_26 AS aop_target),
    STRUCT(DATE '2026-11-01' AS month_dt, aop.gross_nov_26 AS aop_target),
    STRUCT(DATE '2026-12-01' AS month_dt, aop.gross_dec_26 AS aop_target),
    STRUCT(DATE '2027-01-01' AS month_dt, aop.gross_jan_27 AS aop_target),
    STRUCT(DATE '2027-02-01' AS month_dt, aop.gross_feb_27 AS aop_target),
    STRUCT(DATE '2027-03-01' AS month_dt, aop.gross_mar_27 AS aop_target)
  ]) AS month_data
  WHERE aop.sales_source = 'Offline' AND aop.revised_territory IS NOT NULL
),
territory_category_target AS (
  SELECT territory, category_repo, SUM(target_amount) AS month_target
  FROM aop_unpivoted WHERE month_dt = DATE_TRUNC(CURRENT_DATE(), MONTH)
  GROUP BY territory, category_repo
),
territory_category_actual AS (
  SELECT UPPER(TRIM(territory)) AS territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END AS category_repo,
    SUM(SKU_Revenue) AS mtd_actual
  FROM `agrostar-data.optimized_reports_data.sale_return_b2c_b2b`
  WHERE Channel_Name = 'B2B' AND DATE(Invoice_Created) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
    AND unicommerce_status NOT LIKE '%RETURN%'
  GROUP BY territory,
    CASE WHEN LOWER(Product_group) LIKE '%wsf%' OR LOWER(Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE category_repo END
),
territory_category_gap AS (
  SELECT t.territory, t.category_repo,
    GREATEST(1 - SAFE_DIVIDE(IFNULL(a.mtd_actual, 0), t.month_target), 0) AS shortfall_pct
  FROM territory_category_target t
  LEFT JOIN territory_category_actual a ON a.territory = t.territory AND a.category_repo = t.category_repo
  WHERE t.month_target > 0
),
partner_target_score AS (
  SELECT SAFE_CAST(owner_id AS INT64) AS farmer_id,
    SUM(tg.shortfall_pct * s.SKU_Revenue) AS target_exposure,
    ARRAY_AGG(s.category_repo ORDER BY tg.shortfall_pct * s.SKU_Revenue DESC LIMIT 1)[OFFSET(0)] AS top_category
  FROM `agrostar-data.optimized_reports_data.sale_return_b2c_b2b` s
  JOIN territory_category_gap tg ON tg.territory = UPPER(TRIM(s.territory)) AND tg.category_repo =
    CASE WHEN LOWER(s.Product_group) LIKE '%wsf%' OR LOWER(s.Product_group) LIKE '%npk%' THEN 'WSF'
         ELSE s.category_repo END
  WHERE s.Channel_Name = 'B2B' AND DATE(s.Invoice_Created) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
    AND s.unicommerce_status NOT LIKE '%RETURN%'
    AND SAFE_CAST(s.owner_id AS INT64) IN (SELECT farmer_id FROM partner_master)
  GROUP BY farmer_id
),

-- ── 11. ASSEMBLE + SCORE ALL ACTIVE PARTNERS ─────────────────
assembled AS (
  SELECT
    p.farmer_id, p.partner_name, p.state, p.district, p.territory, p.cluster,
    p.sm_email, p.tm_email, p.sh_email, p.cm_email,
    i.reference_customer_id, u.user_id, i.mobile_number,
    i.lat, i.lng, i.addr_pincode, i.addr_district, i.addr_state,
    IFNULL(i.credit_limit, 0) AS credit_limit,
    CASE
      WHEN i.lat IS NOT NULL AND i.lat != 0 AND i.lng IS NOT NULL AND i.lng != 0
        THEN ST_GEOHASH(ST_GEOGPOINT(i.lng, i.lat), 4)
      WHEN i.addr_pincode IS NOT NULL AND i.addr_pincode != ''
        THEN CONCAT('ADDR_', i.addr_state, '_', i.addr_district, '_', i.addr_pincode)
      ELSE CONCAT('DIST_', i.addr_state, '_', i.addr_district)
    END AS geo_key,
    IFNULL(o.ocp_amount, 0) AS ocp_amount,
    IFNULL(o.max_dpd, 0) AS max_dpd,
    IFNULL(pg.pog_amount, 0) AS pog_amount,
    IFNULL(ps.payment_months_6m, 0) AS payment_months_6m,
    IFNULL(ps.days_since_last_payment, 9999) AS days_since_last_payment,
    IFNULL(r.rev_ytd, 0) AS rev_ytd,
    IFNULL(r.rev_ly, 0) AS rev_ly,
    GREATEST(IFNULL(SAFE_DIVIDE(r.rev_ly - r.rev_ytd, NULLIF(r.rev_ly, 0)), 0), 0) AS yoy_decline_pct,
    IFNULL(r.order_months_6m, 0) AS order_months_6m,
    IF(r.last_order_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), r.last_order_date, DAY)) AS days_since_last_order,
    IFNULL(pt.target_exposure, 0) AS target_exposure,
    IFNULL(pt.top_category, 'category') AS top_category,
    SAFE_DIVIDE(IFNULL(op.proximate_order_days, 0), NULLIF(IFNULL(op.total_order_days, 0), 0)) AS visit_proximate_rate
  FROM partner_master p
  JOIN institution i ON i.farmer_id = p.farmer_id
  LEFT JOIN user_ids u ON u.farmer_id = p.farmer_id
  LEFT JOIN ocp_signals o ON o.farmer_id = p.farmer_id
  LEFT JOIN pog_risk pg ON pg.farmer_id = p.farmer_id
  LEFT JOIN payment_signals ps ON ps.farmer_id = p.farmer_id
  LEFT JOIN revenue_signals r ON r.farmer_id = p.farmer_id
  LEFT JOIN partner_target_score pt ON pt.farmer_id = p.farmer_id
  LEFT JOIN order_proximity op ON op.farmer_id = p.farmer_id
),
sub_pctiles AS (
  SELECT *,
    PERCENT_RANK() OVER (ORDER BY ocp_amount ASC)             * 100 AS p_ocp,
    PERCENT_RANK() OVER (ORDER BY pog_amount ASC)             * 100 AS p_pog,
    PERCENT_RANK() OVER (ORDER BY max_dpd ASC)                * 100 AS p_dpd,
    PERCENT_RANK() OVER (ORDER BY payment_months_6m ASC)      * 100 AS p_pay_f,
    PERCENT_RANK() OVER (ORDER BY days_since_last_payment ASC)* 100 AS p_pay_r,
    PERCENT_RANK() OVER (ORDER BY yoy_decline_pct ASC)        * 100 AS p_yoy,
    PERCENT_RANK() OVER (ORDER BY order_months_6m ASC)        * 100 AS p_ord_f,
    PERCENT_RANK() OVER (ORDER BY days_since_last_order ASC)  * 100 AS p_ord_r,
    PERCENT_RANK() OVER (ORDER BY credit_limit ASC)           * 100 AS p_credit,
    PERCENT_RANK() OVER (ORDER BY target_exposure ASC)        * 100 AS p_target
  FROM assembled
),
bucket_scores AS (
  SELECT *,
    IF(ocp_amount <= 5000 AND pog_amount = 0, 0,
       -- FIX: DPD 25%→15% (high DPD = low recovery probability, should not be over-rewarded)
       -- Redistributed: OCP 30%→35%, Payment Recency 15%→20%
       -- New weights: OCP 35% | DPD 15% | Payment F 20% | Payment R 20% | POG 10%
       0.35*p_ocp + 0.15*p_dpd + 0.20*p_pay_f + 0.20*p_pay_r + 0.10*p_pog) AS collection_pctile,
    IF(ocp_amount > 5000, 0,
       0.40*p_yoy + 0.30*p_ord_f + 0.20*p_ord_r + 0.10*p_credit) AS revenue_pctile
  FROM sub_pctiles
),
tagged AS (
  SELECT *,
    CASE WHEN PERCENT_RANK() OVER (ORDER BY collection_pctile) >= 0.667 THEN 'High'
         WHEN PERCENT_RANK() OVER (ORDER BY collection_pctile) >= 0.333 THEN 'Medium'
         ELSE 'Low' END AS collection_tag,
    CASE WHEN ocp_amount > 5000 THEN 'Blocked'
         WHEN PERCENT_RANK() OVER (ORDER BY revenue_pctile) >= 0.667 THEN 'High'
         WHEN PERCENT_RANK() OVER (ORDER BY revenue_pctile) >= 0.333 THEN 'Medium'
         ELSE 'Low' END AS sales_tag,
    CASE
      WHEN ocp_amount > 5000             THEN 'VISIT'
      WHEN order_months_6m = 0          THEN 'VISIT'
      WHEN visit_proximate_rate > 0     THEN 'VISIT'
      WHEN PERCENT_RANK() OVER (ORDER BY collection_pctile) < 0.333
        AND ocp_amount <= 5000
        AND visit_proximate_rate = 0    THEN 'CALL'
      ELSE                                   'VISIT'
    END AS ev_channel
  FROM bucket_scores
),

-- ── 12. SM VIEW — suppression + relationship percentile ───────
sm_view AS (
  SELECT e.*,
    COALESCE(lv_sm.last_visit_date, lv_any.last_visit_date) AS last_visit_date,
    IF(COALESCE(lv_sm.last_visit_date, lv_any.last_visit_date) IS NULL, 9999,
       DATE_DIFF(CURRENT_DATE(), COALESCE(lv_sm.last_visit_date, lv_any.last_visit_date), DAY)) AS days_since_visit,
    PERCENT_RANK() OVER (ORDER BY
      GREATEST(IF(COALESCE(lv_sm.last_visit_date, lv_any.last_visit_date) IS NULL, 9999,
                  DATE_DIFF(CURRENT_DATE(), COALESCE(lv_sm.last_visit_date, lv_any.last_visit_date), DAY)) - 15, 0)
    ASC) * 100 AS relationship_pctile,
    e.sm_email AS rep_email, 'SM' AS role
  FROM tagged e
  LEFT JOIN last_visit_sm lv_sm ON lv_sm.farmer_id = e.farmer_id AND lv_sm.rep_email = e.sm_email
  LEFT JOIN last_visit_any lv_any ON lv_any.farmer_id = e.farmer_id
  WHERE e.sm_email IS NOT NULL AND NOT STARTS_WITH(e.sm_email, 'vacant')
),
sm_scored AS (
  SELECT *,
    -- Bucket weights: Collection 40% | Revenue 30% | Relationship 15% | Targets 15%
    -- Revenue increased 25%→30%: buying behaviour decline is a stronger forward signal
    -- Relationship remains 15%: cadence maintenance, not primary urgency driver
    ROUND((40*collection_pctile + 30*revenue_pctile + 15*relationship_pctile + 15*p_target) / 100, 2) AS active_score,
    CASE
      WHEN collection_pctile >= revenue_pctile AND collection_pctile >= relationship_pctile AND collection_pctile >= p_target AND (ocp_amount > 0 OR pog_amount > 0)
        THEN CASE
          WHEN p_ocp >= p_pog AND p_ocp >= p_dpd AND p_ocp >= p_pay_f AND p_ocp >= p_pay_r
            THEN CONCAT('Rs.', CAST(ROUND(ocp_amount) AS STRING), ' overdue — ', CAST(max_dpd AS STRING), ' days past due')
          WHEN p_pog >= p_dpd AND p_pog >= p_pay_f AND p_pog >= p_pay_r
            THEN CONCAT('Unsold stock worth ~Rs.', CAST(ROUND(pog_amount) AS STRING), ' hasn''t been confirmed sold')
          WHEN p_dpd >= p_pay_f AND p_dpd >= p_pay_r
            THEN CONCAT('Overdue for ', CAST(max_dpd AS STRING), ' days — debt is ageing, visit urgently')
          WHEN p_pay_f >= p_pay_r
            THEN CONCAT('No payments in ', CAST(6 - payment_months_6m AS STRING), ' of the last 6 months')
          ELSE CONCAT('Last payment was ', CAST(days_since_last_payment AS STRING), ' days ago')
        END
      WHEN revenue_pctile >= relationship_pctile AND revenue_pctile >= p_target
        THEN CASE
          WHEN p_yoy >= p_ord_f AND p_yoy >= p_ord_r AND p_yoy >= p_credit AND yoy_decline_pct > 0.05
            THEN CONCAT('Revenue down ', CAST(ROUND(yoy_decline_pct * 100) AS STRING), '% vs same period last year')
          WHEN p_ord_f >= p_ord_r AND p_ord_f >= p_credit
            THEN CONCAT('Order frequency slowing — active only ', CAST(order_months_6m AS STRING), ' months in last 6')
          WHEN p_ord_r >= p_credit
            THEN CONCAT('No order placed in ', CAST(days_since_last_order AS STRING), ' days')
          ELSE CONCAT('Rs.', CAST(ROUND(credit_limit) AS STRING), ' in unused credit — untapped growth opportunity')
        END
      WHEN relationship_pctile >= p_target
        THEN IF(days_since_visit = 9999, 'Never visited — no recent orders.',
               CONCAT('Not visited in ', CAST(days_since_visit AS STRING), ' days'))
      ELSE CONCAT(territory, ' is behind this month''s ', top_category, ' target — this partner is a major ', top_category, ' seller')
    END AS primary_reason,
    days_since_visit > 15 AS eligible
  FROM sm_view
),

-- ── 13. TM VIEW — suppression + relationship percentile ───────
tm_view AS (
  SELECT e.*,
    COALESCE(lv_tm.last_visit_date, lv_any.last_visit_date) AS last_visit_date,
    IF(COALESCE(lv_tm.last_visit_date, lv_any.last_visit_date) IS NULL, 9999,
       DATE_DIFF(CURRENT_DATE(), COALESCE(lv_tm.last_visit_date, lv_any.last_visit_date), DAY)) AS days_since_visit,
    PERCENT_RANK() OVER (ORDER BY
      GREATEST(IF(COALESCE(lv_tm.last_visit_date, lv_any.last_visit_date) IS NULL, 9999,
                  DATE_DIFF(CURRENT_DATE(), COALESCE(lv_tm.last_visit_date, lv_any.last_visit_date), DAY)) - 45, 0)
    ASC) * 100 AS relationship_pctile,
    e.tm_email AS rep_email, 'TM' AS role
  FROM tagged e
  LEFT JOIN last_visit_tm lv_tm ON lv_tm.farmer_id = e.farmer_id AND lv_tm.rep_email = e.tm_email
  LEFT JOIN last_visit_any lv_any ON lv_any.farmer_id = e.farmer_id
  WHERE e.tm_email IS NOT NULL AND NOT STARTS_WITH(e.tm_email, 'vacant')
),
tm_scored AS (
  SELECT *,
    -- Bucket weights: Collection 40% | Revenue 30% | Relationship 15% | Targets 15%
    -- Revenue increased 25%→30%: buying behaviour decline is a stronger forward signal
    -- Relationship remains 15%: cadence maintenance, not primary urgency driver
    ROUND((40*collection_pctile + 30*revenue_pctile + 15*relationship_pctile + 15*p_target) / 100, 2) AS active_score,
    CASE
      WHEN collection_pctile >= revenue_pctile AND collection_pctile >= relationship_pctile AND collection_pctile >= p_target AND (ocp_amount > 0 OR pog_amount > 0)
        THEN CASE
          WHEN p_ocp >= p_pog AND p_ocp >= p_dpd AND p_ocp >= p_pay_f AND p_ocp >= p_pay_r
            THEN CONCAT('Rs.', CAST(ROUND(ocp_amount) AS STRING), ' overdue — ', CAST(max_dpd AS STRING), ' days past due')
          WHEN p_pog >= p_dpd AND p_pog >= p_pay_f AND p_pog >= p_pay_r
            THEN CONCAT('Unsold stock worth ~Rs.', CAST(ROUND(pog_amount) AS STRING), ' hasn''t been confirmed sold')
          WHEN p_dpd >= p_pay_f AND p_dpd >= p_pay_r
            THEN CONCAT('Overdue for ', CAST(max_dpd AS STRING), ' days — debt is ageing, visit urgently')
          WHEN p_pay_f >= p_pay_r
            THEN CONCAT('No payments in ', CAST(6 - payment_months_6m AS STRING), ' of the last 6 months')
          ELSE CONCAT('Last payment was ', CAST(days_since_last_payment AS STRING), ' days ago')
        END
      WHEN revenue_pctile >= relationship_pctile AND revenue_pctile >= p_target
        THEN CASE
          WHEN p_yoy >= p_ord_f AND p_yoy >= p_ord_r AND p_yoy >= p_credit AND yoy_decline_pct > 0.05
            THEN CONCAT('Revenue down ', CAST(ROUND(yoy_decline_pct * 100) AS STRING), '% vs same period last year')
          WHEN p_ord_f >= p_ord_r AND p_ord_f >= p_credit
            THEN CONCAT('Order frequency slowing — active only ', CAST(order_months_6m AS STRING), ' months in last 6')
          WHEN p_ord_r >= p_credit
            THEN CONCAT('No order placed in ', CAST(days_since_last_order AS STRING), ' days')
          ELSE CONCAT('Rs.', CAST(ROUND(credit_limit) AS STRING), ' in unused credit — untapped growth opportunity')
        END
      WHEN relationship_pctile >= p_target
        THEN IF(days_since_visit = 9999, 'Never visited — no recent orders.',
               CONCAT('Not visited in ', CAST(days_since_visit AS STRING), ' days'))
      ELSE CONCAT(territory, ' is behind this month''s ', top_category, ' target — this partner is a major ', top_category, ' seller')
    END AS primary_reason,
    days_since_visit > 45 AS eligible
  FROM tm_view
),

-- ── 14. CHURNED RECOVERY (INACTIVE + OCP > 5k) ───────────────
partner_inactive AS (
  SELECT SAFE_CAST(farmer_id AS INT64) AS farmer_id, name,
    UPPER(TRIM(territory)) AS territory,
    LOWER(TRIM(sm)) AS sm_email, LOWER(TRIM(tm)) AS tm_email
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status = 'INACTIVE' AND farmer_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY farmer_id ORDER BY (cluster IS NULL)) = 1
),
churned_debits AS (
  SELECT cf.farmer_id,
    SUM(GREATEST(d.amount + IFNULL(d.interest_amount,0) - IFNULL(r.reconciled_amount,0), 0)) AS pending_amount,
    MAX(IF((d.amount + IFNULL(d.interest_amount,0) - IFNULL(r.reconciled_amount,0)) > 0,
        DATE_DIFF(CURRENT_DATE(), DATE(d.due_date), DAY), 0)) AS max_dpd
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` d
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = d.wallet_user_id
  LEFT JOIN recon r ON r.reconciled_for_id = d.id
  WHERE d.cancelled = 0 AND d.transaction_type = 0 AND d.reason_id NOT IN (2)
  GROUP BY cf.farmer_id
),

-- ── 15. ONBOARDING (zoho_leads) ───────────────────────────────
rep_directory AS (
  SELECT DISTINCT LOWER(TRIM(sm)) AS email, 'SM' AS role
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE sm IS NOT NULL AND NOT STARTS_WITH(LOWER(TRIM(sm)), 'vacant')
  UNION DISTINCT
  SELECT DISTINCT LOWER(TRIM(tm)), 'TM'
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE tm IS NOT NULL AND NOT STARTS_WITH(LOWER(TRIM(tm)), 'vacant')
),
onboarding_leads AS (
  SELECT reference_customer_id AS farmer_id,
    CAST(Contact_name AS STRING) AS partner_name,
    LOWER(TRIM(Lead_Owner)) AS lead_owner_email,
    stage, UPPER(TRIM(territory)) AS territory
  FROM `agrostar-data.optimized_reports_data.zoho_leads`
  WHERE reference_customer_id IS NOT NULL
    AND (stage NOT LIKE '%Closed Won%' OR stage = 'Closed Won but Cheque Pending')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY reference_customer_id
    ORDER BY IF(stage LIKE '8%' OR stage = 'Closed Won but Cheque Pending', 0, 1), Final_sd_date DESC
  ) = 1
),

-- ── 16. BUILD WEEKLY POOL OF 36 VISITS PER REP ───────────────
-- ALL 36 slots are physical visits. No calls in the primary pool.
-- Fill order: Active first → Churned fills gaps (max 4) → Onboarding fills remainder (max 4)
--
-- Logic per rep:
--   Step 1: How many active-eligible partners exist?
--   Step 2: remaining = 36 - active_eligible (floored at 0)
--   Step 3: churned_take = min(remaining, 4, churned_available)
--   Step 4: onboarding_take = min(remaining - churned_take, 4, onboarding_available)
--   Step 5: active_slots = min(active_eligible, 36 - churned_take - onboarding_take)
--
-- Calls: separate output. App surfaces top 2 calls ONLY after rep completes
-- 24 physical visits for the week (tracked from visit logs in the app layer).
-- Step 1: Count how many eligible active partners exist per rep
active_eligible_sm AS (
  SELECT sm_email AS rep_email, COUNT(*) AS n FROM sm_scored WHERE eligible GROUP BY sm_email
),
active_eligible_tm AS (
  SELECT tm_email AS rep_email, COUNT(*) AS n FROM tm_scored WHERE eligible GROUP BY tm_email
),
-- Step 2: Churned available per rep (capped at 4)
churned_available_sm AS (
  SELECT sm_email AS rep_email, LEAST(COUNT(*), 4) AS n
  FROM partner_inactive pi JOIN churned_debits cd ON cd.farmer_id=pi.farmer_id
  WHERE pi.sm_email IS NOT NULL AND NOT STARTS_WITH(pi.sm_email,'vacant') AND cd.pending_amount>5000
  GROUP BY pi.sm_email
),
churned_available_tm AS (
  SELECT tm_email AS rep_email, LEAST(COUNT(*), 4) AS n
  FROM partner_inactive pi JOIN churned_debits cd ON cd.farmer_id=pi.farmer_id
  WHERE pi.tm_email IS NOT NULL AND NOT STARTS_WITH(pi.tm_email,'vacant') AND cd.pending_amount>5000
  GROUP BY pi.tm_email
),
-- Step 3: Onboarding available per rep (capped at 4)
onboarding_available AS (
  SELECT rd.email AS rep_email, LEAST(COUNT(*), 4) AS n
  FROM onboarding_leads ol JOIN rep_directory rd ON rd.email=ol.lead_owner_email
  GROUP BY rd.email
),
-- Step 4: Compute fill slots per rep
-- FIX: Fill order changed — Onboarding fills before Churned
-- Rationale: About-to-close leads have higher expected value per visit (~9x) than
-- 400+ DPD churned partners. Revenue opportunity > debt recovery when active is short.
-- New order: Active → Onboarding (about-to-close first, max 4) → Churned (max 4)
slot_allocation_sm AS (
  SELECT
    ae.rep_email,
    ae.n AS active_eligible,
    IFNULL(ca.n, 0) AS churned_avail,
    IFNULL(oa.n, 0) AS onboard_avail,
    -- ONBOARDING fills first when active is short (max 4)
    LEAST(GREATEST(36 - ae.n, 0), IFNULL(oa.n, 0)) AS onboard_take,
    -- CHURNED fills what's left after onboarding (max 4)
    LEAST(GREATEST(36 - ae.n - LEAST(GREATEST(36-ae.n,0), IFNULL(oa.n,0)), 0), IFNULL(ca.n,0)) AS churned_take
  FROM active_eligible_sm ae
  LEFT JOIN churned_available_sm ca ON ca.rep_email=ae.rep_email
  LEFT JOIN onboarding_available oa ON oa.rep_email=ae.rep_email
),
slot_allocation_tm AS (
  SELECT
    ae.rep_email,
    ae.n AS active_eligible,
    IFNULL(ca.n, 0) AS churned_avail,
    IFNULL(oa.n, 0) AS onboard_avail,
    LEAST(GREATEST(36 - ae.n, 0), IFNULL(oa.n, 0)) AS onboard_take,
    LEAST(GREATEST(36 - ae.n - LEAST(GREATEST(36-ae.n,0), IFNULL(oa.n,0)), 0), IFNULL(ca.n,0)) AS churned_take
  FROM active_eligible_tm ae
  LEFT JOIN churned_available_tm ca ON ca.rep_email=ae.rep_email
  LEFT JOIN onboarding_available oa ON oa.rep_email=ae.rep_email
),
active_sm_pool AS (
  SELECT
    s.farmer_id, s.partner_name, s.state, s.district, s.territory, s.cluster,
    s.sm_email, s.tm_email, s.sh_email, s.cm_email,
    s.reference_customer_id, s.user_id, s.mobile_number,
    s.lat, s.lng, s.addr_pincode, s.addr_state, s.addr_district, s.geo_key,
    s.ocp_amount, s.max_dpd, s.pog_amount, s.yoy_decline_pct, s.order_months_6m,
    s.days_since_last_order, s.days_since_visit, s.last_visit_date, s.payment_months_6m,
    s.target_exposure, s.top_category, s.credit_limit,
    s.collection_pctile, s.revenue_pctile, s.relationship_pctile, s.p_target AS target_pctile,
    s.active_score, s.collection_tag, s.sales_tag, s.ev_channel, s.primary_reason,
    s.sm_email AS rep_email, 'SM' AS role, 'Active' AS track
  FROM sm_scored s
  JOIN slot_allocation_sm sa ON sa.rep_email = s.sm_email
  WHERE s.eligible
  -- Active slots = 36 - churned_take - onboard_take
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.sm_email ORDER BY s.active_score DESC)
    <= 36 - sa.churned_take - sa.onboard_take
),
active_tm_pool AS (
  SELECT
    s.farmer_id, s.partner_name, s.state, s.district, s.territory, s.cluster,
    s.sm_email, s.tm_email, s.sh_email, s.cm_email,
    s.reference_customer_id, s.user_id, s.mobile_number,
    s.lat, s.lng, s.addr_pincode, s.addr_state, s.addr_district, s.geo_key,
    s.ocp_amount, s.max_dpd, s.pog_amount, s.yoy_decline_pct, s.order_months_6m,
    s.days_since_last_order, s.days_since_visit, s.last_visit_date, s.payment_months_6m,
    s.target_exposure, s.top_category, s.credit_limit,
    s.collection_pctile, s.revenue_pctile, s.relationship_pctile, s.p_target AS target_pctile,
    s.active_score, s.collection_tag, s.sales_tag, s.ev_channel, s.primary_reason,
    s.tm_email AS rep_email, 'TM' AS role, 'Active' AS track
  FROM tm_scored s
  JOIN slot_allocation_tm sa ON sa.rep_email = s.tm_email
  WHERE s.eligible
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.tm_email ORDER BY s.active_score DESC)
    <= 36 - sa.churned_take - sa.onboard_take
),
churned_sm_pool AS (
  SELECT
    pi.farmer_id, pi.name AS partner_name,
    CAST(NULL AS STRING) AS state, CAST(NULL AS STRING) AS district,
    pi.territory, CAST(NULL AS STRING) AS cluster,
    pi.sm_email, pi.tm_email, CAST(NULL AS STRING) AS sh_email, CAST(NULL AS STRING) AS cm_email,
    pi.farmer_id AS reference_customer_id, CAST(NULL AS INT64) AS user_id,
    CAST(NULL AS STRING) AS mobile_number,
    CAST(NULL AS FLOAT64) AS lat, CAST(NULL AS FLOAT64) AS lng,
    CAST(NULL AS STRING) AS addr_pincode, CAST(NULL AS STRING) AS addr_state, CAST(NULL AS STRING) AS addr_district,
    CAST(NULL AS STRING) AS geo_key,
    cd.pending_amount AS ocp_amount, cd.max_dpd, 0.0 AS pog_amount,
    0.0 AS yoy_decline_pct, 0 AS order_months_6m, 9999 AS days_since_last_order,
    9999 AS days_since_visit, CAST(NULL AS DATE) AS last_visit_date,
    0 AS payment_months_6m, 0.0 AS target_exposure,
    'category' AS top_category, 0.0 AS credit_limit,
    100.0 AS collection_pctile, 0.0 AS revenue_pctile, 0.0 AS relationship_pctile, 0.0 AS target_pctile,
    ROUND(cd.pending_amount, 2) AS active_score, 'High' AS collection_tag, 'Blocked' AS sales_tag,
    'VISIT' AS ev_channel,
    CONCAT('Inactive — still owes Rs.', CAST(ROUND(cd.pending_amount) AS STRING), ' — recovery visit, not a sales visit') AS primary_reason,
    pi.sm_email AS rep_email, 'SM' AS role, 'Churned' AS track
  FROM partner_inactive pi
  JOIN churned_debits cd ON cd.farmer_id = pi.farmer_id
  WHERE pi.sm_email IS NOT NULL AND NOT STARTS_WITH(pi.sm_email, 'vacant')
    AND cd.pending_amount > 5000
  -- Churned fills AFTER onboarding in new fill order (churned_take already accounts for this)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY pi.sm_email ORDER BY cd.pending_amount DESC)
    <= IFNULL((SELECT churned_take FROM slot_allocation_sm WHERE rep_email=pi.sm_email), 0)
),
churned_tm_pool AS (
  SELECT
    pi.farmer_id, pi.name,
    CAST(NULL AS STRING), CAST(NULL AS STRING), pi.territory, CAST(NULL AS STRING),
    pi.sm_email, pi.tm_email, CAST(NULL AS STRING), CAST(NULL AS STRING),
    pi.farmer_id, CAST(NULL AS INT64), CAST(NULL AS STRING),
    CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
    cd.pending_amount, cd.max_dpd, 0.0, 0.0, 0, 9999, 9999, CAST(NULL AS DATE), 0, 0.0, 'category', 0.0,
    100.0, 0.0, 0.0, 0.0,
    ROUND(cd.pending_amount, 2), 'High', 'Blocked', 'VISIT',
    CONCAT('Inactive — still owes Rs.', CAST(ROUND(cd.pending_amount) AS STRING), ' — recovery visit, not a sales visit'),
    pi.tm_email, 'TM', 'Churned'
  FROM partner_inactive pi
  JOIN churned_debits cd ON cd.farmer_id = pi.farmer_id
  WHERE pi.tm_email IS NOT NULL AND NOT STARTS_WITH(pi.tm_email, 'vacant')
    AND cd.pending_amount > 5000
  QUALIFY ROW_NUMBER() OVER (PARTITION BY pi.tm_email ORDER BY cd.pending_amount DESC)
    <= IFNULL((SELECT churned_take FROM slot_allocation_tm WHERE rep_email=pi.tm_email), 0)
),
onboarding_pool AS (
  SELECT
    ol.farmer_id, ol.partner_name,
    CAST(NULL AS STRING), CAST(NULL AS STRING), ol.territory, CAST(NULL AS STRING),
    IF(rd.role='SM', rd.email, NULL) AS sm_email,
    IF(rd.role='TM', rd.email, NULL) AS tm_email,
    CAST(NULL AS STRING), CAST(NULL AS STRING),
    ol.farmer_id, CAST(NULL AS INT64), CAST(NULL AS STRING),
    CAST(NULL AS FLOAT64), CAST(NULL AS FLOAT64),
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
    0.0 AS ocp_amount, 0 AS max_dpd, 0.0 AS pog_amount, 0.0, 0, 9999,
    IF(lv.last_visit_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), lv.last_visit_date, DAY)) AS days_since_visit,
    lv.last_visit_date,
    0, 0.0, 'category', 0.0,
    0.0, 0.0, 0.0, 0.0,
    CAST(IF(ol.stage LIKE '8%' OR ol.stage = 'Closed Won but Cheque Pending', 1000, 0)
       + LEAST(IF(lv.last_visit_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), lv.last_visit_date, DAY)), 999)
       AS FLOAT64) AS active_score,
    'Low' AS collection_tag, 'Low' AS sales_tag,
    IF(ol.stage LIKE '8%' OR ol.stage = 'Closed Won but Cheque Pending', 'VISIT', 'CALL') AS ev_channel,
    IF(ol.stage LIKE '8%' OR ol.stage = 'Closed Won but Cheque Pending',
       'Almost ready to become a customer — help push them over the line',
       'New partner, not yet onboarded — help them place their first order') AS primary_reason,
    rd.email AS rep_email, rd.role, 'Onboarding' AS track
  FROM onboarding_leads ol
  JOIN rep_directory rd ON rd.email = ol.lead_owner_email
  LEFT JOIN last_visit_any lv ON lv.farmer_id = ol.farmer_id
  -- FIX: Onboarding now fills BEFORE Churned — about-to-close leads have higher expected value
  -- Within onboarding: about-to-close (stage 8x) always sorts first
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY rd.email
    ORDER BY IF(ol.stage LIKE '8%' OR ol.stage = 'Closed Won but Cheque Pending', 0, 1),
             IF(lv.last_visit_date IS NULL, 9999, DATE_DIFF(CURRENT_DATE(), lv.last_visit_date, DAY)) ASC
  ) <= IFNULL((SELECT onboard_take FROM slot_allocation_sm WHERE rep_email=rd.email),
              IFNULL((SELECT onboard_take FROM slot_allocation_tm WHERE rep_email=rd.email), 0))
),

-- ── 17. COMBINED WEEKLY POOL (36 per rep) ────────────────────
weekly_pool AS (
  SELECT * FROM active_sm_pool
  UNION ALL SELECT * FROM active_tm_pool
  UNION ALL SELECT * FROM churned_sm_pool
  UNION ALL SELECT * FROM churned_tm_pool
  UNION ALL SELECT * FROM onboarding_pool
),

-- ── 18. VISIT POOL + CALL POOL ───────────────────────────────
-- VISIT pool: ALL weekly_pool partners EV-routed to VISIT (Active + Churned + Onboarding)
--             Cadence suppression already applied in sm_scored/tm_scored (days_since_visit > 15/45)
--             All visit partners geo-clustered together regardless of track
--
-- CALL pool: two sources:
--   1. Partners in weekly pool EV-routed to CALL (self-sufficient, healthy signal)
--   2. Recently-visited partners (within cadence, suppressed from VISIT) — cadence only
--      blocks visits, NOT calls. A partner visited 10 days ago can still receive a call.
visit_pool AS (SELECT * FROM weekly_pool WHERE ev_channel = 'VISIT'),

-- Recently-visited SM partners (within 15d cadence) — suppressed from VISIT but valid for CALL
recently_visited_sm AS (
  SELECT
    s.farmer_id, s.partner_name, s.state, s.district, s.territory, s.cluster,
    s.sm_email, s.tm_email, s.sh_email, s.cm_email,
    s.reference_customer_id, s.user_id, s.mobile_number,
    s.lat, s.lng, s.addr_pincode, s.addr_state, s.addr_district, s.geo_key,
    s.ocp_amount, s.max_dpd, s.pog_amount, s.yoy_decline_pct, s.order_months_6m,
    s.days_since_last_order, s.days_since_visit, s.last_visit_date, s.payment_months_6m,
    s.target_exposure, s.top_category, s.credit_limit,
    s.collection_pctile, s.revenue_pctile, s.relationship_pctile, s.p_target AS target_pctile,
    s.active_score, s.collection_tag, s.sales_tag, 'CALL' AS ev_channel,
    CONCAT('Visited ', CAST(s.days_since_visit AS STRING), ' days ago — call to stay connected') AS primary_reason,
    s.sm_email AS rep_email, 'SM' AS role, 'Active' AS track
  FROM sm_scored s
  WHERE NOT s.eligible  -- within cadence (days_since_visit <= 15)
    AND s.ev_channel = 'CALL'  -- only call-eligible partners (not OCP/dormant)
),
recently_visited_tm AS (
  SELECT
    s.farmer_id, s.partner_name, s.state, s.district, s.territory, s.cluster,
    s.sm_email, s.tm_email, s.sh_email, s.cm_email,
    s.reference_customer_id, s.user_id, s.mobile_number,
    s.lat, s.lng, s.addr_pincode, s.addr_state, s.addr_district, s.geo_key,
    s.ocp_amount, s.max_dpd, s.pog_amount, s.yoy_decline_pct, s.order_months_6m,
    s.days_since_last_order, s.days_since_visit, s.last_visit_date, s.payment_months_6m,
    s.target_exposure, s.top_category, s.credit_limit,
    s.collection_pctile, s.revenue_pctile, s.relationship_pctile, s.p_target AS target_pctile,
    s.active_score, s.collection_tag, s.sales_tag, 'CALL' AS ev_channel,
    CONCAT('Visited ', CAST(s.days_since_visit AS STRING), ' days ago — call to stay connected') AS primary_reason,
    s.tm_email AS rep_email, 'TM' AS role, 'Active' AS track
  FROM tm_scored s
  WHERE NOT s.eligible  -- within cadence (days_since_visit <= 45)
    AND s.ev_channel = 'CALL'
),
call_pool AS (
  -- Partners EV-routed to CALL from the weekly pool
  SELECT * FROM weekly_pool WHERE ev_channel = 'CALL'
  UNION ALL
  -- Recently-visited partners (within cadence) — valid for calls, not visits
  SELECT * FROM recently_visited_sm
  WHERE farmer_id NOT IN (SELECT farmer_id FROM weekly_pool)
  UNION ALL
  SELECT * FROM recently_visited_tm
  WHERE farmer_id NOT IN (SELECT farmer_id FROM weekly_pool)
),

-- ── 19. GEO-CLUSTERING — ALL VISIT POOL PARTNERS ─────────────
-- Clusters ALL visit partners (Active + Churned + Onboarding) by proximity.
-- No track-based exclusions — if a partner needs a visit, they enter the cluster.
-- Multiple clusters formed across the full visit pool (one per day of the week).
-- Rep assigns each cluster to a day. System does not prescribe which day.
geo_clustered AS (
  SELECT *,
    CAST(CEIL(ROW_NUMBER() OVER (PARTITION BY rep_email ORDER BY COALESCE(geo_key, 'ZZZZ')) / 4.0) AS INT64) AS cluster_id
  FROM visit_pool
),
cluster_agg AS (
  SELECT rep_email, cluster_id,
    COUNT(*) AS cluster_size, SUM(active_score) AS cluster_score, MAX(active_score) AS max_score,
    AVG(IF(lat IS NOT NULL AND lat != 0, lat, NULL)) AS c_lat,
    AVG(IF(lng IS NOT NULL AND lng != 0, lng, NULL)) AS c_lng
  FROM geo_clustered GROUP BY rep_email, cluster_id
),
-- 120km cap: compute max distance from centroid per cluster before selecting best
-- Any cluster where a partner is >120km from the group centroid is disqualified
-- Prevents impossible daily routes (geohash boundary edge cases)
cluster_max_dist AS (
  SELECT
    g.rep_email, g.cluster_id,
    MAX(IF(g.lat IS NOT NULL AND g.lat != 0 AND g.lng IS NOT NULL AND g.lng != 0
           AND ca.c_lat IS NOT NULL AND ca.c_lng IS NOT NULL,
           ST_DISTANCE(ST_GEOGPOINT(g.lng, g.lat),
                       ST_GEOGPOINT(ca.c_lng, ca.c_lat)) / 1000,
           0)) AS max_km_from_centroid
  FROM geo_clustered g
  JOIN cluster_agg ca ON ca.rep_email = g.rep_email AND ca.cluster_id = g.cluster_id
  GROUP BY g.rep_email, g.cluster_id
),
best_cluster AS (
  SELECT ca.rep_email, ca.cluster_id, ca.cluster_size, ca.c_lat, ca.c_lng
  FROM cluster_agg ca
  JOIN cluster_max_dist cd ON cd.rep_email = ca.rep_email AND cd.cluster_id = ca.cluster_id
  WHERE cd.max_km_from_centroid <= 120  -- disqualify clusters spanning >120km
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY ca.rep_email ORDER BY ca.cluster_score DESC, ca.max_score DESC
  ) = 1
),
visit_members AS (
  SELECT g.*, 'original' AS cluster_source
  FROM geo_clustered g JOIN best_cluster bc ON bc.rep_email = g.rep_email AND bc.cluster_id = g.cluster_id
  UNION ALL
  SELECT g.*, 'filled' AS cluster_source
  FROM geo_clustered g JOIN best_cluster bc ON bc.rep_email = g.rep_email
  WHERE bc.cluster_size < 4 AND ABS(g.cluster_id - bc.cluster_id) = 1 AND g.cluster_id != bc.cluster_id
    AND bc.c_lat IS NOT NULL AND bc.c_lng IS NOT NULL AND g.lat IS NOT NULL AND g.lng IS NOT NULL
    AND ST_DISTANCE(ST_GEOGPOINT(g.lng, g.lat), ST_GEOGPOINT(bc.c_lng, bc.c_lat)) / 1000 < 15
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY g.rep_email
    ORDER BY ST_DISTANCE(ST_GEOGPOINT(g.lng, g.lat), ST_GEOGPOINT(bc.c_lng, bc.c_lat))
  ) <= (4 - bc.cluster_size)
),

-- ── 20. DAILY VISIT GROUP + CALL OVERLAY ─────────────────────
-- visit_members = today's best geo-cluster of 4 from the weekly 36
-- call_output = top 2 call candidates (separate pool, not in weekly 36)
--   App logic: show calls ONLY after rep has completed 24 physical visits this week
--   SQL always outputs top 2 calls; app gates their display on weekly visit count
visit_counts AS (SELECT rep_email, COUNT(*) AS visit_count FROM visit_members GROUP BY rep_email),
-- If today's best cluster < 4, promote next-best visit from weekly pool (not from call pool)
promoted_to_visit AS (
  SELECT g.*, 'promoted' AS cluster_source,
    ROW_NUMBER() OVER (PARTITION BY g.rep_email ORDER BY g.active_score DESC) AS promo_rank,
    vc.visit_count
  FROM geo_clustered g JOIN visit_counts vc ON vc.rep_email = g.rep_email
  WHERE g.farmer_id NOT IN (SELECT farmer_id FROM visit_members) AND vc.visit_count < 4
  QUALIFY ROW_NUMBER() OVER (PARTITION BY g.rep_email ORDER BY g.active_score DESC)
    <= GREATEST(4 - vc.visit_count, 0)
),
-- Top 2 calls — entirely separate from the weekly 36 visit pool
-- Sourced from call_pool (EV-routed CALL partners not in weekly 36)
call_output AS (
  SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.rep_email ORDER BY c.active_score DESC) AS call_rank
  FROM call_pool c
  WHERE c.farmer_id NOT IN (SELECT farmer_id FROM visit_members)
    AND c.farmer_id NOT IN (SELECT farmer_id FROM promoted_to_visit)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY c.rep_email ORDER BY c.active_score DESC) <= 2
)

-- ── 21. FINAL OUTPUT ─────────────────────────────────────────
SELECT
  CURRENT_DATE() AS list_date,
  v.rep_email, v.role, v.track,
  v.sm_email, sm_ec.mobile_number AS sm_mobile_number, sm_ec.full_name AS sm_full_name,
  v.tm_email, tm_ec.mobile_number AS tm_mobile_number, tm_ec.full_name AS tm_full_name,
  v.sh_email, v.cm_email,
  v.farmer_id, v.reference_customer_id, v.user_id,
  v.mobile_number AS partner_mobile_number, v.partner_name,
  v.state, v.district, v.territory, v.cluster, v.addr_pincode,
  'VISIT' AS channel,
  ROW_NUMBER() OVER (PARTITION BY v.rep_email ORDER BY v.active_score DESC) AS priority,
  ROUND(v.active_score, 2) AS active_score,
  ROUND(v.collection_pctile, 1) AS collection_pctile,
  ROUND(v.revenue_pctile, 1) AS revenue_pctile,
  ROUND(v.relationship_pctile, 1) AS relationship_pctile,
  ROUND(v.target_pctile, 1) AS target_pctile,
  v.collection_tag, v.sales_tag,
  ROUND(v.ocp_amount, 0) AS ocp_amount_rs,
  ROUND(v.pog_amount, 0) AS pog_risk_rs,
  v.max_dpd, v.days_since_visit, v.last_visit_date,
  ROUND(v.yoy_decline_pct * 100, 1) AS yoy_decline_pct,
  v.order_months_6m, v.days_since_last_order, v.payment_months_6m,
  v.geo_key AS zone, v.cluster_source, v.lat, v.lng,
  v.primary_reason
FROM visit_members v
LEFT JOIN employee_contacts sm_ec ON sm_ec.email = v.sm_email
LEFT JOIN employee_contacts tm_ec ON tm_ec.email = v.tm_email

UNION ALL

SELECT
  CURRENT_DATE(), p.rep_email, p.role, p.track,
  p.sm_email, sm_ec2.mobile_number, sm_ec2.full_name,
  p.tm_email, tm_ec2.mobile_number, tm_ec2.full_name,
  p.sh_email, p.cm_email,
  p.farmer_id, p.reference_customer_id, p.user_id,
  p.mobile_number, p.partner_name,
  p.state, p.district, p.territory, p.cluster, p.addr_pincode,
  'VISIT', 4 + p.promo_rank,
  ROUND(p.active_score,2), ROUND(p.collection_pctile,1), ROUND(p.revenue_pctile,1),
  ROUND(p.relationship_pctile,1), ROUND(p.target_pctile,1),
  p.collection_tag, p.sales_tag,
  ROUND(p.ocp_amount,0), ROUND(p.pog_amount,0), p.max_dpd, p.days_since_visit, p.last_visit_date,
  ROUND(p.yoy_decline_pct*100,1), p.order_months_6m, p.days_since_last_order, p.payment_months_6m,
  'promoted', 'promoted', p.lat, p.lng, p.primary_reason
FROM promoted_to_visit p
LEFT JOIN employee_contacts sm_ec2 ON sm_ec2.email = p.sm_email
LEFT JOIN employee_contacts tm_ec2 ON tm_ec2.email = p.tm_email

UNION ALL

SELECT
  CURRENT_DATE(), c.rep_email, c.role, c.track,
  c.sm_email, sm_ec3.mobile_number, sm_ec3.full_name,
  c.tm_email, tm_ec3.mobile_number, tm_ec3.full_name,
  c.sh_email, c.cm_email,
  c.farmer_id, c.reference_customer_id, c.user_id,
  c.mobile_number, c.partner_name,
  c.state, c.district, c.territory, c.cluster, c.addr_pincode,
  'CALL', c.call_rank,
  ROUND(c.active_score,2), ROUND(c.collection_pctile,1), ROUND(c.revenue_pctile,1),
  ROUND(c.relationship_pctile,1), ROUND(c.target_pctile,1),
  c.collection_tag, c.sales_tag,
  ROUND(c.ocp_amount,0), ROUND(c.pog_amount,0), c.max_dpd, c.days_since_visit, c.last_visit_date,
  ROUND(c.yoy_decline_pct*100,1), c.order_months_6m, c.days_since_last_order, c.payment_months_6m,
  CAST(NULL AS STRING), 'independent', c.lat, c.lng, c.primary_reason
FROM call_output c
LEFT JOIN employee_contacts sm_ec3 ON sm_ec3.email = c.sm_email
LEFT JOIN employee_contacts tm_ec3 ON tm_ec3.email = c.tm_email

ORDER BY rep_email, role, track, channel DESC, priority
