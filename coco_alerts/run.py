#!/usr/bin/env python3
"""
COCO Alerts — daily guardrails for COCO (company-owned) stores.

Guardrail 1 — STUCK ORDERS. COCO is walk-in POS: every order should reach
DELIVERED same-day.
    CREATED    > 24h  ->  never handed to fulfillment (usually unicommerce sync failure)  -> Sales Ops to check (Google Chat)
    DISPATCHED        ->  NOT flagged — normal part of the process (user, 2026-11-10)
    any other non-terminal status > 24h  ->  unknown, flag it                              -> Tech to check (Slack, default)

CREATED-status orders are a store/sales-ops action item (ask the store manager
if the sale happened), not a tech issue, so they're split out into their own
Google Chat alert instead of going to the #agrostar-pos Slack channel.

Guardrail 2 — QR PAID BUT NOT REFLECTED. A Razorpay QR payment (is_paid = TRUE)
on a COCO order must produce a settlement row in delivery_payment
(by_user='system', creation_type 'manual' or 'COCO-QR-Payment' — tech switched
the tag on 09 Jul 2026 — same store, same amount).
Checks YESTERDAY's payments only (strictly — no carryover); settlements are
searched from the payment day up to now. Always -> Tech to check (payment-sync
failure, not a store-side issue).

This script only COMPUTES. It writes two alert files — EMPTY on a clean day
(nothing is posted; user, 2026-11-12 — the old daily all-clear is gone):
    logs/latest_alert.md        Tech alert (other non-terminal stuck + QR guardrail)
    logs/latest_alert_gchat.md  Sales Ops alert (CREATED stuck orders only)
On Fridays, a channel with no alert gets a weekly recap instead (heartbeat so
the team knows the schedule still runs) — see weekly_recap.py:
    logs/weekly_recap.md / logs/weekly_recap_gchat.md
Posting is send.sh's job — Slack goes through the user's own claude.ai Slack
connector via `claude -p`; Google Chat goes via a plain incoming webhook.
All table timestamps are UTC; every day boundary here converts via
'Asia/Kolkata' first.

Usage:
    python3 coco_alerts/run.py                    # compute + write latest_alert.md
    python3 coco_alerts/run.py --date 2026-07-07  # guardrail 2 for a specific day
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

import weekly_recap

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT = "agrostar-data"
PROGRAM_START = "2026-05-01"   # bounds the scan; COCO program has no orders before this

CREATED_THRESHOLD_HRS = 24
RED_AGE_HRS = 72               # severity marker in the message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
ALERT_FILE = os.path.join(LOG_DIR, "latest_alert.md")
ALERT_FILE_GCHAT = os.path.join(LOG_DIR, "latest_alert_gchat.md")
WEEKLY_FILE = os.path.join(LOG_DIR, "weekly_recap.md")
WEEKLY_FILE_GCHAT = os.path.join(LOG_DIR, "weekly_recap_gchat.md")
HISTORY_FILE = os.path.join(LOG_DIR, "history.jsonl")

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

# Cost notes (dry-run, Oct 2026): BigQuery has no indexes and the
# order_management_order view is NOT partitioned on created_on, so every
# column read is a full scan. This is the ONLY read of that view: all COCO
# orders since PROGRAM_START (~10k rows), reused for guardrail 1 (stuck filter
# in Python) and guardrail 2 (order -> store lookup in Python). `channel` is
# not read: CUSTOM-channel COCO orders are exactly the never-synced ones
# (345/345 since May 2026), so the never_synced flag already covers it.
# Total run ~600 MB (was ~896 MB: two scans of the order view + channel).
COCO_ORDERS_QUERY = f"""
WITH {STORES_CTE}
SELECT
  o.sales_order_id,
  o.retail_store_code                                            AS store_id,
  COALESCE(s.store_name, o.retail_store_code)                    AS store_name,
  o.status,
  o.unicommerce_id IS NULL                                       AS never_synced,
  ROUND(o.cod_amount + o.online_paid_amount, 2)                  AS amount,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), o.created_on, HOUR)        AS age_hours,
  FORMAT_DATETIME('%d %b', DATETIME(o.created_on, 'Asia/Kolkata')) AS created_ist
FROM `{PROJECT}.prod_db_views.order_management_order` o
LEFT JOIN stores s ON o.retail_store_code = s.store_id
WHERE o.order_type = 'COCO'
  AND o.created_on > '{PROGRAM_START}'
"""

TERMINAL_STATUSES = ("DELIVERED", "RETURNED", "CANCELLED")
# DISPATCHED is part of the normal COCO process — never flag it (2026-11-10).
NOT_FLAGGED_STATUSES = TERMINAL_STATUSES + ("DISPATCHED",)


def stuck_orders(coco_orders: list) -> list:
    """CREATED > 24h, any other non-terminal status > 24h. DISPATCHED is skipped."""
    stuck = [
        o for o in coco_orders
        if o["status"] not in NOT_FLAGGED_STATUSES and o["age_hours"] > CREATED_THRESHOLD_HRS
    ]
    return sorted(stuck, key=lambda o: -o["age_hours"])


# Guardrail 2 uses two result sets matched in Python: individual QR payments on
# {day}, and per-(store, amount) settlement counts recorded since {day} 00:00 IST.
# delivery_payment has no order reference, so matching is store + exact amount,
# by count — two ₹500 payments need two ₹500 settlements.

QR_PAYMENTS_QUERY = """
SELECT
  rz.order_id,
  ROUND(CAST(rz.amount_collected AS FLOAT64), 2)                 AS amount,
  FORMAT_DATETIME('%d %b %H:%M', DATETIME(rz.payment_recieved_at, 'Asia/Kolkata')) AS paid_at_ist
FROM `{project}.prod_db_views.order_management_razorpayqrcodepayment` rz
WHERE rz.is_paid = TRUE
  AND DATE(rz.payment_recieved_at, 'Asia/Kolkata') = '{day}'
ORDER BY rz.payment_recieved_at
"""


def coco_qr_payments(raw_payments: list, coco_orders: list) -> list:
    """Keep only payments on COCO orders and attach store id/name."""
    by_id = {int(o["sales_order_id"]): o for o in coco_orders}
    out = []
    for p in raw_payments:
        o = by_id.get(int(p["order_id"]))
        if o is not None:
            out.append({**p, "store_id": o["store_id"], "store_name": o["store_name"]})
    return out


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
  AND delp.creation_type IN ('manual', 'COCO-QR-Payment')
  AND delp.created_on >= TIMESTAMP('{day} 00:00:00', 'Asia/Kolkata')
GROUP BY 1, 2
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
# Output is standard markdown (**bold**) — the claude.ai Slack MCP tool
# converts it; this is NOT Slack mrkdwn.


def clean_store_name(name: str) -> str:
    return name.replace("COCO_", "").replace("Coco_", "").strip(" .")


def format_stuck_section(orders: list) -> str:
    """Tech-facing (Slack). Only ever fed non-CREATED stuck orders — CREATED
    goes to Sales Ops via format_created_section_gchat instead."""
    total = sum(o["amount"] or 0 for o in orders)
    lines = [
        f":package: **Stuck orders — {len(orders)} order(s) · ₹{total:,.0f}**",
        "_COCO is same-day; these should not exist._",
        "",
    ]
    for o in orders:
        days = o["age_hours"] / 24
        severity = ":red_circle:" if o["age_hours"] > RED_AGE_HRS else ":large_yellow_circle:"
        flags = []
        if o["never_synced"]:
            flags.append("never synced to Unicommerce")
        flag_txt = f" ⚠️ _{' · '.join(flags)}_" if flags else ""
        lines.append(
            f"{severity} **{clean_store_name(o['store_name'])}** — "
            f"order `{o['sales_order_id']}` · ₹{o['amount']:,.0f} · "
            f"{days:.0f}d in {o['status']} (since {o['created_ist']}){flag_txt} → **Tech**"
        )
    lines += [
        "",
        ":point_right: Ask the store manager: **did the sale happen?** "
        "Yes → complete the order in Unicommerce & account the cash · No → cancel it.",
    ]
    return "\n".join(lines)


def format_stuck_clean() -> str:
    return ":white_check_mark: **Stuck orders (Tech):** 0 — all COCO orders closed same-day."


# ── Google Chat formatting (Sales Ops — CREATED stuck orders) ──────────────
# Google Chat's basic formatting differs from Slack mrkdwn: *bold* (single
# asterisk), no :emoji: shortcodes (use literal unicode glyphs instead).


def format_created_section_gchat(orders: list) -> str:
    total = sum(o["amount"] or 0 for o in orders)
    lines = [
        f"📦 *Stuck orders — {len(orders)} order(s) · ₹{total:,.0f}*",
        "COCO is same-day; these should not exist.",
        "",
    ]
    for o in orders:
        days = o["age_hours"] / 24
        severity = "🔴" if o["age_hours"] > RED_AGE_HRS else "🟡"
        flags = []
        if o["never_synced"]:
            flags.append("never synced to Unicommerce")
        flag_txt = f" ⚠️ {' · '.join(flags)}" if flags else ""
        lines.append(
            f"{severity} *{clean_store_name(o['store_name'])}* — "
            f"order `{o['sales_order_id']}` · ₹{o['amount']:,.0f} · "
            f"{days:.0f}d in CREATED (since {o['created_ist']}){flag_txt}"
        )
    lines += [
        "",
        "👉 Ask the store manager: *did the sale happen?* "
        "Yes → complete the order in Unicommerce & account the cash · No → cancel it.",
    ]
    return "\n".join(lines)


def format_qr_clean(qr_payments: list, day: str) -> str:
    qr_txt = (
        f"{len(qr_payments)}/{len(qr_payments)} reflected"
        if qr_payments
        else f"no QR payments on {day}"
    )
    return f":white_check_mark: **QR payments:** {qr_txt}."


def format_qr_section(missing: list, day: str) -> str:
    total = sum(m["amount"] or 0 for m in missing)
    lines = [
        f":credit_card: **QR paid but NOT in delivery payments — {len(missing)} payment(s) · ₹{total:,.0f}**",
        f"_Farmer paid via QR on {day}; store ledger was never credited._",
        "",
    ]
    for m in missing:
        lines.append(
            f":red_circle: **{clean_store_name(m['store_name'])}** — "
            f"order `{m['order_id']}` · ₹{m['amount']:,.0f} · paid {m['paid_at_ist']} → **Tech**"
        )
    lines += [
        "",
        ":point_right: Payment-sync failure — escalate to tech for backfill. "
        "Until fixed, this store's outstanding is overstated by the amount above.",
    ]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="COCO daily guardrail checks (compute only)")
    parser.add_argument("--date", help="day to check for guardrail 2 (default: yesterday IST)")
    args = parser.parse_args()

    day = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    log.info("Run started (qr_check_day=%s)", day)

    try:
        client = bq_client()
        coco_orders = fetch_rows(client, COCO_ORDERS_QUERY)
        raw_qr = fetch_rows(client, QR_PAYMENTS_QUERY.format(project=PROJECT, day=day))
        settlements = fetch_rows(
            client, SETTLEMENTS_QUERY.format(project=PROJECT, stores_cte=STORES_CTE, day=day)
        )
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    stuck = stuck_orders(coco_orders)
    qr_payments = coco_qr_payments(raw_qr, coco_orders)
    missing_qr = unmatched_qr_payments(qr_payments, settlements)
    stuck_created = [o for o in stuck if o["status"] == "CREATED"]
    stuck_tech = [o for o in stuck if o["status"] != "CREATED"]
    log.info(
        "Guardrail 1: %d stuck order(s) (%d CREATED -> Sales Ops, %d other -> Tech). "
        "Guardrail 2: %d/%d QR payment(s) unreflected.",
        len(stuck), len(stuck_created), len(stuck_tech), len(missing_qr), len(qr_payments),
    )

    run_date = datetime.now().date()
    today = run_date.strftime("%d %b %Y")
    is_clean_tech = not stuck_tech and not missing_qr
    is_clean_created = not stuck_created

    # ── Tech alert (Slack) — other non-terminal stuck orders + QR guardrail ──
    if is_clean_tech:
        message = ""  # clean day — nothing to post
    else:
        # At least one guardrail fired. Still state BOTH guardrails' status —
        # a fired guardrail must not push the clean one into silence.
        stuck_part = format_stuck_section(stuck_tech) if stuck_tech else format_stuck_clean()
        qr_part = format_qr_section(missing_qr, day) if missing_qr else format_qr_clean(qr_payments, day)
        reassurance = (
            "🙂 Not to worry — this is usually a small fix on tech's side 🔧 or a knowledge gap "
            "in training 📚. Let's solve it today for a better tomorrow 🚀 💪 ☀️"
        )
        message = (
            f":convenience_store: **COCO Alerts — {today}**\n\n"
            f"{stuck_part}\n\n{qr_part}\n\n{reassurance}"
        )
    weekly_recap.write_or_clear(ALERT_FILE, message)
    log.info("Tech alert written to %s:\n%s", ALERT_FILE, message or "(clean — nothing to post)")

    # ── Sales Ops alert (Google Chat) — CREATED stuck orders only ──────────
    if is_clean_created:
        gchat_message = ""  # clean day — nothing to post
    else:
        gchat_message = (
            f"🏪 *COCO Alerts (Sales Ops) — {today}*\n\n"
            f"{format_created_section_gchat(stuck_created)}"
        )
    weekly_recap.write_or_clear(ALERT_FILE_GCHAT, gchat_message)
    log.info("Sales Ops alert written to %s:\n%s", ALERT_FILE_GCHAT,
             gchat_message or "(clean — nothing to post)")

    # ── History + Friday weekly recap ──────────────────────────────────────
    # Only real daily runs (no --date) count toward history and the recap.
    weekly_text, weekly_text_gchat = "", ""
    if args.date is None:
        tech_notes = []
        if stuck_tech:
            tech_notes.append(f"{len(stuck_tech)} stuck order(s)")
        if missing_qr:
            tech_notes.append(f"{len(missing_qr)} QR payment(s) not reflected")
        weekly_recap.save_run(HISTORY_FILE, run_date, day, {
            "slack": {"alert": not is_clean_tech, "note": " · ".join(tech_notes)},
            "gchat": {"alert": not is_clean_created,
                      "note": f"{len(stuck_created)} stuck order(s) in CREATED" if stuck_created else ""},
        })
        if weekly_recap.is_recap_day(run_date):
            history = weekly_recap.load_history(HISTORY_FILE)
            # Friday with an alert on a channel -> alert only, no recap there.
            if is_clean_tech:
                weekly_text = weekly_recap.format_weekly("COCO Alerts", "slack", history, run_date)
            if is_clean_created:
                weekly_text_gchat = weekly_recap.format_weekly(
                    "COCO Alerts (Sales Ops)", "gchat", history, run_date, gchat=True)
    weekly_recap.write_or_clear(WEEKLY_FILE, weekly_text)
    weekly_recap.write_or_clear(WEEKLY_FILE_GCHAT, weekly_text_gchat)
    if weekly_text or weekly_text_gchat:
        log.info("Weekly recap written:\n%s\n---\n%s", weekly_text, weekly_text_gchat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
