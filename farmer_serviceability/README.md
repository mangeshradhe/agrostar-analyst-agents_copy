# check-serviceability

Farmer serviceability classification and address-quality resolution for
AgroStar's B2C farmer base across Gujarat, Maharashtra, Rajasthan, Madhya
Pradesh, and Uttar Pradesh. Snapshot dates noted per file below.

**Start here:** `01-address-problem-fuzzy-resolution.md` for the address-matching
methodology; `serviceability_logic.md` for the underlying 3-bucket classification.

## Files

| File | Contents |
|---|---|
| `serviceability_logic.md` | Logic reference for the Serviceable / Non Serviceable / Address Problem 3-bucket classification, incl. known gotchas (2026-07-20). |
| `serviceability_query.sql` | Runnable query implementing the above. |
| `address_problem_export_query.sql` | Exports every Address-Problem farmer's distinct raw addresses + GPS home-point, all 5 states (2026-08-04 snapshot, 67,801 address rows). |
| `address_problem_farmer_latlong.csv` | Earlier (2026-07-31) farmer-level export — one row per farmer, superseded by the per-address query above. |
| `01-address-problem-fuzzy-resolution.md` | Methodology: how Address-Problem addresses were resolved against the village master using GPS + fuzzy/phonetic matching. |
| `fuzzy_village_match.py` | The matching script (5-gate cascade); runs entirely offline against downloaded CSVs (`~/Downloads/TAM address problem all states - results-20260804-123823.csv`, `~/Downloads/village_master.csv`). |

## Headline conclusions

- **2026-08-04** — Of 67,801 Address-Problem address rows (5 states), ~90.7%
  (61,508) resolved to a proposed correct village via a 5-gate GPS +
  fuzzy/phonetic matching cascade; 9.3% (6,294) remain genuinely unresolved.
  See `01-address-problem-fuzzy-resolution.md` for full method and caveats.

## Open commitments

- Spot-check Gate 5 matches (224 rows) and the unresolved 6,294 rows before
  treating results as final — not yet done as of 2026-08-04.
