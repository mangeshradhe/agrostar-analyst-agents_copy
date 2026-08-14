"""
Fetch a filtered / sampled slice of genesys_db.disposition_data for call-insights
analysis, optionally cross-joined to order/return outcomes.

Two independent outputs, either or both depending on flags:
  --join-orders   -> outcome_<label>.csv   (order-level rows with call_score + is_returned/is_delivered,
                                             for aggregate stats — no LLM needed, these are structured columns)
  --sample-size N -> sample_<label>.csv    (raw-text rows: ai_summary, entities, recording_url, etc,
                                             for LLM-based qualitative synthesis — keep N small, see CLAUDE.md)

Known data-quality gotchas baked in here (do not bypass if hand-rolling a query instead):
  - disposition_data has ~3-4x row duplication per call (same meta_data_call_id, identical rows).
    Deduped via QUALIFY ROW_NUMBER() OVER (PARTITION BY meta_data_call_id ORDER BY created_on) = 1.
  - Calls under 60s are excluded — too short to carry a real conversation.
  - meta_data_client_id is PLAINTEXT in this raw genesys table (unlike prod_db_views.* fields).
    To join it to an encrypted-view column (order notification_mobile, csr_farmer.mobile_1/2/3,
    app_user.primary_mobile), encrypt it with the same deterministic UDF:
      `agrostar-data.AEAD_encryption_keys.ENCRYPT`(meta_data_client_id) = <encrypted view column>
    Never try to decrypt the view side — it's one-way.
  - Channel filter (--channel b2c|b2b|any, default b2c) matches meta_data_client_id against
    csr_farmer.mobile_1/2/3 and classifies by farmer_type: 'Farmer' (or creation_source
    LIKE 'CSR%'/'APP%') = b2c; farmer_type LIKE '%b2b%' (creation_source = 'B2B',
    values seen: 'B2B Profile', 'B2B Partner') = b2b. Unmatched numbers are excluded
    from both b2c and b2b (use --channel any to include everyone, matched or not).

Usage:
  python fetch_calls.py --from 2026-07-01 --to 2026-07-31 \\
      --pattern '\\b(app|application)\\b' --sample-size 200 --sample-method random

  python fetch_calls.py --from 2026-01-01 --to 2026-06-30 --join-orders
"""
import argparse
import csv
import json
import math
import os
import warnings

warnings.filterwarnings("ignore")
from google.cloud import bigquery

PROJECT = "agrostar-data"
ENCRYPT = "`agrostar-data.AEAD_encryption_keys.ENCRYPT`"

RETURN_STATUSES = (
    "RETURN_IN_TRANSIT", "RETURNED_BY_LMD", "RETURNED",
    "STORE_RETURN_ACKNOWLEDGED", "RETURN_ACKNOWLEDGED",
)


def build_filtered_cte(args):
    extra = []
    if args.pattern:
        extra.append(f"AND REGEXP_CONTAINS(LOWER(IFNULL(ai_summary, '')), r'{args.pattern}')")
    if args.disposition:
        extra.append(f"AND disposition_levels_level_1 = '{args.disposition}'")
    if args.agent:
        extra.append(f"AND meta_data_agent_id = '{args.agent}'")
    if args.min_score is not None:
        extra.append(f"AND call_score >= {args.min_score}")
    if args.max_score is not None:
        extra.append(f"AND call_score <= {args.max_score}")
    channel_cte = ""
    join_clause = ""
    if args.channel in ("b2c", "b2b"):
        # csr_farmer.farmer_type distinguishes channel directly:
        #   'B2B Profile' / 'B2B Partner' (creation_source = 'B2B') -> Saathi/retailer (B2B)
        #   'Farmer' (creation_source LIKE 'CSR%'/'APP%')           -> farmer (B2C)
        channel_case = """
    CASE
      WHEN LOWER(farmer_type) LIKE '%b2b%' THEN 'b2b'
      WHEN farmer_type = 'Farmer' OR creation_source LIKE 'CSR%' OR creation_source LIKE 'APP%' THEN 'b2c'
      ELSE NULL
    END"""
        channel_cte = f"""
farmer_channel AS (
  SELECT DISTINCT mobile, channel FROM (
    SELECT mobile_1 AS mobile, {channel_case} AS channel FROM `{PROJECT}.prod_db_views.csr_farmer` WHERE mobile_1 IS NOT NULL
    UNION ALL
    SELECT mobile_2, {channel_case} FROM `{PROJECT}.prod_db_views.csr_farmer` WHERE mobile_2 IS NOT NULL
    UNION ALL
    SELECT mobile_3, {channel_case} FROM `{PROJECT}.prod_db_views.csr_farmer` WHERE mobile_3 IS NOT NULL
  )
  WHERE channel = '{args.channel}'
),"""
        join_clause = f"JOIN farmer_channel fc ON {ENCRYPT}(deduped.meta_data_client_id) = fc.mobile"
    return f"""
WITH deduped AS (
  SELECT *
  FROM `{PROJECT}.genesys_db.disposition_data`
  WHERE created_on BETWEEN TIMESTAMP('{args.date_from}') AND TIMESTAMP('{args.date_to} 23:59:59')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY meta_data_call_id ORDER BY created_on) = 1
),
{channel_cte}
filtered AS (
  SELECT deduped.*
  FROM deduped
  {join_clause}
  WHERE SAFE_CAST(meta_data_call_duration AS FLOAT64) >= 60
  {' '.join(extra)}
)
"""


def run_query(client, query, label):
    job = client.query(query)
    rows = list(job.result())
    print(f"  [{label}] {len(rows)} rows, bytes billed: {job.total_bytes_billed}")
    return rows


def write_csv(rows, path):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    print(f"  wrote {path} ({len(rows)} rows)")


def fetch_sample(client, args, filtered_cte, out_dir, label):
    text_cols = """
    meta_data_call_id, meta_data_agent_id, meta_data_call_start_time, meta_data_call_duration,
    meta_data_recording_url, call_score, disposition_levels_level_1, disposition_levels_level_2,
    lead_interest, ai_summary, ai_insights_question, ai_insights_answer,
    custom_entities_Product_discussed, custom_entities_Crop_Discussed, custom_entities_Crop_issue,
    custom_entities_Pricing_Concern
    """
    if args.sample_method == "random":
        query = filtered_cte + f"""
SELECT {text_cols} FROM filtered
ORDER BY RAND() LIMIT {args.sample_size}
"""
        rows = run_query(client, query, "sample")
    elif args.sample_method in ("top", "bottom"):
        direction = "DESC" if args.sample_method == "top" else "ASC"
        query = filtered_cte + f"""
SELECT {text_cols} FROM filtered
ORDER BY {args.sample_order_by} {direction} LIMIT {args.sample_size}
"""
        rows = run_query(client, query, "sample")
    elif args.sample_method == "stratified":
        group_count_query = filtered_cte + f"""
SELECT COUNT(DISTINCT {args.stratify_by}) AS n_groups FROM filtered
WHERE {args.stratify_by} IS NOT NULL
"""
        n_groups = list(client.query(group_count_query).result())[0]["n_groups"] or 1
        per_group_cap = max(1, math.ceil(args.sample_size / n_groups))
        query = filtered_cte + f"""
SELECT {text_cols} FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY {args.stratify_by} ORDER BY RAND()) AS rn
  FROM filtered
  WHERE {args.stratify_by} IS NOT NULL
)
WHERE rn <= {per_group_cap}
"""
        rows = run_query(client, query, "sample")
    else:
        raise ValueError(f"Unknown sample method: {args.sample_method}")

    write_csv(rows, os.path.join(out_dir, f"sample_{label}.csv"))
    return len(rows)


def fetch_outcome_join(client, args, filtered_cte, out_dir, label):
    query = filtered_cte + f"""
,
csr_orders AS (
  SELECT o.sales_order_id, o.created_on AS order_date, o.notification_mobile,
    au.username AS agent
  FROM `{PROJECT}.prod_db_views.order_management_order` o
  JOIN `{PROJECT}.prod_db_views.auth_user` au ON au.id = o.entered_by_id
  WHERE DATE(o.created_on) BETWEEN '{args.date_from}' AND '{args.date_to}'
    AND LOWER(o.initiating_source) LIKE 'csr%'
    AND o.notification_mobile IS NOT NULL
    AND LOWER(COALESCE(o.order_type,'')) NOT LIKE '%offline%'
    AND o.unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
),
history_agg AS (
  SELECT order_id,
    MIN(CASE WHEN status IN ({', '.join(repr(s) for s in RETURN_STATUSES)}) THEN created_on END) AS return_signal_time,
    MIN(CASE WHEN status = 'DELIVERED' THEN created_on END) AS delivered_time
  FROM `{PROJECT}.prod_db_views.order_management_orderhistorymeta`
  GROUP BY order_id
),
call_matched AS (
  SELECT
    c.sales_order_id,
    MAX(f.call_score) AS call_score,
    MAX(f.meta_data_call_id) AS meta_data_call_id,
    ARRAY_AGG(f.disposition_levels_level_1 IGNORE NULLS ORDER BY f.created_on DESC LIMIT 1)[SAFE_OFFSET(0)] AS disposition_l1
  FROM csr_orders c
  JOIN filtered f
    ON {ENCRYPT}(f.meta_data_client_id) = c.notification_mobile
    AND f.meta_data_agent_id = c.agent
    AND DATE(f.created_on) = DATE(c.order_date)
  GROUP BY c.sales_order_id
)
SELECT
  cm.call_score, cm.meta_data_call_id, cm.disposition_l1,
  CASE
    WHEN cm.call_score IS NULL THEN 'unscored'
    WHEN cm.call_score < 40 THEN '<40'
    WHEN cm.call_score < 60 THEN '40-60'
    WHEN cm.call_score < 80 THEN '60-80'
    ELSE '80+'
  END AS score_bucket,
  (h.delivered_time IS NOT NULL AND h.return_signal_time IS NULL) AS is_delivered,
  (h.return_signal_time IS NOT NULL) AS is_returned
FROM csr_orders c
JOIN history_agg h ON h.order_id = c.sales_order_id
LEFT JOIN call_matched cm ON cm.sales_order_id = c.sales_order_id
WHERE h.delivered_time IS NOT NULL OR h.return_signal_time IS NOT NULL
"""
    rows = run_query(client, query, "outcome")
    write_csv(rows, os.path.join(out_dir, f"outcome_{label}.csv"))
    return len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    p.add_argument("--pattern", default=None, help="regex matched against LOWER(ai_summary)")
    p.add_argument("--disposition", default=None, help="exact match on disposition_levels_level_1")
    p.add_argument("--agent", default=None, help="exact match on meta_data_agent_id")
    p.add_argument("--min-score", type=float, default=None)
    p.add_argument("--max-score", type=float, default=None)
    p.add_argument("--channel", choices=["b2c", "b2b", "any"], default="b2c",
                    help="b2c=farmer calls (csr_farmer.farmer_type='Farmer'), "
                         "b2b=Saathi/retailer calls (farmer_type LIKE '%%b2b%%'), any=no channel filter")
    p.add_argument("--join-orders", action="store_true", default=False,
                    help="cross-join sampled calls to CSR order outcomes (delivered/returned)")
    p.add_argument("--sample-size", type=int, default=0,
                    help="0 = skip text sample; keep this small (see CLAUDE.md sampling guidance)")
    p.add_argument("--sample-method", choices=["random", "top", "bottom", "stratified"], default="random")
    p.add_argument("--sample-order-by", default="call_score", help="column for top/bottom sampling")
    p.add_argument("--stratify-by", default="disposition_levels_level_1", help="column for stratified sampling")
    p.add_argument("--label", default=None, help="filename slug; default derived from date range")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    label = args.label or f"{args.date_from}_{args.date_to}"
    out_dir = args.out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    client = bigquery.Client(project=PROJECT)
    filtered_cte = build_filtered_cte(args)

    manifest = {
        "date_from": args.date_from, "date_to": args.date_to,
        "filters": {
            "pattern": args.pattern, "disposition": args.disposition, "agent": args.agent,
            "min_score": args.min_score, "max_score": args.max_score, "channel": args.channel,
        },
    }

    if args.sample_size > 0:
        n = fetch_sample(client, args, filtered_cte, out_dir, label)
        manifest["sample"] = {"method": args.sample_method, "requested": args.sample_size, "returned": n}

    if args.join_orders:
        n = fetch_outcome_join(client, args, filtered_cte, out_dir, label)
        manifest["outcome_join"] = {"matched_order_rows": n}

    if not args.join_orders and args.sample_size <= 0:
        print("Nothing to do — pass --sample-size N and/or --join-orders")
        return

    manifest_path = os.path.join(out_dir, f"manifest_{label}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  wrote {manifest_path}")


if __name__ == "__main__":
    main()
