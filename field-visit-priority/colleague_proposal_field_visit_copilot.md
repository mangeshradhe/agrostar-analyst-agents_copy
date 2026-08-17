# Field Visit Copilot — Data Requirements (colleague's proposal, verbatim reference)

Source: `Field_Visit_Copilot_Data_Requirements.docx`, extracted 2026-08-17.
Treat as **one input to consider, not the design we adopt** — several sections are explicitly flagged
by the author as proposed/not-signed-off or as open gaps with no confirmed data source.

## Model shape
- Every partner scored across **3 independent tracks**: Active (blended), Onboarding (own ranking), Churned (own ranking).
- Active track blends 3 buckets: Revenue Opportunity, Collection Recovery, Returns & Complaints.
- Daily pool of 20 recommendations split: **Active 14 / Onboarding 4 / Churned 2** (reserved slots, not competing across tracks).
- "Percentile rank" used for continuous signals (OCP amount, outstanding amount, P2P fulfillment) to avoid arbitrary ₹ cliffs.

## Behavioral scores (quarterly refresh, cached — not computed live)
- **P2P Behavior**: fulfilled promises ÷ total promises. ⚠ GAP: "fulfilled" is proximity-matched (no promise-id→payment-id key), risk of false positives with overlapping promises.
- **Visit Conversion**: historical same-day-order conversion rate per partner. Feeds Revenue Opportunity R5.
- **Visit Gap**: days since last visit vs. role baseline cadence — SM ~7.6d, TM ~14.5d, CM ~27.4d, SH ~29.6d. ⚠ GAP: baseline numbers exist but no confirmed tiering/point curve.

## Revenue Opportunity (weights PROPOSED by this doc's author, not signed off)
R1 Frequency Trend (20%) · R2 Same-Month YoY Degrowth (30%) · R3 FYTD YoY Degrowth (25%) · R4 Available Credit (15%) · R5 Same-Visit Conversion (10%).
Point tiers for each are defined in the doc. Note: R5 direction is deliberate — high historical conversion scores *high* ("this visit is likely to pay off"), opposite of a naive "reward struggling partners" read.

## Collection Recovery (weights have sign-off per doc)
OCP Amount percentile (40%) + P2P Fulfillment percentile (25%) + Outstanding Amount percentile (20%) + DPD tier (15%), plus bonuses: +30 upcoming payment due in 15d, +30 P2P due this week / +20 P2P missed unpaid. Capped at 100.
⚠ GAP: "upcoming payment in 15 days" definition is a guess (any debit due_date in next 15d, not yet overdue) — needs confirmation.
⚠ Confirmed bug-fix baked in: OCP must join `wallet_user_id → csr_farmer → farmer_id`; remaining = debit.amount − reconciled amount, due_date < today.

## Returns & Complaints
Open-complaint count (60%, tiers 0/1/2+) + reopen frequency last 12mo (40%, tiers 0/1-2/3+).
⚠ GAP (author flags as most important, unresolved): **no confirmed ticketing/returns/complaints table exists** — only `invoiced_report.is_return` (product-level return flag), which doesn't capture complaint counts or reopen history.

## Onboarding (own ranking, not blended into Active score)
Sort 1: about-to-close leads first. Sort 2: longest visit gap as tie-break.
⚠ GAP: "about-to-close" has no defined source — possibly `expansion_visit`-tagged records or a lead-stage field, unconfirmed.

## Churned Recovery (own ranking) — fully open, no signals/formula/source defined
Agreed direction only: a churned+police-investigation flag competes as a normal signal for its 2 reserved slots (not force-topped, not suppressed). `okr_data_live.status` only has ACTIVE/INACTIVE — no churn recency/reason/legal-status field located.

## Suppression rules
SM: 7-day window (matches ~7.6d cadence). TM: 15-day window (matches ~14.5d cadence). Partner visited within window excluded from that day's pool entirely regardless of score.

## Full list of author-flagged gaps (Section 10)
1. Returns/complaints ticketing table — no confirmed source (highest priority gap).
2. Churn / legal-police flag field — no confirmed source.
3. "About-to-close" lead criteria for Onboarding — no confirmed source.
4. "Upcoming payments in 15 days" — logic is a guess, needs confirmation.
5. **POG Capture** — existed in an earlier draft (never-captured />90d/30-90d/<30d tiers, `pogCaptured`/`visit_reason='POG Capture'`) but is absent from the current structure — unclear if deliberately dropped.

## Confirmed data sources (Section 11, useful regardless of which design we build)
| Domain | Table(s) |
|---|---|
| Visits | `offline_team.store_visits_v2` (FieldStar, Oct 2025+) ∪ `prod_db_views.visit` (SaathiAPP, Apr 14 2026+) — dedupe by email+store+day, latest wins; raw tables overcount ~2-5% |
| Partner master | `offline_team.okr_data_live` — farmer_id, sm/tm/cm/sh, territory, cluster, status |
| Revenue | `order_management_order` (B2B filter, **unpartitioned — always filter by date**), `pristine_wms_views.invoiced_report` |
| Collections/OCP | `wallet_creditwallettransaction` + `wallet_creditwallettransactionreconciliation`, joined through `csr_farmer` |
| Institution/credit | `replica_galaxy_views.institution` — contacts_mobile_number, totalCreditLimit, status, archive, business_type |
| P2P promises | `promise_to_pay_date__p2p_`/`amount_promised` (store_visits_v2), `promiseToPayDate`/`promiseToPayAmount` (prod_db_views.visit) — exclude amount outliers >₹10L (confirmed data-entry errors) |

## Scope note
This doc only covers **SM and TM** roles for suppression (Section 9), consistent with our own SM/TM scope for this hackathon submission. It's broader in ambition than the dashboard's existing Recommendations engine — 3 tracks (Active/Onboarding/Churned) vs. the dashboard's Active-only 4-signal score — but roughly half its buckets have open data-source gaps or unconfirmed weights.
