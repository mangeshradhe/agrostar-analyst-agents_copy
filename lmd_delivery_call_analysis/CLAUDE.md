# LMD Delivery Call Analysis

Analysis of AgroStar LMD (Last Mile Delivery) partner↔farmer call transcripts, in `agrostar-data`.
**Separate from the main signal-engine (sales calls) project one level up — do not merge into
`../signal_engine_master_plan.md` or `../dashboard/` until explicitly asked.**

## Non-negotiable query rules

- **Grain is ORDER, not call or package.** One order → many packages; one package → many calls
  (re-attempts/follow-ups). Always roll up to `order_id` before drawing conclusions.
- **Join key:** `llm_transcripts.lmd_call_audits.package_id = prod_db_views.delivery_shippingpackage.code`
  → gives `delivery_shippingpackage.order_id`. Confirmed clean join, 0 unmatched (2026-09-27, n=3,580).
  One known 1-in-560 edge case where a package_id joined to 2 order_ids — immaterial at this volume.
- **Order journey join:** `delivery_shippingpackagestatushistory.package_id = delivery_shippingpackage.code`
  gives the full status-change history per package (digital events). Pull **all** packages on an
  order, not just the called one — an order can have a package nobody called about. `reason` on that
  table is inconsistent: sometimes human text, sometimes an opaque UUID/Mongo-ObjectId with no
  resolvable lookup table (checked `orderhold`, `order_management_holdreasons`/`orderholdreason`,
  `delivery_returnrequest`/`returnrequesthistory`/`returnrequeststatus` — none match) — only show it
  when it reads like real text.
- **Ignore cached table metadata for `delivery_shippingpackage`.** BQ console/API metadata shows
  `numRows: 0` — stale. Table is live, 7.5M+ rows, current through today. Always query live, never
  trust the cached row count (same pattern as Genesys tables, see main project memory).
- Filter `processing_status = 'success'` in `lmd_call_audits` before analysis.
- **`overall_call_sentiment` has inconsistent casing** (`NEUTRAL`/`neutral`/`Neutral` etc.) — always
  `UPPER()` it before grouping. `delivery_state` has the same problem (`GUJARAT` vs `Gujarat` seen at
  n=3,580) — normalize case there too before grouping, not just sentiment.
- Boolean-ish fields (`ai_conflict_detected`, `farmer_willing_to_accept_delivery`,
  `amount_mismatch_reported_by_farmer`, `lmd_asked_to_pay_online`) use string values `'YES'`/`'NO'`/
  `'NOT_OBSERVED'` — not `'true'`/`'false'`. `NOT_OBSERVED` means the topic wasn't discussed, not "no."

## Standing caveats

- `lmd_call_audits` is LLM-extracted structured data from transcripts, not ground truth — treat
  audit/sentiment/outcome fields as model judgments, spot-check before citing as fact.
- The audit's own `delivery_state`/district/taluka/village are populated in only ~5% of calls — don't use
  them for geography. Use the order's shipping address instead (`output/lmd_order_geo.json`, 99.8% of orders
  resolve): `order_id` has a 4-digit YYMM prefix (`260913752174` = `2609` + `sales_order_id` 13752174), so
  join `order_management_order.sales_order_id` on `SUBSTR(order_id,5)`, then `csr_shippingaddress` on
  `shipping_address_id`. Joining the raw string as an INT matches nothing. Raw SDTVP, not village-master-deduped.
- `ai_conflict_detected='YES'` is a narrower signal than `call_outcome='DELIVERY_CANCELLED'` — most
  cancellations are calm farmer-initiated holds/returns, not disputes. Don't conflate the two.
- Audit-correlation checked at n=3,580 (2026-09-27): **no evidence** that low A3/A6/A7 (order
  confirmation/location/queries-resolved) on the first call predicts a repeat call — the gap between
  single- and multi-call orders is small and, for A6/A7, actually runs the opposite direction. Repeat
  calls track operational reasons (timing, farmer unavailable — see the pivot), not first-call quality.
- **Call-vs-digital evidence check (dashboard):** **stored timestamps are IST wall-clock time mislabelled as
  UTC** (~5.5h ahead of the true instant) — corrected 2026-09-29; an earlier version of this file said they
  were true UTC, which was wrong. Evidence: real S3 recording upload time is 5.7–7.6h *before* the audit's
  `created_at` (median 6.1h, n=20), and the newest package/status rows were stamped ~5h ahead of the real
  clock. The dashboard's `parseTs()` subtracts 5.5h so all internal times are true UTC instants. "Same day"
  is an **IST calendar day** (the business meaning of "today"/"tomorrow" in these calls) with a 15-min grace
  window. "As of" time for pending/not-executed is the real extraction instant (`output/extract_meta.json`),
  not the newest row in the data. The `archive` package status (~1% of packages) doesn't cleanly map to any
  one call outcome — left out of the expected-status sets rather than guessed at.
- `README.md` — human index, file table, headline conclusions.
- `01-call_landscape_overview.md` — descriptive pass: volumes, outcomes, reason codes, audit
  checklist pass rates, price-mismatch and conflict-call deep reads, data quality notes.
- `sql/order_rollup.sql` — package→order join + order-level call rollup starter query.
- `scripts/extract_lmd_data.py` — pulls calls + full order/package + status-history + order geography data from BQ into
  `output/*.json` (+ `extract_meta.json` extraction time) (use `/usr/bin/python3`, not bare `python3` — see main project memory). Re-run to
  refresh.
- `scripts/render_dashboard.py` — splices `output/*.json` into `dashboard/template.html` →
  `dashboard/lmd_call_dashboard.html`. Edit the template, not the rendered HTML.
- `dashboard/lmd_call_dashboard.html` — order-level dashboard: outcome×reason pivot, a call-vs-digital
  **execution-gap pivot + KPI + filter** (did the promised action — delivered/hold/return — actually get
  logged the same IST day as the call, or is it a real gap?), date filter, and a per-order **Order
  Journey** (package status history + calls interleaved chronologically, with a 45-min causal-linking
  heuristic between calls and status changes). Order-list dropdown filters (2026-09-29): Outcome, Reason,
  Execution (same day / late / not executed / pending), State (from order shipping address). Open directly in a browser (double-click / `open`) —
  **do not publish this as a Claude Artifact**, the CSP blocks loading audio from arbitrary external
  hosts (`airtel-telephony.s3.amazonaws.com`), so playback would silently fail.
- **Order items + Product Group (2026-09-30):** `output/lmd_order_items.json` — `order_management_orderitem`
  (join on `sales_order_id`, same YYMM-prefix strip as geo) → `pristine_wms_prod_db.item_mst` on
  `item_sku = item_code` (unique) → `sub_sub_product_group` (code), named via `sub_sub_product_group_mst`
  (raw value used when the mst has no row, e.g. `STICKY TRAP`). Not `prod_db_views.item_master` — that has
  no `sub_sub_product_group`. Only ~12 lines (kit SKUs) stay "Unmapped". Order card shows `sum(total_price)`
  + SKU/qty table; searchable Product Group dropdown sorted by units; group-wise Key findings computed
  live. **`MKT` = mostly the paid "AGRO+ ADVANCE (WELCOME KIT)" add-on on ~69% of orders** — excluded from
  the group rate rankings because it isn't a product. Refresh only this file: `extract_lmd_data.py items`
  (~3.2 GB scan). Group rates are order-level and an order counts once per group it contains; an order
  can be in several groups, so don't sum across groups.
- **Sales call that created each order (2026-10-01):** `output/lmd_sales_calls.json` (+ `lmd_order_sources.json`).
  Path: `order_management_order.owner_id` = `csr_farmer.farmer_id` → all of `mobile_1/2/3` →
  `genesys_db_views.disposition_data` (`RIGHT(meta_data_client_id,10)`) → `call_transcription_data` by `call_id`.
  **Match on call END (start+duration) vs order `created_on`** — the order is created as the call ends (median
  gap −0.1 min). `created_on ≈ confirmed_on` does NOT identify call orders (395/400 within 5 min). Order
  `source` families CSR*, APP*, SUPPORT_CSR* are all call-centre (user-confirmed), so no source filter. "Matched"
  = within ±30 min, "Probable" = call ended 30 min–6 h before. Result: 5,518 of 7,750 orders (71%) have a
  call, 5,380 with transcript; the rest is unexplained (other number? `disposition_data` coverage gap? order
  placed without a call?) — not investigated. Same clock for both tables (aligned within minutes).
  **Recording (2026-10-01):** `meta_data_recording_url` is a bare path; the host is unknown, so the dashboard tries
  `SALES_RECORDING_HOSTS` in order (R2 bucket, then 4 office-network servers 192.168.10.105 / 20.5:8090 / 10.101 / 20.6)
  and fails over on error. Office network only. **PII:** `render_dashboard.py` strips phone numbers (call-number fields
  dropped, numbers in free text -> `[number hidden]`) and replaces `recording_path` with an obfuscated token `rp` decoded on
  Play (obfuscation, not security — the URL still shows in the Network tab; a server proxy would be needed). Players are
  `nodownload`. Extra Genesys fields per call: `extract_sales_disposition.py` (~11 GB) -> `output/lmd_sales_disposition.json`.
  Transcripts (~44 MB) are in `dashboard/lmd_sales_transcripts.js` (gitignored) — **keep it next to the HTML** when sharing.
  Refresh only this: `extract_lmd_data.py sales` (~18 GB scan).
- **Split-order IDs:** some `order_id`s carry a suffix (`260713425265_1`/`_2`). Derive `sales_order_id` with
  `REGEXP_EXTRACT(order_id, r'^[0-9]{4}([0-9]+)')`, not `SUBSTR(order_id,5)` (the suffix made 10 orders miss every
  join). Split orders share the parent's items, so item totals are the parent order's, shown on each split.
- **Security note (flagged, not fixed):** the recording bucket (`airtel-telephony.s3.amazonaws.com`)
  serves customer call audio over plain unauthenticated HTTPS GET — anyone with a URL can play it, no
  AgroStar auth. Worth flagging to infra/security; out of scope for this analysis to fix.

## Cross-referencing an LMD call to the sales engine (do this, not a raw number match)

A farmer profile can have **up to three phone numbers** (`replica_prod_db_views.csr_farmer.mobile_1`,
`mobile_2`, `mobile_3`). The number an LMD delivery call was made to is not guaranteed to be the same
number the original sales call was made to. **Never search `genesys_db_views.disposition_data` (or
anything else) using just the one number you happen to have** — resolve to `farmer_id` first, pull all
three mobiles for that farmer, then search using all of them:

1. Get a number from the LMD call (`lmd_call_audits.destination_number`, strip leading `0`).
2. Resolve to `farmer_id` via `replica_prod_db_views.csr_farmer` — match against **any** of
   `mobile_1`/`mobile_2`/`mobile_3` (`WHERE mobile_1 = X OR mobile_2 = X OR mobile_3 = X`).
3. Pull all three mobiles back for that `farmer_id` (some will be null — that's normal).
4. Search `genesys_db_views.disposition_data.meta_data_client_id` (or the signal-engine pipeline output's
   `farmer_mobile_number`) against **all** non-null numbers from step 3, not just the original one.

Skipping steps 2–4 risks a false "no sales call found" if the sales call was made to a different
registered number than the delivery call. (Caught 2026-09-26 after the user asked "what if the profile
has three numbers" — the specific farmer checked that day only had `mobile_1` populated, so it happened
not to matter, but the gap in method was real and will bite on a farmer with 2–3 numbers on file.)
See [[reference_farmer_geo_resolution]] in the main signal-engine project's memory for the same
`mobile_1/2/3` join path used for geography resolution.

## Open commitments

- The execution-gap check (32% gap rate at n=1,611 evaluable orders, 2026-09-27) is new and unvalidated
  against ground truth — worth spot-checking a sample of flagged "Not executed" orders against
  real-world delivery status before treating 32% as a true partner-compliance number. It's plausible
  some of this is a system-update lag (driver delivered physically, app not touched) rather than a true
  non-delivery.
- **Sales-call reading plan (2026-10-01, planned, not started):** see `02-sales_call_reading_plan.md` — Claude reads each
  Mustard sales transcript (transcript only, nothing from `disposition_data`), newest first, batches of 50, one call at a
  time, verbatim-quote validator, incremental re-runs. Decisions 6–8 in that file are defaults awaiting confirmation.
