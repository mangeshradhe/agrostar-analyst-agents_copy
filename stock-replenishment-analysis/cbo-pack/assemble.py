#!/usr/bin/env python3
"""Assemble cbo-pack/data/*.json into the v2 dashboard's embedded dataset.

v2 replaces the coverage-heatmap/guilty-list/dead-stock view with:
  - cumulative Budget vs Actual Sales vs Procurement, filterable by Product Group + State
  - two drill tables: selling below plan, buying more than selling
  - Clearance (< 150 days to expiry) vs Fresh (>= 150 days) stock split

Run after refreshing extracts (Claude runs the MCP queries on request -- no cron).
Outputs: cbo-pack/dashboard_data.json (embedded into stock_health_dashboard.html by build step)

Key facts baked into this data (repeat wherever shown):
- Budget (ROFO) only has real data for May-Sep 2026 (rolling window); Apr and Oct-Mar are placeholders.
- Sales/Procurement cover Apr-Jul 2026 (elapsed months only; Jul is 13/31 days as of 2026-07-15).
- Product Group = sub_sub_product_group (brand level). Top 100 by FYTD sales volume kept by name,
  everything else folded into 'OTHER'.
- Units are primary everywhere; COGS (Rs) is secondary.
"""
import json, os
from collections import defaultdict

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
def load(name):
    with open(os.path.join(D, name)) as f: return json.load(f)

MONTH_ORDER = {'Apr': 0, 'May': 1, 'Jun': 2, 'Jul': 3, 'Aug': 4, 'Sep': 5}

def rs_cr(units_times_cogs):
    return round(units_times_cogs / 1e7, 2)

out = {'as_of': '2026-07-15', 'fy_start': '2026-04-01'}

# ---------- top100 product-group coverage (for footer note) ----------
top100 = set(load('top100_pg.json'))
out['top100_pg_count'] = len(top100)

# ---------- 1. Budget / Sales / Procurement flat arrays (state x pg x month) ----------
budget = []
for m in ['may', 'jun', 'jul', 'aug', 'sep']:
    for r in load(f'budget_{m}.json'):
        budget.append({'state': r['state'], 'pg': r['pg'], 'month': m.capitalize(), 'qty': int(r['qty'])})

sales = []
for m in ['apr', 'may', 'jun', 'jul']:
    for r in load(f'sales_{m}.json'):
        sales.append({'state': r['state'], 'pg': r['pg'], 'month': m.capitalize(), 'qty': int(r['qty'])})

procurement = []
for m in ['apr', 'may', 'jun', 'jul']:
    for r in load(f'procurement_{m}.json'):
        procurement.append({'state': r['state'], 'pg': r['pg'], 'month': m.capitalize(), 'qty': int(r['qty'])})

out['budget'] = budget
out['sales'] = sales
out['procurement'] = procurement

out['months'] = {
    'budget': ['May', 'Jun', 'Jul', 'Aug', 'Sep'],
    'actual': ['Apr', 'May', 'Jun', 'Jul'],
}

states = sorted(set(r['state'] for r in budget) | set(r['state'] for r in sales) | set(r['state'] for r in procurement))
pgs = sorted(set(r['pg'] for r in budget) | set(r['pg'] for r in sales) | set(r['pg'] for r in procurement))
out['filters'] = {'states': states, 'pgs': pgs}

# ---------- 2. headline tiles ----------
t_budget_fytd = sum(r['qty'] for r in budget)
t_sales_fytd = sum(r['qty'] for r in sales)
t_procurement_fytd = sum(r['qty'] for r in procurement)
out['tiles'] = {
    'budget_may_sep': t_budget_fytd,
    'sales_apr_jul': t_sales_fytd,
    'procurement_apr_jul': t_procurement_fytd,
}

# ---------- 3. Clearance vs Fresh stock ----------
cf = load('onhand_clearance_fresh_state_pg.json')
t_clearance = sum(r['clearance'] for r in cf)
t_fresh = sum(r['fresh'] for r in cf)
t_no_expiry = sum(r['no_expiry'] for r in cf)
t_onhand = t_clearance + t_fresh + t_no_expiry

by_state = defaultdict(lambda: {'clearance': 0, 'fresh': 0, 'no_expiry': 0})
by_pg = defaultdict(lambda: {'clearance': 0, 'fresh': 0, 'no_expiry': 0})
for r in cf:
    for k in ('clearance', 'fresh', 'no_expiry'):
        by_state[r['state']][k] += r[k]
        by_pg[r['pg']][k] += r[k]

out['clearance_fresh'] = {
    'total_onhand': t_onhand,
    'clearance': t_clearance,
    'fresh': t_fresh,
    'no_expiry': t_no_expiry,
    'clearance_pct': round(100 * t_clearance / t_onhand, 1) if t_onhand else 0,
    'by_state': [{'state': s, **v} for s, v in sorted(by_state.items(), key=lambda x: -x[1]['clearance'])],
    'by_pg': sorted(
        [{'pg': p, **v} for p, v in by_pg.items()],
        key=lambda x: -x['clearance']
    )[:30],
}

# top-200 clearance SKUs (already ranked by the extract query)
drill_clearance = load('drill_clearance_skus.json')
for r in drill_clearance:
    r['qty_clearance'] = int(r['qty_clearance'])
    r['value_cr'] = rs_cr(r['qty_clearance'] * r['cogs']) if r.get('cogs') else None
out['drill_clearance'] = drill_clearance
out['drill_clearance_value_cr'] = rs_cr(sum((r['qty_clearance'] * r['cogs']) for r in drill_clearance if r.get('cogs')))

# ---------- 4. drill tables: selling below plan / buying more than selling ----------
under = load('drill_underselling.json')
for r in under:
    r['budget_qty'] = int(r['budget_qty']); r['sales_qty'] = int(r['sales_qty']); r['gap'] = int(r['gap'])
out['drill_underselling'] = under

over = load('drill_overbuying.json')
for r in over:
    r['proc_qty'] = int(r['proc_qty']); r['sales_qty'] = int(r['sales_qty']); r['gap'] = int(r['gap'])
out['drill_overbuying'] = over

with open(os.path.join(os.path.dirname(D), 'dashboard_data.json'), 'w') as f:
    json.dump(out, f)

print('dashboard_data.json written.')
print('tiles:', out['tiles'])
print('states:', len(states), '| product groups:', len(pgs))
print('clearance_fresh:', {k: v for k, v in out['clearance_fresh'].items() if k in ('total_onhand', 'clearance', 'fresh', 'no_expiry', 'clearance_pct')})
print('drill rows -> clearance:', len(out['drill_clearance']), '| underselling:', len(out['drill_underselling']), '| overbuying:', len(out['drill_overbuying']))
