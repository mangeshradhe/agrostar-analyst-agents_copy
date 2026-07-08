#!/bin/bash
# COCO Alerts — daily sender. Triggered by launchd at login/restart and at
# 9:00 AM (a missed 9 AM fires on lid-open). Sends at most once per calendar day.
#
# Posting goes through the user's own claude.ai Slack connector via
# `claude -p` — there is no bot token involved.
#
# Test without touching the team channel:
#   COCO_ALERT_CHANNEL=U5AR2LLBZ bash coco_alerts/send.sh   # DM to Darpan

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL="${COCO_ALERT_CHANNEL:-C0AFTP6QASH}"   # #agrostar-pos
STATE_FILE="$DIR/logs/.last_sent_date"
ALERT_FILE="$DIR/logs/latest_alert.md"
LOG_FILE="$DIR/logs/coco_alerts.log"
CLAUDE_BIN="$HOME/.local/bin/claude"
TODAY="$(date +%Y-%m-%d)"

mkdir -p "$DIR/logs"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') send.sh $1" >> "$LOG_FILE"; }

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$TODAY" ]; then
    note "already sent today — skipping"
    exit 0
fi

if ! /usr/bin/python3 "$DIR/run.py" >> "$LOG_FILE" 2>&1; then
    note "run.py FAILED — will retry on next trigger"
    exit 1
fi

if [ ! -s "$ALERT_FILE" ]; then
    note "clean day — nothing to send"
    echo "$TODAY" > "$STATE_FILE"
    exit 0
fi

"$CLAUDE_BIN" -p "Read the file '$ALERT_FILE' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as a Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT" \
    --allowedTools "Read,mcp__claude_ai_Slack__slack_send_message" \
    >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    note "alert posted to $CHANNEL"
    echo "$TODAY" > "$STATE_FILE"
    exit 0
else
    note "claude -p post FAILED — will retry on next trigger"
    exit 1
fi
