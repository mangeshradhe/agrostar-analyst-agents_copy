# field_dashboard.html — Data Sources, Joins, Caveats

Source: `DVS Analysis/proxy.py` (Flask backend, `/api/field/*` routes), BigQuery project `agrostar-data`.
Backend serves 3 query groups behind `field_dashboard.html`: Partners, Visit Analysis, Recommendations.

## Core tables

| Table | Role |
|---|---|
| `offline_team.okr_data_live` | Partner master — farmer_id, name, territory, cluster, revised_state, sm/tm/cm/sh (rep emails), status, first_order_date. `status='ACTIVE'` = live partner. Rep fields can be `'VACANT...'` (unfilled hierarchy slot) — always filter `NOT STARTS_WITH(UPPER(TRIM(sm)),'VACANT')` when counting reps. |
| `offline_team.store_visits_v2` | Visit log from "fieldstar" app. Cols: `email, date, date_time, store_id, sm/tm/cm/sh, visit_type, promise_to_pay_date__p2p_, amount_promised`. |
| `prod_db_views.visit` | Visit log from "saathiapp". Cols: `email, date, storeId, updatedOn, visitType, promiseToPayDate, promiseToPayAmount`. Has no sm/tm/cm/sh — must LEFT JOIN to `okr_data_live` on `SAFE_CAST(storeId AS INT64) = okr.farmer_id` to get rep hierarchy. |
| `pristine_wms_views.invoiced_report` | Invoice/revenue lines. Join to orders via `DisplayOrderCode = CAST(o.unicommerce_id AS STRING)`. Filter `line_status != 'CANCELLED'`, `is_return = 0`. |
| `prod_db_views.order_management_order` | Orders. `owner_id` = partner/store owner (used as partner_id for revenue attribution). `initiating_source LIKE 'B2B%'` scopes to B2B/partner orders. Exclude junk via regex on `status`/`unicommerce_status`: `cancelled|mob_app_unverified|error|payment_pending|edited`. |
| `prod_db_views.wallet_creditwallettransaction` (cwt) | Credit ledger. `transaction_type=0` = debit, `=1` = credit/collection. `reason_id=2` = CL(credit limit) change → excluded from debits. `reason_id=4` = collection reason for credits. `cancelled=0` required. |
| `prod_db_views.wallet_creditwallettransactionreconciliation` | Settlement of debits. Join on `reconciled_for_id = cwt.id`. Sum `amount` per debit → subtract from debit amount to get remaining/outstanding. |
| `prod_db_views.csr_farmer` | Maps `user_id` (wallet) → `farmer_id` (partner). Join: `cf.user_id = cwt.wallet_user_id`. |

## Join / union logic (repeated pattern across all queries)

**Dual-source visits union:** every visit query UNION ALLs `store_visits_v2` + `prod_db_views.visit`, casting `store_id`/`storeId` to STRING, then dedupes:
```
ROW_NUMBER() OVER (PARTITION BY email, store_id, visit_date ORDER BY visit_ts DESC) = 1
```
(dedupes same rep/store/day logged in both apps or duplicated rows; keeps latest timestamp).

**Rep role tagging:** a visit's `visitor_role` = SM/TM/CM/SH by matching visitor email against the store's assigned sm/tm/cm/sh in `okr_data_live` (not by any role field on the visit itself). Non-matching email → `'Other'`.

**Outstanding credit (OCP) calc:** `all_debits` (transaction_type=0, cancelled=0, reason_id≠2) LEFT JOIN `reconciled` (SUM of reconciliation amounts, cancelled=0) → `remaining = amount - settled_amount`, keep where `remaining > 0`. `max_dpd` = days past due date only for **overdue** remaining amounts.

**Block status (partner table):** `HARD_BLOCK` if `ocp > 5000` OR (`ocp > 1000` AND `max_dpd > 30`); else `CLEAR`. Same rule reused as `is_hard_block` in Recommendations.

**Revenue attribution:** `order_management_order.owner_id` = partner_id; only `initiating_source LIKE 'B2B%'` orders count.

## Recommendations engine (weighted scoring, `FIELD_RECOMMENDATIONS_SQL`)

Only ACTIVE partners with a non-vacant SM. Scores 4 signals, `final_score = 0.40*A + 0.30*B + 0.20*C + 0.10*D`:
- **Signal A – Revenue Risk (0–100):** hard-block=100; zero rev in last 30d but had rev prior year=70; new partner (<90d since first order)=50; MoM decline >40%=60; QoQ decline 20–50%=40; has revenue but not visited this month=20.
- **Signal B – Collection Urgency (0–100, capped):** DPD tiers (>90:100, >60:80, >30:60, >0:40) + P2P (promise-to-pay) proximity bonus (due within 7d:+30, already past:+20).
- **Signal C – Visit Gap (0–100):** never visited=100; >60d=80; >30d=50; >14d=20.
- **Signal D – Territory Coverage (0–20):** cluster visit coverage% this month <50=20, <80=10.

**Suppression rule:** a scored partner is dropped unless `final_score > 0` AND (`visited_7d = 0` OR `dpd > 60`) — i.e. skip recently-visited partners unless collections are critically overdue.

**Ranking:** `ROW_NUMBER() OVER (PARTITION BY sm ORDER BY final_score DESC)`, capped at **top 18 per SM**.

**Date logic:** `input_cutoff = from_date - 1 day` (data as of day before the planning window); current/prior month and quarter windows computed off that cutoff (fiscal quarter starts April, `q_month = 4 if month>=4 else 1`).

## Visit Analysis queries

- **Volume** (`VISIT_VOLUME_SQL`): visits by month × source (fieldstar/saathiapp), split by visitor_role.
- **Coverage** (`VISIT_COVERAGE_SQL`): active partners by state/cluster — vacancy flags for SM/TM/CM, `coverage_pct` = % with ≥1 visit in period, `stale_30d` = last visit >30 days ago.
- **Outcomes** (`VISIT_OUTCOMES_SQL`): visited vs not-visited active partners — revenue & collections compared. Dashboard subtitle explicitly notes **selection bias**: high-value partners get visited more, so the visited/not-visited revenue multiplier is not a causal lift estimate.
- **Anomalies** (`VISIT_ANOMALY_SQL`): `ghost_sms`/`ghost_tms` = reps present in OKR hierarchy but never logging any visit; `repeat_store_reps` = reps hitting the same store ≥3× in one week (possible gaming/padding signal).

## Known caveats

1. Two visit-logging apps (fieldstar legacy + saathiapp current) must always be unioned — using only one undercounts visits.
2. Visit dedup key is `(email, store_id, visit_date)` — multiple visits by the same rep to the same store on the same day collapse to 1.
3. `prod_db_views.visit` has no rep-hierarchy columns; role/hierarchy always comes from joining back to `okr_data_live` on `storeId = farmer_id`, so visits to partners no longer in (or never in) `okr_data_live` can't be role-tagged.
4. Rep emails are matched case/whitespace-insensitively (`LOWER(TRIM(...))`) everywhere — raw email fields are not.
5. "VACANT" SM/TM/CM slots must be filtered out explicitly (string prefix check) or they inflate rep-level stats.
6. Revenue and collections are scoped to a date window per query call; OCP/DPD (credit risk) in the main partner table is calculated as of "now" (`CURRENT_DATE()`), while in Recommendations it's as of `input_cutoff` (the day before the selected planning window) — the two are not directly comparable across dashboards/tabs.
5. Outcomes table correlation (visited vs not-visited revenue) is confounded by selection bias per the dashboard's own subtitle — do not present as causal.
7. B2B order filters (`initiating_source LIKE 'B2B%'`, exclusion regex on status/unicommerce_status) must be applied identically wherever revenue is computed, or numbers won't reconcile between Partner table, Outcomes table, and Recommendations.
