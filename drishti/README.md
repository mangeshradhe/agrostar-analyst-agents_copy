# DVS DRISHTI — Operational README

> *Drishti (दृष्टि) — Sanskrit for "vision, sight, the ability to see what others miss"*

---

## What Is DRISHTI?

DRISHTI is an autonomous analyst for the DVS (Delivery Via Saathi) program. Every morning at 9 AM IST, it pulls fresh data from BigQuery, runs seven sequential analysis layers — detection, diagnosis, sizing (₹ impact), triage, action assignment, outcome tracking, and rule learning — and fires targeted Slack messages to the teams who need to act. It is not a dashboard. It does not wait for someone to open a report. It has memory across weeks: it files predictions daily, scores them seven days later, and updates its own detection thresholds based on what it got right or wrong. One analyst covering 1,200+ stores simultaneously.

---

## How to Run

```bash
python drishti/run.py
```

Required environment variables:

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token for posting to Slack channels |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON for BigQuery access |

The script runs the full seven-layer pipeline and exits. Schedule it via cron at 9 AM IST:

```
0 3 * * * /usr/bin/python3 /path/to/drishti/run.py  # 9 AM IST = 3:30 UTC
```

---

## Slack Output Channels

| Channel | Audience | What Gets Posted |
|---|---|---|
| `#dvs-drishti` | Analysts / leadership | Full daily brief: program pulse, triage stack, predictions, data health |
| `#dvs-ops` | Ground ops team | Priority 1 store actions — store name, root cause, exact fix, effort estimate |
| `#dvs-finance` | Finance / CC team | OCP blocks, MPD recovery opportunities, ₹ at risk per partner |
| `#dvs-lmd` | LMD ops team | LMD behavior flags — selective picking, overload, day-of-week patterns |

Each ops/finance/LMD message is self-contained. The receiving team does not need to read the full brief.

---

## File Structure

```
drishti/
  run.py        — Entry point. Orchestrates the seven layers, posts to Slack.
  config.py     — Thresholds, baselines, channel IDs, rule weights.
  memory.json   — Persisted prediction cards + outcome scores. Updated daily.
  DESIGN.md     — Full architecture, component specs, output format examples.
  README.md     — This file.
```

---

## Key Thresholds

These are the values that trigger alerts. Edit in `config.py`.

| Signal | Warning | Critical |
|---|---|---|
| DVS PACKED status age | >24 hrs | >48 hrs |
| FC dispatch age (PACKED at FC) | >72 hrs | >120 hrs |
| LMD HOLD% at a single store | >25% | >40% |
| LMD HOLD% variance across stores | >30pp | >60pp (selective picking confirmed) |
| Conv% decline (weeks consecutive) | 3 weeks | 4 weeks |
| Routing rate vs baseline | -2pp | -4pp |
| ON_HOLD queue growth (day-on-day) | +100 | +250 |

---

## Memory and Prediction Loop

1. Each run files prediction cards to `memory.json` — store, predicted metric, confidence, evidence pattern, GMV at risk.
2. Seven days later, the same run reads those cards and scores them: Correct / Directionally Right / Wrong / Inconclusive.
3. Rule hit rates are tracked across all predictions. Rules that are wrong >30% of the time are flagged for review.
4. Thresholds in `config.py` are candidates for tightening or loosening based on the scorecard.
5. Regime boundaries (e.g., a routing engine update) are tagged in memory so pre-regime patterns are not applied to post-regime data.

The monthly review (see below) is where a human reads the scorecard and decides which rule changes to commit.

---

## How to Update Baselines

After each monthly review, update `config.py`:

```python
BASELINES = {
    "routing_rate": 0.574,      # update after each month-end review
    "fulfillment_rate": 0.398,
    "rto_rate": 0.230,
    "lmd_hold_pct": 0.18,
}
```

Rules of thumb:
- Use the prior month's actuals as the new baseline, not the target.
- If a regime change happened mid-month (tech release, routing update, new FC), use only post-change data.
- Tag the change date as a regime boundary in `memory.json` so predictions don't cross it.

---

## Four Dead Store Categories

Priority 4 in the triage stack means "ops cannot fix this — route elsewhere."

| Category | What It Means | Who Handles It |
|---|---|---|
| Coverage gap | No licensed partner exists in that pin / taluka | Business expansion — add partner config |
| FC stockout | Item not in any warehouse within PromisedTAT range | Supply chain — procurement / transfer |
| Structural RTO | RTO% >50% for 8+ weeks — farmers in that area consistently reject | Category / product team — wrong SKU-market fit |
| Partner exit intent | Store marked DVS active but partner has not scanned any package in 30+ days | Partner success — reactivation or offboarding |

Spending ops effort on Priority 4 stores is waste. DRISHTI flags them so they are not confused with fixable problems.

---

## Three LMD Flag Types

| Flag | Trigger | What It Means Operationally |
|---|---|---|
| SELECTIVE PICKING | HOLD% variance across stores >30pp for same LMD | LMD is deliberately skipping certain stores or deprioritizing low-value orders. Needs reassignment or route restructure, not coaching. |
| OVERLOADED | LMD HOLD% >40% uniformly across all stores they serve | Too many orders, not enough capacity. Fix: add a second LMD to the territory or reduce assignments. |
| ROUTE INACTION | LMD has not scanned any package in 3+ calendar days (non-holiday) | LMD may be unreachable, sick, or abandoned. Ops follow-up same day. |

All three flags appear in `#dvs-lmd` with the LMD name, stores affected, HOLD%, and a suggested action.

---

## Known LMD Profiles

These profiles are built from historical data and updated monthly. They inform confidence scoring on new predictions.

| LMD | Pattern | Operational Note |
|---|---|---|
| Ravikaran Singh | Uniformly high HOLD% across all stores (overloaded) | Territory has grown faster than his capacity. Not selective — just over-assigned. Solution: split territory or add a second LMD. |
| narayan.up | 90pp HOLD% variance across stores; order-value correlation confirmed | Selective picking confirmed. Deprioritizes stores off his primary route and filters low-value orders. High-confidence flag — has persisted across 3 months. |
| jignesh.jani | HOLD% consistently <10%, delivery rate >75%, on-time SLA >90% | Benchmark LMD. Used as the reference profile when evaluating whether a store's problems are LMD-driven or store-driven. |

New LMD profiles are auto-generated monthly from BigQuery and appended to `config.py KNOWN_LMD_PROFILES`.

---

## What DRISHTI Is Not

- Not a replacement for a human analyst — confidence is always explicit, uncertain signals are surfaced
- Not a dashboard — it sends findings, not charts; you do not need to open anything
- Not always right — the scorecard tracks its own error rate, which is shown in the daily brief
