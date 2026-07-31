# Deep-dive: SKU & lane tonnage — system-generated vs approved (July System TOs)

*Weights from `pristine_unicommerce_views.item_master` (`weight__gms_`, MAX per product_code; 100% SKU coverage). Snapshot Jul 8, 2026.*

## Top 15 SKUs by requested tonnage

| # | SKU | Requested qty | Requested t | Approved qty | Approved t | % wt approved |
|---|---|---:|---:|---:|---:|---:|
| 1 | Bhumika 15N×2kg Drum 30KG | 4,554 | 136.62 | 0 | 0 | **0%** |
| 2 | Bhumika 4kg pouch | 5,040 | 20.16 | 0 | 0 | **0%** |
| 3 | Bhumika 8N×4kg Drum 32kg | 581 | 18.59 | 289 | 9.25 | 50% |
| 4 | Parakh (P-rich Organic Manure) 20kg | 910 | 18.20 | 0 | 0 | **0%** |
| 5 | Sanchaar (Organic Manure) 10kg | 1,790 | 17.90 | 0 | 0 | **0%** |
| 6 | Sanchaar (Organic Manure) 25kg | 665 | 16.63 | 0 | 0 | **0%** |
| 7 | NPK 12:61:0 (MAP) 25kg | 488 | 12.20 | 488 | 12.20 | 100% |
| 8 | Bhumika Drum 30kg | 319 | 9.57 | 153 | 4.59 | 48% |
| 9 | Agronil GR (Fipronil) 1kg | 7,950 | 7.95 | 7,950 | 7.95 | 100% |
| 10 | NPK 0:0:50 (SOP) 25kg | 294 | 7.35 | 294 | 7.35 | 100% |
| 11 | Sulphur Fast FWD 3kg | 2,418 | 7.25 | 1,992 | 5.98 | 82% |
| 12 | NPK 19:19:19 (TE) 25kg | 276 | 6.90 | 276 | 6.90 | 100% |
| 13 | Sulphur Fast FWD 1kg | 5,740 | 5.74 | 5,700 | 5.70 | 99% |
| 14 | Atraz (Atrazine 50% WP) 500g | 11,120 | 5.56 | 10,720 | 5.36 | 96% |
| 15 | Sanchaar (Organic Manure) 5kg | 1,060 | 5.30 | 0 | 0 | **0%** |

**Two clean stories:**
1. **Crop-protection chemicals & NPK fertilizers sail through at ~100% by weight** (MAP, SOP, NPK 19:19:19, Agronil, Sulphur, Atraz).
2. **Heavy organic soil products (Bhumika / Sanchaar / Parakh) systematically blocked — ~215 t requested, ~0% approved:**
   - Bhumika 30kg drum (136.6 t, largest single weight request): all 10 lines WAITING_FOR_APPROVAL → ~70% of the 197 t pending backlog, LKO02-bound
   - Bhumika 4kg pouch (20.2 t): all 8 lines SYSTEM_CANCELLED
   - Parakh 20kg, Sanchaar 25/10/5kg (~58 t): predominantly human-REJECTED

Reviewers treat bulky low-value organic manure very differently from chemicals — freight cost per rupee of product value is terrible for 20–30 kg bags, which a pure budget-gap algorithm doesn't know. **Clearest evidence the algorithm needs a freight-economics input.**

*Caveat: multipack naming ("15N×2kg Drum 30KG", master weight 30 kg) — if `transfer_qty` counts 2 kg inner packs rather than drums, true weight ≈ 9 t not 137 t. The 0%-approval pattern holds either way; confirm UoM with catalog.*

## Lane-level tonnage: generated vs approved (top 25 by generated weight)

| # | From → To | Dist (km) | Lines | Appr. lines | Generated (t) | Approved (t) | % wt approved |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | AGR02 → LKO02 | 338 | 42 | 32 | 52.52 | 6.67 | **13%** |
| 2 | RJ02 → LKO02 | 618 | 21 | 14 | 36.00 | 2.65 | **7%** |
| 3 | JBL02 → IDR02 | 510 | 60 | 45 | 22.42 | 12.41 | 55% |
| 4 | AKD02 → PNQ02 | 270 | 66 | 51 | 22.41 | 19.94 | 89% |
| 5 | JBL02 → LKO02 | 557 | 6 | 1 | 21.15 | 0.38 | **2%** |
| 6 | RJ02 → JDH02 | 359 | 75 | 55 | 18.76 | 11.82 | 63% |
| 7 | JDH02 → LKO02 | 914 | 4 | 2 | 18.17 | 0.78 | **4%** |
| 8 | LKO02 → AGR02 | 338 | 53 | 43 | 15.52 | 9.13 | 59% |
| 9 | PNQ02 → AKD02 | 270 | 76 | 57 | 13.93 | 11.39 | 82% |
| 10 | RJ02 → HSR01 | 327 | 40 | 33 | 13.87 | 3.49 | 25% |
| 11 | AHM02 → LKO02 | 1,250 | 15 | 10 | 12.22 | 0.85 | **7%** |
| 12 | IDR02 → LKO02 | 770 | 13 | 6 | 11.52 | 1.99 | 17% |
| 13 | IDR02 → PNQ02 | 448 | 26 | 14 | 11.16 | 8.07 | 72% |
| 14 | IDR02 → JBL02 | 510 | 66 | 53 | 9.65 | 8.22 | 85% |
| 15 | LKO02 → PAT02 | 534 | 18 | 15 | 9.05 | 8.00 | 88% |
| 16 | RJ02 → AKD02 | 954 | 12 | 9 | 8.26 | 8.26 | 100% |
| 17 | HYD01 → RJ02 | 1,492 | 5 | 4 | 7.87 | 0.21 | **3%** |
| 18 | PAT02 → LKO02 | 534 | 9 | 7 | 7.32 | 7.25 | 99% |
| 19 | HYD01 → PNQ02 | 607 | 12 | 7 | 6.43 | 0.82 | 13% |
| 20 | GNT01 → RPR01 | 783 | 3 | 2 | 5.60 | 0.00 | **0%** |
| 21 | JDH02 → PNQ02 | 1,004 | 5 | 3 | 5.58 | 4.08 | 73% |
| 22 | JDH02 → RJ02 | 359 | 39 | 31 | 4.91 | 3.57 | 73% |
| 23 | PNQ02 → IDR02 | 448 | 21 | 12 | 4.25 | 3.40 | 80% |
| 24 | AHM02 → RJ02 | 688 | 28 | 19 | 3.62 | 2.37 | 65% |
| 25 | RJ02 → AGR02 | 288 | 15 | 10 | 3.24 | 3.13 | 97% |

**Insights:**
1. **The LKO02-inbound corridor is largely phantom freight**: ~140 t generated into Lucknow across 5 lanes, only ~11 t approved (2–13%) — dominated by the blocked organic-manure SKUs.
2. **Corridors that survive approval are short and bidirectional**: AKD02↔PNQ02 (82–89%, 270 km), IDR02↔JBL02 (85%/55%), LKO02↔PAT02 (88%/99%), RJ02→AKD02 (100%). The *approved* network is tighter and more economical than the generated one.
3. **Line-approval and weight-approval diverge on the same lane** (AGR02→LKO02: 32/42 lines approved but 13% of weight) — reviewers approve light chemical lines, veto the few heavy manure lines. Line approval ≈ "is the transfer sensible"; weight veto ≈ "is the freight worth paying".
4. **Long hauls get weight-vetoed hardest**: HYD01→RJ02 (1,492 km, 3%), AHM02→LKO02 (1,250 km, 7%), GNT01→RPR01 (0%). Humans already apply the tonne-km economics filter the algorithm lacks — build it into generation.

## Overall corridor weight summary

| Metric | Value |
|---|---:|
| Total weight proposed | 455.9 t |
| Total transport effort | 281,066 tonne-km |
| Weight-weighted avg haul | 617 km |
| Approved weight | 204.7 t |
| Killed weight | 89.5 t (19.6% by weight vs 10.6% by units — kills skew heavy) |
| Pending weight | 197.0 t |
| Avg weight per TO | ~390 kg generated; **~175 kg approved** (1 line = 1 TO) |
