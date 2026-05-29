# DVS DRISHTI — Design Document
> *Drishti (दृष्टि) — Sanskrit for "vision, sight, the ability to see what others miss"*

---

## What Is DRISHTI?

DRISHTI is an autonomous, self-learning analyst for the DVS (Delivery Via Saathi) program.

It is not a dashboard. It is not an alert bot.

It is a system that thinks like a senior analyst — watches the program continuously, builds memory across weeks, generates hypotheses backed by evidence, predicts problems before they happen, and learns from whether its predictions were right or wrong.

**The core idea:**
```
Human analyst = great judgment, limited time, forgets patterns across 1200 stores
DRISHTI       = perfect memory, no judgment, infinite patience
Together      = senior analyst who never sleeps
```

---

## The Seven Layers

```
┌─────────────────────────────────────────────────────┐
│  Layer 7 — LEARNING     Updates rules from outcomes │
│  Layer 6 — TRACKING     Did the fix work?           │
│  Layer 5 — ACTION       Who does what, by when      │
│  Layer 4 — TRIAGE       Is this worth fixing?       │
│  Layer 3 — SIZING       How much is this costing ₹  │
│  Layer 2 — DIAGNOSIS    Why is it happening         │
│  Layer 1 — DETECTION    Something is off            │
└─────────────────────────────────────────────────────┘
```

Most analytics tools stop at Layer 1. DRISHTI runs all seven in one pass.

---

## System Architecture

```
                    ┌─────────────────────┐
                    │   DRISHTI CORE      │
                    │                     │
   BigQuery ───────►│  ┌───────────────┐  │
                    │  │ Health Scanner │  │◄──── Monday context
   PromisedTAT ────►│  │ (5 fast KPIs) │  │      (3 lines from human)
                    │  └──────┬────────┘  │
   orderhistory ───►│         │           │
                    │  ┌──────▼────────┐  │
   shippingpkg ────►│  │ Causal Walker │  │
                    │  │ (traces WHY)  │  │
   institution ────►│  └──────┬────────┘  │
                    │         │           │
                    │  ┌──────▼────────┐  │
                    │  │Store + LMD    │  │
                    │  │Fingerprints   │  │
                    │  └──────┬────────┘  │
                    │         │           │
                    │  ┌──────▼────────┐  │
                    │  │Leaky Bucket   │  │
                    │  │Scorer         │  │
                    │  └──────┬────────┘  │
                    │         │           │
                    │  ┌──────▼────────┐  │
                    │  │Prediction     │  │◄──── Memory store
                    │  │Engine         │  │      (prior predictions,
                    │  └──────┬────────┘  │       outcomes, rules)
                    └─────────┼───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        Slack #dvs-ops  Slack #dvs-lmd  Slack #dvs-finance
        (Ground Ops)    (LMD team)      (OCP/MPD issues)
```

---

## Evolution Timeline

```
Nov 2025       Dec 2025       Feb 2026       May 2026       Sep 2026+
    │               │               │               │               │
THINKER ──────► JUNIOR ───────► SENIOR ───────► LEARNING ─────► PREDICTING
    │           ANALYST         ANALYST         SYSTEM          AUTONOMOUSLY
    │               │               │               │               │
Instruments     Describes       Explains        Closes          Knows what
the program     what            why it          the loop        it doesn't
Builds clean    happened        happened        Prediction      know. Flags
foundation      Daily           Causal          vs reality      own blind
Flags data      funnel          chains          tracked.        spots before
quality         SLA             Cross-signal    Rules           the human
issues          leaderboards    correlation     updated.        notices.
```

---

## Component 1 — Store Fingerprint

Every store gets a behavioral profile built from its own history.
Updated weekly. Compared against itself, not just the average.

```
STORE FINGERPRINT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Store:     Shree Ram Agro, Nashik District, Maharashtra
Period:    Apr 1 – Apr 30, 2026
Demand:    84 orders (rank: #12 in MH by volume)

METRICS vs SELF HISTORY
  Conv%      Jan:68%  Feb:52%  Mar:29%  Apr:17%  ↓↓ DECLINING
  SLA Hit%   Jan:91%  Feb:84%  Mar:71%  Apr:58%  ↓ DECLINING
  RTO%       Jan:6%   Feb:7%   Mar:9%   Apr:8%   → STABLE
  ON_HOLD%   Jan:4%   Feb:9%   Mar:18%  Apr:31%  ↑↑ RISING

METRICS vs DISTRICT PEERS
  Conv%    17%  vs district avg 52%   ❌ 35pp below peers
  SLA Hit% 58%  vs district avg 79%   ❌ 21pp below peers
  RTO%      8%  vs district avg 28%   ✅ 20pp BETTER than peers

KEY SIGNAL:
  RTO is healthy — farmers WANT this store's deliveries.
  But ON_HOLD rising — store can't fulfill.
  LMD dependency confirmed: HOLD_BY_LMD = 73% at this store
  Same LMD (Kantilal Patel) has 12% HOLD at neighboring stores.

DIAGNOSIS: LMD deprioritization. Not store failure.
```

---

## Component 2 — LMD Behavior Profile

Every LMD profiled across ALL stores they serve simultaneously.
This is where selective picking becomes visible.

```
LMD BEHAVIOR PROFILE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LMD:       Kantilal Patel | Territory: Central Gujarat
Stores:    11 stores | Orders: 847 | Period: Apr 2026

OVERALL    HOLD%: 41%  |  Delivery%: 49%  |  RTO%: 19%

STORE-LEVEL BREAKDOWN (sorted by HOLD%)
┌─────────────────────────┬────────┬────────┬──────────┐
│ Store                   │ Orders │ HOLD%  │ Variance │
├─────────────────────────┼────────┼────────┼──────────┤
│ KRUSHI MITRA, Anand     │   89   │  88%   │  +47pp   │ ← deprioritized
│ Shree Ram Agro, Nashik  │   84   │  73%   │  +32pp   │ ← deprioritized
│ Agro Palace, Kheda      │   71   │  61%   │  +20pp   │ ← deprioritized
│ Kisan Bazar, Nadiad     │  112   │  34%   │   avg    │
│ ...                     │   ...  │  ...   │   ...    │
│ Green Fields, Surat     │  143   │  12%   │  -29pp   │ ← prioritized
│ Farm Hub, Vadodara      │  167   │   9%   │  -32pp   │ ← prioritized
└─────────────────────────┴────────┴────────┴──────────┘

Variance:  88pp  (max HOLD - min HOLD across stores)
           Threshold for selective picking flag: >30pp
           Status: ❌ CONFIRMED SELECTIVE PICKING

DAY-OF-WEEK PATTERN
  KRUSHI MITRA pickups: 94% happen on Fridays only
  GREEN FIELDS pickups: Mon/Wed/Fri (3x/week)
  → Kantilal has a fixed route. KRUSHI MITRA is end-of-week only.

ORDER VALUE CORRELATION
  Orders < ₹800:   HOLD% = 79%
  Orders > ₹2,000: HOLD% = 11%
  Correlation:     -0.68  (strong — higher value = more likely picked)

HYPOTHESIS:
  Kantilal prioritizes by order value AND by route proximity.
  Stores off his primary route get Friday-only pickup.
  Low-value orders within any store are deprioritized further.

ALTERNATIVES TESTED:
  ❌ "Hard stores to reach" — other LMDs in same area have 22% HOLD
  ❌ "SKU issue" — same SKUs picked fine by other LMDs
  ❌ "Distance" — GPS data shows similar pickup distances

CONFIDENCE: HIGH
BUSINESS IMPACT: ₹1.4L/month in held orders across 3 deprioritized stores
```

---

## Component 3 — Leaky Bucket Score & Triage Stack

```
DVS DRISHTI — TRIAGE STACK | Generated: May 1, 2026
Based on: April 2026 behavioral profiles
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌─ PRIORITY 1 — ACT TODAY ──────────────────────────────────────┐
│                                                                │
│  #1  Shree Ram Agro, Nashik (MH)                              │
│      Leak: ₹1,21,800/month | 84 orders at risk                │
│      Root Cause: OCP block since Apr 28. MPD enabled.         │
│      Fix: Partner pays ₹18,000 → unlocks ₹54,000 credit       │
│      Team: CC team | Effort: 15 min call                      │
│      Confidence: HIGH | Similar cases: 6/6 recovered          │
│                                                                │
│  #2  Kisan Seva Kendra, Surat (GJ)                            │
│      Leak: ₹67,400/month | 71 orders at risk                  │
│      Root Cause: LMD Kantilal — HOLD_BY_LMD 73%              │
│      Fix: Reassign to alternate LMD (Suresh Patel available)  │
│      Team: Ops config | Effort: 30 min                        │
│      Confidence: HIGH | LMD pattern confirmed across 3 stores │
│                                                                │
│  #3  Bharat Agro, Akola (MH)                                  │
│      Leak: ₹43,200/month | 36 orders at risk                  │
│      Root Cause: Never triggers ON_HOLD — marks PACKED        │
│        without stock. RTO 52% (farmers rejecting empties)     │
│      Fix: Ground ops visit + stock audit                      │
│      Team: Ground Ops | Effort: Field visit                   │
│      Confidence: MEDIUM | Pattern seen at 4 similar stores    │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌─ PRIORITY 2 — ACT THIS WEEK ──────────────────────────────────┐
│  6 stores | Combined leak: ₹1,87,000/month                    │
│  [Expandable — see full list]                                  │
└────────────────────────────────────────────────────────────────┘

┌─ PRIORITY 3 — MONITOR ────────────────────────────────────────┐
│  14 stores | Chronic but stable. Low-effort nudge only.       │
│  Combined leak: ₹38,400/month (too small for field visits)    │
└────────────────────────────────────────────────────────────────┘

┌─ PRIORITY 4 — DO NOT INVEST TIME ─────────────────────────────┐
│  22 stores | Structural issues ops cannot fix.                │
│  Route to: Business expansion (coverage gaps) or              │
│            Supply chain (FC stockouts)                        │
└────────────────────────────────────────────────────────────────┘
```

---

## Component 4 — Prediction Card

```
DRISHTI PREDICTION — Filed: May 1, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ID:         PRED-2026-05-001
Store:      Kisan Seva Kendra, Surat, Gujarat
Prediction: Conv% will stay below 25% through May
            (currently 19%, was 58% in January)
Confidence: HIGH

Evidence Pattern:
  → Conv% declining 4 consecutive weeks ✓
  → LMD HOLD% at this store: 67% (threshold: >40%) ✓
  → Same LMD deprioritizes 2 other stores confirmed ✓
  → No restock issue (ON_HOLD% stable at 8%) ✓
  → RTO healthy (6%) — farmers want deliveries ✓

Root Cause:  LMD selective picking (not store failure)
Action Rec:  Reassign LMD before May 5
GMV at Risk: ₹67,400/month if unresolved

Outcome field: [PENDING — to be filled May 31]
Was prediction correct? [PENDING]
What happened? [PENDING]
```

---

## Component 5 — Prediction vs Reality Scorecard

```
DRISHTI SCORECARD — May 2026 Review
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PREDICTIONS MADE:    23
✅ Correct:          14   (61%)
↗️ Directionally right: 5  (22%)  — trend right, magnitude off
❌ Wrong:             3   (13%)  — opposite happened
❓ Inconclusive:      1   ( 4%)  — data gap, can't score

OVERALL ACCURACY:    83% (correct + directional)

━━ WHAT WENT RIGHT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Rule "LMD HOLD% > 40% → store underperforms" → 9/10 correct
Rule "OCP block + MPD enabled → recovers in 7 days" → 5/5 correct
Rule "Conv% declining 3 weeks → continues declining" → 7/8 correct

━━ WHAT WENT WRONG ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3 wrong predictions — all stores in MP — all predicted underperformance
Actual: MP performance IMPROVED in May

Reason detected: Routing engine update May 8
  → New PromisedTAT logic increased MP routing rate +12pp
  → Pre-May-8 patterns no longer apply to MP
  → Regime boundary tagged: May 8, 2026

Action: MP baselines reset from May 8. June predictions use new regime.

━━ RULES UPDATED ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEW:     "After routing engine update → wait 2 weeks before
          flagging state-level underperformance. Signal is noisy
          during transition."

UPDATED: "Conv% declining 3 weeks" threshold raised from 3→4 weeks
          (too many false positives in May)

RETIRED: "MP HOLD% > 35% → flag" — MP baseline shifted post-May 8

━━ JUNE PREDICTIONS (based on updated model) ━━━━━━

Program GMV forecast:   ₹9.2 Cr – ₹10.1 Cr  (Kharif demand spike)
At-risk stores:         8 stores flagged (Priority 1)
LMD flags:              2 LMDs showing early selective picking signal
Restock pressure:       High in Seeds/Fertilizer category
Regime watch:           MP — monitor 2 more weeks before new rules apply
```

---

## Component 6 — Daily Slack Brief Format

```
🔍 DVS DRISHTI  |  Daily Brief  |  May 30, 2026  |  09:02 AM

PROGRAM PULSE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Routing Rate   54.2%  ↓  (baseline 57.4%)   -3.2pp  ⚠️
Fulfillment    38.1%  ↓  (baseline 39.8%)   -1.7pp  ⚠️
RTO Rate       24.1%  →  (baseline 23.0%)   +1.1pp  ✅
ON_HOLD Queue  1,847  ↑  (+182 since yesterday)     ⚠️

TODAY'S FOCUS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Routing rate declining 3 weeks straight → ₹18L/month at risk
Root cause: License gap in Vidarbha (23 orders/day blocked)
Action: @business-expansion — 4 talukas need partner config

TRIAGE STACK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 Priority 1 (Act today):    3 stores  |  ₹2,32,200/month leak
🟡 Priority 2 (This week):    6 stores  |  ₹1,87,000/month leak
🟢 Priority 3 (Monitor):     14 stores  |  ₹38,400/month (low)
⚫ Priority 4 (Skip):        22 stores  |  Structural, not ops

PREDICTIONS TO WATCH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRED-2026-05-001  Kisan Seva Kendra  →  outcome due today
PRED-2026-05-007  Bharat Agro       →  4 days remaining
PRED-2026-05-012  Gujarat LMD cap   →  tracking (12 days)

DATA HEALTH ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ All tables fresh  |  delivery_shippingpackage: 2hr lag (normal)
```

---

## Build Roadmap

```
PHASE 0 — DESIGN (now)              ← YOU ARE HERE
  Design document, output formats, component specs

PHASE 1 — BACKTESTING (next)
  April data → store fingerprints → LMD profiles → leaky bucket scores
  May 1 predictions → May actuals → scorecard
  Validates the logic before going live

PHASE 2 — LIVE DAILY RUN
  Scheduler (9 AM IST cron)
  Health scanner → causal walker → triage → Slack output
  Monday context intake

PHASE 3 — MEMORY STORE
  Prediction cards persisted (BQ table or flat file)
  Outcome tracking automated
  Weekly scorecard auto-generated

PHASE 4 — SELF-LEARNING
  Rule hit rates tracked
  Thresholds auto-adjusted
  Regime detection (tech changes → baseline resets)
  Confidence calibration
```

---

## What DRISHTI Is Not

- Not a replacement for a human analyst
- Not a dashboard (it sends findings, not charts)
- Not always right — confidence is always explicit
- Not silent when uncertain — missing data is surfaced, not hidden

## What DRISHTI Is

The analyst who checks everything before your morning chai is ready.
