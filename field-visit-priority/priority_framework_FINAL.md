# Field Visit Priority Framework — Final Spec (SM & TM)

This is the definitive design, consolidated from the full brainstorm. `priority_logic_framework_notes.md`
has the working history/rationale trail; this file is what a developer builds SQL against. Every reason
type below maps to a plain sentence a rep reads directly — never a bare score.

## Structure: 3 independent tracks, one output table

| Track | What it covers | Ranking |
|---|---|---|
| **Active** | Existing partners, blended score | Weighted signals below |
| **Onboarding** | New partners not yet placing orders | Own ranking, not blended |
| **Churned Recovery** | Inactive partners who still owe money | Own ranking, not blended |

Output: one row per (rep_email, role, date, partner, track, rank, score, **primary_reason**). Filter by
`rep_email` to get that SM's or TM's list for that day. Designed to be ingested by a developer into the
app database on a refresh schedule — the app never needs the scoring logic, just the rows.

## Active track — signals, weights, and reasons

| Signal | v1 weight | Status | Reason shown to rep |
|---|---|---|---|
| Collection Recovery (OCP/DPD outstanding, **POG risk blended in** as a modifier — unsold stock raises this signal) | 45% | Sales-Ops sign-off on rank order (was 50% before rebalancing below); backtest attempt was inconclusive (measured gross balance growth, which conflates seasonal volume with real delinquency — needs a DPD-tier-crossing check to actually validate) | "₹X overdue by Y days" / "Unsold stock from a past order hasn't been confirmed sold" |
| Revenue Opportunity (purchase trend) | 25% | Sales-Ops sign-off was 35%; **reduced here** because the backtest showed raw ₹ revenue-decline doesn't hold up as a forward predictor (mean-reversion, likely due to lumpy B2B order timing) — recommend rebuilding on order-frequency trend (closer to the colleague doc's own R1 definition) rather than raw ₹, as a fast-follow | "Purchases have slowed — worth checking in" |
| Visit Gap (days since last visit) | 15% | Not weighted at all in the colleague's doc (used only as a tie-break there); the dashboard's original 4-signal engine weighted it directly. Given here its own slice since it's a plain, unambiguous reason type on its own | "Not visited in X days" |
| Targets / Monthly Category Gap | 15% | **New axis, not in either prior source.** Takes the weight vacated by dropping Returns (Returns is call-resolvable, doesn't need a visit). Category-weighted: a partner inherits urgency in proportion to how much of the lagging category they actually sell — confirmed via the reason wording itself, not re-litigated | "[Territory] is behind on this month's [Category] target, and this partner is a major [Category] seller" |

**These weights are v1 proposals, not backtested-and-confirmed** — flagged the same way the colleague's doc
flagged its own unconfirmed numbers. Good enough to build against now; revisit with a cleaner backtest
(DPD-tier crossing for Collection, order-frequency for Revenue) after the deadline.

## Day-of-week reasons (verified against real data, not assumed)

| Day | Verified signal | Reason shown to rep |
|---|---|---|
| Wed | Collection txns 5-7x normal weekday | "It's collections day and they owe us money" |
| **Thu** | `zoho_leads.Final_sd_date` completions up to 10x normal weekday | **"This lead needs their paperwork/limit allocation finished today so they're ready to order tomorrow"** |
| Fri | Order volume/revenue 2.3-2.7x; first-orders 3-4x | "Good day to convert this visit into a sale" / "Just onboarded — help them place their first order" |

These don't add a new score bucket — they reweight which existing reason surfaces as primary for that day's
run of the query.

## Capacity ceiling (hard limit, not a score)

- Per-rep quota = that rep's actual span of control (verified: SM median 23, range 2-76; TM median 59,
  range 13-181 — **use real per-rep counts, not a fleet constant**) × required cadence (SM 2x/month,
  TM 1x/month minimum) ÷ working days remaining in the cadence period.
- Suppression: exclude a partner entirely if already visited within their role's cadence window.
- Working days: Mon-Sat. Minus 3 national holidays confirmed for FY27 (15 Aug, 2 Oct, 26 Jan) — **known
  undercount**, since the broader festival/restricted-holiday calendar has no table anywhere in this
  dataset. Flag this to whoever consumes the capacity number.

## Onboarding track

- Reason: "Almost ready to become a customer — help push them over the line" (about-to-close — threshold
  from `zoho_leads.stage`, e.g. the `8x_Pending for...`/`Closed Won but Cheque Pending` codes; exact cutoff
  still needs a decision) or "Just onboarded — help them place their first order" (Thu/Fri linkage above).
- Tie-break: longest visit gap surfaces first among equal-priority leads.

## Churned Recovery track

- Definition (user, 2026-08-17): an INACTIVE partner (`okr_data_live.status='INACTIVE'`) with outstanding
  balance still owed (same OCP/pending-amount CTE as Collection Recovery, just re-pointed at INACTIVE).
- Reason: "Inactive, but still owes ₹X — this is a recovery visit, not a sales visit."
- Verified: **3,831 partners, ₹37.23 Cr outstanding** — currently invisible to any existing scoring logic.

## Explicitly out of scope
- **Returns/Complaints** — dropped (user decision): resolvable by phone call, doesn't need a visit, and no
  ticketing table exists anyway. Its weight moved to Targets.

## Still open before SQL is finalized
- Exact slot split across the 3 tracks in the daily output — to be data-derived (e.g. relative size of the
  ₹37.23 Cr churned exposure vs. active collections), not yet computed.
- Onboarding "about-to-close" stage threshold — data exists, exact cutoff not yet chosen.
- Territory-name join key for the Targets axis — use `revised_territory` (target side) matched to
  `sale_return_b2c_b2b.territory`, filtering `revised_territory IS NOT NULL` (drops the 1 null-territory
  row); Jalna/Dhule correctly show 0% attainment, not a bug.
- `aop_offline_online_fy27` gross/net/return columns are in **lacs** — always multiply by 100,000.
