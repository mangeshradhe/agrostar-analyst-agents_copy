import warnings
warnings.filterwarnings('ignore')

import pandas as pd
from google.cloud import bigquery

INPUT_FILE  = '/Users/darpan/Downloads/15K village satya mishra.xlsx'
OUTPUT_FILE = '/Users/darpan/Downloads/15K village satya mishra - serviceability.xlsx'
PROJECT     = 'agrostar-data'

# ── helpers ──────────────────────────────────────────────────────────────────
def norm(s):
    if pd.isna(s):
        return ''
    return str(s).lower().strip()

def norm_pincode(x):
    if pd.isna(x) or str(x).strip() in ('', 'nan', 'None'):
        return ''
    try:
        return str(int(float(x))).zfill(6)
    except (ValueError, OverflowError):
        return str(x).strip()

# ── Step 1: fetch reference data from BQ ────────────────────────────────────
client = bigquery.Client(project=PROJECT)

print('Fetching Village Master (UP) from BQ...')
vm_df = client.query("""
    SELECT
      id,
      LOWER(TRIM(village))  AS village_norm,
      LOWER(TRIM(taluka))   AS taluka_norm,
      LOWER(TRIM(district)) AS district_norm,
      LOWER(TRIM(pin_code)) AS pincode_norm,
      is_archived,
      replaced_by_id
    FROM `agrostar-data.static_tables_views.csr_villageaddress`
    WHERE LOWER(TRIM(state)) LIKE 'uttar%'
""").to_dataframe()
print(f'  VM rows: {len(vm_df):,}')

print('Fetching LMD Coverage (UP) from BQ...')
cov_df = client.query("""
    SELECT DISTINCT
      dc.coverage_type,
      LOWER(TRIM(dc.village))  AS village_norm,
      LOWER(TRIM(dc.pincode))  AS pincode_norm,
      LOWER(TRIM(da.district)) AS district_norm,
      LOWER(TRIM(da.taluka))   AS taluka_norm,
      CONCAT(TRIM(ui.first_name), ' ', TRIM(ui.last_name)) AS lmd_partner_name
    FROM `agrostar-data.prod_agroex_db_views.assignment_deliverycoverage` dc
    JOIN `agrostar-data.prod_agroex_db_views.assignment_deliveryarea` da
      ON da.id = dc.delivery_area_id
    JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocationfranchisemapping` apl
      ON apl.id = da.pickuplocation_franchise_mapping_id
    JOIN `agrostar-data.prod_agroex_db_views.assignment_franchise` asf
      ON asf.id = apl.franchise_id
    JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocation` pl
      ON pl.id = apl.pickuplocation_id
    JOIN `agrostar-data.prod_db_views.delivery_franchise` df
      ON df.id = asf.franchise_id
    JOIN `agrostar-data.prod_db_views.delivery_userinformation` ui
      ON ui.username = df.user_info_id
    WHERE da.is_active = 1 AND dc.is_active = 1
      AND asf.is_active = 1 AND pl.is_active = 1
      AND LOWER(TRIM(da.state)) LIKE 'uttar%'
""").to_dataframe()
print(f'  Coverage rows: {len(cov_df):,}')

# ── Step 2: prepare VM lookup dictionaries ───────────────────────────────────
# Build id → row map for replacement chain resolution
vm_by_id = vm_df.set_index('id')[['village_norm', 'pincode_norm', 'is_archived', 'replaced_by_id']].to_dict('index')

# Active VM rows
vm_active   = vm_df[vm_df['is_archived'] == 0].copy()
# Archived with replacement
vm_archived = vm_df[(vm_df['is_archived'] == 1) & vm_df['replaced_by_id'].notna()].copy()
vm_archived['replaced_by_id'] = vm_archived['replaced_by_id'].astype('Int64')

def resolve_canonical(vm_row):
    """Given a VM row, return (canonical_village, canonical_pincode)."""
    if vm_row['is_archived'] == 0:
        return vm_row['village_norm'], vm_row['pincode_norm']
    repl_id = vm_row.get('replaced_by_id')
    if pd.notna(repl_id) and int(repl_id) in vm_by_id:
        r = vm_by_id[int(repl_id)]
        return r['village_norm'], r['pincode_norm']
    return None, None

# ── Step 3: load and normalise input file ────────────────────────────────────
print('\nLoading input file...')
inp = pd.read_excel(INPUT_FILE)
print(f'  Rows: {len(inp):,} | Columns: {inp.columns.tolist()}')

inp['_village']  = inp['Village'].apply(norm)
inp['_taluka']   = inp['Taluka'].apply(norm)
inp['_district'] = inp['District'].apply(norm)
inp['_pincode']  = inp['Pincode'].apply(norm_pincode)
inp['_idx']      = inp.index          # preserve original row order

# Unique (village, taluka, district, pincode) combos — work on these, then map back
combos = inp[['_village','_taluka','_district','_pincode']].drop_duplicates().copy()
combos = combos.reset_index(drop=True)
print(f'  Unique address combos: {len(combos):,}')

# ── Tier 1: 5-field VM match ──────────────────────────────────────────────────
print('\nTier 1: 5-field VM match...')

# Build a merged VM (active + archived-with-replacement) for join
vm_for_t1 = pd.concat([
    vm_active[['village_norm','taluka_norm','district_norm','pincode_norm','id','is_archived','replaced_by_id']],
    vm_archived[['village_norm','taluka_norm','district_norm','pincode_norm','id','is_archived','replaced_by_id']]
], ignore_index=True)

t1 = combos.merge(
    vm_for_t1,
    left_on=['_village','_taluka','_district','_pincode'],
    right_on=['village_norm','taluka_norm','district_norm','pincode_norm'],
    how='left'
)

# Where multiple VM rows match, prefer active over archived-with-replacement
t1['_arc_priority'] = t1['is_archived'].map({0: 1, 1: 2}).fillna(3).astype(int)
t1 = t1.sort_values(['_village','_taluka','_district','_pincode','_arc_priority','id'])
t1 = t1.drop_duplicates(subset=['_village','_taluka','_district','_pincode'], keep='first')

# Resolve canonical
def get_canonical_t1(row):
    if pd.isna(row.get('id')):
        return None, None, False
    if row['is_archived'] == 0:
        return row['village_norm'], row['pincode_norm'], True
    if pd.notna(row.get('replaced_by_id')) and int(row['replaced_by_id']) in vm_by_id:
        r = vm_by_id[int(row['replaced_by_id'])]
        return r['village_norm'], r['pincode_norm'], True
    return None, None, False   # archived, no replacement → not resolved

t1[['canonical_village','canonical_pincode','t1_resolved']] = t1.apply(
    lambda r: pd.Series(get_canonical_t1(r)), axis=1
)

t1_resolved   = t1[t1['t1_resolved'] == True][['_village','_taluka','_district','_pincode','canonical_village','canonical_pincode']].copy()
t1_resolved['tier'] = 1
t1_unresolved = t1[t1['t1_resolved'] == False][['_village','_taluka','_district','_pincode']].copy()

print(f'  Tier 1 resolved: {len(t1_resolved):,} | unresolved: {len(t1_unresolved):,}')

# ── Tier 2: 4-field match (no pincode), ALL VM matches ───────────────────────
print('Tier 2: 4-field VM match (no pincode)...')

vm_for_t2 = pd.concat([
    vm_active[['village_norm','taluka_norm','district_norm','pincode_norm','id','is_archived','replaced_by_id']],
    vm_archived[['village_norm','taluka_norm','district_norm','pincode_norm','id','is_archived','replaced_by_id']]
], ignore_index=True)

t2_in = t1_unresolved.copy()
t2 = t2_in.merge(
    vm_for_t2,
    left_on=['_village','_taluka','_district'],
    right_on=['village_norm','taluka_norm','district_norm'],
    how='inner'   # INNER: only actual matches; keep ALL (no dedup — one address may match multiple VM pincodes)
)

def get_canonical_t2(row):
    if row['is_archived'] == 0:
        return row['village_norm'], row['pincode_norm']
    if pd.notna(row.get('replaced_by_id')) and int(row['replaced_by_id']) in vm_by_id:
        r = vm_by_id[int(row['replaced_by_id'])]
        return r['village_norm'], r['pincode_norm']
    return None, None

if len(t2) > 0:
    t2[['canonical_village','canonical_pincode']] = t2.apply(
        lambda r: pd.Series(get_canonical_t2(r)), axis=1
    )
    t2 = t2[t2['canonical_village'].notna()].copy()
    t2['tier'] = 2
    t2_resolved = t2[['_village','_taluka','_district','_pincode','canonical_village','canonical_pincode','tier']].drop_duplicates()
else:
    t2_resolved = pd.DataFrame(columns=['_village','_taluka','_district','_pincode','canonical_village','canonical_pincode','tier'])

# Tier 3: addresses not in t2_resolved at all
t2_resolved_keys = set(zip(t2_resolved['_village'], t2_resolved['_taluka'], t2_resolved['_district'], t2_resolved['_pincode']))
t3_in = t1_unresolved[~t1_unresolved.apply(
    lambda r: (r['_village'], r['_taluka'], r['_district'], r['_pincode']) in t2_resolved_keys, axis=1
)].copy()

print(f'  Tier 2 resolved: {len(t2_resolved["_village"].unique() if len(t2_resolved) > 0 else []):,} unique addresses | '
      f'Tier 3 candidates: {len(t3_in):,}')

# ── Tier 3: raw fallback ──────────────────────────────────────────────────────
t3_resolved = t3_in.copy()
t3_resolved['canonical_village'] = t3_resolved['_village']
t3_resolved['canonical_pincode'] = t3_resolved['_pincode']
t3_resolved['tier'] = 3
t3_resolved = t3_resolved[['_village','_taluka','_district','_pincode','canonical_village','canonical_pincode','tier']].drop_duplicates()

print(f'  Tier 3 raw fallback: {len(t3_resolved):,}')

# ── Combine all resolved ──────────────────────────────────────────────────────
all_resolved = pd.concat([
    t1_resolved[['_village','_taluka','_district','_pincode','canonical_village','canonical_pincode','tier']],
    t2_resolved,
    t3_resolved
], ignore_index=True)

# ── Coverage check ────────────────────────────────────────────────────────────
print('\nChecking coverage...')

# Split coverage by type
# Village-level coverage match = canonical_village + canonical_pincode (from VM resolution).
# assignment_deliverycoverage indexes coverage by village + pincode — that is the canonical join key.
# Validated June 2026: UP coverage pincodes align with LGD VM pincodes.
# Do NOT substitute district/taluka — village+pincode is the only correct key per the skill spec.
cov_village = cov_df[cov_df['coverage_type'] == 'village'][['village_norm','pincode_norm','lmd_partner_name']].copy()
cov_taluka  = cov_df[cov_df['coverage_type'] == 'taluka'][['district_norm','taluka_norm','lmd_partner_name']].copy()
cov_pincode = cov_df[cov_df['coverage_type'] == 'pincode'][['pincode_norm','lmd_partner_name']].copy()

# Village-level hits: canonical_village + canonical_pincode (both come from VM resolution)
ar_clean = all_resolved[
    all_resolved['canonical_village'].notna() &
    (all_resolved['canonical_village'] != '') &
    (all_resolved['canonical_village'].str.len() >= 2) &
    ~all_resolved['canonical_village'].isin(['na','n/a','nil','none','unknown','not available'])
].copy()

hits_village = ar_clean.merge(
    cov_village, left_on=['canonical_village','canonical_pincode'], right_on=['village_norm','pincode_norm'], how='inner'
)[['_village','_taluka','_district','_pincode','lmd_partner_name','tier']].copy()
hits_village['cov_priority'] = 1

# Taluka-level hits (district + taluka from raw)
hits_taluka = all_resolved.merge(
    cov_taluka, left_on=['_district','_taluka'], right_on=['district_norm','taluka_norm'], how='inner'
)[['_village','_taluka','_district','_pincode','lmd_partner_name','tier']].copy()
hits_taluka['cov_priority'] = 2

# Pincode-level hits (canonical pincode)
hits_pincode = all_resolved[all_resolved['canonical_pincode'].notna() & (all_resolved['canonical_pincode'] != '')].merge(
    cov_pincode, left_on='canonical_pincode', right_on='pincode_norm', how='inner'
)[['_village','_taluka','_district','_pincode','lmd_partner_name','tier']].copy()
hits_pincode['cov_priority'] = 3

all_hits = pd.concat([hits_village, hits_taluka, hits_pincode], ignore_index=True)

print(f'  Village hits: {len(hits_village):,} | Taluka hits: {len(hits_taluka):,} | Pincode hits: {len(hits_pincode):,}')

# Best LMD per combo: prefer village > taluka > pincode, then tier 1 > 2 > 3, then name alpha
if len(all_hits) > 0:
    best = (
        all_hits.sort_values(['_village','_taluka','_district','_pincode','cov_priority','tier','lmd_partner_name'])
        .drop_duplicates(subset=['_village','_taluka','_district','_pincode'], keep='first')
    )[['_village','_taluka','_district','_pincode','lmd_partner_name','cov_priority','tier']].copy()
    best['cov_type'] = best['cov_priority'].map({1:'village',2:'taluka',3:'pincode'})
    best['tier_label'] = best['tier'].map({1:'Tier 1 (exact)',2:'Tier 2 (no-pincode)',3:'Tier 3 (raw)'})
else:
    best = pd.DataFrame(columns=['_village','_taluka','_district','_pincode','lmd_partner_name','cov_priority','tier','cov_type','tier_label'])

# Serviceable set
svc_keys = set(zip(best['_village'], best['_taluka'], best['_district'], best['_pincode']))

# ── Farmer bucket per combo ───────────────────────────────────────────────────
# For bucket: need to know which combos have ANY Tier 1 or 2 resolution (non-address-problem)
tier12_keys = set(zip(
    all_resolved[all_resolved['tier'].isin([1,2])]['_village'],
    all_resolved[all_resolved['tier'].isin([1,2])]['_taluka'],
    all_resolved[all_resolved['tier'].isin([1,2])]['_district'],
    all_resolved[all_resolved['tier'].isin([1,2])]['_pincode']
))

def get_bucket(row):
    key = (row['_village'], row['_taluka'], row['_district'], row['_pincode'])
    if key in svc_keys:
        return 'Serviceable'
    if key in tier12_keys:
        return 'Non Serviceable'
    return 'Address Problem'

# ── Map back to input file ────────────────────────────────────────────────────
print('\nMapping results back to input rows...')

result = inp.copy()
result['_bucket'] = result.apply(get_bucket, axis=1)

def get_lmd(row):
    key = (row['_village'], row['_taluka'], row['_district'], row['_pincode'])
    match = best[(best['_village']==key[0]) & (best['_taluka']==key[1]) & (best['_district']==key[2]) & (best['_pincode']==key[3])]
    if len(match) > 0:
        return match.iloc[0]['lmd_partner_name'], match.iloc[0]['cov_type'], match.iloc[0]['tier_label']
    return None, None, None

lmd_data = result.apply(get_lmd, axis=1, result_type='expand')
result['LMD_Partner']   = lmd_data[0]
result['Matched_Via']   = lmd_data[1]
result['VM_Tier']       = lmd_data[2]
result['Is_Serviceable'] = (result['_bucket'] == 'Serviceable').astype(int)
result['Serviceability'] = result['_bucket']

# Add canonical address columns (take from Tier 1 resolved first, then 2, then 3)
canon_map = (
    all_resolved.sort_values('tier')
    .drop_duplicates(subset=['_village','_taluka','_district','_pincode'], keep='first')
    .set_index(['_village','_taluka','_district','_pincode'])[['canonical_village','canonical_pincode']]
)

def get_canon(row):
    key = (row['_village'], row['_taluka'], row['_district'], row['_pincode'])
    if key in canon_map.index:
        c = canon_map.loc[key]
        return c['canonical_village'], c['canonical_pincode']
    return None, None

canon_data = result.apply(get_canon, axis=1, result_type='expand')
result['Canonical_Village'] = canon_data[0]
result['Canonical_Pincode'] = canon_data[1]

# Drop internal columns
out_cols = ['Village','Taluka','Pincode','District',
            'Is_Serviceable','Serviceability','LMD_Partner',
            'Matched_Via','VM_Tier','Canonical_Village','Canonical_Pincode']
output = result[out_cols].copy()

# ── Summary ───────────────────────────────────────────────────────────────────
print('\n── RESULTS ─────────────────────────────────────────────────────────')
summary = result['Serviceability'].value_counts()
total   = len(result)
for bucket, count in summary.items():
    print(f'  {bucket:<20} {count:>6,}  ({100*count/total:.1f}%)')
print(f'  {"Total":<20} {total:>6,}')

print('\n── TOP LMD PARTNERS ──────────────────────────────────────────────────')
lmd_counts = result[result['Serviceability']=='Serviceable']['LMD_Partner'].value_counts().head(15)
for lmd, cnt in lmd_counts.items():
    print(f'  {lmd:<35} {cnt:>5,}')

# ── Save ──────────────────────────────────────────────────────────────────────
output.to_excel(OUTPUT_FILE, index=False)
print(f'\n✓ Saved to: {OUTPUT_FILE}')
