#!/usr/bin/env python3
"""
DVS DRISHTI — Self-Improvement Module (learn.py)

Level 2: Diagnose why a prediction was WRONG (runs daily after scoring)
Level 3: Discover new rules from patterns in wrong predictions (runs weekly)

Usage (standalone):
    python drishti/learn.py
    python drishti/learn.py --dry-run        # Skip Slack posting
    python drishti/learn.py --level2-only    # Skip pattern discovery
    python drishti/learn.py --level3-only    # Skip diagnosis, only discover
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Third-party ───────────────────────────────────────────────────────────────
try:
    from google.cloud import bigquery
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError as e:
    print(f"[LEARN] Missing dependency: {e}")
    print("  pip install google-cloud-bigquery slack-sdk")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT = "agrostar-data"

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")

# All output → Darpan Pathar DM during pilot
_DM_DARPAN = "D0B7Y65ENRW"
SLACK_CHANNEL = os.environ.get("DRISHTI_CHANNEL_SUMMARY", _DM_DARPAN)
SLACK_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")

IST = timezone(timedelta(hours=5, minutes=30))

# Minimum wrong predictions required before Level 3 pattern discovery runs
MIN_WRONG_FOR_DISCOVERY = 10

# Minimum predictions of a single cause type to run a pattern query
MIN_CAUSE_FOR_PATTERN = 3

# How many days between Level 3 discovery runs
DISCOVERY_INTERVAL_DAYS = 7


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD string."""
    return datetime.now(IST).date().isoformat()


def _bq_scalar(bq_client, sql: str) -> Any:
    """
    Run a BQ query that returns a single row × single column.
    Returns None if query fails or returns no rows.
    """
    try:
        rows = list(bq_client.query(sql).result())
        if rows:
            return rows[0][0]
        return None
    except Exception as e:
        print(f"  [bq_scalar] Query error: {e}")
        return None


def _bq_rows(bq_client, sql: str) -> list:
    """
    Run a BQ query and return all rows as a list.
    Returns [] on error.
    """
    try:
        return list(bq_client.query(sql).result())
    except Exception as e:
        print(f"  [bq_rows] Query error: {e}")
        return []


def _load_memory() -> dict:
    """Load memory.json from disk with graceful fallback."""
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[LEARN] Warning: could not read memory ({e}). Returning empty dict.")
    return {}


def _save_memory(memory: dict) -> None:
    """Persist memory.json to disk."""
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False, default=str)
    print(f"[LEARN] Memory saved → {MEMORY_PATH}")


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 2 — Diagnose Why Prediction Was Wrong
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_wrong_prediction(pred: dict, bq_client, memory: dict) -> dict:
    """
    Given a WRONG prediction, run a diagnostic chain of BQ queries to find
    the most likely cause of why DRISHTI was wrong.

    Stops at the first check that reaches HIGH confidence.

    Returns:
        {
            "cause_type":  str,   # e.g. "OCP_CLEARED"
            "evidence":    str,   # human-readable explanation
            "confidence":  str,   # "HIGH" | "MEDIUM" | "LOW"
            "lesson":      str,   # one-sentence rule update
            "data":        dict,  # raw supporting numbers
        }
    """
    pred_type   = pred.get("type", "")
    entity_id   = pred.get("entity_id")
    filed_date  = pred.get("filed_date", "")    # "YYYY-MM-DD"
    scored_date = pred.get("scored_on", _today_ist())

    # Default: unknown cause
    unknown = {
        "cause_type": "UNKNOWN",
        "evidence":   "Could not determine why the prediction was wrong — insufficient data.",
        "confidence": "LOW",
        "lesson":     "No lesson extracted — manual review needed.",
        "data":       {},
    }

    if not entity_id or not filed_date:
        return unknown

    filed_str  = filed_date
    scored_str = scored_date

    # ─────────────────────────────────────────────────────────────────────────
    # DEAD_STORE_RISK diagnostic chain
    # ─────────────────────────────────────────────────────────────────────────
    if pred_type in ("DEAD_STORE_RISK", "STARVED_RISK"):

        # ── CHECK 1: OCP cleared? ────────────────────────────────────────────
        # We look at reroutinglogs. Before filed_date: high OCP share.
        # After filed_date: did OCP routes stop?
        try:
            ocp_sql = f"""
                WITH before_filing AS (
                    SELECT
                        COUNT(*) AS total_before,
                        COUNTIF(LOWER(reason_for_routing) LIKE '%ocp%') AS ocp_before
                    FROM `agrostar-data.prod_db_views.order_management_orderreroutinglogs`
                    WHERE partner_id = {entity_id}
                      AND DATE(created_on) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND DATE(created_on) <  DATE '{filed_str}'
                ),
                after_filing AS (
                    SELECT
                        COUNT(*) AS total_after,
                        COUNTIF(LOWER(reason_for_routing) LIKE '%ocp%') AS ocp_after
                    FROM `agrostar-data.prod_db_views.order_management_orderreroutinglogs`
                    WHERE partner_id = {entity_id}
                      AND DATE(created_on) >= DATE '{filed_str}'
                      AND DATE(created_on) <= DATE '{scored_str}'
                )
                SELECT
                    b.total_before, b.ocp_before,
                    a.total_after,  a.ocp_after
                FROM before_filing b, after_filing a
            """
            rows = _bq_rows(bq_client, ocp_sql)
            if rows:
                r = rows[0]
                total_before = int(r[0] or 0)
                ocp_before   = int(r[1] or 0)
                total_after  = int(r[2] or 0)
                ocp_after    = int(r[3] or 0)

                ocp_rate_before = (ocp_before / total_before) if total_before > 0 else 0
                ocp_rate_after  = (ocp_after  / total_after)  if total_after  > 0 else 0

                # OCP routes were dominant before but largely stopped after
                if ocp_rate_before >= 0.4 and ocp_rate_after < 0.2 and total_before >= 3:
                    return {
                        "cause_type": "OCP_CLEARED",
                        "evidence": (
                            f"OCP re-routing rate dropped from {ocp_rate_before:.0%} "
                            f"({ocp_before}/{total_before} reroutes) before filing to "
                            f"{ocp_rate_after:.0%} ({ocp_after}/{total_after}) after — "
                            f"OCP constraint was resolved and store resumed fulfilling."
                        ),
                        "confidence": "HIGH",
                        "lesson": (
                            "OCP-blocked stores with active re-routing resolve faster than predicted — "
                            "lower dead persistence prior for OCP_BLOCKED category to 0.35."
                        ),
                        "data": {
                            "ocp_before": ocp_before,
                            "total_before": total_before,
                            "ocp_rate_before": round(ocp_rate_before, 3),
                            "ocp_after": ocp_after,
                            "total_after": total_after,
                            "ocp_rate_after": round(ocp_rate_after, 3),
                        },
                    }
        except Exception as e:
            print(f"  [diagnose] CHECK 1 (OCP) error for pred {pred.get('id')}: {e}")

        # ── CHECK 2: LMD improved? ───────────────────────────────────────────
        # Compare HOLD_BY_LMD rate 14d before vs 14d after filed_date
        try:
            lmd_hold_sql = f"""
                WITH base AS (
                    SELECT
                        ohm.order_id,
                        ohm.status,
                        ohm.created_on
                    FROM `agrostar-data.prod_db_views.order_management_orderhistorymeta` ohm
                    JOIN `agrostar-data.prod_db_views.order_management_order` o
                      ON ohm.order_id = CAST(o.sales_order_id AS INT64)
                    WHERE SAFE_CAST(o.retail_store_code AS INT64) = {entity_id}
                      AND DATE(ohm.created_on) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND DATE(ohm.created_on) <= DATE_ADD(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND LOWER(COALESCE(o.initiating_source, '')) NOT LIKE 'b2b%'
                      AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
                ),
                before_window AS (
                    SELECT
                        COUNT(DISTINCT order_id) AS orders_before,
                        COUNTIF(status = 'HOLD_BY_LMD') AS holds_before
                    FROM base
                    WHERE DATE(created_on) < DATE '{filed_str}'
                ),
                after_window AS (
                    SELECT
                        COUNT(DISTINCT order_id) AS orders_after,
                        COUNTIF(status = 'HOLD_BY_LMD') AS holds_after
                    FROM base
                    WHERE DATE(created_on) >= DATE '{filed_str}'
                )
                SELECT
                    b.orders_before, b.holds_before,
                    a.orders_after,  a.holds_after
                FROM before_window b, after_window a
            """
            rows = _bq_rows(bq_client, lmd_hold_sql)
            if rows:
                r = rows[0]
                orders_before = int(r[0] or 0)
                holds_before  = int(r[1] or 0)
                orders_after  = int(r[2] or 0)
                holds_after   = int(r[3] or 0)

                hold_rate_before = (holds_before / orders_before) if orders_before > 0 else 0
                hold_rate_after  = (holds_after  / orders_after)  if orders_after  > 0 else 0
                drop_pp = (hold_rate_before - hold_rate_after) * 100

                if drop_pp > 20 and orders_before >= 3:
                    return {
                        "cause_type": "LMD_IMPROVED",
                        "evidence": (
                            f"HOLD_BY_LMD rate dropped {drop_pp:.1f}pp — from "
                            f"{hold_rate_before:.0%} ({holds_before}/{orders_before} orders) "
                            f"before filing to {hold_rate_after:.0%} ({holds_after}/{orders_after}) "
                            f"after. LMD actively improved DVS pickups at this store."
                        ),
                        "confidence": "HIGH" if drop_pp > 35 else "MEDIUM",
                        "lesson": (
                            "Stores with high LMD hold recover when LMD behavior improves — "
                            "check if LMD incentive change or territory rebalance triggered this."
                        ),
                        "data": {
                            "orders_before": orders_before,
                            "holds_before":  holds_before,
                            "hold_rate_before": round(hold_rate_before, 3),
                            "orders_after":  orders_after,
                            "holds_after":   holds_after,
                            "hold_rate_after": round(hold_rate_after, 3),
                            "drop_pp": round(drop_pp, 1),
                        },
                    }
        except Exception as e:
            print(f"  [diagnose] CHECK 2 (LMD hold) error for pred {pred.get('id')}: {e}")

        # ── CHECK 3: New LMD assigned? ───────────────────────────────────────
        # Compare franchise_id on delivery_shippingpackage before vs after
        try:
            lmd_change_sql = f"""
                WITH store_orders AS (
                    SELECT
                        o.sales_order_id,
                        o.unicommerce_id,
                        DATE(o.created_on) AS order_date
                    FROM `agrostar-data.prod_db_views.order_management_order` o
                    WHERE SAFE_CAST(o.retail_store_code AS INT64) = {entity_id}
                      AND DATE(o.created_on) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND DATE(o.created_on) <= DATE_ADD(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND LOWER(COALESCE(o.initiating_source, '')) NOT LIKE 'b2b%'
                      AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
                ),
                pkg_franchise AS (
                    SELECT
                        sp.to_franchise_id,
                        DATE(so.order_date) AS order_date,
                        CASE
                            WHEN DATE(so.order_date) < DATE '{filed_str}' THEN 'before'
                            ELSE 'after'
                        END AS window
                    FROM store_orders so
                    JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                      ON sp.order_id = CAST(so.unicommerce_id AS STRING)
                    WHERE sp.to_franchise_id IS NOT NULL
                )
                SELECT
                    window,
                    APPROX_TOP_COUNT(CAST(to_franchise_id AS STRING), 1)[OFFSET(0)].value AS top_franchise,
                    COUNT(*) AS pkg_count
                FROM pkg_franchise
                GROUP BY window
            """
            rows = _bq_rows(bq_client, lmd_change_sql)
            franchise_before = None
            franchise_after  = None
            for r in rows:
                window   = str(r[0])
                fid      = str(r[1]) if r[1] else None
                if window == "before":
                    franchise_before = fid
                elif window == "after":
                    franchise_after = fid

            if (franchise_before and franchise_after
                    and franchise_before != franchise_after):
                return {
                    "cause_type": "LMD_CHANGED",
                    "evidence": (
                        f"LMD franchise changed from {franchise_before} (before filing) "
                        f"to {franchise_after} (after). A new LMD partner was assigned "
                        f"to this store's territory and resumed deliveries."
                    ),
                    "confidence": "HIGH",
                    "lesson": (
                        "Store recovery after LMD change is fast — when reroutinglogs show "
                        "LMD-related blocks, flag as LMD_RECOVERY_LIKELY rather than DEAD."
                    ),
                    "data": {
                        "franchise_before": franchise_before,
                        "franchise_after":  franchise_after,
                    },
                }
        except Exception as e:
            print(f"  [diagnose] CHECK 3 (LMD changed) error for pred {pred.get('id')}: {e}")

        # ── CHECK 4: Seasonal demand spike? ─────────────────────────────────
        # Kharif prep (May–Jun) is a known high-recovery window per seasonal_calendar
        try:
            filed_month  = int(filed_date[5:7]) if len(filed_date) >= 7 else 0
            scored_month = int(scored_date[5:7]) if len(scored_date) >= 7 else 0

            # Feb–Apr = Kharif trough (dead stores most sticky)
            # May–Jun = Kharif prep (strong reactivation)
            is_kharif_trough_filed = filed_month in (2, 3, 4)
            is_kharif_prep_scored  = scored_month in (5, 6)

            if is_kharif_trough_filed and is_kharif_prep_scored:
                seasonal_cal = memory.get("seasonal_calendar", {})
                kharif_note  = seasonal_cal.get("may_jun", "Kharif prep — demand recovering")
                return {
                    "cause_type": "SEASONAL_RECOVERY",
                    "evidence": (
                        f"Prediction filed in {_month_name(filed_month)} (Kharif trough) and "
                        f"scored in {_month_name(scored_month)} ({kharif_note}). "
                        f"Seasonal demand recovery is a known driver of store reactivation in "
                        f"May–June as farmers start Kharif purchases."
                    ),
                    "confidence": "MEDIUM",
                    "lesson": (
                        "Dead persistence prior should be lowered to 0.30 for predictions "
                        "filed Feb–Apr that will be scored in May–Jun (Kharif reactivation window)."
                    ),
                    "data": {
                        "filed_month":  filed_month,
                        "scored_month": scored_month,
                        "seasonal_note": kharif_note,
                    },
                }
        except Exception as e:
            print(f"  [diagnose] CHECK 4 (seasonal) error for pred {pred.get('id')}: {e}")

        # ── CHECK 5: Demand spike in taluka? ─────────────────────────────────
        # Get servingTaluka from galaxy_views.institution, then check PromisedTAT volume
        try:
            taluka_sql = f"""
                SELECT servingTaluka
                FROM `agrostar-data.galaxy_views.institution`
                WHERE reference_customer_id = {entity_id}
                  AND isDeliveryViaStoreEnabled = TRUE
                QUALIFY ROW_NUMBER() OVER (ORDER BY created_on DESC) = 1
            """
            taluka_val = _bq_scalar(bq_client, taluka_sql)

            if taluka_val:
                # servingTaluka may be a comma-separated list; take the first
                serving_talukas = [t.strip() for t in str(taluka_val).split(",")]
                taluka_filter   = " OR ".join(
                    [f"LOWER(pt.taluka) = LOWER('{t}')" for t in serving_talukas[:5]]
                )

                demand_sql = f"""
                    WITH deduped_tat AS (
                        SELECT
                            cartId,
                            dvsResolutionReason,
                            taluka
                        FROM `agrostar-data.prod_db_views.PromisedTAT`
                        QUALIFY ROW_NUMBER() OVER (
                            PARTITION BY cartId ORDER BY createdOn DESC
                        ) = 1
                    ),
                    before_demand AS (
                        SELECT COUNT(*) AS cnt
                        FROM deduped_tat pt
                        WHERE dvsResolutionReason LIKE '%resolved-yes%'
                          AND ({taluka_filter})
                          AND DATE(
                              PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S%z',
                                REGEXP_REPLACE(CAST(cartId AS STRING), r'.*', ''))
                          ) IS NOT NULL  -- cartId is not a date; skip taluka timing and use a simpler proxy
                    )
                    SELECT 1  -- placeholder; real query below
                """

                # Simpler approach: count PromisedTAT resolved-yes for orders
                # at this store before vs after
                demand_simple_sql = f"""
                    WITH deduped_tat AS (
                        SELECT
                            pt.cartId,
                            pt.dvsResolutionReason,
                            DATE(o.created_on) AS order_date
                        FROM `agrostar-data.prod_db_views.PromisedTAT` pt
                        JOIN `agrostar-data.prod_db_views.order_management_order` o
                          ON pt.cartId = o.cart_id
                        WHERE DATE(o.created_on) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                          AND DATE(o.created_on) <= DATE_ADD(DATE '{filed_str}', INTERVAL 14 DAY)
                          AND SAFE_CAST(o.retail_store_code AS INT64) = {entity_id}
                          AND LOWER(COALESCE(o.initiating_source, '')) NOT LIKE 'b2b%'
                        QUALIFY ROW_NUMBER() OVER (
                            PARTITION BY pt.cartId ORDER BY pt.createdOn DESC
                        ) = 1
                    )
                    SELECT
                        COUNTIF(order_date <  DATE '{filed_str}'
                                AND dvsResolutionReason LIKE '%resolved-yes%') AS demand_before,
                        COUNTIF(order_date >= DATE '{filed_str}'
                                AND dvsResolutionReason LIKE '%resolved-yes%') AS demand_after
                    FROM deduped_tat
                """
                rows = _bq_rows(bq_client, demand_simple_sql)
                if rows:
                    r = rows[0]
                    demand_before = int(r[0] or 0)
                    demand_after  = int(r[1] or 0)

                    if demand_before > 0:
                        demand_pct_change = (demand_after - demand_before) / demand_before * 100
                        if demand_pct_change > 30 and demand_before >= 2:
                            return {
                                "cause_type": "DEMAND_SPIKE",
                                "evidence": (
                                    f"PromisedTAT demand routed to this store jumped "
                                    f"{demand_pct_change:.0f}% — from {demand_before} orders "
                                    f"in 14d before filing to {demand_after} orders in 14d after. "
                                    f"Increased demand pulled the store back into activity."
                                ),
                                "confidence": "MEDIUM",
                                "lesson": (
                                    "Stores in low-demand talukas recover faster during demand surges — "
                                    "incorporate taluka-level demand trend as a signal before filing DEAD predictions."
                                ),
                                "data": {
                                    "demand_before": demand_before,
                                    "demand_after":  demand_after,
                                    "demand_pct_change": round(demand_pct_change, 1),
                                    "serving_talukas": serving_talukas,
                                },
                            }
        except Exception as e:
            print(f"  [diagnose] CHECK 5 (demand spike) error for pred {pred.get('id')}: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # LMD_DEPRIORITIZATION diagnostic chain
    # ─────────────────────────────────────────────────────────────────────────
    elif pred_type == "LMD_DEPRIORITIZATION":
        franchise_id = entity_id

        # ── CHECK 1: Territory reduced? ──────────────────────────────────────
        try:
            territory_sql = f"""
                WITH before_territory AS (
                    SELECT COUNT(DISTINCT SAFE_CAST(o.retail_store_code AS INT64)) AS store_count
                    FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                    JOIN `agrostar-data.prod_db_views.order_management_order` o
                      ON sp.order_id = CAST(o.unicommerce_id AS STRING)
                    WHERE sp.to_franchise_id = {franchise_id}
                      AND DATE(sp.order_placed_date) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                      AND DATE(sp.order_placed_date) <  DATE '{filed_str}'
                      AND o.retail_store_code IS NOT NULL
                      AND o.retail_store_code != ''
                ),
                after_territory AS (
                    SELECT COUNT(DISTINCT SAFE_CAST(o.retail_store_code AS INT64)) AS store_count
                    FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                    JOIN `agrostar-data.prod_db_views.order_management_order` o
                      ON sp.order_id = CAST(o.unicommerce_id AS STRING)
                    WHERE sp.to_franchise_id = {franchise_id}
                      AND DATE(sp.order_placed_date) >= DATE '{filed_str}'
                      AND DATE(sp.order_placed_date) <= DATE '{scored_str}'
                      AND o.retail_store_code IS NOT NULL
                      AND o.retail_store_code != ''
                )
                SELECT b.store_count AS before_cnt, a.store_count AS after_cnt
                FROM before_territory b, after_territory a
            """
            rows = _bq_rows(bq_client, territory_sql)
            if rows:
                r = rows[0]
                stores_before = int(r[0] or 0)
                stores_after  = int(r[1] or 0)
                territory_change = stores_before - stores_after

                if stores_before > 0 and territory_change > 0:
                    change_pct = territory_change / stores_before * 100
                    if change_pct >= 20:
                        return {
                            "cause_type": "TERRITORY_REDUCED",
                            "evidence": (
                                f"LMD franchise {franchise_id} territory shrank from "
                                f"{stores_before} to {stores_after} stores assigned "
                                f"({change_pct:.0f}% reduction). Smaller load = better performance."
                            ),
                            "confidence": "HIGH" if change_pct >= 40 else "MEDIUM",
                            "lesson": (
                                "LMD performance improves when territory is actively reduced — "
                                "territory rebalancing is an effective intervention."
                            ),
                            "data": {
                                "stores_before": stores_before,
                                "stores_after":  stores_after,
                                "territory_change": territory_change,
                                "change_pct": round(change_pct, 1),
                            },
                        }
        except Exception as e:
            print(f"  [diagnose] LMD CHECK 1 (territory) error for pred {pred.get('id')}: {e}")

        # ── CHECK 2: DVS order volume reduced? ───────────────────────────────
        try:
            volume_sql = f"""
                SELECT
                    COUNTIF(DATE(sp.order_placed_date) <  DATE '{filed_str}') AS dvs_before,
                    COUNTIF(DATE(sp.order_placed_date) >= DATE '{filed_str}') AS dvs_after
                FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                JOIN `agrostar-data.prod_db_views.order_management_order` o
                  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
                WHERE sp.to_franchise_id = {franchise_id}
                  AND DATE(sp.order_placed_date) >= DATE_SUB(DATE '{filed_str}', INTERVAL 14 DAY)
                  AND DATE(sp.order_placed_date) <= DATE_ADD(DATE '{filed_str}', INTERVAL 14 DAY)
                  AND o.retail_store_code IS NOT NULL
                  AND o.retail_store_code != ''
                  AND LOWER(COALESCE(o.initiating_source, '')) NOT LIKE 'b2b%'
            """
            rows = _bq_rows(bq_client, volume_sql)
            if rows:
                r = rows[0]
                dvs_before = int(r[0] or 0)
                dvs_after  = int(r[1] or 0)

                if dvs_before > 0:
                    volume_drop_pct = (dvs_before - dvs_after) / dvs_before * 100
                    if volume_drop_pct > 25 and dvs_before >= 5:
                        return {
                            "cause_type": "VOLUME_REDUCED",
                            "evidence": (
                                f"DVS order volume for LMD {franchise_id} dropped "
                                f"{volume_drop_pct:.0f}% — from {dvs_before} orders "
                                f"in 14d before to {dvs_after} after filing. "
                                f"Lower volume gave this LMD capacity to service remaining stores."
                            ),
                            "confidence": "MEDIUM",
                            "lesson": (
                                "LMD deprioritization can be demand-driven — when DVS volume "
                                "drops significantly, hold rates improve without any behavioral change."
                            ),
                            "data": {
                                "dvs_before": dvs_before,
                                "dvs_after":  dvs_after,
                                "volume_drop_pct": round(volume_drop_pct, 1),
                            },
                        }
        except Exception as e:
            print(f"  [diagnose] LMD CHECK 2 (volume) error for pred {pred.get('id')}: {e}")

        # ── CHECK 3: Regime/incentive change within 7 days of filing? ────────
        try:
            regime_boundaries = memory.get("regime_boundaries", [])
            filed_dt = datetime.fromisoformat(filed_date)

            for boundary in regime_boundaries:
                b_date_str = boundary.get("date", "")
                if not b_date_str:
                    continue
                b_date  = datetime.fromisoformat(b_date_str)
                days_diff = abs((filed_dt - b_date).days)
                if days_diff <= 7:
                    return {
                        "cause_type": "REGIME_CHANGE",
                        "evidence": (
                            f"A regime boundary occurred {days_diff} days "
                            f"{'before' if b_date < filed_dt else 'after'} the prediction: "
                            f"'{boundary.get('event', 'Unknown event')}' on {b_date_str}. "
                            f"Impact: {boundary.get('impact', 'Not recorded')}."
                        ),
                        "confidence": "MEDIUM",
                        "lesson": (
                            "Predictions filed within 7 days of a regime boundary are unreliable — "
                            "add a cooldown: skip or flag predictions filed during transition periods."
                        ),
                        "data": {
                            "boundary_date":  b_date_str,
                            "boundary_event": boundary.get("event"),
                            "days_diff":      days_diff,
                        },
                    }
        except Exception as e:
            print(f"  [diagnose] LMD CHECK 3 (regime) error for pred {pred.get('id')}: {e}")

    # Exhausted all checks — return UNKNOWN
    print(f"  [diagnose] No cause found for pred {pred.get('id')} — returning UNKNOWN")
    return unknown


def _month_name(month: int) -> str:
    """Convert month number to abbreviated name."""
    names = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
        5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
        9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }
    return names.get(month, f"Month-{month}")


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 2 — Apply Lesson to Memory
# ─────────────────────────────────────────────────────────────────────────────

def apply_lesson(lesson: str, memory: dict) -> None:
    """
    Update memory["rules"] based on the lesson extracted from a diagnosis.
    Increments counters per cause type and appends to lessons_learned list.

    Does NOT save to disk — caller is responsible for save_memory().
    """
    rules = memory.setdefault("rules", {})

    # ── Increment cause-type counters ────────────────────────────────────────
    cause_counters = {
        "OCP_CLEARED":       "ocp_recovery_count",
        "LMD_IMPROVED":      "lmd_improvement_count",
        "LMD_CHANGED":       "lmd_reassignment_count",
        "SEASONAL_RECOVERY": "seasonal_recovery_count",
        "DEMAND_SPIKE":      "demand_spike_count",
        "TERRITORY_REDUCED": "territory_reduction_count",
        "VOLUME_REDUCED":    "volume_reduction_count",
        "REGIME_CHANGE":     "regime_change_count",
        "UNKNOWN":           "unknown_diagnosis_count",
    }

    # Extract cause_type from lesson string heuristically
    # (lesson is a human-readable sentence; check for keywords)
    cause_found = False
    for cause, counter_key in cause_counters.items():
        if cause.lower().replace("_", " ") in lesson.lower():
            rules[counter_key] = rules.get(counter_key, 0) + 1
            cause_found = True
            print(f"  [apply_lesson] Incremented {counter_key} → {rules[counter_key]}")
            break

    if not cause_found:
        rules["unknown_diagnosis_count"] = rules.get("unknown_diagnosis_count", 0) + 1

    # ── Append to lessons_learned list ───────────────────────────────────────
    lessons_list = rules.setdefault("lessons_learned", [])
    lessons_list.append({
        "date":   _today_ist(),
        "lesson": lesson,
    })

    print(f"  [apply_lesson] Lesson appended. Total lessons: {len(lessons_list)}")


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 3 — Discover New Patterns
# ─────────────────────────────────────────────────────────────────────────────

def discover_patterns(memory: dict, bq_client) -> list[dict]:
    """
    Run weekly. Analyzes all diagnosed WRONG predictions to find statistical
    patterns that suggest new rules or threshold adjustments.

    Only runs if at least MIN_WRONG_FOR_DISCOVERY diagnosed wrong predictions exist.

    Returns list of candidate rule dicts (also stored in memory["candidate_rules"]).
    """
    all_preds   = memory.get("predictions", [])
    wrong_preds = [
        p for p in all_preds
        if p.get("result") == "WRONG" and p.get("diagnosis")
    ]

    print(f"[discover_patterns] {len(wrong_preds)} diagnosed wrong predictions available")

    if len(wrong_preds) < MIN_WRONG_FOR_DISCOVERY:
        print(
            f"[discover_patterns] Not enough data ({len(wrong_preds)} < {MIN_WRONG_FOR_DISCOVERY}). "
            f"Skipping discovery."
        )
        return []

    # ── Group by cause ────────────────────────────────────────────────────────
    by_cause: dict[str, list[dict]] = {}
    for p in wrong_preds:
        cause = p["diagnosis"].get("cause_type", "UNKNOWN")
        by_cause.setdefault(cause, []).append(p)

    print(f"[discover_patterns] Cause distribution: { {k: len(v) for k, v in by_cause.items()} }")

    today      = _today_ist()
    candidates = []

    # ── Pattern 1: SEASONAL_RECOVERY — which months have the most wrong preds? ─
    seasonal_preds = by_cause.get("SEASONAL_RECOVERY", [])
    if len(seasonal_preds) >= MIN_CAUSE_FOR_PATTERN:
        month_counts: dict[int, int] = {}
        for p in wrong_preds:  # all wrong, not just seasonal
            filed = p.get("filed_date", "")
            if len(filed) >= 7:
                m = int(filed[5:7])
                month_counts[m] = month_counts.get(m, 0) + 1

        total_wrong = len(wrong_preds)
        worst_month = max(month_counts, key=month_counts.get) if month_counts else None
        if worst_month:
            worst_count = month_counts[worst_month]
            expected_per_month = total_wrong / max(len(month_counts), 1)
            if worst_count >= expected_per_month * 2:
                candidates.append({
                    "pattern_id":         f"PAT-{len(candidates)+1:03d}",
                    "discovered_date":    today,
                    "type":              "PREDICTION_ACCURACY",
                    "description": (
                        f"Prediction accuracy drops sharply for predictions filed in "
                        f"{_month_name(worst_month)} — {worst_count} of {total_wrong} wrong "
                        f"predictions were filed that month ({worst_count/total_wrong:.0%}), "
                        f"vs expected {expected_per_month:.1f} if uniform."
                    ),
                    "evidence": (
                        f"{worst_count} of {total_wrong} wrong predictions filed in "
                        f"{_month_name(worst_month)} ({worst_count/total_wrong:.0%})"
                    ),
                    "suggested_rule": (
                        f"Lower confidence for all predictions filed in {_month_name(worst_month)} "
                        f"from HIGH/MEDIUM to one level down. "
                        f"Consider skipping DEAD_STORE predictions in this month."
                    ),
                    "requires_validation": True,
                    "status":       "CANDIDATE",
                    "human_note":   None,
                })

    # ── Pattern 2: OCP_CLEARED — how fast do stores recover after OCP clears? ─
    ocp_preds = by_cause.get("OCP_CLEARED", [])
    if len(ocp_preds) >= MIN_CAUSE_FOR_PATTERN:
        try:
            # Build a list of (store_id, filed_date) pairs for OCP-cleared stores
            ocp_pairs = [
                (p.get("entity_id"), p.get("filed_date"), p.get("scored_on"))
                for p in ocp_preds
                if p.get("entity_id") and p.get("filed_date") and p.get("scored_on")
            ]

            if ocp_pairs:
                # Query: for each OCP store, find the first fulfilled order after filing
                # Aggregate using UNION ALL for individual stores (max 20 for safety)
                pairs_sample = ocp_pairs[:20]
                union_parts  = []
                for store_id, filed, _ in pairs_sample:
                    union_parts.append(f"""
                        SELECT
                            {store_id} AS store_id,
                            DATE '{filed}' AS filed_date,
                            MIN(DATE(o.created_on)) AS first_recovery_date
                        FROM `agrostar-data.prod_db_views.order_management_order` o
                        WHERE SAFE_CAST(o.retail_store_code AS INT64) = {store_id}
                          AND DATE(o.created_on) >= DATE '{filed}'
                          AND LOWER(COALESCE(o.initiating_source, '')) NOT LIKE 'b2b%'
                          AND LOWER(COALESCE(o.order_type, '')) NOT LIKE '%offline%'
                          AND o.unicommerce_status NOT IN ('FUTURE ORDER', 'CANCELLED', 'DISPUTED_ADDRESS')
                    """)

                ocp_recovery_sql = f"""
                    WITH recovery_dates AS (
                        {'UNION ALL'.join(union_parts)}
                    )
                    SELECT
                        AVG(DATE_DIFF(first_recovery_date, filed_date, DAY)) AS avg_recovery_days,
                        APPROX_QUANTILES(DATE_DIFF(first_recovery_date, filed_date, DAY), 2)[OFFSET(1)]
                            AS median_recovery_days,
                        COUNT(*) AS stores_with_recovery
                    FROM recovery_dates
                    WHERE first_recovery_date IS NOT NULL
                """
                rows = _bq_rows(bq_client, ocp_recovery_sql)
                if rows and rows[0][0] is not None:
                    avg_days    = float(rows[0][0])
                    median_days = float(rows[0][1] or avg_days)
                    stores_cnt  = int(rows[0][2] or 0)

                    if median_days <= 7:
                        candidates.append({
                            "pattern_id":      f"PAT-{len(candidates)+1:03d}",
                            "discovered_date": today,
                            "type":           "THRESHOLD_ADJUSTMENT",
                            "description": (
                                f"OCP-blocked stores with MPD recover within "
                                f"{median_days:.0f} days median ({avg_days:.1f} avg) "
                                f"across {stores_cnt} stores. DRISHTI is filing DEAD predictions "
                                f"that resolve too quickly."
                            ),
                            "evidence": (
                                f"median recovery = {median_days:.0f}d, avg = {avg_days:.1f}d "
                                f"across {stores_cnt} OCP-cleared stores"
                            ),
                            "suggested_rule": (
                                "For OCP_BLOCKED category, lower dead_persistence_prior to 0.30. "
                                "Add check: if store has MPD enabled, classify as OCP_RECOVERY_LIKELY "
                                "instead of DEAD_STORE_RISK."
                            ),
                            "requires_validation": True,
                            "status":       "CANDIDATE",
                            "human_note":   None,
                        })
        except Exception as e:
            print(f"  [discover_patterns] OCP pattern error: {e}")

    # ── Pattern 3: LMD_IMPROVED — what triggers improvement? ─────────────────
    lmd_improved_preds = by_cause.get("LMD_IMPROVED", [])
    if len(lmd_improved_preds) >= MIN_CAUSE_FOR_PATTERN:
        try:
            franchise_ids = [
                str(p.get("entity_id"))
                for p in lmd_improved_preds
                if p.get("entity_id")
            ][:20]

            if franchise_ids:
                fid_list = ",".join(franchise_ids)
                lmd_trigger_sql = f"""
                    WITH improvement_sample AS (
                        SELECT
                            sp.to_franchise_id,
                            COUNT(DISTINCT SAFE_CAST(o.retail_store_code AS INT64)) AS store_count,
                            COUNT(*) AS dvs_orders
                        FROM `agrostar-data.prod_db_views.delivery_shippingpackage` sp
                        JOIN `agrostar-data.prod_db_views.order_management_order` o
                          ON sp.order_id = CAST(o.unicommerce_id AS STRING)
                        WHERE sp.to_franchise_id IN ({fid_list})
                          AND DATE(sp.order_placed_date) >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
                          AND o.retail_store_code IS NOT NULL
                          AND o.retail_store_code != ''
                        GROUP BY 1
                    )
                    SELECT
                        AVG(store_count) AS avg_stores_per_lmd,
                        AVG(dvs_orders)  AS avg_dvs_orders_per_lmd,
                        COUNT(*)         AS lmds_analyzed
                    FROM improvement_sample
                """
                rows = _bq_rows(bq_client, lmd_trigger_sql)
                if rows and rows[0][0] is not None:
                    avg_stores = float(rows[0][0])
                    avg_orders = float(rows[0][1] or 0)
                    lmds_cnt   = int(rows[0][2] or 0)

                    candidates.append({
                        "pattern_id":      f"PAT-{len(candidates)+1:03d}",
                        "discovered_date": today,
                        "type":           "NEW_SIGNAL",
                        "description": (
                            f"LMD improvement group ({lmds_cnt} franchises) has "
                            f"avg {avg_stores:.1f} stores and {avg_orders:.1f} DVS orders — "
                            f"analyze whether store count or volume is the primary driver."
                        ),
                        "evidence": (
                            f"{len(lmd_improved_preds)} LMD_IMPROVED diagnoses analyzed; "
                            f"avg store count = {avg_stores:.1f}, avg orders = {avg_orders:.1f}"
                        ),
                        "suggested_rule": (
                            "Add store_count_per_lmd as a predictive feature. "
                            "LMDs with >15 stores assigned are systematically overloaded — "
                            "lower the SELECTIVE_DEPRIORITIZATION confidence to MEDIUM for them "
                            "and raise GENUINE_OVERLOAD flag instead."
                        ),
                        "requires_validation": True,
                        "status":       "CANDIDATE",
                        "human_note":   None,
                    })
        except Exception as e:
            print(f"  [discover_patterns] LMD improvement pattern error: {e}")

    # ── Pattern 4: Statistical comparison — wrong vs correct predictions ──────
    # For DEAD_STORE_RISK: do wrong predictions have systematically lower GMV?
    correct_preds = [
        p for p in all_preds
        if p.get("result") == "CORRECT" and p.get("type") == "DEAD_STORE_RISK"
    ]
    wrong_dead = [
        p for p in wrong_preds
        if p.get("type") == "DEAD_STORE_RISK"
    ]

    if len(correct_preds) >= 3 and len(wrong_dead) >= 3:
        avg_gmv_correct = sum(p.get("gmv_at_risk", 0) for p in correct_preds) / len(correct_preds)
        avg_gmv_wrong   = sum(p.get("gmv_at_risk", 0) for p in wrong_dead)   / len(wrong_dead)

        if avg_gmv_correct > avg_gmv_wrong * 1.5:
            candidates.append({
                "pattern_id":      f"PAT-{len(candidates)+1:03d}",
                "discovered_date": today,
                "type":           "THRESHOLD_ADJUSTMENT",
                "description": (
                    f"Correct DEAD_STORE predictions have avg GMV at risk = "
                    f"₹{avg_gmv_correct:,.0f}/mo vs wrong predictions = "
                    f"₹{avg_gmv_wrong:,.0f}/mo ({avg_gmv_correct/avg_gmv_wrong:.1f}x higher). "
                    f"Low-GMV stores recover much more often than high-GMV stores."
                ),
                "evidence": (
                    f"{len(correct_preds)} correct vs {len(wrong_dead)} wrong DEAD_STORE predictions. "
                    f"Avg GMV correct=₹{avg_gmv_correct:,.0f} wrong=₹{avg_gmv_wrong:,.0f}"
                ),
                "suggested_rule": (
                    "For DEAD_STORE_RISK predictions, lower confidence from HIGH to MEDIUM "
                    "when gmv_at_risk < ₹30,000/month. Small stores recover more than predicted."
                ),
                "requires_validation": True,
                "status":       "CANDIDATE",
                "human_note":   None,
            })

    # ── Store candidates in memory ────────────────────────────────────────────
    existing_candidates = memory.setdefault("candidate_rules", [])
    existing_ids = {c.get("pattern_id") for c in existing_candidates}

    added = 0
    for cand in candidates:
        if cand["pattern_id"] not in existing_ids:
            existing_candidates.append(cand)
            added += 1

    # Update last discovery date
    memory["last_pattern_discovery_date"] = today

    print(f"[discover_patterns] {len(candidates)} candidates generated, {added} new. Total in memory: {len(existing_candidates)}")
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Slack Report Formatter
# ─────────────────────────────────────────────────────────────────────────────

def format_learning_report(
    diagnosed_preds: list[dict],
    new_candidates: list[dict],
    memory: dict,
) -> str:
    """
    Build a Slack message summarising:
    1. What DRISHTI got wrong this week and why
    2. New patterns discovered
    3. Rules it wants to update
    4. Human validation requests for candidate rules

    Returns formatted Slack text (mrkdwn compatible).
    """
    today = datetime.now(IST).strftime("%b %d, %Y")
    lines = [
        f":brain: *DRISHTI Learning Report*  |  {today}",
        "",
    ]

    # ── Section 1: Wrong predictions diagnosed ───────────────────────────────
    if diagnosed_preds:
        lines.append(f"*WRONG PREDICTIONS — DIAGNOSES* " + "━" * 30)
        cause_summary: dict[str, int] = {}
        for p in diagnosed_preds:
            diag  = p.get("diagnosis", {})
            cause = diag.get("cause_type", "UNKNOWN")
            cause_summary[cause] = cause_summary.get(cause, 0) + 1

            conf_sym = {
                "HIGH":   ":large_green_circle:",
                "MEDIUM": ":large_yellow_circle:",
                "LOW":    ":white_circle:",
            }.get(diag.get("confidence", "LOW"), ":white_circle:")

            lines.append(
                f"  {conf_sym} *{p.get('entity_name', '?')}* ({p.get('type', '?')})  "
                f"→ *{cause}*"
            )
            evidence = diag.get("evidence", "")
            if evidence:
                lines.append(f"    _{evidence[:120]}{'...' if len(evidence) > 120 else ''}_")

        lines.append("")
        lines.append("*Cause summary:*")
        for cause, cnt in sorted(cause_summary.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cause}: {cnt} prediction{'s' if cnt > 1 else ''}")
        lines.append("")
    else:
        lines.append("*WRONG PREDICTIONS*  No new diagnoses this cycle.")
        lines.append("")

    # ── Section 2: Lessons being applied ────────────────────────────────────
    rules = memory.get("rules", {})
    lessons = rules.get("lessons_learned", [])
    recent_lessons = lessons[-5:] if lessons else []  # last 5

    if recent_lessons:
        lines.append("*LESSONS BEING APPLIED* " + "━" * 33)
        for l in recent_lessons:
            lines.append(f"  :bulb: [{l.get('date', '?')}] {l.get('lesson', '?')}")
        lines.append("")

    # ── Section 3: New pattern candidates ────────────────────────────────────
    if new_candidates:
        lines.append("*NEW PATTERNS DISCOVERED* " + "━" * 31)
        for cand in new_candidates:
            lines.append(
                f"  :mag: *{cand['pattern_id']}* — {cand['type']}"
            )
            lines.append(f"    {cand['description'][:150]}...")
            lines.append(f"    *Suggested change:* {cand['suggested_rule'][:150]}")
            lines.append("")
    else:
        lines.append("*PATTERN DISCOVERY*  No new patterns this cycle (need more data or not yet due).")
        lines.append("")

    # ── Section 4: Pending validations ───────────────────────────────────────
    candidate_rules = memory.get("candidate_rules", [])
    pending = [c for c in candidate_rules if c.get("status") == "CANDIDATE"]

    if pending:
        lines.append("*PENDING HUMAN VALIDATION* " + "━" * 30)
        lines.append(
            "_Reply YES to accept a rule into DRISHTI's logic, NO to reject, or LATER to defer._"
        )
        lines.append("")
        for cand in pending[:5]:  # Show at most 5 pending at once
            lines.append(f"  :ballot_box_with_check: *{cand['pattern_id']}* — {cand['description'][:100]}...")
            lines.append(f"    Suggested: `{cand['suggested_rule'][:120]}`")
            lines.append(f"    Evidence: {cand['evidence'][:100]}")
            lines.append("")
    else:
        lines.append("*VALIDATIONS*  No candidate rules awaiting human review.")
        lines.append("")

    # ── Section 5: Accuracy summary ──────────────────────────────────────────
    acc = rules.get("prediction_accuracy", {})
    if acc and acc.get("total", 0) > 0:
        total   = acc["total"]
        correct = acc.get("correct", 0)
        wrong   = acc.get("wrong", 0)
        direct  = acc.get("directional", 0)
        pct_correct = correct / total * 100
        lines.append("*PREDICTION ACCURACY (ALL TIME)* " + "━" * 25)
        lines.append(
            f"  {total} scored  |  "
            f":white_check_mark: {correct} correct ({pct_correct:.0f}%)  "
            f":x: {wrong} wrong  "
            f":arrow_upper_right: {direct} directional"
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — run_learning_cycle
# ─────────────────────────────────────────────────────────────────────────────

def run_learning_cycle(memory: dict, bq_client) -> dict:
    """
    Orchestrates Level 2 (daily) and Level 3 (weekly) learning.

    Steps:
      1. Find all WRONG predictions not yet diagnosed
      2. Run diagnose_wrong_prediction() for each
      3. Attach diagnosis to prediction; apply lesson to memory
      4. If weekly cadence is due, run discover_patterns()
      5. Return summary dict

    Does NOT save memory to disk — caller must call save_memory() / _save_memory().
    """
    today = _today_ist()
    summary = {
        "run_date":       today,
        "diagnosed_count": 0,
        "newly_diagnosed": [],
        "pattern_candidates": [],
        "weekly_ran": False,
    }

    # ── LEVEL 2: Diagnose undiagnosed wrong predictions ───────────────────────
    all_preds = memory.get("predictions", [])
    undiagnosed_wrong = [
        p for p in all_preds
        if p.get("result") == "WRONG" and not p.get("diagnosis")
    ]

    print(f"\n[learn] Level 2 — {len(undiagnosed_wrong)} undiagnosed wrong predictions")

    for pred in undiagnosed_wrong:
        print(f"  [learn] Diagnosing {pred.get('id')} ({pred.get('type')}) — {pred.get('entity_name', '?')}")
        try:
            diag = diagnose_wrong_prediction(pred, bq_client, memory)
            pred["diagnosis"] = diag
            pred["lesson"]    = diag.get("lesson")

            apply_lesson(diag.get("lesson", ""), memory)

            summary["diagnosed_count"] += 1
            summary["newly_diagnosed"].append({
                "id":         pred["id"],
                "entity_name": pred.get("entity_name"),
                "type":       pred.get("type"),
                "cause_type": diag.get("cause_type"),
                "confidence": diag.get("confidence"),
                "lesson":     diag.get("lesson"),
            })
            print(f"    → cause={diag['cause_type']} confidence={diag['confidence']}")
        except Exception as e:
            print(f"  [learn] Error diagnosing {pred.get('id')}: {e}")
            traceback.print_exc()
            # Mark with a failed diagnosis so we don't retry endlessly
            pred["diagnosis"] = {
                "cause_type": "UNKNOWN",
                "evidence":   f"Diagnosis failed with error: {e}",
                "confidence": "LOW",
                "lesson":     "Diagnosis errored — manual review needed.",
                "data":       {},
            }

    # ── LEVEL 3: Weekly pattern discovery ────────────────────────────────────
    last_discovery = memory.get("last_pattern_discovery_date")
    days_since_discovery = float("inf")

    if last_discovery:
        try:
            last_dt = datetime.fromisoformat(last_discovery)
            today_dt = datetime.fromisoformat(today)
            days_since_discovery = (today_dt - last_dt).days
        except Exception:
            pass

    run_weekly = days_since_discovery >= DISCOVERY_INTERVAL_DAYS
    print(
        f"\n[learn] Level 3 — days since last discovery: "
        f"{'∞ (never run)' if days_since_discovery == float('inf') else days_since_discovery}. "
        f"{'Running.' if run_weekly else 'Skipping (not yet due).'}"
    )

    if run_weekly:
        new_candidates = discover_patterns(memory, bq_client)
        summary["pattern_candidates"] = new_candidates
        summary["weekly_ran"] = True
    else:
        new_candidates = []

    # ── Attach diagnosed predictions for report ───────────────────────────────
    diagnosed_preds_full = [
        p for p in all_preds
        if p.get("result") == "WRONG"
        and p.get("diagnosis")
        and p.get("id") in {d["id"] for d in summary["newly_diagnosed"]}
    ]

    print(
        f"\n[learn] Cycle complete — diagnosed: {summary['diagnosed_count']}, "
        f"new patterns: {len(new_candidates)}"
    )
    summary["diagnosed_preds_full"] = diagnosed_preds_full
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Slack Posting Helper
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
            print(f"  [slack] Non-ok response: {resp}")
            return False
    except SlackApiError as e:
        print(f"  [slack] SlackApiError: {e.response['error']}")
        return False
    except Exception as e:
        print(f"  [slack] Unexpected error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    dry_run      = "--dry-run"      in sys.argv
    level2_only  = "--level2-only"  in sys.argv
    level3_only  = "--level3-only"  in sys.argv

    start = datetime.now(IST)
    print(f"\n{'='*60}")
    print(f"DVS DRISHTI — Learning Cycle")
    print(f"Started: {start.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*60}\n")

    # ── Init clients ──────────────────────────────────────────────────────────
    bq_client    = bigquery.Client(project=PROJECT)
    slack_client = WebClient(token=SLACK_TOKEN) if SLACK_TOKEN else None
    if not slack_client:
        print("[LEARN] Warning: SLACK_BOT_TOKEN not set. Running in dry-run mode.")
        dry_run = True

    # ── Load memory ───────────────────────────────────────────────────────────
    memory = _load_memory()
    if not memory:
        print("[LEARN] Memory is empty or missing. Nothing to diagnose.")
        return

    # ── If level3_only, skip to discovery ────────────────────────────────────
    if level3_only:
        print("[LEARN] --level3-only: skipping Level 2 diagnosis")
        new_candidates = discover_patterns(memory, bq_client)
        _save_memory(memory)
        report = format_learning_report([], new_candidates, memory)
        post_to_slack(slack_client, SLACK_CHANNEL, report, dry_run=dry_run)
        return

    # ── Run full cycle (or Level 2 only) ─────────────────────────────────────
    if level2_only:
        # Force skip weekly discovery by setting last_pattern_discovery_date to today
        memory["last_pattern_discovery_date"] = _today_ist()

    summary = run_learning_cycle(memory, bq_client)
    _save_memory(memory)

    # ── Format and post report ────────────────────────────────────────────────
    diagnosed_preds = summary.get("diagnosed_preds_full", [])
    new_candidates  = summary.get("pattern_candidates", [])

    report = format_learning_report(diagnosed_preds, new_candidates, memory)
    post_to_slack(slack_client, SLACK_CHANNEL, report, dry_run=dry_run)

    # ── Completion summary ────────────────────────────────────────────────────
    end     = datetime.now(IST)
    elapsed = (end - start).seconds

    print(f"\n{'='*60}")
    print("DVS DRISHTI — Learning Cycle Complete")
    print(f"{'='*60}")
    print(f"  Elapsed:            {elapsed}s")
    print(f"  Wrong predictions:  {summary['diagnosed_count']} newly diagnosed")
    print(f"  Weekly discovery:   {'Yes' if summary['weekly_ran'] else 'No (not due)'}")
    print(f"  Pattern candidates: {len(new_candidates)} new")
    print(f"  Memory:             {MEMORY_PATH}")
    print(f"  Finished:           {end.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
