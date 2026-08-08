# Pitch Pipeline Bot

Turns a training-team xlsx of new AgroStar products into everything the sales
training app needs: 6-language pitch content, Hinglish roleplay scripts,
per-row JSON, product thumbnails, and updated product lists.

No `ANTHROPIC_API_KEY` needed — every language-dependent step shells out to
your existing `claude` CLI login (headless `claude -p`), the same account you
use for Claude Code.

## Requirements

- `claude` CLI installed and logged in (`claude -h` should work; if not, run
  `claude` once interactively and log in).
- `pip install openpyxl`
- A working directory that already has (or will have) `pitches_final.xlsx`,
  `scripts/`, `json/`, `assets/`, and `productlist_<lang>.json` — e.g. the
  project's `~/claude_workspace` folder.

## Usage

Run the whole pipeline for a new batch of products:

```sh
python3 pipeline.py run New_product_additions.xlsx --workdir ~/claude_workspace
```

This is safe to re-run — every stage skips work it already did (based on
existing output files), so interrupting and re-running just resumes.

Run only some stages (comma-separated, no spaces):

```sh
python3 pipeline.py run New_product_additions.xlsx --workdir ~/claude_workspace --only translate,scripts
```

Stages, in order: `translate`, `merge-xlsx`, `scripts`, `build-json`,
`thumbnails`, `productlist`.

Control parallelism (default 6 concurrent `claude -p` calls):

```sh
python3 pipeline.py run New_product_additions.xlsx --jobs 3
```

### Audio

Audio comes from the training team via Google Drive, not the internet, and
filenames there aren't predictable — so this step is a human-in-the-loop
helper, not full automation:

```sh
python3 pipeline.py match-audio ~/Downloads/drive_audio_batch --workdir ~/claude_workspace
```

For each expected `audio/<Name>_<lang>.wav`, it suggests candidate files
(matched by product-name keyword) from the folder you point it at, you pick
one, and it copies + renames it into `audio/`. Files already in `audio/` are
skipped, so it's safe to re-run as more recordings arrive.

Once you have S3 credentials, it prints the `aws s3 sync` command to push the
staged `audio/` folder up — wire that up once, then it's a one-liner per
batch.

## What each stage does

| Stage | Type | Output |
|---|---|---|
| `translate` | `claude -p`, parallel | `pipeline_out/translations/<Product>.json` — Hinglish/Gujarati/Marathi/Telugu/Kannada KSP+objections, plus a transliterated `displayName` per language (including Hindi) |
| `merge-xlsx` | deterministic | appends 6 rows/product to `pitches_final.xlsx` (creates it if missing) |
| `scripts` | `claude -p`, parallel | `scripts/<Product>.txt` — Field-vs-Retailer Hinglish roleplay |
| `build-json` | deterministic | `json/<slug>_<lang>.json` per row, for the app |
| `thumbnails` | `claude -p` (WebSearch+Bash), parallel | `assets/<slug>.jpg` — real product photo where found, generic placeholder (clearly flagged) otherwise |
| `productlist` | deterministic | appends new entries to `productlist_<lang>.json`, `category: "New Products"`; warns if any `thumbnail` path doesn't resolve to a real file |

## Files

- `pipeline.py` — CLI entrypoint and all stage logic
- `prompts.py` — the prompt templates sent to `claude -p` for translate/scripts/thumbnails
- `claude_cli.py` — subprocess wrapper around the `claude` CLI, with retries

## Known limits

- Thumbnail search quality depends on what's findable on the web — always
  spot-check a few, especially for newly-named products.
- Translation/roleplay quality should be spot-checked by a native speaker
  before it goes out to reps, same as any MT output.
- Audio matching is assistive, not automatic — it can't guess a file's
  content from an arbitrary Drive filename.
