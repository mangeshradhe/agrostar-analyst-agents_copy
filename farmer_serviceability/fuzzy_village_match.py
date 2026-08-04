"""
Address Problem farmers -> Village Master fuzzy/phonetic resolver.

Inputs (local, no BigQuery calls in this script):
  - TAM address problem all states - results-20260804-123823.csv   (67,801 address rows)
  - village_master.csv                                             (652,140 rows, GJ/MH/RJ/MP/UP, archived+non-archived)

Waterfall gates (stop at first confident match per row):
  Gate 1: GPS nearest village (<=5km), actual distance always recorded
  Gate 2: village + taluka + district (fuzzy + phonetic)
  Gate 3: village + taluka (fuzzy + phonetic)
  Gate 4: village + district (fuzzy + phonetic) + pincode EXACT
  Gate 5: village (fuzzy + phonetic) + pincode EXACT

Normalization (village only):
  strip: * . , [ ] | @ / _ ? { } ` + ; &      (noise punctuation)
  ( ) - -> space                              (conjunction chars)
  remove tokens: ka, ki, ke, b, o, pr
  synonyms:  bk/budruk/buzurg -> budruk ; kh/khurd/k -> khurd ; rural/gramin -> rural ; city/urban -> urban
Taluka/district: lowercase/trim/punctuation-clean only, no stopword removal.
Pincode: 6-digit exact match only, never fuzzy.
"""

import csv
import math
import re
import sys
import time

import numpy as np
from rapidfuzz import fuzz, process
import jellyfish

TAM_PATH = "/Users/darpan/Downloads/TAM address problem all states - results-20260804-123823.csv"
MASTER_PATH = "/Users/darpan/Downloads/village_master.csv"
OUTPUT_PATH = "/Users/darpan/Downloads/address_problem_resolved.csv"

GATE1_KM_THRESHOLD = 5.0
FUZZY_ACCEPT = 90
FUZZY_PHONETIC_FLOOR = 75
VILLAGE_ONLY_FLOOR = 75  # guardrail: composite score can't ride on a matching taluka/district/pincode alone

FILLER_TOKENS = {"ka", "ki", "ke", "b", "o", "pr"}
SYNONYM_MAP = {
    "bk": "budruk", "budruk": "budruk", "buzurg": "budruk",
    "kh": "khurd", "khurd": "khurd", "k": "khurd",
    "rural": "rural", "gramin": "rural",
    "city": "urban", "urban": "urban",
}

PUNCT_RE = re.compile(r"[^a-z0-9\s]")
SPACE_RE = re.compile(r"\s+")


def normalize_state(s):
    s = (s or "").strip().lower()
    if s.startswith("gujarat"):
        return "gujarat"
    if s.startswith("maharash"):
        return "maharashtra"
    if s.startswith("rajas"):
        return "rajasthan"
    if s.startswith("madhya"):
        return "madhya pradesh"
    if s.startswith("uttar"):
        return "uttar pradesh"
    return s


def normalize_pincode(p):
    digits = re.sub(r"\D", "", p or "")
    return digits if len(digits) == 6 else None


def clean_field(s, strip_fillers=False):
    s = (s or "").lower()
    s = PUNCT_RE.sub(" ", s)
    s = SPACE_RE.sub(" ", s).strip()
    if not strip_fillers:
        return s
    out = []
    for tok in s.split():
        if tok in FILLER_TOKENS:
            continue
        out.append(SYNONYM_MAP.get(tok, tok))
    return " ".join(out)


def phonetic_of(s):
    if not s:
        return ""
    return " ".join(jellyfish.metaphone(tok) for tok in s.split())


def parse_latlon(lat, lon):
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat_f == 0 or lon_f == 0:
        return None
    return lat_f, lon_f


def haversine_km_vec(lat1, lon1, lat2_arr, lon2_arr):
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = np.radians(lat2_arr)
    dphi = np.radians(lat2_arr - lat1)
    dlambda = np.radians(lon2_arr - lon1)
    a = np.sin(dphi / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def load_master():
    rows = []
    with open(MASTER_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            state_norm = normalize_state(r["state"])
            village_clean = clean_field(r["village"], strip_fillers=True)
            taluka_clean = clean_field(r["taluka"], strip_fillers=False)
            district_clean = clean_field(r["district"], strip_fillers=False)
            pincode = normalize_pincode(r["pin_code"])
            latlon = parse_latlon(r.get("latitude"), r.get("longitude"))
            rows.append({
                "id": r["id"],
                "state": r["state"],
                "district": r["district"],
                "taluka": r["taluka"],
                "village": r["village"],
                "pincode_disp": r["pin_code"],
                "is_archived": r["is_archived"],
                "replaced_by_id": r["replaced_by_id"],
                "zone_id": r["zone_id"],
                "zone_name": r["zone_name"],
                "state_norm": state_norm,
                "village_clean": village_clean,
                "taluka_clean": taluka_clean,
                "district_clean": district_clean,
                "pincode": pincode,
                "latlon": latlon,
                "vtd_key": f"{village_clean} {taluka_clean} {district_clean}".strip(),
                "vt_key": f"{village_clean} {taluka_clean}".strip(),
                "vd_key": f"{village_clean} {district_clean}".strip(),
                "v_key": village_clean,
            })
    return rows


def build_indices(master):
    by_state_gps = {}          # state_norm -> (lat_arr, lon_arr, idx_arr)
    by_state_taluka_district = {}
    by_state_taluka = {}
    by_state = {}
    by_pincode = {}

    gps_buckets = {}
    for i, r in enumerate(master):
        st = r["state_norm"]
        by_state.setdefault(st, []).append(i)
        by_state_taluka.setdefault((st, r["taluka_clean"]), []).append(i)
        by_state_taluka_district.setdefault((st, r["taluka_clean"], r["district_clean"]), []).append(i)
        if r["pincode"]:
            by_pincode.setdefault(r["pincode"], []).append(i)
        if r["latlon"]:
            gps_buckets.setdefault(st, {"lat": [], "lon": [], "idx": []})
            gps_buckets[st]["lat"].append(r["latlon"][0])
            gps_buckets[st]["lon"].append(r["latlon"][1])
            gps_buckets[st]["idx"].append(i)

    for st, d in gps_buckets.items():
        by_state_gps[st] = (np.array(d["lat"]), np.array(d["lon"]), np.array(d["idx"]))

    return {
        "by_state_gps": by_state_gps,
        "by_state_taluka_district": by_state_taluka_district,
        "by_state_taluka": by_state_taluka,
        "by_state": by_state,
        "by_pincode": by_pincode,
    }


def gate1_nearest(latlon, state_norm, idx, master):
    bucket = idx["by_state_gps"].get(state_norm)
    if bucket is None:
        return None
    lat_arr, lon_arr, idx_arr = bucket
    dists = haversine_km_vec(latlon[0], latlon[1], lat_arr, lon_arr)
    best = np.argmin(dists)
    return idx_arr[best], float(dists[best])


def fuzzy_phonetic_best(query_key, candidate_idxs, key_field, master, query_village_clean):
    if not candidate_idxs:
        return None
    choices = [master[i][key_field] for i in candidate_idxs]
    result = process.extractOne(query_key, choices, scorer=fuzz.token_sort_ratio)
    if result is None:
        return None
    matched_str, score, pos = result
    matched_idx = candidate_idxs[pos]
    phon_match = phonetic_of(query_key) == phonetic_of(matched_str)
    composite_pass = score >= FUZZY_ACCEPT or (phon_match and score >= FUZZY_PHONETIC_FLOOR)

    # Guardrail: blocking already anchors taluka/district/pincode, so a long matching
    # non-village portion of the composite key can inflate the score even when the
    # village name itself is a poor match (e.g. 'Sithol' vs 'Siloj' at 54.5% village-only
    # similarity still cleared 90 composite because taluka+district matched exactly).
    # Require the village name alone to also clear a floor.
    village_score = fuzz.token_sort_ratio(query_village_clean, master[matched_idx]["village_clean"])
    accepted = composite_pass and village_score >= VILLAGE_ONLY_FLOOR

    return {
        "idx": matched_idx,
        "score": score,
        "village_score": village_score,
        "phonetic_match": phon_match,
        "accepted": accepted,
    }


def get_candidates_gate23(state_norm, taluka_clean, district_clean, idx, want_district):
    if want_district:
        cands = idx["by_state_taluka_district"].get((state_norm, taluka_clean, district_clean))
        if cands:
            return cands
    cands = idx["by_state_taluka"].get((state_norm, taluka_clean))
    if cands:
        return cands
    return idx["by_state"].get(state_norm, [])


def resolve_row(row, idx, master):
    state_norm = normalize_state(row["state"])
    village_clean = clean_field(row["village"], strip_fillers=True)
    taluka_clean = clean_field(row["taluka"], strip_fillers=False)
    district_clean = clean_field(row["district"], strip_fillers=False)
    pincode = normalize_pincode(row["pincode"])
    latlon = parse_latlon(row.get("latitude"), row.get("longitude"))

    result = {
        "farmer_id": row["farmer_id"],
        "orig_village": row["village"],
        "orig_taluka": row["taluka"],
        "orig_district": row["district"],
        "orig_state": row["state"],
        "orig_pincode": row["pincode"],
        "orig_latitude": row.get("latitude", ""),
        "orig_longitude": row.get("longitude", ""),
        "matched_gate": "unresolved",
        "matched_village_id": "",
        "matched_village": "",
        "matched_taluka": "",
        "matched_district": "",
        "matched_state": "",
        "matched_pincode": "",
        "matched_is_archived": "",
        "matched_replaced_by_id": "",
        "matched_zone_id": "",
        "matched_zone_name": "",
        "fuzzy_score": "",
        "phonetic_match": "",
        "gps_distance_km": "",
    }

    # Gate 1
    if latlon:
        nearest = gate1_nearest(latlon, state_norm, idx, master)
        if nearest:
            near_idx, dist_km = nearest
            result["gps_distance_km"] = round(dist_km, 3)
            if dist_km <= GATE1_KM_THRESHOLD:
                m = master[near_idx]
                result.update({
                    "matched_gate": "1",
                    "matched_village_id": m["id"],
                    "matched_village": m["village"],
                    "matched_taluka": m["taluka"],
                    "matched_district": m["district"],
                    "matched_state": m["state"],
                    "matched_pincode": m["pincode_disp"],
                    "matched_is_archived": m["is_archived"],
                    "matched_replaced_by_id": m["replaced_by_id"],
                    "matched_zone_id": m["zone_id"],
                    "matched_zone_name": m["zone_name"],
                })
                return result

    # Gate 2: village+taluka+district
    q2 = f"{village_clean} {taluka_clean} {district_clean}".strip()
    cands = get_candidates_gate23(state_norm, taluka_clean, district_clean, idx, want_district=True)
    r2 = fuzzy_phonetic_best(q2, cands, "vtd_key", master, village_clean)
    if r2 and r2["accepted"]:
        m = master[r2["idx"]]
        result.update({
            "matched_gate": "2",
            "matched_village_id": m["id"], "matched_village": m["village"],
            "matched_taluka": m["taluka"], "matched_district": m["district"],
            "matched_state": m["state"], "matched_pincode": m["pincode_disp"],
            "matched_is_archived": m["is_archived"], "matched_replaced_by_id": m["replaced_by_id"],
            "matched_zone_id": m["zone_id"], "matched_zone_name": m["zone_name"],
            "fuzzy_score": r2["score"], "phonetic_match": r2["phonetic_match"],
        })
        return result

    # Gate 3: village+taluka
    q3 = f"{village_clean} {taluka_clean}".strip()
    cands = get_candidates_gate23(state_norm, taluka_clean, district_clean, idx, want_district=False)
    r3 = fuzzy_phonetic_best(q3, cands, "vt_key", master, village_clean)
    if r3 and r3["accepted"]:
        m = master[r3["idx"]]
        result.update({
            "matched_gate": "3",
            "matched_village_id": m["id"], "matched_village": m["village"],
            "matched_taluka": m["taluka"], "matched_district": m["district"],
            "matched_state": m["state"], "matched_pincode": m["pincode_disp"],
            "matched_is_archived": m["is_archived"], "matched_replaced_by_id": m["replaced_by_id"],
            "matched_zone_id": m["zone_id"], "matched_zone_name": m["zone_name"],
            "fuzzy_score": r3["score"], "phonetic_match": r3["phonetic_match"],
        })
        return result

    # Gate 4: village+district + exact pincode
    if pincode:
        cands = idx["by_pincode"].get(pincode, [])
        q4 = f"{village_clean} {district_clean}".strip()
        r4 = fuzzy_phonetic_best(q4, cands, "vd_key", master, village_clean)
        if r4 and r4["accepted"]:
            m = master[r4["idx"]]
            result.update({
                "matched_gate": "4",
                "matched_village_id": m["id"], "matched_village": m["village"],
                "matched_taluka": m["taluka"], "matched_district": m["district"],
                "matched_state": m["state"], "matched_pincode": m["pincode_disp"],
                "matched_is_archived": m["is_archived"], "matched_replaced_by_id": m["replaced_by_id"],
                "matched_zone_id": m["zone_id"], "matched_zone_name": m["zone_name"],
                "fuzzy_score": r4["score"], "phonetic_match": r4["phonetic_match"],
            })
            return result

        # Gate 5: village + exact pincode
        q5 = village_clean
        r5 = fuzzy_phonetic_best(q5, cands, "v_key", master, village_clean)
        if r5 and r5["accepted"]:
            m = master[r5["idx"]]
            result.update({
                "matched_gate": "5",
                "matched_village_id": m["id"], "matched_village": m["village"],
                "matched_taluka": m["taluka"], "matched_district": m["district"],
                "matched_state": m["state"], "matched_pincode": m["pincode_disp"],
                "matched_is_archived": m["is_archived"], "matched_replaced_by_id": m["replaced_by_id"],
                "matched_zone_id": m["zone_id"], "matched_zone_name": m["zone_name"],
                "fuzzy_score": r5["score"], "phonetic_match": r5["phonetic_match"],
            })
            return result

    return result


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    t0 = time.time()
    print("Loading village master...", flush=True)
    master = load_master()
    print(f"  {len(master)} rows loaded in {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    print("Building indices...", flush=True)
    idx = build_indices(master)
    print(f"  built in {time.time()-t0:.1f}s", flush=True)

    fieldnames = [
        "farmer_id", "orig_village", "orig_taluka", "orig_district", "orig_state", "orig_pincode",
        "orig_latitude", "orig_longitude",
        "matched_gate", "matched_village_id", "matched_village", "matched_taluka", "matched_district",
        "matched_state", "matched_pincode", "matched_is_archived", "matched_replaced_by_id",
        "matched_zone_id", "matched_zone_name", "fuzzy_score", "phonetic_match", "gps_distance_km",
    ]

    t0 = time.time()
    n = 0
    gate_counts = {}
    with open(TAM_PATH, newline="", encoding="utf-8") as fin, \
         open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            if limit and n >= limit:
                break
            res = resolve_row(row, idx, master)
            writer.writerow(res)
            gate_counts[res["matched_gate"]] = gate_counts.get(res["matched_gate"], 0) + 1
            n += 1
            if n % 5000 == 0:
                print(f"  processed {n} rows in {time.time()-t0:.1f}s", flush=True)

    print(f"Done: {n} rows in {time.time()-t0:.1f}s -> {OUTPUT_PATH}", flush=True)
    print("Gate breakdown:", flush=True)
    for g in sorted(gate_counts, key=lambda x: (x == "unresolved", x)):
        print(f"  gate {g}: {gate_counts[g]}", flush=True)


if __name__ == "__main__":
    main()
