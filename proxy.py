"""
DVS Dashboard Proxy — runs BigQuery queries server-side using ADC.
Start: python3 proxy.py
Requires: pip install -r requirements.txt
Auth:    gcloud auth application-default login
"""

import datetime
import os
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

PROJECT = 'agrostar-data'
DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), 'dvs_dashboard.html')

# Lazy BigQuery client — initialised on first query so server starts even without ADC
_bq_client = None

def get_client():
    global _bq_client
    if _bq_client is None:
        from google.cloud import bigquery
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client

# ── Queries (mirrors buildSummaryQuery / buildTrendQuery in the dashboard) ──

SUMMARY_SQL = """
WITH orders AS (
  SELECT DISTINCT
    o.sales_order_id,
    o.retail_store_code,
    INITCAP(LOWER(TRIM(sa.state))) AS state
  FROM `agrostar-data.prod_db_views.order_management_order` o
  JOIN `agrostar-data.prod_db_views.csr_shippingaddress` sa ON sa.id = o.shipping_address_id
  WHERE DATE(o.created_on) BETWEEN '{from_date}' AND '{to_date}'
    AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
    AND LOWER(COALESCE(o.order_type,'')) NOT LIKE '%offline%'
    AND o.unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
    AND o.status NOT IN ('MOB_APP_UNVERIFIED')
    AND o.status NOT LIKE 'edited%'
    AND o.unicommerce_status NOT LIKE 'edited%'
),
dvs_orders AS (
  SELECT o.sales_order_id, o.state
  FROM orders o
  WHERE o.retail_store_code IS NOT NULL AND o.retail_store_code != ''
),
gmv AS (
  SELECT oi.order_id, SUM(oi.total_price) AS gmv
  FROM `agrostar-data.prod_db_views.order_management_orderitem` oi
  WHERE oi.order_id IN (SELECT sales_order_id FROM dvs_orders)
  GROUP BY 1
),
rerouted AS (
  SELECT DISTINCT CAST(rl.order_id AS STRING) AS oid
  FROM `agrostar-data.prod_db_views.order_management_orderreroutinglogs` rl
  WHERE CAST(rl.order_id AS STRING) IN (
    SELECT CAST(sales_order_id AS STRING)
    FROM orders
    WHERE (retail_store_code IS NULL OR retail_store_code = '')
  )
),
delivered AS (
  SELECT DISTINCT h.order_id
  FROM `agrostar-data.prod_db_views.order_management_orderhistorymeta` h
  WHERE h.status = 'DELIVERED'
    AND h.order_id IN (SELECT sales_order_id FROM dvs_orders)
)
SELECT
  COALESCE(o.state, 'Unknown')              AS state,
  COUNT(DISTINCT o.sales_order_id)          AS b2c_demand,
  COUNT(DISTINCT d2.sales_order_id)         AS dvs_fulfilled,
  COUNT(DISTINCT r.oid)                     AS rerouted,
  ROUND(COALESCE(SUM(g.gmv),0))             AS dvs_gmv,
  COUNT(DISTINCT del.order_id)              AS delivered,
  COUNT(DISTINCT CASE WHEN o.retail_store_code IS NOT NULL
    AND o.retail_store_code != '' THEN o.retail_store_code END) AS active_stores
FROM orders o
LEFT JOIN dvs_orders   d2  ON d2.sales_order_id = o.sales_order_id
LEFT JOIN gmv           g  ON g.order_id = o.sales_order_id
LEFT JOIN rerouted      r  ON r.oid = CAST(o.sales_order_id AS STRING)
LEFT JOIN delivered   del  ON del.order_id = o.sales_order_id
GROUP BY 1
ORDER BY b2c_demand DESC
"""

TREND_SQL = """
WITH orders AS (
  SELECT DISTINCT
    o.sales_order_id,
    o.retail_store_code,
    DATE_TRUNC(DATE(o.created_on), WEEK(MONDAY)) AS wk
  FROM `agrostar-data.prod_db_views.order_management_order` o
  JOIN `agrostar-data.prod_db_views.csr_shippingaddress` sa ON sa.id = o.shipping_address_id
  WHERE DATE(o.created_on) BETWEEN '{from_date}' AND '{to_date}'
    AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
    AND LOWER(COALESCE(o.order_type,'')) NOT LIKE '%offline%'
    AND o.unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
    AND o.status NOT IN ('MOB_APP_UNVERIFIED')
    AND o.status NOT LIKE 'edited%'
    AND o.unicommerce_status NOT LIKE 'edited%'
)
SELECT
  wk,
  COUNT(DISTINCT sales_order_id)                                                           AS b2c_total,
  COUNT(DISTINCT CASE WHEN retail_store_code IS NOT NULL AND retail_store_code != ''
                      THEN sales_order_id END)                                             AS dvs_fulfilled
FROM orders
GROUP BY 1
ORDER BY 1
"""


FIELD_PARTNER_SQL = """
WITH
partners AS (
  SELECT
    farmer_id,
    COALESCE(name, 'Unknown') AS name,
    COALESCE(territory, 'Unknown') AS territory,
    COALESCE(cluster, 'Unknown') AS cluster,
    COALESCE(revised_state, 'Unknown') AS state,
    COALESCE(sm, '') AS sm,
    COALESCE(tm, '') AS tm,
    status,
    first_order_date
  FROM `agrostar-data.offline_team.okr_data_live`
),
visits_period_raw AS (
  SELECT
    LOWER(TRIM(email)) AS email,
    date AS visit_date,
    date_time AS visit_ts,
    CAST(store_id AS STRING) AS store_id
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date BETWEEN '{from_date}' AND '{to_date}'
  UNION ALL
  SELECT
    LOWER(TRIM(email)) AS email,
    DATE(date) AS visit_date,
    updatedOn AS visit_ts,
    storeId AS store_id
  FROM `agrostar-data.prod_db_views.visit`
  WHERE DATE(date) BETWEEN '{from_date}' AND '{to_date}'
),
visits_period AS (
  SELECT * EXCEPT(rn)
  FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC
    ) AS rn FROM visits_period_raw
  ) WHERE rn = 1
),
partner_visits AS (
  SELECT
    SAFE_CAST(store_id AS INT64) AS partner_id,
    COUNT(*) AS visits_period,
    COUNTIF(visit_date = CURRENT_DATE()) AS visits_today
  FROM visits_period
  GROUP BY 1
),
visits_hist_raw AS (
  SELECT CAST(store_id AS STRING) AS store_id, date AS visit_date, date_time AS visit_ts
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
  UNION ALL
  SELECT storeId AS store_id, DATE(date) AS visit_date, updatedOn AS visit_ts
  FROM `agrostar-data.prod_db_views.visit`
  WHERE DATE(date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
),
visits_hist AS (
  SELECT * EXCEPT(rn)
  FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY store_id, visit_date ORDER BY visit_ts DESC
    ) AS rn FROM visits_hist_raw
  ) WHERE rn = 1
),
partner_last_visit AS (
  SELECT
    SAFE_CAST(store_id AS INT64) AS partner_id,
    MAX(visit_date) AS last_visit_date
  FROM visits_hist
  GROUP BY 1
),
revenue AS (
  SELECT
    o.owner_id AS partner_id,
    ROUND(SUM(inv.TotalPrice), 0) AS revenue_period
  FROM `agrostar-data.pristine_wms_views.invoiced_report` inv
  JOIN `agrostar-data.prod_db_views.order_management_order` o
    ON CAST(o.unicommerce_id AS STRING) = inv.DisplayOrderCode
  WHERE DATE(inv.CreatedOn) BETWEEN '{from_date}' AND '{to_date}'
    AND inv.line_status != 'CANCELLED'
    AND inv.is_return = 0
    AND o.initiating_source LIKE 'B2B%'
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.status, '')), r'cancelled|mob_app_unverified|error|payment_pending|edited')
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.unicommerce_status, '')), r'cancelled|mob_app_unverified|error|payment_pending|edited')
  GROUP BY 1
),
all_debits AS (
  -- All debit entries: exclude CL changes (reason_id=2) and cancelled
  SELECT
    cwt.id,
    cf.farmer_id,
    cwt.amount,
    cwt.due_date
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = cwt.wallet_user_id
  WHERE cwt.transaction_type = 0
    AND cwt.cancelled = 0
    AND cwt.reason_id != 2
),
reconciled AS (
  -- How much of each debit has been settled (cancelled=0 only)
  SELECT
    r.reconciled_for_id,
    SUM(r.amount) AS settled_amount
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransactionreconciliation` r
  WHERE r.cancelled = 0
  GROUP BY r.reconciled_for_id
),
outstanding_debits AS (
  -- Debits not fully reconciled — remaining > 0
  SELECT
    d.farmer_id,
    d.due_date,
    d.amount - COALESCE(r.settled_amount, 0) AS remaining
  FROM all_debits d
  LEFT JOIN reconciled r ON r.reconciled_for_id = d.id
  WHERE d.amount - COALESCE(r.settled_amount, 0) > 0
),
partner_credit AS (
  SELECT
    farmer_id AS partner_id,
    ROUND(SUM(remaining), 0)                                                                     AS total_outstanding,
    ROUND(SUM(CASE WHEN DATE(due_date) < CURRENT_DATE() THEN remaining ELSE 0 END), 0)          AS ocp_raw,
    CASE
      WHEN SUM(CASE WHEN DATE(due_date) < CURRENT_DATE() THEN remaining ELSE 0 END) > 0
      THEN MAX(CASE WHEN DATE(due_date) < CURRENT_DATE()
               THEN DATE_DIFF(CURRENT_DATE(), DATE(due_date), DAY) ELSE NULL END)
      ELSE 0
    END AS max_dpd
  FROM outstanding_debits
  GROUP BY 1
),
collections AS (
  SELECT
    cf.farmer_id AS partner_id,
    ROUND(SUM(cwt.amount), 0) AS collections_period
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = cwt.wallet_user_id
  WHERE DATE(cwt.created_on) BETWEEN '{from_date}' AND '{to_date}'
    AND cwt.transaction_type = 1
    AND cwt.reason_id = 4
    AND cwt.cancelled = 0
  GROUP BY 1
)

SELECT
  CAST(p.farmer_id AS STRING)                                          AS farmer_id,
  p.name,
  p.territory,
  p.cluster,
  p.state,
  p.sm,
  p.tm,
  p.status,
  COALESCE(FORMAT_DATE('%Y-%m-%d', plv.last_visit_date), '')           AS last_visit_date,
  COALESCE(DATE_DIFF(CURRENT_DATE(), plv.last_visit_date, DAY), 9999)  AS days_since_visit,
  COALESCE(pv.visits_period, 0)                                        AS visits_period,
  COALESCE(pv.visits_today, 0)                                         AS visits_today,
  COALESCE(rev.revenue_period, 0)                                      AS revenue_period,
  COALESCE(pc.total_outstanding, 0)                                     AS total_outstanding,
  GREATEST(COALESCE(pc.ocp_raw, 0), 0)                                 AS ocp,
  COALESCE(pc.max_dpd, 0)                                              AS dpd,
  CASE
    WHEN GREATEST(COALESCE(pc.ocp_raw, 0), 0) > 5000 THEN 'HARD_BLOCK'
    WHEN GREATEST(COALESCE(pc.ocp_raw, 0), 0) > 1000 AND COALESCE(pc.max_dpd, 0) > 30 THEN 'HARD_BLOCK'
    ELSE 'CLEAR'
  END                                                                   AS block_status,
  COALESCE(col.collections_period, 0)                                  AS collections_period,
  COALESCE(FORMAT_DATE('%Y-%m-%d', p.first_order_date), '')            AS first_order_date
FROM partners p
LEFT JOIN partner_last_visit plv ON plv.partner_id = p.farmer_id
LEFT JOIN partner_visits pv ON pv.partner_id = p.farmer_id
LEFT JOIN revenue rev ON rev.partner_id = p.farmer_id
LEFT JOIN partner_credit pc ON pc.partner_id = p.farmer_id
LEFT JOIN collections col ON col.partner_id = p.farmer_id
ORDER BY GREATEST(COALESCE(pc.ocp_raw, 0), 0) DESC
"""

FIELD_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), 'field_dashboard.html')
UNDERWRITING_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), 'underwriting_dashboard.html')

# ── Recommendations query ───────────────────────────────────────────────────
# Parameters: input_cutoff, from_date, to_date, curr_month_start,
#             prev_year_month_start, prev_year_month_end,
#             curr_q_start, prev_q_start, prev_q_end

FIELD_RECOMMENDATIONS_SQL = """
WITH
partners AS (
  SELECT
    farmer_id AS partner_id,
    COALESCE(name,'Unknown') AS name,
    COALESCE(territory,'Unknown') AS territory,
    COALESCE(cluster,'Unknown') AS cluster,
    COALESCE(revised_state,'Unknown') AS state,
    LOWER(TRIM(COALESCE(sm,''))) AS sm,
    LOWER(TRIM(COALESCE(tm,''))) AS tm,
    first_order_date
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status = 'ACTIVE'
    AND sm IS NOT NULL AND TRIM(sm) != ''
    AND NOT STARTS_WITH(UPPER(TRIM(sm)),'VACANT')
),
all_debits AS (
  SELECT cwt.id, cf.farmer_id, cwt.amount, cwt.due_date
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt
  JOIN `agrostar-data.prod_db_views.csr_farmer` cf ON cf.user_id = cwt.wallet_user_id
  WHERE cwt.transaction_type = 0 AND cwt.cancelled = 0 AND cwt.reason_id != 2
),
reconciled AS (
  SELECT r.reconciled_for_id, SUM(r.amount) AS settled_amount
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransactionreconciliation` r
  WHERE r.cancelled = 0 AND DATE(r.created_on) <= '{input_cutoff}'
  GROUP BY 1
),
outstanding_debits AS (
  SELECT d.farmer_id, d.due_date,
    d.amount - COALESCE(r.settled_amount, 0) AS remaining
  FROM all_debits d
  LEFT JOIN reconciled r ON r.reconciled_for_id = d.id
  WHERE d.amount - COALESCE(r.settled_amount, 0) > 0
),
partner_ocp AS (
  SELECT
    farmer_id AS partner_id,
    ROUND(SUM(CASE WHEN DATE(due_date) < DATE('{input_cutoff}') THEN remaining ELSE 0 END), 0) AS ocp,
    CASE WHEN SUM(CASE WHEN DATE(due_date) < DATE('{input_cutoff}') THEN remaining ELSE 0 END) > 0
      THEN MAX(CASE WHEN DATE(due_date) < DATE('{input_cutoff}')
               THEN DATE_DIFF(DATE('{input_cutoff}'), DATE(due_date), DAY) ELSE NULL END)
      ELSE 0 END AS max_dpd
  FROM outstanding_debits
  GROUP BY 1
),
revenue_data AS (
  SELECT
    o.owner_id AS partner_id,
    ROUND(SUM(CASE WHEN DATE(inv.CreatedOn) BETWEEN '{curr_month_start}' AND '{input_cutoff}'            THEN inv.TotalPrice ELSE 0 END), 0) AS rev_curr,
    ROUND(SUM(CASE WHEN DATE(inv.CreatedOn) BETWEEN '{prev_year_month_start}' AND '{prev_year_month_end}' THEN inv.TotalPrice ELSE 0 END), 0) AS rev_prev,
    ROUND(SUM(CASE WHEN DATE(inv.CreatedOn) BETWEEN '{curr_q_start}' AND '{input_cutoff}'                THEN inv.TotalPrice ELSE 0 END), 0) AS rev_q_curr,
    ROUND(SUM(CASE WHEN DATE(inv.CreatedOn) BETWEEN '{prev_q_start}' AND '{prev_q_end}'                  THEN inv.TotalPrice ELSE 0 END), 0) AS rev_q_prev,
    ROUND(SUM(CASE WHEN DATE(inv.CreatedOn) >= DATE_SUB(DATE('{input_cutoff}'), INTERVAL 30 DAY)          THEN inv.TotalPrice ELSE 0 END), 0) AS rev_30d
  FROM `agrostar-data.pristine_wms_views.invoiced_report` inv
  JOIN `agrostar-data.prod_db_views.order_management_order` o
    ON CAST(o.unicommerce_id AS STRING) = inv.DisplayOrderCode
  WHERE DATE(inv.CreatedOn) BETWEEN '{prev_q_start}' AND '{input_cutoff}'
    AND inv.line_status != 'CANCELLED' AND inv.is_return = 0
    AND o.initiating_source LIKE 'B2B%'
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.status,'')),         r'cancelled|mob_app_unverified|error|payment_pending|edited')
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.unicommerce_status,'')), r'cancelled|mob_app_unverified|error|payment_pending|edited')
  GROUP BY 1
),
hist_visits_raw AS (
  SELECT CAST(store_id AS STRING) AS store_id, date, date_time AS visit_ts
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE visit_type = 'store_visit' AND date <= '{input_cutoff}'
  UNION ALL
  SELECT storeId AS store_id, DATE(date) AS date, updatedOn AS visit_ts
  FROM `agrostar-data.prod_db_views.visit`
  WHERE visitType = 'store_visit' AND DATE(date) <= '{input_cutoff}'
),
hist_visits AS (
  SELECT * EXCEPT(rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY store_id, date ORDER BY visit_ts DESC) AS rn
    FROM hist_visits_raw
  ) WHERE rn = 1
),
last_visit AS (
  SELECT store_id, MAX(date) AS last_visit_date FROM hist_visits GROUP BY 1
),
visited_curr_month AS (
  SELECT DISTINCT store_id FROM hist_visits WHERE date >= '{curr_month_start}'
),
visited_last_7d AS (
  SELECT DISTINCT store_id FROM hist_visits
  WHERE date >= DATE_SUB(DATE('{input_cutoff}'), INTERVAL 7 DAY)
),
p2p_raw AS (
  SELECT CAST(store_id AS STRING) AS store_id, promise_to_pay_date__p2p_ AS p2p_date, amount_promised
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE promise_to_pay_date__p2p_ IS NOT NULL AND amount_promised > 0
  UNION ALL
  SELECT storeId AS store_id, DATE(promiseToPayDate) AS p2p_date, promiseToPayAmount AS amount_promised
  FROM `agrostar-data.prod_db_views.visit`
  WHERE promiseToPayDate IS NOT NULL AND promiseToPayAmount > 0
),
p2p AS (
  SELECT store_id, MAX(p2p_date) AS p2p_date, MAX(amount_promised) AS p2p_amount
  FROM p2p_raw GROUP BY 1
),
cluster_cov AS (
  SELECT
    okr.cluster,
    ROUND(COUNTIF(lv.store_id IS NOT NULL AND lv.last_visit_date >= '{curr_month_start}') * 100.0
          / NULLIF(COUNT(*), 0), 1) AS cov_pct
  FROM `agrostar-data.offline_team.okr_data_live` okr
  LEFT JOIN last_visit lv ON lv.store_id = CAST(okr.farmer_id AS STRING)
  WHERE okr.status = 'ACTIVE'
  GROUP BY 1
),
actual_visits AS (
  SELECT DISTINCT SAFE_CAST(store_id AS INT64) AS partner_id
  FROM (
    SELECT CAST(store_id AS STRING) AS store_id
    FROM `agrostar-data.offline_team.store_visits_v2`
    WHERE date BETWEEN '{from_date}' AND '{to_date}'
    UNION ALL
    SELECT storeId AS store_id
    FROM `agrostar-data.prod_db_views.visit`
    WHERE DATE(date) BETWEEN '{from_date}' AND '{to_date}'
  )
),
scored AS (
  SELECT
    p.partner_id, p.name, p.sm, p.tm, p.cluster, p.state,
    COALESCE(oc.ocp, 0)     AS ocp,
    COALESCE(oc.max_dpd, 0) AS dpd,
    CASE WHEN COALESCE(oc.ocp,0)>5000
           OR (COALESCE(oc.ocp,0)>1000 AND COALESCE(oc.max_dpd,0)>30) THEN 1 ELSE 0 END AS is_hard_block,
    COALESCE(rv.rev_curr, 0)   AS rev_curr,
    COALESCE(rv.rev_prev, 0)   AS rev_prev,
    COALESCE(rv.rev_q_curr, 0) AS rev_q_curr,
    COALESCE(rv.rev_q_prev, 0) AS rev_q_prev,
    COALESCE(rv.rev_30d, 0)    AS rev_30d,
    lv.last_visit_date,
    COALESCE(DATE_DIFF(DATE('{input_cutoff}'), lv.last_visit_date, DAY), 9999) AS days_gap,
    CASE WHEN vm.store_id IS NOT NULL THEN 1 ELSE 0 END AS visited_curr_month,
    CASE WHEN v7.store_id IS NOT NULL THEN 1 ELSE 0 END AS visited_7d,
    p2p.p2p_date,
    COALESCE(p2p.p2p_amount, 0) AS p2p_amount,
    COALESCE(cc.cov_pct, 0)     AS cluster_cov_pct,
    -- Signal A: Revenue Risk (0-100)
    CASE
      WHEN COALESCE(oc.ocp,0)>5000
        OR (COALESCE(oc.ocp,0)>1000 AND COALESCE(oc.max_dpd,0)>30)                THEN 100
      WHEN COALESCE(rv.rev_30d,0)=0 AND COALESCE(rv.rev_prev,0)>0                  THEN 70
      WHEN p.first_order_date IS NOT NULL
        AND DATE_DIFF(DATE('{input_cutoff}'), p.first_order_date, DAY) < 90         THEN 50
      WHEN COALESCE(rv.rev_prev,0)>0
        AND SAFE_DIVIDE(COALESCE(rv.rev_prev,0)-COALESCE(rv.rev_curr,0),
                        COALESCE(rv.rev_prev,0)) > 0.40                             THEN 60
      WHEN COALESCE(rv.rev_q_prev,0)>0
        AND SAFE_DIVIDE(COALESCE(rv.rev_q_prev,0)-COALESCE(rv.rev_q_curr,0),
                        COALESCE(rv.rev_q_prev,0)) BETWEEN 0.20 AND 0.50           THEN 40
      WHEN COALESCE(rv.rev_curr,0)>0 AND vm.store_id IS NULL                        THEN 20
      ELSE 0
    END AS signal_a,
    -- Signal B: Collection Urgency (0-100, capped)
    LEAST(100,
      CASE
        WHEN COALESCE(oc.max_dpd,0)>90 THEN 100
        WHEN COALESCE(oc.max_dpd,0)>60 THEN 80
        WHEN COALESCE(oc.max_dpd,0)>30 THEN 60
        WHEN COALESCE(oc.max_dpd,0)>0  THEN 40
        ELSE 0
      END +
      CASE
        WHEN p2p.p2p_date BETWEEN DATE('{input_cutoff}')
          AND DATE_ADD(DATE('{input_cutoff}'), INTERVAL 7 DAY)                      THEN 30
        WHEN p2p.p2p_date < DATE('{input_cutoff}')                                  THEN 20
        ELSE 0
      END
    ) AS signal_b,
    -- Signal C: Visit Gap (0-100)
    CASE
      WHEN lv.last_visit_date IS NULL                                                THEN 100
      WHEN DATE_DIFF(DATE('{input_cutoff}'), lv.last_visit_date, DAY) > 60          THEN 80
      WHEN DATE_DIFF(DATE('{input_cutoff}'), lv.last_visit_date, DAY) > 30          THEN 50
      WHEN DATE_DIFF(DATE('{input_cutoff}'), lv.last_visit_date, DAY) > 14          THEN 20
      ELSE 0
    END AS signal_c,
    -- Signal D: Territory Coverage (0-20)
    CASE
      WHEN COALESCE(cc.cov_pct,0) < 50 THEN 20
      WHEN COALESCE(cc.cov_pct,0) < 80 THEN 10
      ELSE 0
    END AS signal_d
  FROM partners p
  LEFT JOIN partner_ocp       oc ON oc.partner_id = p.partner_id
  LEFT JOIN revenue_data      rv ON rv.partner_id = p.partner_id
  LEFT JOIN last_visit        lv ON lv.store_id = CAST(p.partner_id AS STRING)
  LEFT JOIN visited_curr_month vm ON vm.store_id = CAST(p.partner_id AS STRING)
  LEFT JOIN visited_last_7d   v7 ON v7.store_id = CAST(p.partner_id AS STRING)
  LEFT JOIN p2p                  ON p2p.store_id = CAST(p.partner_id AS STRING)
  LEFT JOIN cluster_cov       cc ON cc.cluster = p.cluster
),
with_final AS (
  SELECT *,
    ROUND(0.40*signal_a + 0.30*signal_b + 0.20*signal_c + 0.10*signal_d, 1) AS final_score,
    ROUND(0.40*signal_a, 1) AS sa_c,
    ROUND(0.30*signal_b, 1) AS sb_c,
    ROUND(0.20*signal_c, 1) AS sc_c,
    ROUND(0.10*signal_d, 1) AS sd_c,
    CASE
      WHEN 0.40*signal_a >= 0.30*signal_b AND 0.40*signal_a >= 0.20*signal_c THEN 'A'
      WHEN 0.30*signal_b >= 0.20*signal_c                                     THEN 'B'
      WHEN 0.20*signal_c > 0                                                  THEN 'C'
      ELSE 'D'
    END AS dom_signal
  FROM scored
  WHERE 0.40*signal_a + 0.30*signal_b + 0.20*signal_c + 0.10*signal_d > 0
    AND (visited_7d = 0 OR dpd > 60)   -- suppression: skip if visited <7d unless DPD critical
),
ranked AS (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY sm ORDER BY final_score DESC) AS sm_rank
  FROM with_final
)
SELECT
  CAST(r.partner_id AS STRING) AS partner_id,
  r.name,
  r.sm,
  r.tm,
  r.cluster,
  r.state,
  r.final_score,
  r.signal_a,
  r.signal_b,
  r.signal_c,
  r.signal_d,
  r.sa_c,
  r.sb_c,
  r.sc_c,
  r.sd_c,
  r.dom_signal,
  r.ocp,
  r.dpd,
  r.is_hard_block,
  r.rev_curr,
  r.rev_prev,
  r.rev_q_curr,
  r.rev_q_prev,
  COALESCE(FORMAT_DATE('%Y-%m-%d', r.last_visit_date), '') AS last_visit_date,
  CASE WHEN r.days_gap >= 9999 THEN 9999 ELSE r.days_gap END AS days_gap,
  COALESCE(FORMAT_DATE('%Y-%m-%d', r.p2p_date), '')         AS p2p_date,
  r.p2p_amount,
  CASE WHEN av.partner_id IS NOT NULL THEN 1 ELSE 0 END AS was_visited,
  r.sm_rank
FROM ranked r
LEFT JOIN actual_visits av ON av.partner_id = r.partner_id
WHERE r.sm_rank <= 18
ORDER BY r.sm, r.sm_rank
"""

# ── Visit Analysis queries ──────────────────────────────────────────────────

VISIT_VOLUME_SQL = """
WITH
visits_raw AS (
  SELECT LOWER(TRIM(email)) AS email, date AS visit_date, date_time AS visit_ts,
    CAST(store_id AS STRING) AS store_id,
    LOWER(TRIM(COALESCE(sm,''))) AS sm, LOWER(TRIM(COALESCE(tm,''))) AS tm,
    LOWER(TRIM(COALESCE(cm,''))) AS cm, LOWER(TRIM(COALESCE(sh,''))) AS sh,
    'fieldstar' AS source
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date BETWEEN '{from_date}' AND '{to_date}'
  UNION ALL
  SELECT LOWER(TRIM(v.email)), DATE(v.date), v.updatedOn, v.storeId,
    LOWER(TRIM(COALESCE(okr.sm,''))), LOWER(TRIM(COALESCE(okr.tm,''))),
    LOWER(TRIM(COALESCE(okr.cm,''))), LOWER(TRIM(COALESCE(okr.sh,''))),
    'saathiapp'
  FROM `agrostar-data.prod_db_views.visit` v
  LEFT JOIN `agrostar-data.offline_team.okr_data_live` okr
    ON SAFE_CAST(v.storeId AS INT64) = okr.farmer_id
  WHERE DATE(v.date) BETWEEN '{from_date}' AND '{to_date}'
),
visits_dedup AS (
  SELECT * EXCEPT(rn)
  FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC) AS rn FROM visits_raw)
  WHERE rn = 1
),
tagged AS (
  SELECT email, visit_date, store_id, source,
    FORMAT_DATE('%Y-%m', visit_date) AS month,
    CASE
      WHEN email != '' AND email = sm THEN 'SM'
      WHEN email != '' AND email = tm THEN 'TM'
      WHEN email != '' AND email = cm THEN 'CM'
      WHEN email != '' AND email = sh THEN 'SH'
      ELSE 'Other'
    END AS visitor_role
  FROM visits_dedup
)
SELECT
  month, source,
  COUNT(*) AS total_visits,
  COUNT(DISTINCT email) AS active_reps,
  COUNT(DISTINCT store_id) AS unique_stores,
  COUNTIF(visitor_role='SM') AS sm_visits,
  COUNTIF(visitor_role='TM') AS tm_visits,
  COUNTIF(visitor_role='CM') AS cm_visits,
  COUNTIF(visitor_role='SH') AS sh_visits,
  COUNTIF(visitor_role='Other') AS other_visits
FROM tagged
GROUP BY 1, 2
ORDER BY 1, 2
"""

VISIT_COVERAGE_SQL = """
WITH
visits_raw AS (
  SELECT LOWER(TRIM(email)) AS email, date AS visit_date, date_time AS visit_ts, CAST(store_id AS STRING) AS store_id
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date BETWEEN '{from_date}' AND '{to_date}'
  UNION ALL
  SELECT LOWER(TRIM(v.email)), DATE(v.date), v.updatedOn, v.storeId
  FROM `agrostar-data.prod_db_views.visit` v
  WHERE DATE(v.date) BETWEEN '{from_date}' AND '{to_date}'
),
visits_dedup AS (
  SELECT * EXCEPT(rn)
  FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC) AS rn FROM visits_raw)
  WHERE rn = 1
),
store_visits AS (
  SELECT DISTINCT SAFE_CAST(store_id AS INT64) AS partner_id, visit_date
  FROM visits_dedup WHERE store_id IS NOT NULL AND store_id != ''
),
active_partners AS (
  SELECT farmer_id,
    INITCAP(LOWER(TRIM(revised_state))) AS state,
    TRIM(cluster) AS cluster,
    TRIM(COALESCE(sm,'')) AS sm,
    TRIM(COALESCE(tm,'')) AS tm,
    TRIM(COALESCE(cm,'')) AS cm
  FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status = 'ACTIVE'
),
partner_cov AS (
  SELECT ap.farmer_id, ap.state, ap.cluster,
    STARTS_WITH(UPPER(ap.sm), 'VACANT') AS sm_vacant,
    STARTS_WITH(UPPER(ap.tm), 'VACANT') AS tm_vacant,
    STARTS_WITH(UPPER(ap.cm), 'VACANT') AS cm_vacant,
    COUNT(sv.visit_date) AS total_visits,
    MAX(sv.visit_date) AS last_visit_date
  FROM active_partners ap
  LEFT JOIN store_visits sv ON sv.partner_id = ap.farmer_id
  GROUP BY 1, 2, 3, 4, 5, 6
)
SELECT
  COALESCE(state,'Unknown') AS state,
  COALESCE(cluster,'Unknown') AS cluster,
  COUNT(*) AS total_partners,
  COUNTIF(sm_vacant) AS sm_vacant_count,
  COUNTIF(tm_vacant) AS tm_vacant_count,
  COUNTIF(cm_vacant) AS cm_vacant_count,
  COUNTIF(total_visits > 0) AS visited,
  ROUND(COUNTIF(total_visits > 0) * 100.0 / COUNT(*), 1) AS coverage_pct,
  COUNTIF(total_visits = 0) AS not_visited,
  COUNTIF(last_visit_date IS NOT NULL AND last_visit_date < DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)) AS stale_30d,
  ROUND(AVG(CASE WHEN total_visits > 0 THEN total_visits END), 1) AS avg_visits_covered
FROM partner_cov
GROUP BY 1, 2
ORDER BY 1, 2
"""

VISIT_OUTCOMES_SQL = """
WITH
visits_raw AS (
  SELECT LOWER(TRIM(email)) AS email, date AS visit_date, date_time AS visit_ts, CAST(store_id AS STRING) AS store_id
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date BETWEEN '{from_date}' AND '{to_date}'
  UNION ALL
  SELECT LOWER(TRIM(v.email)), DATE(v.date), v.updatedOn, v.storeId
  FROM `agrostar-data.prod_db_views.visit` v
  WHERE DATE(v.date) BETWEEN '{from_date}' AND '{to_date}'
),
visits_dedup AS (
  SELECT * EXCEPT(rn)
  FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC) AS rn FROM visits_raw)
  WHERE rn = 1
),
store_visits AS (
  SELECT DISTINCT SAFE_CAST(store_id AS INT64) AS partner_id
  FROM visits_dedup WHERE store_id IS NOT NULL AND store_id != ''
),
active_partners AS (
  SELECT farmer_id, INITCAP(LOWER(TRIM(revised_state))) AS state, TRIM(cluster) AS cluster
  FROM `agrostar-data.offline_team.okr_data_live` WHERE status = 'ACTIVE'
),
rev AS (
  SELECT o.owner_id AS partner_id, ROUND(SUM(inv.TotalPrice), 0) AS revenue
  FROM `agrostar-data.pristine_wms_views.invoiced_report` inv
  JOIN `agrostar-data.prod_db_views.order_management_order` o ON CAST(o.unicommerce_id AS STRING) = inv.DisplayOrderCode
  WHERE DATE(inv.CreatedOn) BETWEEN '{from_date}' AND '{to_date}'
    AND inv.line_status != 'CANCELLED' AND inv.is_return = 0 AND o.initiating_source LIKE 'B2B%'
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.status,'')), r'cancelled|mob_app_unverified|error|payment_pending|edited')
    AND NOT REGEXP_CONTAINS(LOWER(COALESCE(o.unicommerce_status,'')), r'cancelled|mob_app_unverified|error|payment_pending|edited')
  GROUP BY 1
),
coll AS (
  SELECT cf.farmer_id AS partner_id, ROUND(SUM(cwt.amount), 0) AS collections
  FROM `agrostar-data.prod_db_views.csr_farmer` cf
  JOIN `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt ON cwt.wallet_user_id = cf.user_id
  WHERE DATE(cwt.created_on) BETWEEN '{from_date}' AND '{to_date}'
    AND cwt.transaction_type = 1 AND cwt.reason_id = 4 AND cwt.cancelled = 0
  GROUP BY 1
)
SELECT
  ap.state, ap.cluster,
  CASE WHEN sv.partner_id IS NOT NULL THEN 1 ELSE 0 END AS was_visited,
  COUNT(*) AS partner_count,
  COUNTIF(COALESCE(r.revenue,0) > 0) AS with_revenue,
  ROUND(SUM(COALESCE(r.revenue,0))) AS total_revenue,
  ROUND(AVG(CASE WHEN COALESCE(r.revenue,0) > 0 THEN r.revenue END)) AS avg_rev_with_revenue,
  ROUND(SUM(COALESCE(c.collections,0))) AS total_collections
FROM active_partners ap
LEFT JOIN store_visits sv ON sv.partner_id = ap.farmer_id
LEFT JOIN rev r ON r.partner_id = ap.farmer_id
LEFT JOIN coll c ON c.partner_id = ap.farmer_id
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

VISIT_ANOMALY_SQL = """
WITH
visits_raw AS (
  SELECT LOWER(TRIM(email)) AS email, date AS visit_date, date_time AS visit_ts,
    CAST(store_id AS STRING) AS store_id,
    LOWER(TRIM(COALESCE(sm,''))) AS sm, LOWER(TRIM(COALESCE(tm,''))) AS tm,
    LOWER(TRIM(COALESCE(cm,''))) AS cm, LOWER(TRIM(COALESCE(sh,''))) AS sh
  FROM `agrostar-data.offline_team.store_visits_v2`
  WHERE date BETWEEN '{from_date}' AND '{to_date}'
  UNION ALL
  SELECT LOWER(TRIM(v.email)), DATE(v.date), v.updatedOn, v.storeId,
    LOWER(TRIM(COALESCE(okr.sm,''))), LOWER(TRIM(COALESCE(okr.tm,''))),
    LOWER(TRIM(COALESCE(okr.cm,''))), LOWER(TRIM(COALESCE(okr.sh,'')))
  FROM `agrostar-data.prod_db_views.visit` v
  LEFT JOIN `agrostar-data.offline_team.okr_data_live` okr ON SAFE_CAST(v.storeId AS INT64) = okr.farmer_id
  WHERE DATE(v.date) BETWEEN '{from_date}' AND '{to_date}'
),
visits_dedup AS (
  SELECT * EXCEPT(rn)
  FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC) AS rn FROM visits_raw)
  WHERE rn = 1
),
tagged AS (
  SELECT email, visit_date, store_id,
    DATE_TRUNC(visit_date, WEEK(MONDAY)) AS week_start,
    CASE WHEN email != '' AND email = sm THEN 'SM'
         WHEN email != '' AND email = tm THEN 'TM'
         WHEN email != '' AND email = cm THEN 'CM'
         WHEN email != '' AND email = sh THEN 'SH'
         ELSE 'Other' END AS visitor_role
  FROM visits_dedup
),
rep_stats AS (
  SELECT email, COUNT(*) AS total_visits, COUNT(DISTINCT store_id) AS unique_stores,
    ROUND(COUNT(*)*1.0/NULLIF(COUNT(DISTINCT store_id),0),1) AS visits_per_store
  FROM tagged GROUP BY 1
),
repeat_store_week AS (
  SELECT email, store_id, week_start, COUNT(*) AS cnt
  FROM tagged WHERE store_id IS NOT NULL AND store_id != ''
  GROUP BY 1, 2, 3 HAVING COUNT(*) >= 3
),
okr_sms AS (SELECT DISTINCT LOWER(TRIM(sm)) AS rep_email FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status='ACTIVE' AND sm IS NOT NULL AND sm!='' AND NOT STARTS_WITH(UPPER(sm),'VACANT')),
okr_tms AS (SELECT DISTINCT LOWER(TRIM(tm)) AS rep_email FROM `agrostar-data.offline_team.okr_data_live`
  WHERE status='ACTIVE' AND tm IS NOT NULL AND tm!='' AND NOT STARTS_WITH(UPPER(tm),'VACANT')),
logging_reps AS (SELECT DISTINCT email FROM tagged)
SELECT 'ghost_sms'           AS metric, COUNT(*) AS value FROM okr_sms o LEFT JOIN logging_reps l ON l.email=o.rep_email WHERE l.email IS NULL
UNION ALL SELECT 'ghost_tms',           COUNT(*) FROM okr_tms o LEFT JOIN logging_reps l ON l.email=o.rep_email WHERE l.email IS NULL
UNION ALL SELECT 'total_sms_in_okr',    COUNT(*) FROM okr_sms
UNION ALL SELECT 'total_tms_in_okr',    COUNT(*) FROM okr_tms
UNION ALL SELECT 'repeat_store_reps',   COUNT(DISTINCT email) FROM repeat_store_week
UNION ALL SELECT 'repeat_store_combos', COUNT(*) FROM repeat_store_week
UNION ALL SELECT 'high_conc_reps',      COUNT(*) FROM rep_stats WHERE visits_per_store >= 5 AND total_visits >= 10
UNION ALL SELECT 'total_active_reps',   COUNT(DISTINCT email) FROM tagged
"""


def run_query(sql):
    rows = get_client().query(sql).result()
    return [[str(v) if v is not None else None for v in row] for row in rows]


@app.route('/')
@app.route('/dvs_dashboard')
def dashboard():
    return send_file(DASHBOARD_PATH)


@app.route('/field_dashboard')
@app.route('/field_dashboard.html')
def field_dashboard():
    return send_file(FIELD_DASHBOARD_PATH)


@app.route('/underwriting_dashboard')
@app.route('/underwriting_dashboard.html')
def underwriting_dashboard():
    return send_file(UNDERWRITING_DASHBOARD_PATH)


@app.route('/health')
def health():
    return jsonify({'ok': True})


@app.route('/api/refresh')
def refresh():
    from_date = request.args.get('from', '2026-04-01')
    to_date   = request.args.get('to',   datetime.date.today().isoformat())
    try:
        summary = run_query(SUMMARY_SQL.format(from_date=from_date, to_date=to_date))
        trend   = run_query(TREND_SQL.format(from_date=from_date,   to_date=to_date))
        return jsonify({'ok': True, 'summary': summary, 'trend': trend})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/field/partners')
def field_partners():
    today     = datetime.date.today()
    from_date = request.args.get('from', today.replace(day=1).isoformat())
    to_date   = request.args.get('to',   today.isoformat())
    try:
        rows = run_query(FIELD_PARTNER_SQL.format(from_date=from_date, to_date=to_date))
        return jsonify({'ok': True, 'rows': rows, 'from_date': from_date, 'to_date': to_date})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/field/visit-analysis')
def visit_analysis():
    today     = datetime.date.today()
    from_date = request.args.get('from', today.replace(day=1).isoformat())
    to_date   = request.args.get('to',   today.isoformat())
    try:
        volume    = run_query(VISIT_VOLUME_SQL.format(from_date=from_date, to_date=to_date))
        coverage  = run_query(VISIT_COVERAGE_SQL.format(from_date=from_date, to_date=to_date))
        outcomes  = run_query(VISIT_OUTCOMES_SQL.format(from_date=from_date, to_date=to_date))
        anomalies = run_query(VISIT_ANOMALY_SQL.format(from_date=from_date, to_date=to_date))
        return jsonify({'ok': True, 'volume': volume, 'coverage': coverage,
                        'outcomes': outcomes, 'anomalies': anomalies,
                        'from_date': from_date, 'to_date': to_date})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/field/recommendations')
def field_recommendations():
    today     = datetime.date.today()
    from_date = request.args.get('from', today.isoformat())
    to_date   = request.args.get('to',   today.isoformat())

    from_dt   = datetime.date.fromisoformat(from_date)
    cutoff_dt = from_dt - datetime.timedelta(days=1)
    input_cutoff = cutoff_dt.isoformat()

    curr_month_start = cutoff_dt.replace(day=1).isoformat()
    # Same month last year
    prev_year_month_start = cutoff_dt.replace(year=cutoff_dt.year - 1, day=1).isoformat()
    prev_year_month_end   = cutoff_dt.replace(year=cutoff_dt.year - 1).isoformat()
    # Quarter: Apr → cutoff (current year); Apr → same date (prev year)
    q_month = 4 if cutoff_dt.month >= 4 else 1
    curr_q_start = cutoff_dt.replace(month=q_month, day=1).isoformat()
    prev_q_start = cutoff_dt.replace(year=cutoff_dt.year - 1, month=q_month, day=1).isoformat()
    prev_q_end   = cutoff_dt.replace(year=cutoff_dt.year - 1).isoformat()

    try:
        rows = run_query(FIELD_RECOMMENDATIONS_SQL.format(
            from_date=from_date,
            to_date=to_date,
            input_cutoff=input_cutoff,
            curr_month_start=curr_month_start,
            prev_year_month_start=prev_year_month_start,
            prev_year_month_end=prev_year_month_end,
            curr_q_start=curr_q_start,
            prev_q_start=prev_q_start,
            prev_q_end=prev_q_end,
        ))
        return jsonify({'ok': True, 'rows': rows, 'meta': {
            'input_cutoff':       input_cutoff,
            'from_date':          from_date,
            'to_date':            to_date,
            'curr_month_start':   curr_month_start,
            'prev_year_month_end': prev_year_month_end,
        }})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print('DVS proxy    → http://localhost:7891/dvs_dashboard')
    print('Field proxy  → http://localhost:7891/field_dashboard')
    app.run(host='127.0.0.1', port=7891)
