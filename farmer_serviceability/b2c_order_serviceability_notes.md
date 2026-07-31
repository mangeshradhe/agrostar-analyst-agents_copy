# B2C Order-Based Farmer Serviceability — Session Notes (2026-07-31)

Companion to `b2c_order_serviceability_query.sql` in this folder. See
`serviceability_logic.md` / `serviceability_query.sql` (copied into this folder
too) for the original **cohort-based** version (farmer universe = `farmer_profile_master`).
This document covers the **order-based** version (farmer universe = actual B2C
orders in `order_management_order`, Apr 2023 → date) and everything found while
building it up over one session.

---

## What's different from the cohort-based query

- Farmer universe = distinct `farmer_id` (via `csr_shippingaddress.farmer_id`)
  behind every qualifying B2C order, not `farmer_profile_master`.
- B2C filter (from `dvs-analyst.md` skill, `.claude/commands/`):
  ```sql
  LOWER(initiating_source) NOT LIKE 'b2b%'
  AND unicommerce_status NOT IN ('FUTURE ORDER','CANCELLED','DISPUTED_ADDRESS')
  AND status NOT IN ('MOB_APP_UNVERIFIED')
  AND status NOT LIKE 'edited%'
  AND unicommerce_status NOT LIKE 'edited%'
  ```
- Additional exclusion decided this session: `order_type NOT IN
  ('COCO','STOCK_TRANSFER_ORDER','RETURN_ORDER','OFFLINE-ORDER')` — these
  aren't genuine customer purchases (stock transfers, dealer credit, etc.).
- Everything else (3-tier VM resolution, active LMD coverage join, 3-bucket
  classification) is identical logic to `serviceability_query.sql`.

## Results snapshot — Apr 2023 to 2026-07-31 (frozen at time of writing)

**Order-type exclusion impact:** COCO/STOCK_TRANSFER_ORDER/RETURN_ORDER/
OFFLINE-ORDER **did not exist at all** in this order set before FY2027 (Apr
2026) — excluding them changes nothing for FY2024–FY2026 and only trims ~1.5%
of orders / ~9% of GMV in the FY2027 partial year, concentrated in Maharashtra.

**Match-rate trend (address → village-master, Tier-1 only) is consistent
across every unit of measure** (orders, GMV, unique addresses, unique
farmers): flat ~83% for FY2024–25, up to ~87–88% in FY2026, then a jump to
~97–98% in the FY2027 partial year (Apr–Jul 2026) — uniformly across all 5
states. **Cause not confirmed** — see caveat below.

**Full lifetime 3-bucket serviceability** (Tier-1/2/3 resolution + active LMD
coverage, one classification per farmer across their whole order history):

| State | Serviceable | Non Serviceable | Address Problem | Farmers | GMV (₹Cr) |
|---|---|---|---|---|---|
| Gujarat | 69% | 28% | 3% | 133,990 | 113.6 |
| Madhya Pradesh | 69% | 28% | 3% | 96,634 | 78.7 |
| Maharashtra | 73% | 19% | 8% | 260,981 | 178.4 |
| Rajasthan | 73% | 22% | 5% | 157,227 | 161.4 |
| Uttar Pradesh | 55% | 15% | 30% | 108,099 | 85.4 |
| **All states** | **69%** | **22%** | **9%** | **756,931** | **617.5** |

GMV-weighted split (all states): Serviceable 76%, Non Serviceable 18%, Address
Problem 6% — Serviceable farmers skew somewhat higher-value.

**Address Problem GPS-recovery investigation (67,868 farmers):**
- 56% (38,293) have at least one `clevertap_views.app_launched` event.
- Only 42% (28,611) have a valid, non-zero lat/long.
- ~44% (29,575) never launched the app at all — likely orders placed entirely
  by CSR/store on the farmer's behalf; no GPS recoverable this way for them.
- Planned next steps (not yet executed past the feasibility check): round
  coordinates to ~4 decimals → take each farmer's most-frequent bucket as a
  "home point" with a confidence score → `ST_DWITHIN`/`ST_DISTANCE` nearest-
  active-village lookup **within the farmer's own state**, capped at 20km →
  re-run Tier-1 + coverage on the corrected village.

---

## Known Data Caveats (read before reusing any of this)

1. **`clevertap_views.app_launched.farmer_id` is declared `INTEGER` in
   BigQuery's schema metadata but the underlying data is actually `STRING`.**
   A direct equality join against a real `INTEGER` column (e.g.
   `csr_shippingaddress.farmer_id`) throws:
   `No matching signature for operator = for argument types: STRING, INT64`.
   **Always wrap both sides in `SAFE_CAST(... AS INT64)` before joining** —
   confirmed necessary; the declared schema type cannot be trusted here.

2. **Village-master `replaced_by_id` chains can jump to a completely
   different district/taluka**, not just a renamed/merged version of the
   same place. Example found: farmer_id `11114312`'s shipping address had
   raw village `"kota"` (= district name, likely a lazy/placeholder entry) in
   Kota district, pincode 324001 → matched archived VM id `262214` →
   `replaced_by_id` → VM id `991116` = **"Googal Kota", Neemrana taluka,
   Kotputli-Behror district** — over 100km away, in a different part of
   Rajasthan. Because that merged village happened to have active LMD
   coverage, the farmer was flagged **Serviceable** purely on this artifact,
   while their real village (Sangod, Kota district) has zero coverage. This
   is a genuine false-positive risk baked into Tier 1/2 resolution — worth an
   occasional audit of `replaced_by_id` pairs for large geographic jumps.

3. **Uttar Pradesh is a persistent, multi-year outlier** in address-match
   quality — 54–61% matched vs. 83–96% for the other four states, across
   FY2024–FY2026, at every granularity (orders, GMV, unique addresses, unique
   farmers). It also has by far the highest "Archived – Dead End" and "No
   Match" shares. **However, ~95% of UP's "No Match" farmers have a valid,
   known taluka+district** — meaning the geography is basically right; the
   failure is specifically at the village-name/pincode text level (typos,
   merged words, stray whitespace/characters), not genuinely unmapped areas.
   Likely fixable with a Tier-2-style fallback or fuzzy village-name
   matching rather than needing new village-master coverage.

4. **A system-wide, all-state jump in match quality starts FY2027 (Apr
   2026 onward)** — order-level matched % goes from 83–87% (FY24–26) to
   ~97% uniformly across all 5 states, and the mix flips from mostly
   "Archived – Replaced" to mostly "Active". **This is unconfirmed/
   unexplained as of this writing** — before treating it as a genuine data-
   quality improvement, check with whoever owns village-master data whether
   a cleanup/consolidation ran recently, or whether the address-capture UI
   changed.

5. **`csr_shippingaddress.latitude`/`longitude` is ~99.5% NULL** — never use
   it for distance/geolocation (documented in `dvs-analyst.md`). Use
   `static_tables_views.csr_villageaddress` (filtered `is_archived = 0`)
   instead — confirmed **~99%+ of active villages have valid lat/long** in
   all 5 states (e.g. UP: 109,559 active villages, 109,238 with coordinates).

6. **BigQuery has no `RADIANS()`** — the `dvs-analyst.md` skill works around
   this with manual trig (`x * 3.14159265358979 / 180`). For new work,
   prefer native `ST_GEOGPOINT()` / `ST_DISTANCE()` / `ST_DWITHIN()` instead —
   simpler, and lets BigQuery's spatial join optimizer prune candidates
   before computing exact distance (critical for nearest-village lookups at
   scale — a naive cross join against ~100K+ villages per state is
   prohibitively expensive without this).

7. **Original serviceability query's `filters` CTE only handles one state per
   run** — this order-based version processes all 5 states in a single pass
   instead (no `filters` CTE), which is why it needed its own `farmer_state`
   CTE to pick one representative state per farmer for reporting (best-tier
   address wins; alphabetical tiebreak).
