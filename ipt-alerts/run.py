#!/usr/bin/env python3
"""
IPT Alerts — daily checks on inter-party transfers (IPTs).

An IPT moves stock from Saathi partner A to partner B. A field officer raises
a DRAFT cart -> both partners approve -> two orders are created (a_order_id =
A's return order, b_order_id = B's purchase order) -> IPT becomes COMPLETED.

Alert 1 — DRAFT > 7 days: cart never sent for approval. No orders exist on a
          DRAFT, so it is safe to DISCARD. Every one is listed every day.
Alert 2 — COMPLETED IPT whose partner-B order is in ERROR status.
Alert 3 — INPROGRESS IPT, both partners APPROVED, partner-B order created and
          in READY_TO_SHIP — the order is ready but the IPT never closed.
          (DISPATCHED / DELIVERED B orders are valid and NOT flagged.)

Table: prod_db_views.order_management_interpartytransfer
       (note: NOT "orderinterpartytransfer" — that name does not exist)
B order status: prod_db_views.order_management_order (sales_order_id = b_order_id)

This script only COMPUTES. It writes the alert as one or more Slack message
parts into logs/parts/part_NN.md (each <= MAX_PART_CHARS so `claude -p` can
copy it exactly). Posting is send.sh's job.
All table timestamps are UTC; ages are computed on IST calendar days.

Usage:
    python3 ipt-alerts/run.py
"""

import glob
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT = "agrostar-data"
STALE_DAYS = 7                 # Alert 1: DRAFT older than this (IST days)
OLD_DAYS = 30                  # red marker threshold
ALERT3_B_STATUSES = ("READY_TO_SHIP",)   # DISPATCHED/DELIVERED are valid orders — excluded
MAX_PART_CHARS = 6000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
PARTS_DIR = os.path.join(LOG_DIR, "parts")

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(PARTS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "ipt_alerts.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ipt_alerts")

# ── BigQuery ─────────────────────────────────────────────────────────────────

COMMON_COLS = """
  i.id,
  REPLACE(i.source, 'B2B', '')                                          AS state,
  TRIM(RTRIM(TRIM(i.from_partner_a_name), ' .'))                        AS partner_a,
  TRIM(RTRIM(TRIM(i.to_partner_b_name), ' .'))                          AS partner_b,
  i.from_partner_a_id                                                   AS partner_a_id,
  i.to_partner_b_id                                                     AS partner_b_id,
  DATE_DIFF(CURRENT_DATE('Asia/Kolkata'), DATE(i.created_on, 'Asia/Kolkata'), DAY) AS age_days,
  FORMAT_DATE('%d %b %y', DATE(i.created_on, 'Asia/Kolkata'))           AS created_ist
"""

# ONE query for all three alerts — `alert` column = 1/2/3, Python splits.
# Cost notes (dry-run, Oct 2026): BigQuery has no indexes and
# order_management_order is NOT partitioned on created_on (a 1-day vs 3-year
# filter reads the same bytes), so a date filter there only ADDS bytes — read
# just the 3 columns needed (sales_order_id, status, grand_total), no date
# filter. Do NOT filter on pushed_on to skip the archive branch: ERROR orders
# may never have been pushed, so they'd be missed. Field-officer name
# (auth_user, +164 MB) was dropped on purpose — partner IDs identify who's
# involved. ~389 MB/run (was ~713 MB); B order status alone is the 275 MB floor.
ALERTS_QUERY = f"""
WITH ipt AS (
  SELECT
    i.id, i.source, i.status, i.created_on,
    i.from_partner_a_name, i.from_partner_a_id, i.from_partner_a_approval,
    i.to_partner_b_name, i.to_partner_b_id, i.to_partner_b_approval,
    i.b_order_id,
    -- only these 3 columns of order_management_order are read:
    -- sales_order_id (join), status, grand_total
    ob.status      AS b_status,
    ob.grand_total AS b_amount
  FROM `{PROJECT}.prod_db_views.order_management_interpartytransfer` i
  LEFT JOIN `{PROJECT}.prod_db_views.order_management_order` ob
    ON ob.sales_order_id = i.b_order_id
  WHERE i.status IN ('DRAFT', 'COMPLETED', 'INPROGRESS')
),
flagged AS (
  SELECT
    ipt.*,
    CASE
      WHEN status = 'DRAFT' THEN 1
      WHEN status = 'COMPLETED' AND b_status = 'ERROR' THEN 2
      ELSE 3
    END AS alert
  FROM ipt
  WHERE (status = 'DRAFT'
         AND NOT REGEXP_CONTAINS(LOWER(CONCAT(IFNULL(from_partner_a_name, ''), ' ', IFNULL(to_partner_b_name, ''))), r'\\btest\\b')
         AND DATE_DIFF(CURRENT_DATE('Asia/Kolkata'), DATE(created_on, 'Asia/Kolkata'), DAY) > {STALE_DAYS})
     OR (status = 'COMPLETED' AND b_status = 'ERROR')
     OR (status = 'INPROGRESS'
         AND from_partner_a_approval = 'APPROVED'
         AND to_partner_b_approval = 'APPROVED'
         AND b_status IN ({", ".join(f"'{s}'" for s in ALERT3_B_STATUSES)}))
)
SELECT {COMMON_COLS},
  i.b_order_id,
  i.b_status,
  i.b_amount,
  i.alert
FROM flagged i
ORDER BY alert, state, age_days, i.id
"""


def fetch_rows(client, query: str) -> list:
    return [dict(row) for row in client.query(query).result()]


# ── Formatting helpers ───────────────────────────────────────────────────────
# Standard markdown (**bold**) — the claude.ai Slack MCP tool converts it;
# this is NOT Slack mrkdwn.


def inr(x) -> str:
    """Indian comma format, full rupees (no lakh/crore rounding)."""
    s = str(int(round(x or 0)))
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups) + "," + tail


def dot(age_days: int) -> str:
    return ":red_circle:" if age_days > OLD_DAYS else ":large_yellow_circle:"


def partners(r: dict) -> str:
    return f"{r['partner_a']} ({r['partner_a_id']}) → {r['partner_b']} ({r['partner_b_id']})"


def b_order_row(r: dict) -> str:
    return (
        f"{dot(r['age_days'])} `{r['id']}` · {r['state']} · {partners(r)} · "
        f"B order `{r['b_order_id']}` · {inr(r['b_amount'])} · {r['age_days']}d old"
    )


def age_buckets(rows: list) -> str:
    b1 = sum(1 for r in rows if r["age_days"] <= OLD_DAYS)
    b2 = sum(1 for r in rows if OLD_DAYS < r["age_days"] <= 180)
    b3 = sum(1 for r in rows if r["age_days"] > 180)
    return f"8–30 days: {b1} · 31–180 days: {b2} · 180+ days: {b3}"


# ── Message assembly ─────────────────────────────────────────────────────────


def build_blocks(drafts: list, alert2: list, alert3: list, today: str) -> list:
    """One message: the three alerts, in order. Alert 1 is counts only (no
    per-IPT list — user asked to keep the post short); alerts 2 and 3 list
    each IPT since they are few."""
    if not (drafts or alert2 or alert3):
        return [[
            f":tada: **IPT Alerts — {today}: All clear!**",
            "",
            "1️⃣ Drafts older than 7 days: 0 :white_check_mark:",
            "2️⃣ Completed IPTs with B order in ERROR: 0 :white_check_mark:",
            "3️⃣ In-progress IPTs stuck at READY_TO_SHIP: 0 :white_check_mark:",
            "",
            "Every IPT is moving the way it should. Stock is reaching the partners who need it, "
            "and nothing is stuck in between. Great work, team — let's keep it this clean tomorrow! :muscle:",
        ]]

    lines = [f":arrows_counterclockwise: **IPT Alerts — {today}**", ""]

    if drafts:
        by_state = defaultdict(int)
        for r in drafts:
            by_state[r["state"]] += 1
        lines += [
            f"1️⃣ **Draft for more than {STALE_DAYS} days: {len(drafts)} IPT(s) — We should DISCARD these IPTs.**",
            age_buckets(drafts),
            "By state — " + " · ".join(f"{s}: {n}" for s, n in sorted(by_state.items(), key=lambda x: -x[1])),
        ]
    else:
        lines.append(f"1️⃣ **Draft for more than {STALE_DAYS} days:** 0 :white_check_mark:")
    lines.append("")

    if alert2:
        lines.append(
            f"2️⃣ **Completed IPT, partner-B order in ERROR: {len(alert2)} IPT(s) · "
            f"{inr(sum(r['b_amount'] or 0 for r in alert2))}**"
        )
        lines += [b_order_row(r) for r in alert2]
        lines.append(":point_right: IPT shows completed, but B's order failed. Check the error on the B order and re-push or recreate it.")
    else:
        lines.append("2️⃣ **Completed IPT, partner-B order in ERROR:** 0 :white_check_mark:")
    lines.append("")

    if alert3:
        lines.append(
            f"3️⃣ **In-progress IPT, both approved, B order READY_TO_SHIP: {len(alert3)} IPT(s) · "
            f"{inr(sum(r['b_amount'] or 0 for r in alert3))}**"
        )
        lines += [b_order_row(r) for r in alert3]
        lines.append(":point_right: B's order is ready to ship but the IPT never closed. Check why it is stuck and complete it (or cancel both).")
    else:
        lines.append("3️⃣ **In-progress IPT, both approved, B order READY_TO_SHIP:** 0 :white_check_mark:")

    return [lines]


def pack(blocks: list) -> list:
    """Greedily pack blocks into messages <= MAX_PART_CHARS; split a block only
    when it alone is too long."""
    parts, current, size = [], [], 0
    for lines in blocks:
        block_size = sum(len(l) + 1 for l in lines) + 1
        if current and size + block_size > MAX_PART_CHARS:
            parts.append("\n".join(current))
            current, size = [], 0
        for i, line in enumerate(lines):
            if current and size + len(line) + 1 > MAX_PART_CHARS:
                parts.append("\n".join(current))
                current = [lines[0] + " _(contd.)_"]
                size = len(current[0]) + 1
            if i == 0 and current:
                current.append("")
                size += 1
            current.append(line)
            size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    log.info("Run started")
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=PROJECT)
        rows = fetch_rows(client, ALERTS_QUERY)
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    drafts = [r for r in rows if r["alert"] == 1]
    alert2 = [r for r in rows if r["alert"] == 2]
    alert3 = sorted((r for r in rows if r["alert"] == 3), key=lambda r: -r["age_days"])
    today = datetime.now().strftime("%d %b %Y")
    parts = pack(build_blocks(drafts, alert2, alert3, today))

    for old in glob.glob(os.path.join(PARTS_DIR, "part_*.md")):
        os.remove(old)
    for n, text in enumerate(parts, 1):
        with open(os.path.join(PARTS_DIR, f"part_{n:02d}.md"), "w") as f:
            f.write(text)

    log.info(
        "Alert1 drafts=%d · Alert2 B-ERROR=%d · Alert3 stuck-inprogress=%d; "
        "wrote %d part(s) (largest %d chars)",
        len(drafts), len(alert2), len(alert3), len(parts), max(len(p) for p in parts),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
