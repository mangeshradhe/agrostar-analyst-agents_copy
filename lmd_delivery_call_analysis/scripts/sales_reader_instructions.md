# Instructions for a sales-call reader (one batch of AgroStar Mustard sales calls)

You are one of several Claude readers. Each reads ONE batch of ~25 sales-call transcripts, **one transcript at a time,
yourself**. The point of this exercise is a careful human-style reading of each call. **Do not use regex, keyword
rules, scripts or any automated way to produce a read.** The only code you run is the three helper commands below.

Working directory: `/Users/darpan/Documents/claude code/signal-engine/lmd_delivery_call_analysis`
Interpreter: `/usr/bin/python3` (always; the bare `python3` has no BigQuery libs).

## What you may and may not use
- USE ONLY the transcript printed for the call (and the order's items shown "for reference only").
- Do NOT look at, query or open anything else: not `disposition_data`, not any AI summary, not the LMD delivery
  outcome or reasons, not other files in `output/` (except the two example reads named below), not other calls.
- Do NOT edit any script or file other than your own read files. Do NOT run anything except the commands below.

## The loop (repeat until the command prints `ALL DONE`)
1. Print your next unread call:
   `/usr/bin/python3 scripts/sales_reads_next.py --batch NN`   (NN = your batch number)
2. Read the whole transcript. Write your read as a JSON file with the Write tool to
   `<YOUR SCRATCH DIR>/<call_id>.json` (exact scratch dir is given in your task message).
3. Save it: `/usr/bin/python3 scripts/sales_reads_save.py <call_id> <YOUR SCRATCH DIR>/<call_id>.json`
   - `SAVED ...` -> go to step 1.
   - `REJECTED ...` -> the checker found a problem (usually a quote that is not word-for-word in the transcript, or a
     value with no quote). Fix the read honestly (correct the quote from the transcript, or set the field to null /
     UNCLEAR if you cannot support it) and save again. Never invent a quote to get past the checker.
Do one call fully (read -> write -> save) before printing the next. Never read or write two calls together.

## First, see what a good read looks like
Run this once and study the two example reads (their quotes are copied word-for-word from their transcripts):
`/usr/bin/python3 -c "import json;[print(json.dumps(r['read'],ensure_ascii=False,indent=1)) for r in map(json.loads,open('output/sales_reads.jsonl')) if r['call_id'] in ('7472929055','7469672758')]"`

## The transcript
- Roman-script Hindi/Hinglish (sometimes Marathi/Gujarati/Marwari) from speech-to-text, so it is rough. `AGENT` is the
  AgroStar telecaller, `FARMER` is the farmer. Speaker labels can be wrong, especially after a third person joins
  (a dealer, a "senior officer", a relative) - attribute by content, and say so in `other_notable`.
- A glossary of known speech-to-text confusions is printed with every call. Important: **"cash per" is the product
  name Kasper (AgroStar's mustard hybrid), NOT a payment term.**
- Numbers are usually spelled as words ("tin sau tis rupaye"). Quote them as spoken; you may add the digits in
  brackets in the value, e.g. `"tin sau tis rupaye kilo (330 per kg)"`.

## The read (JSON). Every field is required; use null / [] when something is not in the transcript
```
{
 "story": "2-4 plain-English sentences: what happened on the call, in order. No digits you cannot support.",
 "commitment": {"value": "CLEAR_YES|AGREED_AFTER_PUSH|HESITANT|UNCLEAR|NO_ORDER_DISCUSSED", "quote": "verbatim or null"},
 "who_decided": {"value": "FARMER|FAMILY_MEMBER|UNCLEAR|null", "quote": "verbatim or null"},
 "product_discussed": {"value": "text or null", "quote": "verbatim or null"},
 "offers_pitched": [{"value": "cashback|lucky_draw|gift|discount|welcome_kit|other", "detail": "...", "quote": "verbatim"}],
 "price_stated": {"value": "amounts as spoken, or null", "quote": "verbatim or null"},
 "payment": {"mode": "COD|ADVANCE|UPI|OTHER|null", "readiness": "READY|MONEY_LATER|NOT_MENTIONED", "detail": "text or null", "quote": "verbatim or null"},
 "delivery_promise": {"value": "text or null", "quote": "verbatim or null"},
 "crop_and_need": {"crop": "text or null", "stage": "text or null", "fit": "FITS_ORDER|MISMATCH|UNCLEAR", "quote": "verbatim or null"},
 "objections": [{"value": "what worried the farmer", "agent_response": "how the agent answered", "quote": "verbatim"}],
 "agent_conduct_flags": [{"value": "pressure|cod_not_explained|kit_not_explained|exaggerated_claim|other", "detail": "...", "quote": "verbatim"}],
 "competition": {"value": "text or null", "quote": "verbatim or null"},
 "other_notable": null  OR  {"value": "anything important not captured above", "quote": "verbatim"},
 "reliability": {"language": "...", "transcript_quality": "GOOD|PARTLY_GARBLED|POOR|TOO_SHORT", "confidence": "HIGH|MEDIUM|LOW"}
}
```

### How to judge the enums (be consistent with the examples)
- **commitment**: `CLEAR_YES` = the farmer asked for it or said yes without being pushed. `AGREED_AFTER_PUSH` = the
  farmer first deferred/doubted (or the agent raised the quantity/added items) and only agreed after the agent
  pushed. `HESITANT` = doubt or deferral and no clear agreement. `UNCLEAR` = cannot tell (garbled, or only address
  verification). `NO_ORDER_DISCUSSED` = the order is never spoken about.
- **who_decided**: `FARMER` only if a quote shows the farmer deciding; otherwise `UNCLEAR`. `FAMILY_MEMBER` if the
  order is clearly made/approved by a relative.
- **payment.readiness**: `READY` only if the farmer says he can pay; `MONEY_LATER` if he says money is short / later;
  otherwise `NOT_MENTIONED`. Do not guess the mode: `COD` only if pay-on-delivery is said ("chhudava lena", "paisa
  tabhi dena jab ghar pahunche", "cash on delivery").
- **crop_and_need.fit**: `FITS_ORDER` if the crop discussed fits what is being sold; `MISMATCH` if the farmer's crop or
  plan does not fit (e.g. talks only about another crop); `UNCLEAR` if not enough is said.
- **offers_pitched**: only offers the agent actually pitches (lucky draw, coupons, commission/wallet credit, free bag,
  discounts, gifts for referring farmers). `other` needs a `detail`.
- **agent_conduct_flags**: `pressure` = urgency, "last day", "must take delivery / return not allowed", threats about the
  profile; `exaggerated_claim` = guarantees, "100 percent", yield or prize-chance claims that sound inflated or are
  internally inconsistent; `cod_not_explained` / `kit_not_explained` = the pay-on-delivery amount or the AGRO+ welcome
  kit is charged but never explained; `other` for anything else improper (coaching the farmer what to say, naming
  people as buyers the farmer does not know, conflicting prices or dates). Only flag what the transcript shows.

### Quote rules (the checker enforces these)
- Every non-null value needs a **verbatim** quote: copy the exact words from the printed transcript, 5-15 words.
  Copy the spelling exactly (e.g. the transcript may say `rupae`, not `rupaye`, `keo`, `peo`).
- To join two separate passages in one quote use ` ... ` between them; each part must appear word-for-word.
- Do not quote the order-items block; quote the transcript only.

## Honesty rules
- Never guess. If it is not said, it is null / NOT_MENTIONED / UNCLEAR. A thin honest read beats a rich invented one.
- Distinguish what the AGENT said from what the FARMER agreed to. An agent claim is not farmer agreement.
- If the transcript is too garbled to interpret a passage, say so in `other_notable` and lower `confidence`.
- If the same price, date or quantity is stated inconsistently, record both as spoken and flag it.

## When you finish
Reply with a SHORT report: how many calls saved, how many were rejected at least once, how many you marked POOR or
TOO_SHORT quality, and anything odd you noticed (e.g. a transcript that was not about mustard, repeated patterns).
Do not paste read contents.
