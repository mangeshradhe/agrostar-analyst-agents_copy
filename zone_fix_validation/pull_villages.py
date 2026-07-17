"""
Pull all active Gujarat villages (with zone) from BQ into a local CSV.
Run once per state before test_dvs_resolution.py.
"""
import csv
import sys
import warnings
warnings.filterwarnings("ignore")

from google.cloud import bigquery

STATE = sys.argv[1] if len(sys.argv) > 1 else "gujarat"
OUT_PATH = f"/Users/darpan/Documents/claude code/DVS Analysis/zone_fix_validation/villages_{STATE}.csv"

client = bigquery.Client(project="agrostar-data")

print(f"Fetching active villages for state LIKE '{STATE}%' ...")
rows = list(client.query(f"""
    SELECT
      v.id AS village_id,
      v.village,
      v.district,
      v.taluka,
      v.state,
      v.pin_code,
      v.zone_id,
      z.name AS zone_name
    FROM `agrostar-data.static_tables.csr_villageaddress` v
    LEFT JOIN `agrostar-data.static_tables.csr_zone` z ON z.id = v.zone_id
    WHERE LOWER(TRIM(v.state)) LIKE '{STATE}%'
      AND v.is_archived = 0
    ORDER BY v.id
""").result())

print(f"  {len(rows)} villages fetched")

with open(OUT_PATH, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["village_id", "village", "district", "taluka", "state", "pin_code", "zone_id", "zone_name"])
    for r in rows:
        writer.writerow([r.village_id, r.village, r.district, r.taluka, r.state, r.pin_code, r.zone_id, r.zone_name])

print(f"Written to {OUT_PATH}")
