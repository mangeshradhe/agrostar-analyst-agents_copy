#!/bin/bash
# DVS DRISHTI — One-command macOS launchd installer
# Run once: bash drishti/launch/install.sh

set -euo pipefail

PLIST_SRC="/Users/darpan/Documents/claude code/DVS Analysis/drishti/launch/com.agrostar.drishti.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.agrostar.drishti.plist"
SCRIPT="/Users/darpan/Documents/claude code/DVS Analysis/drishti/launch/run_drishti.sh"

echo "Installing DVS DRISHTI scheduler..."

# Make run script executable
chmod +x "$SCRIPT"

# Copy plist to LaunchAgents
cp "$PLIST_SRC" "$PLIST_DST"

# Unload if already registered (ignore error if not loaded)
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Load the agent
launchctl load "$PLIST_DST"

echo ""
echo "✅ DVS DRISHTI scheduled successfully."
echo ""
echo "   Schedule:  Daily at 10:00 AM IST"
echo "   Behaviour: If laptop was off at 10 AM, runs on next wake"
echo "   Logs:      drishti/logs/drishti.log"
echo "   Guard:     Won't run twice on the same day"
echo ""
echo "   To run manually right now:"
echo "     bash drishti/launch/run_drishti.sh"
echo ""
echo "   To uninstall:"
echo "     bash drishti/launch/uninstall.sh"
