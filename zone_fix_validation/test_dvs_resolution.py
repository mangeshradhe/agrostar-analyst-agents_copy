"""
Read villages_<state>.csv, hit the live CRM dvsresolution API per row,
compare the resolved store's own zone against the village's expected zone.

Checks two things per village:
  store0_match  - does the FIRST store in the returned list (closest) match the village's zone?
  any_match     - does ANY store in the returned list match the village's zone?

Prints one summary line to stdout every 1000 rows (for Monitor to pick up),
plus an immediate line on any hard error (auth failure, exception).

Usage:
  python3 test_dvs_resolution.py gujarat --limit 19107 --delay 0.5
"""
import csv
import os
import sys
import time
import argparse
import requests
import warnings
warnings.filterwarnings("ignore")

API_URL = "https://crm.agrostar.in/crmservice/v1/addressbyid/"

# Token is a live CSR session credential — never hardcode it here.
# Set it before running: export CRM_AUTH_TOKEN="eyJ..."
CRM_AUTH_TOKEN = os.environ.get("CRM_AUTH_TOKEN")
if not CRM_AUTH_TOKEN:
    sys.exit("CRM_AUTH_TOKEN env var not set. Run: export CRM_AUTH_TOKEN=\"<token from a fresh CRM session>\"")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://crm.agrostar.in/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Source": "CSRRJ",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "X-Authorization-Token": CRM_AUTH_TOKEN,
}


def call_api(village, district, state, pincode, taluka):
    params = {
        "addressType": "shipping",
        "village": village,
        "district": district,
        "state": state,
        "pincode": pincode,
        "taluka": taluka,
        "type": "dvsresolution",
    }
    return requests.get(API_URL, params=params, headers=HEADERS, timeout=15)


def zone_match(store_list, expected_zone):
    if not expected_zone:
        return "NO_EXPECTED_ZONE", "NO_EXPECTED_ZONE", None
    if not store_list:
        return "NO_STORE", "NO_STORE", None
    store0_zones = store_list[0].get("servingZones") or []
    store0 = "MATCH" if expected_zone in store0_zones else "MISMATCH"
    any_ok = any(expected_zone in (s.get("servingZones") or []) for s in store_list)
    any_result = "MATCH" if any_ok else "MISMATCH"
    return store0, any_result, store0_zones


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--start-index", type=int, default=0, help="resume from this row index (0-based), appends to existing results file")
    ap.add_argument("--out-suffix", type=str, default="", help="write to results_<state><suffix>.csv instead of the main results file")
    args = ap.parse_args()

    base = "/Users/darpan/Documents/claude code/DVS Analysis/zone_fix_validation"
    in_path = f"{base}/villages_{args.state}.csv"
    out_path = f"{base}/results_{args.state}{args.out_suffix}.csv"

    with open(in_path) as f:
        rows = list(csv.DictReader(f))

    total = min(args.limit, len(rows))
    resume = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    print(f"[{args.state}] START: {total - args.start_index} villages to test (rows {args.start_index}-{total}), "
          f"delay={args.delay}s, resume={resume}, "
          f"est. runtime ~{round((total-args.start_index)*args.delay/60,1)} min", flush=True)

    batch_match0 = batch_mismatch0 = batch_matchA = batch_mismatchA = 0
    batch_no_store = batch_no_zone = 0
    batch_flags = []
    batch_start = args.start_index + 1

    file_mode = "a" if resume else "w"
    with open(out_path, file_mode, newline="") as out:
        writer = csv.writer(out)
        if not resume:
            writer.writerow([
                "village_id", "village", "district", "taluka", "state", "pin_code",
                "expected_zone_id", "expected_zone_name",
                "http_status", "resolved_store_count", "store0_zones", "resolution_reason",
                "store0_match", "any_match", "error"
            ])

        for i, r in enumerate(rows[args.start_index:total], start=args.start_index):
            error = ""
            http_status = None
            store_count = ""
            reason = ""
            store0_match = ""
            any_match_result = ""
            store0_zones = None
            try:
                resp = call_api(r["village"], r["district"], r["state"], r["pin_code"], r["taluka"])
                http_status = resp.status_code
                if resp.status_code in (401, 403):
                    print(f"[{args.state}] AUTH ERROR at row {i} ({r['village']}) — token likely expired/rejected. STOPPING.", flush=True)
                    writer.writerow([r["village_id"], r["village"], r["district"], r["taluka"], r["state"], r["pin_code"],
                                      r["zone_id"], r["zone_name"], http_status, "", "", "", "", "", "TOKEN_EXPIRED_OR_FORBIDDEN"])
                    break
                data = resp.json()
                store_raw = data.get("responseData", {}).get("store", [])
                # API returns a single dict when exactly 1 store resolves, a list otherwise
                if isinstance(store_raw, dict):
                    store_list = [store_raw] if store_raw else []
                else:
                    store_list = store_raw or []
                store_count = len(store_list)
                reason = data.get("responseData", {}).get("resolutionReason", "")
                store0_match, any_match_result, store0_zones = zone_match(store_list, r["zone_name"])
            except Exception as e:
                error = str(e)
                print(f"[{args.state}] ERROR at row {i} ({r['village']}): {error}", flush=True)

            writer.writerow([
                r["village_id"], r["village"], r["district"], r["taluka"], r["state"], r["pin_code"],
                r["zone_id"], r["zone_name"], http_status, store_count, store0_zones, reason,
                store0_match, any_match_result, error
            ])

            if store0_match == "MATCH":
                batch_match0 += 1
            elif store0_match == "MISMATCH":
                batch_mismatch0 += 1
                batch_flags.append(f"{r['village']}/{r['district']}/{r['taluka']} expected={r['zone_name']} got={store0_zones}")
            elif store0_match == "NO_STORE":
                batch_no_store += 1
            elif store0_match == "NO_EXPECTED_ZONE":
                batch_no_zone += 1

            if any_match_result == "MATCH":
                batch_matchA += 1
            elif any_match_result == "MISMATCH":
                batch_mismatchA += 1

            out.flush()

            if (i + 1) % 1000 == 0 or (i + 1) == total:
                flag_str = "NONE" if not batch_flags else " | ".join(batch_flags[:5]) + (f" (+{len(batch_flags)-5} more)" if len(batch_flags) > 5 else "")
                print(f"[{args.state}] BATCH {batch_start}-{i+1}/{total}: "
                      f"store0 match={batch_match0} mismatch={batch_mismatch0} no_store={batch_no_store} no_zone={batch_no_zone} | "
                      f"any_store match={batch_matchA} mismatch={batch_mismatchA} | "
                      f"RED FLAGS: {flag_str}", flush=True)
                batch_match0 = batch_mismatch0 = batch_matchA = batch_mismatchA = 0
                batch_no_store = batch_no_zone = 0
                batch_flags = []
                batch_start = i + 2

            time.sleep(args.delay)

    print(f"[{args.state}] DONE. Results written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
