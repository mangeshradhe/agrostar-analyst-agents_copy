# TO Request Approval/Rejection Analysis (started 21 Sep 2026)

Question: of the transfer *requests* raised, how many get approved vs rejected — cut by
request type (UF / Budget balancing) and by geography tier (Intra-FC / Inter-City /
Inter-State), month on month from Jan 2026 to date.

## Source & scope

Table: `catalog_views.catalog_management_transferorders` — the CRM approval-funnel table,
**not** `transfer_header`/`invoiced_transfer_report` (those are the executed-TO side covered
in `TRANSFER_ANALYSIS_METHODOLOGY.md`). This table is where a system-generated recommendation
gets an approve/reject decision before it becomes (or doesn't become) a live TO.

Scoped to `transfer_reason IN ('System TO - UF case','System TO - Budget balancing','System TO - Adhoc')`.
Manually-created reasons (Current BM, Coming BM, UF Fulfil, DRR Fulfil, POS-STO, Debulking, ADHOC, NE,
Sale mvmnt, FC SPCE MV) were checked and **never** carry `WAITING_FOR_APPROVAL`/`REJECTED` status —
they go straight to RECEIVED/CANCELLED with no approval workflow, so they're out of scope for an
"approved vs rejected" question. Adhoc is tiny volume (9-730/month) and noisy; excluded from the
final grouped tables on request but still queryable.

## Definitions (confirm before reusing)

- **Approved** = `action_status = 'APPROVED'` — counts regardless of what happens to the TO
  afterward (even if later cancelled downstream; that's a separate WMS event, not un-approval).
- **Rejected** = `transfer_status = 'REJECTED'` **AND `action_by != 'SYSTEM'`**. System-attributed
  rejections (`action_by = 'SYSTEM'`, reasons "Data archived by system" / "rejected during update
  cycle" — 16,920 rows Jan-Sep) are **excluded from the analysis entirely** (not counted in either
  numerator or denominator) — confirmed with Darpan 21 Sep: these are the recommendation engine
  archiving/superseding a stale suggestion before any human looked at it, not a real reject
  decision. Genuine human rejections (named `action_by`, `action_status='REJECTED'`) use reasons
  like "Stock required for own FC demand," "Excess Stock not available Pan India," "transfer qty
  reduced to zero during re-allocation," "Supply arriving shortly."
- **Pending/Other** = `WAITING_FOR_APPROVAL`/`PENDING`/`LOCKED`, not yet actioned. Small every
  month except the current partial month (right-censoring — same cohort-discipline caveat as the
  rest of this project: don't read a partial-month approval rate as final).
- **This exclusion matters a lot**: pre-exclusion, Budget-balancing looked like it was failing
  (26-42% approved). Genuine-decision-only, it's actually **80-88% approved** — most of the
  apparent rejection was system churn, not a person saying no. Always apply the `action_by`
  filter; the un-filtered numbers below are kept only as a "what it looks like if you forget this
  filter" comparison, not a number to quote.

## Geography tiers

Same convention as `TRANSFER_ANALYSIS_METHODOLOGY.md`: join `from_facility`/`to_facility` to
`location_mst.location_id` (100% match, no join loss) → Intra-FC = same city, Inter-City = same
state/different city, Inter-State = different state.

## Headline findings (through 21 Sep 2026, SYSTEM-rejects excluded)

1. **Budget-balancing has zero volume before Jul 2026** — the feature only went live in July.
   Jan-Jun is UF-only (+ a trickle of Adhoc from April). Don't compare Budget month-on-month
   before July, and don't compute a "Jan-Jun Budget" baseline — there isn't one.
2. **UF approval is strong almost everywhere**: Intra-FC 91-97%, Inter-City 89-97%. The one
   soft spot is **UF Inter-State in January (44.8% approved)** — recovers to 70%+ by Feb and
   climbs to ~80% by Jul-Sep. Worth a look if picking a next thread.
3. **Budget-balancing approval is actually strong once system-noise is stripped out**: 80-88%
   approved every month since launch (not 26-42% as the raw/unfiltered numbers suggested — see
   caveat above). Remaining genuine rejections skew toward "stock required for own FC" / "excess
   stock not available pan-India" / "supply arriving shortly" — real network-constraint reasons,
   not an approval-process problem.
4. **Approval still falls somewhat with distance for UF** (Intra-FC > Inter-City > Inter-State),
   but the gap is far smaller than it first appeared, and for Budget-balancing Inter-City and
   Inter-State are roughly on par (80-88% both).
5. **Budget-balancing never has an Intra-FC row** — it never tries to balance stock within the
   same city, in any month. Structural, not a data gap (checked: 0 rows in the full 3-way cut).
6. Inter-State request volume jumped materially once Budget-balancing launched in July, adding
   its own Inter-State volume on top of UF's.

## Tables delivered this session (in chat, not saved as artifact — user wanted to view inline)

- Month × Type (UF/Budget/Adhoc): requested/approved/rejected/pending + approval%/rejection%.
- Month × Geo tier (all types combined): same metrics.
- Full grain Month × Type × Tier (Adhoc included) — pivot-ready flat table.
- Same full grain, **Adhoc excluded**, re-sorted **Type → Tier → Month** so each Type+Tier trend
  reads top-to-bottom in one block (this is the cut Darpan asked to keep coming back to for
  UF/Intra-FC specifically).

Queries behind all four tables are in `08_to_approval_rejection_queries.sql`.

## Part 2 — Time-to-action (started 21 Sep 2026, same session)

Question: of **Approved** requests only (Rejected excluded per Darpan's request — revisit later if
needed), how long between `created_on` and `action_on`? Same scope/exclusions as Part 1 (UF +
Budget, SYSTEM action_by excluded, Adhoc excluded). Delta = `TIMESTAMP_DIFF(action_on, created_on,
MINUTE)/60.0` hours. Checked first: no NULL `action_on` and no negative deltas for
`action_status IN ('APPROVED','REJECTED')` — clean.

Two tables produced, Type → Tier → Month grain:
- **Percentile summary**: N approved, median/P90/P95/mean hours.
- **Cumulative % approved within N hours**: 1,2,3,4,5,6,7,8,9,12,24,48,72,96,120h.

### Headline findings

1. **UF approves fast**: ~25-50% within the first hour, ~85-94% within 24h, essentially all done
   by 48-72h, consistently every month Jan-Sep.
2. **Budget-balancing approves much slower**: only 2-15% within the first hour; regularly needs
   48-96+ hours to clear 80%+ of the queue. **Aug 2026 was the worst month** — only 38.5% approved
   within 48h, needed 96h to reach 83.8% (Inter-City) / 90.1% (Inter-State).
3. **Budget median time-to-approve is unstable month to month**: 3.3h (Jul) → 61.6h (Aug) → 12.8h
   (Sep) for Inter-State; similar swing for Inter-City. This instability (not just slowness) reads
   more like a queue/staffing/process issue than a stock-availability constraint — worth checking
   who's actioning Budget requests (`action_by`) and whether Aug had a specific backlog event.
4. UF turnaround is comparatively stable across months and tiers — no equivalent Aug anomaly.

Queries: `08_to_approval_rejection_queries.sql`, query 5 (turnaround/TAT).

## Open items / next session

- **Explain the Aug 2026 Budget-balancing turnaround spike** (median 61.6h Inter-State, worst
  month by far) — check `action_by` concentration, headcount/leave, or a backlog event. This is
  the most actionable open thread right now.
- **Look at UF Inter-State January (44.8% approved)** — the one visibly weak cell after the
  system-reject exclusion; recovers within a month, not yet explained.
- **Recheck UF/Intra-FC and Inter-City Sep numbers** once September fully closes — Sep pending
  buckets (12-22) are elevated vs prior months (0-13), some censoring risk. Same applies to Sep
  turnaround percentiles in Part 2 — partial month, will shift.
- Turnaround analysis currently **Approved-only**; Rejected excluded on request — revisit if a
  full (Approved+Rejected) time-to-decision view is wanted later.
- Not yet asked: should Adhoc be folded back in as a third bucket, or dropped from scope
  permanently? Currently just excluded per the last table request, not a firm decision.
- Not yet checked: reject-reason breakdown *by month*, genuine-rejections only (only pulled
  reject reasons in aggregate for Jan-Sep combined so far) — would sharpen the Budget-balancing
  "why" beyond "stock availability."
- Not yet checked: `reject_remarks` (free text) — could hold detail beyond `reject_reason`
  category, unexplored so far.
- Not yet checked: turnaround by `action_by` (approver) — flagged as the natural next step to
  explain the Aug spike.
- No SKU or FC-level cut done yet — everything so far is Month × Type × Tier only, per what was
  asked.
