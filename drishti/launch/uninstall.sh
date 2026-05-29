#!/bin/bash
# DVS DRISHTI — Uninstaller

PLIST_DST="$HOME/Library/LaunchAgents/com.agrostar.drishti.plist"

echo "Uninstalling DVS DRISHTI scheduler..."
launchctl unload "$PLIST_DST" 2>/dev/null && rm -f "$PLIST_DST"
echo "✅ Uninstalled."
