#!/usr/bin/env python3
"""BT Scan — Greedy Allocation for Agrostar RF Program (2026-05-29)"""

import json
from collections import defaultdict

# ─── FILE PATHS ───────────────────────────────────────────────────────────────
BQ_FILE = (
    "/Users/darpan/.claude/projects/"
    "-Users-darpan-Documents-claude-code-DVS-Analysis/"
    "77df03d8-2db2-4828-bd62-e7494d4c4296/tool-results/"
    "mcp-claude_ai_Google_Cloud_BigQuery-execute_sql_readonly-1780030596906.txt"
)
SHEET_FILE = (
    "/Users/darpan/.claude/projects/"
    "-Users-darpan-Documents-claude-code-DVS-Analysis/"
    "77df03d8-2db2-4828-bd62-e7494d4c4296/tool-results/"
    "mcp-claude_ai_Google_Drive-read_file_content-1780030602163.txt"
)


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def inr(amount):
    """Format float as Indian comma-separated ₹ string."""
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


def parse_float(val):
    if val is None or str(val).strip() in ("", "null", "0", "-"):
        return 0.0
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return 0.0


# ─── PARSE BQ FILE ───────────────────────────────────────────────────────────
with open(BQ_FILE, "r", encoding="utf-8") as f:
    bq_raw = f.read()

bq_data = json.loads(bq_raw)

total_bytes_processed = int(bq_data.get("totalBytesProcessed", 0))
total_bytes_billed    = int(bq_data.get("totalBytesBilled", 0))
mb_processed = total_bytes_processed / (1024 * 1024)
mb_billed    = total_bytes_billed    / (1024 * 1024)

FIELDS = [
    "partner_id", "store_name", "partner_name", "state", "district",
    "order_id", "order_bt_amount", "invoice_created_date",
    "invoice_age_days", "credit_term_days"
]

rows = bq_data.get("rows", [])
bq_orders = []
for row in rows:
    vals = [cell["v"] for cell in row["f"]]
    record = dict(zip(FIELDS, vals))
    record["partner_id"]      = str(record["partner_id"])
    record["order_id"]        = str(record["order_id"])
    record["order_bt_amount"] = parse_float(record["order_bt_amount"])
    record["invoice_age_days"]= int(float(record["invoice_age_days"])) if record["invoice_age_days"] else 0
    record["credit_term_days"]= int(float(record["credit_term_days"])) if record["credit_term_days"] else 0
    bq_orders.append(record)

# Group by partner
partner_orders = defaultdict(list)
partner_meta   = {}
for o in bq_orders:
    pid = o["partner_id"]
    partner_orders[pid].append(o)
    if pid not in partner_meta:
        partner_meta[pid] = {
            "store_name": o["store_name"],
            "partner_name": o["partner_name"],
            "state": o["state"],
            "district": o["district"],
        }


# ─── PARSE SHEET FILE ────────────────────────────────────────────────────────
with open(SHEET_FILE, "r", encoding="utf-8") as f:
    sheet_raw = f.read()

sheet_data   = json.loads(sheet_raw)
file_content = sheet_data.get("fileContent", "")
lines        = file_content.split("\n")

# The sheet has two Rupifi-specific sections with STATUS column:
#
# ACTIVE section  — lines 2495-2626
#   Header: # | CREDITLINE ID | CREATED ON | BIZ NAME | PHONE | EMAIL | NAME
#           | LIMIT | BALANCE | LENDER | STATUS | BIZ ID | Date | Month
#           | Vender | Partner Id | CRM LIMIT | Agrostar Status |
#   After stripping leading '' from pipe-split:
#   idx:  0=#  1=cl_id  2=created  3=name  4=phone  5=email  6=person
#         7=LIMIT  8=BALANCE  9=LENDER  10=STATUS  11=biz_id  12=date
#         13=month  14=vender  15=Partner Id  [16=crm_limit  17=ag_status]
#
# INACTIVE section — lines 2628-2645 (same header but only 16 cols; BALANCE=limit value, actual available=0)
#   idx:  0=#  1=cl_id  2=created  3=name  4=phone  5=email  6=person
#         7=LIMIT  8=BALANCE  9=LENDER  10=STATUS  11=biz_id  12=date
#         13=month  14=vender  15=Partner Id

def parse_pipe_row(line):
    """Parse a markdown pipe-delimited row, stripping leading/trailing empties."""
    parts = [p.strip() for p in line.split("|")]
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


rupifi_dict = {}  # partner_id (str) -> {status, balance, limit}

# ACTIVE section: lines 2497 to 2626 (skip header at 2495, separator at 2496)
for i in range(2497, 2627):
    line = lines[i].strip()
    if not line or ":-:" in line:
        continue
    parts = parse_pipe_row(line)
    if len(parts) < 16:
        continue
    pid = parts[15].strip()
    if not pid or not pid.isdigit():
        continue
    status  = parts[10].strip().upper()  # "ACTIVE"
    balance = parse_float(parts[8])      # BALANCE col — actual available balance
    limit   = parse_float(parts[7])      # LIMIT col
    rupifi_dict[pid] = {"status": status, "balance": balance, "limit": limit}

# INACTIVE section: lines 2630 to 2645
# For INACTIVE rows the col layout is same but BALANCE (parts[8]) shows the
# sanctioned limit (not usable). Account is deactivated → effective balance = 0
# but the BALANCE column value is used to decide B3 vs B4 per the spec.
for i in range(2630, 2646):
    line = lines[i].strip()
    if not line or ":-:" in line:
        continue
    parts = parse_pipe_row(line)
    if len(parts) < 16:
        continue
    pid = parts[15].strip()
    if not pid or not pid.isdigit():
        continue
    status  = parts[10].strip().upper()  # "INACTIVE"
    # For inactive rows: parts[8] contains the credit limit value (same as parts[7])
    # and there is no valid available-balance figure — treat as 0 since deactivated
    balance = 0.0
    limit   = parse_float(parts[7])
    rupifi_dict[pid] = {"status": status, "balance": balance, "limit": limit}


# ─── GREEDY ALLOCATION ────────────────────────────────────────────────────────
bucket0 = []  # missing from sheet
bucket1 = []  # actionable
bucket3 = []  # balance exhausted / zero / inactive+zero
bucket4 = []  # inactive but balance > 0 (not applicable given above, but kept)

for pid, orders in partner_orders.items():
    meta = partner_meta[pid]

    if pid not in rupifi_dict:
        total_bt  = sum(o["order_bt_amount"] for o in orders)
        oldest    = max(o["invoice_age_days"] for o in orders)
        bucket0.append({
            "partner_id":             pid,
            "store_name":             meta["store_name"],
            "state":                  meta["state"],
            "orders":                 len(orders),
            "total_bt_amount":        total_bt,
            "oldest_invoice_age_days": oldest,
        })
        continue

    rdata             = rupifi_dict[pid]
    rupifi_status     = rdata["status"]
    available_balance = rdata["balance"]
    sorted_orders     = sorted(orders, key=lambda o: o["invoice_age_days"], reverse=True)
    total_bt          = sum(o["order_bt_amount"] for o in sorted_orders)

    if rupifi_status == "ACTIVE" and available_balance > 0:
        remaining     = available_balance
        selected      = []
        not_selected  = []
        for o in sorted_orders:
            if o["order_bt_amount"] <= remaining:
                selected.append(o)
                remaining -= o["order_bt_amount"]
            else:
                not_selected.append(o)

        if selected:
            actionable_amt = sum(o["order_bt_amount"] for o in selected)
            oldest_sel     = max(o["invoice_age_days"] for o in selected)
            bucket1.append({
                "partner_id":             pid,
                "store_name":             meta["store_name"],
                "state":                  meta["state"],
                "available_balance":      available_balance,
                "actionable_bt_amount":   actionable_amt,
                "orders_selected":        len(selected),
                "oldest_invoice_age_days": oldest_sel,
            })
            if not_selected:
                blocked_amt = sum(o["order_bt_amount"] for o in not_selected)
                bucket3.append({
                    "partner_id":      pid,
                    "store_name":      meta["store_name"],
                    "state":           meta["state"],
                    "available_balance": remaining,
                    "blocked_amount":  blocked_amt,
                    "reason":          "Partial: balance exhausted after B1 picks",
                    "orders":          len(not_selected),
                })
        else:
            bucket3.append({
                "partner_id":      pid,
                "store_name":      meta["store_name"],
                "state":           meta["state"],
                "available_balance": available_balance,
                "blocked_amount":  total_bt,
                "reason":          "Active — balance too small for any order",
                "orders":          len(sorted_orders),
            })

    elif rupifi_status == "ACTIVE" and available_balance <= 0:
        bucket3.append({
            "partner_id":      pid,
            "store_name":      meta["store_name"],
            "state":           meta["state"],
            "available_balance": available_balance,
            "blocked_amount":  total_bt,
            "reason":          "Active — zero balance",
            "orders":          len(sorted_orders),
        })

    elif rupifi_status == "INACTIVE" and available_balance > 0:
        # B4: inactive but has usable balance (shouldn't occur with our 0-override above,
        # but kept for completeness if any future row has a real balance)
        bucket4.append({
            "partner_id":        pid,
            "store_name":        meta["store_name"],
            "state":             meta["state"],
            "available_balance": available_balance,
            "eligible_bt_amount": total_bt,
            "orders":            len(sorted_orders),
        })

    else:
        # INACTIVE + zero balance
        bucket3.append({
            "partner_id":      pid,
            "store_name":      meta["store_name"],
            "state":           meta["state"],
            "available_balance": available_balance,
            "blocked_amount":  total_bt,
            "reason":          "Inactive — zero/no balance",
            "orders":          len(sorted_orders),
        })


# ─── AGGREGATE STATS ─────────────────────────────────────────────────────────
b0_partners = len(bucket0)
b0_orders   = sum(p["orders"] for p in bucket0)
b0_amount   = sum(p["total_bt_amount"] for p in bucket0)

b1_partners = len(bucket1)
b1_orders   = sum(p["orders_selected"] for p in bucket1)
b1_amount   = sum(p["actionable_bt_amount"] for p in bucket1)

b3_partners = len(bucket3)
b3_orders   = sum(p["orders"] for p in bucket3)
b3_amount   = sum(p["blocked_amount"] for p in bucket3)

b4_partners = len(bucket4)
b4_orders   = sum(p["orders"] for p in bucket4)
b4_amount   = sum(p["eligible_bt_amount"] for p in bucket4)

total_partners  = len(partner_orders)
total_eligible  = b1_amount + b3_amount + b4_amount + b0_amount
actionable_pct  = (b1_amount / total_eligible * 100) if total_eligible > 0 else 0


# ─── PRINT TABLE ─────────────────────────────────────────────────────────────
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


# ─── OUTPUT ──────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("=== BT SCAN — 2026-05-29 ===")
print(f"Scan cost: {mb_processed:.1f} MB processed | {mb_billed:.1f} MB billed")
print("=" * 72)

# BUCKET 0
print()
print("--- BUCKET 0: MISSING FROM SHEET ---")
print(f"{b0_partners} partners with eligible orders but no Rupifi sheet record")
if bucket0:
    rows_data = []
    for p in sorted(bucket0, key=lambda x: x["total_bt_amount"], reverse=True):
        rows_data.append([
            p["partner_id"],
            p["store_name"][:38],
            p["state"],
            str(p["orders"]),
            inr(p["total_bt_amount"]),
        ])
    print_table(
        ["partner_id", "store_name", "state", "orders", "total_bt_amount"],
        rows_data,
    )

# BUCKET 1
print()
print("--- BUCKET 1: ACTIONABLE TODAY (B1) ---")
print(f"{b1_partners} partners | {b1_orders} orders | {inr(b1_amount)} actionable")
if bucket1:
    rows_data = []
    for p in sorted(bucket1, key=lambda x: x["actionable_bt_amount"], reverse=True):
        rows_data.append([
            p["partner_id"],
            p["store_name"][:35],
            p["state"],
            inr(p["available_balance"]),
            inr(p["actionable_bt_amount"]),
            str(p["orders_selected"]),
            str(p["oldest_invoice_age_days"]) + "d",
        ])
    print_table(
        ["partner_id", "store_name", "state", "avail_balance", "actionable_bt", "orders", "oldest_age"],
        rows_data,
    )

# BUCKET 3
print()
print("--- BUCKET 3: BALANCE EXHAUSTED / ZERO ---")
print(f"{b3_partners} partners | {b3_orders} orders | {inr(b3_amount)} eligible but blocked")
if bucket3:
    rows_data = []
    for p in sorted(bucket3, key=lambda x: x["blocked_amount"], reverse=True):
        rows_data.append([
            p["partner_id"],
            p["store_name"][:28],
            p["state"],
            inr(p["available_balance"]),
            inr(p["blocked_amount"]),
            p["reason"][:42],
        ])
    print_table(
        ["partner_id", "store_name", "state", "avail_balance", "blocked_amount", "reason"],
        rows_data,
    )

# BUCKET 4
print()
print("--- BUCKET 4: INACTIVE ON RUPIFI (balance available) ---")
print(f"{b4_partners} partners | {inr(b4_amount)} stranded — push Rupifi to activate")
if bucket4:
    rows_data = []
    for p in sorted(bucket4, key=lambda x: x["eligible_bt_amount"], reverse=True):
        rows_data.append([
            p["partner_id"],
            p["store_name"][:35],
            p["state"],
            inr(p["available_balance"]),
            inr(p["eligible_bt_amount"]),
        ])
    print_table(
        ["partner_id", "store_name", "state", "avail_balance", "eligible_bt_amount"],
        rows_data,
    )
else:
    print("  (none)")

# SUMMARY
print()
print("--- SUMMARY ---")
print(f"Total RF partners with eligible orders: {total_partners}")
print(f"  B0 (no sheet data):  {b0_partners} partners")
print(f"  B1 (actionable):     {b1_partners} partners | {inr(b1_amount)}")
print(f"  B3 (blocked):        {b3_partners} partners | {inr(b3_amount)}")
print(f"  B4 (inactive):       {b4_partners} partners | {inr(b4_amount)}")
print(f"Total eligible BT amount: {inr(total_eligible)}")
print(f"Actionable today: {inr(b1_amount)} ({actionable_pct:.1f}%)")
