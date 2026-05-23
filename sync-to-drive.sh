#!/bin/bash
# sync-to-drive.sh
# Pushes latest analyst files to GitHub AND syncs to Google Drive.
# Run this after every merge to main: ./sync-to-drive.sh

set -e

# ─── Detect Google Drive path ───────────────────────────────────────────────
GDRIVE_ACCOUNT="Darpan.pathar@agrostar.in"

POSSIBLE_PATHS=(
  "$HOME/Library/CloudStorage/GoogleDrive-${GDRIVE_ACCOUNT}/My Drive/agrostar-analyst-agents"
  "$HOME/Library/CloudStorage/GoogleDrive-${GDRIVE_ACCOUNT}/My Drive/Agrostar Analyst Agents"
  "$HOME/Google Drive/My Drive/agrostar-analyst-agents"
  "$HOME/Google Drive/My Drive/Agrostar Analyst Agents"
  "$HOME/Google Drive/Agrostar Analyst Agents"
)

DRIVE_PATH=""
for path in "${POSSIBLE_PATHS[@]}"; do
  if [ -d "$path" ]; then
    DRIVE_PATH="$path"
    break
  fi
done

if [ -z "$DRIVE_PATH" ]; then
  echo "❌ Google Drive folder not found. Make sure:"
  echo "   1. Google Drive for Desktop is installed: https://www.google.com/drive/download"
  echo "   2. You are signed in with $GDRIVE_ACCOUNT"
  echo "   3. The 'Agrostar Analyst Agents' folder exists in My Drive"
  exit 1
fi

# ─── Git push to GitHub ──────────────────────────────────────────────────────
echo "🔄 Pushing to GitHub..."
git push origin main
echo "✅ GitHub up to date"

# ─── Sync files to Google Drive ─────────────────────────────────────────────
echo "🔄 Syncing to Google Drive..."

cp CLAUDE.md "$DRIVE_PATH/CLAUDE.md"
echo "   ✅ CLAUDE.md"

mkdir -p "$DRIVE_PATH/commands"

for file in .claude/commands/*.md; do
  filename=$(basename "$file")
  cp "$file" "$DRIVE_PATH/commands/$filename"
  echo "   ✅ commands/$filename"
done

# ─── Also sync setup scripts so team can run directly from Drive ─────────────
cp setup-agents.sh "$DRIVE_PATH/setup-agents.sh"
chmod +x "$DRIVE_PATH/setup-agents.sh"
echo "   ✅ setup-agents.sh"

cp setup-agents.ps1 "$DRIVE_PATH/setup-agents.ps1"
echo "   ✅ setup-agents.ps1"

echo ""
echo "✅ All done! GitHub + Google Drive are in sync."
echo "   Drive path: $DRIVE_PATH"
