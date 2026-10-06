"""
Splices output/lmd_calls_data.json + lmd_order_packages.json +
lmd_package_status_history.json into dashboard/template.html to produce
dashboard/lmd_call_dashboard.html -- the file to open in a browser (or double-click).

Run scripts/extract_lmd_data.py first to refresh those JSON files from BigQuery.

This dashboard embeds direct <audio src> links to the AgroStar/Airtel S3 call
recordings -- do NOT publish it as a Claude Artifact, the artifact sandbox's CSP
blocks loading media from arbitrary external hosts, so playback would silently
fail. Open the rendered HTML file directly in a browser instead.

Usage:
  /usr/bin/python3 scripts/render_dashboard.py
"""
import base64
import json
import os
import re
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(BASE, "dashboard", "template.html")
OUT_PATH = os.path.join(BASE, "dashboard", "lmd_call_dashboard.html")

DATA_FILES = {
    "window.__LMD_CALLS__ = /*__LMD_CALLS_PLACEHOLDER__*/;": "lmd_calls_data.json",
    "window.__LMD_PACKAGES__ = /*__LMD_PACKAGES_PLACEHOLDER__*/;": "lmd_order_packages.json",
    "window.__LMD_HISTORY__ = /*__LMD_HISTORY_PLACEHOLDER__*/;": "lmd_package_status_history.json",
    "window.__LMD_GEO__ = /*__LMD_GEO_PLACEHOLDER__*/;": "lmd_order_geo.json",
    "window.__LMD_ITEMS__ = /*__LMD_ITEMS_PLACEHOLDER__*/;": "lmd_order_items.json",
    "window.__LMD_ORDER_SRC__ = /*__LMD_ORDER_SRC_PLACEHOLDER__*/;": "lmd_order_sources.json",
}
DATE_PLACEHOLDER = "__SNAP_DATE_PLACEHOLDER__"
EXTRACTED_PLACEHOLDER = "__EXTRACTED_AT_PLACEHOLDER__"


# Farmer phone numbers must not be readable in the shipped page (or via Inspect Element).
PHONE_RE = re.compile(r"(?<![\d])(?:\+?91[\s-]?|0)?[6-9]\d{9}(?![\d])")
CALL_NUMBER_FIELDS = ("caller_number", "destination_number", "alternate_contact_number")


def mask_text(v):
    # Free text only (has a space): ids, uuids and URLs have none, so they are never touched.
    return PHONE_RE.sub("[number hidden]", v) if isinstance(v, str) and " " in v else v


def main():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        rendered = f.read()

    counts = {}
    for placeholder, filename in DATA_FILES.items():
        if placeholder not in rendered:
            raise SystemExit(f"Placeholder not found in {TEMPLATE_PATH}: {placeholder}")
        path = os.path.join(BASE, "output", filename)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        counts[filename] = len(data)
        if filename == "lmd_calls_data.json":
            for r in data:
                for k in CALL_NUMBER_FIELDS:
                    r.pop(k, None)
                for k, v in r.items():
                    r[k] = mask_text(v)
        blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        var_name = placeholder.split(" = ")[0]
        rendered = rendered.replace(placeholder, f"{var_name} = {blob};", 1)

    # Sales calls: call details are embedded, but the transcripts (~50 MB of text) go to a sibling .js file
    # the page loads with <script src>, so the HTML itself doesn't double in size. Keep both files together.
    sales_ph = "window.__LMD_SALES__ = /*__LMD_SALES_PLACEHOLDER__*/;"
    if sales_ph not in rendered:
        raise SystemExit(f"Placeholder not found in {TEMPLATE_PATH}: {sales_ph}")
    with open(os.path.join(BASE, "output", "lmd_sales_calls.json"), encoding="utf-8") as f:
        sales = json.load(f)
    # Extra Genesys fields per call (extract_sales_disposition.py) -> compact `g` dict, empty / "not discussed" dropped.
    disp = {}
    dpath = os.path.join(BASE, "output", "lmd_sales_disposition.json")
    if os.path.exists(dpath):
        with open(dpath, encoding="utf-8") as f:
            disp = {d["call_id"]: d for d in json.load(f)}

    def clean(v):
        if v is None:
            return None
        parts = []
        for x in str(v).split(","):   # Genesys repeats values: "Tarplus,Tarplus"
            x = x.strip()
            if x and x.lower() not in ("not discussed", "not mentioned", "na", "none") and x.lower() not in [p.lower() for p in parts]:
                parts.append(x)
        return ", ".join(parts) or None

    for r in sales:
        d = disp.get(r["call_id"])
        if d:
            g = {"sale": d["disposition_levels_level_3"], "price": d["custom_entities_Pricing_Concern"],
                 "land": clean(d["custom_entities_Land_holding"]), "crop": clean(d["custom_entities_Crop_Discussed"]),
                 "issue": clean(d["custom_entities_Crop_issue"]), "prod": clean(d["custom_entities_Product_discussed"]),
                 "offer": clean(d["custom_entities_Offer_Pitched"]), "cb": clean(d["custom_entities_Call_Back_Date_and_Time"])}
            r["g"] = {k: v for k, v in g.items() if v}
    for r in sales:
        path = r.pop("recording_path", None)
        if path:   # file name carries the farmer's number -> obfuscated token, decoded only on Play
            r["rp"] = base64.b64encode(path[::-1].encode()).decode()
        # Genesys call type is kept (short keys d1/d2) for the sales-call-type pivot/filter; the rest are dropped.
        r["d1"], r["d2"] = r.pop("disposition_1", None), r.pop("disposition_2", None)
        for k in ("direction", "lead_interest"):
            r.pop(k, None)
        r["ai_summary"] = mask_text(r.get("ai_summary"))
        r["transcript"] = [[who, mask_text(t)] for who, t in (r.get("transcript") or [])] or None
    transcripts = {r["call_id"]: r.pop("transcript") for r in sales if r.get("transcript")}
    rendered = rendered.replace(sales_ph, "window.__LMD_SALES__ = " + json.dumps(sales, separators=(",", ":"), ensure_ascii=False) + ";", 1)
    tr_path = os.path.join(BASE, "dashboard", "lmd_sales_transcripts.js")
    with open(tr_path, "w", encoding="utf-8") as f:
        f.write("window.__LMD_SALES_TR__ = " + json.dumps(transcripts, separators=(",", ":"), ensure_ascii=False) + ";")
    counts["lmd_sales_calls.json"] = len(sales)

    # True-UTC extraction instant -- the dashboard's "as of" time for pending / not-executed.
    with open(os.path.join(BASE, "output", "extract_meta.json"), encoding="utf-8") as f:
        extracted_at = json.load(f)["extracted_at_utc"]
    if EXTRACTED_PLACEHOLDER not in rendered:
        raise SystemExit(f"Extraction-time placeholder not found in {TEMPLATE_PATH}")
    rendered = rendered.replace(EXTRACTED_PLACEHOLDER, extracted_at)

    if DATE_PLACEHOLDER not in rendered:
        raise SystemExit(f"Date placeholder not found in {TEMPLATE_PATH}")
    rendered = rendered.replace(DATE_PLACEHOLDER, date.today().isoformat())

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(rendered)
    counts_str = ", ".join(f"{n:,} {name}" for name, n in counts.items())
    print(f"Wrote {OUT_PATH} ({len(rendered):,} bytes) — {counts_str}")


if __name__ == "__main__":
    main()
