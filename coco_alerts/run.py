#!/usr/bin/env python3
"""
COCO Alerts — stuck order guardrail.

COCO stores are walk-in POS: every order should reach DELIVERED same-day.
This script flags any order violating that:

    CREATED    > 24h  ->  never handed to fulfillment (usually unicommerce sync failure)
    DISPATCHED > 48h  ->  synced but never closed
    any other non-terminal status > 24h  ->  unknown, flag it

Posts one Slack message to #agrostar-pos when violations exist.
Silent when clean (by design) — check logs/ to confirm the run happened.

Usage:
    python3 coco_alerts/run.py             # query + post to Slack
    python3 coco_alerts/run.py --dry-run   # query + print, no Slack post
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime

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


# ── Query ────────────────────────────────────────────────────────────────────

QUERY = f"""
WITH stores AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(name)                       AS store_name
  FROM `{PROJECT}.galaxy_views.institution`
  WHERE LOWER(ancestor_institutions_name) LIKE '% ebo %'
    AND reference_customer_id IS NOT NULL
  GROUP BY 1
)

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


def fetch_stuck_orders() -> list:
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    return [dict(row) for row in client.query(QUERY).result()]


# ── Message ──────────────────────────────────────────────────────────────────


def clean_store_name(name: str) -> str:
    return name.replace("COCO_", "").replace("Coco_", "").strip(" .")


def format_message(orders: list) -> str:
    today = datetime.now().strftime("%d %b %Y")
    total = sum(o["amount"] or 0 for o in orders)
    lines = [
        f":convenience_store: *COCO Stuck Orders — {today}*",
        f"*{len(orders)} order(s) stuck · ₹{total:,.0f}* — COCO is same-day; these should not exist.",
        "",
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
    lines += [
        "",
        ":point_right: Ask the store manager: *did the sale happen?* "
        "Yes → complete the order in Unicommerce & account the cash · No → cancel it.",
    ]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="COCO stuck-order alert")
    parser.add_argument("--dry-run", action="store_true", help="print message, skip Slack")
    args = parser.parse_args()

    log.info("Run started (dry_run=%s)", args.dry_run)
    try:
        orders = fetch_stuck_orders()
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    if not orders:
        log.info("Clean run — 0 stuck orders. Nothing posted.")
        return 0

    message = format_message(orders)
    log.info("Found %d stuck order(s):\n%s", len(orders), message)

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
