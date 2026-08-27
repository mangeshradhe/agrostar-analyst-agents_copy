# FC Workload & Manpower Analysis — GJ, MH, RJ, MP, UP

Prepared 26 Aug 2026. Question: has Fulfilment Centre workload (Inbound + Outbound) grown enough
to justify more manpower — or is a different lever (efficiency, facility consolidation) the real
story? Window: **Apr–Jul FY25-26 (2025) vs Apr–Jul FY26-27 (2026)**, both fully elapsed, so no
censoring issue.

## Scope & definitions (as agreed)

- **FC** = `location_mst.location_type = 'Warehouse'` in GJ/MH/RJ/MP/UP (excludes Manufacturing
  sites and COCO retail Stores, which also carry these state codes).
- **Inbound** = PO GRN (`grn_header.document_type='Purchase Order'` × `grn_line.physical_qty`) +
  B2B Return GRN + B2C Return GRN (`return_grn_header.Channal` × `return_grn_line.qty`).
- **Outbound** = units picked against Sale Orders and Transfer Orders only (`pick_header.source_document`
  = `Sales Order` / `transfer order`) — dispatch itself is excluded; that's first-mile, not FC ops.
- **Two personas**, both sourced from `pick_line` (one row carries the full chain):
  - **Picker** — picking (`qty_picked`/`picked_date`, picker = `pick_header.assign_user`) **and**
    consolidation (`consolidation_qty`/`consolidation_date`/`consolidation_person_id`).
  - **OQC/Biller** — OQC + invoice creation, starts only once consolidation is complete
    (`oqc_good_qty`+`oqc_bad_qty`+`oqc_miss_qty`, `oqc_date`, `oqc_person_id`).
- Efficiency = units per active person-day (median / average / P10-worst / P90-best), not lines —
  tracked separately for the two personas and separately for Sale Order vs Transfer Order.

## Critical data-quality finding — fix before anyone else queries this

**`pick_header` stopped being populated at real volume from December 2025 onward** (dropped from
~25–29K rows/month to under 350/month) while `pick_line` kept accumulating normally (1.5–3.7M
rows/month). Joining `pick_line` → `pick_header` for FY26-27 silently drops ~99% of rows — it
looks like picking "collapsed," but it's a pipeline gap, not reality. **The complete data lives in
`pick_header_arc_main`** (row counts there track `pick_line` correctly through Aug 2026). Every
query in this analysis joins to `pick_header_arc_main`, not `pick_header`. This is now logged in
`agrostar-bigquery-conventions` memory and should be added to the shared methodology doc.

## Facility "closures" — what the data actually shows (not all three are what they were described as)

| Claimed event | What the data shows |
|---|---|
| **MH01/MH02 → PNQ01/PNQ02** | Real, sharp, single-month cutover. MH01 ran 3.7–19K orders/month through Jun-26, dropped to **5 orders** in Jul-26. PNQ01/PNQ02 have **zero history before Jul-26**, opened at ~3K each same month. Same B2C/B2B (`01`/`02`) split as MH — a genuine city relocation (Aurangabad → Pune), landing mid-window. |
| **JDH01/JDH02 → RJ01/RJ02** | **Not a clean cutover.** JDH01 declined gradually from Jan-26 (8,860 orders) through Jul-26 (712) — a slow bleed, not a shutdown event. JDH02 stayed flat. RJ01/RJ02 grew, but nowhere near enough to absorb JDH01's lost volume. Recommend confirming with ops whether a closure order actually exists and when. |
| **NGP01/NGP02 → AKD01/AKD02** | **Already happened before this analysis window.** NGP01 was down to ~1 order/month by **Apr-2025** — before even the FY25-26 baseline. Both periods already reflect AKD as the sole facility; AKD's FY26-27 growth is organic, not migration-driven. |

Because of this, Maharashtra's Outbound/Inbound growth numbers below should be read as
**MH01+MH02+PNQ01+PNQ02 combined** (the underlying query already sums by state, so the mid-window
handoff doesn't distort it) — but per-FC trend lines for MH01 alone will show a fake cliff in July.

## Inbound & Outbound workload, Apr–Jul (units)

| State | Inbound FY25-26 | Inbound FY26-27 | Δ | Outbound FY25-26 | Outbound FY26-27 | Δ |
|---|---:|---:|---:|---:|---:|---:|
| Gujarat | 642,600 | 1,154,324 | **+79.6%** | 628,046 | 750,168 | **+19.4%** |
| Madhya Pradesh | 1,450,847 | 1,813,087 | +25.0% | 1,394,009 | 1,301,839 | **-6.6%** |
| Maharashtra | 2,402,149 | 3,731,551 | **+55.3%** | 2,188,366 | 2,562,242 | +17.1% |
| Rajasthan | 1,483,909 | 1,637,815 | +10.4% | 1,371,480 | 1,236,634 | **-9.8%** |
| Uttar Pradesh | 1,754,486 | 1,899,467 | +8.3% | 1,669,938 | 1,522,118 | **-8.9%** |
| **All 5 states** | 8,733,991 | 10,236,244 | +17.2% | 7,251,839 | 7,373,001 | +1.7% |

**Inbound is growing everywhere (+8% to +80%). Outbound is falling in MP, RJ and UP** and only
growing in GJ and MH (MH's growth is inflated by the MH→PNQ transfer-order handoff noise). This
alone should make you skeptical of a flat "workload is up, hire more" narrative — the two halves
of "workload" are moving in opposite directions in 3 of 5 states.

## Efficiency: median units per active person-day

Filtered strictly to `source_document IN ('Sales Order','transfer order')` — verified against an
unfiltered version that also let in the tiny Purchase Return / RGP Transfer Order volume; the two
versions differ by ≤1 unit per cell, confirming that stray volume is immaterial here.

| State | Picking FY25-26→FY26-27 | Consolidation FY25-26→FY26-27 | OQC/Billing FY25-26→FY26-27 |
|---|---:|---:|---:|
| Gujarat | 355 → 389 (+9.6%) | 318 → 450 (+41.5%) | 608 → 443 (**-27.1%**) |
| Madhya Pradesh | 388 → 383 (-1.3%) | 400 → 427 (+6.8%) | 817 → 897 (+9.8%) |
| Maharashtra | 369 → 328 (**-11.1%**) | 307 → 411 (+33.9%) | 629 → 479 (**-23.8%**) |
| Rajasthan | 285 → 231 (**-18.9%**) | 263 → 255 (-3.0%) | 680 → 316 (**-53.5%**) |
| Uttar Pradesh | 432 → 338 (**-21.8%**) | 419 → 397 (-5.2%) | 760 → 590 (**-22.4%**) |

Active headcount barely moved anywhere (Picking role: GJ 18→19, MP 30→25, MH 72→70, RJ 32→30,
UP 38→40) despite Inbound growing 8–80% in every state — the same roughly-sized crew absorbed
different volumes with sharply different outcomes by state and by stage.

**Consolidation efficiency mostly held or improved. Picking and OQC/Billing efficiency fell in
most states — OQC/Billing fell hardest, especially Rajasthan (-53.5%).**

## Cycle time: where the time actually goes (median minutes, pooled Sale Order + Transfer Order)

| State | Pick-task→picked | Picked→consolidated | Consolidated→OQC done | Total pick→OQC |
|---|---:|---:|---:|---:|
| Gujarat | 28 → 77 (**+175%**) | 5 → 4 | 2 → 2 | 55 → 105 (+91%) |
| Madhya Pradesh | 18 → 20 | 5 → 3 | 1 → 1 | 32 → 35 |
| Maharashtra | 41 → 51 (+24%) | 6 → 11 (+83%) | 2 → 2 | 67 → 84 (+25%) |
| Rajasthan | 16 → 24 (+50%) | 3 → 2 | 2 → 1 | 34 → 36 |
| Uttar Pradesh | 35 → 40 (+14%) | 7 → 5 | 2 → 1 | 60 → 56 (-7%) |

The **consolidated→OQC gap is consistently ~1-2 minutes in both years everywhere** — OQC/billing
itself is fast once work reaches that desk. The bottleneck that's growing is **upstream, in the
pick-task-to-picked stage** (queue time + actual picking combined) — most visible in Gujarat,
where it nearly tripled. This is a different lens from the per-person-day efficiency table above:
that measures how much a person gets done on days they work; this measures how long a line
actually waits to clear. They can (and do) move in different directions.

## Manpower judgment — read this as multiple live hypotheses, not a verdict

The data supports more than one story, and they aren't mutually exclusive:

1. **Volume-driven case (GJ, MH)**: Inbound up sharply, picking cycle time up sharply, OQC/billing
   throughput per person down. Consistent with genuine under-staffing relative to new volume —
   most defensible in Gujarat, where pick-task queue time nearly tripled on a headcount that grew
   by only 1 person.
2. **Efficiency-driven case (RJ, UP, and partly MP)**: Outbound volume is *down* YoY, yet OQC/billing
   throughput per person fell 22-54% and picking throughput fell up to 22%. More volume is not the
   story here — something else is slowing individual output (SKU mix, distance/bin layout, a
   process or system change, attrition of experienced staff, or the JDH decline masking real
   demand pattern shift within Rajasthan). Adding manpower here without diagnosing the efficiency
   drop would be the counter-intuitive move you flagged — throwing bodies at a problem that isn't
   a bodies problem.
3. **Structural-noise case (MH specifically)**: A meaningful slice of Maharashtra's numbers is the
   MH→PNQ facility relocation landing mid-window, not organic demand. Before acting on MH figures,
   separate the relocation's one-off disruption (new-facility ramp-up inefficiency, double-running
   costs) from the underlying trend.
4. **Not-yet-diagnosed case (JDH/RJ specifically)**: the claimed JDH shutdown isn't visible as a
   clean event in the data — it's a slow decline. Before treating RJ manpower needs as "absorbing
   JDH's transferred load," confirm whether that transfer is actually planned/executing or whether
   JDH is simply losing demand for unrelated reasons.

**Recommended next step, not a conclusion**: split each state's efficiency numbers by individual
FC and by month (data already pulled, not yet in this doc) to see whether the decline is a step
change (points to a specific incident/process/system change) or a gradual drift (points to
volume/mix creep or attrition) — that shape is usually what discriminates between "hire" and
"fix a process" as the right lever.

## Caveats

- Inbound uses `physical_qty` (PO GRN) and `qty` (Return GRN) — nominal/received quantities, not
  necessarily "good" stock; a small share is later reclassified as bad/short.
- Outbound/efficiency figures exclude `pick_header.source_document` values outside Sale Order /
  Transfer Order (Purchase Return, RGP Transfer Order — immaterial volume).
- Person-day medians use `APPROX_QUANTILES` (BigQuery approximate percentiles) — fine for this
  scale of comparison, not exact-order-statistic precision.
- FC universe = `location_type='Warehouse'` only; a few legacy/duplicate codes exist in
  `location_mst` (`AGROSTARWAREHOUSE`, `AMD_GJ`, `AUR_MH`, `JBL_MP`, `LKW_UP`, `INTRANSIT`) per the
  known free-text facility-code issue already logged in `TRANSFER_ANALYSIS_METHODOLOGY.md`;
  included here since they carry real volume in the state totals.
- Cycle-time outliers (negative or >20,000/40,000-minute gaps, mostly data-entry artifacts) are
  filtered out of the median calculation.

## Source tables

`pristine_wms_prod_db.{grn_header, grn_line, return_grn_header, return_grn_line, pick_line,
pick_header_arc_main, location_mst}`. See `fc_workload_manpower_queries.sql` for the exact queries.

## Follow-on: interactive dashboard + manpower diagnosis (same day, 26 Aug 2026)

Built out into an interactive artifact — **Fulfilment Pulse**:
https://claude.ai/code/artifact/14cd24a3-8151-41a6-87f9-7575ffc89071 (also saved locally at
`~/Downloads/fulfilment-pulse.html`, self-contained, opens in any browser). FC-wise month-pair
tables (Apr'25 vs Apr'26 etc.), Sale Order vs Transfer Order split throughout, P10 worst-case pace,
per-facility Top 5 focus items, and three additions not in this doc:

1. **Picker utilization** — per picklist, first-scan→last-scan gives active time; summed per person-day
   and compared to the person's observed shift window (first scan to last scan of the day) to get a
   utilization % and a median waiting-time-between-picklists figure. No true clock-in/out data exists
   in this schema (`user_mst` is a 323-row profile table, not a session log), so this is explicitly a
   **lower bound** on idle time. Verified as real (not a timezone/data bug) two ways: distribution
   check on the picked→consolidated gap (no systematic offset), and a manual full-day timeline
   reconstruction for one picker (Bhanu Kumar, AGR01, 2026-06-02) showing genuine mixed bursts and gaps.
2. **Person-level table** — per individual (this year only), pace/pick-duration/share-of-volume/
   utilization, to see whether a facility's workload is carried by a handful of people or genuinely
   shared. Most facilities are top-heavy: at several sites 2 of 6-16 people carry 80%+ of volume.
3. **Manpower diagnosis (2×2 framework)** — replaces "is manpower up or down" with "is this actually a
   headcount problem." Per facility: **core** = fewest people (by volume desc) reaching 80% of total
   volume; **concentrated** if core ≤40% of headcount; **core busy** if core utilization ≥50%. Four
   verdicts: **Bottleneck** (concentrated + busy — genuine hiring case), **Scheduling issue**
   (concentrated + idle core — fix picklist cadence, don't hire), **Team capacity** (shared + busy
   broadly — genuine hiring case), **Not headcount** (shared + idle — no action).

   **Result across 19 facilities with enough people to diagnose: 0 Bottleneck, 2 Team capacity
   (MH01, JBL01 — the only facilities worth prioritizing for real headcount), 5 Scheduling issue
   (AGROSTARWAREHOUSE, AKD01, AGR01, LKO01, LKO02 — fix picklist creation/wave timing, not hiring),
   12 Not headcount.** Headline: the data does not broadly support "hire more people" as a default
   read — most facilities show idle capacity spread across the team rather than a genuine squeeze.
   Caveat: this reads current state, not trajectory — GJ/MH inbound is growing 55-80% YoY (see table
   above), so re-run monthly rather than treating any verdict as fixed, especially for GJ/MH sites.
