# Plan: reading every Mustard sales-call transcript (status: PLANNED — nothing executed yet)

Goal: for every Mustard order in the LMD dashboard, show **what actually happened on the sales call
that created the order** — read by Claude, transcript by transcript — so it can sit next to what the
farmer says when the LMD partner calls. Written 2026-10-01, reviewed with this PR (#61) before any
reading starts.

## Decisions (what was agreed, and what is still a default)

| # | Decision | Status |
|---|---|---|
| 1 | **Transcript only.** Nothing from `disposition_data` — no `ai_summary`, dispositions, `lead_interest`, `ai_insights_*`. | Agreed |
| 2 | **Claude reads every transcript.** No regex, keyword rules or any mechanical extraction. | Agreed |
| 3 | **Incremental.** A re-run reads only new / changed calls, never everything again. | Agreed |
| 4 | **Order of work:** Mustard orders, newest sales call first (30 Sep, then 29 Sep, …). **Batches of 50 calls; inside a batch, one transcript at a time.** | Agreed |
| 5 | **Shown per order in the LMD dashboard** ("What happened on the sales call"), plus cancelled-vs-delivered findings. | Agreed |
| 6 | Give the reader the order's items (name, qty, price) as reference. | Default — recommended, not yet confirmed |
| 7 | Run sequentially in the main session (no parallel agents). | Default — matches the user's standing preference |
| 8 | Read the 59 "probable"-match calls too, flagged as probable. | Default — recommended, not yet confirmed |

Change 6–8 before execution if you disagree; they are cheap to flip now and expensive later.

## Scope (measured 2026-10-01 from the local extract)

- 2,729 Mustard orders (any order with a `Mustard`-group line; group = `item_mst.sub_sub_product_group`).
- 1,930 have a matched sales call; **1,891 have a transcript** (59 of them "probable" matches, not "matched").
  Orders with no call / no transcript get an explicit "no sales call" / "no transcript" state — never a guess.
- Transcripts: 15.5M characters in total, median ~7,000 per call, longest ~55,000. ≈ 5M tokens to read,
  ≈ 6.5M including output. ~38 batches of 50.
- The design is product-group-agnostic; Mustard is only the first cohort.

## Rules for the reader (the prompt Claude uses, verbatim, for every call)

```
You are reading ONE AgroStar sales-call transcript (agent = "owner", farmer = "client"). Language may be
Hindi, Marathi, Gujarati or Hinglish, written in Roman script with speech-to-text errors. Read every turn.

Use ONLY what is said in the transcript. Do not use any summary, disposition or outcome. You are given the
order's items for reference only — report what the call shows, not what the order says.

Rules:
- Attribute every statement to the right speaker. An agent's claim is not farmer agreement.
- Never guess. If it is not said, the value is null.
- Every non-null field needs a short VERBATIM quote (original words, copied exactly from the transcript).
- If a passage is too garbled to interpret, say so; do not fill the gap.
- Distinguish: farmer asked for it / farmer agreed after the agent pushed / farmer said "I'll think".
- Record ₹ amounts and dates exactly as spoken.
- "other_notable" must capture anything important the fixed fields miss.
Return JSON matching schema v1, nothing else.
```

The LMD outcome (cancelled / on hold / reason) is **never shown to the reader** — it is joined afterwards,
so hindsight cannot colour the reading.

## What is captured per call (schema v1 — frozen after the calibration step)

Every field: `value` + `quote` (verbatim). `null` = not in the transcript.

| Field | Values / content |
|---|---|
| `story` | 2–3 plain sentences: what happened |
| `commitment` | `CLEAR_YES` · `AGREED_AFTER_PUSH` · `HESITANT` · `UNCLEAR` · `NO_ORDER_DISCUSSED` |
| `who_decided` | `FARMER` · `FAMILY_MEMBER` · `UNCLEAR` |
| `product_discussed` | variety/product named, packets agreed |
| `offers_pitched` | list: cashback, lucky draw, gift, discount, welcome kit, other |
| `price_stated` | ₹ amounts as said |
| `payment` | mode (COD / advance / UPI), dates, farmer's stated readiness (`READY` / `MONEY_LATER` / `NOT_MENTIONED`) |
| `delivery_promise` | timeline the agent gave |
| `crop_and_need` | crop, sowing stage, other crops, problems, `FITS_ORDER` · `MISMATCH` · `UNCLEAR` |
| `objections` | list: what worried the farmer + how the agent responded |
| `agent_conduct_flags` | list: pressure · COD/kit not explained · exaggerated claim · other — each with quote |
| `competition` | local purchase / price comparison, if said |
| `other_notable` | free text for anything unplanned |
| `reliability` | language, `transcript_quality` (`GOOD`/`PARTLY_GARBLED`/`POOR`/`TOO_SHORT`), confidence |

## Execution procedure

1. **Order:** Mustard calls sorted by sales-call date, newest first, ties by `call_id`. Batches of 50.
2. **One transcript at a time.** A helper prints exactly one call (its transcript + the order's items);
   Claude writes one JSON read; a validator saves it; only then the next call is printed. No call is ever
   read together with another, so nothing can bleed across calls.
3. **Validator (mechanical check on Claude's output — it does not read for meaning):** rejects and sends
   back any read where (a) JSON/enums don't match the schema, (b) a non-null field has no quote, (c) a quote is
   **not found verbatim** in that call's transcript (after whitespace/case normalisation), (d) a ₹ amount or
   date in a value doesn't appear in the transcript. A rejected read is redone, not patched.
4. **Checkpoint after each batch of 50:** counts by commitment / transcript quality, number of rejections and
   re-reads, 3 random reads re-checked against their transcripts. Stop and review before the next batch if
   anything looks off.
5. **Calibration first:** the first 30 calls (newest, mixed outcomes) are read and shown for review; schema is
   frozen as v1 only after that. Fields found missing go into `other_notable` and are promoted in v1.

## Storage and incremental re-runs

- Reads go to `output/sales_call_reads.jsonl` (gitignored — contains farmer PII and quotes), one line per
  call: `call_id`, `order_id`, `transcript_sha256`, `schema_version`, `read_at`, the JSON above.
- A call needs reading only if there is no line with the same `call_id` + `transcript_sha256` +
  `schema_version`. So after a data refresh, only **new orders, changed transcripts, or a schema bump** are
  read; everything else is reused.
- Planned scripts (not yet written): `scripts/sales_reads_next.py` (print next unread call),
  `scripts/sales_reads_save.py` (validate + save), `scripts/sales_reads_status.py` (progress / checkpoint).

## Dashboard (after reading starts)

- Order card gets "What happened on the sales call": story, chips (commitment, offers, payment, crop fit,
  conduct flags) and expandable evidence quotes. Not yet read → "not yet read"; no call → "no sales call".
- Filters: commitment level, offer pitched. Reads load from a sibling file like the transcripts (local only).
- Key findings: each signal compared on **cancelled/returned vs delivered** orders. A signal counts only if it
  clearly differs between the two groups — reading only the cancelled orders would make everything look
  meaningful. Plus a per-order sales-vs-delivery mismatch note.

## Known limits (state these whenever results are shown)

- Only ~71% of orders have a sales call; the matched call may not be the call where the farmer decided.
- Transcripts are rough speech-to-text; some calls will be `PARTLY_GARBLED`/`POOR` and get thin reads.
- Reads are a model's judgement of a transcript, not ground truth. ~5% get a second blind read to measure
  agreement before any number is quoted.
- Patterns are associations with LMD outcomes, not proven causes.
- Recordings exist only for roughly the last week, so most reads cannot be audio-verified.

---

## Execution log and how to resume (updated 2026-10-01; execution has started)

**Status:** reading is in progress. All reading is done by Claude instances (never regex/keyword rules). Reads are saved to
`output/sales_reads.jsonl` (local only, gitignored) under schema `v0-calibration` (not yet frozen to v1 — the 30-call
calibration review was skipped at the user's request to keep going; freeze it after reviewing a sample of reads).

**Changes from the plan above (all agreed with the user in conversation):**
- **Order of work changed** from "newest first" to a **priority cohort of 801 Mustard calls** (`scripts/sales_reads_make_batches.py`):
  A every order whose last LMD call was *cancelled* (215) · B every *on-hold* order with a non-logistics reason (86) ·
  C a seeded random sample of the remaining on-hold orders (250 of 557) · D a seeded random sample of *delivered* orders
  (250 of 458, the baseline). LMD outcomes are used **only to choose which calls to read**; a reader never sees them.
  Tiers are shuffled into 33 mixed batches of 25, so any finished subset is a fair cancelled-vs-delivered comparison.
  The other ~1,090 Mustard calls are **not yet read** (they can follow in the same way).
- **Parallel readers:** batches are read by several independent Claude readers at once (one batch each, one transcript at a
  time, same instructions in `scripts/sales_reader_instructions.md`, same quote checker). The first 19 calls were read in
  the main session.
- **Reader glossary** (printed with every call): speech-to-text confusions found while reading. Most important: **"cash per" =
  Kasper** (the product), not a payment term — this was misread in early reads and corrected (calls 7472929055,
  7472919320, 7472959037). Also `rayada/raya` = mustard, `bees` = beej (seed), `bavan ikkis` = 5221.
- **Number check relaxed:** many transcripts spell numbers as words, so the "numbers must appear in the transcript" check
  only runs when the transcript itself uses digits; otherwise the verbatim quote is the evidence.

**Quality controls in use:** one transcript at a time; every non-null field needs a verbatim quote that the checker finds in that
call's transcript (it rejected ~4 reads in the first 19 for typos / unsupported claims); reader never sees outcomes.
Still to do: second blind read of ~5% to measure agreement, and a cancelled-vs-delivered comparison before quoting any pattern.

**To check progress:** `/usr/bin/python3 scripts/sales_reads_status.py` (overall) — reads already saved are skipped automatically.
**To resume after a stop or a token-limit reset:** a new Claude session should (1) read this file and
`scripts/sales_reader_instructions.md`; (2) run `scripts/sales_reads_make_batches.py` only if `output/sales_batches/` is missing
(it is deterministic, seed 42, so batches are identical); (3) for each batch NN with unread calls
(`scripts/sales_reads_next.py --batch NN` does not print `ALL DONE`), launch a reader for it following the instructions file —
nothing already saved is re-read. Mustard calls outside the priority set can be added by widening the set in
`sales_reads_make_batches.py`. The dashboard does **not** yet show any of this.
