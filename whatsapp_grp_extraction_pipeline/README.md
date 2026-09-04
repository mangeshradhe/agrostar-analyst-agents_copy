# WhatsApp Group Extraction Pipeline

Turns a COCO Training & Technical Support WhatsApp chat export into a
structured agricultural diagnosis test-case dataset (Excel), cross-checked
against an Issues V4 taxonomy CSV.

No `ANTHROPIC_API_KEY` needed — the judgment-heavy steps shell out to your
existing `claude` CLI login (headless `claude -p`), the same account you use
for Claude Code.

## Why this exists

Built after a manual extraction run (Sep 2026) that hit the same bug
repeatedly: the plain-text `.txt` chat export drops WhatsApp's reply-to/quote
links. When multiple farmers post images close together and a trainer
answers slightly out of order (constant in this group), naive "nearest
response after the images" matching grabs the **wrong** reply. This pipeline
bakes in the checks that caught those bugs by hand:

- a **duplicate-response-text scan** — two different cases sharing
  byte-identical solution text is a strong signal one stole the other's reply
- an **interleaving-risk scan** — other farmers posting images in the same
  window as a case, before its matched response (the exact pattern behind
  every bug found)
- a **verify** stage that re-reads raw chat context for every flagged case
  via `claude -p` and fixes/drops/splits it as needed

It still cannot fully substitute for the reply-arrow WhatsApp itself shows —
if something looks wrong, a screenshot of the actual thread (showing the
reply link) is the fastest way to resolve it; there's no way to reconstruct
that from the .txt export alone.

## Requirements

- `claude` CLI installed and logged in (`claude -h` should work).
- `pip install openpyxl`
- The WhatsApp chat export as a `.zip` (from WhatsApp's own "Export Chat" —
  include media) or an already-unzipped folder containing the `.txt` and
  media files.
- The Issues V4 taxonomy CSV (must have an `issueName` column).

## Usage

```sh
python3 pipeline.py run "WhatsApp Chat export.zip" \
    --v4 "Issues V4 - issueProducts.csv" \
    --workdir ./out
```

This is safe to re-run — every stage skips work it already did (based on
existing output files in `--workdir`), so interrupting and re-running just
resumes. Delete a specific output file (or pass `--only`) to force that
stage to redo.

Run only some stages:

```sh
python3 pipeline.py run export.zip --v4 IssuesV4.csv --only parse,cluster,extract
```

Stages, in order: `parse`, `cluster`, `extract`, `scan`, `verify`, `map-v4`,
`build-xlsx`.

Control parallelism and how many candidate clusters go into each `claude -p`
call (default 6 concurrent calls, 25 clusters/call):

```sh
python3 pipeline.py run export.zip --v4 IssuesV4.csv --jobs 3 --chunk-size 15
```

By default the final xlsx drops Low-confidence cases and cases with no
recoverable images (media the export listed as `<Media omitted>`, no
filename). Keep them in if you want to review everything:

```sh
python3 pipeline.py run export.zip --v4 IssuesV4.csv --keep-low-confidence --keep-blank-images
```

## What each stage does

| Stage | Type | Output |
|---|---|---|
| `parse` | deterministic | `messages.json` — every chat line parsed into `{date,time,sender,content,idx,dt}` |
| `cluster` | deterministic | `clusters.json` — draft groups of same-sender image bursts (≤10min gaps) — a *starting point* only, re-verified in `extract` |
| `extract` | `claude -p`, parallel | `extracted.json` — one structured case per genuine farmer issue: images, symptoms, diagnosis, solution, source_expert, source_date, confidence |
| `scan` | deterministic | `scan_report.json` — duplicate-response-text groups + interleaving-risk cases (the two bug signatures) |
| `verify` | `claude -p`, parallel | `verified.json` — every flagged case re-checked against raw context; fixed, dropped, split, or confirmed |
| `map-v4` | deterministic | `mapped.json` — each diagnosis term fuzzy-matched against Issues V4's `issueName` list (keyword-overlap gated, not pure string-similarity, to avoid false matches like "Leaf blast" → "Leaf Blotch"); falls back to the chat-derived phrasing when no confident match exists |
| `build-xlsx` | deterministic | the final `.xlsx` — columns: TC ID, Image Count, Images (original files), S3 Image URLs, Symptoms / User Input, Expected Diagnosis, Expected Solution, Source Expert, Confidence, Source Date |

## Files

- `pipeline.py` — CLI entrypoint and all stage logic
- `prompts.py` — prompt templates sent to `claude -p` for extract/verify
- `claude_cli.py` — subprocess wrapper around the `claude` CLI, with retries

## Known limits

- Cannot recover WhatsApp's actual reply-to link — the `scan`/`verify`
  stages catch most of the resulting mispairings, but not all; treat
  Low-confidence output (and honestly, some Medium) as needing a human
  glance before it goes into an eval set.
- A farmer's image sent as `<Media omitted>` in the export has no
  recoverable filename — those cases get `images: []` rather than a
  guessed/wrong filename.
- V4 mapping quality depends on the taxonomy actually having a matching
  crop-appropriate entry; it deliberately does *not* force a match across
  crops (e.g. won't map a pomegranate issue to a mango-only V4 entry).
- `extract`/`verify` output quality should be spot-checked, same as any
  LLM-assisted extraction — especially early runs against a new group's
  chat, where the training team's phrasing/abbreviation habits may differ.
