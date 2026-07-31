# Farmer Serviceability — Logic Reference

**Serviceable** = LMD (last-mile delivery) logistics team has declared active
coverage for the farmer's location. It is independent of DVS partner presence —
a farmer can be serviceable (LMD can reach them) but still route to FC because
no DVS store exists in their taluka.

Query template: `serviceability_query.sql` in this folder — change the
`filters` CTE (state, farmer_type) and it runs end to end.

---

## Step 1 — Farmer universe

Source: `dwh_views.farmer_profile_master`, filtered `is_archived = 0`.

- `farmer_type = 'transacting'` if `first_transaction_date IS NOT NULL`, else `'non_transacting'`.
- `cohort_fy` = FY of first transaction (transacting) or profile registration (non-transacting).
- State normalized via `LIKE` prefix match (`'rajas%'` → `'rajasthan'` etc.) — never `REGEXP_CONTAINS`
  inside a `CASE`, it throws a BigQuery syntax error.

## Step 2 — Address pool (per farmer, can be multiple rows)

Two sources, `UNION DISTINCT`ed together — a farmer's serviceability is
evaluated across **every address they've ever used**, not just one:

1. `prod_db_views.csr_shippingaddress` — all saved shipping addresses via
   `farmer_id` FK. No `is_archived` filter — archived just means hidden from
   UI, not that the farmer moved away.
2. `farmer_profile_master` registration address — independently entered,
   sometimes the only address a non-transacting farmer has.

Village must be non-null, not a garbage value (`na`/`nil`/`unknown`/...), and
`LENGTH >= 2`.

## Step 3 — 3-Tier village-master (VM) resolution

Raw addresses are free-text (CSR or farmer typed). Coverage tables are keyed
against `static_tables_views.csr_villageaddress` (LGD canonical). A strict
5-field match silently drops addresses with an empty/wrong pincode — so
resolution falls back in tiers:

| Tier | Match fields | When used | Confidence |
|---|---|---|---|
| 1 | village + taluka + district + pincode + state | Always tried first | High |
| 2 | village + taluka + district + state (**no pincode**) | Tier 1 failed | High — VM supplies the canonical pincode |
| 3 | Raw address as-is | Tiers 1 & 2 failed | Best effort — free-text noise |

Rules:
- Tier 1/2 archived-record handling: `is_archived = 0` → use directly;
  `is_archived = 1 AND replaced_by_id IS NOT NULL` → resolve to the
  replacement row; `is_archived = 1 AND replaced_by_id IS NULL` → falls
  through to the next tier.
- Tier 2 keeps **all** VM matches, not just the top one — any one of them
  hitting coverage is enough to mark the farmer Serviceable.
- Every address ends up "resolved" at exactly one tier (1, 2, or 3) with a
  `canonical_village` + `canonical_pincode`.

## Step 4 — Active LMD coverage

Chain (all in `prod_agroex_db_views` except the last two, which are
`prod_db_views`):

```
assignment_deliverycoverage (coverage_type, village, pincode)
  → assignment_deliveryarea            da.id = dc.delivery_area_id
  → assignment_pickuplocationfranchisemapping   apl.id = da.pickuplocation_franchise_mapping_id
  → assignment_franchise               asf.id = apl.franchise_id
  → assignment_pickuplocation          pl.id = apl.pickuplocation_id
  → delivery_franchise                 df.id = asf.franchise_id
  → delivery_userinformation           ui.username = df.user_info_id
```

**All 4 active flags required:** `da.is_active=1 AND dc.is_active=1 AND
asf.is_active=1 AND pl.is_active=1`. The pickup hub (`pl.is_active`) is the
real gate — franchise + delivery area can show active while the physical
pickup hub is deactivated, meaning zero real operations. Skipping this filter
has inflated serviceable counts 5–14x in past validation.

`coverage_type` has 3 grains, each with its own match key:

| Coverage type | Match key | Why |
|---|---|---|
| `village` | village + pincode | Pincode is specific enough; adding taluka/state over-constrains and causes false negatives from spelling drift |
| `taluka` | state + district + taluka | District is mandatory — the same taluka name repeats across districts |
| `pincode` | state + pincode | State disambiguates rare cross-state pincode collisions |

Match against coverage using **UNION**, not `LEFT JOIN` — one village
routinely has both a village-level and taluka-level coverage row, and a
`LEFT JOIN` double/triple-counts.

## Step 5 — 3-bucket classification per farmer

```
Any address (any tier) hits coverage?
  YES → Serviceable
  NO  → Was at least one address resolved at Tier 1 or Tier 2 (VM match found)?
          YES → Non Serviceable   (clean address, genuine coverage gap)
          NO  → Address Problem   (address too dirty to resolve at all)
```

This is a **farmer-level, lifetime** flag — computed once across all
addresses a farmer has ever used, not per order or per year.

## Step 6 — Best LMD partner (for display)

When a farmer has multiple coverage hits (different addresses, different
grains), pick one deterministically:
`ORDER BY cov_priority ASC (village=1, taluka=2, pincode=3), tier ASC, lmd_partner_name ASC`.

---

## Known gotchas (validated in production)

- **`csr_shippingaddress.pin_code` is frequently empty/wrong** vs. the
  LGD-canonical pincode — this is exactly why Tier 2 exists. In UP alone,
  10,112 farmers were wrongly non-serviceable before Tier 2 was added.
- **State normalization** must use `LOWER(TRIM(state)) LIKE 'gujarat%'` style
  matching — `REGEXP_CONTAINS` with a raw string literal inside a `CASE`
  block throws `"Expected keyword END but got identifier"`.
- **`assignment_pickuplocationfranchisemapping` has no `is_active` column** —
  don't filter on it; the gate is `assignment_pickuplocation.is_active`.
- **Join direction**: `apl.id = da.pickuplocation_franchise_mapping_id`, not
  the reverse — `da.id` is not a column on the mapping table.
- Low serviceability % on a list of **expansion-target villages** is the
  correct answer, not a query bug — those lists are villages picked
  specifically because they're outside current LMD coverage.
- **"NA/New Customer" in an order-time sheet ≠ non-serviceable.** That label
  usually means the routing engine had no `PromisedTAT` record for a
  first-time order — this pure geography check can still resolve the farmer
  to a serviceable LMD independent of what happened at order time.
