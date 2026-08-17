# Panil's Query — Saathi Partner Outstanding & Collections Report

Source: `DVS Analysis/.claude/commands/b2b-ledger.md` and `sales-analyst.md` (Panil's canonical B2B collections query, verified working Jun 2026). One row per active Saathi partner, ~25,100 rows.

**Critical fix:** `offline_team.OKR_RAW_MAPPING` (Google Sheets–backed external table) throws Drive credential errors — always use `agrostar-data.offline_team.okr_data_live` (native BQ table, identical fields) instead.

## Structure: `A LEFT JOIN B LEFT JOIN C LEFT JOIN D LEFT JOIN E LEFT JOIN F LEFT JOIN G`

| Subquery | What it provides |
|---|---|
| **A** | Last B2B order per partner (base — everything else hangs off this) |
| **B** | FY-wise invoiced revenue & product group counts |
| **C** | DPD (Days Past Due) weighted score |
| **D** | Last payment date, amount, age bucket |
| **E** | First and last B2B order dates |
| **F** | Outstanding balance + full OCP ageing + billing flag (core CTE) |
| **G** | 6-month rolling average monthly app launches (MAU) |

### A — Last B2B order per partner
```sql
FROM `agrostar-data.prod_db_views.sale_order` so
LEFT JOIN `agrostar-data.dwh_views.txn_source` ts ON ts.unicommerce_id = so.Display_Order_Code
LEFT JOIN `agrostar-data.galaxy_views.institution` ins ON ts.farmer_id = ins.reference_customer_id
LEFT JOIN `agrostar-data.offline_team.okr_data_live` okr ON ts.farmer_id = okr.farmer_id
WHERE ts.initiating_source LIKE '%B2B%' AND reference_customer_id IS NOT NULL
```
Dense rank on `ts.farmer_id ORDER BY order_date DESC`, keep `rn=1`. Gets city, pincode, address, `ins.address_village/taluka/district/pincode`, `ins.status`, `ins.gst_slabs`, `ins.type_of_locality AS store_type`, `okr.Territory`.
**`ts.farmer_id = ins.reference_customer_id = okr.farmer_id`** = the Saathi partner's ID (join key across the whole query).

### B — FY-wise revenue
`agrostar-data.optimized_reports_data.debit_id_wise_Sales_settlement`, filter `reason_id = 3`, `GROUP BY farmer_id`. FY23–FY26 gross revenue, return revenue, distinct product group count, split by `PL_NPL = 'PL'`.

### C — DPD score
Debits (`wallet_creditwallettransaction`, reason_id=3, transaction_type=0) joined to credits via `wallet_creditwallettransactionreconciliation`; partner resolved via `csr_farmer.user_id = wallet_user_id`.
1. Each reconciliation record classified WCP (credit_date ≤ due_date) or OCP (credit_date > due_date)
2. DSO = `DATE_DIFF(credit_date, debit.created_on, DAY)` (0 if same day; days-since-creation if credit NULL)
3. Weight = `reconciled_amount × DSO`
4. `DPD = SUM(weights) / SUM(debit_amount)` — higher = slower payer
Filter: `due_date >= current_date - 365 AND (is_reconciled = 1 OR due_date < current_date)`.

### D — Last payment
Three UNION branches, ranked by recency (`rank_1=1`) then largest amount (`rank_2=1`):
1. `reason_id=4, transaction_type=1, amount ≥ 1000` (primary cash payments)
2. `reason_id IN (4,12,36,31), transaction_type=1` (all payment types)
3. `reason_id=3, transaction_type=0` (first-ever debit, fallback, amount=0)
Output: `last_paid_created_on`, `amount`, `paymnet_Bucket` (0-30/31-60/61-90/91-120/120+ days).

### E — Order history
`order_management_order` where `initiating_source LIKE '%b2b%' AND unicommerce_id IS NOT NULL AND status <> 'CANCELLED'`, grouped by `owner_id AS farmer_id` → `first_order_date`, `last_order_date`.

### F — Outstanding + OCP ageing + billing flag (core)

**CTE `helth`** — unreconciled debits with full ageing. Joins:
- `wallet_creditwallettransaction` (cwt): all open debits — `cancelled=0, transaction_type=0, reason_id NOT IN (2), is_reconciled=0`
- `csr_farmer` (csr): `wallet_user_id → farmer_id`
- `okr_data_live` (okr): `csr.farmer_id = okr.farmer_id` → State, Revised_State, Territory, Cluster, Business_Unit, status, name, sh
- `wallet_creditwallettransactionreconciliation` (cwtr), aggregated: `SUM(amount) WHERE cancelled=0 GROUP BY reconciled_for_id`
- `order_management_order` (omo): `safe_cast(sales_order_id AS STRING) = cwt.reference_id` → unicommerce_id
- `agrostar_sale_order` (sa): `sa.Display_order_code = omo.unicommerce_id` → `invoice_created` date
- `optimized_reports_data.debit_id_wise_Sales_settlement` (opti): `opti.id = cwt.id` → item_type_name, sku_code, PL_NPL, product_group, category

Computed fields:
- `pending_amount = (cwt.amount + IFNULL(cwt.interest_amount,0)) − IFNULL(cwtr.reconciled_amount,0)`
- `ageing_days = DATE_DIFF(current_date, cwt.due_date, DAY)` — negative = not yet due (WCP zone), positive = overdue (OCP zone)
- `billing_ageing`: if `reason_id=3` and invoice exists → days since invoice; else days since `created_on`

Revised_State split:
```sql
CASE
  WHEN Revised_State LIKE '%MH%' THEN 'MH'
  WHEN Revised_State LIKE '%UP%' AND SH = 'RAKESH.SINGH@AGROSTAR.IN' THEN 'UP_A'
  WHEN Revised_State LIKE '%UP%' AND SH = 'PINTOO.VERMA@AGROSTAR.IN' THEN 'UP_B'
  ELSE Revised_State
END
```

Outer filter: `pending_amount >= 1` → then `total_os >= 100 AND status IS NOT NULL`.
Ageing buckets across 5 dimensions (Overall, Seeds [`reason_id=3, category='Seeds'`], CPCN [`reason_id=3, category!='Seeds'`], Interest [`reason_id=10`], Other debits): OCP buckets = WCP / nex_3_day_due / nex_7_day_due / OCP_0_30 / 30_60 / 60_90 / 90_150 / 150_180 / 180_210 / 210_240 / 240_plus. Billing buckets = 0-30 through 365+.

**CTE `gal`** — MPD-enabled partners: `galaxy_views.institution` where `archive=FALSE AND LOWER(ancestor_institutions_name) LIKE '%sathi%' AND isMpdEnabled=TRUE`. Fields: `reference_customer_id`, `partnerCreditType` (NULL→"Agrostar Credit"), `isDeliveryViaStoreEnabled`, `isMpdEnabled`.

**CTE `mpd`** — MPD window: `galaxy_views.creditwallet` LEFT JOIN `csr_farmer ON csr.user_id = wal.walletUserId`. Fields: `mpdData_startWindowDate/endWindowDate`, `mpdData_initialAmount`, `mpdData_orderAmount`, `mpdData_orderEligibleAmount`, `mpdData_remainingAmount`, `can_place_order = orderEligibleAmount − orderAmount`, `bucket_0_30/31_60/60_plus` (SPLIT on `bucketed_data`).

**`bill_flag` logic:**
```sql
CASE
  WHEN OCP < 5000 AND OCP_90_plus < 1000 THEN 'Open for sale'
  WHEN total_os IS NULL                   THEN 'Open for sale'
  WHEN isMpdEnabled = TRUE AND mpdData_remainingAmount = 0 THEN 'OCP billing unlocked'
  WHEN isMpdEnabled = TRUE AND mpdData_remainingAmount > 0 THEN 'OCP Collect MPD'
  WHEN OCP >= 5000                        THEN 'OCP Blocked'
  WHEN OCP_90_plus > 1000                 THEN 'OCP Blocked'
END AS bill_flag
```

### G — Monthly average app usage (MAU)
`saathi_clevertap_views.app_launched`, last 180 days, active Saathi partners only (institution + ≥1 B2B order). `ROUND(AVG(MAU),0)` per partner, joined to A via `CAST(farmer_id AS STRING) = patner_id`.

## Complete table map

| Alias | Full table | Role |
|---|---|---|
| so | `prod_db_views.sale_order` | Order facts |
| ts | `dwh_views.txn_source` | Order → farmer_id + initiating_source |
| ins | `galaxy_views.institution` | Store address, status, GST slab, type |
| okr | `offline_team.okr_data_live` | Territory/hierarchy — **never `OKR_RAW_MAPPING`** |
| cwt | `prod_db_views.wallet_creditwallettransaction` | All wallet debits and credits |
| cwtr | `prod_db_views.wallet_creditwallettransactionreconciliation` | Partial reconciliation amounts |
| csr | `prod_db_views.csr_farmer` | `wallet_user_id → farmer_id` |
| omo | `prod_db_views.order_management_order` | `reference_id → unicommerce_id` |
| sa | `prod_db_views.agrostar_sale_order` | `invoice_created` date for billing_ageing |
| opti | `optimized_reports_data.debit_id_wise_Sales_settlement` | Item/category/SKU/PL_NPL |
| gal | `galaxy_views.institution` | MPD flag, credit type |
| wal | `galaxy_views.creditwallet` | MPD window, remaining amount |
| — | `saathi_clevertap_views.app_launched` | App session events for MAU |

## reason_id reference (`wallet_creditwallettransaction`)
| reason_id | Meaning |
|---|---|
| 2 | Adjustment — always exclude |
| 3 | Product billing (main B2B invoice debit) |
| 4 | Cash payment received |
| 10 | Interest charged |
| 12, 31, 36 | Other payment types |

`transaction_type`: `0` = debit (owed to Agrostar), `1` = credit (received from partner).

## Data caveats

0. **`wallet_user_id` ≠ `farmer_id`** — always JOIN `csr_farmer ON cf.user_id = t.wallet_user_id` first. Using `wallet_user_id` directly as `farmer_id`/`retail_store_code` gives wrong or empty results.
1. `cancelled = 0` required on both `wallet_creditwallettransaction` and its reconciliation table, always — `cancelled=1` = void.
2. `reason_id = 2` (CL/credit-limit change) always excluded from collections/settlement — it's a limit adjustment, not cash flow.
3. Collections filter is `reason_id = 4`, not `reference_type` — all payment types (VAN, Saathi App, QR, etc.) share `reason_id = 4`.
4. Settlement is at reconciliation-record level, not debit level — a single debit can be settled across multiple reconciliation records/days/credit types.
5. WCP/OCP classification is per reconciliation record — one debit can be partly WCP and partly OCP depending on when each settlement landed vs `due_date`.
6. Partial settlement: `SUM(reconciliation.amount WHERE reconciled_for_id = debit.id) < debit.amount`. Never assume an uncancelled debit is fully outstanding without checking reconciliation.
7. `interest_amount` exists on `transaction_type=0` rows but is **not visible in INFORMATION_SCHEMA** (lives in the underlying table, not the view definition) — query it directly. Accrued interest ≠ posted interest; posted interest is a separate `reason_id=10` entry.
8. `due_date` can be NULL (non-order debit entries) — handle before computing WCP/OCP.
9. Reason code lookup table: `wallet_reason` (`id → explanation`).
10. Payment mode: use `reference_sub_type` first (e.g. `RAZORPAY_QR_CODE`, `PAY_BY_PRODUCT_GROUP`), not `description` LIKE-parsing.
11. VAN identification requires a metadata join, not `reference_type` (stale) — true VAN = `wallet_creditwallettransactionmetadata.unhold_by IS NOT NULL AND unhold_by NOT IN ('537940')`.
12. `wallet_creditwallettransactionmetadata` has multiple rows per transaction — always `DISTINCT` on `transaction_id` or collection amounts double-count.
13. `is_usable`: `0` = on hold, `1` = unholded/applied. For **collection totals** do NOT filter on `is_usable` (money received regardless); filter `is_usable=1` only for settlement/application analysis.
14. `537940` = permanent system auto-process user constant — `unhold_by = '537940'` or NULL means auto-processed (not VAN); a real human ID means VAN.
15. Payment-mode classification priority: `reference_sub_type` → VAN metadata check → `description` LIKE patterns. Never classify on `reference_type` alone.
16. `invoiced_report` is item-level — always aggregate with `SUM`/`COUNT(DISTINCT InvoiceNo)` for order-level counts.
17. `okr_data_live` covers **B2B/Saathi partners only** — do not use it for B2C geography (use `csr_shippingaddress` instead).
18. Use invoice date (`inv.CreatedOn`) not order date (`o.created_on`) for sales/revenue reporting.
19. `is_return = 1` rows in `invoiced_report` are credit notes/returns — exclude with `is_return = 0` for forward sales.
