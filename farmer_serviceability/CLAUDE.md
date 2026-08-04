# check-serviceability — Agent Index

Farmer serviceability classification (Serviceable / Non Serviceable / Address Problem)
and address-problem resolution for AgroStar B2C farmers. BigQuery project: `agrostar-data`.

## Non-negotiable query rules

- State normalization: `LOWER(TRIM(state)) LIKE 'gujarat%'` style only —
  `REGEXP_CONTAINS` inside a `CASE` throws a BigQuery syntax error.
- `clevertap_views.app_launched`: `farmer_id`, `latitude`, `longitude` are declared
  INTEGER/FLOAT in schema metadata but are actually STRING at query time (stale
  cached schema) — always `SAFE_CAST(... AS INT64/FLOAT64)` before comparing or
  you get `No matching signature for operator != for argument types: STRING, INT64`.
- `csr_villageaddress` archived handling: `is_archived=0` → use directly;
  `is_archived=1 AND replaced_by_id IS NOT NULL` → resolve via replacement;
  `is_archived=1 AND replaced_by_id IS NULL` → unresolved.
- `assignment_pickuplocationfranchisemapping` has no `is_active` column — the
  real gate is `assignment_pickuplocation.is_active`.
- Pincode is always exact-matched (6-digit string) — never fuzzy/phonetic,
  anywhere in this project.

## Standing caveats

- "Address Problem" bucket ≠ unserviceable area — mostly spelling/typo mismatches
  against the village master. See `01-address-problem-fuzzy-resolution.md`.
- Fuzzy-match output is a **proposed match list for review**, never auto-applied
  to the Serviceable/Non-Serviceable bucket.

## File map

- `serviceability_logic.md` — 3-bucket serviceability classification logic.
- `serviceability_query.sql` — reusable query for the above.
- `address_problem_export_query.sql` — exports all Address-Problem farmers' raw
  addresses (one row per distinct address, not per farmer) + GPS home-point.
- `01-address-problem-fuzzy-resolution.md` — methodology for resolving
  Address-Problem addresses via GPS + fuzzy/phonetic matching vs. village master.
- `fuzzy_village_match.py` — the 5-gate matching script; local CSVs only, no
  BigQuery calls once village master + address list are downloaded.

## Open commitments

- Review sample of Gate 5 matches (224 rows, loosest gate) and the 6,294
  unresolved rows — not yet done as of 2026-08-04.
