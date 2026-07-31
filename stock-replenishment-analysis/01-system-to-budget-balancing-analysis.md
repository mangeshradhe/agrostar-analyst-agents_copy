# System TO – Budget Balancing: Complete Analysis

*Feature live Jul 7, 2026. Data: `catalog_views.catalog_management_transferorders` (BigQuery project `agrostar-data`), all System TO lines Jul 7–8, benchmarked against manual "Current BM"/"Coming BM" TOs Apr–Jun (first-10-days cohorts). Weights joined from `pristine_unicommerce_views.item_master` (100% SKU coverage). Snapshot: Jul 8, 2026 evening — statuses keep moving.*

## Verdict

**The feature is working, and the data says trust it more than the initial numbers suggested.** Direction of stock movement is coherent, human approval behavior matches the manual era, net approved freight is *below* the manual benchmark, and the waste rate is within historical range. Two real issues remain — shipment fragmentation (~4× smaller shipments than manual) and the system racing its own approval queue — plus instrumentation gaps that prevent auditing the algorithm's logic. Nothing indicates broken core logic.

---

## 1. Scale and adoption

| Metric | Value |
|---|---:|
| TO lines | 1,681 |
| Transfer orders | 1,171 |
| SKUs touched | 355 |
| FCs participating | 15 of 15 (all send and receive) |
| Directed lanes used | 191 of 210 possible |
| Quantity proposed | 420,196 units |
| Weight proposed | 455.9 tonnes |
| Transport effort | 281,066 tonne-km (weight-avg haul 617 km) |

- Day 1 was a network-wide burst (1,417 lines / 390k units / 185 lanes); day 2 settled to ~15% of that — consistent with backlog-clear-then-incremental cadence.
- The feature instantly became the dominant movement source: 76% of all July TO lines, ~94% of quantity. Manual BM collapsed from 1,716 lines (June) to 227 (July) — a clean hand-off, with planners now hand-creating only residual cases.
- Every line has the same profile: B2B→B2B channel, `created_type` = Budget, LIFO pick. `transfer_type` only distinguishes geography: **Inter-State 67% of qty, Inter-City 33%**.

## 2. Approval funnel

How to read: `action_status` = human decision (APPROVED / REJECTED / blank); `transfer_status` = lifecycle stage (WAITING_FOR_APPROVAL → PROCESSING/PENDING → PICK CREATED → PICKED → INVOICED → DISPATCHED → RECEIVED, with REJECTED / CANCELLED / SYSTEM_CANCELLED as exits).

| Stage | Lines | Qty | Tonnes |
|---|---:|---:|---:|
| Approved, total | 1,170 | 339,405 | 204.7 |
| — of which PROCESSING | 785 | 224,264 | — |
| — PICK CREATED / PENDING | 221 | 72,406 | — |
| — INVOICED / PICKED / DISPATCHED | 149 | 41,756 | ~20.3 |
| — approved then CANCELLED | 15 | 979 | — |
| Waiting for approval | 316 + 6 locked | 37,141 | **197.0** |
| Rejected (human) | 127 | 24,066 | — |
| System cancelled | 28 | 19,584 | — |
| Archived ("Data archived by system") | 34 | 0 | — |

**Key findings:**

- **Approval rate 90% on actioned lines** (1,170 vs 127), avg ~4.3 hrs to action. Approvals concentrated in 5 people (Ravindra Shelke, Vikas Kashid, Kaushik Gaur, Harshal Sathe, Tejas Gangurde) — fine today, a bottleneck at scale.
- **Reviewers approve at the exact proposed quantity — no trimming.** `edited_transfer_qty` is a workflow field: 0 until approval, then set equal to `transfer_qty`. Approvers are strictly binary (approve whole line / reject whole line). See file 02 for the deep-dive.
- **Every human rejection (127 lines) has one cause: "transfer qty reduced to zero during re-allocation"** — the next system run zeroes lines still awaiting approval. With the 28 SYSTEM_CANCELLED lines, ~10% of proposed quantity dies from the system racing itself. Fixable: lock pending lines out of re-allocation or auto-expire them.
- **197 t (43% of generated tonnage) sits in the approval queue** — the single biggest operational lever right now; it ages while destination FCs wait. ~70% of that pending weight is one SKU (Bhumika 30 kg drum → LKO02, see file 03).

## 3. Execution

Only 149 lines (~42 t) have physically progressed to picked/invoiced/dispatched; 785 approved lines (224k units) are in PROCESSING. Execution is accelerating (invoiced tonnage doubled within day 2), but the warehouse pipeline is the choke point to watch. Benchmark to beat (from manual cohorts): **76–89% of approved tonnage RECEIVED** — check the Jul 7–8 cohort around **Jul 21**.

## 4. Network structure — sane

**Net flows (units):** clear west/central → east/south/north pattern, consistent with kharif redistribution:

| Net exporters | Net | Net importers | Net |
|---|---:|---|---:|
| IDR02 (MP) | −57,473 | PAT02 (BH) | +23,620 |
| PNQ02 (MH) | −27,036 | HSR01 (HR) | +21,304 |
| AHM02 (GJ) | −18,937 | JBL02 (MP) | +18,687 |
| RJ02 (RJ) | −12,860 | RPR01 (CT) | +13,340 |
| AGR02 (UP) | −3,740 | GNT01 (AD) | +13,099 |
| | | BAY01 (KA) | +10,839 |

AKD02, LKO02 act as balanced high-throughput hubs. No FC is being simultaneously drained and flooded at scale.

**Lanes:** 191 directed lanes (91% of possible); 90 of 101 FC-pairs flow both directions, but **only one SKU anywhere moves both ways on the same pair** — genuine SKU-level rebalancing, not churn. Volume concentrated: top 15 lanes ≈ 51% of qty; but **85 lanes carry <500 units each** (some absurd: 1 unit / 1,783 km). The manual benchmark used a near-identical footprint (194 lanes in June's first 10 days), so the promiscuity is inherited, not invented — but it was worth fixing then too.

**By weight, the map changes:** the biggest unit-lane (PNQ02→AKD02, 31k light packets = 15.4 t) is not the biggest freight lane. LKO02 is the *generated* gravity center — AGR02→LKO02 (52.3 t), RJ02→LKO02 (36 t), JBL02→LKO02 (21 t), JDH02→LKO02 (18 t) — **but almost none of that inbound weight got approved** (2–13%); it's dominated by blocked organic-manure SKUs (file 03). Long-haul waste is quantifiable: HYD01→RJ02 (7.9 t × 1,492 km) and AHM02→LKO02 (12.2 t × 1,250 km) each burn as many tonne-km as the 52-t short-haul corridor. **Tonne-km per lane is the right threshold metric.**

**Product mix:** all kharif crop-protection — Omni Star (29.2k), Agmix (21.6k, single SKU on 8 lines), Amaze-X (20.5k), Wetsil/Wetsil Plus, Mandoz, Heliox, Atraz, Nutripro. Seasonally coherent.

## 5. Benchmark vs manual process (first 10 days of each month)

Manual cohort = transfer_reason IN ('Current BM','Coming BM'); "approved" for manual = not cancelled/rejected (manual TOs never used the approval workflow).

| Metric | Apr | May | Jun | **Jul (System TO)** | vs benchmark |
|---|---:|---:|---:|---:|---|
| Lines | 233 | 647 | 714 | **1,681** | 2.4× June ⚠️ |
| Qty | 52.5k | 370k | 505k | **420k** (+83k manual = 503k) | ≈ June ✅ |
| Tonnes generated | 110 | 410 | 427 | **456** (+99 manual = 555) | +30% vs June ⚠️ |
| **Tonnes approved** | 89 | 312 | 379 | **205** (+89 manual = 294) | below June ✅ |
| Approval yield (tonnes, decided) | 81% | 76% | 89% | **79%** | in band ✅ |
| SKUs | 86 | 170 | 180 | **354** | 2× coverage ✅ |
| Lanes | 116 | 184 | 194 | **193** | identical ✅ |
| Qty per line | 226 | 572 | 708 | **250** | fragmented ⚠️ |
| Tonnes per approved TO | 0.55 | 0.68 | 0.70 | **0.17** | 4× smaller ⚠️ |
| % qty killed | 5.7% | 10.9% | 16.9% | **10.6%** | in band ✅ |
| % tonnes received (eventual) | 81% | 76% | 89% | — | target ≥76–89% |

**Reading:** the system replicates manual volume, footprint, approval yield, and waste rate almost exactly, while doubling SKU coverage (it rebalances the long tail planners never had bandwidth for). It over-*generates* by ~30% on weight, but the approval gate filters that back to below-benchmark committed freight. The one clear regression: **approved shipments are single-SKU, 1-line-per-TO, ~175 kg average — a quarter of the manual shipment weight** — meaning ~4× the documents, picks, and part-load freight per tonne moved.

## 6. Data quality & corrections log

1. **`budget_tci` is not rupees** — it is exactly `transfer_qty × distance` (unit-km). An early "₹21.3 Cr moved" read was wrong and was retracted; correct figures: 212.9M unit-km / 281k tonne-km. Monetary value needs a price-master join (not in this table).
2. **`edited_transfer_qty` is not a human edit** — workflow field (0 → set equal to transfer_qty on approval). An early "19% quantity haircut" claim was retracted; approvals are pass-through at proposed qty.
3. **`to_stock_coverage_days` = 0 on all 1,681 lines** — the algorithm's core input isn't being written, so "did stock go where coverage was lowest" cannot be audited from this table. Highest-value instrumentation fix.
4. **`catalog_views` view schema is stale** — advertises `from_*` metric columns (from_drr, from_inventory_qty, budgets) that no longer exist in `catalog_prod`; queries on them fail.
5. 34 "Data archived by system" lines pollute REJECTED; deserve their own status.
6. No access to `pristine_wms_prod.item_mst` from this account; Unicommerce `item_master` weights used instead (±few %). Multipack SKUs (e.g., "15N×2kg Drum 30KG") may have unit-of-measure ambiguity — confirm before quoting per-SKU tonnage.
7. **VTO-series documents** (`VTO-2425-###`) flow into this table with blank `transfer_reason`/`created_from` — 78 TOs / ~242k units in 30 days, invisible to any reason-filtered reporting (file 04).

## 7. Recommendations, ranked

1. **Fix the re-allocation race** (lock or auto-expire pending lines) — recovers ~10% of proposed volume from pointless rejection.
2. **Consolidate lane-day shipments** — merge same-day, same-lane approved lines into multi-SKU TOs; approved shipment weight should climb from 0.17 t back toward the manual 0.7 t.
3. **Put an approval SLA on the queue** — 197 t pending, 43% of generated freight, ages daily; either widen the approver pool or auto-approve below a risk threshold.
4. **Add a lane-level floor** (min units/value/tonne-km) — kills the 85-lane micro-tail at ~2–3% volume cost.
5. **Give the algorithm a freight-economics input** (value density / weight cap) — reviewers already veto bulky low-value organic manure on long hauls; build that filter into generation (file 03).
6. **Instrument the algorithm's inputs** (coverage days, from-side stock/DRR) so the next review can audit decisions, not just outcomes.
7. **Re-run two checks around Jul 21:** (a) % of Jul 7–8 approved tonnage RECEIVED vs the 76–89% manual bar; (b) daily generated tonnage trend — confirm the burst decayed and July lands near June's run rate.

**One line:** the algorithm made the same decisions the humans used to make, faster, across twice the SKUs, with the same hit rate — it just ships them in boxes four times too small and occasionally shoots its own pending orders; fix those two and this feature is a clear win.

---
*Posted to Slack #md-files-for-claude on Jul 8, 2026 (3-part thread).*
