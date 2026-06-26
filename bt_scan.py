#!/usr/bin/env python3
"""
BT Scan v2 — Two-Mode Balance Transfer Scan for Agrostar RF Program
  Mode 1: Full Order BT  — order total ≤ original partner balance → push entire order
  Mode 2: Debit ID Level — order total > original balance → maximise utilisation per debit
CSV source: /Users/darpan/Documents/AvailableLimit Rupifi Partners.csv
"""

import pandas as pd
from google.cloud import bigquery
from datetime import date, timedelta
import warnings
warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
CSV_PATH  = "/Users/darpan/Documents/AvailableLimit Rupifi Partners.csv"
PROJECT   = "agrostar-data"
DELIVERED = {"delivered", "delivered_at_godown"}

# ── Helpers ───────────────────────────────────────────────────────────────────
def inr(amount):
    amount = int(round(float(amount)))
    s = str(abs(amount))
    if len(s) <= 3:
        result = s
    else:
        result = s[-3:]
        s = s[:-3]
        while s:
            result = s[-2:] + "," + result
            s = s[:-2]
    return ("₹" if amount >= 0 else "-₹") + result


def print_table(headers, rows_data):
    if not rows_data:
        print("  (none)")
        return
    all_rows = [headers] + [[str(c) for c in row] for row in rows_data]
    widths   = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    fmt      = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows_data:
        print(fmt.format(*[str(c) for c in row]))


# ── Load CSV ──────────────────────────────────────────────────────────────────
limit_df = pd.read_csv(CSV_PATH)
limit_df.columns = ["partner_id", "available_limit"]
limit_df = limit_df.dropna(subset=["partner_id"])
limit_df["partner_id"] = limit_df["partner_id"].apply(lambda x: str(int(float(x))))
limit_dict = dict(zip(limit_df["partner_id"], limit_df["available_limit"].astype(float)))
print(f"CSV loaded: {len(limit_dict):,} partners with credit limits")

# ── BQ Query ──────────────────────────────────────────────────────────────────
QUERY = """
WITH
rf_partners AS (
  SELECT
    i.reference_customer_id AS farmer_id,
    i.user_id,
    i.name                  AS store_name,
    i.partner_name,
    i.address_state,
    i.address_district,
    ROW_NUMBER() OVER (PARTITION BY i.reference_customer_id ORDER BY i.created_on DESC) AS rn
  FROM `agrostar-data.galaxy_views.institution` i
  WHERE i.lendingProvider = 'RUPIFI'
    AND i.status = 'ACTIVE'
),
partners AS (
  SELECT farmer_id, user_id, store_name, partner_name, address_state, address_district
  FROM rf_partners WHERE rn = 1
),
partner_wallet AS (
  SELECT f.farmer_id, f.user_id AS wallet_user_id
  FROM `agrostar-data.prod_db_views.csr_farmer` f
  JOIN partners p ON p.farmer_id = f.farmer_id
),
all_debits AS (
  SELECT
    t.id                               AS debit_id,
    SAFE_CAST(t.reference_id AS INT64) AS order_id,
    t.wallet_user_id,
    t.amount                           AS debit_amount,
    t.due_date,
    t.finbox_transaction_id,
    t.is_reconciled
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransaction` t
  JOIN partner_wallet pw ON pw.wallet_user_id = t.wallet_user_id
  WHERE t.reason_id = 3
    AND t.transaction_type = 0
    AND t.cancelled = 0
),
recon_agg AS (
  SELECT
    r.reconciled_for_id AS debit_id,
    SUM(r.amount)       AS reconciled_amount
  FROM `agrostar-data.prod_db_views.wallet_creditwallettransactionreconciliation` r
  WHERE r.cancelled = 0
  GROUP BY 1
),
debits AS (
  SELECT
    d.debit_id,
    d.order_id,
    d.wallet_user_id,
    d.debit_amount,
    d.due_date,
    d.finbox_transaction_id,
    d.is_reconciled,
    COALESCE(r.reconciled_amount, 0)                          AS reconciled_amount,
    d.debit_amount - COALESCE(r.reconciled_amount, 0)         AS remaining_amount,
    (d.finbox_transaction_id IS NULL AND d.is_reconciled = 0) AS is_bt_eligible_debit
  FROM all_debits d
  LEFT JOIN recon_agg r ON r.debit_id = d.debit_id
),
order_agg AS (
  SELECT
    order_id,
    wallet_user_id,
    SUM(debit_amount)  AS total_order_debit_amount,
    MAX(due_date)      AS max_due_date
  FROM debits
  GROUP BY 1, 2
),
order_info AS (
  SELECT
    o.sales_order_id                 AS order_id,
    o.status                         AS order_status,
    o.unicommerce_status,
    CAST(o.unicommerce_id AS STRING) AS unicommerce_id
  FROM `agrostar-data.prod_db_views.order_management_order` o
  JOIN order_agg oa ON oa.order_id = o.sales_order_id
),
invoice_dates AS (
  SELECT
    oi.order_id,
    MIN(DATE(inv.CreatedOn)) AS invoice_created_date
  FROM order_info oi
  JOIN `agrostar-data.pristine_wms_views.invoiced_report` inv
    ON oi.unicommerce_id = inv.DisplayOrderCode
  WHERE inv.line_status != 'CANCELLED'
    AND inv.is_return = 0
  GROUP BY 1
)
SELECT
  p.farmer_id                                                                     AS partner_id,
  p.store_name,
  p.partner_name,
  p.address_state                                                                  AS state,
  p.address_district                                                               AS district,
  d.order_id,
  oi.order_status,
  oi.unicommerce_status,
  id.invoice_created_date,
  DATE_DIFF(CURRENT_DATE('Asia/Kolkata'), id.invoice_created_date, DAY)           AS invoice_age_days,
  oa.total_order_debit_amount,
  DATE_DIFF(DATE(oa.max_due_date), id.invoice_created_date, DAY)                  AS credit_term_days,
  d.debit_id,
  d.debit_amount,
  d.reconciled_amount,
  d.remaining_amount,
  d.due_date,
  d.is_reconciled,
  d.is_bt_eligible_debit,
  DATE_DIFF(DATE(d.due_date), id.invoice_created_date, DAY) AS credit_term_per_debit
FROM debits d
JOIN order_agg oa      ON oa.order_id = d.order_id AND oa.wallet_user_id = d.wallet_user_id
JOIN partner_wallet pw ON pw.wallet_user_id = d.wallet_user_id
JOIN partners p        ON p.farmer_id = pw.farmer_id
LEFT JOIN order_info oi   ON oi.order_id = d.order_id
LEFT JOIN invoice_dates id ON id.order_id = d.order_id
ORDER BY p.farmer_id, d.order_id, d.due_date DESC, d.remaining_amount DESC
"""

client = bigquery.Client(project=PROJECT)
print("Running BQ query (may take 60-90s)…")
query_job = client.query(QUERY)
df = query_job.to_dataframe()

billed_mb = (query_job.total_bytes_billed or 0) / (1024 * 1024)
print(f"Done. {len(df):,} rows | {df['partner_id'].nunique():,} partners | "
      f"{df['order_id'].nunique():,} orders | Billed: {billed_mb:.1f} MB")

# ── BT Logic ──────────────────────────────────────────────────────────────────
b0_partners      = []   # in BQ but not in CSV
partner_summaries = []

for partner_id, p_df in df.groupby("partner_id"):
    pid_str    = str(int(partner_id))
    store_name = p_df["store_name"].iloc[0]
    state      = p_df["state"].iloc[0]
    district   = p_df["district"].iloc[0]

    # B0 — no CSV record
    if pid_str not in limit_dict:
        b0_partners.append({
            "partner_id": partner_id, "store_name": store_name, "state": state
        })
        continue

    original_balance  = limit_dict[pid_str]
    remaining_balance = original_balance

    mode1_candidates = []
    mode2_candidates = []
    b2_waiting        = []

    for order_id, o_df in p_df.groupby("order_id"):

        # Gate 1: unicommerce_status must be delivered
        uni_raw = o_df["unicommerce_status"].iloc[0]
        if pd.isna(uni_raw) or str(uni_raw).lower().strip() not in DELIVERED:
            continue

        # Gate 2: invoice date must exist
        inv_date_raw = o_df["invoice_created_date"].iloc[0]
        if pd.isna(inv_date_raw):
            continue
        inv_date = inv_date_raw.date() if hasattr(inv_date_raw, "date") else inv_date_raw

        # Gate 3: invoice age ≤ 55 days
        inv_age_raw = o_df["invoice_age_days"].iloc[0]
        if pd.isna(inv_age_raw):
            continue
        inv_age = int(inv_age_raw)
        if inv_age > 55:
            continue

        # Gate 4: total order debit amount ≥ ₹200
        total_amt = float(o_df["total_order_debit_amount"].iloc[0])
        if total_amt < 200:
            continue

        # Gate 5: credit term — Mode 1 requires all debits within 120d term;
        #   Mode 2 applies the gate per debit, so mixed-term orders can still be partially pushed
        ct_raw          = o_df["credit_term_days"].iloc[0]
        credit_term_max = int(ct_raw) if not pd.isna(ct_raw) else 0
        m1_credit_ok    = not (credit_term_max > 120 and inv_age <= 33)

        order_rec = {
            "order_id":                 order_id,
            "order_status":             o_df["order_status"].iloc[0],
            "unicommerce_status":       uni_raw,
            "invoice_date":             inv_date,
            "invoice_age_days":         inv_age,
            "is_exceptional":           inv_age >= 46,
            "total_order_debit_amount": total_amt,
            "credit_term_max":          credit_term_max,
            "m1_credit_ok":             m1_credit_ok,
            "debits_df":                o_df,
        }

        # Mode 1: full order push — only if order fits balance AND all debits within credit term
        # Mode 2: debit-level — for oversized orders OR orders with mixed credit terms
        if total_amt <= original_balance and m1_credit_ok:
            mode1_candidates.append(order_rec)
        else:
            mode2_candidates.append(order_rec)

    # Sort oldest invoice first for both modes
    mode1_candidates.sort(key=lambda x: x["invoice_date"])
    mode2_candidates.sort(key=lambda x: x["invoice_date"])

    b1_picks  = []
    b3_orders = []

    # ── Pass 1: Full Order BT ──────────────────────────────────────────────────
    for oi in mode1_candidates:
        eligible     = oi["debits_df"][oi["debits_df"]["is_bt_eligible_debit"] == True].copy()
        net_consumed = float(eligible["remaining_amount"].sum())

        if net_consumed > 0 and net_consumed <= remaining_balance:
            remaining_balance -= net_consumed
            b1_picks.append({
                "mode":              "FULL_ORDER",
                "order_id":          oi["order_id"],
                "order_status":      oi["order_status"],
                "unicommerce_status": oi["unicommerce_status"],
                "invoice_date":      oi["invoice_date"],
                "invoice_age_days":  oi["invoice_age_days"],
                "is_exceptional":    oi["is_exceptional"],
                "total_order_amount": oi["total_order_debit_amount"],
                "actionable_amount": net_consumed,
                "debit_count":       len(eligible),
                "push_instructions": [
                    {
                        "debit_id":    int(row["debit_id"]),
                        "push_amount": float(row["debit_amount"]),
                        "void_amount": float(row["reconciled_amount"]) if float(row["reconciled_amount"]) > 0 else None,
                    }
                    for _, row in eligible.iterrows()
                ],
            })
        else:
            b3_orders.append({
                "mode":              "FULL_ORDER_NO_BALANCE",
                "order_id":          oi["order_id"],
                "invoice_date":      oi["invoice_date"],
                "invoice_age_days":  oi["invoice_age_days"],
                "total_order_amount": oi["total_order_debit_amount"],
                "blocked_amount":    net_consumed,
            })

    # ── Pass 2: Debit ID Level BT ──────────────────────────────────────────────
    for oi in mode2_candidates:
        eligible = oi["debits_df"][oi["debits_df"]["is_bt_eligible_debit"] == True].copy()

        if remaining_balance <= 0:
            b3_orders.append({
                "mode":              "DEBIT_LEVEL_NO_BALANCE",
                "order_id":          oi["order_id"],
                "invoice_date":      oi["invoice_date"],
                "invoice_age_days":  oi["invoice_age_days"],
                "total_order_amount": oi["total_order_debit_amount"],
                "blocked_amount":    float(eligible["remaining_amount"].sum()),
            })
            continue

        # Debit-level filters
        eligible = eligible[eligible["remaining_amount"] >= 200]
        eligible = eligible[eligible["invoice_age_days"] <= 55]

        # Per-debit credit term gate: exclude debits where lender would hold paper >120d
        #   only applies while invoice is still in the 0–33d waiting window
        inv_age_order = oi["invoice_age_days"]
        ct_gated  = (eligible["credit_term_per_debit"] > 120) & (inv_age_order <= 33)
        waiting_df = eligible[ct_gated]
        eligible   = eligible[~ct_gated]

        if not waiting_df.empty:
            b2_waiting.append({
                "order_id":           oi["order_id"],
                "order_status":       oi["order_status"],
                "unicommerce_status": oi["unicommerce_status"],
                "invoice_date":       oi["invoice_date"],
                "invoice_age_days":   inv_age_order,
                "credit_term_days":   oi["credit_term_max"],
                "eligible_from":      oi["invoice_date"] + timedelta(days=34),
                "total_order_amount": float(waiting_df["debit_amount"].sum()),
                "partial":            not eligible.empty,
            })

        if eligible.empty:
            continue

        eligible = eligible.copy()
        eligible["due_date"] = pd.to_datetime(eligible["due_date"])
        eligible = eligible.sort_values(
            ["due_date", "remaining_amount"], ascending=[False, False]
        )

        picked  = []
        skipped = []
        for _, row in eligible.iterrows():
            rem = float(row["remaining_amount"])
            if rem <= remaining_balance:
                picked.append(row)
                remaining_balance -= rem
            else:
                skipped.append(row)

        if picked:
            b1_picks.append({
                "mode":              "DEBIT_LEVEL",
                "order_id":          oi["order_id"],
                "order_status":      oi["order_status"],
                "unicommerce_status": oi["unicommerce_status"],
                "invoice_date":      oi["invoice_date"],
                "invoice_age_days":  oi["invoice_age_days"],
                "is_exceptional":    oi["is_exceptional"],
                "total_order_amount": oi["total_order_debit_amount"],
                "actionable_amount": sum(float(r["remaining_amount"]) for r in picked),
                "debit_count":       len(picked),
                "push_instructions": [
                    {
                        "debit_id":    int(r["debit_id"]),
                        "push_amount": float(r["debit_amount"]),
                        "void_amount": float(r["reconciled_amount"]) if float(r["reconciled_amount"]) > 0 else None,
                    }
                    for r in picked
                ],
            })

        if skipped:
            b3_orders.append({
                "mode":              "DEBIT_LEVEL_PARTIAL",
                "order_id":          oi["order_id"],
                "invoice_date":      oi["invoice_date"],
                "invoice_age_days":  oi["invoice_age_days"],
                "total_order_amount": oi["total_order_debit_amount"],
                "blocked_amount":    sum(float(r["remaining_amount"]) for r in skipped),
            })

    total_actionable = sum(p["actionable_amount"] for p in b1_picks)
    partner_summaries.append({
        "partner_id":       partner_id,
        "store_name":       store_name,
        "state":            state,
        "district":         district,
        "original_balance": original_balance,
        "remaining_balance": remaining_balance,
        "total_actionable": total_actionable,
        "utilization_pct":  round(100 * total_actionable / original_balance, 1) if original_balance > 0 else 0,
        "b1_picks":         b1_picks,
        "b2_waiting":       b2_waiting,
        "b3_orders":        b3_orders,
    })

# ── Output ────────────────────────────────────────────────────────────────────
SEP = "=" * 80
print()
print(SEP)
print(f"  BT SCAN — {date.today()}")
print(f"  Billed: {billed_mb:.1f} MB")
print(SEP)

# B0
if b0_partners:
    print(f"\n⚠  BUCKET 0 — {len(b0_partners)} partners in Rupifi BQ but NOT in available limit CSV")
    print_table(
        ["partner_id", "store_name", "state"],
        [[p["partner_id"], str(p["store_name"])[:40], p["state"]] for p in b0_partners],
    )

# B1 summary
actionable = sorted(
    [ps for ps in partner_summaries if ps["total_actionable"] > 0],
    key=lambda x: x["total_actionable"], reverse=True,
)
total_b1_amount = sum(ps["total_actionable"] for ps in actionable)
total_m1 = sum(
    len([p for p in ps["b1_picks"] if p["mode"] == "FULL_ORDER"]) for ps in actionable
)
total_m2 = sum(
    len([p for p in ps["b1_picks"] if p["mode"] == "DEBIT_LEVEL"]) for ps in actionable
)

print(f"\n--- BUCKET 1: ACTIONABLE TODAY ---")
print(f"{len(actionable)} partners | {inr(total_b1_amount)} | "
      f"M1 (Full Order): {total_m1} orders | M2 (Debit Level): {total_m2} orders")

rows_data = []
for ps in actionable:
    m1_cnt   = len([p for p in ps["b1_picks"] if p["mode"] == "FULL_ORDER"])
    m2_cnt   = len([p for p in ps["b1_picks"] if p["mode"] == "DEBIT_LEVEL"])
    exc_cnt  = len([p for p in ps["b1_picks"] if p.get("is_exceptional")])
    mode_str = f"M1:{m1_cnt} M2:{m2_cnt}" + (f" ⚡{exc_cnt}exc" if exc_cnt else "")
    rows_data.append([
        str(ps["partner_id"]),
        str(ps["store_name"])[:32],
        str(ps["state"])[:12],
        inr(ps["original_balance"]),
        inr(ps["total_actionable"]),
        f"{ps['utilization_pct']}%",
        mode_str,
    ])
print_table(
    ["partner_id", "store_name", "state", "avail_limit", "actionable", "util%", "mode"],
    rows_data,
)

# B1 push detail — per partner with actual debit IDs
print(f"\n--- PUSH INSTRUCTIONS (B1 DETAIL) ---")
for ps in actionable:
    if not ps["b1_picks"]:
        continue
    print(f"\n  ► {ps['partner_id']} | {ps['store_name']} | {ps['state']} "
          f"| Avail: {inr(ps['original_balance'])} | Action: {inr(ps['total_actionable'])}")
    for pick in ps["b1_picks"]:
        exc_tag = " ⚡EXCEPTIONAL" if pick.get("is_exceptional") else ""
        print(f"    [{pick['mode']}] Order {pick['order_id']} "
              f"| Status: {pick['order_status']} / {pick['unicommerce_status']} "
              f"| Invoice: {pick['invoice_date']} ({pick['invoice_age_days']}d){exc_tag} "
              f"| Action: {inr(pick['actionable_amount'])}")
        for instr in pick["push_instructions"]:
            void_str = f" → void {inr(instr['void_amount'])}" if instr["void_amount"] else ""
            print(f"      debit_id={instr['debit_id']}  push={inr(instr['push_amount'])}{void_str}")

# B2 waiting room
b2_partners_list = [ps for ps in partner_summaries if ps["b2_waiting"]]
total_b2 = sum(len(ps["b2_waiting"]) for ps in b2_partners_list)
print(f"\n--- BUCKET 2: WAITING ROOM ({total_b2} orders across {len(b2_partners_list)} partners) ---")
rows_data = []
for ps in sorted(b2_partners_list, key=lambda x: x["partner_id"]):
    for o in ps["b2_waiting"]:
        partial_tag = "[PARTIAL]" if o.get("partial") else ""
        rows_data.append([
            str(ps["partner_id"]),
            str(ps["store_name"])[:28],
            str(o["order_id"]),
            str(o["invoice_date"]),
            f"{o['invoice_age_days']}d",
            str(o["eligible_from"]),
            inr(o["total_order_amount"]),
            partial_tag,
        ])
print_table(
    ["partner_id", "store_name", "order_id", "invoice_date", "age", "eligible_from", "amount", "note"],
    rows_data,
)

# B3 blocked
b3_partners_list = [ps for ps in partner_summaries if ps["b3_orders"]]
total_b3 = sum(len(ps["b3_orders"]) for ps in b3_partners_list)
total_b3_amt = sum(
    sum(o["blocked_amount"] for o in ps["b3_orders"]) for ps in b3_partners_list
)
print(f"\n--- BUCKET 3: BALANCE INSUFFICIENT ({total_b3} orders | {inr(total_b3_amt)} blocked) ---")
rows_data = []
for ps in sorted(b3_partners_list, key=lambda x: x["partner_id"]):
    for o in ps["b3_orders"]:
        rows_data.append([
            str(ps["partner_id"]),
            str(ps["store_name"])[:25],
            o["mode"],
            str(o["order_id"]),
            f"{o['invoice_age_days']}d",
            inr(o["blocked_amount"]),
        ])
print_table(
    ["partner_id", "store_name", "mode", "order_id", "age", "blocked_amount"],
    rows_data,
)

# B0 partners in CSV but no orders in BQ
csv_only = set(limit_dict.keys()) - {str(int(ps["partner_id"])) for ps in partner_summaries} - {str(p["partner_id"]) for p in b0_partners}

# Summary
total_avail_csv = sum(limit_dict.values())
print(f"\n{SEP}")
print(f"  SUMMARY")
print(f"  Partners in CSV:               {len(limit_dict):>6,}")
print(f"  Partners in BQ (RUPIFI ACTIVE):{df['partner_id'].nunique():>6,}")
print(f"  B0 (BQ not in CSV):            {len(b0_partners):>6,}")
print(f"  Partners with actionable BT:   {len(actionable):>6,}")
print(f"  B2 waiting (orders):           {total_b2:>6,}")
print(f"  B3 blocked (orders):           {total_b3:>6,}")
print(f"  Total available limit (CSV):   {inr(total_avail_csv):>15}")
print(f"  Total actionable BT:           {inr(total_b1_amount):>15}")
print(SEP)
