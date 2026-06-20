"""
B2B Credit Health Dashboard Generator
======================================
Fetches live data from BigQuery and generates a self-contained HTML dashboard.

Usage:
    python3 b2b_credit_dashboard.py

Requirements:
    pip install google-cloud-bigquery
    gcloud auth application-default login   # one-time setup

Output:
    b2b_credit_health.html   (open in any browser)

Data source: Panil's Saathi outstanding & collections query
             Fixed to use okr_data_live instead of OKR_RAW_MAPPING (Drive-backed).
"""

import json
import sys
import os
import webbrowser

try:
    from google.cloud import bigquery
except ImportError:
    print("ERROR: google-cloud-bigquery not installed.")
    print("Run: pip install google-cloud-bigquery")
    sys.exit(1)

PROJECT = "agrostar-data"
TODAY   = "20 June 2026"   # update this or auto-derive below


def fetch_data(client):
    """
    Fetches partner-level outstanding + OCP ageing + revenue + payment data.
    One row per active Saathi partner with outstanding balance.

    Key logic mirrors Panil's helth CTE:
      pending_amount = cwt.amount + interest_amount - reconciled_amount
      ageing_days    = DATE_DIFF(current_date, due_date, DAY)
      ageing_days <= 0  → WCP (within credit period)
      ageing_days >  0  → OCP (overdue)
    """
    query = """
WITH
-- Step 1: partial reconciliation amounts per debit
reconciled AS (
  SELECT reconciled_for_id, SUM(amount) AS reconciled_amount
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransactionreconciliation`
  WHERE cancelled = 0
  GROUP BY 1
),

-- Step 2: all unreconciled debits with ageing & category
pending_debits AS (
  SELECT
    csr.farmer_id,
    cwt.reason_id,
    cwt.amount + IFNULL(cwt.interest_amount, 0)
      - IFNULL(r.reconciled_amount, 0) AS pending_amount,
    DATE_DIFF(CURRENT_DATE(), DATE(cwt.due_date), DAY) AS ageing_days,
    opti.category
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt
  LEFT JOIN `agrostar-data.prod_db_views.csr_farmer` csr
    ON csr.user_id = cwt.wallet_user_id
  LEFT JOIN reconciled r
    ON r.reconciled_for_id = cwt.id
  LEFT JOIN `agrostar-data.optimized_reports_data.debit_id_wise_Sales_settlement` opti
    ON opti.id = cwt.id
  WHERE cwt.cancelled = 0
    AND cwt.transaction_type = 0      -- debits only
    AND cwt.reason_id NOT IN (2)      -- exclude adjustments
    AND cwt.is_reconciled = 0         -- outstanding only
),

-- Step 3: aggregate OS + OCP ageing buckets per partner
os_summary AS (
  SELECT
    farmer_id,
    SUM(CASE WHEN pending_amount >= 1 THEN pending_amount ELSE 0 END)                                         AS total_os,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days <= 0 THEN pending_amount ELSE 0 END)                    AS WCP,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days >  0 THEN pending_amount ELSE 0 END)                    AS OCP,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN  1 AND  30 THEN pending_amount ELSE 0 END)      AS OCP_0_30,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 31 AND  60 THEN pending_amount ELSE 0 END)      AS OCP_30_60,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 61 AND  90 THEN pending_amount ELSE 0 END)      AS OCP_60_90,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 91 AND 150 THEN pending_amount ELSE 0 END)      AS OCP_90_150,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 151 AND 180 THEN pending_amount ELSE 0 END)     AS OCP_150_180,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 181 AND 210 THEN pending_amount ELSE 0 END)     AS OCP_180_210,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days BETWEEN 211 AND 240 THEN pending_amount ELSE 0 END)     AS OCP_210_240,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days >  240 THEN pending_amount ELSE 0 END)                  AS OCP_240_plus,
    SUM(CASE WHEN pending_amount >= 1 AND ageing_days >   90 THEN pending_amount ELSE 0 END)                  AS OCP_90_plus,
    -- Seeds (category = 'Seeds')
    SUM(CASE WHEN pending_amount >= 1 AND category = 'Seeds' THEN pending_amount ELSE 0 END)                              AS total_os_seed,
    SUM(CASE WHEN pending_amount >= 1 AND category = 'Seeds' AND ageing_days > 0 THEN pending_amount ELSE 0 END)          AS OCP_seed,
    SUM(CASE WHEN pending_amount >= 1 AND category = 'Seeds' AND ageing_days > 90 THEN pending_amount ELSE 0 END)         AS OCP_90plus_seed,
    -- CPCN (reason_id=3, not Seeds)
    SUM(CASE WHEN pending_amount >= 1 AND reason_id = 3 AND category != 'Seeds' THEN pending_amount ELSE 0 END)           AS total_os_cpcn,
    SUM(CASE WHEN pending_amount >= 1 AND reason_id = 3 AND category != 'Seeds' AND ageing_days > 0 THEN pending_amount ELSE 0 END) AS OCP_cpcn,
    -- Interest (reason_id = 10)
    SUM(CASE WHEN pending_amount >= 1 AND reason_id = 10 THEN pending_amount ELSE 0 END)                                  AS total_os_interest
  FROM pending_debits
  GROUP BY 1
  HAVING SUM(CASE WHEN pending_amount >= 1 THEN pending_amount ELSE 0 END) >= 100
),

-- Step 4: FY-wise PL revenue history
revenue AS (
  SELECT farmer_id,
    ROUND(SUM(CASE WHEN debit_date BETWEEN '2023-04-01' AND '2024-03-31' AND PL_NPL = 'PL' THEN amount END), 0) AS FY24_rev,
    ROUND(SUM(CASE WHEN debit_date BETWEEN '2024-04-01' AND '2025-03-31' AND PL_NPL = 'PL' THEN amount END), 0) AS FY25_rev,
    ROUND(SUM(CASE WHEN debit_date BETWEEN '2025-04-01' AND '2026-03-31' AND PL_NPL = 'PL' THEN amount END), 0) AS FY26_rev,
    COUNT(DISTINCT CASE WHEN debit_date BETWEEN '2025-04-01' AND '2026-03-31' AND PL_NPL = 'PL'
                        THEN product_group END) AS FY26_PG
  FROM `agrostar-data.optimized_reports_data.debit_id_wise_Sales_settlement`
  WHERE reason_id = 3
  GROUP BY 1
),

-- Step 5: last cash payment date per partner (reason_id=4, amount>=1000)
last_payment AS (
  SELECT csr.farmer_id,
    FORMAT_DATE('%Y-%m-%d', MAX(DATE(cwt.created_on))) AS last_paid_date,
    DATE_DIFF(CURRENT_DATE(), MAX(DATE(cwt.created_on)), DAY)  AS days_since_payment
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` cwt
  LEFT JOIN `agrostar-data.prod_db_views.csr_farmer` csr ON csr.user_id = cwt.wallet_user_id
  WHERE cwt.reason_id = 4
    AND cwt.transaction_type = 1   -- credit = payment received
    AND cwt.cancelled = 0
    AND cwt.amount >= 1000
  GROUP BY 1
),

-- Step 6: MPD flag from institution
mpd_data AS (
  SELECT reference_customer_id AS farmer_id, isMpdEnabled
  FROM (
    SELECT reference_customer_id, isMpdEnabled,
      ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) AS rn
    FROM `agrostar-data.galaxy_views.institution`
    WHERE archive = FALSE
      AND LOWER(ancestor_institutions_name) LIKE '%sathi%'
  ) WHERE rn = 1
),

-- Step 7: store location from institution
inst AS (
  SELECT reference_customer_id, address_district AS district,
    address_taluka AS taluka, type_of_locality AS store_type, gst_slabs
  FROM (
    SELECT reference_customer_id, address_district, address_taluka,
      type_of_locality, gst_slabs,
      ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) AS rn
    FROM `agrostar-data.galaxy_views.institution`
    WHERE archive = FALSE
      AND LOWER(ancestor_institutions_name) LIKE '%sathi%'
  ) WHERE rn = 1
)

-- Final: join everything
SELECT
  os.farmer_id,
  COALESCE(okr.name, '')           AS partner_name,
  COALESCE(okr.Territory, '')      AS territory,
  COALESCE(okr.Cluster, '')        AS cluster,
  COALESCE(okr.Business_Unit, '')  AS business_unit,
  COALESCE(okr.state, '')          AS state,
  COALESCE(okr.Revised_State, '')  AS revised_state,
  COALESCE(okr.sh, '')             AS state_head,
  COALESCE(okr.status, '')         AS partner_status,
  COALESCE(inst.district, '')      AS district,
  COALESCE(inst.taluka, '')        AS taluka,
  COALESCE(inst.store_type, '')    AS store_type,
  COALESCE(inst.gst_slabs, '')     AS gst_slabs,
  ROUND(os.total_os, 0)            AS total_os,
  ROUND(os.WCP, 0)                 AS WCP,
  ROUND(os.OCP, 0)                 AS OCP,
  ROUND(os.OCP_0_30, 0)            AS OCP_0_30,
  ROUND(os.OCP_30_60, 0)           AS OCP_30_60,
  ROUND(os.OCP_60_90, 0)           AS OCP_60_90,
  ROUND(os.OCP_90_150, 0)          AS OCP_90_150,
  ROUND(os.OCP_150_180, 0)         AS OCP_150_180,
  ROUND(os.OCP_180_210, 0)         AS OCP_180_210,
  ROUND(os.OCP_210_240, 0)         AS OCP_210_240,
  ROUND(os.OCP_240_plus, 0)        AS OCP_240_plus,
  ROUND(os.OCP_90_plus, 0)         AS OCP_90_plus,
  ROUND(os.total_os_seed, 0)       AS total_os_seed,
  ROUND(os.OCP_seed, 0)            AS OCP_seed,
  ROUND(os.OCP_90plus_seed, 0)     AS OCP_90plus_seed,
  ROUND(os.total_os_cpcn, 0)       AS total_os_cpcn,
  ROUND(os.OCP_cpcn, 0)            AS OCP_cpcn,
  ROUND(os.total_os_interest, 0)   AS total_os_interest,
  COALESCE(rev.FY24_rev, 0)        AS FY24_rev,
  COALESCE(rev.FY25_rev, 0)        AS FY25_rev,
  COALESCE(rev.FY26_rev, 0)        AS FY26_rev,
  COALESCE(rev.FY26_PG, 0)         AS FY26_PG,
  COALESCE(lp.last_paid_date, '')  AS last_paid_date,
  COALESCE(lp.days_since_payment, 999) AS days_since_payment,
  COALESCE(mpd.isMpdEnabled, FALSE) AS mpd_enabled,
  CASE
    WHEN os.OCP < 5000 AND os.OCP_90_plus < 1000          THEN 'Open for sale'
    WHEN mpd.isMpdEnabled = TRUE                           THEN 'OCP Collect MPD'
    WHEN os.OCP >= 5000 OR os.OCP_90_plus > 1000          THEN 'OCP Blocked'
    ELSE 'Open for sale'
  END AS bill_flag
FROM os_summary os
LEFT JOIN `agrostar-data.offline_team.okr_data_live` okr
  ON okr.farmer_id = os.farmer_id
LEFT JOIN inst
  ON inst.reference_customer_id = os.farmer_id
LEFT JOIN revenue rev
  ON rev.farmer_id = os.farmer_id
LEFT JOIN last_payment lp
  ON lp.farmer_id = os.farmer_id
LEFT JOIN mpd_data mpd
  ON mpd.farmer_id = os.farmer_id
WHERE okr.status IS NOT NULL
ORDER BY os.OCP DESC
"""
    print("Running BigQuery query...")
    job = client.query(query)
    print(f"Job ID: {job.job_id}")
    rows = list(job.result())
    print(f"Fetched {len(rows)} partner rows")
    return rows


def compute_kpis(rows, cols):
    """Compute hero KPIs from partner rows."""
    ci = {c: i for i, c in enumerate(cols)}
    total_os  = sum(r[ci['total_os']] or 0 for r in rows)
    total_ocp = sum(r[ci['OCP']] or 0 for r in rows)
    ocp_240   = sum(r[ci['OCP_240_plus']] or 0 for r in rows)
    blocked   = sum(1 for r in rows if r[ci['bill_flag']] == 'OCP Blocked')
    total_p   = len(rows)

    def cr(v): return f"₹{v/1e7:.1f} Cr"
    return {
        "total_os":  cr(total_os),
        "total_ocp": cr(total_ocp),
        "ocp_240":   cr(ocp_240),
        "blocked":   f"{blocked:,}",
        "total":     f"{total_p:,}",
        "blocked_pct": f"{blocked/total_p*100:.0f}%",
    }


def compute_ageing(rows, cols):
    """Compute ageing waterfall from live data."""
    ci = {c: i for i, c in enumerate(cols)}
    buckets = [
        ("Within Term",  "WCP",        "#52B788"),
        ("0–30 d OCP",   "OCP_0_30",   "#7DC47A"),
        ("30–60 d",      "OCP_30_60",  "#E8A02A"),
        ("60–90 d",      "OCP_60_90",  "#D4811A"),
        ("90–150 d",     "OCP_90_150", "#E05C20"),
        ("150–180 d",    "OCP_150_180","#E04030"),
        ("180–240 d",    "OCP_180_210","#E5383B"),  # 180-210 + 210-240 combined
        ("240+ d",       "OCP_240_plus","#C0141A"),
    ]
    result = []
    for label, col, color in buckets:
        val = sum(r[ci[col]] or 0 for r in rows) / 1e7  # in Cr
        if col == "OCP_180_210":
            val += sum(r[ci["OCP_210_240"]] or 0 for r in rows) / 1e7
        result.append({"label": label, "val": round(val, 2), "color": color})
    return result


def compute_states(rows, cols):
    """Aggregate OS + OCP by state."""
    ci = {c: i for i, c in enumerate(cols)}
    agg = {}
    for r in rows:
        s = r[ci['state']] or 'OTHER'
        if s not in agg:
            agg[s] = {"os": 0, "ocp": 0}
        agg[s]["os"]  += r[ci['total_os']] or 0
        agg[s]["ocp"] += r[ci['OCP']] or 0
    result = [{"label": k, "os": round(v["os"]/1e7, 2), "ocp": round(v["ocp"]/1e7, 2)}
              for k, v in agg.items() if v["os"] > 1e6]
    result.sort(key=lambda x: -x["os"])
    return result[:12]


def compute_top15(rows, cols):
    """Top 15 partners by OCP from live data."""
    ci = {c: i for i, c in enumerate(cols)}
    sorted_rows = sorted(rows, key=lambda r: -(r[ci['OCP']] or 0))[:15]
    result = []
    for r in sorted_rows:
        ocp     = r[ci['OCP']] or 0
        ocp90   = r[ci['OCP_90_plus']] or 0
        total   = r[ci['total_os']] or 0
        result.append({
            "n":    r[ci['partner_name']],
            "st":   r[ci['state']],
            "t":    r[ci['territory']],
            "os":   round(total / 100000, 1),
            "ocp":  round(ocp / 100000, 1),
            "is90": ocp90 > ocp * 0.8,
        })
    return result


def rows_to_payload(rows, cols):
    """Convert BQ rows to array-of-arrays JSON payload."""
    str_cols = {'partner_name','territory','cluster','business_unit','state',
                'revised_state','state_head','partner_status','district',
                'taluka','store_type','gst_slabs','last_paid_date','bill_flag'}
    data = []
    for row in rows:
        r = list(row)
        for i, (c, v) in enumerate(zip(cols, r)):
            if v is None:
                r[i] = '' if c in str_cols else 0
            elif isinstance(v, bool):
                r[i] = v
            elif isinstance(v, float):
                r[i] = int(v) if v == int(v) else round(v, 2)
        data.append(r)
    return json.dumps({"cols": cols, "rows": data}, separators=(',', ':'), default=str)


def build_html(raw_json, kpis, ageing_data, state_data, top15, today):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>B2B Credit Health — Agrostar</title>
<style>
:root {{
  --ground:#0E1C2E;--surface:#152840;--surface-2:#1C3454;
  --text:#E8F0F5;--muted:#7A9BB5;--accent:#F5A623;
  --danger:#E5383B;--safe:#52B788;--warn:#E8A02A;
  --border:rgba(122,155,181,.12);--border-bright:rgba(122,155,181,.25);
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{background:var(--ground);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.6;min-height:100vh}}
nav{{display:flex;justify-content:space-between;align-items:center;padding:14px 40px;border-bottom:1px solid var(--border);position:sticky;top:0;background:rgba(14,28,46,.95);backdrop-filter:blur(8px);z-index:100}}
.nav-brand{{display:flex;align-items:center;gap:10px}}
.nav-logo{{width:28px;height:28px;background:var(--accent);border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#0E1C2E}}
.nav-title{{font-size:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}}
.nav-title span{{color:var(--text)}}
.nav-date{{font-size:12px;color:var(--muted)}}
.live-dot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--safe);margin-right:6px;animation:pulse 2s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
.hero{{padding:56px 40px 48px;border-bottom:1px solid var(--border);position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;top:-80px;right:-80px;width:400px;height:400px;background:radial-gradient(circle,rgba(229,56,59,.08) 0%,transparent 65%);pointer-events:none}}
.hero-eyebrow{{font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:16px}}
.hero-headline{{font-size:clamp(36px,5vw,62px);font-weight:800;line-height:1.05;letter-spacing:-.03em;color:var(--danger);margin-bottom:12px;font-family:Georgia,'Times New Roman',serif}}
.hero-headline em{{font-style:normal;color:var(--text)}}
.hero-sub{{font-size:16px;color:var(--muted);max-width:560px;margin-bottom:36px;line-height:1.5}}
.hero-sub strong{{color:var(--text)}}
.hero-stats{{display:flex;gap:0;flex-wrap:wrap}}
.hero-stat{{padding:16px 28px 16px 0;margin-right:28px;border-right:1px solid var(--border-bright)}}
.hero-stat:last-child{{border-right:none}}
.hero-stat-val{{font-size:28px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1;margin-bottom:4px}}
.hero-stat-val.danger{{color:var(--danger)}}.hero-stat-val.warn{{color:var(--warn)}}.hero-stat-val.muted{{color:var(--text)}}
.hero-stat-label{{font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
.grid-2{{display:grid;grid-template-columns:1fr 1.6fr;gap:0;border-bottom:1px solid var(--border)}}
.grid-2>*{{padding:36px 40px}}
.grid-2>*:first-child{{border-right:1px solid var(--border)}}
.section{{padding:36px 40px;border-bottom:1px solid var(--border)}}
.section-header{{display:flex;align-items:baseline;gap:12px;margin-bottom:28px;flex-wrap:wrap}}
.section-title{{font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}}
.section-note{{font-size:11px;color:rgba(122,155,181,.5)}}
.status-bar{{display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px;margin-bottom:16px}}
.status-seg{{border-radius:3px}}
.status-legend{{display:flex;flex-direction:column;gap:10px}}
.status-row{{display:flex;align-items:center;justify-content:space-between}}
.status-row-left{{display:flex;align-items:center;gap:8px}}
.status-dot{{width:8px;height:8px;border-radius:2px;flex-shrink:0}}
.status-name{{font-size:13px;color:var(--text)}}
.status-count{{font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--text)}}
.status-amt{{font-size:11px;color:var(--muted)}}
.ageing-chart{{display:flex;flex-direction:column;gap:10px}}
.age-row{{display:grid;grid-template-columns:90px 1fr 56px;align-items:center;gap:12px}}
.age-label{{font-size:11px;font-weight:500;color:var(--muted);text-align:right;white-space:nowrap}}
.age-bar-track{{height:8px;background:rgba(122,155,181,.08);border-radius:4px;overflow:hidden}}
.age-bar-fill{{height:100%;border-radius:4px;transition:width .8s cubic-bezier(.16,1,.3,1)}}
.age-val{{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--text);text-align:right}}
.state-chart{{display:flex;flex-direction:column;gap:9px}}
.state-row{{display:grid;grid-template-columns:100px 1fr 70px;align-items:center;gap:12px}}
.state-label{{font-size:12px;font-weight:600;color:var(--text);text-align:right}}
.state-bars{{display:flex;flex-direction:column;gap:3px}}
.state-bar-track{{height:6px;background:rgba(122,155,181,.08);border-radius:3px;overflow:hidden}}
.state-bar-fill{{height:100%;border-radius:3px}}
.state-vals{{text-align:right}}
.state-os-val{{font-size:12px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}}
.state-ocp-val{{font-size:10px;color:var(--danger);font-variant-numeric:tabular-nums}}
.risk-table{{width:100%;border-collapse:collapse}}
.risk-table th{{font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:0 12px 12px 0;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}}
.risk-table th:last-child{{text-align:right;padding-right:0}}
.risk-table td{{padding:11px 12px 11px 0;border-bottom:1px solid var(--border);vertical-align:middle}}
.risk-table tr:last-child td{{border-bottom:none}}
.risk-table td:last-child{{text-align:right;padding-right:0}}
.partner-name{{font-size:13px;font-weight:600;color:var(--text)}}
.tag{{display:inline-block;font-size:10px;font-weight:600;letter-spacing:.06em;padding:2px 7px;border-radius:3px;text-transform:uppercase}}
.tag-state{{background:rgba(122,155,181,.12);color:var(--muted)}}
.ocp-bar-wrap{{display:flex;align-items:center;gap:8px;justify-content:flex-end}}
.ocp-pill{{font-size:12px;font-weight:700;color:var(--danger);font-variant-numeric:tabular-nums;white-space:nowrap}}
.ocp-mini-bar{{width:60px;height:4px;background:rgba(229,56,59,.15);border-radius:2px;overflow:hidden}}
.ocp-mini-fill{{height:100%;background:var(--danger);border-radius:2px}}
.ocp-90-badge{{font-size:10px;font-weight:600;color:var(--danger);background:rgba(229,56,59,.12);padding:2px 6px;border-radius:3px}}
/* Partner table */
.pt-controls{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:20px}}
.pt-search{{flex:1;min-width:200px;background:var(--surface-2);border:1px solid var(--border-bright);color:var(--text);font-size:13px;padding:8px 12px;border-radius:6px;outline:none}}
.pt-search::placeholder{{color:var(--muted)}}
.pt-search:focus{{border-color:var(--accent)}}
.pt-select{{background:var(--surface-2);border:1px solid var(--border-bright);color:var(--text);font-size:12px;padding:8px 10px;border-radius:6px;outline:none;cursor:pointer}}
.pt-select option{{background:var(--surface)}}
.pt-groups{{display:flex;gap:6px;flex-wrap:wrap}}
.pt-group-btn{{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding:5px 10px;border-radius:4px;border:1px solid var(--border-bright);background:transparent;color:var(--muted);cursor:pointer;transition:all .15s}}
.pt-group-btn.active{{background:var(--surface-2);color:var(--text);border-color:var(--muted)}}
.pt-info{{font-size:11px;color:var(--muted);margin-left:auto;white-space:nowrap;align-self:center}}
.pt-wrap{{overflow-x:auto}}
.pt-table{{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}}
.pt-table th{{position:sticky;top:0;background:var(--surface);border-bottom:1px solid var(--border-bright);padding:8px 10px;text-align:right;white-space:nowrap;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);cursor:pointer;user-select:none}}
.pt-table th:first-child,.pt-table th:nth-child(2){{text-align:left}}
.pt-table th.sorted-asc::after{{content:' ↑';color:var(--accent)}}
.pt-table th.sorted-desc::after{{content:' ↓';color:var(--accent)}}
.pt-table td{{padding:8px 10px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}}
.pt-table td:first-child,.pt-table td:nth-child(2){{text-align:left}}
.pt-table tr:last-child td{{border-bottom:none}}
.pt-table tr:hover td{{background:rgba(122,155,181,.04)}}
.cell-name{{font-weight:600;color:var(--text);max-width:200px;overflow:hidden;text-overflow:ellipsis}}
.bf-tag{{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:3px;white-space:nowrap}}
.bf-open{{background:rgba(82,183,136,.15);color:var(--safe)}}
.bf-blocked{{background:rgba(229,56,59,.15);color:var(--danger)}}
.bf-mpd{{background:rgba(232,160,42,.15);color:var(--warn)}}
.bf-unlocked{{background:rgba(245,166,35,.15);color:var(--accent)}}
.ocp-cell{{color:var(--danger);font-weight:600}}
.wcp-cell{{color:var(--safe)}}
.zero-cell{{color:rgba(122,155,181,.3)}}
.pt-pagination{{display:flex;align-items:center;gap:8px;margin-top:16px;justify-content:center}}
.pt-page-btn{{background:var(--surface-2);border:1px solid var(--border-bright);color:var(--text);font-size:12px;padding:5px 12px;border-radius:4px;cursor:pointer}}
.pt-page-btn:disabled{{opacity:.3;cursor:default}}
.pt-page-info{{font-size:12px;color:var(--muted)}}
footer{{padding:20px 40px;display:flex;justify-content:space-between;align-items:center}}
.footer-note{{font-size:11px;color:rgba(122,155,181,.4)}}
@media(max-width:768px){{
  nav,.hero,.section{{padding-left:20px;padding-right:20px}}
  .grid-2{{grid-template-columns:1fr}}
  .grid-2>*:first-child{{border-right:none;border-bottom:1px solid var(--border)}}
  .grid-2>*{{padding:24px 20px}}
  .hero-stats{{flex-direction:column}}
  .hero-stat{{border-right:none;margin-right:0;padding:10px 0;border-bottom:1px solid var(--border)}}
}}
</style>
</head>
<body>
<nav>
  <div class="nav-brand">
    <div class="nav-logo">AS</div>
    <div class="nav-title"><span>Agrostar</span> · B2B Credit Health</div>
  </div>
  <div class="nav-date"><span class="live-dot"></span>As of {today}</div>
</nav>

<section class="hero">
  <div class="hero-eyebrow">Executive Dashboard · Collections &amp; Credit Risk</div>
  <div class="hero-headline">{kpis['total_ocp']} <em>is overdue.</em></div>
  <div class="hero-sub">
    <strong>{kpis['blocked']} of {kpis['total']} Saathi partners</strong> — {kpis['blocked_pct']} of the network — are blocked from placing orders today due to outstanding credit.
  </div>
  <div class="hero-stats">
    <div class="hero-stat"><div class="hero-stat-val muted">{kpis['total_os']}</div><div class="hero-stat-label">Total Outstanding</div></div>
    <div class="hero-stat"><div class="hero-stat-val danger">{kpis['total_ocp']}</div><div class="hero-stat-label">Overdue (OCP)</div></div>
    <div class="hero-stat"><div class="hero-stat-val warn">{kpis['blocked']}</div><div class="hero-stat-label">Partners Blocked</div></div>
    <div class="hero-stat"><div class="hero-stat-val danger">{kpis['ocp_240']}</div><div class="hero-stat-label">240+ Days Overdue</div></div>
  </div>
</section>

<div class="grid-2">
  <div>
    <div class="section-header">
      <span class="section-title">Partner Billing Status</span>
      <span class="section-note">{kpis['total']} partners</span>
    </div>
    <div class="status-bar" id="status-bar-render"></div>
    <div class="status-legend" id="status-legend-render"></div>
  </div>
  <div>
    <div class="section-header">
      <span class="section-title">Outstanding by Ageing Bucket</span>
      <span class="section-note">{kpis['total_os']} total</span>
    </div>
    <div class="ageing-chart" id="ageing-chart"></div>
  </div>
</div>

<section class="section">
  <div class="section-header">
    <span class="section-title">All Partner Details</span>
    <span class="section-note" id="pt-count-label">{kpis['total']} partners · sorted by OCP ↓</span>
  </div>
  <div class="pt-controls">
    <input class="pt-search" id="pt-search" type="text" placeholder="Search name, farmer ID, territory, district, taluka…">
    <select class="pt-select" id="filter-state"><option value="">All States</option></select>
    <select class="pt-select" id="filter-bu"><option value="">All BUs</option></select>
    <select class="pt-select" id="filter-territory"><option value="">All Territories</option></select>
    <select class="pt-select" id="filter-flag"><option value="">All Statuses</option></select>
    <button class="pt-group-btn" id="btn-clear" onclick="clearFilters()" style="color:var(--warn);border-color:var(--warn)">✕ Clear</button>
    <div class="pt-groups">
      <button class="pt-group-btn active" data-group="core">Core</button>
      <button class="pt-group-btn active" data-group="ageing">OCP Ageing</button>
      <button class="pt-group-btn" data-group="category">Seeds / CPCN</button>
      <button class="pt-group-btn" data-group="revenue">Revenue</button>
      <button class="pt-group-btn" data-group="info">Info</button>
    </div>
    <div class="pt-info" id="pt-showing">Showing 0–0 of 0</div>
  </div>
  <div class="pt-wrap">
    <table class="pt-table" id="pt-table">
      <thead id="pt-thead"></thead>
      <tbody id="pt-tbody"></tbody>
    </table>
  </div>
  <div class="pt-pagination">
    <button class="pt-page-btn" id="pt-prev" onclick="ptPage(-1)">← Prev</button>
    <span class="pt-page-info" id="pt-page-info">Page 1</span>
    <button class="pt-page-btn" id="pt-next" onclick="ptPage(1)">Next →</button>
  </div>
</section>

<section class="section">
  <div class="section-header">
    <span class="section-title">State-wise Credit Exposure</span>
    <span class="section-note">Outstanding (grey) · OCP (red) — in Cr</span>
  </div>
  <div class="state-chart" id="state-chart"></div>
</section>

<section class="section">
  <div class="section-header">
    <span class="section-title">Top 15 Partners by OCP</span>
    <span class="section-note">100% OCP ratio = all outstanding is overdue</span>
  </div>
  <div style="overflow-x:auto">
  <table class="risk-table">
    <thead><tr>
      <th style="width:36px">#</th><th>Partner</th><th>State · Territory</th>
      <th style="text-align:right">Total OS</th>
      <th style="text-align:right">OCP Amount · 90d+ Share</th>
    </tr></thead>
    <tbody id="top15-tbody"></tbody>
  </table>
  </div>
</section>

<footer>
  <div class="footer-note">Source: wallet_creditwallettransaction · okr_data_live · galaxy_views · Agrostar BigQuery</div>
  <div class="footer-note">Outstanding = unreconciled debits ≥ ₹100 &amp; partner status not null · Generated {today}</div>
</footer>

<script>
const RAW    = {raw_json};
const AGEING = {json.dumps(ageing_data)};
const STATES = {json.dumps(state_data)};
const TOP15  = {json.dumps(top15)};

function fmt(v){{
  if(!v||v===0) return '—';
  const L=Math.abs(v)/100000;
  return(v<0?'-':'')+'₹'+(L>=100?(L/100).toFixed(1)+'Cr':L.toFixed(1)+'L');
}}

// Ageing chart
const maxAge=Math.max(...AGEING.map(d=>d.val));
const ageEl=document.getElementById('ageing-chart');
AGEING.forEach(d=>{{
  const pct=(d.val/maxAge*100).toFixed(1);
  ageEl.innerHTML+=`<div class="age-row">
    <div class="age-label">${{d.label}}</div>
    <div class="age-bar-track"><div class="age-bar-fill" style="width:0%;background:${{d.color}}" data-pct="${{pct}}"></div></div>
    <div class="age-val">₹${{d.val.toFixed(1)}} Cr</div>
  </div>`;
}});

// Status bar (computed from partner data)
const COLS=RAW.cols; const CI={{}};
COLS.forEach((c,i)=>CI[c]=i);
function countFlag(f){{return RAW.rows.filter(r=>r[CI['bill_flag']]===f).length}}
function sumOS(f){{return RAW.rows.filter(r=>!f||r[CI['bill_flag']]===f).reduce((s,r)=>s+(r[CI['total_os']]||0),0)}}
const total=RAW.rows.length;
const nBlocked=countFlag('OCP Blocked'), nMPD=countFlag('OCP Collect MPD'), nOpen=countFlag('Open for sale');
const osBlocked=sumOS('OCP Blocked'), osMPD=sumOS('OCP Collect MPD'), osOpen=sumOS('Open for sale');
const pBlocked=(nBlocked/total*100).toFixed(1), pMPD=(nMPD/total*100).toFixed(1), pOpen=(nOpen/total*100).toFixed(1);
document.getElementById('status-bar-render').innerHTML=`
  <div class="status-seg" style="background:var(--danger);width:${{pBlocked}}%"></div>
  <div class="status-seg" style="background:var(--warn);width:${{pMPD}}%"></div>
  <div class="status-seg" style="background:var(--safe);width:${{pOpen}}%"></div>`;
document.getElementById('status-legend-render').innerHTML=`
  <div class="status-row"><div class="status-row-left"><div class="status-dot" style="background:var(--danger)"></div><span class="status-name">OCP Blocked</span></div><div><div class="status-count">${{nBlocked.toLocaleString()}} partners</div><div class="status-amt">${{fmt(osBlocked)}} outstanding</div></div></div>
  <div class="status-row"><div class="status-row-left"><div class="status-dot" style="background:var(--warn)"></div><span class="status-name">OCP — Collect MPD</span></div><div><div class="status-count">${{nMPD.toLocaleString()}} partners</div><div class="status-amt">${{fmt(osMPD)}} outstanding</div></div></div>
  <div class="status-row"><div class="status-row-left"><div class="status-dot" style="background:var(--safe)"></div><span class="status-name">Open for Sale</span></div><div><div class="status-count">${{nOpen.toLocaleString()}} partners</div><div class="status-amt">${{fmt(osOpen)}} outstanding</div></div></div>`;

// State chart
const maxOS=Math.max(...STATES.map(d=>d.os));
const stateEl=document.getElementById('state-chart');
STATES.forEach(d=>{{
  const osPct=(d.os/maxOS*100).toFixed(1),ocpPct=(d.ocp/maxOS*100).toFixed(1);
  const ratio=((d.ocp/d.os)*100).toFixed(0);
  stateEl.innerHTML+=`<div class="state-row">
    <div class="state-label">${{d.label}}</div>
    <div class="state-bars">
      <div class="state-bar-track"><div class="state-bar-fill" style="width:${{osPct}}%;background:rgba(122,155,181,.4)" data-pct="${{osPct}}"></div></div>
      <div class="state-bar-track"><div class="state-bar-fill" style="width:${{ocpPct}}%;background:var(--danger)" data-pct="${{ocpPct}}"></div></div>
    </div>
    <div class="state-vals"><div class="state-os-val">₹${{d.os.toFixed(1)}} Cr</div><div class="state-ocp-val">₹${{d.ocp.toFixed(1)}} Cr (${{ratio}}%)</div></div>
  </div>`;
}});

// Top 15
const maxOCP=Math.max(...TOP15.map(d=>d.ocp));
const t15body=document.getElementById('top15-tbody');
TOP15.forEach((p,i)=>{{
  const barW=(p.ocp/maxOCP*100).toFixed(0);
  t15body.innerHTML+=`<tr>
    <td style="color:var(--muted);font-size:11px">${{String(i+1).padStart(2,'0')}}</td>
    <td><div class="partner-name">${{p.n}}</div></td>
    <td><span class="tag tag-state">${{p.st}}</span><span style="color:var(--muted);font-size:11px;margin-left:6px">${{p.t}}</span></td>
    <td style="text-align:right;font-weight:600;font-size:13px">₹${{p.os.toFixed(1)}}L</td>
    <td><div class="ocp-bar-wrap">
      ${{p.is90?'<span class="ocp-90-badge">90d+</span>':''}}
      <div class="ocp-mini-bar"><div class="ocp-mini-fill" style="width:${{barW}}%"></div></div>
      <span class="ocp-pill">₹${{p.ocp.toFixed(1)}}L</span>
    </div></td>
  </tr>`;
}});

// Partner table
const GROUPS={{
  core:    ['partner_name','territory','state','bill_flag','total_os','OCP','OCP_90_plus','last_paid_date','days_since_payment'],
  ageing:  ['OCP_0_30','OCP_30_60','OCP_60_90','OCP_90_150','OCP_150_180','OCP_180_210','OCP_210_240','OCP_240_plus'],
  category:['total_os_seed','OCP_seed','OCP_90plus_seed','total_os_cpcn','OCP_cpcn','total_os_interest'],
  revenue: ['FY24_rev','FY25_rev','FY26_rev','FY26_PG'],
  info:    ['farmer_id','cluster','business_unit','district','taluka','store_type','gst_slabs','mpd_enabled','state_head','partner_status'],
}};
const COL_LABELS={{
  partner_name:'Partner',territory:'Territory',state:'State',bill_flag:'Status',
  total_os:'Total OS',OCP:'OCP',OCP_90_plus:'90d+ OCP',last_paid_date:'Last Payment',
  days_since_payment:'Days Since Paid',OCP_0_30:'0–30d',OCP_30_60:'30–60d',
  OCP_60_90:'60–90d',OCP_90_150:'90–150d',OCP_150_180:'150–180d',OCP_180_210:'180–210d',
  OCP_210_240:'210–240d',OCP_240_plus:'240d+',total_os_seed:'OS Seeds',OCP_seed:'OCP Seeds',
  OCP_90plus_seed:'Seeds 90d+',total_os_cpcn:'OS CPCN',OCP_cpcn:'OCP CPCN',
  total_os_interest:'OS Interest',FY24_rev:'FY24 Rev',FY25_rev:'FY25 Rev',FY26_rev:'FY26 Rev',
  FY26_PG:'FY26 PGs',farmer_id:'Farmer ID',cluster:'Cluster',business_unit:'BU',
  district:'District',taluka:'Taluka',store_type:'Store Type',gst_slabs:'GST Slab',
  mpd_enabled:'MPD',state_head:'State Head',partner_status:'Status',
}};
const MONEY_COLS=new Set(['total_os','WCP','OCP','OCP_0_30','OCP_30_60','OCP_60_90','OCP_90_150','OCP_150_180','OCP_180_210','OCP_210_240','OCP_240_plus','OCP_90_plus','total_os_seed','OCP_seed','OCP_90plus_seed','total_os_cpcn','OCP_cpcn','total_os_interest','FY24_rev','FY25_rev','FY26_rev']);
const OCP_COLS=new Set(['OCP','OCP_0_30','OCP_30_60','OCP_60_90','OCP_90_150','OCP_150_180','OCP_180_210','OCP_210_240','OCP_240_plus','OCP_90_plus','OCP_seed','OCP_90plus_seed','OCP_cpcn']);
let activeGroups=new Set(['core','ageing']),sortCol='OCP',sortDir=-1,currentPage=0;
const PAGE_SIZE=50;
let filtered=[];
function getVisCols(){{const seen=new Set(),out=[];for(const g of['core','ageing','category','revenue','info']){{if(!activeGroups.has(g))continue;for(const c of GROUPS[g]){{if(!seen.has(c)&&CI[c]!==undefined){{seen.add(c);out.push(c)}}}}}}return out}}
function bfClass(f){{if(!f)return 'bf-open';if(f.includes('Blocked'))return 'bf-blocked';if(f.includes('MPD'))return 'bf-mpd';if(f.includes('unlocked'))return 'bf-unlocked';return 'bf-open'}}
function cellVal(col,val){{
  if(col==='bill_flag')return `<span class="bf-tag ${{bfClass(val)}}">${{val||'—'}}</span>`;
  if(col==='mpd_enabled')return val?'✓':'—';
  if(col==='FY26_PG')return val||'—';
  if(col==='days_since_payment')return val>=999?'—':val+'d';
  if(MONEY_COLS.has(col)){{
    if(!val||val===0)return '<span class="zero-cell">—</span>';
    const cls=OCP_COLS.has(col)?' class="ocp-cell"':col==='WCP'?' class="wcp-cell"':'';
    return `<span${{cls}}>${{fmt(val)}}</span>`;
  }}
  if(col==='state_head'){{const name=(val||'').split('@')[0].replace(/\./g,' ');return `<span style="color:var(--muted);font-size:10px">${{name||'—'}}</span>`;}}
  return val||'<span class="zero-cell">—</span>';
}}
function buildFilters(){{
  const states=[...new Set(RAW.rows.map(r=>r[CI['state']]).filter(Boolean))].sort();
  const bus=[...new Set(RAW.rows.map(r=>r[CI['business_unit']]).filter(Boolean))].sort();
  const territories=[...new Set(RAW.rows.map(r=>r[CI['territory']]).filter(Boolean))].sort();
  const flags=[...new Set(RAW.rows.map(r=>r[CI['bill_flag']]).filter(Boolean))].sort();
  const selS=document.getElementById('filter-state');
  const selB=document.getElementById('filter-bu');
  const selT=document.getElementById('filter-territory');
  const selF=document.getElementById('filter-flag');
  states.forEach(s=>{{const o=document.createElement('option');o.value=o.textContent=s;selS.appendChild(o)}});
  bus.forEach(b=>{{const o=document.createElement('option');o.value=o.textContent=b;selB.appendChild(o)}});
  territories.forEach(t=>{{const o=document.createElement('option');o.value=o.textContent=t;selT.appendChild(o)}});
  flags.forEach(f=>{{const o=document.createElement('option');o.value=o.textContent=f;selF.appendChild(o)}});
  // When state changes, re-populate territory options to match
  selS.addEventListener('change',()=>{{
    const st=selS.value;
    selT.innerHTML='<option value="">All Territories</option>';
    const terrs=[...new Set(RAW.rows.filter(r=>!st||r[CI['state']]===st).map(r=>r[CI['territory']]).filter(Boolean))].sort();
    terrs.forEach(t=>{{const o=document.createElement('option');o.value=o.textContent=t;selT.appendChild(o)}});
    applyFilters();
  }});
}}
function applyFilters(){{
  const q=document.getElementById('pt-search').value.toLowerCase().trim();
  const st=document.getElementById('filter-state').value;
  const bu=document.getElementById('filter-bu').value;
  const te=document.getElementById('filter-territory').value;
  const fl=document.getElementById('filter-flag').value;
  filtered=RAW.rows.filter(r=>{{
    if(st&&r[CI['state']]!==st)return false;
    if(bu&&r[CI['business_unit']]!==bu)return false;
    if(te&&r[CI['territory']]!==te)return false;
    if(fl&&r[CI['bill_flag']]!==fl)return false;
    if(q){{
      const fid=String(r[CI['farmer_id']]||'');
      const name=(r[CI['partner_name']]||'').toLowerCase();
      const terr=(r[CI['territory']]||'').toLowerCase();
      const dist=(r[CI['district']]||'').toLowerCase();
      const taluka=(r[CI['taluka']]||'').toLowerCase();
      const clus=(r[CI['cluster']]||'').toLowerCase();
      const state=(r[CI['state']]||'').toLowerCase();
      const bu2=(r[CI['business_unit']]||'').toLowerCase();
      if(!fid.includes(q)&&!name.includes(q)&&!terr.includes(q)&&!dist.includes(q)&&!taluka.includes(q)&&!clus.includes(q)&&!state.includes(q)&&!bu2.includes(q))return false;
    }}
    return true;
  }});
  const si=CI[sortCol];
  filtered.sort((a,b)=>{{const av=a[si],bv=b[si];if(typeof av==='number')return sortDir*(bv-av);return sortDir*String(av||'').localeCompare(String(bv||''))}});
  currentPage=0;render();
}}
function clearFilters(){{
  document.getElementById('pt-search').value='';
  document.getElementById('filter-state').value='';
  document.getElementById('filter-bu').value='';
  document.getElementById('filter-flag').value='';
  // Reset territory dropdown to full list
  const selT=document.getElementById('filter-territory');
  selT.innerHTML='<option value="">All Territories</option>';
  const terrs=[...new Set(RAW.rows.map(r=>r[CI['territory']]).filter(Boolean))].sort();
  terrs.forEach(t=>{{const o=document.createElement('option');o.value=o.textContent=t;selT.appendChild(o)}});
  selT.value='';
  applyFilters();
}}
function render(){{
  const visCols=getVisCols(),start=currentPage*PAGE_SIZE,end=Math.min(start+PAGE_SIZE,filtered.length);
  document.getElementById('pt-thead').innerHTML='<tr>'+visCols.map(c=>`<th class="${{c===sortCol?(sortDir<0?'sorted-desc':'sorted-asc'):''}}" onclick="sortBy('${{c}}')">${{COL_LABELS[c]||c}}</th>`).join('')+'</tr>';
  if(filtered.length===0){{
    document.getElementById('pt-tbody').innerHTML=`<tr><td colspan="${{visCols.length}}" style="text-align:center;color:var(--muted);padding:32px">No partners match the current filters. <a href="#" onclick="clearFilters();return false;" style="color:var(--accent)">Clear filters</a></td></tr>`;
    document.getElementById('pt-showing').textContent='No results';
    document.getElementById('pt-count-label').textContent='0 partners';
    document.getElementById('pt-page-info').textContent='—';
    document.getElementById('pt-prev').disabled=true;
    document.getElementById('pt-next').disabled=true;
    return;
  }}
  document.getElementById('pt-tbody').innerHTML=filtered.slice(start,end).map(r=>'<tr>'+visCols.map(c=>`<td>${{cellVal(c,r[CI[c]])}}</td>`).join('')+'</tr>').join('');
  document.getElementById('pt-showing').textContent=`Showing ${{start+1}}–${{end}} of ${{filtered.length.toLocaleString('en-IN')}}`;
  document.getElementById('pt-count-label').textContent=`${{filtered.length.toLocaleString('en-IN')}} partners`;
  document.getElementById('pt-page-info').textContent=`Page ${{currentPage+1}} of ${{Math.ceil(filtered.length/PAGE_SIZE)}}`;
  document.getElementById('pt-prev').disabled=currentPage===0;
  document.getElementById('pt-next').disabled=end>=filtered.length;
}}
function sortBy(col){{if(sortCol===col)sortDir*=-1;else{{sortCol=col;sortDir=-1}}filtered.sort((a,b)=>{{const si=CI[col],av=a[si],bv=b[si];if(typeof av==='number')return sortDir*(bv-av);return sortDir*String(av||'').localeCompare(String(bv||''))}});currentPage=0;render()}}
function ptPage(dir){{const max=Math.ceil(filtered.length/PAGE_SIZE)-1;currentPage=Math.max(0,Math.min(max,currentPage+dir));render();document.getElementById('pt-table').scrollIntoView({{behavior:'smooth',block:'nearest'}})}}
document.querySelectorAll('.pt-group-btn').forEach(btn=>{{btn.addEventListener('click',()=>{{const g=btn.dataset.group;if(g==='core')return;if(activeGroups.has(g)){{activeGroups.delete(g);btn.classList.remove('active')}}else{{activeGroups.add(g);btn.classList.add('active')}};render()}})}});
['pt-search','filter-bu','filter-territory','filter-flag'].forEach(id=>{{document.getElementById(id).addEventListener('input',applyFilters);document.getElementById(id).addEventListener('change',applyFilters)}});
window.addEventListener('load',()=>{{
  setTimeout(()=>document.querySelectorAll('[data-pct]').forEach(el=>el.style.width=el.dataset.pct+'%'),120);
  buildFilters();applyFilters();
}});
</script>
</body>
</html>"""


def main():
    import datetime
    today = datetime.date.today().strftime("%-d %B %Y")

    client = bigquery.Client(project=PROJECT)

    rows = fetch_data(client)
    cols = [field.name for field in client.get_table(
        f"{PROJECT}.prod_db_views.wallet_creditwallettransaction"
    ).schema]  # not needed — get from job

    # Re-fetch schema from job result
    job = client.query("SELECT 1")  # dummy
    # Actually get cols from rows
    if rows:
        cols = list(rows[0].keys())

    kpis        = compute_kpis(rows, cols)
    ageing_data = compute_ageing(rows, cols)
    state_data  = compute_states(rows, cols)
    top15       = compute_top15(rows, cols)
    raw_json    = rows_to_payload(rows, cols)

    html = build_html(raw_json, kpis, ageing_data, state_data, top15, today)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b2b_credit_health.html")
    with open(out_path, "w") as f:
        f.write(html)

    size_mb = len(html.encode()) / 1024 / 1024
    print(f"\nDone. Written to: {out_path}")
    print(f"File size: {size_mb:.2f} MB")
    print(f"Partners: {len(rows):,}")

    # Open in browser
    webbrowser.open(f"file://{out_path}")


if __name__ == "__main__":
    main()
