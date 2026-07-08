#!/usr/bin/env python3
"""
COCO Alerts — daily guardrails for COCO (company-owned) stores.

Guardrail 1 — STUCK ORDERS. COCO is walk-in POS: every order should reach
DELIVERED same-day.
    CREATED    > 24h  ->  never handed to fulfillment (usually unicommerce sync failure)
    DISPATCHED > 48h  ->  synced but never closed
    any other non-terminal status > 24h  ->  unknown, flag it

Guardrail 2 — QR PAID BUT NOT REFLECTED. A Razorpay QR payment (is_paid = TRUE)
on a COCO order must produce a settlement row in delivery_payment
(by_user='system', creation_type='manual', same store, same amount).
Checks YESTERDAY's payments only (strictly — no carryover); settlements are
searched from the payment day up to now. By 9 AM every payment has had 9+
hours to reflect; healthy sync takes seconds.

Posts one Slack message to #agrostar-pos when either guardrail fires.
Silent when clean (by design) — check logs/ to confirm the run happened.

Usage:
    python3 coco_alerts/run.py                    # query + post to Slack
    python3 coco_alerts/run.py --dry-run          # query + print, no Slack post
    python3 coco_alerts/run.py --date 2026-07-07  # guardrail 2 for a specific day
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT = "agrostar-data"
SLACK_CHANNEL = "C0AFTP6QASH"  # #agrostar-pos
PROGRAM_START = "2026-05-01"   # bounds the scan; COCO program has no orders before this

CREATED_THRESHOLD_HRS = 24
DISPATCHED_THRESHOLD_HRS = 48
RED_AGE_HRS = 72               # severity marker in the message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "coco_alerts.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("coco_alerts")

# ── Slack ────────────────────────────────────────────────────────────────────


def slack_token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if token:
        return token
    settings_path = os.path.expanduser("~/.claude/settings.json")
    with open(settings_path) as f:
        return json.load(f)["env"]["SLACK_BOT_TOKEN"]


def post_to_slack(text: str, channel: str) -> None:
    payload = json.dumps(
        {"channel": channel, "text": text, "unfurl_links": False}
    ).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {slack_token()}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise RuntimeError(f"Slack API error: {body.get('error')}")


# ── BigQuery ─────────────────────────────────────────────────────────────────

STORES_CTE = f"""
stores AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(name)                       AS store_name,
    ANY_VALUE(agroex_franchise_id)        AS agroex_franchise_id
  FROM `{PROJECT}.galaxy_views.institution`
  WHERE LOWER(ancestor_institutions_name) LIKE '% ebo %'
    AND reference_customer_id IS NOT NULL
  GROUP BY 1
)
"""

STUCK_ORDERS_QUERY = f"""
WITH {STORES_CTE}
SELECT
  o.sales_order_id,
  COALESCE(s.store_name, o.retail_store_code)                    AS store_name,
  o.status,
  o.unicommerce_id IS NULL                                       AS never_synced,
  o.channel,
  ROUND(o.cod_amount + o.online_paid_amount, 2)                  AS amount,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), o.created_on, HOUR)        AS age_hours,
  FORMAT_DATETIME('%d %b', DATETIME(o.created_on, 'Asia/Kolkata')) AS created_ist
FROM `{PROJECT}.prod_db_views.order_management_order` o
LEFT JOIN stores s ON o.retail_store_code = s.store_id
WHERE o.order_type = 'COCO'
  AND o.created_on > '{PROGRAM_START}'
  AND o.status NOT IN ('DELIVERED', 'RETURNED', 'CANCELLED')
  AND (
        (o.status = 'CREATED'
         AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), o.created_on, HOUR) > {CREATED_THRESHOLD_HRS})
     OR (o.status = 'DISPATCHED'
         AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), o.created_on, HOUR) > {DISPATCHED_THRESHOLD_HRS})
     OR (o.status NOT IN ('CREATED', 'DISPATCHED')
         AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), o.created_on, HOUR) > {CREATED_THRESHOLD_HRS})
  )
ORDER BY age_hours DESC
"""

# Guardrail 2 uses two result sets matched in Python: individual QR payments on
# {day}, and per-(store, amount) settlement counts recorded since {day} 00:00 IST.
# delivery_payment has no order reference, so matching is store + exact amount,
# by count — two ₹500 payments need two ₹500 settlements.

QR_PAYMENTS_QUERY = """
WITH {stores_cte}
SELECT
  rz.order_id,
  o.retail_store_code                                            AS store_id,
  COALESCE(s.store_name, o.retail_store_code)                    AS store_name,
  ROUND(CAST(rz.amount_collected AS FLOAT64), 2)                 AS amount,
  FORMAT_DATETIME('%d %b %H:%M', DATETIME(rz.payment_recieved_at, 'Asia/Kolkata')) AS paid_at_ist
FROM `{project}.prod_db_views.order_management_razorpayqrcodepayment` rz
JOIN `{project}.prod_db_views.order_management_order` o
  ON o.sales_order_id = rz.order_id
LEFT JOIN stores s ON o.retail_store_code = s.store_id
WHERE rz.is_paid = TRUE
  AND o.order_type = 'COCO'
  AND DATE(rz.payment_recieved_at, 'Asia/Kolkata') = '{day}'
ORDER BY rz.payment_recieved_at
"""

SETTLEMENTS_QUERY = """
WITH {stores_cte}
SELECT
  s.store_id,
  ROUND(delph.amount_settled, 2) AS amount,
  COUNT(*)                       AS n
FROM `{project}.prod_db_views.delivery_payment` delp
JOIN `{project}.prod_db_views.delivery_lppostpaidtransaction` delph
  ON delp.lp_postpaid_transaction_id = delph.id
JOIN stores s ON CAST(delp.franchise_id AS STRING) = s.agroex_franchise_id
WHERE delp.by_user = 'system'
  AND delp.creation_type = 'manual'
  AND delp.created_on >= TIMESTAMP('{day} 00:00:00', 'Asia/Kolkata')
GROUP BY 1, 2
"""


def bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


def fetch_rows(client, query: str) -> list:
    return [dict(row) for row in client.query(query).result()]


# ── Guardrail 2 matching ─────────────────────────────────────────────────────


def unmatched_qr_payments(qr_payments: list, settlements: list) -> list:
    """Consume one settlement per QR payment of the same (store, amount);
    whatever can't be matched is missing from delivery payments."""
    pool = {}
    for s in settlements:
        pool[(s["store_id"], s["amount"])] = pool.get((s["store_id"], s["amount"]), 0) + s["n"]
    missing = []
    for q in qr_payments:
        key = (q["store_id"], q["amount"])
        if pool.get(key, 0) > 0:
            pool[key] -= 1
        else:
            missing.append(q)
    return missing


# ── Message formatting ───────────────────────────────────────────────────────


def clean_store_name(name: str) -> str:
    return name.replace("COCO_", "").replace("Coco_", "").strip(" .")


def format_stuck_section(orders: list) -> str:
    total = sum(o["amount"] or 0 for o in orders)
    lines = [
        f":package: *Stuck orders — {len(orders)} order(s) · ₹{total:,.0f}*",
        "_COCO is same-day; these should not exist._",
    ]
    for o in orders:
        days = o["age_hours"] / 24
        severity = ":red_circle:" if o["age_hours"] > RED_AGE_HRS else ":large_yellow_circle:"
        flags = []
        if o["never_synced"]:
            flags.append("never synced to Unicommerce")
        if o["channel"] == "CUSTOM":
            flags.append("CUSTOM channel")
        flag_txt = f"  ⚠️ _{' · '.join(flags)}_" if flags else ""
        lines.append(
            f"{severity} *{clean_store_name(o['store_name'])}* — "
            f"order `{o['sales_order_id']}` · ₹{o['amount']:,.0f} · "
            f"{days:.0f}d in {o['status']} (since {o['created_ist']}){flag_txt}"
        )
    lines.append(
        ":point_right: Ask the store manager: *did the sale happen?* "
        "Yes → complete the order in Unicommerce & account the cash · No → cancel it."
    )
    return "\n".join(lines)


def format_qr_section(missing: list, day: str) -> str:
    total = sum(m["amount"] or 0 for m in missing)
    lines = [
        f":credit_card: *QR paid but NOT in delivery payments — {len(missing)} payment(s) · ₹{total:,.0f}*",
        f"_Farmer paid via QR on {day}; store ledger was never credited._",
    ]
    for m in missing:
        lines.append(
            f":red_circle: *{clean_store_name(m['store_name'])}* — "
            f"order `{m['order_id']}` · ₹{m['amount']:,.0f} · paid {m['paid_at_ist']}"
        )
    lines.append(
        ":point_right: Payment-sync failure — escalate to tech for backfill. "
        "Until fixed, this store's outstanding is overstated by the amount above."
    )
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="COCO daily guardrail alerts")
    parser.add_argument("--dry-run", action="store_true", help="print message, skip Slack")
    parser.add_argument("--date", help="day to check for guardrail 2 (default: yesterday IST)")
    args = parser.parse_args()

    day = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    log.info("Run started (dry_run=%s, qr_check_day=%s)", args.dry_run, day)

    try:
        client = bq_client()
        stuck = fetch_rows(client, STUCK_ORDERS_QUERY)
        qr_payments = fetch_rows(
            client, QR_PAYMENTS_QUERY.format(project=PROJECT, stores_cte=STORES_CTE, day=day)
        )
        settlements = fetch_rows(
            client, SETTLEMENTS_QUERY.format(project=PROJECT, stores_cte=STORES_CTE, day=day)
        )
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    missing_qr = unmatched_qr_payments(qr_payments, settlements)
    log.info(
        "Guardrail 1: %d stuck order(s). Guardrail 2: %d/%d QR payment(s) unreflected.",
        len(stuck), len(missing_qr), len(qr_payments),
    )

    sections = []
    if stuck:
        sections.append(format_stuck_section(stuck))
    if missing_qr:
        sections.append(format_qr_section(missing_qr, day))

    if not sections:
        log.info("Clean run — nothing posted.")
        return 0

    today = datetime.now().strftime("%d %b %Y")
    message = f":convenience_store: *COCO Alerts — {today}*\n\n" + "\n\n".join(sections)
    log.info("Alert message:\n%s", message)

    if args.dry_run:
        return 0

    try:
        post_to_slack(message, SLACK_CHANNEL)
        log.info("Posted to Slack channel %s", SLACK_CHANNEL)
    except Exception:
        log.exception("Slack post failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
