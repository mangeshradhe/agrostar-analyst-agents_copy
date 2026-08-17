# Data Access Ledger — Field Visit Priority Logic (SM/TM)

Single source of truth for what's actually been verified against BigQuery vs. assumed from docs. Updated
2026-08-17. Per [[feedback_verify_with_actual_data]] — nothing below is carried forward from a doc without
having been run.

## Verified accessible (query executed successfully, not just read from a doc)

| Table | Verified via |
|---|---|
| `offline_team.okr_data_live` | Span-of-control query, cadence query |
| `offline_team.store_visits_v2` | Day-of-week visit volume query |
| `prod_db_views.visit` | Day-of-week visit volume query |
| `prod_db_views.order_management_order` | Collections/first-order/Friday day-of-week queries |
| `prod_db_views.wallet_creditwallettransaction` | Collections-by-day-of-week query |
| `prod_db_views.wallet_creditwallettransactionreconciliation` | Dry-run count |
| `prod_db_views.csr_farmer` | Dry-run count |
| `prod_db_views.order_management_stocktracking` | POG query (real run) |
| `optimized_reports_data.sale_return_b2c_b2b` | POG query (real run) |
| `optimized_reports_data.zoho_leads` | Stage + Final_sd_date day-of-week query |
| `catalog_views.catalog_management_stocktrackingconfig` | POG query (real run) |
| `pristine_wms_prod_db.item_mst` | POG query (real run) — NOTE: use this dataset, not `pristine_wms_views.item_mst` |
| `pristine_wms_prod_db.return_grn_header` / `return_grn_line` | POG query (real run) |
| `pristine_wms_prod_db.invoiced_report` | Dry-run count (isolated, re-verified after a batching false-positive) |
| `offline_team.FieldStar_Attendance_Request` | Dry-run count |
| `offline_team.master_database_table` | Dry-run count |
| `replica_galaxy_views.institution` | Dry-run count |
| `hackathone2026_dataset.one_view_rofo` | NOT actually verified — dry-run result for this was a batching artifact (see caveat below), needs a clean isolated re-check before trusting either way |

## Confirmed BLOCKED — root cause identified, not guessed

**No access to the `pristine_wms_views` dataset at all** (confirmed via `list_dataset_ids` — it doesn't even
appear in the list of datasets my credentials can see). This is not a table-level permission gap, it's
dataset-level.

This cascades silently into anything that internally joins `pristine_wms_views.item_mst`, even when your own
query never references that dataset directly:
- `revenue_and_growth_team.sku_cat_repo` — confirmed via reading its view definition (`get_table_info`):
  joins `pristine_wms_views.item_mst` for `moq`, plus `bizfin_team.PL_Seeds_variety`, `catalog_views.catalog_management_productcatalog`,
  `revenue_and_growth_team.pl_product_class`, `bizfin_team.subcategory_table`.
- `bizfin_team.PL_Seeds_variety` — confirmed via view definition: joins `pristine_wms_views.item_mst` directly.
- `hackathone2026_dataset.aop_target` — **confirmed via reading its full view definition**: joins
  `revenue_and_growth_team.sku_cat_repo` for `category`/`class`. Same root cause, independently confirmed,
  not inferred by pattern-matching.

**Not yet individually confirmed, but share the same category/class-join pattern per
`hackathon2026_dataset_context.md` and therefore presumed blocked until checked**: `sm_aop_target`,
`target_master_query`, `one_view_rofo`, `one_view_aop`, `one_view_okr`, `one_view_outstanding_and_pg`, the
`one_view_top10_ocp_pg*` trio. Do not treat these as confirmed-blocked or confirmed-accessible — check each
individually before relying on it.

**Workaround used so far (POG query only)**: inlined the `category` classification (`Seeds`/`CP`/`CN`/etc.
from the `AGS-S-`/`AGS-CP-`/... SKU-code prefix) copied verbatim from `sku_cat_repo`'s own view SQL, instead
of joining the blocked view. This is verified-equivalent for `category` only. It does **not** cover:
- `Product_group` — a several-thousand-line hardcoded product-name CASE inside `sku_cat_repo`, not safely
  replicable by hand.
- `class` (Class A/B/C/D, used by `aop_target`) — sourced inside `sku_cat_repo` from a separate table,
  `revenue_and_growth_team.pl_product_class`, not yet checked for direct accessibility.

**Standing rule (user directive, 2026-08-17): whenever `pristine_wms_views` is blocked, query
`pristine_wms_prod_db` directly instead — no escalation needed.** That's already how the POG query above
was made to work (`item_mst`, `return_grn_header`, `return_grn_line` all queried from `pristine_wms_prod_db`).

**RESOLVED (user correction, 2026-08-17): `pristine_wms_prod_db.item_mst` already carries the fields
needed natively — no hand-replication of `sku_cat_repo`'s derivation logic required.**
- `category_code` — governed column, values `SEEDS`/`CP`/`CN`/`HW`/`KIT`/`PKG`/`MKT`/`FB`/`AH`/`SC`/`EC`.
  Verified **more accurate** than the SKU-code-prefix hack used in the first POG run: `category_code='SEEDS'`
  matches 4,511 rows vs. only 4,170 matching the `ags-s-%` prefix pattern (CP: 1,659 vs. 1,525; CN: 840 vs.
  665). **Action item: rerun the POG compliance numbers in `panils_pog_query.md` using `category_code`
  instead of the prefix hack** — the 75.4%/16.6%/1.6% splits were computed on the less-accurate substitute
  and should be treated as provisional until recomputed.
- `sub_sub_product_group` — the `Product_group`-equivalent granularity (user-confirmed), avoiding
  `sku_cat_repo`'s several-thousand-line hardcoded brand/name-matching CASE entirely.
- `moq` — already used, unchanged.
- `class` (Class A/B/C/D, needed by `aop_target`) — confirmed **directly accessible**:
  `revenue_and_growth_team.pl_product_class` (dry-run succeeded).

**Net effect: the Targets axis (`aop_target`/`one_view_rofo`-style logic) is no longer blocked — and
verified end-to-end with real numbers (2026-08-17).** Even simpler than expected:
`optimized_reports_data.aop_offline_online_fy27` (the raw target input table) **already carries its own
native `category_repo` and `class` columns** (`CN`/`CP`/`HW`/`WSF`/`Seeds - FC`/`Seeds - Veg`;
`Class A`-`Class D`) — no join to `sku_cat_repo`, `item_mst`, or `pl_product_class` needed at all for the
Targets axis specifically.

Ran a territory-level YTD (Apr-Aug 2026) gross target vs. actual revenue check first:
**overall attainment 83.0%** (₹374.9 Cr target vs. ₹311.3 Cr actual, 172 territories).

**Corrected (user, 2026-08-17): the Targets axis must run on the CURRENT MONTH, not yearly/YTD** — a
daily-refreshed priority list needs to react to this month's pace, not a slow-moving annual number that
blends in stronger past months and can mask a current shortfall. Reran on August 2026 month-target vs.
month-to-date actual: **overall MTD attainment is only 30.2%, while the month is 54.8% elapsed** (17 of 31
days) — a real, current, actionable shortfall the YTD 83% figure completely hid. Worst this month: Dhule
(still zero actuals), Shahpur 0.9%, Saharanpur 1.6%, Tumkur 2.2%, Araria 3.0%, Gorakhpur 3.3%, Karauli 3.6%,
Suryapet 4.2%. **The Targets axis in the final design uses current-month target vs. MTD actual, not YTD.**

**Bug caught in this verification pass, not shipped**: `aop_offline_online_fy27`'s `gross_*` columns are
stored in **lacs**, not raw rupees — `aop_target`'s own SQL multiplies by 100,000 before use
(`ROUND(month_data.AOP_Target * 100000, 0)`). Missing that scaling produced a nonsense 8,300,000%
"attainment" on the first pass. Any query touching this table's gross/net/return columns must apply the
×100,000 conversion.

**Territory mismatch — resolved (2026-08-17), not a naming bug.** Checked all 3 unmatched territories
individually: one is a `NULL` `revised_territory` on the target side (a data-quality artifact — some
`aop_offline_online_fy27` rows have no territory assigned at all; filter with
`WHERE revised_territory IS NOT NULL`, not a join problem). The other two, Jalna and Dhule, were searched
for under any spelling variant (`LIKE '%JALNA%'`/`'%DHULE%'`) in `sale_return_b2c_b2b.territory` and found
**zero matching rows under any spelling** — these territories genuinely have no B2B invoiced revenue
recorded this FY at all (likely newly-opened or dormant territories), so their 0% attainment is a real
business fact, not a broken join. No further fix needed beyond filtering the NULL row.

## Churned Recovery — resolved (2026-08-17)

User's definition: an INACTIVE partner with outstanding balance still owed. This is **not a missing-data
gap** — it's the exact same OCP/pending-amount logic already used for the Collections signal
([[panils_query_data_sources]]'s `helth` CTE pattern), just filtered to `okr_data_live.status = 'INACTIVE'`
instead of `'ACTIVE'`.

Ran it for real: **3,831 INACTIVE partners carry ₹37.23 Cr in outstanding pending amount** (vs. 9,532 ACTIVE
partners carrying ₹201.1 Cr — the ACTIVE side is already covered by the existing Collections signal; the
INACTIVE ₹37.23 Cr is currently invisible to any existing scoring logic because inactive partners are
excluded from the ACTIVE-only visit lists). Same `wallet_creditwallettransaction` (debits, `cancelled=0`,
`transaction_type=0`, `reason_id NOT IN (2)`) → `csr_farmer` → `okr_data_live` → reconciliation-netting
pattern as Collections, just re-pointed at `status='INACTIVE'`. No new table or gap — reuse the existing
CTE with a different status filter.

## Holiday calendar — no table exists; resolved via domain knowledge, with a caveat

Checked `static_tables` and `static_tables_views` (both fully listed) for anything holiday/calendar-named —
nothing exists. **India's legally-defined "National Holidays" are a fixed, well-known set of exactly 3 per
year**: Republic Day (26 Jan), Independence Day (15 Aug), Gandhi Jayanti (2 Oct) — none of which is
state/company-configurable. For FY27 (Apr 2026-Mar 2027): all 3 fall inside the window (15 Aug 2026,
2 Oct 2026, 26 Jan 2027) → **3 national holidays this financial year**.

**Caveat — flagging clearly, not glossing over it**: this is general/public knowledge, not something pulled
from BigQuery, and it is *not* the same number as "total non-working days a field rep gets." The much
larger set of festival/restricted holidays (Diwali, Holi, regional festivals, etc.) that companies typically
also grant is state- and company-policy-specific, varies by year, and **has no table in any dataset I can
see**. If the capacity math needs the *full* non-working-day count (which is what actually matters for
realistic daily-visit-quota calculations), 3 national holidays alone will understate it — that fuller
calendar is a genuine open gap, unlike this specific 3-day figure.

## Confirmed NOT FOUND — no table located, real data gap (not a permission issue)

- **Returns/complaints ticketing table** — flagged by [[colleague_proposal_field_visit_copilot]] as their
  top unresolved gap; I have not found one either. Only `invoiced_report.is_return` exists (product-level
  return flag, no complaint count/reopen history).
- **Churn / legal-investigation flag** — no table located. `okr_data_live.status` only has ACTIVE/INACTIVE.
- **Working-day / national-holiday calendar table** — not yet located. Needed for precise capacity math
  (Mon-Sat minus holidays). Actual visit data shows Sunday still runs ~15-18% of weekday volume, so this
  needs a real calendar source, not an assumption that Sunday=0 capacity.

## Partially resolved — data exists, a definition decision is still needed

- **Onboarding "about-to-close" criterion** (colleague's doc gap, Section 6): `zoho_leads.stage` has real
  values — `83_Pending for first Order`, `82_Pending for Limit Allocation`, `81_Pending for Cheque Reciept`,
  `3 SD Pending or Incomplete Document`, `Closed Won but Cheque Pending`, etc. The data to define
  "about-to-close" exists now; which stage(s) count is a threshold decision, not a missing-data problem.

## Tool caveat learned this session

Running multiple `execute_sql_readonly` dry-run calls in parallel in one message can return the **same
cached error from one failing call across other calls in the batch** — I hit this twice (bizfin_team +
revenue_and_growth_team both showing the same `item_mst` error simultaneously; `invoiced_report` and
`aop_target` doing the same). Re-running a suspicious dry-run result **alone, sequentially**, is what
confirmed which failures were real (`aop_target` — real, confirmed by reading its SQL) vs. artifacts
(`invoiced_report` — false positive, cleared on retry). Don't trust a parallel-batch dry-run failure without
an isolated re-check.
