#!/usr/bin/env python3
"""
IPT Alerts — daily checks on inter-party transfers (IPTs).

An IPT moves stock from Saathi partner A to partner B. A field officer raises
a DRAFT cart -> both partners approve -> two orders are created (a_order_id =
A's return order, b_order_id = B's purchase order) -> IPT becomes COMPLETED.

Alert 1 — DRAFT > 7 days: cart never sent for approval. No orders exist on a
          DRAFT, so it is safe to DISCARD. Every one is listed every day.
Alert 2 — COMPLETED IPT whose partner-B order is in ERROR status.
Alert 3 — INPROGRESS IPT, both partners APPROVED, partner-B order in
          READY_TO_SHIP (ready but the IPT never closed) or ERROR (order failed,
          usually a credit/OCP block — the reason from the IPT's `remarks` is
          shown). Added ERROR 2026-11-17 after IPT 19538 / order 13883326 fell
          between alerts 2 and 3. Also WMS_SUBMITTED IPT + B order ERROR (added
          2026-11-17; IPTs 19766 / 19761). (DISPATCHED / DELIVERED B orders are
          valid and NOT flagged.)

Posting rule (2026-11-17, same as COCO + DVS alerts):
  alert today -> post the alert; clean day -> post NOTHING;
  Friday + clean -> weekly recap (weekly_recap.py, slogan filled by send.sh).

Table: prod_db_views.order_management_interpartytransfer
       (note: NOT "orderinterpartytransfer" — that name does not exist)
B order status: prod_db_views.order_management_order (sales_order_id = b_order_id)

This script only COMPUTES. It writes the alert (or Friday recap) as one or more Slack message
parts into logs/parts/part_NN.md (each <= MAX_PART_CHARS so `claude -p` can
copy it exactly). Posting is send.sh's job.
All table timestamps are UTC; ages are computed on IST calendar days.

Usage:
    python3 ipt-alerts/run.py
"""

import glob
import logging
import re
import os
import sys
from collections import defaultdict
from datetime import datetime

import weekly_recap

# ── Config ───────────────────────────────────────────────────────────────────

PROJECT = "agrostar-data"
STALE_DAYS = 7                 # Alert 1: DRAFT older than this (IST days)
OLD_DAYS = 30                  # red marker threshold
ALERT3_B_STATUSES = ("READY_TO_SHIP", "ERROR")   # DISPATCHED/DELIVERED are valid orders — excluded
REMARKS_MAX = 160              # error reason shown on Alert 3 ERROR rows
MAX_PART_CHARS = 6000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
PARTS_DIR = os.path.join(LOG_DIR, "parts")
HISTORY_FILE = os.path.join(LOG_DIR, "history.jsonl")

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
    i.b_order_id, i.remarks,
    -- only these 3 columns of order_management_order are read:
    -- sales_order_id (join), status, grand_total
    ob.status      AS b_status,
    ob.grand_total AS b_amount
  FROM `{PROJECT}.prod_db_views.order_management_interpartytransfer` i
  LEFT JOIN `{PROJECT}.prod_db_views.order_management_order` ob
    ON ob.sales_order_id = i.b_order_id
  WHERE i.status IN ('DRAFT', 'COMPLETED', 'INPROGRESS', 'WMS_SUBMITTED')
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
     OR (status = 'WMS_SUBMITTED' AND b_status = 'ERROR')
)
SELECT {COMMON_COLS},
  i.status AS ipt_status,
  i.b_order_id,
  i.b_status,
  i.b_amount,
  i.remarks,
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


def error_reason(remarks) -> str:
    """Short, one-line version of the IPT's error remarks. Remarks that are
    not an error (e.g. 'realCash:True') are not shown as a reason."""
    if "error" not in (remarks or "").lower():
        return "no error reason in IPT remarks — open the B order to see why"
    text = re.sub(r"^\s*ERRORS? in processing\s*:\s*", "", remarks or "", flags=re.I)
    text = " · ".join(p.strip() for p in text.split("||") if p.strip())
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "no reason in remarks"
    return text if len(text) <= REMARKS_MAX else text[: REMARKS_MAX - 1].rstrip() + "…"


def alert3_row(r: dict) -> str:
    row = b_order_row(r) + f" · **{r['b_status']}**"
    if r["ipt_status"] != "INPROGRESS":
        row += f" (IPT {r['ipt_status']})"
    if r["b_status"] == "ERROR":
        row += f"\n      ↳ _{error_reason(r['remarks'])}_"
    return row


def age_buckets(rows: list) -> str:
    b1 = sum(1 for r in rows if r["age_days"] <= OLD_DAYS)
    b2 = sum(1 for r in rows if OLD_DAYS < r["age_days"] <= 180)
    b3 = sum(1 for r in rows if r["age_days"] > 180)
    return f"8–30 days: {b1} · 31–180 days: {b2} · 180+ days: {b3}"


# ── Message assembly ─────────────────────────────────────────────────────────


def build_blocks(drafts: list, alert2: list, alert3: list, today: str) -> list:
    """One message: the three alerts, in order (empty list on a clean day). Alert 1 is counts only (no
    per-IPT list — user asked to keep the post short); alerts 2 and 3 list
    each IPT since they are few."""
    if not (drafts or alert2 or alert3):
        return []   # clean day — post nothing (Friday recap is built separately)

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
            f"3️⃣ **In-progress / WMS-submitted IPT, B order READY_TO_SHIP or ERROR: {len(alert3)} IPT(s) · "
            f"{inr(sum(r['b_amount'] or 0 for r in alert3))}**"
        )
        lines += [alert3_row(r) for r in alert3]
        if any(r["b_status"] == "READY_TO_SHIP" for r in alert3):
            lines.append(":point_right: READY_TO_SHIP — B's order is ready but the IPT never closed. Check why it is stuck and complete it (or cancel both).")
        if any(r["b_status"] == "ERROR" for r in alert3):
            lines.append(":point_right: ERROR — B's order failed. Read the reason above; if it is credit/OCP, partner B must clear dues first, then re-push the order.")
    else:
        lines.append("3️⃣ **In-progress / WMS-submitted IPT, B order READY_TO_SHIP or ERROR:** 0 :white_check_mark:")

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
    now = datetime.now()
    today = now.strftime("%d %b %Y")
    blocks = build_blocks(drafts, alert2, alert3, today)
    is_clean = not blocks

    # History (one row per run day; re-runs replace it) feeds the Friday recap.
    note_parts = []
    if alert2:
        note_parts.append(f"{len(alert2)} completed IPT(s) with B order in ERROR")
    if alert3:
        note_parts.append(f"{len(alert3)} in-progress IPT(s) stuck")
    if drafts:
        note_parts.append(f"{len(drafts)} draft(s) older than {STALE_DAYS} days")
    weekly_recap.save_run(HISTORY_FILE, now.date(), now.date().isoformat(),
                          {"slack": {"alert": not is_clean, "note": " · ".join(note_parts)}})

    if not is_clean:
        parts = pack(blocks)
    elif weekly_recap.is_recap_day(now.date()):
        # Friday + clean -> recap (send.sh fills {{SLOGAN}}). Friday + alert -> alert only.
        parts = [weekly_recap.format_weekly(
            "IPT Alerts", "slack", weekly_recap.load_history(HISTORY_FILE), now.date())]
    else:
        parts = []   # clean day — nothing to post

    for old in glob.glob(os.path.join(PARTS_DIR, "part_*.md")):
        os.remove(old)
    for n, text in enumerate(parts, 1):
        with open(os.path.join(PARTS_DIR, f"part_{n:02d}.md"), "w") as f:
            f.write(text)

    log.info(
        "Alert1 drafts=%d · Alert2 B-ERROR=%d · Alert3 stuck-inprogress=%d; "
        "wrote %d part(s) (largest %d chars)%s",
        len(drafts), len(alert2), len(alert3), len(parts), max((len(p) for p in parts), default=0),
        "" if not is_clean else (" — Friday recap" if parts else " — clean day, nothing to post"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
