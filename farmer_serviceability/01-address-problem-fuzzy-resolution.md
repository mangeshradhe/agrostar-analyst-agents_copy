# Address-Problem Resolution via Fuzzy/Phonetic Matching

**Date:** 2026-08-04
**Goal:** Recover a real village-master match for farmers stuck in the "Address
Problem" serviceability bucket (see `serviceability_logic.md`), on the
hypothesis that most of them are typo/spelling mismatches, not genuine
unserviceable locations — supported by the observation that taluka names
already match the village master correctly in the large majority of cases.

---

## 1. Inputs

1. **Address-Problem list** — `address_problem_export_query.sql`, run in
   BigQuery, downloaded as `TAM address problem all states - results-20260804-123823.csv`.
   One row per **distinct address** per farmer (not one row per farmer — a
   farmer with 3 different problem addresses gets 3 rows). 67,801 rows total.
   Fields: `farmer_id, village, taluka, district, state, pincode, latitude, longitude`.
   ~52.3% of rows have no GPS (`latitude`/`longitude` blank).

2. **Village master** — full mirror of `static_tables.csr_villageaddress`
   (left-joined to `static_tables.csr_zone` for zone name), filtered to the
   same 5 states, **archived and non-archived both included**. Exported via
   `bq` CLI (too large — ~652k rows — to pull through a chat tool call, so
   redirected straight to a local file instead) to `~/Downloads/village_master.csv`.
   Fields: `id, state, district, taluka, village, pin_code, is_archived,
   replaced_by_id, zone_id, zone_name, latitude, longitude`.
   Row counts: UP 262,772 / MP 123,932 / RJ 105,244 / MH 94,683 / GJ 65,509 =
   652,140 total (282,593 non-archived + 369,547 archived).

Both files live in `~/Downloads/` (not committed to this repo — regenerate
from `address_problem_export_query.sql` and the Step-0 `bq` export below if
they go missing).

## 2. Two query bugs fixed along the way

- **`address_problem_export_query.sql` originally collapsed to one row per
  farmer** (their single most-recent address). Changed the `rep_address` CTE's
  `ROW_NUMBER()` partition key from `farmer_id` alone to
  `farmer_id + normalized village/taluka/district/pincode/state`, so every
  distinct address survives — a farmer with 3 different problem addresses now
  gets 3 rows.
- **`clevertap_views.app_launched.latitude`/`.longitude`** are declared FLOAT
  in BigQuery's schema metadata but are actually **STRING at query time**
  (stale cached view metadata — same issue the query already had a comment
  about for `farmer_id`). Comparing `latitude != 0` directly threw
  `No matching signature for operator != for argument types: STRING, INT64`.
  Fixed by wrapping every use in `SAFE_CAST(... AS FLOAT64)`.

## 3. Matching approach: 5-gate waterfall

For each address-problem row, try gates in order, **stop at the first
confident match**. Nothing here is queried from BigQuery again — everything
runs locally against the two downloaded CSVs (`fuzzy_village_match.py`,
runtime ~80s for all 67,801 rows).

| Gate | Fields used | Match type | Notes |
|---|---|---|---|
| 1 | GPS → nearest village | Haversine distance | Only for rows with lat/long (~48%). Accept if ≤5km. **Actual distance is always recorded**, even when it doesn't clear the threshold, for reviewer reference. |
| 2 | village + taluka + district | Fuzzy + phonetic | Composite key, blocked by exact (state, taluka, district), falling back to (state, taluka) then whole-state if that block is empty. |
| 3 | village + taluka | Fuzzy + phonetic | Same blocking, drops district — handles cases where district was renamed/split (e.g. Junagadh → Gir Somnath) but taluka is still right. |
| 4 | village + district | Fuzzy + phonetic | **Pincode required exact match** — blocks candidates to just that pincode first (small, precise pool). |
| 5 | village only | Fuzzy + phonetic | Same exact-pincode blocking, loosest text requirement. |

If no gate accepts, the row is left `unresolved`.

**Fuzzy scoring:** RapidFuzz `token_sort_ratio` on the composite key.
**Phonetic scoring:** Jellyfish Metaphone code comparison.
**Accept rule (Gates 2–5):** composite score ≥ 90, **or** phonetic codes match
and composite score ≥ 75 — **and** (see guardrail below) the village name
alone must independently score ≥ 75.

## 4. Village-name normalization (before any comparison)

Applies to **village only** — taluka/district came back clean from frequency
mining (no filler-word pattern), so they only get basic lowercase/trim/
punctuation cleanup. Pincode is never touched by any of this — always a
straight 6-digit string equality check.

**Stripped as noise** (found by frequency-mining the actual 67,801 village
strings, not guessed):
- Punctuation/junk: `* . , [ ] | @ / _ ? { } \` + ; &`
- Conjunction characters `( ) -` → replaced with a space (e.g.
  `"vasan(kuda)"` → `"vasan kuda"`)
- Hindi genitive filler: `ka`, `ki`, `ke`
- Postal/admin fragments `b`, `o`, `pr` — confirmed by example rows like
  `"Saijpur B.O"` (India Post "Branch Office" suffix) and
  `"Bambrud Pr. Bahal"` (parent-unit/Parganah reference) — neither is part of
  the actual village name.

**Canonicalized, not stripped** (spelling/abbreviation variants of the *same*
real distinguishing suffix, mapped to one form so they compare equal to each
other while staying distinct from unrelated villages):

| Variants seen in data | → Canonical |
|---|---|
| `bk`, `budruk`, `buzurg` | `budruk` |
| `kh`, `khurd`, `k` | `khurd` |
| `rural`, `gramin` | `rural` |
| `city`, `urban` | `urban` |

Evidence for these pairs: the same base village literally appears both ways
in the data — e.g. `"Moyali Kh"` and `"Moyali Khurd"`, `"Vadgaon Budruk"` and
`"...Bk."`. Without canonicalizing, these would be treated as different
villages by the fuzzy scorer.

**Deliberately left untouched** — these look like generic suffixes but are
either confirmed or suspected to be real village-distinguishing markers, so
stripping them risked silently merging genuinely different villages:
- `nagla` — most frequent single token in the whole file (1,435 occurrences);
  it's the shared root of thousands of *distinct* UP villages ("Nagla Ram",
  "Nagla Shyam", ...), not filler.
- `kalan`, `khas` — twin-village distinguishers (Kalan = bigger, Khas = main),
  same pattern as Budruk/Khurd but no abbreviation form was found for these.
- `dhani`, `pur`/`pura`/`purwa`/`pure`, `khera`/`kheda`/`gaon`/`nagar` —
  generic settlement-type suffixes; judged safe to leave in the comparison
  and let fuzzy scoring absorb minor variation, rather than risk over-stripping.

## 5. Guardrail added after testing (important)

Initial testing surfaced a real precision bug: when Gates 2–4 block on an
exact (or near-exact) taluka/district/pincode, that already-matching text
becomes part of the composite string being scored — and a long matching
taluka+district can "carry" a genuinely wrong village name over the 90-score
threshold. Concrete example: `'Sithol' → 'Siloj'` scored 90.2 composite, but
village-name-only similarity was just 54.5%.

Found by testing 64 of 308 (~21%) sampled matches had this inflation pattern.

**Fix:** added a second requirement — the village name alone must score ≥ 75
on its own, independent of the composite score. Re-running the Sithol case
after the fix: Gate 2 correctly rejected it, and the row fell through to
Gate 4, which found the true match ("Sithol" = "Sithol", pincode exact, 100%
score). This is the current behavior of `fuzzy_village_match.py`.

## 6. Results (full run, 67,801 rows)

| Gate | Rows | % |
|---|---|---|
| 1 — GPS ≤5km | 28,127 | 41.5% |
| 2 — village+taluka+district | 21,182 | 31.2% |
| 3 — village+taluka | 8,107 | 12.0% |
| 4 — village+district+pincode | 3,868 | 5.7% |
| 5 — village+pincode | 224 | 0.3% |
| **Unresolved** | **6,294** | **9.3%** |

**~90.7% resolved.** Output: `~/Downloads/address_problem_resolved.csv` —
one row per input address, with original fields plus `matched_gate`,
matched village/taluka/district/state/pincode/zone_id/zone_name/
is_archived/replaced_by_id, `fuzzy_score`, `phonetic_match`, `gps_distance_km`.

## 7. Caveats / what this is not

- This is a **proposed match list for review** — nothing here writes back to
  the Serviceable/Non-Serviceable bucket automatically.
- Matched rows where `matched_is_archived = 1` point at an archived village
  master row (sometimes with a `replaced_by_id`) — the script reports this
  as-is, it does not auto-resolve to the replacement's current details.
- Gate 5 (224 rows) and the 6,294 unresolved rows are the least-validated —
  see Open Commitments in `README.md`.
- GPS-based Gate-1 matches can pick a village with a very different name from
  the declared one (it only cares about proximity) — expected, not a bug, but
  worth a human glance on ones with a large name-similarity gap.
