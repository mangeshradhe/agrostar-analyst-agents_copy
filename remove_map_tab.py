"""Remove the Village Maps tab and all associated code from farmer_serviceability_flow.html."""
import re, os

PATH = "/Users/darpan/Documents/claude code/DVS Analysis/farmer_serviceability_flow.html"

with open(PATH, encoding="utf-8") as f:
    html = f.read()

before = len(html)

# 1. Remove Leaflet link + script from <head>
html = re.sub(r'<link rel="stylesheet" href="https://unpkg\.com/leaflet[^"]+"/>\n', '', html)
html = re.sub(r'<script src="https://unpkg\.com/leaflet[^"]+"></script>\n?', '', html)

# 2. Remove MAP CSS block (from the comment through all map styles up to the next original comment)
html = re.sub(
    r'\n  /\* ── MAP TAB ── \*/.*?(?=\n  /\* ── VILLAGE UPDATE ── \*/)',
    '', html, flags=re.DOTALL
)

# 3. Remove the tab button
html = html.replace(
    '\n    <button class="tab-btn" onclick="switchTab(\'map\')">🗺 Village Maps</button>',
    ''
)

# 4. Remove the entire tab-map panel (from <div id="tab-map" to its closing </div>\n)
html = re.sub(
    r'\n<div id="tab-map".*?</div>\n(?=\n<script)',
    '', html, flags=re.DOTALL
)

# 5. Remove the map init hook inside switchTab
html = re.sub(
    r'\n  if \(tab === \'map\'\) \{\n    requestAnimationFrame[^}]+\}\n  \}',
    '', html
)

# 6. Remove VILLAGE MAP DATA block + IIFE (from comment through closing })();)
html = re.sub(
    r'\n/\* ══+\n   VILLAGE MAP DATA.*?\}\)\(\);\n',
    '', html, flags=re.DOTALL
)

after = len(html)
print(f"Removed {(before - after) / 1_048_576:.1f} MB of map code")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(html)

size_mb = os.path.getsize(PATH) / 1_048_576
print(f"Done. File size now: {size_mb:.1f} MB")
