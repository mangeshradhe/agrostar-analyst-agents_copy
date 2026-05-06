#!/bin/bash
# setup-agents.sh
# Run this ONCE to connect Agrostar analyst agents to your Claude Code.
# After this, any updates to the agents sync automatically via Google Drive.

set -e

echo ""
echo "🤖 Agrostar Analyst Agents — Setup"
echo "────────────────────────────────────"

# ─── Step 1: Find Google Drive path ─────────────────────────────────────────
echo ""
echo "🔍 Looking for your Google Drive..."

# Auto-detect Google Drive CloudStorage path
GDRIVE_BASE="$HOME/Library/CloudStorage"
DRIVE_PATH=""

if [ -d "$GDRIVE_BASE" ]; then
  for account_dir in "$GDRIVE_BASE"/GoogleDrive-*; do
    candidate="$account_dir/My Drive/Agrostar Analyst Agents"
    if [ -d "$candidate" ]; then
      DRIVE_PATH="$candidate"
      ACCOUNT=$(basename "$account_dir" | sed 's/GoogleDrive-//')
      echo "   ✅ Found Drive at: $candidate"
      echo "   📧 Account: $ACCOUNT"
      break
    fi
  done
fi

if [ -z "$DRIVE_PATH" ]; then
  echo ""
  echo "   ❌ Could not auto-detect the 'Agrostar Analyst Agents' folder."
  echo ""
  echo "   Make sure you have:"
  echo "   1. Google Drive for Desktop installed and signed in with your Agrostar account"
  echo "      → https://www.google.com/drive/download"
  echo "   2. Opened the shared Google Drive link Darpan sent you"
  echo "   3. Clicked 'Add shortcut to My Drive' on the 'Agrostar Analyst Agents' folder"
  echo "   4. Waited ~60 seconds for it to sync to your computer"
  echo ""
  echo "   Then run this script again."
  exit 1
fi

# ─── Step 2: Create ~/.claude/commands/ if needed ───────────────────────────
echo ""
echo "📁 Setting up Claude commands folder..."
mkdir -p "$HOME/.claude/commands"
echo "   ✅ ~/.claude/commands/ ready"

# ─── Step 3: Create symlinks for all agent files ────────────────────────────
echo ""
echo "🔗 Linking agents..."

COMMANDS_DIR="$DRIVE_PATH/commands"

if [ ! -d "$COMMANDS_DIR" ]; then
  echo "   ❌ No 'commands' subfolder found in the Drive folder. Contact Darpan."
  exit 1
fi

LINKED=0
for file in "$COMMANDS_DIR"/*.md; do
  filename=$(basename "$file")
  target="$HOME/.claude/commands/$filename"
  # Remove old symlink or file if it exists
  [ -L "$target" ] && rm "$target"
  ln -sf "$file" "$target"
  echo "   ✅ Linked: $filename"
  LINKED=$((LINKED + 1))
done

if [ $LINKED -eq 0 ]; then
  echo "   ⚠️  No .md files found in $COMMANDS_DIR"
  exit 1
fi

# ─── Step 4: Verify ─────────────────────────────────────────────────────────
echo ""
echo "✅ Setup complete! $LINKED agent(s) linked."
echo ""
echo "   To use in Claude Code:"
echo "   1. Open Terminal and type: claude"
echo "   2. Type a slash command to activate an agent, e.g.:"

for file in "$COMMANDS_DIR"/*.md; do
  filename=$(basename "$file" .md)
  echo "      /$filename"
done

echo ""
echo "   Updates from the team sync automatically — no action needed."
echo ""
