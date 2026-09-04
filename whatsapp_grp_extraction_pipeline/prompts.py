"""Prompt templates sent to `claude -p` by pipeline.py.

These encode the lessons learned building the COCO WhatsApp diagnosis
test-case dataset by hand: the .txt export drops WhatsApp's reply-to
threading, so a purely proximity-based pairing of "farmer images -> next
training response" is wrong often enough to matter. Every prompt below
tells the model to actually read the surrounding context and use judgment,
not just take the nearest match.
"""

TRAINING_TEAM_HINT = (
    "Training-team members typically include names like: Tarun Kumar, "
    "Pooja Training team, Jaya Training Team, Sujatha Bhambure Agrostar, "
    "Yogesh Agrostar, Sachin Agrostar, Puneet Sethi Agrostar, Sunil Jain "
    "Agrostar, Darpan Pathar Agrostar, Pankaj Jadhav Agrostar. But staff "
    "sometimes post from a phone-number-only WhatsApp identity instead of "
    "a display name (e.g. a trainer named in others' @-tags but not in "
    "the sender list) — if someone is clearly giving diagnoses/solutions "
    "authoritatively and is tagged/treated as staff by others, treat them "
    "as training team even if their name isn't in this list."
)


def extract_batch_prompt(batch_json, messages_path):
    return f"""Build structured agricultural diagnosis test cases from this slice of a COCO
Training & Technical Support WhatsApp chat export.

WHY THIS IS HARD: the plain-text .txt export does NOT preserve WhatsApp's
reply-to/quote links. A trainer's response that is visually a reply to one
specific farmer's photo just appears as an ordinary sequential line in the
export. When multiple farmers post images around the same time and the
trainer answers slightly out of order (which happens constantly), naive
"nearest response after the images" matching grabs the WRONG response. You
must actually read the surrounding conversation — who is being addressed,
whether the response's content (crop, dosage, wording) plausibly matches
which farmer's message — not just take whatever comes next chronologically.

{TRAINING_TEAM_HINT}

Your candidate clusters (pre-grouped by same-sender image bursts, but this
grouping is a DRAFT only — re-verify it, don't trust it blindly):
{batch_json}

Full parsed chat for context lookups by idx (date,time,sender,content,idx,dt):
{messages_path}

FOR EACH cluster: read a window of messages before/after it (idx-based) to
see real context, then decide:
1. Is this a genuine farmer issue + training-response pair, or junk (promo
   broadcast, sticker spam, a store-decoration/celebration photo, a
   screenshot of an assessment/quiz result, a "happy farmer" testimonial
   share, SKU/pricing question, or an unanswered query)? Skip junk
   entirely — do not invent a diagnosis for it.
2. Group/split images correctly per actual conversational context (not
   just the time-window heuristic) — a single farmer's issue may span
   images sent in two bursts a few minutes apart; two different farmers'
   images sent in the same minute are NOT one case.
3. If a case's real images were sent as "<Media omitted>" in the export
   (no filename), that's fine — leave Images empty, don't substitute a
   different farmer's images just to have something.

Extract fields exactly:
- images: JSON list of exact original filenames (e.g. "IMG-20260729-WA0007.jpg")
- symptoms: the farmer's actual complaint/question, meaning preserved,
  WhatsApp typos cleaned, Hindi/Hinglish rendered into clear English. If
  the farmer sent only images with generic tags/no real question ("image
  shared", "solution?", "expert tagged", nothing else informative), leave
  this BLANK ("") rather than writing boilerplate filler text — do not
  invent a description that says nothing.
- diagnosis: JSON list of concise problem names, taken from what the
  trainer actually said (do not invent). If no diagnosis name was given,
  use exactly: "Diagnosis not explicitly stated in the response."
- solution: JSON list, each product/step as a separate element, preserving
  dosage/concentration/method/frequency/acreage-or-pump-quantity/
  sequencing exactly as stated. Do not invent missing dosage. If no
  product was mentioned, use exactly: "No specific product/dosage
  mentioned in the response."
- source_expert: name of the training-team member who responded (or the
  phone-number identity if unnamed).
- source_date: date of the farmer's query/images (YYYY-MM-DD), not
  necessarily the response date.
- confidence: "High" (explicit, clearly the right pairing) / "Medium"
  (reasonably clear, something implicit or a delayed response) / "Low"
  (genuinely ambiguous linkage — use this rather than guessing).
- first_query_idx: the message idx of the first image/text in this case.

Do NOT invent anything not grounded in the actual chat text.

Output ONLY a JSON array of case objects with exactly these keys: images,
symptoms, diagnosis, solution, source_expert, source_date, confidence,
first_query_idx. No prose, no markdown fences, just the JSON array.
"""


def verify_flagged_prompt(flagged_json, messages_path):
    return f"""Re-verify a batch of already-extracted WhatsApp diagnosis test cases that
were flagged as at-risk for a specific known bug: the .txt chat export
drops reply-to threading, so when multiple farmers posted images in the
same short window, an earlier automated pass may have paired a farmer's
images with the WRONG training-team response (one meant for a different,
concurrently-posting farmer).

{TRAINING_TEAM_HINT}

Flagged cases to re-check, with the message idx of their first image and
the OTHER senders who also posted images nearby (the interleaving risk
signal):
{flagged_json}

Full parsed chat for context lookups by idx:
{messages_path}

For each flagged case: read a window of ~30 messages around first_query_idx
and determine the CORRECT pairing by actually reading who is being replied
to (crop mentioned, dosage details, direct @-tags, what a farmer explicitly
confirms afterward like "haan yehi hai" or corrects). Common patterns you
will find:
- The stored diagnosis/solution is actually correct — the flag was a false
  positive because the interleaved farmer's issue was clearly answered by
  a DIFFERENT, separate response you can also identify.
- The stored diagnosis/solution actually belongs to a different image set
  entirely (duplicated/copy-pasted from elsewhere) — the flagged case's
  real response is different, or was never given (no genuine answer exists
  in the window — in that case, this case should be DROPPED, not guessed).
- Two real cases got merged into one, or one real case got split wrongly.

For each case, output one of:
  {{"action": "keep", "case_id": "<id>"}}
  {{"action": "fix", "case_id": "<id>", "images": [...], "symptoms": "...",
    "diagnosis": [...], "solution": [...], "source_expert": "...",
    "confidence": "High|Medium|Low"}}
  {{"action": "drop", "case_id": "<id>", "reason": "..."}}
  {{"action": "add", "images": [...], "symptoms": "...", "diagnosis": [...],
    "solution": [...], "source_expert": "...", "confidence": "...",
    "source_date": "YYYY-MM-DD"}}
  (use "add" when you find a genuine second case hiding in the same
  interleaved window that the earlier pass missed entirely)

Output ONLY a JSON array of these action objects. No prose, no markdown fences.
"""
