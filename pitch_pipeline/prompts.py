"""Prompt templates for the pitch pipeline's headless `claude -p` calls."""

ALL_LANGS = ["Hindi", "English", "Gujarati", "Marathi", "Telugu", "Kannada"]


def target_langs_for(source_lang):
    """English (Hinglish transliteration of the source language) plus every
    native-script language other than the source itself — these need full
    ksp/oh translation. The source language itself only needs a displayName."""
    return ["English"] + [l for l in ALL_LANGS if l not in (source_lang, "English")]


def translate_prompt(product, source_lang, ksp_src, oh_src, output_path):
    targets = target_langs_for(source_lang)

    shape_lines = [f'  "{source_lang}": {{"displayName": "..."}}']
    shape_lines += [
        f'  "{lang}": {{"ksp": "...", "oh": "...", "displayName": "..."}}'
        for lang in targets
    ]
    shape = f'{{\n  "product": "{product}",\n' + ",\n".join(shape_lines) + "\n}"

    return f"""Read the following {source_lang} agri-input sales pitch content for the product "{product}":

KEY SELLING POINTS ({source_lang}):
{ksp_src}

OBJECTION HANDLING ({source_lang}, pairs of a warning-symbol objection line and a check-mark answer line):
{oh_src}

TASK: Translate/transliterate this into these {len(targets)} language variants —
{', '.join(targets)} — and also produce a short transliterated product display name
for each of those PLUS for {source_lang} itself (a {source_lang}-script rendering of
the product name "{product}", since {source_lang} needs a display name too even
though its pitch content doesn't need translating). Save everything as one JSON file
to {output_path} using the Write tool.

RULES (follow exactly):
1. "English" variant = Romanized "{source_lang}-lish" (e.g. Hinglish if source is
   Hindi, Marathi-in-Roman if source is Marathi), NOT an English translation. Write
   the SAME {source_lang} words/meaning using Roman/English letters (transliteration),
   light grammar cleanup only. Do NOT translate the meaning into actual English prose.
2. Every other listed variant = translate the {source_lang} meaning into that
   language's own native script. Full translation, not transliteration.
3. In ALL variants, do NOT translate or transliterate: technical/chemical/agronomy
   terms already in English in the source (e.g. Insecticide, Fungicide, Systemic,
   Broad-Spectrum, IGR, NPK, any English agronomy/chemistry term present), and the
   product name "{product}" itself. Keep these exactly as written, in Roman English,
   in every variant.
4. Keep every number and dosage unchanged. Always render "acre" (in whichever
   source-language word/script it appears, e.g. "एकड़"/"एकर") as "acre" (Roman
   English) in every language variant.
5. Preserve the warning/checkmark symbols exactly as they appear in the source
   (⚠/✓ or ⚠️/✅ — whichever variant is used), the objection/answer pairing
   structure, and every line break and blank line exactly as in the source text.
6. Do not add, remove, merge, or reorder any objection/answer pairs.
7. displayName: a SHORT transliteration of just the product name "{product}" into
   that language's script (phonetic, like a brand name would be written on
   packaging) — not a translation, not a sentence. Keep pure codes/model numbers/
   abbreviations (e.g. "NX", "70", numbers) in Roman as-is.

OUTPUT: valid JSON (no markdown fences), exactly this shape:
{shape}
Use \\n for line breaks inside strings. After writing the file, reply with just the word DONE.
"""


def roleplay_prompt(product, source_lang, ksp_src, oh_src, output_path):
    return f"""Here is {source_lang} agri-input sales pitch content for the product "{product}":

KEY SELLING POINTS ({source_lang}):
{ksp_src}

OBJECTION HANDLING ({source_lang}, pairs of a warning-symbol objection line and a check-mark answer line):
{oh_src}

TASK: Write a Field-vs-Retailer roleplay training script in Hinglish (the {source_lang}
meaning written in Roman/English script — transliteration, NOT English translation)
and save it to {output_path} using the Write tool.

FORMAT (follow exactly):
- Two speakers. Speaker 1 = Rahul (AgroStar SM). Speaker 2 = Brij ji (retailer).
- Start with a short header block: product name, "Speaker 1 = Rahul (AgroStar Rep)",
  "Speaker 2 = Brij ji (Retailer)", and a one-line context: Rahul calls Brij ji,
  stock has arrived, wants him to actively pitch it.
- Then the dialogue as alternating "Speaker 1:" / "Speaker 2:" lines.
- Rahul opens warmly, mentions the product and that stock has arrived.
- Brij ji raises the real objections from the {source_lang} objection-handling content
  above, ONE AT A TIME, in his own retailer/farmer-facing words (paraphrase naturally
  into spoken Hinglish, don't copy-paste the source script verbatim). Go through every
  objection/answer pair, in order, don't skip any.
- Rahul rebuts each objection using that pair's actual answer content, and uses the
  product's USPs from the key selling points naturally through the conversation.
- End with Rahul giving a single "bas yeh ek line bolo" summary pitch (one punchy
  line capturing the core USP), then a soft close (stock hai, le jao — no pressure).
  Brij ji agrees warmly.

STYLE: Roman script Hinglish, the way a real AgroStar Sales Manager talks on a phone
call — conversational, warm, not stiff. Use em-dashes ( — ) and ".." for natural
spoken pauses. Do NOT write [pause] or any bracket/stage-direction tags — this gets
read aloud by a TTS system. Use "ji"/"aap" — Brij ji is addressed with respect.

HARD RULES:
- Every claim must trace to the KSP/objection content above. Invent NOTHING — no
  made-up percentages, crops, numbers, or benefits not present in the source.
- NO negative framing — never "maangega tabhi dunga", "bikta hai kya", "risk itna
  chota hai", or anything that puts the retailer/farmer down.
- Keep technical/chemical/formulation terms and the product name in Roman English
  exactly as in the source. Keep all dosages/numbers unchanged. Render the source
  language's word for "acre" in Roman as "acre".
- Do NOT mention billing price, farmer-count stats, call recordings, Convin, or any
  internal data source.
- If an objection is technical, have Rahul explain it simply and conversationally,
  not as a textbook definition.

After writing the file, reply with just the word DONE.
"""


def backfill_objections_prompt(product, source_lang, ksp_src, output_path):
    return f"""Here is {source_lang} content describing a feature/topic called "{product}":

KEY SELLING POINTS ({source_lang}):
{ksp_src}

There is NO existing objection-handling content for this topic. Both "key selling
points" and "objections" are mandatory fields for every topic in our app, so you
need to generate the missing objections content.

TASK: Generate 3-5 natural clarifying-question / helpful-answer pairs in
{source_lang}, in the same ⚠ (question) / ✓ (answer) format used elsewhere, that a
retailer would realistically ask while learning about this feature for the first
time — e.g. how it works, where to find it, what happens in a specific situation.

BE CAREFUL: these must be genuine, informative questions with substantive answers —
NOT filler words ("theek hai", "achha") dressed up as a question, and NOT invented
skeptical/pushback objections (price complaints, "why should I trust this") since
there is no such content to draw from for this topic. Each answer must be fully
grounded in the KEY SELLING POINTS above — invent nothing beyond what's stated there.

Save as JSON to {output_path} using the Write tool, exactly this shape:
{{"oh": "⚠ <question 1>\\n✓ <answer 1>\\n\\n⚠ <question 2>\\n✓ <answer 2>\\n..."}}
Use \\n for line breaks, blank line between pairs, matching the format of existing
objection-handling content in this app. After writing, reply with just the word DONE.
"""


def backfill_keypoints_prompt(product, source_lang, oh_src, output_path):
    return f"""Here is {source_lang} content describing how to handle a scenario for
"{product}":

OBJECTION/QUERY HANDLING ({source_lang}):
{oh_src}

There is NO existing "key selling points" content for this topic. Both "key selling
points" and "objections" are mandatory fields for every topic in our app, so you
need to generate the missing key-points content.

TASK: Generate 3-5 bullet points in {source_lang} summarizing HOW TO HANDLE this
scenario — the core guidance/process a rep should follow — grounded entirely in the
content above. Invent nothing beyond what's stated there; just extract and
summarize the actual guidance into clear standalone bullet points.

Save as JSON to {output_path} using the Write tool, exactly this shape:
{{"ksp": "-<bullet 1>\\n-<bullet 2>\\n-<bullet 3>"}}
Use \\n for line breaks, each bullet starting with "-", matching the format of
existing key-selling-points content in this app. After writing, reply with just the
word DONE.
"""


def roleplay_prompt_ksp_only(product, source_lang, ksp_src, output_path):
    return f"""Here is {source_lang} content describing a feature/process for "{product}":

KEY SELLING POINTS ({source_lang}):
{ksp_src}

TASK: Write a Field-vs-Retailer training script in Hinglish (the {source_lang} meaning
written in Roman/English script — transliteration, NOT English translation) and save
it to {output_path} using the Write tool. There is NO objection-handling content for
this topic — do not invent objections.

FORMAT (follow exactly):
- Two speakers. Speaker 1 = Rahul (AgroStar SM). Speaker 2 = Brij ji (retailer).
- Start with a short header block: topic name, "Speaker 1 = Rahul (AgroStar Rep)",
  "Speaker 2 = Brij ji (Retailer)", and a one-line context: Rahul calls Brij ji to
  walk him through this.
- Then the dialogue as alternating "Speaker 1:" / "Speaker 2:" lines.
- Rahul opens warmly and explains each key point from the list above, one at a time,
  in his own natural spoken words (paraphrase, don't copy-paste the source verbatim).
- Brij ji does NOT raise objections (there are none to work from). Instead he responds
  with natural conversational fillers and light acknowledgment/clarifying questions —
  things like "Achha", "Theek hai", "Samajh gaya", "Ye kaise karna hai?", "Aur kuch?" —
  short, natural, spoken reactions that keep the conversation flowing, not objections
  or pushback.
- Rahul answers any clarifying question Brij ji asks, using only the content above.
- End with Rahul giving a single "bas yeh ek line bolo" summary line capturing the
  core point, then a warm close. Brij ji acknowledges positively.

STYLE: Roman script Hinglish, the way a real AgroStar Sales Manager talks on a phone
call — conversational, warm, not stiff. Use em-dashes ( — ) and ".." for natural
spoken pauses. Do NOT write [pause] or any bracket/stage-direction tags — this gets
read aloud by a TTS system. Use "ji"/"aap" — Brij ji is addressed with respect.

HARD RULES:
- Every claim must trace to the content above. Invent NOTHING — no made-up
  percentages, numbers, or details not present in the source.
- NO negative framing — never anything that puts the retailer/farmer down.
- Keep technical terms, app/feature names, and the topic name in Roman English
  exactly as in the source. Keep all numbers unchanged.
- Do NOT mention billing price, farmer-count stats, call recordings, Convin, or any
  internal data source.

After writing the file, reply with just the word DONE.
"""


def thumbnail_prompt(product, description, asset_path):
    return f"""Find a real product image for the AgroStar (Indian agri-input company)
product "{product}" — {description}.

Steps:
1. Use WebSearch to find a product image — try queries like "{product} AgroStar",
   "{product} agrostar bottle/pouch". Prefer images from agrostar.in, AgroStar's
   app/catalog pages, or Indian agri-input marketplaces (BigHaat, Kisandeals,
   IndiaMART, Amazon.in) that clearly show the actual product packaging/label with
   the name "{product}" visible.
2. Once you have a direct image URL, download it with:
   curl -sL -A "Mozilla/5.0" -o "{asset_path}" "<url>"
3. Verify: run `file "{asset_path}"` — it must report a real image type (JPEG, PNG,
   WebP, etc.), not "HTML" or "ASCII text", and the file must be at least 5KB
   (check with `ls -la`).
4. If it's not already a JPEG, convert it in place:
   sips -s format jpeg "{asset_path}" --out "{asset_path}"
5. If validation fails, try up to 2 more candidate image URLs before giving up.
6. If no working product-specific image is found after 3 attempts, download a
   generic-but-relevant placeholder instead so the path is never left empty, and
   say clearly in your final report that it's a placeholder.

Report: the final source URL used, the file size, and whether it's the actual
product or a placeholder.
"""
