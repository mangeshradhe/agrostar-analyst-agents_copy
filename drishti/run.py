#!/usr/bin/env python3
"""
DVS DRISHTI — Autonomous Daily Analyst
Runs every morning, scans the DVS program, files predictions, posts to Slack.

Usage:
    python drishti/run.py
    python drishti/run.py --dry-run        # Skip Slack posting
    python drishti/run.py --no-predictions # Skip filing new predictions
"""

import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Third-party ──────────────────────────────────────────────────────────────
try:
    from google.cloud import bigquery
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError as e:
    print(f"[DRISHTI] Missing dependency: {e}")
    print("  pip install google-cloud-bigquery slack-sdk")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT = "agrostar-data"

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")

# Slack channels
# All output → Darpan Pathar DM during pilot (darpan.pathar@agrostar.in)
# Slack user ID: U5AR2LLBZ | DM channel: D0B7Y65ENRW
_DM_DARPAN = "D0B7Y65ENRW"
SLACK_CHANNEL_SUMMARY  = os.environ.get("DRISHTI_SLACK_SUMMARY",  _DM_DARPAN)
SLACK_CHANNEL_OPS      = os.environ.get("DRISHTI_SLACK_OPS",      _DM_DARPAN)
SLACK_CHANNEL_FINANCE  = os.environ.get("DRISHTI_SLACK_FINANCE",  _DM_DARPAN)
SLACK_CHANNEL_LMD      = os.environ.get("DRISHTI_SLACK_LMD",      _DM_DARPAN)

SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")

# Program baselines (from FY27 analysis)
BASELINE = {
    "routing_rate_pct":     48.5,   # % of B2C demand pushed to DVS partner
    "fulfillment_rate_pct": 43.7,   # % of routed demand actually fulfilled
    "rto_rate_pct":         24.1,   # % of delivered+returned that are returned
    "dvs_3day_sla_pct":     38.4,   # % of PACKED→DELIVERED within 3 days
    "fc_5day_sla_pct":      28.7,   # % of FC dispatch within 5 days of order
    "packed_to_picked_p50_hrs": 51, # median hrs PACKED → PICKED_BY_LMD (target=24)
}

# SLA thresholds (hours)
SLA = {
    "dvs_packed_warning_hrs":  24,
    "dvs_packed_critical_hrs": 48,
    "fc_dispatch_warning_hrs":  72,
    "fc_dispatch_critical_hrs": 120,
}

# Dead store category labels
DEAD_CATEGORIES = ["STARVED", "TRULY_DEAD", "OCP_BLOCKED", "CLOSED"]

# LMD flag labels
LMD_FLAGS = [
    "SELECTIVE_DEPRIORITIZATION",
    "GENUINE_OVERLOAD",
    "LAST_MILE_FAILURE",
    "HEALTHY",
]

IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Memory
# ─────────────────────────────────────────────────────────────────────────────

def _default_memory() -> dict:
    return {
        "schema_version": 2,
        "predictions": [],          # list of prediction dicts
        "rules": {
            "prediction_accuracy": {
                "total":       0,
                "correct":     0,
                "directional": 0,
                "wrong":       0,
                "inconclusive": 0,
            }
        },
        "baselines": BASELINE.copy(),
        "last_run": None,
    }


def load_memory() -> dict:
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Back-fill missing keys
            defaults = _default_memory()
            for k, v in defaults.items():
                data.setdefault(k, v)
            data["rules"].setdefault("prediction_accuracy", defaults["rules"]["prediction_accuracy"])
            print(f"[DRISHTI] Memory loaded — {len(data['predictions'])} predictions on record")
            return data
        except Exception as e:
            print(f"[DRISHTI] Warning: could not read memory ({e}). Starting fresh.")
    return _default_memory()


def save_memory(memory: dict) -> None:
    memory["last_run"] = datetime.now(IST).isoformat()
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False, default=str)
    print(f"[DRISHTI] Memory saved → {MEMORY_PATH}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Score Pending Predictions
# ─────────────────────────────────────────────────────────────────────────────

def _bq_scalar(bq_client, sql: str) -> Any:
    """Run a BQ query that returns a single row × single column. Returns the value."""
    rows = list(bq_client.query(sql).result())
    if rows:
        return rows[0][0]
    return None


def score_pending_predictions(memory: dict, bq_client) -> list[dict]:
    """
    Check predictions filed >= 7 days ago that are still PENDING.
    For DEAD_STORE_RISK / STARVED_RISK — query if the store fulfilled any orders
    since the prediction date.
    Updates memory in-place. Returns list of newly scored predictions.
    """
    today = datetime.now(IST).date()
    cutoff = today - timedelta(days=7)
    acc = memory["rules"]["prediction_accuracy"]
    scored = []

    for pred in memory["predictions"]:
        if pred.get("result") != "PENDING":
            continue
        filed = datetime.fromisoformat(pred["filed_date"]).date()
        if filed > cutoff:
            continue  # Too recent

        ptype = pred.get("type", "")
        entity_id = pred.get("entity_id")
        filed_str = filed.isoformat()

        result = "INCONCLUSIVE"

        try:
            if ptype in ("DEAD_STORE_RISK", "STARVED_RISK") and entity_id:
                # Did the store fulfill any orders since prediction was filed?
                sql = f"""
                    SELECT COUNT(DISTINCT o.sales_order_id) AS cnt
                    FROM `agrostar-data.prod_db_views.order_management_order` o
                    WHERE DATE(o.created_on) >= '{filed_str}'
                      AND DATE(o.created_on) <= '{today.isoformat()}'
                      AND o.retail_store_code IS NOT NULL
                      AND o.retail_store_code != ''
                      AND SAFE_CAST(o.retail_store_code AS INT64) = {entity_id}
                      AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
                      AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
                      AND o.unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
                """
                fulfilled_count = _bq_scalar(bq_client, sql) or 0
                if ptype == "DEAD_STORE_RISK":
                    # Prediction was "store will stay dead / underperform"
                    if fulfilled_count == 0:
                        result = "CORRECT"
                    elif fulfilled_count <= 3:
                        result = "DIRECTIONAL"
                    else:
                        result = "WRONG"
                elif ptype == "STARVED_RISK":
                    # Prediction was "store is starved of demand routed to it"
                    # If it fulfilled orders, demand came back → wrong prediction, or issue resolved
                    if fulfilled_count >= 5:
                        result = "WRONG"
                    elif fulfilled_count > 0:
                        result = "DIRECTIONAL"
                    else:
                        result = "CORRECT"

            elif ptype == "LMD_DEPRIORITIZATION" and entity_id:
                # Check if LMD's HOLD_BY_LMD % dropped (recovered) or stayed high
                sql = f"""
                    WITH lmd_pkg AS (
                        SELECT
                            sp.to_franchise_id,
                            COUNT(*) AS total_pkg,
                            COUNTIF(ohm.status = 'HOLD_BY_LMD') AS hold_count
                        FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                        JOIN `agrostar-data.prod_db_views.order_management_order` o
                          ON sp.order_id = CAST(o.unicommerce_id AS STRING)
                        LEFT JOIN `agrostar-data.prod_db_views.order_management_orderhistorymeta` ohm
                          ON ohm.order_id = CAST(o.sales_order_id AS INT64)
                         AND ohm.status = 'HOLD_BY_LMD'
                        WHERE sp.to_franchise_id = {entity_id}
                          AND DATE(sp.order_placed_date) >= '{filed_str}'
                          AND DATE(sp.order_placed_date) <= '{today.isoformat()}'
                          AND o.retail_store_code IS NOT NULL
                          AND o.retail_store_code != ''
                    )
                    SELECT
                        SAFE_DIVIDE(hold_count, total_pkg) * 100 AS hold_pct
                    FROM lmd_pkg
                """
                hold_pct = _bq_scalar(bq_client, sql)
                if hold_pct is None:
                    result = "INCONCLUSIVE"
                elif hold_pct >= 30:
                    result = "CORRECT"     # Still holding → prediction correct
                elif hold_pct >= 15:
                    result = "DIRECTIONAL"
                else:
                    result = "WRONG"       # LMD improved

        except Exception as e:
            print(f"  [score] Error scoring prediction {pred['id']}: {e}")
            result = "INCONCLUSIVE"

        pred["result"] = result
        pred["scored_on"] = today.isoformat()
        scored.append(pred)

        # Update accuracy counters
        acc["total"] += 1
        if result == "CORRECT":
            acc["correct"] += 1
        elif result == "DIRECTIONAL":
            acc["directional"] += 1
        elif result == "WRONG":
            acc["wrong"] += 1
        else:
            acc["inconclusive"] += 1

        print(f"  [score] {pred['id']} ({pred['type']}, {pred.get('entity_name', '?')}) → {result}")

    return scored


# ─────────────────────────────────────────────────────────────────────────────
# 3. Health Scan
# ─────────────────────────────────────────────────────────────────────────────

def run_health_scan(bq_client) -> dict:
    """
    7-day rolling program health metrics.
    Returns dict with rates, counts, and baseline deviation flags.
    """
    today = datetime.now(IST).date()
    window_start = (today - timedelta(days=7)).isoformat()
    window_end = today.isoformat()

    sql = f"""
        WITH b2c_orders AS (
            SELECT
                o.sales_order_id,
                o.retail_store_code,
                o.unicommerce_id,
                o.created_on,
                o.unicommerce_status,
                pt.dvsResolutionReason
            FROM `agrostar-data.prod_db_views.order_management_order` o
            LEFT JOIN (
                SELECT cartId, dvsResolutionReason,
                       ROW_NUMBER() OVER (PARTITION BY cartId ORDER BY createdOn DESC) AS rn
                FROM `agrostar-data.prod_db_views.PromisedTAT`
            ) pt ON pt.cartId = o.cart_id AND pt.rn = 1
            WHERE DATE(o.created_on) >= '{window_start}'
              AND DATE(o.created_on) < '{window_end}'
              AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
              AND o.unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
              AND o.status NOT IN ('MOB_APP_UNVERIFIED')
              AND o.status NOT LIKE 'edited%'
              AND o.unicommerce_status NOT LIKE 'edited%'
        ),
        dvs_routed AS (
            SELECT *
            FROM b2c_orders
            WHERE dvsResolutionReason LIKE '%resolved-yes%'
        ),
        dvs_fulfilled AS (
            SELECT *
            FROM dvs_routed
            WHERE retail_store_code IS NOT NULL
              AND retail_store_code != ''
        ),
        rto_base AS (
            SELECT
                sp.delivery_status,
                sp.order_id,
                sp.to_franchise_id
            FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
            JOIN dvs_fulfilled df
              ON sp.order_id = CAST(df.unicommerce_id AS STRING)
            WHERE sp.delivery_status IN (
                'delivered','returned','process_for_return',
                'return_manifest_created','return_in_transit','returned_by_lmd'
            )
            QUALIFY ROW_NUMBER() OVER (PARTITION BY sp.order_id ORDER BY sp.order_placed_date DESC) = 1
        )
        SELECT
            COUNT(DISTINCT b.sales_order_id)   AS b2c_demand,
            COUNT(DISTINCT dr.sales_order_id)  AS dvs_routed,
            COUNT(DISTINCT df.sales_order_id)  AS dvs_fulfilled,
            ROUND(
                SAFE_DIVIDE(COUNT(DISTINCT dr.sales_order_id), COUNT(DISTINCT b.sales_order_id)) * 100,
                1
            ) AS routing_rate_pct,
            ROUND(
                SAFE_DIVIDE(COUNT(DISTINCT df.sales_order_id), COUNT(DISTINCT dr.sales_order_id)) * 100,
                1
            ) AS fulfillment_rate_pct,
            ROUND(
                SAFE_DIVIDE(
                    COUNTIF(rb.delivery_status IN ('returned','process_for_return','return_manifest_created','return_in_transit','returned_by_lmd')),
                    COUNT(rb.order_id)
                ) * 100,
                1
            ) AS rto_rate_pct
        FROM b2c_orders b
        LEFT JOIN dvs_routed  dr ON dr.sales_order_id = b.sales_order_id
        LEFT JOIN dvs_fulfilled df ON df.sales_order_id = b.sales_order_id
        LEFT JOIN rto_base rb ON TRUE
    """

    try:
        rows = list(bq_client.query(sql).result())
        row = rows[0] if rows else {}

        b2c_demand         = int(row[0] or 0)
        dvs_routed         = int(row[1] or 0)
        dvs_fulfilled      = int(row[2] or 0)
        routing_rate_pct   = float(row[3] or 0)
        fulfillment_rate_pct = float(row[4] or 0)
        rto_rate_pct       = float(row[5] or 0)

    except Exception as e:
        print(f"[health_scan] Query error: {e}")
        b2c_demand = dvs_routed = dvs_fulfilled = 0
        routing_rate_pct = fulfillment_rate_pct = rto_rate_pct = 0.0

    # Baseline deviation flags (>3pp = flag)
    flags = {}
    if (BASELINE["routing_rate_pct"] - routing_rate_pct) > 3:
        flags["routing_rate"] = f"{routing_rate_pct:.1f}% vs baseline {BASELINE['routing_rate_pct']}% (-{BASELINE['routing_rate_pct']-routing_rate_pct:.1f}pp)"
    if (BASELINE["fulfillment_rate_pct"] - fulfillment_rate_pct) > 3:
        flags["fulfillment_rate"] = f"{fulfillment_rate_pct:.1f}% vs baseline {BASELINE['fulfillment_rate_pct']}% (-{BASELINE['fulfillment_rate_pct']-fulfillment_rate_pct:.1f}pp)"
    if (rto_rate_pct - BASELINE["rto_rate_pct"]) > 3:
        flags["rto_rate"] = f"{rto_rate_pct:.1f}% vs baseline {BASELINE['rto_rate_pct']}% (+{rto_rate_pct-BASELINE['rto_rate_pct']:.1f}pp)"

    result = {
        "window_start": window_start,
        "window_end": window_end,
        "b2c_demand": b2c_demand,
        "dvs_routed": dvs_routed,
        "dvs_fulfilled": dvs_fulfilled,
        "routing_rate_pct": routing_rate_pct,
        "fulfillment_rate_pct": fulfillment_rate_pct,
        "rto_rate_pct": rto_rate_pct,
        "flags": flags,
    }
    print(f"[health_scan] routing={routing_rate_pct}% fulfillment={fulfillment_rate_pct}% rto={rto_rate_pct}% flags={list(flags.keys())}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. Live Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_live_pipeline(bq_client) -> dict:
    """
    Pending DVS and FC orders — stage breakdown + SLA breach counts.
    Returns dict with 'dvs_stages' (list) and 'fc_summary' (dict).
    """
    today = datetime.now(IST).date()
    since = (today - timedelta(days=60)).isoformat()

    # DVS pipeline: pending orders (not yet delivered or returned) per latest stage
    dvs_sql = f"""
        WITH dvs_orders AS (
            SELECT
                o.sales_order_id,
                o.unicommerce_id,
                o.created_on
            FROM `agrostar-data.prod_db_views.order_management_order` o
            WHERE DATE(o.created_on) >= '{since}'
              AND o.retail_store_code IS NOT NULL
              AND o.retail_store_code != ''
              AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
              AND o.unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
              AND o.status NOT IN ('MOB_APP_UNVERIFIED')
        ),
        latest_status AS (
            SELECT
                ohm.order_id,
                ohm.status AS stage,
                ohm.created_on AS status_time,
                ROW_NUMBER() OVER (
                    PARTITION BY ohm.order_id ORDER BY ohm.created_on DESC
                ) AS rn
            FROM `agrostar-data.prod_db_views.order_management_orderhistorymeta` ohm
            JOIN dvs_orders dvo ON ohm.order_id = CAST(dvo.sales_order_id AS INT64)
        ),
        pending AS (
            SELECT
                dvo.sales_order_id,
                dvo.created_on,
                ls.stage,
                ls.status_time,
                TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), ls.status_time, HOUR) AS hrs_waiting
            FROM dvs_orders dvo
            JOIN latest_status ls
              ON ls.order_id = CAST(dvo.sales_order_id AS INT64)
             AND ls.rn = 1
            WHERE ls.stage NOT IN ('DELIVERED', 'RETURNED', 'RETURNED_BY_LMD')
        )
        SELECT
            stage,
            COUNT(*) AS orders,
            APPROX_QUANTILES(hrs_waiting, 2)[OFFSET(1)] AS median_hrs_waiting,
            COUNTIF(hrs_waiting > {SLA['dvs_packed_critical_hrs']}) AS past_sla_critical,
            COUNTIF(DATE(status_time) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 7 DAY)) AS past_7days
        FROM pending
        GROUP BY stage
        ORDER BY
            CASE stage
                WHEN 'WAITING_FOR_PARTNER_APPROVAL' THEN 1
                WHEN 'ON_HOLD' THEN 2
                WHEN 'PACKED' THEN 3
                WHEN 'PUSHED' THEN 4
                WHEN 'HOLD_BY_LMD' THEN 5
                WHEN 'PICKED_BY_LMD' THEN 6
                ELSE 7
            END
    """

    dvs_stages = []
    try:
        for row in bq_client.query(dvs_sql).result():
            dvs_stages.append({
                "stage":            row[0],
                "orders":           int(row[1] or 0),
                "median_hrs_waiting": float(row[2] or 0),
                "past_3day_sla":    int(row[3] or 0),   # past critical SLA
                "past_7days":       int(row[4] or 0),
            })
    except Exception as e:
        print(f"[live_pipeline] DVS query error: {e}")

    # FC pipeline: non-DVS orders from last 60 days
    fc_sql = f"""
        WITH fc_orders AS (
            SELECT
                o.sales_order_id,
                o.unicommerce_id,
                o.created_on
            FROM `agrostar-data.prod_db_views.order_management_order` o
            WHERE DATE(o.created_on) >= '{since}'
              AND (o.retail_store_code IS NULL OR o.retail_store_code = '')
              AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
              AND o.unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
              AND o.status NOT IN ('MOB_APP_UNVERIFIED')
        ),
        sp_fc AS (
            SELECT
                sp.order_id,
                sp.delivery_status,
                sp.order_placed_date,
                TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), sp.order_placed_date, HOUR) AS hrs_since_dispatch,
                ROW_NUMBER() OVER (PARTITION BY sp.order_id ORDER BY sp.order_placed_date DESC) AS rn
            FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
            JOIN fc_orders fco ON sp.order_id = CAST(fco.unicommerce_id AS STRING)
        )
        SELECT
            COUNT(DISTINCT fc.sales_order_id) AS total,
            COUNTIF(sp.delivery_status NOT IN ('delivered','returned','process_for_return',
                                               'return_manifest_created','return_in_transit','returned_by_lmd'))
                AS not_dispatched_delivered,
            COUNTIF(sp.delivery_status = 'in_transit') AS in_transit,
            COUNTIF(sp.hrs_since_dispatch > {SLA['fc_dispatch_critical_hrs']}) AS past_5day_sla,
            APPROX_QUANTILES(sp.hrs_since_dispatch, 2)[OFFSET(1)] AS median_hrs_since_dispatch
        FROM fc_orders fc
        LEFT JOIN sp_fc sp
          ON sp.order_id = CAST(fc.unicommerce_id AS STRING) AND sp.rn = 1
    """

    fc_summary = {}
    try:
        rows = list(bq_client.query(fc_sql).result())
        if rows:
            r = rows[0]
            fc_summary = {
                "total":                    int(r[0] or 0),
                "not_dispatched":           int(r[1] or 0),
                "in_transit":               int(r[2] or 0),
                "past_5day_sla":            int(r[3] or 0),
                "median_hrs_since_dispatch": float(r[4] or 0),
            }
    except Exception as e:
        print(f"[live_pipeline] FC query error: {e}")

    total_pending_dvs = sum(s["orders"] for s in dvs_stages)
    print(f"[live_pipeline] DVS pending stages: {len(dvs_stages)}, total orders: {total_pending_dvs}")
    print(f"[live_pipeline] FC total: {fc_summary.get('total', 0)}, past-5d SLA: {fc_summary.get('past_5day_sla', 0)}")
    return {"dvs_stages": dvs_stages, "fc_summary": fc_summary}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Dead Store Classifier
# ─────────────────────────────────────────────────────────────────────────────

def run_dead_store_classifier(bq_client) -> list[dict]:
    """
    Compare prev 30-60 days vs current 0-30 days per store.
    Join reroutinglogs + institution for diagnosis and naming.
    Returns list of store dicts with category + gmv_at_risk.
    """
    sql = """
        WITH inst AS (
            SELECT
                reference_customer_id AS store_id,
                name AS store_name,
                address_state AS state,
                address_district AS district,
                ROW_NUMBER() OVER (
                    PARTITION BY reference_customer_id ORDER BY created_on DESC
                ) AS rn
            FROM `agrostar-data.galaxy_views.institution`
            WHERE isDeliveryViaStoreEnabled = TRUE
              AND status = 'ACTIVE'
              AND archive = FALSE
              AND LOWER(ancestor_institutions_name) LIKE '%sathi%'
        ),
        stores AS (SELECT * FROM inst WHERE rn = 1),

        prev_window AS (
            SELECT
                SAFE_CAST(retail_store_code AS INT64) AS store_id,
                COUNT(DISTINCT sales_order_id) AS prev_orders,
                SUM(grand_total) AS prev_gmv
            FROM `agrostar-data.prod_db_views.order_management_order`
            WHERE DATE(created_on) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 60 DAY)
              AND DATE(created_on) <  DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND retail_store_code IS NOT NULL
              AND retail_store_code != ''
              AND LOWER(initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(order_type, '')) NOT LIKE '%offline%'
              AND unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
            GROUP BY 1
        ),

        curr_window AS (
            SELECT
                SAFE_CAST(retail_store_code AS INT64) AS store_id,
                COUNT(DISTINCT sales_order_id) AS curr_orders,
                SUM(grand_total) AS curr_gmv
            FROM `agrostar-data.prod_db_views.order_management_order`
            WHERE DATE(created_on) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND DATE(created_on) <  CURRENT_DATE('Asia/Kolkata')
              AND retail_store_code IS NOT NULL
              AND retail_store_code != ''
              AND LOWER(initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(order_type, '')) NOT LIKE '%offline%'
              AND unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
            GROUP BY 1
        ),

        -- Demand routed to this store in the last 30 days (via PromisedTAT)
        demand_routed AS (
            SELECT
                SAFE_CAST(o.retail_store_code AS INT64) AS store_id,
                COUNT(DISTINCT o.sales_order_id) AS demand_received
            FROM `agrostar-data.prod_db_views.order_management_order` o
            JOIN (
                SELECT cartId, dvsResolutionReason,
                       ROW_NUMBER() OVER (PARTITION BY cartId ORDER BY createdOn DESC) AS rn
                FROM `agrostar-data.prod_db_views.PromisedTAT`
            ) pt ON pt.cartId = o.cart_id AND pt.rn = 1
            WHERE DATE(o.created_on) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND pt.dvsResolutionReason LIKE '%resolved-yes%'
              AND o.retail_store_code IS NOT NULL
              AND o.retail_store_code != ''
            GROUP BY 1
        ),

        rerouted AS (
            SELECT
                partner_id AS store_id,
                COUNT(*) AS rerouted_total,
                COUNTIF(LOWER(reason_for_routing) LIKE '%ocp%') AS ocp_count,
                COUNTIF(LOWER(reason_for_routing) LIKE '%close%') AS closed_count
            FROM `agrostar-data.prod_db_views.order_management_orderreroutinglogs`
            WHERE DATE(created_on) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
            GROUP BY 1
        ),

        combined AS (
            SELECT
                s.store_id,
                s.store_name,
                s.state,
                s.district,
                COALESCE(p.prev_orders, 0) AS prev_orders,
                COALESCE(p.prev_gmv, 0)    AS prev_gmv,
                COALESCE(c.curr_orders, 0) AS curr_orders,
                COALESCE(c.curr_gmv, 0)    AS curr_gmv,
                COALESCE(d.demand_received, 0) AS demand_received,
                COALESCE(r.rerouted_total, 0)  AS rerouted_total,
                COALESCE(r.ocp_count, 0)       AS ocp_count,
                COALESCE(r.closed_count, 0)    AS closed_count
            FROM stores s
            JOIN prev_window p ON p.store_id = s.store_id   -- must have had orders before
            LEFT JOIN curr_window c   ON c.store_id = s.store_id
            LEFT JOIN demand_routed d ON d.store_id = s.store_id
            LEFT JOIN rerouted r      ON r.store_id = s.store_id
            WHERE p.prev_orders >= 5  -- Only flag stores that were previously active
              AND COALESCE(c.curr_orders, 0) < p.prev_orders * 0.5  -- Dropped >50%
        )

        SELECT
            store_id,
            store_name,
            state,
            district,
            prev_orders,
            curr_orders,
            demand_received,
            rerouted_total,
            ocp_count,
            closed_count,
            -- Category logic
            CASE
                WHEN rerouted_total = 0 AND demand_received = 0 THEN 'STARVED'
                WHEN rerouted_total > 0 AND SAFE_DIVIDE(ocp_count, rerouted_total) >= 0.5 THEN 'OCP_BLOCKED'
                WHEN rerouted_total > 0 AND SAFE_DIVIDE(closed_count, rerouted_total) >= 0.5 THEN 'CLOSED'
                ELSE 'TRULY_DEAD'
            END AS category,
            ROUND(prev_orders * 850.0) AS gmv_at_risk  -- ₹ at risk monthly (avg order ₹850)
        FROM combined
        ORDER BY prev_orders DESC
        LIMIT 50
    """

    stores = []
    try:
        for row in bq_client.query(sql).result():
            stores.append({
                "store_id":        int(row[0] or 0),
                "store_name":      str(row[1] or ""),
                "state":           str(row[2] or ""),
                "district":        str(row[3] or ""),
                "prev_orders":     int(row[4] or 0),
                "curr_orders":     int(row[5] or 0),
                "demand_received": int(row[6] or 0),
                "rerouted_total":  int(row[7] or 0),
                "ocp_count":       int(row[8] or 0),
                "closed_count":    int(row[9] or 0),
                "category":        str(row[10] or "TRULY_DEAD"),
                "gmv_at_risk":     float(row[11] or 0),
            })
    except Exception as e:
        print(f"[dead_store] Query error: {e}")
        traceback.print_exc()

    cat_counts = {}
    for s in stores:
        cat_counts[s["category"]] = cat_counts.get(s["category"], 0) + 1
    print(f"[dead_store] {len(stores)} stores flagged: {cat_counts}")
    return stores


# ─────────────────────────────────────────────────────────────────────────────
# 6. LMD Dual Job Analysis
# ─────────────────────────────────────────────────────────────────────────────

def run_lmd_dual_job(bq_client) -> list[dict]:
    """
    FC + DVS performance per LMD partner. Cross-signal flag.
    Returns list of LMD dicts with flag type.
    """
    sql = """
        WITH lmd_names AS (
            SELECT
                df.id AS franchise_id,
                df.user_info_id AS lmd_name
            FROM `agrostar-data.prod_db_views.delivery_franchise` df
        ),

        -- FC packages: orders NOT fulfilled via DVS store
        fc_pkgs AS (
            SELECT
                sp.to_franchise_id,
                COUNT(*) AS fc_total,
                COUNTIF(sp.delivery_status = 'delivered') AS fc_delivered,
                COUNTIF(sp.delivery_status IN (
                    'returned','process_for_return','return_manifest_created',
                    'return_in_transit','returned_by_lmd'
                )) AS fc_returned
            FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
            JOIN `agrostar-data.prod_db_views.order_management_order` o
              ON sp.order_id = CAST(o.unicommerce_id AS STRING)
            WHERE DATE(sp.order_placed_date) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND (o.retail_store_code IS NULL OR o.retail_store_code = '')
              AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
              AND sp.to_franchise_id IS NOT NULL
            GROUP BY 1
        ),

        -- DVS packages: orders fulfilled via a DVS store
        dvs_pkgs AS (
            SELECT
                sp.to_franchise_id,
                COUNT(*) AS dvs_total,
                COUNTIF(sp.delivery_status = 'delivered') AS dvs_delivered,
                COUNTIF(sp.delivery_status IN (
                    'returned','process_for_return','return_manifest_created',
                    'return_in_transit','returned_by_lmd'
                )) AS dvs_returned
            FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
            JOIN `agrostar-data.prod_db_views.order_management_order` o
              ON sp.order_id = CAST(o.unicommerce_id AS STRING)
            WHERE DATE(sp.order_placed_date) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND o.retail_store_code IS NOT NULL
              AND o.retail_store_code != ''
              AND LOWER(o.initiating_source) NOT LIKE 'b2b%'
              AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
              AND sp.to_franchise_id IS NOT NULL
            GROUP BY 1
        ),

        -- HOLD_BY_LMD events for DVS orders last 30 days
        dvs_hold AS (
            SELECT
                sp.to_franchise_id,
                COUNT(DISTINCT o.sales_order_id) AS hold_count
            FROM `agrostar-data.prod_db_views.order_management_orderhistorymeta` ohm
            JOIN `agrostar-data.prod_db_views.order_management_order` o
              ON ohm.order_id = CAST(o.sales_order_id AS INT64)
            JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
              ON sp.order_id = CAST(o.unicommerce_id AS STRING)
            WHERE ohm.status = 'HOLD_BY_LMD'
              AND DATE(ohm.created_on) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
              AND o.retail_store_code IS NOT NULL
              AND o.retail_store_code != ''
              AND sp.to_franchise_id IS NOT NULL
            GROUP BY 1
        )

        SELECT
            COALESCE(fc.to_franchise_id, dvs.to_franchise_id) AS franchise_id,
            lmd.lmd_name,
            COALESCE(fc.fc_total, 0)     AS fc_total,
            COALESCE(fc.fc_delivered, 0) AS fc_delivered,
            COALESCE(fc.fc_returned, 0)  AS fc_returned,
            COALESCE(dvs.dvs_total, 0)    AS dvs_total,
            COALESCE(dvs.dvs_delivered, 0) AS dvs_delivered,
            COALESCE(h.hold_count, 0)    AS dvs_hold_count,
            -- FC delivery %
            ROUND(SAFE_DIVIDE(COALESCE(fc.fc_delivered,0), NULLIF(COALESCE(fc.fc_total,0),0)) * 100, 1) AS fc_del_pct,
            -- FC RTO %
            ROUND(SAFE_DIVIDE(COALESCE(fc.fc_returned,0),
                NULLIF(COALESCE(fc.fc_delivered,0)+COALESCE(fc.fc_returned,0), 0)) * 100, 1) AS fc_rto_pct,
            -- DVS hold %
            ROUND(SAFE_DIVIDE(COALESCE(h.hold_count,0), NULLIF(COALESCE(dvs.dvs_total,0),0)) * 100, 1) AS dvs_hold_pct,
            -- DVS RTO %
            ROUND(SAFE_DIVIDE(COALESCE(dvs.dvs_returned,0),
                NULLIF(COALESCE(dvs.dvs_delivered,0)+COALESCE(dvs.dvs_returned,0), 0)) * 100, 1) AS dvs_rto_pct,
            -- Flag
            CASE
                WHEN COALESCE(fc.fc_total, 0) + COALESCE(dvs.dvs_total, 0) < 10 THEN 'INSUFFICIENT_DATA'
                WHEN SAFE_DIVIDE(COALESCE(fc.fc_delivered,0), NULLIF(COALESCE(fc.fc_total,0),0)) * 100 >= 80
                 AND SAFE_DIVIDE(COALESCE(h.hold_count,0), NULLIF(COALESCE(dvs.dvs_total,0),0)) * 100 >= 40
                    THEN 'SELECTIVE_DEPRIORITIZATION'
                WHEN SAFE_DIVIDE(COALESCE(fc.fc_delivered,0), NULLIF(COALESCE(fc.fc_total,0),0)) * 100 < 70
                 AND SAFE_DIVIDE(COALESCE(h.hold_count,0), NULLIF(COALESCE(dvs.dvs_total,0),0)) * 100 >= 40
                    THEN 'GENUINE_OVERLOAD'
                WHEN SAFE_DIVIDE(COALESCE(h.hold_count,0), NULLIF(COALESCE(dvs.dvs_total,0),0)) * 100 < 20
                 AND SAFE_DIVIDE(COALESCE(dvs.dvs_returned,0),
                     NULLIF(COALESCE(dvs.dvs_delivered,0)+COALESCE(dvs.dvs_returned,0), 0)) * 100 >= 30
                    THEN 'LAST_MILE_FAILURE'
                ELSE 'HEALTHY'
            END AS flag
        FROM fc_pkgs fc
        FULL OUTER JOIN dvs_pkgs dvs
          ON fc.to_franchise_id = dvs.to_franchise_id
        LEFT JOIN dvs_hold h
          ON COALESCE(fc.to_franchise_id, dvs.to_franchise_id) = h.to_franchise_id
        LEFT JOIN lmd_names lmd
          ON COALESCE(fc.to_franchise_id, dvs.to_franchise_id) = lmd.franchise_id
        WHERE COALESCE(fc.fc_total, 0) + COALESCE(dvs.dvs_total, 0) >= 10
          AND flag != 'INSUFFICIENT_DATA'
        ORDER BY
            CASE flag
                WHEN 'SELECTIVE_DEPRIORITIZATION' THEN 1
                WHEN 'GENUINE_OVERLOAD' THEN 2
                WHEN 'LAST_MILE_FAILURE' THEN 3
                ELSE 4
            END,
            dvs_hold_pct DESC
        LIMIT 30
    """

    lmds = []
    try:
        for row in bq_client.query(sql).result():
            lmds.append({
                "franchise_id": int(row[0] or 0),
                "lmd_name":     str(row[1] or f"LMD-{row[0]}"),
                "fc_total":     int(row[2] or 0),
                "fc_delivered": int(row[3] or 0),
                "fc_returned":  int(row[4] or 0),
                "dvs_total":    int(row[5] or 0),
                "dvs_delivered": int(row[6] or 0),
                "dvs_hold_count": int(row[7] or 0),
                "fc_del_pct":   float(row[8] or 0),
                "fc_rto_pct":   float(row[9] or 0),
                "dvs_hold_pct": float(row[10] or 0),
                "dvs_rto_pct":  float(row[11] or 0),
                "flag":         str(row[12] or "HEALTHY"),
            })
    except Exception as e:
        print(f"[lmd_dual_job] Query error: {e}")
        traceback.print_exc()

    flag_counts = {}
    for l in lmds:
        flag_counts[l["flag"]] = flag_counts.get(l["flag"], 0) + 1
    print(f"[lmd_dual_job] {len(lmds)} LMDs: {flag_counts}")
    return lmds


# ─────────────────────────────────────────────────────────────────────────────
# 7. File Predictions
# ─────────────────────────────────────────────────────────────────────────────

def _make_pred_id(memory: dict) -> str:
    """Generate a unique, sequential prediction ID."""
    today = datetime.now(IST).date()
    prefix = f"PRED-{today.strftime('%Y-%m')}"
    existing = [
        p["id"] for p in memory["predictions"]
        if p["id"].startswith(prefix)
    ]
    seq = len(existing) + 1
    return f"{prefix}-{seq:03d}"


def file_predictions(memory: dict, dead_stores: list[dict], lmd_flags: list[dict]) -> list[dict]:
    """
    File new PENDING predictions for truly dead / starved stores and selective LMDs.
    Deduplicates by entity_id + type — won't re-file if an active prediction exists.
    """
    today = datetime.now(IST).date().isoformat()
    new_preds = []

    # Index existing PENDING predictions by (type, entity_id)
    existing_pending = set()
    for p in memory["predictions"]:
        if p.get("result") == "PENDING":
            existing_pending.add((p.get("type"), str(p.get("entity_id"))))

    def _already_filed(ptype: str, entity_id: int) -> bool:
        return (ptype, str(entity_id)) in existing_pending

    # Top 10 TRULY_DEAD stores
    truly_dead = [s for s in dead_stores if s["category"] == "TRULY_DEAD"][:10]
    for store in truly_dead:
        ptype = "DEAD_STORE_RISK"
        if _already_filed(ptype, store["store_id"]):
            continue
        prev = store["prev_orders"]
        curr = store["curr_orders"]
        drop_pct = round((prev - curr) / prev * 100) if prev > 0 else 100
        pred = {
            "id":          _make_pred_id(memory),
            "filed_date":  today,
            "type":        ptype,
            "entity_id":   store["store_id"],
            "entity_name": store["store_name"],
            "state":       store["state"],
            "district":    store["district"],
            "prediction":  f"Store will remain non-functional — orders dropped {drop_pct}% (prev={prev}, curr={curr})",
            "confidence":  "HIGH" if drop_pct >= 80 else "MEDIUM",
            "evidence": [
                f"Prev 30-60d: {prev} orders | Current 0-30d: {curr} orders ({drop_pct}% drop)",
                f"Demand routed: {store['demand_received']} | Re-routed: {store['rerouted_total']}",
                f"Category: TRULY_DEAD (no OCP or closure signal)",
            ],
            "gmv_at_risk": store["gmv_at_risk"],
            "result":      "PENDING",
            "scored_on":   None,
        }
        memory["predictions"].append(pred)
        new_preds.append(pred)
        existing_pending.add((ptype, str(store["store_id"])))
        print(f"  [predict] Filed {pred['id']} — DEAD_STORE_RISK — {store['store_name']}")

    # STARVED stores (no demand routed, no re-routing)
    starved = [s for s in dead_stores if s["category"] == "STARVED"][:10]
    for store in starved:
        ptype = "STARVED_RISK"
        if _already_filed(ptype, store["store_id"]):
            continue
        pred = {
            "id":          _make_pred_id(memory),
            "filed_date":  today,
            "type":        ptype,
            "entity_id":   store["store_id"],
            "entity_name": store["store_name"],
            "state":       store["state"],
            "district":    store["district"],
            "prediction":  "Store is starved — no demand being routed to it by the system",
            "confidence":  "HIGH",
            "evidence": [
                f"Prev 30-60d: {store['prev_orders']} orders | Current: {store['curr_orders']} orders",
                "Demand received = 0, Re-routed = 0 → routing engine not assigning this store",
                "Likely cause: taluka config gap, license issue, or distance threshold",
            ],
            "gmv_at_risk": store["gmv_at_risk"],
            "result":      "PENDING",
            "scored_on":   None,
        }
        memory["predictions"].append(pred)
        new_preds.append(pred)
        existing_pending.add((ptype, str(store["store_id"])))
        print(f"  [predict] Filed {pred['id']} — STARVED_RISK — {store['store_name']}")

    # SELECTIVE LMDs
    selective = [l for l in lmd_flags if l["flag"] == "SELECTIVE_DEPRIORITIZATION"]
    for lmd in selective:
        ptype = "LMD_DEPRIORITIZATION"
        if _already_filed(ptype, lmd["franchise_id"]):
            continue
        pred = {
            "id":          _make_pred_id(memory),
            "filed_date":  today,
            "type":        ptype,
            "entity_id":   lmd["franchise_id"],
            "entity_name": lmd["lmd_name"],
            "state":       "",
            "district":    "",
            "prediction":  "LMD will continue selectively deprioritizing DVS pickups while maintaining FC delivery",
            "confidence":  "HIGH",
            "evidence": [
                f"FC delivery rate: {lmd['fc_del_pct']}% (>=80% threshold)",
                f"DVS HOLD_BY_LMD rate: {lmd['dvs_hold_pct']}% (>=40% threshold)",
                f"DVS orders: {lmd['dvs_total']} | Held: {lmd['dvs_hold_count']}",
            ],
            "gmv_at_risk": lmd["dvs_hold_count"] * 850.0,  # avg order value
            "result":      "PENDING",
            "scored_on":   None,
        }
        memory["predictions"].append(pred)
        new_preds.append(pred)
        existing_pending.add((ptype, str(lmd["franchise_id"])))
        print(f"  [predict] Filed {pred['id']} — LMD_DEPRIORITIZATION — {lmd['lmd_name']}")

    print(f"[file_predictions] {len(new_preds)} new predictions filed")
    return new_preds


# ─────────────────────────────────────────────────────────────────────────────
# 8. Slack Formatters
# ─────────────────────────────────────────────────────────────────────────────

def _inr(val: float) -> str:
    """Format a number in Indian comma style with ₹ prefix."""
    n = int(abs(val))
    s = str(n)
    if len(s) <= 3:
        return f"₹{'-' if val < 0 else ''}{s}"
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while len(rest) > 2:
        parts.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.append(rest)
    parts.reverse()
    formatted = ",".join(parts) + "," + last3
    return f"{'₹-' if val < 0 else '₹'}{formatted}"


def _flag_sym(actual: float, baseline: float, higher_is_bad: bool = False) -> str:
    diff = actual - baseline
    if higher_is_bad:
        if diff > 3:
            return "⚠️"
        if diff > 1:
            return "🔸"
        return "✅"
    else:
        if diff < -3:
            return "⚠️"
        if diff < -1:
            return "🔸"
        return "✅"


def format_slack_summary(
    health: dict,
    pipeline: dict,
    dead_stores: list[dict],
    lmd_flags: list[dict],
    scored_preds: list[dict],
    memory: dict,
) -> str:
    today = datetime.now(IST).strftime("%b %d, %Y")
    now   = datetime.now(IST).strftime("%I:%M %p IST")

    # ── Program Pulse ────────────────────────────────────────────────────────
    rr   = health.get("routing_rate_pct", 0)
    fr   = health.get("fulfillment_rate_pct", 0)
    rto  = health.get("rto_rate_pct", 0)
    dem  = health.get("b2c_demand", 0)
    ful  = health.get("dvs_fulfilled", 0)

    rr_sym  = _flag_sym(rr,  BASELINE["routing_rate_pct"])
    fr_sym  = _flag_sym(fr,  BASELINE["fulfillment_rate_pct"])
    rto_sym = _flag_sym(rto, BASELINE["rto_rate_pct"], higher_is_bad=True)

    pulse_lines = [
        f"*Routing Rate*   {rr:.1f}%  (baseline {BASELINE['routing_rate_pct']}%)  {rr_sym}",
        f"*Fulfillment*    {fr:.1f}%  (baseline {BASELINE['fulfillment_rate_pct']}%)  {fr_sym}",
        f"*RTO Rate*       {rto:.1f}%  (baseline {BASELINE['rto_rate_pct']}%)  {rto_sym}",
        f"*Demand (7d)*    {dem:,} orders  |  *DVS Fulfilled*  {ful:,}",
    ]
    flags = health.get("flags", {})
    if flags:
        pulse_lines.append(f"\n:rotating_light: *Baseline breaches:* " + " | ".join(flags.values()))

    # ── Live Pipeline ────────────────────────────────────────────────────────
    dvs_stages = pipeline.get("dvs_stages", [])
    fc         = pipeline.get("fc_summary", {})

    stage_map = {s["stage"]: s for s in dvs_stages}
    packed     = stage_map.get("PACKED", {})
    hold_lmd   = stage_map.get("HOLD_BY_LMD", {})
    on_hold    = stage_map.get("ON_HOLD", {})
    picked     = stage_map.get("PICKED_BY_LMD", {})

    pipeline_lines = []
    if packed:
        sla_warn = " :warning:" if packed.get("median_hrs_waiting", 0) > SLA["dvs_packed_warning_hrs"] else ""
        pipeline_lines.append(
            f"*PACKED*        {packed['orders']:,} orders  |  median wait {packed['median_hrs_waiting']:.0f}h  |  >{SLA['dvs_packed_critical_hrs']}h breach: {packed['past_3day_sla']:,}{sla_warn}"
        )
    if hold_lmd:
        pipeline_lines.append(
            f"*HOLD_BY_LMD*   {hold_lmd['orders']:,} orders  |  median wait {hold_lmd['median_hrs_waiting']:.0f}h"
        )
    if on_hold:
        pipeline_lines.append(
            f"*ON_HOLD*       {on_hold['orders']:,} orders  |  median wait {on_hold['median_hrs_waiting']:.0f}h"
        )
    if picked:
        pipeline_lines.append(
            f"*PICKED*        {picked['orders']:,} orders  |  in transit"
        )
    if fc:
        pipeline_lines.append(
            f"*FC pending*    {fc.get('not_dispatched', 0):,} not dispatched  |  {fc.get('past_5day_sla', 0):,} past 5-day SLA"
        )

    # ── Dead Store Triage ────────────────────────────────────────────────────
    cat_counts = {}
    cat_gmv    = {}
    for s in dead_stores:
        cat = s["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        cat_gmv[cat]    = cat_gmv.get(cat, 0) + s["gmv_at_risk"]

    triage_lines = []
    for cat, sym in [
        ("TRULY_DEAD",  ":red_circle:"),
        ("OCP_BLOCKED", ":large_yellow_circle:"),
        ("STARVED",     ":large_blue_circle:"),
        ("CLOSED",      ":black_circle:"),
    ]:
        cnt = cat_counts.get(cat, 0)
        if cnt:
            triage_lines.append(f"{sym} *{cat}*  {cnt} stores  |  GMV at risk: {_inr(cat_gmv.get(cat, 0))}/mo")

    if not triage_lines:
        triage_lines = ["All stores operating normally :white_check_mark:"]

    # ── LMD Flags ────────────────────────────────────────────────────────────
    flag_counts = {}
    for l in lmd_flags:
        flag_counts[l["flag"]] = flag_counts.get(l["flag"], 0) + 1

    lmd_lines = []
    for flag, sym in [
        ("SELECTIVE_DEPRIORITIZATION", ":rotating_light:"),
        ("GENUINE_OVERLOAD",           ":warning:"),
        ("LAST_MILE_FAILURE",          ":no_entry_sign:"),
        ("HEALTHY",                    ":white_check_mark:"),
    ]:
        cnt = flag_counts.get(flag, 0)
        if cnt:
            lmd_lines.append(f"{sym} *{flag.replace('_', ' ')}*  {cnt} LMD partners")

    if not lmd_lines:
        lmd_lines = ["No LMD flags today"]

    # ── Prediction Scorecard ─────────────────────────────────────────────────
    acc = memory["rules"]["prediction_accuracy"]
    total_scored = acc["total"]
    scorecard_line = (
        f"*All time:* {total_scored} scored  |  "
        f":white_check_mark: {acc['correct']} correct  "
        f":arrow_upper_right: {acc['directional']} directional  "
        f":x: {acc['wrong']} wrong"
    )
    today_line = f"*Scored today:* {len(scored_preds)} predictions"

    # ── Assemble ─────────────────────────────────────────────────────────────
    blocks = [
        f":eye: *DVS DRISHTI*  |  Daily Brief  |  {today}  |  {now}",
        "",
        "*PROGRAM PULSE* " + "━" * 40,
        *pulse_lines,
        "",
        "*LIVE PIPELINE* " + "━" * 40,
        *(pipeline_lines or ["No pending DVS stages data"]),
        "",
        "*DEAD STORE TRIAGE* " + "━" * 38,
        *triage_lines,
        "",
        "*LMD FLAGS* " + "━" * 44,
        *lmd_lines,
        "",
        "*PREDICTION SCORECARD* " + "━" * 33,
        scorecard_line,
        today_line,
    ]
    return "\n".join(blocks)


def format_ops_message(dead_stores: list[dict]) -> str | None:
    """Actionable message for Ops team — TRULY_DEAD and STARVED stores."""
    truly_dead = [s for s in dead_stores if s["category"] == "TRULY_DEAD"]
    starved    = [s for s in dead_stores if s["category"] == "STARVED"]

    if not truly_dead and not starved:
        return None

    lines = [":rotating_light: *DVS DRISHTI — Ops Triage*\n"]

    if truly_dead:
        lines.append(f"*TRULY DEAD ({len(truly_dead)} stores)* — Store is receiving demand but not fulfilling")
        for s in truly_dead[:10]:
            lines.append(
                f"  • {s['store_name']} ({s['district']}, {s['state'].title()})  "
                f"prev={s['prev_orders']} orders  curr={s['curr_orders']}  "
                f"GMV risk: {_inr(s['gmv_at_risk'])}/mo"
            )
        if len(truly_dead) > 10:
            lines.append(f"  _... and {len(truly_dead)-10} more_")
        lines.append("")

    if starved:
        lines.append(f"*STARVED ({len(starved)} stores)* — No demand being routed by the system")
        for s in starved[:10]:
            lines.append(
                f"  • {s['store_name']} ({s['district']}, {s['state'].title()})  "
                f"prev={s['prev_orders']} orders  "
                f"GMV risk: {_inr(s['gmv_at_risk'])}/mo  "
                f"→ Check taluka config / license"
            )
        if len(starved) > 10:
            lines.append(f"  _... and {len(starved)-10} more_")

    return "\n".join(lines)


def format_finance_message(dead_stores: list[dict]) -> str | None:
    """Finance alert for OCP-blocked stores."""
    ocp = [s for s in dead_stores if s["category"] == "OCP_BLOCKED"]
    if not ocp:
        return None

    total_gmv = sum(s["gmv_at_risk"] for s in ocp)
    lines = [
        f":moneybag: *DVS DRISHTI — OCP Block Alert*\n",
        f"*{len(ocp)} stores* are OCP-blocked — orders are re-routing back to FC\n",
        f"*Combined GMV at risk: {_inr(total_gmv)}/month*\n",
    ]
    for s in ocp[:15]:
        lines.append(
            f"  • {s['store_name']} ({s['district']}, {s['state'].title()})  "
            f"re-routed: {s['rerouted_total']}  OCP-re-routes: {s['ocp_count']}  "
            f"risk: {_inr(s['gmv_at_risk'])}/mo"
        )
    if len(ocp) > 15:
        lines.append(f"  _... and {len(ocp)-15} more_")

    lines.append("\nAction: Review outstanding balances and MPD eligibility for these partners.")
    return "\n".join(lines)


def format_lmd_message(lmd_flags: list[dict]) -> str | None:
    """LMD escalation message for selective/overloaded LMDs."""
    selective  = [l for l in lmd_flags if l["flag"] == "SELECTIVE_DEPRIORITIZATION"]
    overloaded = [l for l in lmd_flags if l["flag"] == "GENUINE_OVERLOAD"]

    if not selective and not overloaded:
        return None

    lines = [":truck: *DVS DRISHTI — LMD Performance Flags*\n"]

    if selective:
        lines.append(
            f"*SELECTIVE DEPRIORITIZATION ({len(selective)} LMDs)* "
            f"— FC delivery high, DVS hold high"
        )
        for l in selective:
            lines.append(
                f"  • {l['lmd_name']}  FC del: {l['fc_del_pct']:.0f}%  "
                f"DVS hold: {l['dvs_hold_pct']:.0f}% ({l['dvs_hold_count']} orders)  "
                f"DVS total: {l['dvs_total']}"
            )
        lines.append(
            "  → Action: Ops config — review LMD incentive structure / territory split\n"
        )

    if overloaded:
        lines.append(
            f"*GENUINE OVERLOAD ({len(overloaded)} LMDs)* "
            f"— Poor across FC and DVS"
        )
        for l in overloaded:
            lines.append(
                f"  • {l['lmd_name']}  FC del: {l['fc_del_pct']:.0f}%  "
                f"DVS hold: {l['dvs_hold_pct']:.0f}%  DVS total: {l['dvs_total']}"
            )
        lines.append(
            "  → Action: Reduce store count per LMD or split territory\n"
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Slack Posting
# ─────────────────────────────────────────────────────────────────────────────

def post_to_slack(slack_client, channel: str, text: str, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"\n[DRY-RUN] Would post to {channel}:\n{'─'*60}\n{text}\n{'─'*60}")
        return True
    try:
        resp = slack_client.chat_postMessage(channel=channel, text=text, mrkdwn=True)
        if resp["ok"]:
            print(f"  [slack] Posted to {channel} (ts={resp.get('ts', '?')})")
            return True
        else:
            print(f"  [slack] Non-ok response for {channel}: {resp}")
            return False
    except SlackApiError as e:
        print(f"  [slack] SlackApiError posting to {channel}: {e.response['error']}")
        return False
    except Exception as e:
        print(f"  [slack] Unexpected error posting to {channel}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 10. Main Runner
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, no_predictions: bool = False) -> None:
    start = datetime.now(IST)
    print(f"\n{'='*60}")
    print(f"DVS DRISHTI — Daily Run")
    print(f"Started: {start.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*60}\n")

    # ── Init clients ─────────────────────────────────────────────────────────
    bq_client = bigquery.Client(project=PROJECT)
    slack_client = WebClient(token=SLACK_TOKEN) if SLACK_TOKEN else None
    if not slack_client:
        print("[DRISHTI] Warning: SLACK_BOT_TOKEN not set. Slack posts will be dry-run.")
        dry_run = True

    # ── 1. Load memory ───────────────────────────────────────────────────────
    memory = load_memory()

    # ── 2. Score pending predictions ─────────────────────────────────────────
    print("\n--- Scoring pending predictions ---")
    scored_preds = score_pending_predictions(memory, bq_client)

    # ── 3. Health scan ───────────────────────────────────────────────────────
    print("\n--- Running health scan (7-day rolling) ---")
    health = run_health_scan(bq_client)

    # ── 4. Live pipeline ─────────────────────────────────────────────────────
    print("\n--- Running live pipeline scan (60-day window) ---")
    pipeline = run_live_pipeline(bq_client)

    # ── 5. Dead store classifier ─────────────────────────────────────────────
    print("\n--- Running dead store classifier ---")
    dead_stores = run_dead_store_classifier(bq_client)

    # ── 6. LMD dual job analysis ─────────────────────────────────────────────
    print("\n--- Running LMD dual job analysis ---")
    lmd_flags = run_lmd_dual_job(bq_client)

    # ── 7. File new predictions ───────────────────────────────────────────────
    if not no_predictions:
        print("\n--- Filing new predictions ---")
        file_predictions(memory, dead_stores, lmd_flags)
    else:
        print("\n--- Skipping prediction filing (--no-predictions) ---")

    # ── 8. Build Slack messages ───────────────────────────────────────────────
    print("\n--- Building Slack messages ---")
    summary_msg = format_slack_summary(health, pipeline, dead_stores, lmd_flags, scored_preds, memory)
    ops_msg     = format_ops_message(dead_stores)
    finance_msg = format_finance_message(dead_stores)
    lmd_msg     = format_lmd_message(lmd_flags)

    # ── 9. Post to Slack ─────────────────────────────────────────────────────
    print("\n--- Posting to Slack ---")
    post_to_slack(slack_client, SLACK_CHANNEL_SUMMARY, summary_msg, dry_run=dry_run)
    if ops_msg:
        post_to_slack(slack_client, SLACK_CHANNEL_OPS, ops_msg, dry_run=dry_run)
    if finance_msg:
        post_to_slack(slack_client, SLACK_CHANNEL_FINANCE, finance_msg, dry_run=dry_run)
    if lmd_msg:
        post_to_slack(slack_client, SLACK_CHANNEL_LMD, lmd_msg, dry_run=dry_run)

    # ── 10. Save memory ───────────────────────────────────────────────────────
    save_memory(memory)

    # ── Completion summary ────────────────────────────────────────────────────
    end = datetime.now(IST)
    elapsed = (end - start).seconds
    acc = memory["rules"]["prediction_accuracy"]

    print(f"\n{'='*60}")
    print("DVS DRISHTI — Run Complete")
    print(f"{'='*60}")
    print(f"  Elapsed:          {elapsed}s")
    print(f"  Health flags:     {len(health.get('flags', {}))}")
    print(f"  DVS pipeline:     {sum(s['orders'] for s in pipeline.get('dvs_stages', []))} pending orders across {len(pipeline.get('dvs_stages', []))} stages")
    print(f"  FC pending:       {pipeline.get('fc_summary', {}).get('not_dispatched', 0)} not dispatched")
    print(f"  Dead stores:      {len(dead_stores)} flagged")
    print(f"    TRULY_DEAD:     {sum(1 for s in dead_stores if s['category']=='TRULY_DEAD')}")
    print(f"    OCP_BLOCKED:    {sum(1 for s in dead_stores if s['category']=='OCP_BLOCKED')}")
    print(f"    STARVED:        {sum(1 for s in dead_stores if s['category']=='STARVED')}")
    print(f"    CLOSED:         {sum(1 for s in dead_stores if s['category']=='CLOSED')}")
    print(f"  LMD flags:        {len(lmd_flags)} evaluated")
    print(f"    SELECTIVE:      {sum(1 for l in lmd_flags if l['flag']=='SELECTIVE_DEPRIORITIZATION')}")
    print(f"    OVERLOAD:       {sum(1 for l in lmd_flags if l['flag']=='GENUINE_OVERLOAD')}")
    print(f"    LAST_MILE:      {sum(1 for l in lmd_flags if l['flag']=='LAST_MILE_FAILURE')}")
    print(f"  Predictions:      {len(memory['predictions'])} total  |  {len(scored_preds)} scored today")
    print(f"  Accuracy (all):   {acc['correct']}/{acc['total']} correct ({acc['directional']} directional)")
    print(f"  Routing rate:     {health.get('routing_rate_pct', 0):.1f}%  (baseline {BASELINE['routing_rate_pct']}%)")
    print(f"  Fulfillment rate: {health.get('fulfillment_rate_pct', 0):.1f}%  (baseline {BASELINE['fulfillment_rate_pct']}%)")
    print(f"  RTO rate:         {health.get('rto_rate_pct', 0):.1f}%  (baseline {BASELINE['rto_rate_pct']}%)")
    print(f"  Memory:           {MEMORY_PATH}")
    print(f"  Finished:         {end.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _dry_run        = "--dry-run"        in sys.argv
    _no_predictions = "--no-predictions" in sys.argv
    run(dry_run=_dry_run, no_predictions=_no_predictions)
