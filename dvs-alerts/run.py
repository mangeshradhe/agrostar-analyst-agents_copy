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

Guardrail 2 — ZONE MATCH. An order's farmer zone (resolved from
csr_shippingaddress via an exact 5-field match — state, district, taluka,
village, pincode — against the static_tables.csr_villageaddress village
master, then zone_id -> static_tables.csr_zone.name) must exactly match the
assigned Saathi store's own declared zone (galaxy_prod.institution.servingZones
— note: NOT exposed via the galaxy_views.institution view, and NOT the same as
the store's own address, which usually fails to resolve via the village master
because stores commonly sit in taluka-center towns rather than listed rural
villages). Scoped to Gujarat/Rajasthan/Madhya Pradesh/Uttar Pradesh only —
csr_zone has zone definitions only for those 4 states; elsewhere "zone" isn't
a concept in the data. institution has occasional duplicate rows per store
where an archived record disagrees with the live one (observed: legacy
"GJ_100"-style codes stranded on archived rows vs current "GJ-E4-08"-style on
the live row) — always filter archive = false before deduplicating, or these
show up as false-positive mismatches.

Guardrail 3 — LMD PARTNER ASSIGNMENT. A DVS order's shipping address
(csr_shippingaddress, same 5-field state/district/taluka/village/pincode as
Guardrail 2) is matched against prod_agroex_db_views.vpm_report_history — a
daily-partitioned, village-level "who covers this village" mapping
(is_active = 1, user_info_id = the LMD partner's username) — using the
snapshot as of (nearest capturedOn on/before) order_placed_date, since the
mapping changes over time. This is the "intended" partner (occasionally more
than one is simultaneously active for a village — any of them counts as a
match). The "actual" partner is resolved via
delivery_shippingpackage.to_franchise_id -> delivery_franchise.id ->
user_info_id (same username space, confirmed by direct join). Flagged when
the actual partner isn't among the intended ones for that village, including
when no partner was assigned at all. No exclusion for Saathi-store
self-delivery (to_franchise_id resolving to the store's own "sathi<id>"
franchise account) — that mismatch is itself a genuine ops error, not a
non-issue. Not scoped to any subset of states (unlike Guardrail 2) since the
village-partner map isn't state-limited the way csr_zone is. Addresses that
don't resolve to any vpm_report_history row are reported as "unresolved",
same as Guardrail 2's unresolved bucket.

This script only COMPUTES. It writes ONE combined alert (covering all three
guardrails) to logs/latest_alert.md — a single all-clear line when all are
clean, otherwise a section per guardrail — and, per guardrail, when more than
CANVAS_STORE_THRESHOLD distinct stores are flagged, the full per-order detail
to a separate Canvas-content file for send.sh to post as a Slack Canvas.
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
CANVAS_STORE_THRESHOLD = 5  # > this many distinct stores flagged -> Canvas instead of inline list
ZONE_STATES = ["Gujarat", "Rajasthan", "Madhya Pradesh", "Uttar Pradesh"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
ALERT_FILE = os.path.join(LOG_DIR, "latest_alert.md")
CANVAS_FILE_LEDGER = os.path.join(LOG_DIR, "canvas_ledger.md")
CANVAS_FILE_ZONE = os.path.join(LOG_DIR, "canvas_zone.md")
CANVAS_FILE_LMD = os.path.join(LOG_DIR, "canvas_lmd.md")

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

# ── BigQuery: Guardrail 2 — Zone match ──────────────────────────────────────

ZONE_STATES_SQL = ",".join(f"'{s}'" for s in ZONE_STATES)

ZONE_MISMATCH_QUERY = f"""
WITH stores AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(name) AS store_name,
    ANY_VALUE(servingZones) AS store_serving_zone,
    ANY_VALUE(address_state) AS store_state
  FROM `{PROJECT}.galaxy_prod.institution`
  WHERE reference_customer_id IS NOT NULL AND archive = false
  GROUP BY 1
),
inst_meta AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(ancestor_institutions_name) AS ancestor_institutions_name
  FROM `{PROJECT}.galaxy_views.institution`
  WHERE reference_customer_id IS NOT NULL AND archive = false
  GROUP BY 1
),
zone_counts AS (
  SELECT
    LOWER(TRIM(state)) AS state, LOWER(TRIM(district)) AS district,
    LOWER(TRIM(taluka)) AS taluka, LOWER(TRIM(village)) AS village, TRIM(pin_code) AS pin_code,
    state AS raw_state, zone_id, COUNT(*) AS n
  FROM `{PROJECT}.static_tables.csr_villageaddress`
  WHERE is_archived = 0 AND zone_id IS NOT NULL
    AND state IN ({ZONE_STATES_SQL})
  GROUP BY 1,2,3,4,5,6,7
),
village_zone AS (
  SELECT state, district, taluka, village, pin_code, raw_state, zone_id,
    ROW_NUMBER() OVER (PARTITION BY state, district, taluka, village, pin_code ORDER BY n DESC) AS rn
  FROM zone_counts
  QUALIFY rn = 1
),
zone_names AS (
  SELECT id, state, name AS zone_name FROM `{PROJECT}.static_tables.csr_zone`
),
dvs_orders AS (
  SELECT o.sales_order_id, o.retail_store_code, o.shipping_address_id, o.created_on
  FROM `{PROJECT}.prod_db_views.order_management_order` o
  JOIN stores s ON s.store_id = o.retail_store_code
  JOIN inst_meta im ON im.store_id = o.retail_store_code
  WHERE DATE(o.created_on, 'Asia/Kolkata') BETWEEN '{{start_date}}' AND '{{end_date}}'
    AND o.retail_store_code IS NOT NULL AND o.retail_store_code != ''
    AND LOWER(im.ancestor_institutions_name) LIKE '%sathi%'
    AND LOWER(im.ancestor_institutions_name) NOT LIKE '% ebo %'
    AND LOWER(TRIM(s.store_state)) IN ({ZONE_STATES_SQL.lower()})
)
SELECT
  d.sales_order_id                                              AS order_id,
  d.retail_store_code                                           AS store_id,
  s.store_name                                                  AS store_name,
  FORMAT_DATETIME('%d %b %H:%M', DATETIME(d.created_on, 'Asia/Kolkata')) AS created_ist,
  fz.zone_name                                                  AS farmer_zone,
  TRIM(s.store_serving_zone)                                    AS store_serving_zone
FROM dvs_orders d
JOIN `{PROJECT}.prod_db_views.csr_shippingaddress` sa ON sa.id = d.shipping_address_id
JOIN stores s ON s.store_id = d.retail_store_code
LEFT JOIN village_zone fvz ON fvz.state = LOWER(TRIM(sa.state)) AND fvz.district = LOWER(TRIM(sa.district)) AND fvz.taluka = LOWER(TRIM(sa.taluka)) AND fvz.village = LOWER(TRIM(sa.village)) AND fvz.pin_code = TRIM(sa.pin_code)
LEFT JOIN zone_names fz ON fz.id = fvz.zone_id AND fz.state = fvz.raw_state
ORDER BY d.retail_store_code, d.created_on
"""

# ── BigQuery: Guardrail 3 — LMD partner assignment ──────────────────────────

LMD_MISMATCH_QUERY = f"""
WITH {LEDGER_STORES_CTE},
dvs_orders AS (
  SELECT
    o.sales_order_id,
    o.retail_store_code,
    o.shipping_address_id,
    sp.to_franchise_id,
    sp.order_placed_date
  FROM `{PROJECT}.prod_db_views.delivery_shippingpackage` sp
  JOIN `{PROJECT}.prod_db_views.order_management_order` o ON CAST(o.unicommerce_id AS STRING) = sp.order_id
  JOIN stores s ON s.store_id = o.retail_store_code
  WHERE o.retail_store_code IS NOT NULL AND o.retail_store_code != ''
    AND LOWER(s.ancestor_institutions_name) LIKE '%sathi%'
    AND LOWER(s.ancestor_institutions_name) NOT LIKE '% ebo %'
    AND DATE(sp.order_placed_date) BETWEEN '{{start_date}}' AND '{{end_date}}'
),
actual_partner AS (
  SELECT d.sales_order_id, df.user_info_id AS actual_lmd_partner
  FROM dvs_orders d
  LEFT JOIN `{PROJECT}.prod_db_views.delivery_franchise` df ON df.id = d.to_franchise_id
),
addr AS (
  SELECT
    d.sales_order_id,
    sa.state AS raw_state,
    LOWER(TRIM(sa.state)) AS state, LOWER(TRIM(sa.district)) AS district,
    LOWER(TRIM(sa.taluka)) AS taluka, LOWER(TRIM(sa.village)) AS village, TRIM(sa.pin_code) AS pincode,
    d.order_placed_date
  FROM dvs_orders d
  JOIN `{PROJECT}.prod_db_views.csr_shippingaddress` sa ON sa.id = d.shipping_address_id
),
vpm AS (
  SELECT
    LOWER(TRIM(state)) AS state, LOWER(TRIM(district)) AS district,
    LOWER(TRIM(taluka)) AS taluka, LOWER(TRIM(village)) AS village, TRIM(pincode) AS pincode,
    capturedOn, user_info_id
  FROM `{PROJECT}.prod_agroex_db_views.vpm_report_history`
  WHERE is_active = 1
),
best_day AS (
  SELECT a.sales_order_id, MAX(DATE(v.capturedOn)) AS best_captured_date
  FROM addr a
  JOIN vpm v ON v.state = a.state AND v.district = a.district AND v.taluka = a.taluka AND v.village = a.village AND v.pincode = a.pincode
  WHERE DATE(v.capturedOn) <= DATE(a.order_placed_date)
  GROUP BY 1
),
intended AS (
  SELECT a.sales_order_id, ARRAY_AGG(DISTINCT v.user_info_id) AS intended_partners
  FROM addr a
  JOIN best_day b ON b.sales_order_id = a.sales_order_id
  JOIN vpm v ON v.state = a.state AND v.district = a.district AND v.taluka = a.taluka AND v.village = a.village AND v.pincode = a.pincode
    AND DATE(v.capturedOn) = b.best_captured_date
  GROUP BY 1
)
SELECT
  d.sales_order_id                                              AS order_id,
  d.retail_store_code                                           AS store_id,
  COALESCE(s.store_name, d.retail_store_code)                   AS store_name,
  FORMAT_DATETIME('%d %b %H:%M', d.order_placed_date)           AS placed_ist,
  a.raw_state                                                   AS state,
  ap.actual_lmd_partner,
  i.intended_partners,
  (i.intended_partners IS NULL)                                 AS unresolved,
  (i.intended_partners IS NOT NULL
    AND (ap.actual_lmd_partner IS NULL
         OR ap.actual_lmd_partner NOT IN UNNEST(i.intended_partners))) AS mismatch
FROM dvs_orders d
LEFT JOIN actual_partner ap ON ap.sales_order_id = d.sales_order_id
LEFT JOIN addr a ON a.sales_order_id = d.sales_order_id
LEFT JOIN intended i ON i.sales_order_id = d.sales_order_id
LEFT JOIN stores s ON s.store_id = d.retail_store_code
ORDER BY d.retail_store_code, d.order_placed_date
"""


def bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


def fetch_rows(client, query: str) -> list:
    return [dict(row) for row in client.query(query).result()]


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


def format_ledger_canvas(flagged: list, total: int, label: str) -> str:
    lines = [
        f"# DVS Ledger Gaps — {label}",
        f"{len(flagged)} of {total} reconciled DVS orders are missing at least one "
        f"expected ledger entry. Order ID + Store ID below are what Finance/Tech need to trace "
        f"and backfill each one.",
        "",
        "| Order ID | Store ID | Store Name | Reconciled (IST) | Missing | Credit Amount |",
        "|---|---|---|---|---|---|",
    ]
    for r in flagged:
        lines.append(
            f"| `{r['order_id']}` | `{r['store_id']}` | {r['store_name']} | "
            f"{r['reconciled_ist']} | {', '.join(missing_labels(r))} | {fmt_amount(r['credit_amount'])} |"
        )
    return "\n".join(lines)


def format_ledger_section(result: dict, use_canvas: bool) -> str:
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

    if use_canvas:
        lines.append(
            f":point_right: {distinct_stores} stores affected (> {CANVAS_STORE_THRESHOLD}-store threshold) — "
            f"full Order ID + Store ID breakdown is in the Ledger Gaps Canvas posted with this message."
        )
        lines.append("")
        lines.append("**Top 5 by credit amount:**")
        top = sorted(flagged, key=lambda r: r["credit_amount"] or 0, reverse=True)[:5]
        for r in top:
            lines.append(
                f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
                f"{fmt_amount(r['credit_amount'])} · missing {', '.join(missing_labels(r))}"
            )
    else:
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


# ── Guardrail 2 formatting ───────────────────────────────────────────────────


def compute_zone_mismatches(client, start_date: str, end_date: str) -> dict:
    rows = fetch_rows(client, ZONE_MISMATCH_QUERY.format(start_date=start_date, end_date=end_date))
    total = len(rows)
    unresolved = [r for r in rows if not r["farmer_zone"] or not r["store_serving_zone"]]
    comparable = [r for r in rows if r["farmer_zone"] and r["store_serving_zone"]]
    flagged = [r for r in comparable if r["farmer_zone"] != r["store_serving_zone"]]
    distinct_stores = len({r["store_id"] for r in flagged})
    return {
        "total": total,
        "unresolved": len(unresolved),
        "flagged": flagged,
        "distinct_stores": distinct_stores,
    }


def format_zone_canvas(flagged: list, total: int, label: str) -> str:
    lines = [
        f"# DVS Zone Mismatches — {label}",
        f"{len(flagged)} of {total} orders (Gujarat/Rajasthan/MP/UP) went to a store whose declared "
        f"zone doesn't match the farmer's zone. Order ID + Store ID below are what Ops needs to "
        f"investigate the routing.",
        "",
        "| Order ID | Store ID | Store Name | Created (IST) | Farmer Zone | Store's Zone |",
        "|---|---|---|---|---|---|",
    ]
    for r in flagged:
        lines.append(
            f"| `{r['order_id']}` | `{r['store_id']}` | {r['store_name']} | "
            f"{r['created_ist']} | {r['farmer_zone']} | {r['store_serving_zone']} |"
        )
    return "\n".join(lines)


def format_zone_section(result: dict, use_canvas: bool) -> str:
    total, flagged, unresolved = result["total"], result["flagged"], result["unresolved"]
    unresolved_note = f" ({unresolved} order(s) couldn't be zone-checked — address didn't match the village master.)" if unresolved else ""

    if not flagged:
        return (
            f":white_check_mark: **Guardrail 2 — Zone Match (GJ/RJ/MP/UP): All clear.** "
            f"0 of {total} order(s) mismatched.{unresolved_note}"
        )

    distinct_stores = result["distinct_stores"]
    lines = [
        f":round_pushpin: **Guardrail 2 — Zone Match (GJ/RJ/MP/UP)**",
        f"{len(flagged)} of {total} order(s) · {distinct_stores} store(s) · "
        f"farmer's zone doesn't match the assigned store's declared zone.{unresolved_note}",
        "",
    ]

    if use_canvas:
        lines.append(
            f":point_right: {distinct_stores} stores affected (> {CANVAS_STORE_THRESHOLD}-store threshold) — "
            f"full Order ID + Store ID breakdown is in the Zone Mismatches Canvas posted with this message."
        )
        lines.append("")
        lines.append("**First 5:**")
        for r in flagged[:5]:
            lines.append(
                f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
                f"farmer zone `{r['farmer_zone']}` vs store zone `{r['store_serving_zone']}`"
            )
    else:
        for r in flagged:
            lines.append(
                f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
                f"created {r['created_ist']} · farmer zone `{r['farmer_zone']}` vs store zone `{r['store_serving_zone']}`"
            )

    lines += [
        "",
        ":point_right: Escalate to Ops — order was routed to a store outside its declared zone. "
        "Order ID + Store ID above are required to investigate.",
    ]
    return "\n".join(lines)


# ── Guardrail 3 formatting ───────────────────────────────────────────────────


def fmt_partners(partners) -> str:
    return ", ".join(partners) if partners else "—"


def compute_lmd_mismatches(client, start_date: str, end_date: str) -> dict:
    rows = fetch_rows(client, LMD_MISMATCH_QUERY.format(start_date=start_date, end_date=end_date))
    total = len(rows)
    unresolved = [r for r in rows if r["unresolved"]]
    flagged = [r for r in rows if not r["unresolved"] and r["mismatch"]]
    distinct_stores = len({r["store_id"] for r in flagged})
    return {"total": total, "unresolved": len(unresolved), "flagged": flagged, "distinct_stores": distinct_stores}


def format_lmd_canvas(flagged: list, total: int, label: str) -> str:
    lines = [
        f"# DVS LMD Partner Mismatches — {label}",
        f"{len(flagged)} of {total} DVS orders were assigned to an LMD partner other than the one "
        f"mapped for the farmer's village. Order ID + Store ID below are what Ops needs to "
        f"investigate the assignment.",
        "",
        "| Order ID | Store ID | Store Name | Placed (IST) | Actual Partner | Intended Partner | State |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in flagged:
        lines.append(
            f"| `{r['order_id']}` | `{r['store_id']}` | {r['store_name']} | {r['placed_ist']} | "
            f"{r['actual_lmd_partner'] or '—'} | {fmt_partners(r['intended_partners'])} | {r['state'] or '—'} |"
        )
    return "\n".join(lines)


def format_lmd_section(result: dict, use_canvas: bool) -> str:
    total, flagged, unresolved = result["total"], result["flagged"], result["unresolved"]
    unresolved_note = f" ({unresolved} order(s) couldn't be checked — address didn't match the village partner map.)" if unresolved else ""

    if not flagged:
        return (
            f":white_check_mark: **Guardrail 3 — LMD Partner Assignment: All clear.** "
            f"0 of {total} order(s) mismatched.{unresolved_note}"
        )

    distinct_stores = result["distinct_stores"]
    lines = [
        f":truck: **Guardrail 3 — LMD Partner Assignment**",
        f"{len(flagged)} of {total} order(s) · {distinct_stores} store(s) · "
        f"assigned to an LMD partner other than the one mapped for the farmer's village.{unresolved_note}",
        "",
    ]

    if use_canvas:
        lines.append(
            f":point_right: {distinct_stores} stores affected (> {CANVAS_STORE_THRESHOLD}-store threshold) — "
            f"full Order ID + Store ID breakdown is in the LMD Mismatches Canvas posted with this message."
        )
        lines.append("")
        lines.append("**First 5:**")
        for r in flagged[:5]:
            lines.append(
                f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
                f"{r['state']} · actual `{r['actual_lmd_partner'] or 'none'}` vs intended `{fmt_partners(r['intended_partners'])}`"
            )
    else:
        for r in flagged:
            lines.append(
                f":red_circle: **{r['store_name']}** — order `{r['order_id']}` (store `{r['store_id']}`) · "
                f"placed {r['placed_ist']} · {r['state']} · actual `{r['actual_lmd_partner'] or 'none'}` vs "
                f"intended `{fmt_partners(r['intended_partners'])}`"
            )

    lines += [
        "",
        ":point_right: Escalate to Ops — order was assigned to the wrong LMD partner (or none) at placement. "
        "Order ID + Store ID above are required to investigate.",
    ]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="DVS daily guardrail checks (Guardrail 1 + 2 + 3, combined)")
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
        zone = compute_zone_mismatches(client, start_date, end_date)
        lmd = compute_lmd_mismatches(client, start_date, end_date)
    except Exception:
        log.exception("BigQuery query failed")
        return 1

    ledger_use_canvas = ledger["distinct_stores"] > CANVAS_STORE_THRESHOLD
    zone_use_canvas = zone["distinct_stores"] > CANVAS_STORE_THRESHOLD
    lmd_use_canvas = lmd["distinct_stores"] > CANVAS_STORE_THRESHOLD

    log.info(
        "Guardrail 1: %d/%d order(s) missing ledger entries across %d store(s). use_canvas=%s",
        len(ledger["flagged"]), ledger["total"], ledger["distinct_stores"], ledger_use_canvas,
    )
    log.info(
        "Guardrail 2: %d/%d order(s) zone-mismatched across %d store(s) (%d unresolved). use_canvas=%s",
        len(zone["flagged"]), zone["total"], zone["distinct_stores"], zone["unresolved"], zone_use_canvas,
    )
    log.info(
        "Guardrail 3: %d/%d order(s) LMD-partner-mismatched across %d store(s) (%d unresolved). use_canvas=%s",
        len(lmd["flagged"]), lmd["total"], lmd["distinct_stores"], lmd["unresolved"], lmd_use_canvas,
    )

    for f in (CANVAS_FILE_LEDGER, CANVAS_FILE_ZONE, CANVAS_FILE_LMD):
        if os.path.exists(f):
            os.remove(f)
    if ledger_use_canvas:
        with open(CANVAS_FILE_LEDGER, "w") as f:
            f.write(format_ledger_canvas(ledger["flagged"], ledger["total"], label))
    if zone_use_canvas:
        with open(CANVAS_FILE_ZONE, "w") as f:
            f.write(format_zone_canvas(zone["flagged"], zone["total"], label))
    if lmd_use_canvas:
        with open(CANVAS_FILE_LMD, "w") as f:
            f.write(format_lmd_canvas(lmd["flagged"], lmd["total"], label))

    if not ledger["flagged"] and not zone["flagged"] and not lmd["flagged"]:
        zone_unresolved_note = f" ({zone['unresolved']} zone-check(s) skipped — address unresolved.)" if zone["unresolved"] else ""
        lmd_unresolved_note = f" ({lmd['unresolved']} LMD-check(s) skipped — address unresolved.)" if lmd["unresolved"] else ""
        message = (
            f":white_check_mark: **DVS Alerts — {label}: All clear.** "
            f"Guardrail 1 (Ledger): 0/{ledger['total']} · Guardrail 2 (Zone Match): 0/{zone['total']} · "
            f"Guardrail 3 (LMD Partner): 0/{lmd['total']}.{zone_unresolved_note}{lmd_unresolved_note}"
        )
    else:
        message = "\n\n".join(
            [
                f":robot_face: **DVS Alerts — {label}**",
                format_ledger_section(ledger, ledger_use_canvas),
                format_zone_section(zone, zone_use_canvas),
                format_lmd_section(lmd, lmd_use_canvas),
            ]
        )

    with open(ALERT_FILE, "w") as f:
        f.write(message)
    log.info("Alert written to %s:\n%s", ALERT_FILE, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
