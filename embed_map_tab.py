"""
Embeds the Zone & LMD map as a new tab inside farmer_serviceability_flow.html.
Extracts JS data blobs from zone_lmd_map.html (already generated) and inserts
all map CSS, HTML, and JS at the right anchors in the main dashboard file.
"""
import re, os

BASE = "/Users/darpan/Documents/claude code/DVS Analysis"
SRC  = os.path.join(BASE, "zone_lmd_map.html")
DST  = os.path.join(BASE, "farmer_serviceability_flow.html")

# ── 1. Extract JS data constants from the generated map file ─────────────────
print("Reading zone_lmd_map.html...")
with open(SRC, encoding="utf-8") as f:
    map_src = f.read()

def extract_const(src, name):
    m = re.search(rf'const {name}\s*=\s*(.+?);[ \t]*\n', src, re.DOTALL)
    if not m:
        raise ValueError(f"Could not find: const {name}")
    return m.group(1).strip()

ZONE_COLORS = extract_const(map_src, "ZONE_COLORS")
LMD_COLORS  = extract_const(map_src, "LMD_COLORS")
ZONES_META  = extract_const(map_src, "ZONES_META")
LMDS_META   = extract_const(map_src, "LMDS_META")
ZONE_DATA   = extract_const(map_src, "ZONE_DATA")
LMD_DATA    = extract_const(map_src, "LMD_DATA")
print(f"  ZONE_DATA rows : {ZONE_DATA.count('[')//1} chars={len(ZONE_DATA):,}")
print(f"  LMD_DATA  rows : {LMD_DATA.count('[')//1} chars={len(LMD_DATA):,}")

# ── 2. Read current dashboard ─────────────────────────────────────────────────
print("Reading farmer_serviceability_flow.html...")
with open(DST, encoding="utf-8") as f:
    html = f.read()

# Guard: don't double-embed
if "tab-map" in html:
    print("Map tab already present — nothing to do.")
    exit(0)

# ── 3. MAP CSS ────────────────────────────────────────────────────────────────
MAP_CSS = """
  /* ── MAP TAB ── */
  @import url('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');

  .map-fullpanel { display: none; }
  .map-fullpanel.active {
    display: flex; flex-direction: column;
    height: calc(100vh - 108px);
    border-radius: 12px; overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,.12);
  }
  .map-subtab-bar {
    background: #1e293b; display: flex; align-items: center; gap: 4px;
    padding: 0 16px; height: 44px; flex-shrink: 0;
    border-bottom: 1px solid #334155;
  }
  .map-subtab-bar .msb-title {
    font-size: 11px; font-weight: 700; color: #64748b;
    text-transform: uppercase; letter-spacing: .5px; margin-right: 14px; white-space: nowrap;
  }
  .msub-btn {
    padding: 6px 16px; border: none; background: transparent;
    color: #64748b; font-size: 12px; font-weight: 600; cursor: pointer;
    border-bottom: 2px solid transparent; transition: all .15s; border-radius: 0;
    height: 44px;
  }
  .msub-btn:hover { color: #e2e8f0; }
  .msub-btn.active { color: #38bdf8; border-bottom-color: #38bdf8; }
  .map-stat-pill {
    margin-left: auto; font-size: 11px; color: #94a3b8;
    background: #0f172a; border: 1px solid #334155; border-radius: 20px;
    padding: 3px 12px; white-space: nowrap;
  }
  .map-stat-pill b { color: #38bdf8; }

  .submap-wrap { flex: 1; display: none; overflow: hidden; }
  .submap-wrap.active { display: flex; }

  /* sidebar */
  .m-sidebar {
    width: 255px; flex-shrink: 0; background: #1e293b;
    border-right: 1px solid #334155; display: flex; flex-direction: column; overflow: hidden;
  }
  .m-sb-hdr { padding: 12px 14px 8px; border-bottom: 1px solid #334155; }
  .m-sb-hdr h2 { font-size: 10px; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: .5px; margin-bottom: 7px; }
  .m-search { width: 100%; padding: 6px 10px; background: #0f172a; border: 1px solid #334155;
    border-radius: 5px; color: #e2e8f0; font-size: 11px; outline: none; }
  .m-search:focus { border-color: #38bdf8; }
  .m-sb-acts { display: flex; gap: 5px; margin-top: 7px; }
  .m-act { flex: 1; padding: 5px 0; background: #334155; border: none; color: #94a3b8;
    font-size: 10px; font-weight: 700; border-radius: 4px; cursor: pointer; transition: background .15s; }
  .m-act:hover { background: #475569; color: #e2e8f0; }
  .m-act.mp { background: #0284c7; color: #fff; }
  .m-act.mp:hover { background: #0369a1; }
  .m-opt-list { flex: 1; overflow-y: auto; padding: 4px 0; }
  .m-opt-list::-webkit-scrollbar { width: 3px; }
  .m-opt-list::-webkit-scrollbar-thumb { background: #334155; border-radius: 2px; }
  .m-opt { display: flex; align-items: center; gap: 7px; padding: 4px 12px; cursor: pointer;
    transition: background .1s; font-size: 11px; }
  .m-opt:hover { background: #263148; }
  .m-opt.sel { background: #1e3a5f; }
  .m-swatch { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }
  .m-opt-lbl { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #cbd5e1; }
  .m-opt-cnt { font-size: 10px; color: #475569; flex-shrink: 0; }
  .m-tier { font-size: 8px; padding: 1px 4px; border-radius: 3px; font-weight: 700; flex-shrink: 0; }
  .m-t1 { background: #1d4ed8; color: #bfdbfe; }
  .m-t2 { background: #7c3aed; color: #ede9fe; }
  .m-t3 { background: #b45309; color: #fde68a; }

  /* map area */
  .m-area { flex: 1; position: relative; }
  #mmap-zone, #mmap-lmd { height: 100%; width: 100%; }
  .leaflet-tooltip { background: #1e293b !important; border: 1px solid #334155 !important;
    color: #e2e8f0 !important; font-size: 10px !important; border-radius: 4px; padding: 4px 7px; }
"""

# ── 4. MAP HTML PANEL ─────────────────────────────────────────────────────────
MAP_HTML = """
<div id="tab-map" class="tab-panel map-fullpanel">
  <!-- sub-tab bar -->
  <div class="map-subtab-bar">
    <span class="msb-title">🗺 Village Maps · Rajasthan</span>
    <button class="msub-btn active" id="msub-zone" onclick="switchSubmap('zone',this)">🎨 Zone Map</button>
    <button class="msub-btn" id="msub-lmd"  onclick="switchSubmap('lmd',this)">🚚 LMD Partner Map</button>
    <span class="map-stat-pill" id="mstat-active">Loading…</span>
  </div>

  <!-- Zone sub-map -->
  <div id="submap-zone" class="submap-wrap active">
    <div class="m-sidebar">
      <div class="m-sb-hdr">
        <h2>Filter by Zone</h2>
        <input class="m-search" id="msearch-zone" placeholder="Search zone…" oninput="mFilterList('zone')">
        <div class="m-sb-acts">
          <button class="m-act" onclick="mClearFilter('zone')">Show All</button>
          <button class="m-act mp" onclick="mZoomSel('zone')">Zoom to Zone</button>
        </div>
      </div>
      <div class="m-opt-list" id="mlist-zone"></div>
    </div>
    <div class="m-area"><div id="mmap-zone"></div></div>
  </div>

  <!-- LMD sub-map -->
  <div id="submap-lmd" class="submap-wrap">
    <div class="m-sidebar">
      <div class="m-sb-hdr">
        <h2>Filter by LMD Partner</h2>
        <input class="m-search" id="msearch-lmd" placeholder="Search LMD…" oninput="mFilterList('lmd')">
        <div class="m-sb-acts">
          <button class="m-act" onclick="mClearFilter('lmd')">Show All</button>
          <button class="m-act mp" onclick="mZoomSel('lmd')">Zoom to LMD</button>
        </div>
      </div>
      <div class="m-opt-list" id="mlist-lmd"></div>
    </div>
    <div class="m-area"><div id="mmap-lmd"></div></div>
  </div>
</div>
"""

# ── 5. MAP JAVASCRIPT ─────────────────────────────────────────────────────────
MAP_JS = f"""
/* ══════════════════════════════════════════════════
   VILLAGE MAP DATA  (auto-generated — do not hand-edit)
══════════════════════════════════════════════════ */
const M_ZONE_COLORS = {ZONE_COLORS};
const M_LMD_COLORS  = {LMD_COLORS};
const M_ZONES_META  = {ZONES_META};
const M_LMDS_META   = {LMDS_META};
const M_ZONE_DATA   = {ZONE_DATA};
const M_LMD_DATA    = {LMD_DATA};

/* ══════════════════════════════════════════════════
   MAP ENGINE
══════════════════════════════════════════════════ */
(function() {{
  let mapZ = null, mapL = null;
  let layerZ = null, layerL = null;
  let selZ = null, selL = null;
  let mapsInited = false;

  const RJ = [27.0, 74.2];

  // Precompute counts
  const zCnt = new Array(M_ZONES_META.length).fill(0);
  M_ZONE_DATA.forEach(d => zCnt[d[0]]++);
  const lCnt = new Array(M_LMDS_META.length).fill(0);
  M_LMD_DATA.forEach(d => lCnt[d[0]]++);

  function mkMap(id) {{
    const m = L.map(id, {{
      center: RJ, zoom: 6,
      renderer: L.canvas({{ padding: 0.5 }}),
      preferCanvas: true
    }});
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© OpenStreetMap', maxZoom: 18, opacity: 0.5
    }}).addTo(m);
    return m;
  }}

  function buildLayer(map, data, colors, selIdx) {{
    const grp = L.layerGroup([], {{ renderer: map.options.renderer }});
    const hasFil = selIdx !== null;
    for (let i = 0; i < data.length; i++) {{
      const d = data[i];
      const active = !hasFil || d[0] === selIdx;
      const parts = d[3].split('|');
      L.circleMarker([d[1], d[2]], {{
        radius     : active ? (hasFil ? 5 : 3) : 2,
        fillColor  : active ? colors[d[0]] : '#334155',
        color      : active ? colors[d[0]] : '#1e293b',
        weight     : 0.4,
        fillOpacity: active ? (hasFil ? 0.92 : 0.72) : 0.2,
        opacity    : active ? 0.8 : 0.12
      }}).bindTooltip(
        `<b>${{parts[0]}}</b><br>Pin: ${{parts[1]}} · ${{parts[2]}}`,
        {{ sticky: true, offset: [8,0] }}
      ).addTo(grp);
    }}
    return grp;
  }}

  function renderZ() {{
    if (!mapZ) return;
    if (layerZ) mapZ.removeLayer(layerZ);
    layerZ = buildLayer(mapZ, M_ZONE_DATA, M_ZONE_COLORS, selZ);
    layerZ.addTo(mapZ);
    updateStat();
  }}

  function renderL() {{
    if (!mapL) return;
    if (layerL) mapL.removeLayer(layerL);
    layerL = buildLayer(mapL, M_LMD_DATA, M_LMD_COLORS, selL);
    layerL.addTo(mapL);
    updateStat();
  }}

  function buildSidebar(type) {{
    const meta   = type === 'zone' ? M_ZONES_META : M_LMDS_META;
    const colors = type === 'zone' ? M_ZONE_COLORS : M_LMD_COLORS;
    const counts = type === 'zone' ? zCnt : lCnt;
    const list   = document.getElementById('mlist-' + type);
    let html = '';
    for (let i = 0; i < meta.length; i++) {{
      const item  = meta[i];
      const name  = type === 'zone' ? item.n : item;
      const tier  = type === 'zone' ? item.t : null;
      const cnt   = counts[i] || 0;
      const tierH = tier ? `<span class="m-tier m-${{tier.toLowerCase()}}">${{tier}}</span>` : '';
      html += `<div class="m-opt" data-idx="${{i}}" data-nm="${{name.toLowerCase()}}" onclick="mSelItem('${{type}}',${{i}},this)">
        <span class="m-swatch" style="background:${{colors[i]}}"></span>
        <span class="m-opt-lbl">${{name}}</span>
        ${{tierH}}<span class="m-opt-cnt">${{cnt}}</span></div>`;
    }}
    list.innerHTML = html;
  }}

  function updateStat() {{
    const curTab = document.querySelector('.msub-btn.active')?.id === 'msub-zone' ? 'zone' : 'lmd';
    const sel = curTab === 'zone' ? selZ : selL;
    const data = curTab === 'zone' ? M_ZONE_DATA : M_LMD_DATA;
    const meta = curTab === 'zone' ? M_ZONES_META : M_LMDS_META;
    const cnt = sel === null ? data.length : data.filter(d => d[0] === sel).length;
    const lbl = sel === null ? 'All zones' : (curTab === 'zone' ? meta[sel].n : meta[sel]);
    const unit = curTab === 'zone' ? `${{M_ZONES_META.length}} zones` : `${{M_LMDS_META.length - 1}} LMD partners`;
    document.getElementById('mstat-active').innerHTML =
      `${{sel !== null ? `<b>${{lbl}}</b>:` : ''}} <b>${{cnt.toLocaleString()}}</b> villages · ${{unit}}`;
  }}

  /* public API — called by inline HTML handlers */
  window.mSelItem = function(type, idx, el) {{
    const list = document.getElementById('mlist-' + type);
    list.querySelectorAll('.m-opt').forEach(e => e.classList.remove('sel'));
    const same = type === 'zone' ? selZ === idx : selL === idx;
    if (same) {{
      if (type === 'zone') selZ = null; else selL = null;
    }} else {{
      el.classList.add('sel');
      if (type === 'zone') selZ = idx; else selL = idx;
    }}
    if (type === 'zone') renderZ(); else renderL();
  }};

  window.mClearFilter = function(type) {{
    if (type === 'zone') selZ = null; else selL = null;
    document.querySelectorAll('#mlist-' + type + ' .m-opt').forEach(e => e.classList.remove('sel'));
    if (type === 'zone') renderZ(); else renderL();
  }};

  window.mZoomSel = function(type) {{
    const idx = type === 'zone' ? selZ : selL;
    if (idx === null) return;
    const data = type === 'zone' ? M_ZONE_DATA : M_LMD_DATA;
    const pts = data.filter(d => d[0] === idx).map(d => [d[1], d[2]]);
    if (!pts.length) return;
    (type === 'zone' ? mapZ : mapL).fitBounds(L.latLngBounds(pts), {{ padding: [40,40] }});
  }};

  window.mFilterList = function(type) {{
    const q = document.getElementById('msearch-' + type).value.toLowerCase();
    document.querySelectorAll('#mlist-' + type + ' .m-opt').forEach(el =>
      el.style.display = el.dataset.nm.includes(q) ? '' : 'none');
  }};

  window.switchSubmap = function(type, btn) {{
    document.querySelectorAll('.msub-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.submap-wrap').forEach(w => w.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('submap-' + type).classList.add('active');
    if (type === 'zone' && mapZ) mapZ.invalidateSize();
    if (type === 'lmd'  && mapL) mapL.invalidateSize();
    updateStat();
  }};

  window.initMaps = function() {{
    if (mapsInited) {{ mapZ && mapZ.invalidateSize(); return; }}
    mapsInited = true;
    mapZ = mkMap('mmap-zone');
    buildSidebar('zone');
    renderZ();
  }};

  window.initLmdMap = function() {{
    if (!mapL) {{
      mapL = mkMap('mmap-lmd');
      buildSidebar('lmd');
      renderL();
    }} else {{
      mapL.invalidateSize();
    }}
  }};
}})();
"""

# ── 6. Patch the switchTab function to trigger map init ──────────────────────
# We'll add a hook after the existing switchTab body
SWITCHTAB_PATCH = """
  if (tab === 'map') {
    // defer one frame so the panel is visible before Leaflet measures it
    setTimeout(function() {
      if (typeof initMaps === 'function') initMaps();
    }, 50);
  }
"""

# ── 7. Apply all patches ──────────────────────────────────────────────────────
print("Patching farmer_serviceability_flow.html...")

# a) Add Leaflet CSS inside <style> block (import at very top of styles)
html = html.replace(
    "* { box-sizing: border-box; margin: 0; padding: 0; }",
    "* { box-sizing: border-box; margin: 0; padding: 0; }\n" + MAP_CSS,
    1
)

# b) Add Leaflet JS before </body>
html = html.replace(
    "</body>",
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n</body>',
    1
)

# c) Add the new tab button after the 3rd tab button (audit)
html = html.replace(
    '<button class="tab-btn" onclick="switchTab(\'audit\')">🔍 Zone Integrity Audit</button>',
    '<button class="tab-btn" onclick="switchTab(\'audit\')">🔍 Zone Integrity Audit</button>\n    <button class="tab-btn" onclick="switchTab(\'map\')">🗺 Village Maps</button>',
    1
)

# d) Add the new tab panel — insert right before the closing </div> of tab-audit panel.
#    Find the last </div> before <script> — that's the end of the tab-audit panel.
#    More robustly: insert MAP_HTML right before '<script>'
html = html.replace(
    "\n<script>",
    MAP_HTML + "\n<script>",
    1
)

# e) Patch switchTab to call initMaps
html = html.replace(
    "function switchTab(tab) {\n  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));\n  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));\n  document.getElementById('tab-' + tab).classList.add('active');\n  event.target.classList.add('active');\n}",
    "function switchTab(tab) {\n  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));\n  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));\n  document.getElementById('tab-' + tab).classList.add('active');\n  event.target.classList.add('active');\n" + SWITCHTAB_PATCH + "\n}",
    1
)

# f) Patch switchSubmap to init LMD map on first switch
MAP_JS_PATCHED = MAP_JS.replace(
    "window.switchSubmap = function(type, btn) {",
    "window.switchSubmap = function(type, btn) {\n    if (type === 'lmd') initLmdMap();"
)

# g) Add MAP_JS before </script>
html = html.replace("renderAudit();\n</script>", "renderAudit();\n" + MAP_JS_PATCHED + "\n</script>", 1)

# ── 8. Write ──────────────────────────────────────────────────────────────────
with open(DST, "w", encoding="utf-8") as f:
    f.write(html)

size_mb = os.path.getsize(DST) / 1_048_576
print(f"Done. File size: {size_mb:.1f} MB → {DST}")
