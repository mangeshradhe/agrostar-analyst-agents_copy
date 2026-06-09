"""
Generate zone_lmd_map.html — two-map Leaflet visualisation for Rajasthan villages.
Map 1: villages coloured by zone
Map 2: villages coloured by LMD partner
Each map has a searchable filter widget.
"""
import json, warnings
warnings.filterwarnings("ignore")

from google.cloud import bigquery

client = bigquery.Client(project="agrostar-data")

print("Fetching zone master...")
zones_rows = list(client.query("""
    SELECT id, name, tier
    FROM `agrostar-data.static_tables.csr_zone`
    ORDER BY id
""").result())
zones = [{"id": r.id, "name": r.name, "tier": r.tier} for r in zones_rows]
zone_id_to_idx = {z["id"]: i for i, z in enumerate(zones)}
print(f"  {len(zones)} zones")

print("Fetching village data (lat/lng + zone)...")
village_rows = list(client.query("""
    SELECT
      v.zone_id,
      ROUND(v.latitude, 4) AS lat,
      ROUND(v.longitude, 4) AS lng,
      v.village,
      v.pin_code,
      v.district
    FROM `agrostar-data.static_tables.csr_villageaddress` v
    WHERE LOWER(v.state) = 'rajasthan'
      AND v.is_archived = 0
      AND v.zone_id IS NOT NULL
      AND v.latitude IS NOT NULL
      AND v.longitude IS NOT NULL
""").result())
print(f"  {len(village_rows)} villages with zone+latlong")

# Build vpk → (lat, lng, village, pin, district)
vpk_to_village = {}
for r in village_rows:
    vpk = (r.village or "").strip().lower() + (r.pin_code or "").strip()
    vpk_to_village[vpk] = r

print("Fetching LMD coverage data...")
lmd_rows = list(client.query("""
    WITH lmd_raw AS (
      SELECT
        LOWER(CONCAT(TRIM(dc.village), TRIM(dc.pincode))) AS vpk,
        CONCAT(TRIM(dui.first_name), ' ', TRIM(dui.last_name)) AS lmd_name
      FROM `agrostar-data.prod_agroex_db_views.assignment_deliverycoverage` dc
      LEFT JOIN `agrostar-data.prod_agroex_db_views.assignment_deliveryarea` da
        ON dc.delivery_area_id = da.id
      LEFT JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocationfranchisemapping` aplfm
        ON da.pickuplocation_franchise_mapping_id = aplfm.id
      LEFT JOIN `agrostar-data.prod_agroex_db_views.assignment_franchise` asf
        ON aplfm.franchise_id = asf.id
      LEFT JOIN `agrostar-data.prod_agroex_db_views.assignment_pickuplocation` apl
        ON aplfm.pickuplocation_id = apl.id
      LEFT JOIN `agrostar-data.prod_db_views.delivery_franchise` df
        ON df.id = asf.franchise_id
      LEFT JOIN `agrostar-data.prod_db_views.delivery_userinformation` dui
        ON dui.username = df.user_info_id
      WHERE da.is_active = 1 AND dc.is_active = 1 AND apl.is_active = 1 AND asf.is_active = 1
        AND LOWER(dc.coverage_type) = 'village'
        AND LOWER(da.state) IN ('rajasthan', 'rajashtan')
    ),
    lmd_dedup AS (
      SELECT vpk, lmd_name,
        ROW_NUMBER() OVER (PARTITION BY vpk ORDER BY lmd_name) AS rn
      FROM lmd_raw
    )
    SELECT vpk, lmd_name FROM lmd_dedup WHERE rn = 1
""").result())
vpk_to_lmd = {r.vpk: r.lmd_name for r in lmd_rows}
print(f"  {len(vpk_to_lmd)} LMD-covered vpks")

# Build sorted LMD list (add "No LMD" at index 0)
all_lmds_set = sorted(set(vpk_to_lmd.values()))
lmd_list = ["No LMD"] + all_lmds_set
lmd_to_idx = {name: i for i, name in enumerate(lmd_list)}
print(f"  {len(lmd_list)-1} distinct LMD partners")

# Build compact data arrays
# Zone map: [[zone_idx, lat, lng, "village|pin|district"], ...]
# LMD map:  [[lmd_idx, lat, lng, "village|pin|district"], ...]
zone_data = []
lmd_data  = []

for r in village_rows:
    z_idx = zone_id_to_idx.get(r.zone_id, -1)
    if z_idx < 0:
        continue
    label = f"{r.village or ''}|{r.pin_code or ''}|{r.district or ''}"
    zone_data.append([z_idx, r.lat, r.lng, label])

    vpk = (r.village or "").strip().lower() + (r.pin_code or "").strip()
    lmd_name = vpk_to_lmd.get(vpk, "No LMD")
    l_idx = lmd_to_idx.get(lmd_name, 0)
    lmd_data.append([l_idx, r.lat, r.lng, label])

print(f"Zone map points: {len(zone_data)}")
print(f"LMD map points:  {len(lmd_data)}")

# Colour generators using golden-ratio hue distribution
def gen_colors(n, s=70, l=45):
    """n visually distinct HSL colours."""
    golden = 137.508
    return [f"hsl({int(i * golden) % 360},{s}%,{l}%)" for i in range(n)]

zone_colors = gen_colors(len(zones), s=72, l=44)
lmd_colors_list = ["#94a3b8"] + gen_colors(len(lmd_list)-1, s=78, l=42)

# Tier badge lookup
tier_map = {z["name"]: z["tier"] for z in zones}

# Zone select options
zone_opts = "\n".join(
    f'<option value="{i}">[{z["tier"]}] {z["name"]}</option>'
    for i, z in enumerate(zones)
)
# LMD select options
lmd_opts = "\n".join(
    f'<option value="{i}">{"⚪ " if i==0 else ""}{name}</option>'
    for i, name in enumerate(lmd_list)
)

zone_colors_js = json.dumps(zone_colors)
lmd_colors_js  = json.dumps(lmd_colors_list)
zone_data_js   = json.dumps(zone_data, separators=(',', ':'))
lmd_data_js    = json.dumps(lmd_data,  separators=(',', ':'))
zones_meta_js  = json.dumps([{"n": z["name"], "t": z["tier"]} for z in zones])
lmds_meta_js   = json.dumps(lmd_list)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rajasthan Village Maps — Zone & LMD</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}

/* ── TOP BAR ── */
.topbar {{ background: #1e293b; border-bottom: 1px solid #334155; padding: 0 20px; display: flex; align-items: center; gap: 0; flex-shrink: 0; height: 48px; }}
.topbar h1 {{ font-size: 14px; font-weight: 700; color: #f8fafc; margin-right: 24px; white-space: nowrap; }}
.tab-btn {{ padding: 0 18px; height: 48px; background: transparent; border: none; color: #94a3b8; font-size: 13px; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all .15s; white-space: nowrap; }}
.tab-btn:hover {{ color: #e2e8f0; }}
.tab-btn.active {{ color: #38bdf8; border-bottom-color: #38bdf8; }}

/* ── LAYOUT ── */
.map-panel {{ display: none; flex: 1; overflow: hidden; position: relative; }}
.map-panel.active {{ display: flex; }}

/* ── SIDEBAR ── */
.sidebar {{ width: 270px; flex-shrink: 0; background: #1e293b; border-right: 1px solid #334155; display: flex; flex-direction: column; overflow: hidden; z-index: 1000; }}
.sidebar-header {{ padding: 14px 16px 10px; border-bottom: 1px solid #334155; }}
.sidebar-header h2 {{ font-size: 12px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: .6px; margin-bottom: 8px; }}
.search-box {{ width: 100%; padding: 7px 10px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; font-size: 12px; outline: none; }}
.search-box:focus {{ border-color: #38bdf8; }}
.sidebar-actions {{ display: flex; gap: 6px; margin-top: 8px; }}
.act-btn {{ flex: 1; padding: 5px 0; background: #334155; border: none; color: #94a3b8; font-size: 11px; font-weight: 600; border-radius: 4px; cursor: pointer; transition: background .15s; }}
.act-btn:hover {{ background: #475569; color: #e2e8f0; }}
.act-btn.primary {{ background: #0284c7; color: #fff; }}
.act-btn.primary:hover {{ background: #0369a1; }}

/* ── OPTION LIST ── */
.option-list {{ flex: 1; overflow-y: auto; padding: 6px 0; }}
.option-list::-webkit-scrollbar {{ width: 4px; }}
.option-list::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 2px; }}
.opt-item {{ display: flex; align-items: center; gap: 8px; padding: 5px 14px; cursor: pointer; transition: background .1s; font-size: 12px; }}
.opt-item:hover {{ background: #263148; }}
.opt-item.selected {{ background: #1e3a5f; }}
.opt-swatch {{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }}
.opt-label {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #cbd5e1; }}
.opt-count {{ font-size: 10px; color: #475569; flex-shrink: 0; }}
.tier-badge {{ font-size: 9px; padding: 1px 4px; border-radius: 3px; font-weight: 700; flex-shrink: 0; }}
.tier-T1 {{ background: #1d4ed8; color: #bfdbfe; }}
.tier-T2 {{ background: #7c3aed; color: #ede9fe; }}
.tier-T3 {{ background: #b45309; color: #fde68a; }}

/* ── MAP CONTAINER ── */
.map-wrap {{ flex: 1; position: relative; }}
#map-zone, #map-lmd {{ height: 100%; width: 100%; }}

/* ── STATUS BAR ── */
.status-bar {{ position: absolute; bottom: 10px; left: 280px; background: rgba(15,23,42,.85); border: 1px solid #334155; border-radius: 6px; padding: 6px 12px; font-size: 11px; color: #94a3b8; z-index: 500; backdrop-filter: blur(4px); pointer-events: none; }}
.status-bar b {{ color: #38bdf8; }}

/* ── TOOLTIP ── */
.leaflet-tooltip {{ background: #1e293b; border: 1px solid #334155; color: #e2e8f0; font-size: 11px; border-radius: 5px; padding: 5px 8px; white-space: nowrap; }}
</style>
</head>
<body>

<div class="topbar">
  <h1>🗺 Rajasthan Village Maps</h1>
  <button class="tab-btn active" onclick="switchMap('zone', this)">🎨 Zone Map</button>
  <button class="tab-btn" onclick="switchMap('lmd', this)">🚚 LMD Partner Map</button>
</div>

<!-- ZONE MAP -->
<div id="panel-zone" class="map-panel active">
  <div class="sidebar" id="sb-zone">
    <div class="sidebar-header">
      <h2>Filter by Zone</h2>
      <input class="search-box" id="search-zone" placeholder="Search zone name..." oninput="filterList('zone')">
      <div class="sidebar-actions">
        <button class="act-btn" onclick="clearFilter('zone')">Show All</button>
        <button class="act-btn primary" onclick="zoomToSelected('zone')">Zoom to Selection</button>
      </div>
    </div>
    <div class="option-list" id="list-zone"></div>
  </div>
  <div class="map-wrap">
    <div id="map-zone"></div>
    <div class="status-bar" id="stat-zone">All <b>0</b> villages · 227 zones · Rajasthan</div>
  </div>
</div>

<!-- LMD MAP -->
<div id="panel-lmd" class="map-panel">
  <div class="sidebar" id="sb-lmd">
    <div class="sidebar-header">
      <h2>Filter by LMD Partner</h2>
      <input class="search-box" id="search-lmd" placeholder="Search LMD name..." oninput="filterList('lmd')">
      <div class="sidebar-actions">
        <button class="act-btn" onclick="clearFilter('lmd')">Show All</button>
        <button class="act-btn primary" onclick="zoomToSelected('lmd')">Zoom to Selection</button>
      </div>
    </div>
    <div class="option-list" id="list-lmd"></div>
  </div>
  <div class="map-wrap">
    <div id="map-lmd"></div>
    <div class="status-bar" id="stat-lmd">All <b>0</b> villages · {len(lmd_list)-1} LMD partners · Rajasthan</div>
  </div>
</div>

<script>
/* ══════════════════════════════════════
   DATA (generated by generate_village_map.py)
══════════════════════════════════════ */
const ZONE_COLORS = {zone_colors_js};
const LMD_COLORS  = {lmd_colors_js};
const ZONES_META  = {zones_meta_js};
const LMDS_META   = {lmds_meta_js};

// Each row: [idx, lat, lng, "village|pin|district"]
const ZONE_DATA = {zone_data_js};
const LMD_DATA  = {lmd_data_js};

/* ══════════════════════════════════════
   MAP INIT
══════════════════════════════════════ */
const RJ_CENTER = [27.0, 74.2];
const RJ_ZOOM   = 6;

const mapsReady = {{}};

function initMap(id) {{
  const m = L.map(id, {{
    center: RJ_CENTER,
    zoom: RJ_ZOOM,
    renderer: L.canvas({{ padding: 0.5 }}),
    preferCanvas: true
  }});
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18,
    opacity: 0.55
  }}).addTo(m);
  return m;
}}

/* Deferred: init maps only when tab is shown */
let mapZone = null, mapLmd = null;
let layerZone = null, layerLmd = null;

/* ══════════════════════════════════════
   BUILD LAYERS
══════════════════════════════════════ */
function buildLayer(map, data, colors, selectedIdx) {{
  if (layerCache[map._leaflet_id] && selectedIdx === null) {{
    return layerCache[map._leaflet_id];
  }}
  const layer = L.layerGroup([], {{ renderer: map.options.renderer }});
  const hasFilter = selectedIdx !== null;
  for (let i = 0; i < data.length; i++) {{
    const d = data[i];
    const idx = d[0], lat = d[1], lng = d[2], label = d[3];
    const active = !hasFilter || idx === selectedIdx;
    const parts = label.split('|');
    const village = parts[0], pin = parts[1], district = parts[2];
    const c = L.circleMarker([lat, lng], {{
      radius: active ? (hasFilter ? 5 : 3) : 2,
      fillColor: active ? colors[idx] : '#334155',
      color: active ? colors[idx] : '#1e293b',
      weight: 0.5,
      fillOpacity: active ? (hasFilter ? 0.9 : 0.75) : 0.25,
      opacity: active ? 0.8 : 0.15
    }});
    c.bindTooltip(
      `<b>${{village}}</b><br>Pin: ${{pin}} · ${{district}}`,
      {{ sticky: true, offset: [8, 0] }}
    );
    layer.addLayer(c);
  }}
  return layer;
}}

const layerCache = {{}};
let selectedZone = null;
let selectedLmd  = null;

function renderZoneLayer() {{
  if (!mapZone) return;
  if (layerZone) mapZone.removeLayer(layerZone);
  layerZone = buildLayer(mapZone, ZONE_DATA, ZONE_COLORS, selectedZone);
  layerZone.addTo(mapZone);
  updateStat('zone', selectedZone);
}}

function renderLmdLayer() {{
  if (!mapLmd) return;
  if (layerLmd) mapLmd.removeLayer(layerLmd);
  layerLmd = buildLayer(mapLmd, LMD_DATA, LMD_COLORS, selectedLmd);
  layerLmd.addTo(mapLmd);
  updateStat('lmd', selectedLmd);
}}

/* ══════════════════════════════════════
   SIDEBAR LISTS
══════════════════════════════════════ */
// Precompute counts
const zoneCount = new Array(ZONES_META.length).fill(0);
ZONE_DATA.forEach(d => zoneCount[d[0]]++);
const lmdCount = new Array(LMDS_META.length).fill(0);
LMD_DATA.forEach(d => lmdCount[d[0]]++);

function buildSidebarList(type) {{
  const meta   = type === 'zone' ? ZONES_META : LMDS_META;
  const colors = type === 'zone' ? ZONE_COLORS : LMD_COLORS;
  const counts = type === 'zone' ? zoneCount   : lmdCount;
  const list   = document.getElementById('list-' + type);
  list.innerHTML = '';
  for (let i = 0; i < meta.length; i++) {{
    const item = meta[i];
    const name = type === 'zone' ? item.n : item;
    const tier = type === 'zone' ? item.t : null;
    const cnt  = counts[i] || 0;
    const div = document.createElement('div');
    div.className = 'opt-item';
    div.dataset.idx = i;
    div.dataset.name = name.toLowerCase();
    div.onclick = () => selectItem(type, i, div);
    const tierHtml = tier ? `<span class="tier-badge tier-${{tier}}">${{tier}}</span>` : '';
    div.innerHTML = `
      <span class="opt-swatch" style="background:${{colors[i]}}"></span>
      <span class="opt-label">${{name}}</span>
      ${{tierHtml}}
      <span class="opt-count">${{cnt}}</span>`;
    list.appendChild(div);
  }}
}}

function filterList(type) {{
  const q = document.getElementById('search-' + type).value.toLowerCase();
  document.querySelectorAll(`#list-${{type}} .opt-item`).forEach(el => {{
    el.style.display = el.dataset.name.includes(q) ? '' : 'none';
  }});
}}

function selectItem(type, idx, el) {{
  const listEl = document.getElementById('list-' + type);
  listEl.querySelectorAll('.opt-item').forEach(e => e.classList.remove('selected'));
  const same = type === 'zone' ? selectedZone === idx : selectedLmd === idx;
  if (same) {{
    if (type === 'zone') selectedZone = null; else selectedLmd = null;
  }} else {{
    el.classList.add('selected');
    if (type === 'zone') selectedZone = idx; else selectedLmd = idx;
  }}
  if (type === 'zone') renderZoneLayer(); else renderLmdLayer();
}}

function clearFilter(type) {{
  if (type === 'zone') selectedZone = null; else selectedLmd = null;
  document.querySelectorAll(`#list-${{type}} .opt-item`).forEach(e => e.classList.remove('selected'));
  if (type === 'zone') renderZoneLayer(); else renderLmdLayer();
}}

function zoomToSelected(type) {{
  const idx = type === 'zone' ? selectedZone : selectedLmd;
  if (idx === null) return;
  const data = type === 'zone' ? ZONE_DATA : LMD_DATA;
  const pts = data.filter(d => d[0] === idx).map(d => [d[1], d[2]]);
  if (!pts.length) return;
  const map = type === 'zone' ? mapZone : mapLmd;
  map.fitBounds(L.latLngBounds(pts), {{ padding: [40, 40] }});
}}

function updateStat(type, sel) {{
  const data = type === 'zone' ? ZONE_DATA : LMD_DATA;
  const meta = type === 'zone' ? ZONES_META : LMDS_META;
  const total = sel === null ? data.length : data.filter(d => d[0] === sel).length;
  const label = sel === null ? 'All' : (type === 'zone' ? meta[sel].n : meta[sel]);
  const groups = type === 'zone' ? ZONES_META.length : (LMDS_META.length - 1);
  const unit   = type === 'zone' ? 'zones' : 'LMD partners';
  document.getElementById('stat-' + type).innerHTML =
    `${{sel !== null ? `<b>${{label}}</b>:` : 'All'}} <b>${{total.toLocaleString()}}</b> villages · ${{groups}} ${{unit}} · Rajasthan`;
}}

/* ══════════════════════════════════════
   TAB SWITCHING
══════════════════════════════════════ */
function switchMap(type, btn) {{
  document.querySelectorAll('.map-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + type).classList.add('active');
  btn.classList.add('active');

  if (type === 'zone' && !mapZone) {{
    mapZone = initMap('map-zone');
    buildSidebarList('zone');
    renderZoneLayer();
  }} else if (type === 'zone') {{
    mapZone.invalidateSize();
  }}

  if (type === 'lmd' && !mapLmd) {{
    mapLmd = initMap('map-lmd');
    buildSidebarList('lmd');
    renderLmdLayer();
  }} else if (type === 'lmd') {{
    mapLmd.invalidateSize();
  }}
}}

/* ══════════════════════════════════════
   BOOT — init zone map immediately
══════════════════════════════════════ */
window.addEventListener('DOMContentLoaded', () => {{
  mapZone = initMap('map-zone');
  buildSidebarList('zone');
  renderZoneLayer();
}});
</script>
</body>
</html>
"""

out_path = "/Users/darpan/Documents/claude code/DVS Analysis/zone_lmd_map.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

import os
size_mb = os.path.getsize(out_path) / 1_048_576
print(f"\nGenerated: {out_path}")
print(f"File size: {size_mb:.1f} MB")
print("Done.")
