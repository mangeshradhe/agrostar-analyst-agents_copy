# field-visit-priority — Agent Index

SM/TM field-visit priority scoring engine. BigQuery project: `agrostar-data`, primarily
`agrostar-data.hackathone2026_dataset`. Ships toward the core application — treat every
signal/weight/table as unverified until checked against real BigQuery output.

## Non-negotiable query rules

- `pristine_wms_views` dataset is entirely inaccessible under this project's credentials — for
  tables you query directly, substitute `pristine_wms_prod_db` (same schema, confirmed
  accessible). For existing views you don't control that have this cascade baked into their own
  SQL (`sku_cat_repo`, `aop_target`, `one_view_rofo`), this substitution does NOT apply — either
  get the view fixed at the source or hand-replicate only the specific fields needed.
- AOP target columns (`gross_apr_26` etc. in `aop_offline_online_fy27`) are stored in **lacs** —
  multiply by 100,000 to get rupees.
- `one_view_rofo`'s `Gross_rev_*` columns are actually **volumes**, not revenue, despite the name
  (confirmed via the raw source table's `uom`/`kg_lit` columns).
- `zoho_leads.contact_name` is typed INTEGER (an internal ID), not a display name, despite the
  column name — `CAST(... AS STRING)` is required just to satisfy type-matching, and it still
  isn't a real name.
- `zoho_leads` has up to 4 rows per `reference_customer_id` — always dedupe (see the SQL's
  `QUALIFY ROW_NUMBER()` pattern) before treating a lead as one row per partner.
- `okr_data_live` is partner-grain, not rep-grain — joining a rep's email directly against
  `sm`/`tm` columns fans out to every partner that rep manages. Build a deduped rep→role lookup
  first.

## Standing caveats

- Day-of-week reweighting (Wed/Thu/Fri) is verified against real transaction-volume ratios, not
  assumed — see `priority_logic_framework_notes.md` Axis 3.
- Capacity ceiling (`daily_quota`/`menu_size`) is derived from real span-of-control data (SM
  median 23, TM median 59), not the flat numbers quoted in earlier proposals.
- POG (unsold-on-shelf) is SKU-level mandatory tracking, not a Seeds-only policy — CP/CN's low
  capture rates are a compliance failure, not an out-of-scope signal. See `panils_pog_query.md`.

## File map

- `priority_framework_FINAL.md` — read this first. The spec.
- `field_visit_priority_prototype.sql` — the query. Header comment has the full output schema.
- `sql_build_verification_log.md` — bugs caught + regression suite, read before modifying the SQL.
- `priority_logic_framework_notes.md` — why each decision was made.
- `data_access_ledger.md` — what's queryable, what's blocked, what has no source at all.
- `panils_pog_query.md`, `colleague_proposal_field_visit_copilot.md`,
  `field_dashboard_data_sources.md`, `panils_query_data_sources.md`,
  `hackathon2026_dataset_context.md` — reference/prior-art inputs.

## Open commitments

- Implement ROFO-priority/AOP-fallback for the Targets axis (currently AOP-only) — blocked on the
  `one_view_rofo` volume-vs-revenue unit mismatch above.
- Locate the onboarding "10 new first-orders" target source, if one exists.
- Resolve `pristine_wms_views` access at the source rather than the per-query workaround.
