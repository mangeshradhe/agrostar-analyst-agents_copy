# 01 — LMD Call Landscape Overview

Snapshot: 2026-09-27. n = 3,580 successful extractions (`processing_status='success'`) out of
`llm_transcripts.lmd_call_audits`. This is a full re-run of the original 2026-09-26 pass (n=755) at
4.7x the volume — every table below is recomputed from the current data, and the two qualitative
reads (price-mismatch, conflict calls) were re-read call-by-call, not just recounted.

## Call volume per order

| Calls on the order | # orders | % |
|---|---|---|
| 1 | 1,541 | 66.3% |
| 2 | 501 | 21.5% |
| 3 | 167 | 7.2% |
| 4 | 75 | 3.2% |
| 5 | 25 | 1.1% |
| 6 | 11 | 0.5% |
| 7 | 3 | 0.1% |
| 8 | 3 | 0.1% |

**33.7% of orders needed more than one LMD call** — up sharply from 24% at the original n=755 read.
This is a real shift, not noise from the larger sample: worth watching as an ops KPI going forward.

## Outcome, at order level (last call by time, per order)

| Final call outcome | # orders | % |
|---|---|---|
| DELIVERY_ON_HOLD | 753 | 32.4% |
| FOLLOW_UP_REQUIRED | 660 | 28.4% |
| DELIVERY_CONFIRMED | 608 | 26.1% |
| DELIVERY_CANCELLED | 250 | 10.7% |
| NOT_ENOUGH_DATA | 55 | 2.4% |

**The order-level mix flipped since the original read.** At n=755, confirmed led (35%) ahead of on-hold
(28%); at n=3,580, on-hold is now the *largest* bucket (32.4%) and confirmed has dropped to 26.1%. This
isn't just more data on the same pattern — the underlying mix of orders is genuinely more hold-heavy at
this later, larger snapshot. Cancellation rate is close whether measured at call level (372/3,580 =
10.4%) or order level (250/2,326 = 10.7%), same as before — cancellations are still rarely reversed by a
later call.

## Why orders don't go through — `reason_category_known` (call level, n=3,580)

| Reason | # calls | % |
|---|---|---|
| NOT_APPLICABLE (no issue / smooth call) | 2,242 | 62.6% |
| DELIVERY_TIMING_ISSUE | 480 | 13.4% |
| FARMER_UNAVAILABLE | 370 | 10.3% |
| PRODUCT_NOT_REQUIRED | 145 | 4.1% |
| NO_MONEY | 79 | 2.2% |
| LATE_DELIVERY | 62 | 1.7% |
| FARMER_DENIES_ORDERING | 55 | 1.5% |
| OTHER | 44 | 1.2% |
| LMD_UNABLE_TO_SERVICE (route/area) | 37 | 1.0% |
| ADDRESS_ISSUE | 15 | 0.4% |
| FARMER_UNREACHABLE_PRIOR_ATTEMPTS | 12 | 0.3% |
| WRONG_PRODUCT | 9 | 0.3% |
| PAYMENT_ISSUE | 9 | 0.3% |
| PRICING_CONCERN | 7 | 0.2% |
| LOCATION_CONFUSION | 5 | 0.1% |
| INVENTORY_UNAVAILABLE | 5 | 0.1% |
| NETWORK_ISSUE | 4 | 0.1% |

Rank order is the same as the original read (timing and farmer-unavailability still dominate), but the
gap widened: these two reasons alone now cover 23.7% of all calls (was smaller at n=755). See the
dashboard's outcome×reason pivot for the cross-tab — timing/unavailability drive on-hold specifically,
while `PRODUCT_NOT_REQUIRED` drives cancellations specifically.

## Who's driving the action — `action_type` × `action_initiated_by` (n=3,580)

| Action | Initiated by | # calls |
|---|---|---|
| (none) | not applicable | 2,242 (62.6%) |
| HOLD | Farmer | 604 (16.9%) |
| HOLD | LMD agent | 362 (10.1%) |
| RETURN | Farmer | 336 (9.4%) |
| RETURN | LMD agent | 36 (1.0%) |

Of the 1,338 calls with any action taken, **70.4% are farmer-initiated** vs. **29.6% LMD-agent-initiated**
— still farmer-side-driven overall, but a real shift from the original 90%/10% split. Agent-initiated
holds in particular (362 calls, 27% of all actions) are a much bigger share of the picture now than the
original single-digit-percent read suggested — worth a closer look at what's driving agent-side holds
specifically (route/capacity constraints vs. genuine delivery problems) before concluding it's still
mostly farmer behavior driving repeat contact.

## Call-quality audit checklist pass rates (n=3,580, `audit_a1`–`audit_a10`)

| Step | % marked YES |
|---|---|
| A8 — professional & polite communication | 99.7% |
| A1 — greeting done | 74.6% |
| A10 — proper call closing | 66.8% |
| A9 — delivery commitment obtained | 55.1% |
| A4 — delivery availability checked | 54.7% |
| A2 — farmer identity confirmed | 49.9% |
| A5 — delivery time confirmed | 46.1% |
| A3 — order confirmation done | 40.8% |
| A6 — delivery location confirmed | 37.9% |
| A7 — farmer queries resolved | 28.7% |

Same weak spots as before (A6/A7/A3), and each got slightly *worse* at scale — A7 dropped from 33% to
28.7%, A6 from 39% to 37.9%. **Audit-correlation question now checked and answered:** comparing an
order's *first* call's audit scores for single-call orders (n=1,541) vs. multi-call orders (n=785)
shows no meaningful predictive gap — A3/A6/A7 differ by only 4-6 points between the two groups, and for
A6/A7 the multi-call group actually scores *higher* on the first call, not lower. **The audit checklist
is not a leading indicator of repeat-call risk** — repeat calls track operational reasons (timing,
farmer availability — see the reason table above), not first-call quality. This closes the open
question from the original read with a real, if negative, answer.

## Price/amount mismatches — re-read in full (13 calls / 12 orders, up from 3)

`amount_mismatch_reported_by_farmer='YES'` now fires on 13 calls (0.36% of 3,580, essentially the same
rate as the original 0.4%). Reading all 13 (not just recounting) shows the flag covers **three distinct
mechanisms**, not one — the original n=755 read only had the first:

- **Booking-price vs. delivery-app-price desync** (the original finding) — **6 of 13**: the price
  quoted at booking doesn't match what the LMD partner's app shows at delivery. Includes the same two
  cases the original read found (₹999/₹1050 booked vs. ₹1150 at delivery → cancelled; a
  previously-lower-priced tarpaulin → held pending customer-care verification) plus four new ones:
  a ₹1300/₹1200 gap clarified by invoice (resolved, delivery proceeded), a ₹19,190-vs-₹18,989
  free-offer dispute (held), a ₹2,400-booked-vs-₹2,902-app gap (held, routed to booking agent), and a
  ₹2,784-vs-~₹2,600-expected gap (cancelled). **This mechanism recurs and generalizes** — it's not the
  three-off anecdote the original read left open; still small in absolute volume but real and worth
  escalating to whoever owns pricing/catalog sync now that there's a bigger evidence base.
- **Missing-items short-payment** — **1 of 13**: farmer paid ₹1400 against a ₹1653 invoice because
  items were missing from the physical package — a fulfillment/picking error, not a live price change.
  Different root cause, different owner (fulfillment, not pricing).
- **Wrong quantity/product flagged as a "mismatch"** — **5 of 13**: the LLM's amount-mismatch flag also
  fires when the farmer disputes something about the bill that's actually a wrong-quantity or
  wrong-product dispatch (e.g. two packets sent instead of one, three items dispatched against a
  single-item order), not a genuine price disagreement. **This is a refinement to the original
  conclusion, not just a bigger sample of it** — the field is a broader "farmer objected to something
  on the bill" signal than "price changed between booking and delivery." Reading the underlying summary
  is necessary to tell which sub-type you're looking at; the raw flag alone overstates the pricing-sync
  problem specifically.

## Conflict calls — full re-read (51/3,580 flagged `ai_conflict_detected='YES'`, up from 14)

Read all 51 conflict summaries (50 distinct orders — one order had a conflict on two separate calls).
Outcome distribution held steady: **66.7% ended in `DELIVERY_CANCELLED`** (34/51, vs. 64% at n=14),
19.6% on hold, 11.8% confirmed anyway, one follow-up-required. Themes, by rough frequency:

- **Delivery-delay anger** (~16 of 51 orders, ~32%) — now the single largest theme, and it barely
  registered in the original 14-call read (2/14). At this volume, farmer frustration over lateness —
  sometimes a month or more — is the dominant driver of a flagged conflict, well ahead of any other
  single cause.
- **Route/serviceability refusal or doorstep-vs-pickup disputes** (~10 of 51, ~20%) — consistent with
  the original read's top theme; agent says the village/route is outside the serviceable area or
  "closed," farmer expected doorstep delivery as promised at booking.
- **Price/amount discrepancy** (4 of 51, ~8%) — same underlying cases as the price-mismatch section
  above; proportionally similar to the original read (2/14, ~14%), not trending up.
- **Payment/advance-payment/pending-balance disputes** (5 of 51, ~10%) — a theme that wasn't distinct in
  the original 14-call read: agent demanding advance payment or full balance clearance, farmer offering
  partial payment or objecting to repeated payment-reminder calls.
- **OTP/cancellation-and-return friction** (3 of 51, ~6%) — farmer won't share a cancellation OTP, or
  disputes that the parcel was already returned — same theme as before, smaller relative share now.
- **Misdirected calls / wrong number** (2 of 51) and **repeated-call annoyance / "stop calling me"**
  (2 of 51) — small but recurring operational-error themes, not farmer-side substance disputes.
- **Unrelated past-grievance venting** (2 of 51) — farmer uses the call to vent about an unrelated past
  issue (a previously-received defective product, an old handwritten-bill dispute), not about the
  current delivery.
- **Open-parcel/CCTV policy dispute** (1 of 51) — same single case type as the original read.
- **New: agent-driven hostility** (3-4 of 51) — a handful of calls where the *LMD agent* is the one
  being rude, dismissive, or aggressive (telling a farmer "not to tell stories," refusing a re-delivery
  attempt citing a past refusal, blaming the farmer for being unreachable). This category essentially
  didn't show up in the original 14-call read and is worth flagging to LMD ops as a coaching item — the
  audit checklist's "professional and polite" step (A8) still shows 99.7% pass, so these agent-side
  conflict moments aren't being caught by the current audit checklist at all.

**Conflict is still not the same as cancellation** — most cancelled calls remain calm farmer-initiated
holds/returns (see the action-type table above), not disputes; only 51 of 3,580 calls (1.4%) are flagged
as an actual conflict, down slightly from ~1.9% at the smaller n.

## Data quality notes

- `overall_call_sentiment` casing inconsistency confirmed unchanged — always `UPPER()` before grouping.
  Normalized at n=3,580: NEUTRAL 64.3%, POSITIVE 34.1%, NEGATIVE 1.6% (sentiment skews more neutral and
  less positive than the original n=755 read's 59%/39%/2% split).
- `delivery_state` (and district/taluka/village) populated in only 192/3,580 calls (5.4%, essentially
  unchanged from the original 6%) — still not usable for geography cuts at scale. New data-quality note:
  the state field has the same casing problem as sentiment — one row has `GUJARAT` instead of
  `Gujarat` — `UPPER()`/`INITCAP()` it before grouping too, not just sentiment.
- Populated-geography split (n=192): Maharashtra 70, Gujarat 68 (+1 miscased), Rajasthan 42, Uttar
  Pradesh 7, Madhya Pradesh 4.
- **No package_id joined to 2 order_ids at this snapshot** — the 1-in-560 data glitch noted in the
  original read isn't present in the current package pull. Not confirmed fixed upstream (could just be
  that the specific affected package isn't in scope this time) — don't assume it's permanently resolved.

## Payment mode discussion (n=3,580)

Payment is explicitly discussed in 230/3,580 calls (6.4%, down slightly from 8% at n=755). Of those,
online payment (PhonePe/GPay/other — 150 calls, 65%) still outpaces cash (80 calls, 35%) by roughly the
same ~2x margin as the original read. Sample is bigger now but still a small minority of calls — keep
treating this as directional, not a firm COD-vs-online conclusion.

## Open items — status after this re-run

1. ~~Check whether low A3/A6/A7 audit scores correlate with orders needing >1 call~~ — **done, negative
   result** (see audit-checklist section above).
2. ~~Re-run the price-mismatch and conflict reads once n is in the thousands~~ — **done** (see both
   sections above) — both mechanisms hold up and generalize, with real refinements (mismatch flag covers
   3 sub-types; conflict themes now led by delivery delay, plus a new agent-driven-hostility cluster).
3. Decide if/how `PRODUCT_NOT_REQUIRED` reason calls should feed the main signal-engine (demand signal
   overlap) — still open, unchanged by this re-run.
4. New from this re-run: investigate the agent-initiated-hold share (27% of all actions, up from
   single digits) and the agent-driven-hostility conflict cluster — both suggest the LMD-agent side of
   these calls deserves its own closer look, not just the farmer side.
