"""
DVS Dashboard Proxy — runs BigQuery queries server-side using ADC.
Start: python3 proxy.py
Requires: pip install -r requirements.txt
Auth:    gcloud auth application-default login
"""

import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
from google.cloud import bigquery

app = Flask(__name__)
CORS(app)

PROJECT = 'agrostar-data'
client = bigquery.Client(project=PROJECT)

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


def run_query(sql):
    rows = client.query(sql).result()
    return [[str(v) if v is not None else None for v in row] for row in rows]


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


if __name__ == '__main__':
    print('DVS proxy → http://localhost:8080  (Ctrl-C to stop)')
    app.run(host='127.0.0.1', port=8080)
