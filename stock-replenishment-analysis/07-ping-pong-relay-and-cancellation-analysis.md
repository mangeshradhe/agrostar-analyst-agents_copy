# Ping-Pong / Quick-Relay & Order-Cancellation Analysis (Sep 2026)

**Status: in progress, paused for follow-up.** Started from a business-team complaint: received stock keeps
getting picked up and shipped out again before it can sell. This body of work traces that pattern at three
levels of rigor and ends with one concrete root cause (order cancellation) quantified against real data.

## The three artifacts (all live, all refreshed to Sep 4, 2026 data)

1. **[TO Relay Ledger](https://claude.ai/code/artifact/64dce5a6-2db0-478a-aca9-e6d1e9269628)** — FIFO
   quantity-matched relay chains (consumption-rules version: a facility's inbound-transfer stock is a pool,
   drawn down oldest-first). Gap capped at ≤15 days (received→TO2-creation). 2,448 chains, 198,849 units.
2. **[Quick Stock-Lift Pairs](https://claude.ai/code/artifact/97f246a5-b1e9-4b1d-aefe-ff23b8558bab)** — direct
   date-window match (TO2 dispatched within 10 days of TO1's receipt, same facility+SKU), **no FIFO/consumption
   logic** — every qualifying TO1↔TO2 combo is its own row. This is the one senior stakeholders are looking at;
   it's also saved locally at `~/Downloads/quick_stock_lift_pairs.html` (self-contained, no internet needed).
   4,308 pairs; 387,701 units real re-transferred (TO2-deduplicated).
3. **[Cancellation-Driven Relay Funnel](https://claude.ai/code/artifact/6a31a73a-e209-4f08-90a0-3fa2fb07290d)** —
   4-stage funnel narrowing from all genuine cross-city relay volume down to the specific failure: stock rushed
   in for urgent (UF-case) demand, whose originating order was then cancelled. 387,717 → 203,060 → 121,459 →
   **47,855 units (12.3%)** cancellation-driven.

## Key methodology decisions (apply these to any follow-on work)

- **"Real re-transferred quantity" = TO2-deduplicated.** Since there's no serial tracking, a TO1 can match
  several TO2s and vice versa. Counting TO1_Qty or TO2_Qty per row double-counts. The correct "how much freight
  did this actually cost" number counts each **distinct outbound TO2 once, at its own booked quantity**. This
  is the basis for every headline %/total across all three artifacts — never quote a raw per-row sum as "units
  affected."
- **Same-city exclusion, not just PNQ01/PNQ02.** The network has 8 same-city facility-pairs, not one:
  AKD01/AKD02 (Akola), RJ01/RJ02 (Jaipur), IDR01/IDR02 (Indore), AGR01/AGR02 (Agra), PNQ01/PNQ02 (Pune),
  LKO01/LKO02 (Lucknow), JBL01/JBL02 (Jabalpur), AHM02/AGROSTARWAREHOUSE (Ahmedabad). A TO2 leg between any of
  these pairs is a local shuffle, not real transport — excluded from all three artifacts (filter: TO2's origin
  city ≠ destination city, via `location_mst.city`). This is separate from the MH01/MH02→PNQ01/PNQ02 exclusion
  (that one's the Aurangabad factory-shutdown migration, excluded for a different reason).
- **Order-cancellation root cause, confirmed mechanism**: `supply_management.audittransferorderslogs`
  (`orderIds`, `fullTransferQuantity`, `sourceFc`/`destinationFc`/`skuCode`, snapshot log — join on
  source+dest+SKU, closest snapshot to TO1's creation, ±5 days) → order IDs → join
  `prod_db_views.order_management_order` (status) + `order_management_ordercancellationdata` (cancellation
  timestamp). "Cancellation-driven" = linked order shows `CANCELLED` with cancellation timestamp between TO1's
  creation and TO2's creation. ~68.5% of UF-case relay pairs trace to *an* order this way (the rest aren't
  provably *not* cancellation-driven — audit-log match failure, not a negative result).
- **Budget Protection rule**: `catalog_prod.catalog_management_transferorders.reject_reason = 'Stock required
  for own FC demand (Budget fulfillment)'` is the CRM engine's own source-facility protection flag. Checked a
  389-TO watchlist (`~/Downloads/Untitled`, no extension) against this field — **none carried the flag**, so
  that specific watchlist wasn't confirmed rejected-then-overridden via this table. Separately, 133 of those
  389 TOs *do* appear as TO2 in the Quick-Lift dataset (46,627 units, 12.0% of the 387,701 real total) — stated
  on the artifact as "could have been avoided if Budget Protection had applied," per Darpan's explicit framing,
  not as a system-confirmed rejection.

## Headline numbers (Jul–Sep 2026, same-city excluded, current as of Sep 4 refresh)

| Cut | Relay rate / share |
|---|---|
| Jul relay rate (Relay Ledger, ≤15d) | 10.7% |
| Aug relay rate | 5.7% |
| Last-week-of-month TO1 → next-month TO2 spillover | 90.8% (vs. 23.5% for TO1s created earlier in the month) |
| Real qty re-transferred, all reasons (Quick-Lift, TO2-deduped) | 387,701 units / 2,819 TO2s |
| ...of which System→System | 318,687 units / 2,193 TO2s (82%) |
| ...of which UF case → Budget balancing specifically | 61,503 units / 408 TO2s |
| Cancellation-driven (Funnel, Stage 4) | 47,855 units, 12.3% of genuine cross-city churn |

## Open items / where to pick this back up

1. **367,297 denominator mismatch** — Darpan quoted this number for a % calc; I could never trace it to any
   query output. Used the verified 387,701 total instead (12.0% not 12.69%) and flagged it on the artifact.
   Ask before reusing 367,297 anywhere.
2. Whether to extend the same-city exclusion check to TO1's own leg too (currently only TO2 must be inter-city;
   TO1 can still be a same-city hop feeding a real TO2 downstream — this is intentional but worth re-confirming).
3. The audit-log join only covers UF-case TO1s so far — Budget-balancing-originated TO1s haven't been run
   through the same order-cancellation trace.
4. Live-data caveat: `is_inbound_complete` keeps flipping as WMS receiving catches up, so Aug/Sep numbers drift
   slightly on every refresh (July is stable/closed). Re-run before quoting a number that's more than a day old.

## Files
`quick_lift_pairs_10day_jul_aug_sep.csv`, `relay_chains_LE15D_jul_aug_sep_till_date.csv`,
`cancellation_driven_funnel_detail.csv` — full underlying data behind each artifact, same-city-filtered,
Sep 4 refresh. Reusable queries: `07_relay_and_cancellation_queries.sql`.
