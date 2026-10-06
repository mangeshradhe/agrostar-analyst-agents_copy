"""
Pulls the rest of the Genesys `disposition_data` row for each sales call already matched to an order
(output/lmd_sales_calls.json), so the dashboard can summarise what Genesys recorded about the call.
Writes output/lmd_sales_disposition.json (one row per call_id, deduped -- the view has duplicate rows).

  /usr/bin/python3 scripts/extract_sales_disposition.py --dry-run   # cost only
  /usr/bin/python3 scripts/extract_sales_disposition.py             # run
"""
import json, os, sys
from google.cloud import bigquery

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "output")
FIELDS = ["disposition_levels_level_3", "lead_analysis", "internal_status", "tags_Tag_Name",
          "ai_insights_question", "ai_insights_answer", "call_score",
          "custom_entities_Pricing_Concern", "custom_entities_Land_holding", "custom_entities_Product_discussed",
          "custom_entities_Offer_Pitched", "custom_entities_Crop_issue", "custom_entities_Crop_Stage_or_Crop_agining",
          "custom_entities_Crop_Discussed", "custom_entities_Call_Back_Date_and_Time"]

sales = json.load(open(os.path.join(OUT, "lmd_sales_calls.json"), encoding="utf-8"))
ids = sorted({r["call_id"] for r in sales})
starts = sorted(r["call_start"] for r in sales)
lo, hi = starts[0][:10], starts[-1][:10]
query = f"""
SELECT meta_data_call_id AS call_id, {", ".join(FIELDS)}
FROM `agrostar-data.genesys_db_views.disposition_data`
WHERE meta_data_call_start_time BETWEEN TIMESTAMP('{lo}') AND TIMESTAMP_ADD(TIMESTAMP('{hi}'), INTERVAL 1 DAY)
  AND meta_data_call_id IN UNNEST(@ids)
QUALIFY ROW_NUMBER() OVER (PARTITION BY meta_data_call_id ORDER BY created_on DESC) = 1
"""
bq = bigquery.Client(project="agrostar-data")
cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids)],
                              dry_run="--dry-run" in sys.argv, use_query_cache=False)
job = bq.query(query, job_config=cfg)
if cfg.dry_run:
    print(f"{len(ids):,} call ids, {lo}..{hi}: would scan {job.total_bytes_processed/1e9:.1f} GB"); sys.exit()
rows = [dict(r.items()) for r in job.result()]
json.dump(rows, open(os.path.join(OUT, "lmd_sales_disposition.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), default=str)
print(f"Wrote {len(rows):,} rows")
