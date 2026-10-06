"""
Extracts fresh LMD (Last Mile Delivery) call + order-journey data from BigQuery into
the three JSON files that scripts/render_dashboard.py splices into the dashboard.

Re-run this whenever you want the dashboard to reflect current data (the underlying
lmd_call_audits table grows continuously -- see CLAUDE.md).

Pulls three datasets, in order (each depends on the previous):

1. Calls -- every successful row in llm_transcripts.lmd_call_audits.
   -> output/lmd_calls_data.json

2. Packages -- every package (not just called ones) on every order reachable from
   the call set, via package_id -> delivery_shippingpackage.code -> order_id. An
   order can have a package nobody called about (e.g. quietly delivered), and the
   order journey should be complete -- see CLAUDE.md "one order, multiple packages".
   -> output/lmd_order_packages.json

3. Status history -- every delivery_shippingpackagestatushistory row for that same
   package set (the digital events in the order journey timeline).
   -> output/lmd_package_status_history.json

4. Order geography -- state/district/taluka/village/pin_code of each called order's
   shipping address (order_management_order.shipping_address_id -> csr_shippingaddress.id).
   The audit table's own delivery_state is filled on only ~5% of calls (the LLM only
   captures it if the farmer says it aloud); the order's real address covers far more.
   Used by the dashboard's State filter.
   -> output/lmd_order_geo.json
   Also writes output/extract_meta.json with the extraction instant, which the dashboard
   uses as "now" for the pending / not-executed check (see render_dashboard.py).

Requires: google-cloud-bigquery (pip install google-cloud-bigquery)
Auth: Application Default Credentials (gcloud auth application-default login)

IMPORTANT: use /usr/bin/python3, not a bare `python3` -- see reference_
python3_bigquery_interpreter.md in the main signal-engine project memory; the
Homebrew python3 on this machine does not have google-cloud-bigquery installed.

Usage:
  /usr/bin/python3 scripts/extract_lmd_data.py
"""
import datetime
import json
import os
import sys

from google.cloud import bigquery

PROJECT_ID = "agrostar-data"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "output")

CALLS_QUERY = """
-- lmd_call_audits itself has no order_id column; attach it via the package join
-- (same key as sql/order_rollup.sql) so the dashboard can group calls by order.
SELECT a.*, p.order_id AS order_id
FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
  ON a.package_id = p.code
WHERE a.processing_status = 'success'
"""

# Every package on every order that has at least one call -- not just the called
# package itself, so an order's full package set is visible in the journey view.
PACKAGES_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
)
SELECT
  p.code AS package_id,
  p.order_id,
  p.delivery_status AS current_status,
  p.scheduled_date,
  p.attempt,
  p.updated_on
FROM `agrostar-data.prod_db_views.delivery_shippingpackage` p
JOIN called_orders co ON p.order_id = co.order_id
"""

# Status history for that same full package set (digital events for the journey).
STATUS_HISTORY_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
),
order_packages AS (
  SELECT p.code AS package_id
  FROM `agrostar-data.prod_db_views.delivery_shippingpackage` p
  JOIN called_orders co ON p.order_id = co.order_id
)
SELECT
  h.package_id,
  h.delivery_status,
  h.created_on,
  h.reason,
  h.comment,
  h.by_user,
  h.channel
FROM `agrostar-data.prod_db_views.delivery_shippingpackagestatushistory` h
JOIN order_packages op ON h.package_id = op.package_id
ORDER BY h.package_id, h.created_on
"""


# Delivery geography per called order. order_id in delivery_shippingpackage is a string with a
# 4-digit YYMM prefix ('260913752174' = '2609' + sales_order_id 13752174); order_management_order
# keys on the bare INT64 sales_order_id. Matching the raw string as an INT never joins (0 of 6,430);
# stripping the prefix matched 499 of 500 in a check on 2026-09-29. Split orders carry a suffix
# ('260713425265_1', '_2'); the regex keeps only the digits after YYMM so they join to the parent order
# (SUBSTR left the '_1' on and missed them -- 10 of 7,750 orders on 2026-10-01).
ORDER_GEO_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
)
SELECT c.order_id, s.state, s.district, s.taluka, s.village, s.pin_code
FROM called_orders c
LEFT JOIN `agrostar-data.prod_db_views.order_management_order` o
  ON SAFE_CAST(REGEXP_EXTRACT(c.order_id, r'^[0-9]{4}([0-9]+)') AS INT64) = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.csr_shippingaddress` s
  ON s.id = o.shipping_address_id
"""


# Line items per called order, with the Product Group of each SKU. order_management_orderitem keys on
# the same bare INT64 sales_order_id as order_management_order (see ORDER_GEO_QUERY above). SKU ->
# group: orderitem.item_sku = pristine_wms_prod_db.item_mst.item_code (unique per code), whose
# sub_sub_product_group is a code that sub_sub_product_group_mst resolves to a display name (raw value
# used when the mst has no row). Items with a blank group / SKU missing from item_mst come out as NULL
# and the dashboard shows them as "Unmapped".
# ~3.2 GB scanned (dry run 2026-09-30).
ORDER_ITEMS_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
)
SELECT
  c.order_id,
  oi.item_sku,
  oi.item_name,
  oi.quantity,
  oi.total_price,
  oi.status_code,
  -- Some codes (e.g. 'STICKY TRAP') have no row in sub_sub_product_group_mst; fall back to the raw
  -- item_mst value, which is already readable, rather than losing the group.
  COALESCE(NULLIF(g.sub_sub_product_group_name, ''), NULLIF(im.sub_sub_product_group, '')) AS product_group
FROM called_orders c
JOIN `agrostar-data.prod_db_views.order_management_order` o
  ON SAFE_CAST(REGEXP_EXTRACT(c.order_id, r'^[0-9]{4}([0-9]+)') AS INT64) = o.sales_order_id
JOIN `agrostar-data.prod_db_views.order_management_orderitem` oi
  ON oi.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.pristine_wms_prod_db.item_mst` im
  ON im.item_code = oi.item_sku
LEFT JOIN `agrostar-data.pristine_wms_prod_db.sub_sub_product_group_mst` g
  ON g.sub_sub_product_group_code = im.sub_sub_product_group
"""


# Sales call that created each order. Path: order_management_order.owner_id = csr_farmer.farmer_id ->
# all of mobile_1/2/3 -> genesys_db_views.disposition_data.meta_data_client_id (last 10 digits) within
# [order created - 1 day, + 6 hours]. "created_on ~ confirmed_on" does NOT separate call orders (395 of 400
# orders were confirmed within 5 min of creation). Every order source seen here is call-centre-created (CSR*,
# APP* and SUPPORT_CSR* -- confirmed by the user 2026-09-30), so no source filter is applied. The real
# link is that the order is created almost exactly when the sales call ends (median gap -0.1 min,
# n=230, 2026-09-30 sample), so matching is on call END (start + duration) vs order created_on.
# Both timestamps were on the same clock in the sample. Candidates only; picking is in pick_sales_calls().
SALES_CANDIDATES_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
), o AS (
  SELECT c.order_id, m.sales_order_id, m.owner_id, m.created_on, m.confirmed_on, m.source
  FROM called_orders c
  JOIN `agrostar-data.prod_db_views.order_management_order` m
    ON SAFE_CAST(REGEXP_EXTRACT(c.order_id, r'^[0-9]{4}([0-9]+)') AS INT64) = m.sales_order_id
), nums AS (
  SELECT o.order_id, o.created_on, CAST(mob AS STRING) AS mob
  FROM o
  JOIN `agrostar-data.replica_prod_db_views.csr_farmer` f ON f.farmer_id = o.owner_id,
  UNNEST([f.mobile_1, f.mobile_2, f.mobile_3]) mob
  WHERE mob IS NOT NULL
)
SELECT DISTINCT
  n.order_id, n.created_on AS order_created,
  d.meta_data_call_id AS call_id,
  d.meta_data_call_start_time AS call_start,
  d.meta_data_call_duration AS duration_sec,
  d.meta_data_call_direction AS direction,
  d.meta_data_agent_id AS agent_id,
  d.meta_data_recording_url AS recording_path,
  d.disposition_levels_level_1 AS disposition_1,
  d.disposition_levels_level_2 AS disposition_2,
  d.lead_interest AS lead_interest,
  d.ai_summary AS ai_summary
FROM nums n
JOIN `agrostar-data.genesys_db_views.disposition_data` d
  ON RIGHT(d.meta_data_client_id, 10) = RIGHT(n.mob, 10)
 AND d.meta_data_call_start_time BETWEEN TIMESTAMP_SUB(n.created_on, INTERVAL 1 DAY)
                                     AND TIMESTAMP_ADD(n.created_on, INTERVAL 6 HOUR)
"""

# Order source per called order, so the dashboard can say "not a call-centre order" instead of "no call found".
ORDER_SOURCE_QUERY = """
WITH called_orders AS (
  SELECT DISTINCT p.order_id
  FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
  JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
    ON a.package_id = p.code
  WHERE a.processing_status = 'success'
)
SELECT c.order_id, m.source, m.created_on AS order_created
FROM called_orders c
JOIN `agrostar-data.prod_db_views.order_management_order` m
  ON SAFE_CAST(REGEXP_EXTRACT(c.order_id, r'^[0-9]{4}([0-9]+)') AS INT64) = m.sales_order_id
"""

SALES_TRANSCRIPT_QUERY = """
SELECT call_id, speaker_type, start_time, transcript
FROM `agrostar-data.prod_db_views.call_transcription_data`
WHERE call_id IN UNNEST(@ids)
ORDER BY call_id, start_time
"""

MATCH_TIGHT_MIN = 30      # |order created - call end| <= this -> "matched"
MATCH_LOOSE_MIN = 360     # call ended up to this long BEFORE order creation -> "probable"


def _parse_ts(v):
    if isinstance(v, datetime.datetime):
        return v.replace(tzinfo=None)
    return datetime.datetime.fromisoformat(str(v).replace(" UTC", "").replace("+00:00", ""))


def pick_sales_calls(cands: list) -> dict:
    """order_id -> best sales-call candidate (closest call end to order creation), with match quality.
    Candidates are deduped by call_id (disposition_data has full-row duplicates in some months)."""
    by_order = {}
    for c in cands:
        by_order.setdefault(c["order_id"], {})[c["call_id"]] = c
    picked = {}
    for oid, calls in by_order.items():
        best = None
        for c in calls.values():
            dur = c["duration_sec"]
            dur = float(dur) if str(dur).replace(".", "", 1).isdigit() else 0.0   # 'NA' seen in data
            end = _parse_ts(c["call_start"]) + datetime.timedelta(seconds=dur)
            gap = (_parse_ts(c["order_created"]) - end).total_seconds() / 60.0   # >0: order created after call ended
            if gap < -MATCH_TIGHT_MIN or gap > MATCH_LOOSE_MIN:
                continue
            if best is None or abs(gap) < abs(best[0]):
                best = (gap, c)
        if best:
            gap, c = best
            picked[oid] = dict(c, gap_min=round(gap, 1), match="matched" if abs(gap) <= MATCH_TIGHT_MIN else "probable",
                               duration_sec=int(float(c["duration_sec"])) if str(c["duration_sec"]).replace(".", "", 1).isdigit() else None)
    return picked


def extract_sales_calls(bq: bigquery.Client) -> None:
    sources = run_query(bq, ORDER_SOURCE_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_order_sources.json"), sources)
    cands = run_query(bq, SALES_CANDIDATES_QUERY)
    picked = pick_sales_calls(cands)
    ids = [p["call_id"] for p in picked.values()]
    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids)])
    utter = [dict(r.items()) for r in bq.query(SALES_TRANSCRIPT_QUERY, job_config=cfg).result()]
    by_call = {}
    for u in utter:
        by_call.setdefault(u["call_id"], []).append([u["speaker_type"], u["transcript"]])
    rows = []
    for oid, p in picked.items():
        rows.append({"order_id": oid, "call_id": p["call_id"], "call_start": p["call_start"], "duration_sec": p["duration_sec"],
                     "direction": p["direction"], "agent_id": p["agent_id"], "recording_path": p["recording_path"],
                     "disposition_1": p["disposition_1"], "disposition_2": p["disposition_2"], "lead_interest": p["lead_interest"],
                     "ai_summary": p["ai_summary"], "gap_min": p["gap_min"], "match": p["match"],
                     "transcript": by_call.get(p["call_id"], [])})
    write_json(os.path.join(OUT_DIR, "lmd_sales_calls.json"), rows)
    n_csr = len(sources)
    n_tight = sum(1 for r in rows if r["match"] == "matched")
    n_tr = sum(1 for r in rows if r["transcript"])
    print(f"{n_csr:,} orders searched / {len(rows):,} with a sales call "
          f"({n_tight:,} matched, {len(rows)-n_tight:,} probable) / {n_tr:,} with transcript")


def run_query(bq: bigquery.Client, query: str) -> list:
    rows = list(bq.query(query).result())
    return [dict(row.items()) for row in rows]


def _json_default(obj):
    # datetime/date objects -> ISO 8601 ("T" separator, millisecond precision) so the
    # dashboard's JS can parse them reliably with plain `new Date(...)` -- str() gives
    # a space-separated, microsecond-precision format that not every JS engine's Date
    # parser accepts.
    if isinstance(obj, datetime.datetime):
        return obj.isoformat(timespec="milliseconds")
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def write_json(path: str, rows: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, default=_json_default, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {path} ({len(rows):,} rows)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    bq = bigquery.Client(project=PROJECT_ID)

    # `extract_lmd_data.py items` refreshes only the order line items (leaves the other files and the
    # "as of" time untouched).
    if len(sys.argv) > 1 and sys.argv[1] == "sales":
        extract_sales_calls(bq)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "items":
        write_json(os.path.join(OUT_DIR, "lmd_order_items.json"), run_query(bq, ORDER_ITEMS_QUERY))
        return

    calls = run_query(bq, CALLS_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_calls_data.json"), calls)

    packages = run_query(bq, PACKAGES_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_order_packages.json"), packages)

    status_history = run_query(bq, STATUS_HISTORY_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_package_status_history.json"), status_history)

    geo = run_query(bq, ORDER_GEO_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_order_geo.json"), geo)

    items = run_query(bq, ORDER_ITEMS_QUERY)
    write_json(os.path.join(OUT_DIR, "lmd_order_items.json"), items)

    extract_sales_calls(bq)

    # Extraction instant (UTC) -- the dashboard's "as of" time. Written BEFORE the queries would
    # be more conservative, but rows land continuously, so end-of-extraction is the honest "now".
    with open(os.path.join(OUT_DIR, "extract_meta.json"), "w") as f:
        json.dump({"extracted_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}, f)

    n_orders = len({p["order_id"] for p in packages})
    print(f"{len(calls):,} calls / {n_orders:,} orders / {len(packages):,} packages / "
          f"{len(status_history):,} status-history rows")


if __name__ == "__main__":
    main()
