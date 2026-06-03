#!/usr/bin/env python3
"""
DVS DRISHTI — Store Behavioral Fingerprint Builder
Parses three BigQuery result files and produces store fingerprints + scoring.
"""

import json
import sys
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────────────────
# File paths
# ─────────────────────────────────────────────────────────────────────────────
FILE1 = "/Users/darpan/.claude/projects/-Users-darpan-Documents-claude-code-DVS-Analysis/4882f5b6-b0fd-49df-8cb2-e02ebcdf2871/tool-results/mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085207391.txt"
FILE2 = "/Users/darpan/.claude/projects/-Users-darpan-Documents-claude-code-DVS-Analysis/4882f5b6-b0fd-49df-8cb2-e02ebcdf2871/tool-results/mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085216563.txt"
FILE3 = "/Users/darpan/.claude/projects/-Users-darpan-Documents-claude-code-DVS-Analysis/4882f5b6-b0fd-49df-8cb2-e02ebcdf2871/tool-results/mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085219263.txt"

MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]
MONTH_LABELS = ["Nov-25", "Dec-25", "Jan-26", "Feb-26", "Mar-26", "Apr-26"]

AVG_ORDER_VALUE = 850  # ₹

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def parse_bq_file(path):
    """Read a BigQuery JSON result file (single line) and return list of row dicts."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    data = json.loads(raw)
    return data.get("rows", [])

def v(field):
    """Extract value string from a BQ field dict."""
    val = field.get("v")
    return val if val is not None else ""

def safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def safe_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0

def indian_comma(n):
    """Format integer with Indian comma style."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        return ("−" if n < 0 else "") + s
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while len(rest) > 2:
        parts.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.append(rest)
    parts.reverse()
    return ("−" if n < 0 else "") + ",".join(parts) + "," + last3


# ─────────────────────────────────────────────────────────────────────────────
# Parse File 1 — Volume trajectory
# Schema: store_id, month, fulfilled_orders
# ─────────────────────────────────────────────────────────────────────────────
print("Parsing File 1 (volume trajectory)...", flush=True)
rows1 = parse_bq_file(FILE1)
# volume[store_id][month] = fulfilled_orders
volume = defaultdict(lambda: defaultdict(int))
for row in rows1:
    fields = row["f"]
    sid = v(fields[0])
    month = v(fields[1])
    orders = safe_int(v(fields[2]))
    volume[sid][month] = orders

print(f"  → {len(rows1)} rows, {len(volume)} unique stores", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Parse File 2 — Health signals
# Schema: store_id, month, total_orders, on_hold_count, hold_by_lmd_count,
#         packed_count, delivered_count, returned_count, on_hold_pct, hold_by_lmd_pct
# ─────────────────────────────────────────────────────────────────────────────
print("Parsing File 2 (health signals)...", flush=True)
rows2 = parse_bq_file(FILE2)
# health[store_id][month] = dict of signals
health = defaultdict(dict)
for row in rows2:
    fields = row["f"]
    sid    = v(fields[0])
    month  = v(fields[1])
    health[sid][month] = {
        "total_orders"     : safe_int(v(fields[2])),
        "on_hold_count"    : safe_int(v(fields[3])),
        "hold_by_lmd_count": safe_int(v(fields[4])),
        "packed_count"     : safe_int(v(fields[5])),
        "delivered_count"  : safe_int(v(fields[6])),
        "returned_count"   : safe_int(v(fields[7])),
        "on_hold_pct"      : safe_float(v(fields[8])),
        "hold_by_lmd_pct"  : safe_float(v(fields[9])),
    }

print(f"  → {len(rows2)} rows, {len(health)} unique stores", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Parse File 3 — Store metadata
# Schema: store_id, store_name, state, district, status, taluka_count
# ─────────────────────────────────────────────────────────────────────────────
print("Parsing File 3 (store metadata)...", flush=True)
rows3 = parse_bq_file(FILE3)
meta = {}
for row in rows3:
    fields = row["f"]
    sid = v(fields[0])
    meta[sid] = {
        "store_name"  : v(fields[1]),
        "state"       : v(fields[2]),
        "district"    : v(fields[3]),
        "status"      : v(fields[4]),
        "taluka_count": safe_int(v(fields[5])),
    }

print(f"  → {len(rows3)} rows, {len(meta)} unique stores", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Build Fingerprints
# ─────────────────────────────────────────────────────────────────────────────
print("\nBuilding store fingerprints...", flush=True)

def classify_trend(mar, apr):
    if apr == 0 and mar == 0:
        return "NEVER_ACTIVE"
    if apr == 0 and mar > 0:
        return "DEAD"
    if mar == 0 and apr > 0:
        return "NEW"
    if apr > mar * 1.1:
        return "GROWING"
    if apr < mar * 0.9:
        return "DECLINING"
    return "STABLE"

def get_health_for_store(sid):
    """Return best available health signals: Apr preferred, fallback Mar."""
    h = health.get(sid, {})
    if "2026-04" in h:
        row = h["2026-04"]
        return row["on_hold_pct"], row["hold_by_lmd_pct"], "Apr-26"
    elif "2026-03" in h:
        row = h["2026-03"]
        return row["on_hold_pct"], row["hold_by_lmd_pct"], "Mar-26"
    return None, None, None

fingerprints = []

for sid, monthly in volume.items():
    m = meta.get(sid, {})
    store_name   = m.get("store_name", "UNKNOWN")
    state        = m.get("state", "UNKNOWN")
    district     = m.get("district", "UNKNOWN")
    status       = m.get("status", "UNKNOWN")
    taluka_count = m.get("taluka_count", 0)

    # Monthly volumes for each of 6 months
    vols = {mo: monthly.get(mo, 0) for mo in MONTHS}
    mar  = vols["2026-03"]
    apr  = vols["2026-04"]

    trend = classify_trend(mar, apr)

    on_hold_pct, hold_by_lmd_pct, health_month = get_health_for_store(sid)

    # Leaky bucket scoring
    monthly_gmv_at_risk = apr * AVG_ORDER_VALUE

    # Recoverability
    if on_hold_pct is None:
        recoverability = "UNKNOWN"
    elif hold_by_lmd_pct > 50:
        recoverability = "LOW"
    elif on_hold_pct < 20 and hold_by_lmd_pct < 30:
        recoverability = "HIGH"
    else:
        recoverability = "MEDIUM"

    # Urgency
    if trend == "DEAD":
        urgency = 3
    elif trend == "DECLINING":
        urgency = 2
    else:
        urgency = 1

    priority_score = monthly_gmv_at_risk * urgency

    # Priority tier
    if trend in ("DEAD", "DECLINING") and priority_score > 50000:
        priority_tier = "P1"
    elif trend == "DECLINING" and 20000 <= priority_score <= 50000:
        priority_tier = "P2"
    elif trend in ("DECLINING", "NEVER_ACTIVE") and priority_score < 20000:
        priority_tier = "P3"
    elif trend in ("STABLE", "GROWING"):
        priority_tier = "P4"
    elif trend == "DEAD" and priority_score <= 50000:
        # DEAD but small GMV → P3 if not caught above
        priority_tier = "P3"
    elif trend == "NEW":
        priority_tier = "P4"
    else:
        priority_tier = "P3"

    fingerprints.append({
        "store_id"          : sid,
        "store_name"        : store_name,
        "state"             : state,
        "district"          : district,
        "status"            : status,
        "taluka_count"      : taluka_count,
        "nov25"             : vols["2025-11"],
        "dec25"             : vols["2025-12"],
        "jan26"             : vols["2026-01"],
        "feb26"             : vols["2026-02"],
        "mar26"             : mar,
        "apr26"             : apr,
        "trend"             : trend,
        "on_hold_pct"       : on_hold_pct,
        "hold_by_lmd_pct"   : hold_by_lmd_pct,
        "health_month"      : health_month,
        "monthly_gmv_at_risk": monthly_gmv_at_risk,
        "recoverability"    : recoverability,
        "urgency"           : urgency,
        "priority_score"    : priority_score,
        "priority_tier"     : priority_tier,
    })

print(f"  → {len(fingerprints)} store fingerprints built", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────
SEP  = "═" * 120
SEP2 = "─" * 120

def pct_str(val):
    if val is None:
        return "  N/A "
    return f"{val:5.1f}%"

def vol_str(v):
    return f"{v:4d}" if v else "   -"


# ─────────────────────────────────────────────────────────────────────────────
# A. PROGRAM-LEVEL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("A. PROGRAM-LEVEL SUMMARY")
print(SEP)

total_stores = len(fingerprints)
trend_counts = defaultdict(int)
for fp in fingerprints:
    trend_counts[fp["trend"]] += 1

print(f"\nTotal unique stores in fingerprint: {total_stores}")
print(f"\nTrend Classification Breakdown:")
for t in ["GROWING", "STABLE", "DECLINING", "DEAD", "NEW", "NEVER_ACTIVE"]:
    cnt = trend_counts[t]
    pct = cnt / total_stores * 100
    bar = "█" * int(pct / 2)
    print(f"  {t:<14} {cnt:>4}  ({pct:5.1f}%)  {bar}")

# State breakdown (top 8)
state_counts = defaultdict(int)
for fp in fingerprints:
    state_counts[fp["state"]] += 1

print(f"\nTop 8 States by Store Count:")
sorted_states = sorted(state_counts.items(), key=lambda x: -x[1])
for state, cnt in sorted_states[:8]:
    pct = cnt / total_stores * 100
    bar = "█" * int(pct / 2)
    print(f"  {state:<25} {cnt:>4}  ({pct:5.1f}%)  {bar}")

# Program-wide Apr vs Mar trend
all_apr = [fp["apr26"] for fp in fingerprints]
all_mar = [fp["mar26"] for fp in fingerprints]
avg_apr = sum(all_apr) / len(all_apr) if all_apr else 0
avg_mar = sum(all_mar) / len(all_mar) if all_mar else 0
total_apr = sum(all_apr)
total_mar = sum(all_mar)
chg = (avg_apr - avg_mar) / avg_mar * 100 if avg_mar else 0

print(f"\nProgram-wide Volume Trend (Mar-26 → Apr-26):")
print(f"  Avg fulfilled orders Mar-26: {avg_mar:.1f}")
print(f"  Avg fulfilled orders Apr-26: {avg_apr:.1f}  ({chg:+.1f}%)")
print(f"  Total orders Mar-26: {indian_comma(total_mar)}  →  Apr-26: {indian_comma(total_apr)}")
print(f"  Total GMV at risk (Apr × ₹850): ₹{indian_comma(total_apr * AVG_ORDER_VALUE)}")


# ─────────────────────────────────────────────────────────────────────────────
# B. TOP 20 AT-RISK STORES (P1 — DEAD + DECLINING, highest priority_score)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("B. TOP 20 AT-RISK STORES (P1 — DEAD + DECLINING, highest priority_score)")
print(SEP)

at_risk = [fp for fp in fingerprints if fp["trend"] in ("DEAD", "DECLINING")]
at_risk_sorted = sorted(at_risk, key=lambda x: -x["priority_score"])
top20_risk = at_risk_sorted[:20]

hdr = f"{'#':>2} {'StoreID':>8}  {'Store Name':<40} {'State':<15} {'District':<20} {'Nov':>4} {'Dec':>4} {'Jan':>4} {'Feb':>4} {'Mar':>4} {'Apr':>4}  {'Trend':<10} {'OnHld':>6} {'LMD':>6}  {'GMVRisk':>10} {'PScore':>10}"
print(hdr)
print(SEP2)

for i, fp in enumerate(top20_risk, 1):
    print(
        f"{i:>2} {fp['store_id']:>8}  "
        f"{fp['store_name'][:40]:<40} "
        f"{fp['state'][:15]:<15} "
        f"{fp['district'][:20]:<20} "
        f"{vol_str(fp['nov25'])} "
        f"{vol_str(fp['dec25'])} "
        f"{vol_str(fp['jan26'])} "
        f"{vol_str(fp['feb26'])} "
        f"{vol_str(fp['mar26'])} "
        f"{vol_str(fp['apr26'])}  "
        f"{fp['trend']:<10} "
        f"{pct_str(fp['on_hold_pct'])} "
        f"{pct_str(fp['hold_by_lmd_pct'])}  "
        f"₹{indian_comma(fp['monthly_gmv_at_risk']):>9} "
        f"₹{indian_comma(fp['priority_score']):>9}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# C. TOP 20 STRONGEST PERFORMERS (highest Apr, GROWING or STABLE)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("C. TOP 20 STRONGEST PERFORMERS (GROWING or STABLE, highest Apr-26 orders)")
print(SEP)

strong = [fp for fp in fingerprints if fp["trend"] in ("GROWING", "STABLE")]
strong_sorted = sorted(strong, key=lambda x: -x["apr26"])
top20_strong = strong_sorted[:20]

print(hdr)
print(SEP2)

for i, fp in enumerate(top20_strong, 1):
    print(
        f"{i:>2} {fp['store_id']:>8}  "
        f"{fp['store_name'][:40]:<40} "
        f"{fp['state'][:15]:<15} "
        f"{fp['district'][:20]:<20} "
        f"{vol_str(fp['nov25'])} "
        f"{vol_str(fp['dec25'])} "
        f"{vol_str(fp['jan26'])} "
        f"{vol_str(fp['feb26'])} "
        f"{vol_str(fp['mar26'])} "
        f"{vol_str(fp['apr26'])}  "
        f"{fp['trend']:<10} "
        f"{pct_str(fp['on_hold_pct'])} "
        f"{pct_str(fp['hold_by_lmd_pct'])}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# D. DEAD STORES (Apr=0, Mar>0) — Full List
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
dead_list = [fp for fp in fingerprints if fp["trend"] == "DEAD"]
dead_sorted = sorted(dead_list, key=lambda x: -x["mar26"])
print(f"D. DEAD STORES (Apr-26 = 0, Mar-26 > 0) — {len(dead_sorted)} stores")
print(SEP)

print(f"{'#':>3} {'StoreID':>8}  {'Store Name':<40} {'State':<15} {'District':<20} {'Mar26':>5}  {'OnHld':>6} {'LMD':>6}")
print(SEP2)

for i, fp in enumerate(dead_sorted, 1):
    print(
        f"{i:>3} {fp['store_id']:>8}  "
        f"{fp['store_name'][:40]:<40} "
        f"{fp['state'][:15]:<15} "
        f"{fp['district'][:20]:<20} "
        f"{fp['mar26']:>5}  "
        f"{pct_str(fp['on_hold_pct'])} "
        f"{pct_str(fp['hold_by_lmd_pct'])}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# E. LMD RISK SIGNALS (hold_by_lmd_pct > 40% in Apr-26)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")

lmd_risk = []
for fp in fingerprints:
    h_val = fp.get("hold_by_lmd_pct")
    hm    = fp.get("health_month")
    if h_val is not None and h_val > 40 and hm == "Apr-26":
        lmd_risk.append(fp)

lmd_risk_sorted = sorted(lmd_risk, key=lambda x: -x["hold_by_lmd_pct"])
print(f"E. LMD RISK SIGNALS (hold_by_lmd_pct > 40% in Apr-26) — {len(lmd_risk_sorted)} stores")
print(SEP)

print(f"{'#':>3} {'StoreID':>8}  {'Store Name':<40} {'State':<15} {'District':<20} {'Apr26':>5}  {'LMD%':>6}  {'OnHold%':>7}  {'Trend':<10}")
print(SEP2)

for i, fp in enumerate(lmd_risk_sorted, 1):
    print(
        f"{i:>3} {fp['store_id']:>8}  "
        f"{fp['store_name'][:40]:<40} "
        f"{fp['state'][:15]:<15} "
        f"{fp['district'][:20]:<20} "
        f"{fp['apr26']:>5}  "
        f"{pct_str(fp['hold_by_lmd_pct'])} "
        f" {pct_str(fp['on_hold_pct'])}  "
        f"{fp['trend']:<10}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# F. STATE-LEVEL HEALTH SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("F. STATE-LEVEL HEALTH SUMMARY")
print(SEP)

# Aggregate by state
state_agg = defaultdict(lambda: {
    "stores": [], "GROWING": 0, "STABLE": 0, "DECLINING": 0,
    "DEAD": 0, "NEW": 0, "NEVER_ACTIVE": 0,
    "apr_total": 0, "on_hold_vals": [], "hold_lmd_vals": []
})

for fp in fingerprints:
    s = fp["state"]
    state_agg[s]["stores"].append(fp["store_id"])
    state_agg[s][fp["trend"]] += 1
    state_agg[s]["apr_total"] += fp["apr26"]
    if fp["on_hold_pct"] is not None:
        state_agg[s]["on_hold_vals"].append(fp["on_hold_pct"])
    if fp["hold_by_lmd_pct"] is not None:
        state_agg[s]["hold_lmd_vals"].append(fp["hold_by_lmd_pct"])

rows_f = []
for state, agg in state_agg.items():
    cnt = len(agg["stores"])
    avg_apr = agg["apr_total"] / cnt if cnt else 0
    avg_oh  = sum(agg["on_hold_vals"]) / len(agg["on_hold_vals"]) if agg["on_hold_vals"] else None
    rows_f.append((state, cnt, agg["GROWING"], agg["STABLE"], agg["DECLINING"],
                   agg["DEAD"], avg_apr, avg_oh))

rows_f.sort(key=lambda x: -x[1])

hdr_f = f"{'State':<25} {'Stores':>6} {'GROW':>5} {'STBL':>5} {'DECL':>5} {'DEAD':>5} {'AvgApr':>7} {'AvgOnHld':>8}"
print(hdr_f)
print(SEP2)

for row in rows_f:
    state, cnt, grow, stbl, decl, dead, avg_apr, avg_oh = row
    oh_str = f"{avg_oh:6.1f}%" if avg_oh is not None else "    N/A"
    print(
        f"{state:<25} {cnt:>6} {grow:>5} {stbl:>5} {decl:>5} {dead:>5} "
        f"{avg_apr:>7.1f} {oh_str:>8}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY TIER SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("G. PRIORITY TIER SUMMARY")
print(SEP)

tier_counts = defaultdict(int)
tier_gmv    = defaultdict(int)
for fp in fingerprints:
    tier_counts[fp["priority_tier"]] += 1
    tier_gmv[fp["priority_tier"]]    += fp["monthly_gmv_at_risk"]

for tier in ["P1", "P2", "P3", "P4"]:
    desc = {
        "P1": "DEAD or DECLINING, priority_score > 50,000 — Immediate action",
        "P2": "DECLINING, priority_score 20,000–50,000 — High attention",
        "P3": "Small DECLINING / NEVER_ACTIVE — Monitor",
        "P4": "STABLE or GROWING — Monitor only",
    }[tier]
    print(f"  {tier}: {tier_counts[tier]:>4} stores  |  Monthly GMV: ₹{indian_comma(tier_gmv[tier])}  |  {desc}")

print(f"\n{SEP}")
print("FINGERPRINT BUILD COMPLETE")
print(SEP)
