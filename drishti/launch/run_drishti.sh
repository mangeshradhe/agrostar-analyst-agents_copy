#!/bin/bash
# DVS DRISHTI — Daily runner with "run-once-per-day" guard
#
# Behaviour:
#   - Laptop on at 10 AM       → runs at exactly 10 AM
#   - Laptop wakes after 10 AM → runs immediately on wake (launchd fires missed job)
#   - Already ran today        → skips (guard prevents double-run)

set -euo pipefail

REPO="/Users/darpan/Documents/claude code/DVS Analysis"
LOCK_FILE="$REPO/drishti/logs/.last_run_date"
LOG_FILE="$REPO/drishti/logs/drishti.log"
TODAY=$(date +%Y-%m-%d)
NOW=$(date +"%Y-%m-%d %H:%M:%S")

# ── Guard: skip if already ran today ────────────────────────────────────────
if [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE")" = "$TODAY" ]; then
    echo "[$NOW] DRISHTI already ran today ($TODAY). Skipping." >> "$LOG_FILE"
    exit 0
fi

# ── Guard: only run at 10 AM or later ───────────────────────────────────────
CURRENT_HOUR=$(date +%H | sed 's/^0//')
if [ "$CURRENT_HOUR" -lt 10 ]; then
    echo "[$NOW] Before 10 AM (hour=$CURRENT_HOUR). Skipping — will run at scheduled time." >> "$LOG_FILE"
    exit 0
fi

# ── Run DRISHTI ──────────────────────────────────────────────────────────────
echo "[$NOW] Starting DVS DRISHTI run..." >> "$LOG_FILE"

# Load environment (token available via shell profile)
source "$HOME/.zshrc" 2>/dev/null || source "$HOME/.bash_profile" 2>/dev/null || true

# Change to repo directory and run
cd "$REPO"
/usr/bin/python3 drishti/run.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # Mark today as done
    echo "$TODAY" > "$LOCK_FILE"
    echo "[$NOW] DRISHTI run completed successfully." >> "$LOG_FILE"
else
    echo "[$NOW] DRISHTI run FAILED (exit code $EXIT_CODE). Will retry on next wake." >> "$LOG_FILE"
    # Don't write lock file — allow retry on next wake
fi

exit $EXIT_CODE
