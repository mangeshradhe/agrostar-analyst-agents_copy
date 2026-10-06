# TO Lead-Time Analysis — Andhra Pradesh / Telangana / Karnataka destinations (started 28 Sep 2026)

## Question

AP, Telangana and Karnataka are complaining that inbound stock transfers arrive late. Break the
lead time into stages — approval, warehouse processing, dispatch, in-transit, destination
putaway — to find where the lag actually sits, and check whether it depends on which state the
stock is coming FROM (distance/lane effect).

Scope: System TOs only (Adhoc / UF case / Budget balancing — the three types Darpan named),
landing at the three FCs that actually sit in these states: **GNT01** (Guntur, AP), **HYD01**
(Hyderabad, Telangana), **BAY01** (Ballari, Karnataka). Jan 2026 – date.

## Method & source tables

Five timestamps, chained on `transfer_no`:

| Stage | Table | Fields |
|---|---|---|
| Request → Approval | `catalog_views.catalog_management_transferorders` | `created_on` → `action_on` (filtered `action_status='APPROVED'`) |
| Warehouse processing (approval → invoice raised) | `transfer_header` (created_on ≈ `action_on`, confirmed same-timestamp) → `invoice_transfer_header.CreatedOn` | |
| Dispatch hold (invoice raised → vehicle actually leaves) | `invoice_transfer_header.CreatedOn` → `DispatchedOn` | |
| In-transit (dispatch → destination starts putaway) | `invoice_transfer_header.DispatchedOn` → `putaway_header.created_on` | filtered `document_type='Transfer Order'`, `document_no=transfer_no` |
| Destination putaway (start → complete, stock live) | `putaway_header.created_on` → `completed_on` | |

Destination state resolved off `to_facility` directly (only 3 FCs match, no ambiguity). Origin
state resolved by joining `from_facility` to `location_mst` (`UPPER(TRIM())` both sides — same
free-text facility-code issue as the rest of this project).

**Data-quality notes (checked this session, new to this project):**
- **There is no picklist timestamp for TOs.** `pick_header`/`pick_header_arc_main` only ever
  carries `work_type IN ('B2B','B2C')` — that's sale-order picking, not transfer picking. So
  "warehouse processing" below is one blended bucket (pick + pack + invoice prep), not split
  further — a real gap if the warehouse-side diagnosis needs to go deeper.
- **There is no gate-entry/arrival timestamp for TOs either.** `gate_entry` and `grn_header` are
  100% `document_type='Purchase Order'` — vendor-inbound only. `putaway_header` (`document_type=
  'Transfer Order'`) is the only destination-side event for a transfer, so **"in-transit" below
  = dispatch → putaway start, which blends true road time with however long the truck sits at
  the destination dock before someone starts putaway.** Can't separate the two with current
  data — flag this if it becomes the next thing to fix.
- No negative durations at any stage (checked) — join/timestamp logic is clean.
- **September is partial/censored** (per this project's cohort-discipline rule) — only 66-80% of
  Sep requests have a completed putaway as of 28 Sep; Sep numbers below are directional only.

Queries: `09_to_lead_time_queries.sql`.

## Headline finding: in-transit is the lag, by a wide margin

Stage medians/P90 (hours), System TOs to the 3 destinations, Jan–Sep 2026:

| Stage | Median | P90 |
|---|---|---|
| Approval (request → approved) | 4–27h | 28–160h |
| Warehouse processing (approved → invoiced) | 22–28h | 75–166h |
| Dispatch hold (invoiced → dispatched) | 23–28h | 50–76h |
| **In-transit (dispatched → putaway starts)** | **118–164h (5–7 days)** | **170–330h (7–14 days)** |
| Destination putaway (start → complete) | ~0h | negligible |

In-transit alone is 4-6x bigger than approval + warehouse + dispatch-hold combined, at every one
of the three destinations and across all three TO types. This holds across the whole
distribution, not just the tail — even the 10th percentile is 67 hours (2.8 days). **The
warehouse-process and approval stages are not where the complaint should be aimed; the road/
dispatch-cadence side is.**

Total lead time (request raised → stock live at destination), median: **196–293 hours (8–12
days)**, P90 **326–475 hours (14–20 days)**.

By TO type (median total lead time): **Budget-balancing is consistently ~3-4 days slower than
UF** at all three destinations (Budget 264-293h / 11-12 days vs UF 197-215h / 8-9 days vs Adhoc
213-241h / 9-10 days, small N). Same relative gap holds for in-transit alone. Worth asking
logistics whether Budget-balancing shipments get lower dispatch priority than UF ones.

## Lane view: does distance/origin state explain it?

Yes, directionally — but not cleanly by raw distance. In-transit median (days), by origin state,
≥10 TOs, sorted slowest→fastest per destination:

| To GNT01 (AP) | | To HYD01 (Telangana) | | To BAY01 (Karnataka) | |
|---|---|---|---|---|---|
| Rajasthan | 9.8d | Rajasthan | 11.7d | Rajasthan | 8.9d |
| Bihar | 7.7d | Bihar | 8.1d | Bihar | 9.0d |
| UP | 6.8d | Gujarat | 7.8d | UP | 8.7d |
| MP | 6.8d | MP | 7.3d | MP | 7.7d |
| Karnataka | 5.9d | UP | 7.2d | Gujarat | 6.6d |
| Haryana | 5.1d | Karnataka | 6.1d | Andhra Pradesh | 5.9d |
| Chhattisgarh | 4.9d | Haryana | 5.9d | Maharashtra | 5.7d |
| Maharashtra | 4.8d | Chhattisgarh | 5.4d | Chhattisgarh | 5.0d |
| Gujarat | 4.0d | Maharashtra | 5.4d | Telangana | 4.7d |
| **Telangana** | **2.8d** | **Andhra Pradesh** | **3.1d** | | |

**Own-region moves are fastest** (Telangana→GNT01: 2.8 days; AP→HYD01: 3.1 days) — that part
matches the "closer = faster" intuition directly. But **Rajasthan is the worst lane into all
three states by a clear margin (9-12 days)**, followed by Bihar and UP — and these are *not*
simply "furthest = slowest": Gujarat and Maharashtra, roughly comparable air-distance to south
India, run 2-4 days faster than Rajasthan/UP/Bihar into the same destinations. `TransportMethod`
on `invoice_transfer_header` doesn't explain it either — it's 64% blank and the rest is uniformly
"Road" (no rail lanes to compare against). Reads more like a **shipment-frequency/consolidation
problem** than a pure road-distance problem — Rajasthan/UP/Bihar→South lanes are lower volume
(see counts in the SQL output) and likely wait longer to fill a truck. Not confirmed — worth
checking with logistics whether these lanes run on a fixed weekly schedule vs. load-triggered
dispatch.

## It's also getting worse, and it lines up with the Budget-balancing volume ramp

Monthly total-lead-time median (days), all 3 destinations:

| Month | BAY01 | GNT01 | HYD01 | Combined monthly volume |
|---|---|---|---|---|
| Jan | 8.9 | 5.9 | 7.3 | ~100 |
| Feb | 6.9 | 8.1 | 7.0 | ~160 |
| Mar | 7.0 | 7.0 | 7.8 | ~120 |
| Apr | 7.6 | 6.7 | 7.8 | ~80 |
| May | 8.0 | 9.7 | 10.1 | ~65 |
| Jun | 8.9 | 7.8 | 10.0 | ~150 |
| **Jul** | 10.9 | 11.6 | 9.9 | **~650** (Budget-balancing launches) |
| **Aug** | 12.6 | 12.3 | 10.9 | **~900** |
| Sep (partial, censored) | 9.0 | 8.0 | 11.8 | ~1035 |

Volume to these three destinations jumped **~5-6x** the moment Budget-balancing launched in
July, and total lead time rose in step — from a 6-8 day baseline (Jan-Jun) to 10-13 days in
Jul-Aug. This reads as a **capacity/throughput story, not a process-design story**: the pipeline
mechanics (approval → warehouse → dispatch → transit → putaway) look the same shape all year;
what changed is that 5-6x more requests are now queueing through the same
dispatch/transporter/putaway capacity. September looks better only because it's censored — the
slow tail of Sep requests hasn't finished yet, so don't read Sep as genuine improvement.

## Answer to "where's the lag"

1. **Not the approval process** — median 4-27 hours, smallest of the four active stages.
2. **Not the warehouse process** — median 22-28 hours, roughly flat across type and destination.
   (Can't split pick vs pack vs invoice further — no TO-level picklist timestamp exists.)
2. **Dispatch hold is a real but secondary factor** — 23-28 hours median, up to 3 days at P90 —
   worth a look but not the headline.
3. **In-transit is the headline** — 5-7 days median, 4-6x every other stage combined, and it's
   the stage that visibly degraded when volume ramped up in July.
4. **Which states are worst**: Rajasthan, Bihar, UP consistently take the longest into all three
   south destinations (7-12 days), regardless of which of the 3 destination FCs. Own-region
   moves (Telangana↔AP) are fastest (~3 days). Gujarat/Maharashtra sit in between despite being
   comparably far — a frequency/consolidation effect on the low-volume long lanes is the leading
   hypothesis, not pure distance.

## Facility-level lane view (not just state-level)

State-level buckets hide facility differences. Broke out by actual origin FC for each
destination (≥5 TOs, Jan-Sep 2026). Full tables (all stages, med/P90) delivered in chat 28 Sep
2026 — not reproduced in full here, key numbers below. Query 4 in the SQL file reproduces any of
them.

**Top-volume origins into each destination:**
- GNT01 (AP): AKD02 (185), BAY01 (139), HYD01 (139), PNQ02 (115), AHM02 (111)
- BAY01 (KA): HYD01 (228), PNQ02 (226), AHM02 (117), GNT01 (113), AKD02 (91)
- HYD01 (TG): AKD02 (218), GNT01 (180), PNQ02 (148), BAY01 (103), AHM02 (99)

**PNQ02 is a confirmed, cross-destination warehouse-stage outlier** — not a lane/distance
effect. Warehouse-stage median (approval→invoice) from PNQ02 vs. AKD02 (same state,
comparable volume), Jan-Sep:

| Destination | PNQ02 warehouse med / P90 | AKD02 warehouse med / P90 |
|---|---|---|
| GNT01 (AP) | 73.5h / 218.3h | 22.8h / 55.2h |
| BAY01 (KA) | 40.2h / 313.9h | 24.6h / 199.4h |
| HYD01 (TG) | 68.7h / 215.5h | 24.4h / 123.8h |

In-transit time from the two origins is nearly identical (~4.7-5.9 days both), so this isn't
road/distance — it's specifically slow pick/pack/invoice at PNQ02. Worth raising directly with
the Pune FC rather than treating as a transport issue.

Also flagged: HYD01↔BAY01 (the two south-neighbor lanes feeding each other) both show elevated
dispatch-hold (35-48h) and warehouse time (18-48h) — slower than distance alone would suggest for
an intra-region hop.

## September-only warehouse-stage speed check (by origin: PNQ02/AKD02/AHM02/RJ02)

Cumulative % of Sep-2026 System TOs (from these 4 origins, into all 3 south destinations) whose
warehouse stage (approved→invoiced) finished within N days. Denominator = orders that have
reached invoicing so far this month (cohort discipline — un-invoiced Sep orders excluded, noted
separately, not counted as "fast"):

| Source | Sep orders raised | Reached invoicing | ≤1d | ≤2d | ≤3d | ≤4d | ≤5d | ≤10d | >10d |
|---|---|---|---|---|---|---|---|---|---|
| PNQ02 | 205 | 177 (86%) | 25.4% | 67.8% | 97.2% | 99.4% | 99.4% | 100% | 0% |
| AKD02 | 168 | 159 (95%) | 76.7% | 99.4% | 99.4% | 99.4% | 99.4% | 100% | 0% |
| AHM02 | 133 | 130 (98%) | 43.8% | 80.0% | 86.2% | 99.2% | 100% | 100% | 0% |
| RJ02 | 69 | 69 (100%) | 42.0% | 58.0% | 95.7% | 100% | 100% | 100% | 0% |

Nobody breached 10 days in September specifically — the >10-day tail seen in the full Jan-Sep
P90 numbers is a year-to-date pattern, not happening right now. AKD02 is consistently fast (77%
≤1 day). PNQ02 is still the laggard of the four (only 25% ≤1 day, needs 3 days to reach 97%) and
has the highest un-invoiced share (14% of its Sep orders haven't even reached invoicing yet as of
28 Sep) — consistent with the year-to-date PNQ02 finding above, but not as extreme this month.

## Open items / next session

- Confirm with logistics/transport team whether Rajasthan/UP/Bihar→South lanes run on a fixed
  weekly schedule (would explain the gap vs. Gujarat/Maharashtra) or are genuinely a transporter-
  availability problem.
- Get a true "truck arrival at destination" timestamp if one exists outside WMS (e.g. transporter
  GPS/POD system) to split in-transit into road-time vs. destination-dock queue — `putaway_header.
  created_on` may itself be lagging physical arrival by an unknown, unmeasured amount.
- Check whether the Jul-Aug dispatch/transit slowdown is a **destination-side** capacity
  constraint (HYD01/GNT01/BAY01 receiving-dock or putaway-crew capacity) vs. an **origin-side**
  one (AKD02/PNQ02/HYD01-as-origin outbound dispatch capacity, since Maharashtra and Telangana are
  the top-volume origins into these three FCs) — not yet separated in this pass.
- Re-run once September fully closes (currently 66-80% complete) to confirm whether the Aug peak
  eased or whether Sep will match/exceed it once censoring clears.
- Not yet cut by SKU/product group — could reveal if slow lanes are carrying particular product
  categories (e.g. bulky/low-priority SKUs parked for truck-fill) vs. uniform across catalog.

---

# Session 2 additions (30 Sep 2026) — pooled, source-state, FY26-27, and receipt-to-putaway views

Live data: row counts drift between runs (Jan-Sep System TO count was 3,366 early in the session, 3,377 later).

## A. Pooled stage-by-stage table (all orders, 3 destinations, Jan-Sep, N=3,366)

Hours. Funnel: 3,366 requested → 3,312 invoiced (98.4%) → 3,252 dispatched (96.6%) → 3,099 putaway started/complete (92.1%).

| Transition | N | Mean | P25 | Median | P75 | P90 | Share of mean total |
|---|---|---|---|---|---|---|---|
| Request → Approval | 3,366 | 26.6 | 1.5 | 11.6 | 25.8 | 77.0 | 10% |
| Approval → Invoice | 3,312 | 47.4 | 6.5 | 24.8 | 53.8 | 124.0 | 18% |
| Invoice → Dispatch | 3,252 | 33.6 | 7.5 | 26.3 | 47.8 | 72.3 | 13% |
| Dispatch → Putaway start | 3,099 | 146.6 | 95.5 | 138.7 | 181.8 | 237.8 | 57% |
| Putaway start → complete | 3,099 | 3.0 | 0 | 0 | 0 | 0.2 | 1% |
| TOTAL | 3,099 | 257.8 | 175.9 | 237.5 | 312.4 | 408.2 | 100% |

## B. By source facility (Jan-Sep, 26 FCs) — headline points

Full per-FC table (median/P90 per stage) was delivered in chat; rerun Query 6 in the SQL file.
- In-transit is the biggest stage for almost every source (HYD01 3.9d → RJ02/JDH02 ~9.8d).
- RJ02, JDH02, IDR02 slowest overall (~13d total). LKO02: warehouse median only 6h but in-transit median 7.8d / P90 18.8d (all road).
- PNQ02 is the only big source with a real warehouse problem (56h median, P90 246h → 12.7d total vs AKD02 9.3d).
- BAY01-as-source slow on warehouse (45h) + dispatch hold (41h). Fastest big sources: HYD01 6.8d, MH02 6.9d, GNT01 8.1d.

## C. By source STATE — Jan-Sep and FY26-27 (from 1 Apr 2026)

FY26-27 (N=2,991; hours; median / P75 / P90). Total in days in last column.

| Source state | N | Approval | Warehouse | Dispatch hold | In-transit | Total | Total (days) |
|---|---|---|---|---|---|---|---|
| Maharashtra | 1,060 | 12/29/81 | 33/79/203 | 26/43/66 | 121/165/213 | 255/348/454 | 10.6/14.5/18.9 |
| Gujarat | 355 | 13/29/80 | 26/55/123 | 23/55/84 | 142/182/214 | 255/305/392 | 10.6/12.7/16.3 |
| Telangana | 290 | 13/27/79 | 24/42/77 | 17/39/50 | 97/141/169 | 169/246/303 | 7.0/10.3/12.6 |
| Andhra Pradesh | 265 | 12/28/79 | 18/31/73 | 32/55/77 | 109/142/175 | 195/266/318 | 8.1/11.1/13.3 |
| Madhya Pradesh | 257 | 13/31/81 | 34/74/124 | 27/50/73 | 174/222/285 | 296/376/457 | 12.3/15.7/19.0 |
| Karnataka | 208 | 13/28/67 | 46/126/165 | 35/53/71 | 144/183/211 | 288/357/466 | 12.0/14.9/19.4 |
| Uttar Pradesh | 188 | 10/28/81 | 9/20/25 | 27/31/52 | 208/243/450 | 268/355/496 | 11.2/14.8/20.7 |
| Rajasthan | 171 | 13/29/81 | 25/49/54 | 28/32/50 | 236/282/309 | 322/373/431 | 13.4/15.5/18.0 |
| Chhattisgarh | 122 | 13/23/67 | 26/51/170 | 25/71/74 | 121/159/188 | 201/283/378 | 8.4/11.8/15.8 |
| Bihar (low N) | 46 | 15/43/67 | 19/27/65 | 24/47/71 | 212/242/287 | 295/333/378 | 12.3/13.9/15.8 |
| Haryana (low N) | 29 | 7/31/70 | 17/24/45 | 68/91/130 | 142/170/220 | 222/273/296 | 9.3/11.4/12.3 |
| ALL | 2,991 | median 13 | 26 | 27 | 141 | 249 (P90 421) | 10.4 (P90 17.5) |

Jan-Sep state view had the same ranking (Rajasthan 13.2d slowest, Telangana 6.8d fastest). FY26-27 is ~10-15h slower than Jan-Sep
at the median (pooled 249h vs 237.5h). Rajasthan = tight slow in-transit (median 236h, P90 309h); UP = long tail (P90 450h, from LKO02)
with fastest warehouse (median 9h); Karnataka-as-source warehouse P75 126h (BAY01 outbound); Maharashtra warehouse P90 203h (PNQ02).
Sep still ~91% complete → P75/P90 slightly understated.

## D. Single-TO trace: UTO-2425-78771 (IDR02 → BAY01, Budget-balancing, Treza 1L ×50)

Timestamps are IST wall-clock stored as UTC (match IRN/e-way times).
Request 01 Aug 03:00 → posting 03 Aug 00:00 → approved 03 Aug 19:41 → invoice 06 Aug 19:46 → e-way bill 07 Aug 15:24 → dispatched 07 Aug 20:20
→ header RECEIVED 14 Aug 20:16 → putaway created 17 Sep 15:31 → completed 17 Sep 15:31.
Putaway created 34 days AFTER header went RECEIVED (putaway row has grn `TGRNKAB-004649`, odd invoice_no 'ReturnGRN') — this TO shows as a 40-day "transit"
under the dispatch→putaway definition. Real trip ≈ 7 days. Only 2 of 2,713 FY26-27 TOs show a >7-day received→putaway gap, so it is a rare outlier.

## E. Received → putaway created, and putaway created → completed (FY26-27, System TOs)

"Received" = `transfer_header.updated_on` where status='RECEIVED' (last-update time, NOT confirmed truck arrival; 0 negative gaps).
Dispatch → received (3 south FCs, FY26-27): P25 90.9h / median 123.0h (5.1d) / P90 236.1h — vs 141h median for dispatch→putaway,
so ~18h of old "in-transit" was putaway-queue wait, mostly HYD01.

**3 south destinations (N=2,713):** received→putaway created hrs P25/med/P90 = 0.3 / 0.8 / 46.6 (18% wait >1 day); putaway create→complete ≈ 1 min median, P90 ≈ 11 min.
HYD01 median 11.1h / P75 46.1h / P90 58.4h, 39% >1 day; BAY01 0.8/1.9/25.0 (12%); GNT01 0.3/0.8/2.8 (2%).
By request month (all 3): P90 3.2h Apr → 1.9 May → 3.1 Jun → 32.4 Jul → 48.3 Aug → 57.9 Sep (partial). Not yet split by destination.

**All destination FCs, System TOs, FY26-27 (N=25,838; hrs P25/median/P90; % >1 day; putaway create→complete min P25/med/P90):**

| State | FC | N | Recv→putaway created | >1 day | Putaway created→complete (min) |
|---|---|---|---|---|---|
| MH | AKD02 | 1,986 | 0.0/0.0/0.8 | 1.4% | 0/2/17 |
| MH | AKD01 | 1,330 | 0.0/0.1/3.3 | 1.1% | 0/1/7 |
| MH | PNQ02 | 939 | 0.2/1.4/64.5 | 23.5% | 0/1/8 |
| MH | MH01 | 569 | 0.1/1.1/37.2 | 11.8% | 0/0/5 |
| MH | PNQ01 | 439 | 0.0/0.2/35.5 | 12.5% | 0/1/9 |
| MH | MH02 | 383 | 0.0/0.3/31.1 | 12.3% | 0/1/23 |
| MP | JBL02 | 1,960 | 0.7/2.9/24.1 | 10.2% | 0/0/3 |
| MP | IDR02 | 1,598 | 0.0/0.0/0.0 | 0.9% | 0/0/3 |
| MP | JBL01 | 1,092 | 0.2/0.6/15.3 | 4.9% | 0/0/4 |
| MP | IDR01 | 1,086 | 0.0/0.1/0.7 | 0.3% | 0/0/3 |
| UP | AGR01 | 1,355 | 0.1/0.1/2.1 | 1.3% | 0/1/7 |
| UP | LKO02 | 1,293 | 0.1/0.3/20.6 | 6.7% | 0/1/16 |
| UP | AGR02 | 1,241 | 0.0/0.1/0.7 | 0.5% | 0/2/15 |
| UP | LKO01 | 1,031 | 0.0/0.1/19.2 | 6.7% | 0/1/7 |
| RJ | RJ01 | 1,157 | 0.0/0.0/1.2 | 3.4% | 1/2/9 |
| RJ | RJ02 | 1,066 | 0.0/0.0/0.1 | 2.9% | 1/2/14 |
| RJ | JDH02 | 509 | 0.0/0.1/0.5 | 0.4% | 0/0/3 |
| RJ | JDH01 | 338 | 0.0/0.0/0.3 | 1.8% | 0/0/3 |
| GJ | AHM02 | 917 | 0.0/0.0/0.6 | 1.7% | 0/1/12 |
| GJ | AGROSTARWAREHOUSE | 631 | 0.0/0.0/0.5 | 3.5% | 0/0/5 |
| CG | RPR01 | 1,019 | 1.3/5.0/37.4 | 17.9% | 0/1/4 |
| KA | BAY01 | 953 | 0.4/0.8/25.0 | 11.6% | 0/0/3 |
| TG | HYD01 | 916 | 0.5/11.1/58.4 | 39.2% | 0/1/4 |
| AP | GNT01 | 844 | 0.1/0.3/2.8 | 1.8% | 0/1/118 |
| BR | PAT02 | 607 | 0.0/0.0/0.0 | 0.0% | 0/0/3 |
| HR | HSR01 | 579 | 0.1/0.2/1.1 | 0.7% | 0/1/4 |
| | ALL | 25,838 | 0.0/0.1/18.0 | 6.5% | 0/1/8 |

State roll-up (recv→putaway created P25/med/P90; >1 day): MP 0.0/0.2/17.7 (4.7%); MH 0.0/0.1/19.9 (7.7%); UP 0.0/0.1/16.8 (3.6%); RJ 0.0/0.0/0.4 (2.5%);
GJ 0.0/0.0/0.6 (2.5%); CG 1.3/5.0/37.4 (17.9%); KA 0.4/0.8/25.0 (11.6%); TG 0.5/11.1/58.4 (39.2%); AP 0.1/0.3/2.8 (1.8%); BR 0 (0%); HR 0.1/0.2/1.1 (0.7%).

Takeaways: putaway execution is never the bottleneck (median 0-2 min). The delay is receipt → putaway start, concentrated at HYD01, PNQ02, RPR01, JBL02
(plus tails at MH01/MH02/PNQ01/LKO01/LKO02/BAY01 — median fast, P90 19-37h, probably late-arriving trucks waiting for next shift; unproven).
Always fast: IDR01/02, AGR01/02, all RJ FCs, AHM02, PAT02, JDH01/02.

## Open / next session (additions)

- Split the Jul-Sep received→putaway rise by destination FC (HYD01, PNQ02, RPR01, JBL02 first) — only the 3-south monthly view is done.
- Rebuild the whole stage table with "Received" as its own stage (dispatch→received, received→putaway created) and re-state in-transit using dispatch→received.
- Check the 153 dispatched-but-no-putaway TOs (likely Sep censoring — not verified) and 54 approved-but-never-invoiced TOs.
- Why is MH02's approval median only ~3h (vs ~11-13h elsewhere)? Not checked.
