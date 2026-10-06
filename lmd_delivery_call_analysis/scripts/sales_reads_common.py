"""
Shared pieces for the sales-call reading workflow (see ../02-sales_call_reading_plan.md).

Claude reads ONE sales-call transcript at a time and writes a structured read. These helpers pick
the next unread call, validate a read against its transcript, and keep the results on disk.

What the reader may see: the transcript + the order's items. Nothing from disposition_data (no ai_summary,
dispositions, lead_interest) and never the LMD outcome -- see next_call().

Local only: output/ is gitignored (farmer PII + quotes).
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "output")
READS_PATH = os.path.join(OUT, "sales_reads.jsonl")
REJECTS_PATH = os.path.join(OUT, "sales_reads_rejections.jsonl")

SCHEMA_VERSION = "v0-calibration"   # becomes "v1" once the 30-call calibration is reviewed and frozen
COHORT_GROUP = "Mustard"

# ---- schema enums -------------------------------------------------------------------------------
COMMITMENT = {"CLEAR_YES", "AGREED_AFTER_PUSH", "HESITANT", "UNCLEAR", "NO_ORDER_DISCUSSED"}
WHO_DECIDED = {"FARMER", "FAMILY_MEMBER", "UNCLEAR"}
OFFER = {"cashback", "lucky_draw", "gift", "discount", "welcome_kit", "other"}
PAY_MODE = {"COD", "ADVANCE", "UPI", "OTHER"}
PAY_READY = {"READY", "MONEY_LATER", "NOT_MENTIONED"}
CROP_FIT = {"FITS_ORDER", "MISMATCH", "UNCLEAR"}
CONDUCT = {"pressure", "cod_not_explained", "kit_not_explained", "exaggerated_claim", "other"}
QUALITY = {"GOOD", "PARTLY_GARBLED", "POOR", "TOO_SHORT"}
CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def transcript_text(turns):
    """Canonical text of a transcript: every utterance in order, speaker-labelled. Used for hashing."""
    return "\n".join(f"{'AGENT' if t[0] == 'owner' else 'FARMER'}: {t[1] or ''}" for t in turns)


def sha(turns):
    return hashlib.sha256(transcript_text(turns).encode("utf-8")).hexdigest()[:16]


def norm(s):
    """Lowercase, drop punctuation, collapse whitespace -- for verbatim-quote matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s₹]", " ", (s or "").lower(), flags=re.UNICODE)).strip()


def cohort_calls():
    """Mustard orders' sales calls with a transcript, newest sales call first (ties by call_id)."""
    items = {}
    for i in load("lmd_order_items.json"):
        items.setdefault(i["order_id"], []).append(i)
    mustard = {o for o, its in items.items() if any(i["product_group"] == COHORT_GROUP for i in its)}
    calls = [c for c in load("lmd_sales_calls.json") if c["order_id"] in mustard and c["transcript"]]
    calls.sort(key=lambda c: (c["call_start"], c["call_id"]), reverse=True)
    return calls, items


def read_done():
    """{(call_id, sha, schema_version)} already read and saved."""
    done = set()
    if os.path.exists(READS_PATH):
        with open(READS_PATH, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                done.add((r["call_id"], r["transcript_sha256"], r["schema_version"]))
    return done


BATCH_DIR = os.path.join(OUT, "sales_batches")


def batch_ids(batch):
    with open(os.path.join(BATCH_DIR, f"batch_{batch}.json"), encoding="utf-8") as f:
        return json.load(f)


def next_call(batch=None):
    """The next unread call in cohort order (or in the given batch, in the batch's order), or None.
    Returns only what the reader may see."""
    calls, items = cohort_calls()
    if batch is not None:
        order = {cid: i for i, cid in enumerate(batch_ids(batch))}
        calls = sorted((c for c in calls if c["call_id"] in order), key=lambda c: order[c["call_id"]])
    done = read_done()
    remaining = [c for c in calls if (c["call_id"], sha(c["transcript"]), SCHEMA_VERSION) not in done]
    if not remaining:
        return None, 0, len(calls)
    c = remaining[0]
    return {
        "call_id": c["call_id"], "order_id": c["order_id"],
        "call_date": c["call_start"][:10],   # for progress only; the reader is not asked to use it
        "match": c["match"], "transcript_sha256": sha(c["transcript"]),
        "transcript": c["transcript"],
        "order_items": [{"item": i["item_name"], "qty": i["quantity"], "total_price": i["total_price"]}
                        for i in sorted(items[c["order_id"]], key=lambda x: -(x["total_price"] or 0))],
    }, len(remaining), len(calls)


# ---- validation ---------------------------------------------------------------------------------
def _quote_ok(quote, haystack):
    """Every fragment of the quote (split on ... / …) must appear verbatim in the transcript."""
    if not quote or not quote.strip():
        return False
    frags = [f for f in re.split(r"\.\.\.|…", quote) if norm(f)]
    return bool(frags) and all(len(norm(f)) >= 3 and norm(f) in haystack for f in frags)


def _nums(s):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*", s or "") if len(n.replace(",", "")) >= 2}


def validate(read, turns):
    """Mechanical checks only (structure, enums, verbatim quotes, numbers present). Returns [errors]."""
    errs = []
    hay = norm(" ".join(t[1] or "" for t in turns))
    digits_in_transcript = _nums(" ".join(t[1] or "" for t in turns))

    def need(d, key, where):
        if not isinstance(d, dict) or key not in d:
            errs.append(f"{where}: missing '{key}'"); return False
        return True

    def quote_for(d, where, required):
        q = d.get("quote")
        if q is None or q == "":
            if required: errs.append(f"{where}: non-null value needs a verbatim quote")
        elif not _quote_ok(q, hay):
            errs.append(f"{where}: quote not found verbatim in transcript: {q[:70]!r}")

    def nums_ok(text, where):
        # Speech-to-text often writes numbers as words ("tin sau tis rupaye"), so digits in a value can be a
        # faithful conversion. The number check therefore only applies when the transcript itself uses digits;
        # otherwise the verbatim quote on the field is the evidence (known limitation, see the plan).
        if not digits_in_transcript:
            return
        bad = _nums(text) - digits_in_transcript
        if bad: errs.append(f"{where}: number(s) {sorted(bad)} not present in transcript")

    if not isinstance(read, dict):
        return ["read is not a JSON object"]
    if not (isinstance(read.get("story"), str) and len(read["story"].split()) >= 8):
        errs.append("story: missing or too short")
    else:
        nums_ok(read["story"], "story")

    c = read.get("commitment")
    if need(c, "value", "commitment"):
        if c["value"] not in COMMITMENT: errs.append(f"commitment: bad value {c['value']!r}")
        quote_for(c, "commitment", c["value"] not in ("UNCLEAR", "NO_ORDER_DISCUSSED"))
    w = read.get("who_decided")
    if need(w, "value", "who_decided"):
        if w["value"] is not None and w["value"] not in WHO_DECIDED: errs.append(f"who_decided: bad value {w['value']!r}")
        quote_for(w, "who_decided", w["value"] not in (None, "UNCLEAR"))

    for key in ("product_discussed", "price_stated", "delivery_promise", "competition"):
        d = read.get(key)
        if need(d, "value", key):
            quote_for(d, key, d["value"] is not None)
            if d["value"] and key in ("price_stated", "delivery_promise"): nums_ok(str(d["value"]), key)

    for key, enum in (("offers_pitched", OFFER), ("agent_conduct_flags", CONDUCT)):
        lst = read.get(key)
        if not isinstance(lst, list):
            errs.append(f"{key}: must be a list (empty if none)"); continue
        for n, it in enumerate(lst):
            if not isinstance(it, dict) or it.get("value") not in enum:
                errs.append(f"{key}[{n}]: value must be one of {sorted(enum)}"); continue
            quote_for(it, f"{key}[{n}]", True)

    obj = read.get("objections")
    if not isinstance(obj, list): errs.append("objections: must be a list (empty if none)")
    else:
        for n, it in enumerate(obj):
            if not isinstance(it, dict) or not it.get("value"): errs.append(f"objections[{n}]: needs 'value'"); continue
            quote_for(it, f"objections[{n}]", True)

    p = read.get("payment")
    if not isinstance(p, dict): errs.append("payment: missing")
    else:
        if p.get("mode") is not None and p["mode"] not in PAY_MODE: errs.append(f"payment.mode bad: {p['mode']!r}")
        if p.get("readiness") not in PAY_READY: errs.append(f"payment.readiness must be one of {sorted(PAY_READY)}")
        has = p.get("mode") is not None or p.get("readiness") in ("READY", "MONEY_LATER") or p.get("detail")
        quote_for(p, "payment", bool(has))
        if p.get("detail"): nums_ok(str(p["detail"]), "payment.detail")

    cn = read.get("crop_and_need")
    if not isinstance(cn, dict): errs.append("crop_and_need: missing")
    else:
        if cn.get("fit") not in CROP_FIT: errs.append(f"crop_and_need.fit must be one of {sorted(CROP_FIT)}")
        quote_for(cn, "crop_and_need", bool(cn.get("crop") or cn.get("stage")))

    on = read.get("other_notable")
    if on is not None and not isinstance(on, (str, dict)): errs.append("other_notable: string, {value,quote} or null")
    if isinstance(on, dict) and on.get("quote"): quote_for(on, "other_notable", False)

    rel = read.get("reliability")
    if not isinstance(rel, dict) or rel.get("transcript_quality") not in QUALITY or rel.get("confidence") not in CONFIDENCE \
            or not rel.get("language"):
        errs.append("reliability: needs language, transcript_quality, confidence")
    return errs


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
