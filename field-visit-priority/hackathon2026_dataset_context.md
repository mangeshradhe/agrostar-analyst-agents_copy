# `agrostar-data.hackathone2026_dataset` — Tables, Grain, Joins, Caveats

All 23 objects are BigQuery **VIEWs** (not materialized tables) built for a Sept 2026 AgroStar hackathon — pre-joined "one view" rollups of the B2B/Saathi partner sales, collections, onboarding, targets (AOP/RoFo) and field-visit data, mostly derived from the same base tables documented in `field_dashboard_data_sources.md` and `panils_query_data_sources.md` (`offline_team.okr_data_live`, `prod_db_views.wallet_creditwallettransaction[reconciliation]`, `csr_farmer`, `order_management_order/orderitem`, `sale_order`, `optimized_reports_data.debit_id_wise_Sales_settlement`, `revenue_and_growth_team.sku_cat_repo`). BigQuery reports `numRows=0`/`numBytes=0` for all of them because they're views, not physical tables — row counts require running `COUNT(*)`.

## Table map

| Table | What it represents | Grain (1 row =) |
|---|---|---|
| `aop_target` | FY27 AOP (Annual Operating Plan) revenue targets per manager role (SH/CM/TM/ASM/CST), gross & net, prorated by date-of-joining | state(CPCN/Seed variant) × territory × cluster × category × class × Month |
| `field_attendance` | Daily field-rep attendance derived from store/village/Brahmastra visit logs + leave/attendance requests | rep email × Date |
| `one_view_aop` | AOP monthly targets by geography × product, **MP only** | state × territory × cluster × category × Product_group × class |
| `one_view_collection` | Current OCP/WCP collection target vs settled amount per partner, **MP only** | farmer_id |
| `one_view_dvs_onboarding` | DVS Activation Fee orders (partner onboarding fee), all states | order (display_order_code) |
| `one_view_dvs_revenue` | DVS ("Direct via Store") channel revenue by month/category, **MP only** | month × category × Product_group × geography/hierarchy |
| `one_view_last_paid_as_of_start_month` | Last payment per partner, aged as of the **start of current month** | farmer_id |
| `one_view_last_paid_as_today` | Same as above, aged as of **today** | farmer_id |
| `one_view_mom_okr_outstanding` | Monthly (1st-of-month) outstanding snapshot per partner, **MP only** | farmer_id × month |
| `one_view_okr` | Enriched partner master (OKR) with DVS flag, state/cluster/territory sales rank, FY26 net revenue, **MP only** | farmer_id |
| `one_view_onboarding` | New-partner onboarding funnel: lead owner/role, confirmation date, first-30-day payment qualification, **MP only** | onboarding record (reference_customer_id × confirmed_on) |
| `one_view_outstanding_and_pg` | Outstanding (WCP/OCP/OCP_120+) + FY27 product-group count/revenue + top-5 products by OCP, **MP only** | farmer_id |
| `one_view_return` | B2B return amounts by month, **MP only** | Partner_id × Return_month |
| `one_view_rofo` | RoFo (rolling forecast) monthly revenue projection Apr26–Mar27 by geography/product, **MP only** | state × territory × cluster × category × Product_group × class |
| `one_view_sale_mom` | Granular partner-level sales, month + week-of-month bucketed, **MP only** | partner(owner_id) × territory × category × Product_group × class × month × week |
| `one_view_top10_ocp_pg` | Top-10 product groups by outstanding (OCP) per **Territory**, with ageing buckets | Territory × Product_group (top 10 only) |
| `one_view_top10_ocp_pg_cluster` | Same as above, ranked per **Cluster** | Cluster × Product_group (top 10 only) |
| `one_view_top10_ocp_pg_state` | Same as above, ranked per **State** | State × Product_group (top 10 only) |
| `one_view_uf` | Revenue of orders stuck `unicommerce_status = 'ON_HOLD'` (likely "Un-Fulfilled"), **MP only** | partner × territory × category × Product_group × class × month × week |
| `one_view_visits` | Field visit log enriched with rep role, same-day collections (`amount_paid`) and same-day revenue (`Total_rev`), **MP only** | rep email × date × store |
| `partner_incentive_calculation` | Class A/B gross & net FY27 sales + reconciliation-ageing buckets (0–60/61–90/91–120/120+), for partner incentive/commission calc | partner_id |
| `sm_aop_target` | Same AOP target logic as `aop_target` but for SM/SSM/EM roles | state × territory × cluster × category × class × Month |
| `target_master_query` | Consolidated long-format target table combining all roles (SH/CM/TM/ASM/CST + SM/SSM/EM) into one | revised_state × email × role |

## Detail by group

### AOP / target tables (`aop_target`, `sm_aop_target`, `target_master_query`)

Source: `optimized_reports_data.aop_offline_online_fy27` (raw AOP input, one row per SKU with 12 monthly gross/return columns `gross_apr_26`...`gross_mar_27`) joined to `revenue_and_growth_team.sku_cat_repo` for category/class, then cross-joined to `offline_team.okr_data_live` hierarchy.

- **Dual hierarchy**: `okr_data_live` carries two parallel org trees — the default "CPCN" tree (`sh/cm/tm/sm/em/ssm_cpcn`) and a separate "Seed" tree (`sh_seed/cm_seed/tm_seed/sm_seed/ssm_seed/cluster_seed/territory_seed`). All three target tables UNION ALL both trees, and for Maharashtra specifically tag state as `MH_CP/CN` vs `MH_SEED` since MH is the only state split this way.
- **DOJ proration**: manager DOJ (date of joining) is looked up from `offline_team.master_database_table` (matched by `TRIM(UPPER(email))`). A month's target is only credited to a manager if `<manager>_DOJ < Month + 9` — this is date arithmetic (`Month + 9` = 9 **days** after the 1st of that month), i.e. an approximate "was already in role by ~day 10 of the month" rule, not "joined before month start."
- **VACANT handling**: `sh`/similar fields containing `'VACANT'` are backfilled from the zonal manager (`zm_cpcn`/`zm_seed`); TM/ASM/SSM default to the literal string `'VACANT'` via `IFNULL`.
- `aop_target` / `sm_aop_target` are **wide format** (one target column per role: `SH_Target_gross`, `CM_Target_gross`, etc. / `SM_Target_gross`). `target_master_query` **unpivots** both into a single long/tidy table (`email`, `role`, `target_gross`, `target_ytd_gross`, `target_net`, `target_ytd_net`) — this looks like the final consumption table; the other two look like intermediate/debug artifacts.
- None of these three filter to MP — they cover all states (aop_target/target_master_query explicitly exclude `State != 'HP'`; sm_aop_target has no state filter at all).

### Top-10 OCP-by-product-group trio (`one_view_top10_ocp_pg`, `_cluster`, `_state`)

Identical query logic — `optimized_reports_data.debit_id_wise_Sales_settlement` filtered `reason_id=3, OCP_amount>=1`, aggregated by state/cluster/territory/product_group into ageing buckets (0-30/31-60/61-90/90+, both OCP amount and distinct-partner-count), then `ROW_NUMBER()` ranked and capped at top 10 — **only the `PARTITION BY` column differs** (Territory / Cluster / Revised_state respectively). Same source table as OCP ageing elsewhere (Panil's query CTE `helth`). Not MP-restricted.

### Partner-level outstanding/OKR snapshots (`one_view_okr`, `one_view_outstanding_and_pg`, `one_view_mom_okr_outstanding`, `one_view_collection`)

- `one_view_okr`: partner master + `galaxy_views.institution` (DVS flag via `isDeliveryViaStoreEnabled`) + a nested revenue-ranking subquery computing FY25–FY27 net revenue (gross minus returns, split further by Seeds/CP/CN category) and `Overall_Rank`/`Territory_Rank`/`Cluster_Rank`/`State_Rank` by FY26 net revenue (or FY26 Seed net for seed-category ranking).
- `one_view_outstanding_and_pg`: rebuilds the OCP ageing logic from scratch (own `pending_amount`/`ageing_days` CTE, same shape as Panil's `helth` CTE) restricted to `is_reconciled=0` and `pending_amount>0`, then adds FY27 product-group count/revenue and a `top_5_products_ocp` string (`STRING_AGG` of top-5 product groups by OCP, built from `debit_id_wise_Sales_settlement`).
- `one_view_mom_okr_outstanding`: pulls `prod_db_views.ledger_summary` (a daily ledger snapshot table, `due_wcp`/`due_ocp`/`total_outstanding`) filtered to the 1st of each month from Apr 2026 onward — gives a real month-over-month outstanding trend (unlike the other tables which are point-in-time as-of-now).
- `one_view_collection`: OCP/WCP **target** (from `ledger_summary` snapshot dated 2026-04-01, plus a debit-due-in-period sum) vs OCP/WCP/Future **settlement** (from `wallet_creditwallettransactionreconciliation`, bucketed by `due_date` into OCP/WCP/Future relative to FY start 2026-04-01) — this is a collections target-vs-actual view, not a plain balance.

### Last-payment pair (`one_view_last_paid_as_of_start_month`, `one_view_last_paid_as_today`)

Byte-for-byte identical ranking logic (3-scenario UNION: latest payment ≥₹1000 → latest payment any amount → earliest debit as fallback, `reason_id=4/transaction_type=1` for payments, `reason_id=3/transaction_type=0` for the fallback debit), differing **only** in the `Age_last_payment` reference point: `DATE_TRUNC(CURRENT_DATE(), MONTH)` (start of month) vs `CURRENT_DATE()` (today). Easy to grab the wrong one when doing "as-of" period comparisons.

### Sales/revenue tables (`one_view_dvs_revenue`, `one_view_sale_mom`, `one_view_uf`, `one_view_rofo`, `one_view_aop`)

- `one_view_dvs_revenue`: DVS **store-order** (`order_type="STORE-ORDER"`) revenue, joined to `bizfin_team.DVS_shipping_package_status` for actual invoice/dispatch/delivery dates rather than order-management dates.
- `one_view_sale_mom`: general B2B sales from `optimized_reports_data.sale_return_b2c_b2b`, bucketed into calendar week-of-month (`Week1`–`Week4` by day-of-month, not ISO weeks).
- `one_view_uf`: same shape as `one_view_sale_mom` but filtered to `unicommerce_status="ON_HOLD"` orders only — likely tracking revenue "stuck"/un-fulfilled, table name not self-explanatory.
- `one_view_rofo`: forecast revenue (`optimized_reports_data.rofo_fy_27_table`, columns `Vol_Apr_26`...`Vol_Mar_27`) rather than actuals — despite `Gross_rev_*` column naming, these are RoFo (rolling forecast) volumes, not booked sales.
- `one_view_aop`: raw AOP input table filtered to `state="MP"`, simple passthrough of `aop_offline_online_fy27` joined to `sku_cat_repo` — the source data behind `aop_target`/`one_view_rofo`-style category mapping.

### Onboarding & incentive (`one_view_onboarding`, `one_view_dvs_onboarding`, `partner_incentive_calculation`)

- `one_view_onboarding`: matches Zoho CRM leads (`optimized_reports_data.zoho_leads`) to the partner's first qualifying B2B order, computes `Total_pay_in_30_days` (sum of collections within 30 days of onboarding) and flags `First_payment_qualified = 'Yes'` when that sum ≥ ₹10,000. Also derives `lead_owner_role` (CM/TM/SM/EM) by matching the lead owner's email against the partner's assigned hierarchy in `okr_data_live` (both CPCN and Seed trees).
- `one_view_dvs_onboarding`: much simpler — just DVS Activation Fee line-item orders (`item_type_name LIKE '%DVS Activation Fee%'`) joined to owner/hierarchy, no MP filter.
- `partner_incentive_calculation`: FY27-only (`2026-04-01`–`2027-03-31`), splits gross sales into Class A / Class B (from `sku_cat_repo.class`), and separately buckets **reconciliation speed** (not ageing of outstanding, but how fast past debits were reconciled: `recon_done_upto_60`/`61_90`/`91_120`/`above_120` vs `recon_due_*` for what's still pending in each age band) — this is the shape used to calculate a partner's incentive/commission based on both sales volume and payment promptness.

### Visit/attendance tables (`field_attendance`, `one_view_visits`)

- `field_attendance`: unions 4 activity sources — `offline_team.weekly_store_visits` + `prod_db_views.visit` (store visits), `offline_team.village_visit` (village visits, excludes `Activity='Farmer Connect'`), `offline_team.FS_Demo` (crop demo site visits, 3 date columns unioned), `offline_team.brahmastra_new_leads`/`brahmastra_visit` (a lead-gen program's leads and visits) — then joins approved leave/attendance requests from `offline_team.FieldStar_Attendance_Request` and a role-based daily visit `target` (2 for TM/Cluster Sales/ASM/State Head, 3 for SO/Expansion/Sales Executive/Intern) from `offline_team.master_database_table`, producing a final `attendance_status` (`Valid day` / `AttendanceRequest` / `LeaveRequest` / `Absent`).
- `one_view_visits`: unions `store_visits_v2` + `store_visit_summary` + a Brahmastra-lead-visit branch (joined via phone number to `galaxy_views.institution`, not store ID) + `prod_db_views.visit`, restricted to the **current calendar month only** (`date_trunc(current_date(),month)` to next month), then attaches same-day collections and same-day sale revenue per visit — this is a much narrower/real-time slice than the dashboard's `store_visits_v2 UNION prod_db_views.visit` pattern (missing the historical range, but adding revenue/collections correlation per visit).

## Data caveats

1. **Most `one_view_*` tables are hardcoded to `state = "MP"` (Madhya Pradesh) only** — despite generic names, they are NOT all-India. MP-restricted: `one_view_aop`, `one_view_collection`, `one_view_dvs_revenue`, `one_view_mom_okr_outstanding`, `one_view_okr`, `one_view_onboarding`, `one_view_outstanding_and_pg`, `one_view_return`, `one_view_rofo`, `one_view_sale_mom`, `one_view_uf`, `one_view_visits`. NOT MP-restricted: `one_view_dvs_onboarding`, the top10_ocp_pg trio, `aop_target`, `sm_aop_target`, `target_master_query`, `partner_incentive_calculation`, `field_attendance`. Check the WHERE clause before assuming any of these cover other states.
2. **`one_view_return` joins `offline_team.okr_raw_mapping_table`** — this is the exact Google-Sheets-backed external table that `panils_query_data_sources.md` / the b2b-ledger playbook explicitly warn against (throws Drive credential errors, superseded by `okr_data_live`). This view is inconsistent with the rest of the dataset and may be stale/broken.
3. **DOJ proration quirk**: `<role>_DOJ < Month + 9` (in `aop_target`, `sm_aop_target`, `target_master_query`) adds 9 **days**, not 9 months — reads as "manager already in role by ~the 10th of the target month." Non-obvious date arithmetic; don't mistake it for a 9-month grace period.
4. **Dual org hierarchy (CPCN vs Seed)**: `okr_data_live` has two parallel reporting trees for Maharashtra-adjacent logic — plain columns (`sh/cm/tm/sm`) for CP/CN and `_seed`-suffixed columns for the Seed business. Any table that only reads the plain columns (e.g. simpler joins elsewhere) will silently miss Seed-hierarchy assignments; the AOP/target tables and `one_view_onboarding` correctly UNION both, others may not.
5. **`one_view_last_paid_as_of_start_month` vs `one_view_last_paid_as_today`** are identical except the ageing reference date (start-of-month vs today) — easy to pick the wrong one for period-over-period comparisons.
6. **`one_view_top10_ocp_pg` / `_cluster` / `_state` are the same query duplicated 3×**, differing only in the ranking `PARTITION BY` column — maintain/update all three together or they'll drift.
7. **Row counts unavailable via metadata** — all 23 objects are views (`numRows`/`numBytes` report as 0); use `SELECT COUNT(*)` if you need actual row counts, and expect query cost/latency to depend on the underlying view's own joins (several are deeply nested, e.g. `one_view_okr`, `one_view_outstanding_and_pg`, `one_view_onboarding`).
8. **`one_view_uf` name is not self-documenting** — inferred meaning ("Un-Fulfilled", `unicommerce_status='ON_HOLD'` orders) is a best guess from the query body, not a documented definition; verify with the table's author before relying on it.
9. **Two different "last payment" concepts across the dataset**: `one_view_last_paid_as_*` computes it from wallet transactions directly, while `one_view_onboarding`'s `Total_pay_in_30_days` computes a *sum* of payments in a fixed post-onboarding window — don't conflate "last payment date" with "total collected in first 30 days."
10. **`partner_incentive_calculation`'s ageing buckets are about reconciliation *speed*** (days between debit creation and when it was reconciled), not the OCP/DPD *outstanding-ageing* buckets used elsewhere (days between due date and today) — same-looking bucket names (`0-60`, `61-90`, etc.) but a different clock.
11. All the underlying join keys and caveats already documented for `okr_data_live`, `wallet_creditwallettransaction[reconciliation]`, `csr_farmer`, and B2B order filtering in `field_dashboard_data_sources.md` and `panils_query_data_sources.md` apply here too — this dataset is built on the same base tables, just pre-aggregated into hackathon-ready views.
