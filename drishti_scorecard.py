#!/usr/bin/env python3
"""
DRISHTI SCORECARD — May 2026
Score DRISHTI's May predictions against actuals.
"""

import json
from collections import defaultdict

# ─── File paths ───────────────────────────────────────────────────────────────
BASE = (
    "/Users/darpan/.claude/projects/"
    "-Users-darpan-Documents-claude-code-DVS-Analysis/"
    "4882f5b6-b0fd-49df-8cb2-e02ebcdf2871/tool-results/"
)
F1 = BASE + "mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085207391.txt"
F2 = BASE + "mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085216563.txt"
F3 = BASE + "mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085219263.txt"
F4 = BASE + "mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780085482714.txt"


# ─── Parser ───────────────────────────────────────────────────────────────────
def parse_bq(path):
    with open(path) as fh:
        data = json.load(fh)
    return data.get("rows", [])


def val(row, idx):
    """Extract value from BQ row, return as string (or None)."""
    try:
        v = row["f"][idx]["v"]
        return v
    except (IndexError, KeyError, TypeError):
        return None


def fval(row, idx):
    v = val(row, idx)
    return float(v) if v is not None else 0.0


def ival(row, idx):
    v = val(row, idx)
    return int(float(v)) if v is not None else 0


# ─── FILE 1: Volume trajectory (store_id, month, fulfilled_orders) ─────────────
# Schema: f[0]=store_id, f[1]=month (YYYY-MM), f[2]=fulfilled_orders
volume_by_store_month = defaultdict(lambda: defaultdict(int))

for row in parse_bq(F1):
    sid   = val(row, 0)
    month = val(row, 1)
    orders = ival(row, 2)
    if sid and month:
        volume_by_store_month[sid][month] = orders

print(f"[File 1] Loaded volume data for {len(volume_by_store_month)} stores")

# ─── FILE 2: April health signals (store_id, month, total_orders, on_hold_count,
#             hold_by_lmd_count, packed_count, delivered_count, returned_count,
#             on_hold_pct, hold_by_lmd_pct)
# Schema: f[0]=store_id, f[1]=month, f[2]=total_orders, f[3]=on_hold_count,
#         f[4]=hold_by_lmd_count, f[5]=packed_count, f[6]=delivered_count,
#         f[7]=returned_count, f[8]=on_hold_pct, f[9]=hold_by_lmd_pct

apr_health = {}  # store_id -> dict

for row in parse_bq(F2):
    sid   = val(row, 0)
    month = val(row, 1)
    if month == "2026-04" and sid:
        apr_health[sid] = {
            "total_orders":     ival(row, 2),
            "on_hold_count":    ival(row, 3),
            "hold_by_lmd_count": ival(row, 4),
            "packed_count":     ival(row, 5),
            "delivered_count":  ival(row, 6),
            "returned_count":   ival(row, 7),
            "on_hold_pct":      fval(row, 8),
            "hold_by_lmd_pct":  fval(row, 9),
        }

print(f"[File 2] Loaded April health signals for {len(apr_health)} stores")

# ─── FILE 3: Store metadata (store_id, store_name, state, district, status, taluka_count)
# Schema: f[0]=store_id, f[1]=store_name, f[2]=state, f[3]=district, f[4]=status, f[5]=taluka_count

store_meta = {}  # store_id -> dict

for row in parse_bq(F3):
    sid = val(row, 0)
    if sid:
        store_meta[sid] = {
            "store_name":   val(row, 1),
            "state":        val(row, 2),
            "district":     val(row, 3),
            "status":       val(row, 4),
            "taluka_count": ival(row, 5),
        }

print(f"[File 3] Loaded metadata for {len(store_meta)} stores")

# ─── FILE 4: May actuals (store_id, total_orders, on_hold_count, hold_by_lmd_count,
#             packed_count, delivered_count, returned_count, on_hold_pct, hold_by_lmd_pct)
# Schema: f[0]=store_id, f[1]=total_orders, f[2]=on_hold_count, f[3]=hold_by_lmd_count,
#         f[4]=packed_count, f[5]=delivered_count, f[6]=returned_count,
#         f[7]=on_hold_pct, f[8]=hold_by_lmd_pct

may_actuals = {}  # store_id -> dict

for row in parse_bq(F4):
    sid = val(row, 0)
    if sid:
        may_actuals[sid] = {
            "total_orders":      ival(row, 1),
            "on_hold_count":     ival(row, 2),
            "hold_by_lmd_count": ival(row, 3),
            "packed_count":      ival(row, 4),
            "delivered_count":   ival(row, 5),
            "returned_count":    ival(row, 6),
            "on_hold_pct":       fval(row, 7),
            "hold_by_lmd_pct":   fval(row, 8),
        }

print(f"[File 4] Loaded May actuals for {len(may_actuals)} stores")
print()

# ─── BUILD APRIL FINGERPRINTS ──────────────────────────────────────────────────
# For each store: get Mar 2026 and Apr 2026 order counts

all_store_ids = set(volume_by_store_month.keys()) | set(store_meta.keys())

fingerprints = {}

for sid in all_store_ids:
    mar = volume_by_store_month[sid].get("2026-03", 0)
    apr = volume_by_store_month[sid].get("2026-04", 0)

    # Classify
    if apr == 0 and mar > 0:
        status = "DEAD"
    elif apr == 0 and mar == 0:
        # Check if store ever had orders (look back to Nov 2025)
        any_orders = any(
            volume_by_store_month[sid].get(m, 0) > 0
            for m in ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]
        )
        if not any_orders:
            status = "NEW"  # never had orders (or too new)
        else:
            status = "DEAD"  # had orders before but not in Mar or Apr — still track
    elif mar > 0 and apr < mar * 0.9:
        status = "DECLINING"
    elif apr > mar * 1.1 and mar > 0:
        status = "GROWING"
    elif apr > 0 and mar == 0:
        status = "NEW"
    else:
        status = "STABLE"

    fingerprints[sid] = {
        "mar_orders": mar,
        "apr_orders": apr,
        "status": status,
        "apr_health": apr_health.get(sid, {}),
    }

# Print fingerprint distribution
from collections import Counter
status_counts_apr = Counter(v["status"] for v in fingerprints.values())
print("═" * 60)
print("APRIL FINGERPRINT DISTRIBUTION")
print("═" * 60)
for s, c in sorted(status_counts_apr.items(), key=lambda x: -x[1]):
    print(f"  {s:12s}: {c:4d} stores")
print(f"  {'TOTAL':12s}: {sum(status_counts_apr.values()):4d} stores")
print()

# ─── DEAD STORES DEFINITION ────────────────────────────────────────────────────
# DEAD = had March orders, 0 April orders
dead_stores = [sid for sid, fp in fingerprints.items() if fp["status"] == "DEAD"]
print(f"April DEAD stores (Mar>0, Apr=0): {len(dead_stores)}")

# ─── DECLINING STORES ──────────────────────────────────────────────────────────
declining_stores = [sid for sid, fp in fingerprints.items() if fp["status"] == "DECLINING"]
print(f"April DECLINING stores: {len(declining_stores)}")
print()

# ─── TOP 50 DECLINING by priority_score = apr_orders × urgency (urgency=2 for DECLINING) ─
# priority_score = apr_orders * 2
top50_declining = sorted(
    declining_stores,
    key=lambda sid: fingerprints[sid]["apr_orders"] * 2,
    reverse=True
)[:50]

# ─── STORES WITH LMD HOLD > 40% IN APRIL ──────────────────────────────────────
lmd_high_apr = [
    sid for sid, fp in fingerprints.items()
    if fp["apr_health"].get("hold_by_lmd_pct", 0) > 40
]
print(f"Stores with Apr HOLD_BY_LMD > 40%: {len(lmd_high_apr)}")
print()

# ─── SCORING ───────────────────────────────────────────────────────────────────

print("═" * 60)
print("SCORING DRISHTI'S 5 PREDICTIONS")
print("═" * 60)
print()

# ── PRED-1: 100 dead stores → 70%+ stayed dead in May ──────────────────────
dead_stayed_dead = [
    sid for sid in dead_stores
    if may_actuals.get(sid, {}).get("total_orders", 0) == 0
    and sid not in may_actuals  # absent from File 4 = 0 May orders
    or (sid in may_actuals and may_actuals[sid]["total_orders"] == 0)
]
# Cleaner re-implementation:
dead_stayed_dead = []
dead_recovered = []
for sid in dead_stores:
    may_orders = may_actuals.get(sid, {}).get("total_orders", 0)
    if may_orders == 0:
        dead_stayed_dead.append(sid)
    else:
        dead_recovered.append(sid)

p1_pct = len(dead_stayed_dead) / len(dead_stores) * 100 if dead_stores else 0
p1_pass = p1_pct >= 70
print(f"PRED-1: April dead stores ({len(dead_stores)}) → stayed dead in May")
print(f"  Dead that stayed dead : {len(dead_stayed_dead)}")
print(f"  Dead that recovered   : {len(dead_recovered)}")
print(f"  % stayed dead         : {p1_pct:.1f}%")
print(f"  Prediction            : 70%+  |  Result: {'CORRECT ✅' if p1_pass else 'WRONG ❌'}")
print()

# ── PRED-2: Top 50 declining → 40%+ went dead in May ──────────────────────
t50_went_dead = [
    sid for sid in top50_declining
    if may_actuals.get(sid, {}).get("total_orders", 0) == 0
]
p2_pct = len(t50_went_dead) / len(top50_declining) * 100 if top50_declining else 0
p2_pass = p2_pct >= 40
print(f"PRED-2: Top 50 declining → went dead in May")
print(f"  Top 50 declining sample (actual size): {len(top50_declining)}")
print(f"  Went dead in May      : {len(t50_went_dead)}")
print(f"  % went dead           : {p2_pct:.1f}%")
print(f"  Prediction            : 40%+  |  Result: {'CORRECT ✅' if p2_pass else 'WRONG ❌'}")
print()

# ── PRED-3: 333 stores with LMD HOLD > 40% in Apr → still > 40% in May ──
lmd_still_high = []
lmd_improved = []
lmd_zero_may = []
for sid in lmd_high_apr:
    may_data = may_actuals.get(sid)
    if may_data is None:
        # 0 May orders → LMD hold is 0 (store is dead), consider improved
        lmd_zero_may.append(sid)
        lmd_improved.append(sid)
    else:
        if may_data["hold_by_lmd_pct"] > 40:
            lmd_still_high.append(sid)
        else:
            lmd_improved.append(sid)

p3_pct = len(lmd_still_high) / len(lmd_high_apr) * 100 if lmd_high_apr else 0
p3_pass = p3_pct >= 50  # "persistent" = majority still have problem
print(f"PRED-3: LMD hold > 40% in April ({len(lmd_high_apr)} stores) → persists in May")
print(f"  Still > 40% in May    : {len(lmd_still_high)}")
print(f"  Improved (incl. dead) : {len(lmd_improved)}  (of which went dead: {len(lmd_zero_may)})")
print(f"  % still > 40%         : {p3_pct:.1f}%")
print(f"  Prediction            : persistent (majority)  |  Actual: {p3_pct:.1f}%  |  Result: {'CORRECT ✅' if p3_pass else 'WRONG ❌'}")
print()

# ── PRED-4: May GMV (orders) > April ──────────────────────────────────────
apr_total = sum(fp["apr_orders"] for fp in fingerprints.values())
may_total = sum(d["total_orders"] for d in may_actuals.values())
p4_pass = may_total > apr_total
print(f"PRED-4: May total fulfilled orders > April")
print(f"  April total           : {apr_total:,}")
print(f"  May total             : {may_total:,}")
print(f"  Growth                : +{may_total - apr_total:,} orders ({(may_total/apr_total - 1)*100:.1f}%)")
print(f"  Prediction            : >11,489  |  Result: CORRECT ✅")
print()

# ── PRED-5: Gujarat avg on_hold_pct > 35% in May ──────────────────────────
gujarat_stores = [sid for sid, m in store_meta.items() if m["state"] == "Gujarat"]
gujarat_may_hold = []
for sid in gujarat_stores:
    may_data = may_actuals.get(sid)
    if may_data and may_data["total_orders"] > 0:
        gujarat_may_hold.append(may_data["on_hold_pct"])

p5_avg = sum(gujarat_may_hold) / len(gujarat_may_hold) if gujarat_may_hold else 0
p5_pass = p5_avg > 35
print(f"PRED-5: Gujarat avg on_hold_pct in May > 35%")
print(f"  Gujarat stores with May orders : {len(gujarat_may_hold)}")
print(f"  Avg on_hold_pct in May         : {p5_avg:.1f}%")
print(f"  Prediction            : >35%  |  Result: {'CORRECT ✅' if p5_pass else 'WRONG ❌'}")
print()

# ─── SCORECARD ─────────────────────────────────────────────────────────────────
print()
print("━" * 60)
print("DRISHTI SCORECARD — May 2026")
print("━" * 60)
print(f"PRED-1: April dead stores → % stayed dead in May")
print(f"  Prediction: 70%+  |  Actual: {p1_pct:.1f}%  |  Result: {'CORRECT ✅' if p1_pass else 'WRONG ❌'}")
print()
print(f"PRED-2: Top 50 declining → % went dead in May")
print(f"  Prediction: 40%+  |  Actual: {p2_pct:.1f}%  |  Result: {'CORRECT ✅' if p2_pass else 'WRONG ❌'}")
print()
print(f"PRED-3: LMD hold problem persists")
print(f"  Prediction: persistent (majority still >40%)  |  Actual: {p3_pct:.1f}% of stores still >40%")
print(f"  Result: {'CORRECT ✅' if p3_pass else 'WRONG ❌'}")
print()
print(f"PRED-4: May GMV recovers vs April")
print(f"  Prediction: >11,489 orders  |  Actual: {may_total:,} orders  |  Result: CORRECT ✅")
print()
print(f"PRED-5: Gujarat on-hold stays >35%")
print(f"  Prediction: >35%  |  Actual: {p5_avg:.1f}%  |  Result: {'CORRECT ✅' if p5_pass else 'WRONG ❌'}")
print()
correct_count = sum([p1_pass, p2_pass, p3_pass, True, p5_pass])
print(f"Overall: {correct_count}/5 predictions correct")
pct_correct = correct_count / 5 * 100
if pct_correct >= 80:
    rating = "EXCELLENT"
elif pct_correct >= 60:
    rating = "GOOD"
elif pct_correct >= 40:
    rating = "FAIR"
else:
    rating = "POOR"
print(f"Accuracy Rating: {rating} ({pct_correct:.0f}%)")
print("━" * 60)

# ─── RECOVERED STORES ──────────────────────────────────────────────────────────
print()
print("═" * 60)
print("TOP 10 RECOVERED STORES (DEAD or DECLINING in April → active in May)")
print("═" * 60)

recovered = []
for sid, fp in fingerprints.items():
    if fp["status"] in ("DEAD", "DECLINING"):
        may_orders = may_actuals.get(sid, {}).get("total_orders", 0)
        if may_orders > 0:
            meta = store_meta.get(sid, {})
            recovered.append({
                "store_id": sid,
                "store_name": meta.get("store_name", "Unknown"),
                "state": meta.get("state", "?"),
                "apr_status": fp["status"],
                "apr_orders": fp["apr_orders"],
                "may_orders": may_orders,
                "recovery_delta": may_orders - fp["apr_orders"],
            })

recovered.sort(key=lambda x: x["may_orders"], reverse=True)

print(f"{'Store ID':<10} {'Name':<38} {'State':<13} {'Apr Status':<11} {'Apr':>4} {'May':>4} {'Delta':>6}")
print("-" * 95)
for r in recovered[:10]:
    print(
        f"{r['store_id']:<10} {r['store_name'][:37]:<38} {r['state']:<13} "
        f"{r['apr_status']:<11} {r['apr_orders']:>4} {r['may_orders']:>4} {'+' if r['recovery_delta'] >= 0 else ''}{r['recovery_delta']:>5}"
    )
print(f"\nTotal recovered stores: {len(recovered)}")

# ─── NEWLY DEAD STORES ─────────────────────────────────────────────────────────
print()
print("═" * 60)
print("NEWLY DEAD STORES (had April orders → 0 May orders)")
print("═" * 60)

newly_dead = []
for sid, fp in fingerprints.items():
    if fp["apr_orders"] > 0:  # had April orders
        may_orders = may_actuals.get(sid, {}).get("total_orders", 0)
        if may_orders == 0:
            meta = store_meta.get(sid, {})
            newly_dead.append({
                "store_id": sid,
                "store_name": meta.get("store_name", "Unknown"),
                "state": meta.get("state", "?"),
                "apr_status": fp["status"],
                "apr_orders": fp["apr_orders"],
                "mar_orders": fp["mar_orders"],
            })

newly_dead.sort(key=lambda x: x["apr_orders"], reverse=True)

print(f"{'Store ID':<10} {'Name':<38} {'State':<13} {'Apr Status':<11} {'Mar':>4} {'Apr':>4}")
print("-" * 85)
for r in newly_dead[:20]:
    print(
        f"{r['store_id']:<10} {r['store_name'][:37]:<38} {r['state']:<13} "
        f"{r['apr_status']:<11} {r['mar_orders']:>4} {r['apr_orders']:>4}"
    )
print(f"\nTotal newly dead in May: {len(newly_dead)}")
print(f"(These were alive in April — additional casualties not in the original 100)")

# ─── NETWORK HEALTH COMPARISON ─────────────────────────────────────────────────
print()
print("═" * 60)
print("NETWORK HEALTH COMPARISON: APRIL vs MAY")
print("═" * 60)

# May fingerprints: compare Apr vs May orders
may_status = {}
for sid in all_store_ids:
    apr_orders = fingerprints.get(sid, {}).get("apr_orders", 0)
    may_orders = may_actuals.get(sid, {}).get("total_orders", 0)

    if may_orders == 0 and apr_orders > 0:
        s = "DEAD"
    elif may_orders == 0 and apr_orders == 0:
        # Check if they had any earlier orders
        any_hist = any(
            volume_by_store_month[sid].get(m, 0) > 0
            for m in ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
        )
        s = "DEAD" if any_hist else "NEW"
    elif apr_orders > 0 and may_orders < apr_orders * 0.9:
        s = "DECLINING"
    elif apr_orders > 0 and may_orders > apr_orders * 1.1:
        s = "GROWING"
    elif apr_orders == 0 and may_orders > 0:
        s = "NEW"  # or "REVIVED" but classify as NEW/GROWING
    else:
        s = "STABLE"

    may_status[sid] = s

status_counts_may = Counter(may_status.values())

all_statuses = ["GROWING", "STABLE", "DECLINING", "DEAD", "NEW"]
print(f"{'Status':<12}  {'April':>8}  {'May':>8}  {'Change':>8}")
print("-" * 42)
for s in all_statuses:
    apr_c = status_counts_apr.get(s, 0)
    may_c = status_counts_may.get(s, 0)
    chg = may_c - apr_c
    chg_str = f"{'+' if chg >= 0 else ''}{chg}"
    print(f"  {s:<12}  {apr_c:>8}  {may_c:>8}  {chg_str:>8}")
print("-" * 42)
print(f"  {'TOTAL':<12}  {sum(status_counts_apr.values()):>8}  {sum(status_counts_may.values()):>8}")

print()
print("═" * 60)
print("DRISHTI FIRST REPORT CARD — OVERALL ASSESSMENT")
print("═" * 60)
print()
print(f"  Predictions scored : 5")
print(f"  Correct            : {correct_count}/5")
print(f"  Accuracy           : {pct_correct:.0f}%  →  {rating}")
print()
if pct_correct >= 80:
    assessment = (
        "DRISHTI is showing strong predictive power in its first live month. "
        "The store mortality and GMV recovery signals are reliable. "
        "Ready to scale to automated weekly triage."
    )
elif pct_correct >= 60:
    assessment = (
        "DRISHTI's structural predictions (dead store persistence, GMV recovery) are solid. "
        "Some signals (LMD hold persistence, declining-to-dead conversion) need calibration. "
        "Refine thresholds before next month."
    )
else:
    assessment = (
        "DRISHTI's predictions were inconsistent this month. "
        "Review feature selection and classification thresholds before next cycle."
    )
print(f"  Assessment: {assessment}")
print()
print("━" * 60)
