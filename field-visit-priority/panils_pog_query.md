# Panil's POG (Product-On-Ground / stock-on-shelf) Query — Reference

Shared verbatim by the user 2026-08-17. Output grain: **partner (farmer_id) × tracked SKU**.
Answers: "of what we billed this partner for a tracked SKU, how much is still sitting unsold at their store?"

## What POG actually measures
Not a binary "was a check done" flag — it's a **physical-stock-count rolled forward by transactions since that count**:
- A field rep periodically does a POG capture visit: physically counts remaining stock of tracked SKUs at a
  partner's store (`prod_db_views.order_management_stocktracking.unsold`, latest per partner×SKU via
  `is_active=TRUE` + `QUALIFY ROW_NUMBER() ... ORDER BY last_captured_on DESC`).
- `unsold_qty` = that physical count **+ billed since the capture − returned since the capture** (only
  transactions *after* `last_capture_date` are added, so the physical count isn't double-counted with sales
  it already reflects).
- If a partner has **never been captured** for a SKU: worst-case fallback — `unsold_qty` = 100% of
  net-billed boxes (gross billed − customer returns − IPT returns). No physical evidence → assume nothing
  has sold through yet.
- `unsold_revenue_lacs` = unsold_qty prorated against gross_billed_amount, i.e. the ₹ value of goods
  presumed still sitting on the shelf, not the ₹ value of goods paid for/unpaid.

## Scope — only specific SKUs, not the whole catalog
`tracked_skus` comes from `catalog_management_stocktrackingconfig` (`is_active=TRUE`) — POG tracking is
config-driven per SKU, not universal. Matches the user's framing: POG matters most for **Seeds** (single-season,
non-returnable-once-planted, high channel-stuffing/return risk), but the config could include other categories too.
Unit convention differs by category: Seeds tracked in raw units (1 unit = 1 box); everything else converted
to boxes via MOQ (`pristine_wms_views.item_mst`, default MOQ=1 if null/0).

## Two distinct return channels, kept separate
- `return_a_combined` — ordinary customer/B2B returns (`return_grn_header.return_type='B2B Return'`, excludes IPT).
- `return_b_combined` — IPT returns (`agrox_order_id LIKE '%ipt%'`) — reads as an internal/logistics
  reallocation channel, not a farmer-facing return. Kept as a separate revenue bucket
  (`ipt_returned_revenue_lacs` vs `non_ipt_returned_revenue_lacs`) rather than merged, which matters if IPT
  returns shouldn't count as a "this partner is returning goods" risk signal the same way customer returns do.

## Why this matters for the priority logic
Directly answers the gap the colleague's doc flagged and left unresolved (Section 10, item 5 — "POG Capture
existed in an earlier draft... unclear if deliberately dropped"). Panil's version gives two usable outputs:

1. **A risk/collection-confidence sub-signal**: high `unsold_revenue_lacs` on a partner's tracked (Seeds) SKUs
   = goods billed but not yet sold to farmers = elevated return risk + lower confidence the partner can pay
   from resale proceeds. This is what the user meant by "if products are sold, they can definitely recover
   the payment" — sell-through is the leading indicator, not just DPD/OCP ageing after the fact.
2. **A distinct visit *trigger*, separate from the score**: `is_captured=0` or a large `days_since_pog_capture`
   on a partner carrying tracked-SKU exposure means the visit itself has a job to do (go count the shelf),
   not just "this partner scored high, go collect." That's a different *reason* for the visit than
   Collection Recovery or Revenue Opportunity — worth keeping as its own flag/queue rather than folding
   invisibly into one blended score.

## Verified numbers (run 2026-08-17, FY27-to-date i.e. since 2026-04-01)

Ran against BigQuery directly (not just read from the SQL) per the project's verify-with-actual-data rule.
Grain: partner × tracked-SKU, aggregated by category and capture status.

| Category | Captured? | Partner-SKU rows | Distinct partners | Billed amount | Unsold amount (implied) | Unsold % | Avg days since capture |
|---|---|---|---|---|---|---|---|
| Seeds | No  | 3,333  | 1,474 | ₹11.6 Cr | ₹7.4 Cr | 64%  | — |
| Seeds | Yes | 10,207 | 3,152 | ₹96.1 Cr | ₹7.5 Cr | **7.8%** | 40.9d |
| CP    | No  | 26,143 | 5,900 | ₹58.8 Cr | ₹55.8 Cr | 95%  | — |
| CP    | Yes | 5,222  | 1,495 | ₹20.0 Cr | ₹8.8 Cr  | 44%  | 25.4d |
| CN    | No  | 13,943 | 4,858 | ₹25.3 Cr | ₹24.3 Cr | 96%  | — |
| CN    | Yes | 231    | 86    | ₹1.1 Cr  | ₹0.55 Cr | 49%  | 3.3d |

**Correction (user, 2026-08-17): POG is not a Seeds-policy choice.** It's configured per-SKU
(`catalog_management_stocktrackingconfig.is_active`) — whichever SKUs are flagged, capture is **100%
mandatory** for that partner, regardless of category. So the coverage numbers below are not "POG applies
mostly to Seeds" — they're a **compliance gap**: Seeds capture compliance is 75.4%, CP is 16.6%, CN is
1.6%, against a 100% mandatory bar in all three. CP and CN are badly out of compliance, not "correctly
out of scope." That reframes CP's ₹55.8 Cr uncaptured exposure from "an edge case we could extend to" into
"a mandatory check that's failing 5x worse than Seeds" — a stronger visit-priority signal than originally
read, not a weaker one.

Where a SKU-partner *is* captured, Seeds sell-through is genuinely strong (7.8% unsold vs. 44-49% for
CP/CN) — but that's a separate fact from the compliance-rate finding above, and shouldn't be read as
justification for treating POG as Seeds-specific.

## Access gap hit while verifying (relevant if this query is reused verbatim in production)

My BigQuery credentials have **no access to the `pristine_wms_views` dataset** — this blocks `item_mst`,
`return_grn_header`, `return_grn_line` when referenced via that dataset, and cascades into
`revenue_and_growth_team.sku_cat_repo` and `bizfin_team.PL_Seeds_variety` (both are views that internally
join `pristine_wms_views.item_mst`), even when my own query avoids that dataset directly.

Workaround used to get the numbers above: swapped to the underlying `pristine_wms_prod_db` dataset (same
tables, confirmed accessible) for `item_mst`/`return_grn_header`/`return_grn_line`, and inlined the
`category` classification (`Seeds`/`CP`/`CN`/etc. from the `AGS-S-`/`AGS-CP-`/... SKU-code prefix) directly
instead of going through `sku_cat_repo` — that classification rule is copied verbatim from
`sku_cat_repo`'s own view definition, not guessed. Dropped `Product_group` and `Variety` from this run —
`Product_group` is a many-thousand-line hardcoded name-matching CASE inside `sku_cat_repo` that isn't safe
to hand-replicate, and wasn't needed for the unsold-revenue numbers above.

**Before this ships to production**: get `pristine_wms_views` access granted (or confirm `pristine_wms_prod_db`
is the sanctioned direct path) so the production query can reference the canonical `sku_cat_repo` view
directly rather than a parallel reimplementation that could drift from it over time.

## Caveats worth confirming with Panil before adopting as-is
- `tracked_skus` dedupes to one row per SKU via `QUALIFY ROW_NUMBER() ... ORDER BY c.state` when a SKU has
  configs in multiple states — "tracked in any state = tracked in all states," but the specific
  tracking/sales window kept is whichever state sorts first alphabetically, which could misstate the window
  for partners in a different state than the one that won the tie-break.
- Full `partner_list × tracked_skus` cross join is a scaffold (later `INNER JOIN billed_combined` prunes to
  actual sales) — not wrong, just note it's intentionally oversized before pruning, in case it's copied into
  a context without that downstream filter.
