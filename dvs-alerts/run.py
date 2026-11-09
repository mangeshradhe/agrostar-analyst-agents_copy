#!/usr/bin/env python3
"""
DVS Alerts — daily guardrail checks for the DVS (Direct from Village Store) program.

Guardrail 1 — LEDGER RECONCILIATION GAPS. A DVS (Saathi-store-fulfilled) order
that has been reconciled with the LMD partner
(delivery_shippingpackagereconciliationstatushistory.reconciliation_status =
'reconciliation_done') must produce exactly 3 rows in
wallet_creditwallettransaction (reference_id = sales_order_id, cancelled = 0):
    reason_id 31  Farmer order payment   (CREDIT — store gets paid)
    reason_id 30  Delivery Charges       (DEBIT)
    reason_id 33  Platform Fee           (DEBIT)
In practice these land within seconds of the reconciliation event, so any gap
found here (checking yesterday's reconciliations) is a genuine posting
failure, not normal processing lag. All three are treated as equally severe —
the report lists exactly which reason_id(s) are missing per order rather than
collapsing to one flag. Excludes COCO (EBO) stores explicitly — COCO settles
through delivery_payment / delivery_lppostpaidtransaction, not this ledger,
and would otherwise show up as a 100%-missing false positive on every COCO
order.

Guardrails 2 (zone match) and 3 (LMD partner assignment) were removed on
2026-10-31 at the user's request — not needed for now. See git history
(commit before "DVS alerts: ledger only") to restore them.

Cost notes (dry-run, Oct 2026): ~453 MB/run (was ~4.9 GB with all three
guardrails). reconciliationstatushistory and wallet_creditwallettransaction are
partitioned on created_on, so both are date-bounded. The ledger lookback is
LEDGER_LOOKBACK_DAYS before start_date: in Oct 2026, 1 of 8,270 reconciled
orders had ledger rows posted before reconciliation (73 days earlier, a
re-reconciled package); all others posted after. delivery_shippingpackage and
order_management_order are not date-prunable (full scans of 2-3 columns).

This script only COMPUTES. It writes the alert to logs/latest_alert.md — a
single all-clear line when clean, otherwise every flagged order inline. (The
Slack Canvas overflow was removed 2026-10-31: since Jul it triggered once.)
Posting is send.sh's job, via the user's own claude.ai Slack connector
(`claude -p`).

Usage:
    python3 run.py                                              # yesterday only (IST)
    python3 run.py --start-date 2026-07-01 --end-date 2026-07-17 # backfill range (IST, inclusive)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT = "agrostar-data"
LEDGER_LOOKBACK_DAYS = 90  # wallet ledger partition lower bound, days before start_date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
ALERT_FILE = os.path.join(LOG_DIR, "latest_alert.md")
# Leftover Canvas files from the old version — deleted on every run so nothing stale is ever posted.
STALE_CANVAS_FILES = [os.path.join(LOG_DIR, f) for f in ("canvas_ledger.md", "canvas_zone.md", "canvas_lmd.md")]

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "dvs_alerts.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("dvs_alerts")

# ── BigQuery: Guardrail 1 — Ledger reconciliation gaps ──────────────────────

LEDGER_STORES_CTE = f"""
stores AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(name)                       AS store_name,
    ANY_VALUE(ancestor_institutions_name) AS ancestor_institutions_name
  FROM `{PROJECT}.galaxy_views.institution`
  WHERE reference_customer_id IS NOT NULL AND archive = false
  GROUP BY 1
)
"""

LEDGER_GAP_QUERY = f"""
WITH {LEDGER_STORES_CTE},
reconciled_pkgs AS (
  SELECT package_id, MAX(created_on) AS reconciled_at
  FROM `{PROJECT}.prod_db_views.delivery_shippingpackagereconciliationstatushistory`
  WHERE reconciliation_status = 'reconciliation_done'
    AND DATE(created_on, 'Asia/Kolkata') BETWEEN '{{start_date}}' AND '{{end_date}}'
  GROUP BY 1
),
dvs_orders AS (
  SELECT
    o.sales_order_id,
    o.retail_store_code,
    sp.code AS package_code,
    rp.reconciled_at
  FROM reconciled_pkgs rp
  JOIN `{PROJECT}.prod_db_views.delivery_shippingpackage` sp ON sp.code = rp.package_id
  JOIN `{PROJECT}.prod_db_views.order_management_order` o ON CAST(o.unicommerce_id AS STRING) = sp.order_id
  JOIN stores s ON s.store_id = o.retail_store_code
  WHERE o.retail_store_code IS NOT NULL AND o.retail_store_code != ''
    AND LOWER(s.ancestor_institutions_name) LIKE '%sathi%'
    AND LOWER(s.ancestor_institutions_name) NOT LIKE '% ebo %'
),
ledger AS (
  SELECT
    reference_id AS sales_order_id,
    ARRAY_AGG(DISTINCT reason_id) AS reason_ids_present,
    SUM(CASE WHEN reason_id = 31 THEN amount END) AS credit_amount
  FROM `{PROJECT}.prod_db_views.wallet_creditwallettransaction`
  WHERE cancelled = 0
    AND reason_id IN (30, 31, 33)
    AND created_on >= TIMESTAMP(DATE_SUB(DATE '{{start_date}}', INTERVAL {LEDGER_LOOKBACK_DAYS} DAY), 'Asia/Kolkata')
  GROUP BY 1
)
SELECT
  d.sales_order_id                                              AS order_id,
  d.retail_store_code                                           AS store_id,
  COALESCE(s.store_name, d.retail_store_code)                   AS store_name,
  DATE(d.reconciled_at, 'Asia/Kolkata')                         AS reconciled_date,
  FORMAT_DATETIME('%d %b %H:%M', DATETIME(d.reconciled_at, 'Asia/Kolkata')) AS reconciled_ist,
  l.credit_amount,
  (31 NOT IN UNNEST(COALESCE(l.reason_ids_present, [])))        AS missing_credit,
  (30 NOT IN UNNEST(COALESCE(l.reason_ids_present, [])))        AS missing_delivery_charge,
  (33 NOT IN UNNEST(COALESCE(l.reason_ids_present, [])))        AS missing_platform_fee
FROM dvs_orders d
LEFT JOIN ledger l ON l.sales_order_id = CAST(d.sales_order_id AS STRING)
LEFT JOIN stores s ON s.store_id = d.retail_store_code
ORDER BY d.retail_store_code, d.reconciled_at
"""

def bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


# Hard limits so a run frozen by laptop sleep fails fast after wake instead of
# hanging for ~40 min and blocking launchd's next trigger (incident 2026-11-09).
BQ_TIMEOUT_S = 120


def fetch_rows(client, query: str) -> list:
    from google.cloud import bigquery

    retry = bigquery.DEFAULT_RETRY.with_deadline(BQ_TIMEOUT_S)
    job_config = bigquery.QueryJobConfig(job_timeout_ms=BQ_TIMEOUT_S * 1000)
    job = client.query(query, job_config=job_config, retry=retry, timeout=60)
    return [dict(row) for row in job.result(timeout=BQ_TIMEOUT_S, retry=retry)]


def fmt_amount(amount) -> str:
    return f"₹{amount:,.0f}" if amount is not None else "—"


# ── Guardrail 1 formatting ───────────────────────────────────────────────────


def missing_labels(row: dict) -> list:
    labels = []
    if row["missing_credit"]:
        labels.append("Credit / Farmer order payment (reason 31)")
    if row["missing_delivery_charge"]:
        labels.append("Delivery Charges debit (reason 30)")
    if row["missing_platform_fee"]:
        labels.append("Platform Fee debit (reason 33)")
    return labels


def compute_ledger_gaps(client, start_date: str, end_date: str) -> dict:
    rows = fetch_rows(client, LEDGER_GAP_QUERY.format(start_date=start_date, end_date=end_date))
    total = len(rows)
    flagged = [
        r for r in rows if r["missing_credit"] or r["missing_delivery_charge"] or r["missing_platform_fee"]
    ]
    distinct_stores = len({r["store_id"] for r in flagged})
    return {"total": total, "flagged": flagged, "distinct_stores": distinct_stores}


def format_ledger_section(result: dict) -> str:
    total, flagged = result["total"], result["flagged"]
    if not flagged:
        return f":white_check_mark: **Guardrail 1 — Ledger Reconciliation: All clear.** 0 of {total} reconciled order(s) missing ledger entries."

    distinct_stores = result["distinct_stores"]
    only_fee_missing = sum(
        1
        for r in flagged
        if r["missing_platform_fee"] and not r["missing_credit"] and not r["missing_delivery_charge"]
    )

    lines = [
        f":ledger: **Guardrail 1 — Ledger Reconciliation**",
        f"{len(flagged)} of {total} reconciled order(s) · {distinct_stores} store(s) · "
        f"missing at least one expected ledger entry.",
    ]
    if only_fee_missing == len(flagged):
        lines.append(
            "_Every flagged order: store was credited and delivery-charge debited fine — only the "
            "Platform Fee debit never posted. Stores are temporarily overpaid by that amount, not stuck._"
        )
    lines.append("")
    for r in flagged:
        lines.append(
            f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
            f"reconciled {r['reconciled_ist']} · {fmt_amount(r['credit_amount'])} · "
            f"missing {', '.join(missing_labels(r))}"
        )

    lines += [
        "",
        ":point_right: Escalate to Finance/Tech — ledger posting failed after reconciliation. "
        "Order ID + Store ID above are required to trace and backfill each entry.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="DVS daily guardrail check (ledger reconciliation)")
    parser.add_argument("--start-date", help="range start, IST (default: yesterday)")
    parser.add_argument("--end-date", help="range end, IST inclusive (default: yesterday)")
    args = parser.parse_args()

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = args.start_date or yesterday
    end_date = args.end_date or yesterday
    label = start_date if start_date == end_date else f"{start_date} to {end_date}"

    log.info("Run started (range=%s to %s)", start_date, end_date)

    try:
        client = bq_client()
        ledger = compute_ledger_gaps(client, start_date, end_date)
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    log.info(
        "Guardrail 1: %d/%d order(s) missing ledger entries across %d store(s).",
        len(ledger["flagged"]), ledger["total"], ledger["distinct_stores"],
    )

    for f in STALE_CANVAS_FILES:
        if os.path.exists(f):
            os.remove(f)

    if not ledger["flagged"]:
        message = (
            f":white_check_mark: **DVS Alerts — {label}: All clear.** "
            f"Ledger reconciliation: 0 of {ledger['total']} reconciled order(s) missing ledger entries."
        )
    else:
        message = "\n\n".join(
            [
                f":robot_face: **DVS Alerts — {label}**",
                format_ledger_section(ledger),
            ]
        )

    with open(ALERT_FILE, "w") as f:
        f.write(message)
    log.info("Alert written to %s:\n%s", ALERT_FILE, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
