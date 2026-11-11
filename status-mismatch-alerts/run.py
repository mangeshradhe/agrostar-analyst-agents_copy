#!/usr/bin/env python3
"""
Status Mismatch Alert — daily CRM vs WMS vs AgroEx order-status reconciliation.

Scope (decided with the user 2026-11-11):
  * Sale orders placed in the last 30 days, EXCLUDING today (IST).
  * FC orders   -> WMS + CRM + AgroEx
    DVS orders  -> CRM + AgroEx            (order_type = 'STORE-ORDER')
    COCO orders -> CRM + WMS + AgroEx      (order_type = 'COCO')
  * Excluded: IPT forward/return, stock-transfer, return and offline orders.
  * DVS same-stage sub-status differences (e.g. PICKED_BY_LMD vs hold) are NOT flagged.
  * No @-mentions, no Slack Canvas. Max 10 orders per group, then "+N more".

All matching logic lives in status_mismatch_query.sql (the same SQL is shared
with the team for the channel description). This script only runs that query,
groups the rows and writes Slack-ready message parts to logs/parts/. Posting is
send.sh's job, via the user's own claude.ai Slack connector (`claude -p`).

Orders flagged today but not in the previous run's list are marked 🆕
(history kept in logs/flagged_history.json).
"""

import json
import logging
import os
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta

PROJECT = "agrostar-data"
WINDOW_DAYS = 30
MAX_PER_GROUP = 10
PART_CHAR_LIMIT = 3800          # keep each Slack message comfortably short
BQ_TIMEOUT_S = 180              # fail fast if the laptop sleeps mid-query (see 2026-11-09 incident)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQL_FILE = os.path.join(BASE_DIR, "status_mismatch_query.sql")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PARTS_DIR = os.path.join(LOG_DIR, "parts")
HISTORY_FILE = os.path.join(LOG_DIR, "flagged_history.json")

os.makedirs(PARTS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(os.path.join(LOG_DIR, "status_mismatch_alerts.log")), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("status_mismatch_alerts")


def fetch_rows() -> list:
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    retry = bigquery.DEFAULT_RETRY.with_deadline(BQ_TIMEOUT_S)
    job_config = bigquery.QueryJobConfig(job_timeout_ms=BQ_TIMEOUT_S * 1000)
    with open(SQL_FILE) as f:
        query = f.read()
    job = client.query(query, job_config=job_config, retry=retry, timeout=60)
    return [dict(r) for r in job.result(timeout=BQ_TIMEOUT_S, retry=retry)]


def load_history() -> dict:
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_history(history: dict, today: str, ids: list) -> None:
    history[today] = sorted(ids)
    keep = sorted(history)[-14:]  # two weeks is plenty
    with open(HISTORY_FILE, "w") as f:
        json.dump({d: history[d] for d in keep}, f)


def order_key(r: dict) -> str:
    return r["order_id"] or f"so-{r['sales_order_id']}"


def fmt_line(r: dict, is_new: bool) -> str:
    statuses = f"CRM {r['crm_status']} | AgroEx {r['agroex_status']}"
    if r["seg"] != "DVS":
        statuses += f" | WMS {r['wms_status']}"
    fac = r["facility"] or "—"
    new = " 🆕" if is_new else ""
    return f"• `{order_key(r)}` · {r['seg']} · {fac} · {statuses} · {r['age_days']}d{new}"


def group_title(issue: str) -> str:
    return issue.split(". ", 1)[1] if ". " in issue else issue


def build_sections(rows: list, prev_ids: set, has_prev: bool) -> list:
    groups = OrderedDict()
    for r in rows:  # rows already ordered by issue, age desc
        groups.setdefault(r["issue"], []).append(r)

    sections = []
    for n, (issue, items) in enumerate(groups.items(), start=1):
        seg_counts = Counter(r["seg"] for r in items)
        seg_txt = " · ".join(f"{s} {seg_counts[s]}" for s in ("FC", "DVS", "COCO") if seg_counts[s])
        # new ones first so they never hide behind "+N more", then oldest first
        items = sorted(items, key=lambda r: (not (has_prev and order_key(r) not in prev_ids), -r["age_days"]))
        lines = [f"*{n}. {group_title(issue)} — {len(items)}*  ({seg_txt})"]
        for r in items[:MAX_PER_GROUP]:
            lines.append(fmt_line(r, has_prev and order_key(r) not in prev_ids))
        if len(items) > MAX_PER_GROUP:
            lines.append(f"_+{len(items) - MAX_PER_GROUP} more_")
        sections.append("\n".join(lines))
    return sections


def pack_parts(header: str, sections: list) -> list:
    parts, current = [], header
    for sec in sections:
        if len(current) + 2 + len(sec) > PART_CHAR_LIMIT:
            parts.append(current)
            current = sec
        else:
            current += "\n\n" + sec
    parts.append(current)
    return parts


def main() -> int:
    today = datetime.now()
    start = (today - timedelta(days=WINDOW_DAYS)).strftime("%d %b")
    end = (today - timedelta(days=1)).strftime("%d %b %Y")
    today_s = today.strftime("%Y-%m-%d")
    log.info("Run started (window %s – %s)", start, end)

    try:
        rows = fetch_rows()
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    history = load_history()
    prev_dates = [d for d in sorted(history) if d < today_s]
    has_prev = bool(prev_dates)
    prev_ids = set(history[prev_dates[-1]]) if has_prev else set()
    ids = [order_key(r) for r in rows]
    new_count = sum(1 for i in ids if i not in prev_ids) if has_prev else 0

    for f in os.listdir(PARTS_DIR):
        os.remove(os.path.join(PARTS_DIR, f))

    title = f"*Status Mismatch Alert — {today.strftime('%d %b %Y')}*"
    scope = f"Orders placed {start} – {end} · FC: WMS+CRM+AgroEx · DVS: CRM+AgroEx · COCO: CRM+WMS+AgroEx"

    if not rows:
        parts = [f":white_check_mark: {title}\n{scope}\nAll clear — 0 orders with a status mismatch."]
    else:
        seg_counts = Counter(r["seg"] for r in rows)
        seg_txt = " · ".join(f"{s} {seg_counts[s]}" for s in ("FC", "DVS", "COCO") if seg_counts[s])
        summary = f"*{len(rows)} orders need action* ({seg_txt})"
        if has_prev:
            summary += f" · 🆕 {new_count} new since last run"
        header = f":rotating_light: {title}\n{scope}\n{summary}"
        parts = pack_parts(header, build_sections(rows, prev_ids, has_prev))

    for i, text in enumerate(parts, start=1):
        with open(os.path.join(PARTS_DIR, f"part_{i:02d}.md"), "w") as f:
            f.write(text)
    save_history(history, today_s, ids)

    log.info("%d flagged order(s), %d new; %d part(s) written:\n%s", len(rows), new_count, len(parts), "\n\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
