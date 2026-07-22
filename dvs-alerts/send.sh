#!/bin/bash
# DVS Alerts — daily sender. Triggered by launchd at login/restart and at
# 10:00 AM (a missed 10 AM fires on lid-open). Sends at most once per calendar day.
#
# Posting goes through the user's own claude.ai Slack connector via
# `claude -p` — there is no bot token involved.
#
# Test without touching the team channel:
#   DVS_ALERT_CHANNEL=U5AR2LLBZ bash send.sh   # DM to Darpan

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL="${DVS_ALERT_CHANNEL:-C0A30M01PBK}"   # #dvs-issues-internal
STATE_FILE="$DIR/logs/.last_sent_date"
ALERT_FILE="$DIR/logs/latest_alert.md"
CANVAS_FILE_LEDGER="$DIR/logs/canvas_ledger.md"
CANVAS_FILE_ZONE="$DIR/logs/canvas_zone.md"
CANVAS_FILE_LMD="$DIR/logs/canvas_lmd.md"
LOG_FILE="$DIR/logs/dvs_alerts.log"
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

# run.py always writes exactly ONE combined alert (all three guardrails) to
# ALERT_FILE. Independently, any guardrail can also write its own Canvas file
# when it flags more than 5 distinct stores. Build one prompt that creates
# whichever Canvas files exist, then sends the ONE alert message with their
# links appended — never more than one Slack message per day.
CANVAS_STEPS=""
ALLOWED_TOOLS="Read,mcp__claude_ai_Slack__slack_send_message"
if [ -s "$CANVAS_FILE_LEDGER" ]; then
    CANVAS_STEPS="$CANVAS_STEPS Read '$CANVAS_FILE_LEDGER' and create a Slack canvas in channel ID $CHANNEL titled 'DVS Ledger Gaps' using the slack_create_canvas tool with that content, exactly as-is."
    ALLOWED_TOOLS="$ALLOWED_TOOLS,mcp__claude_ai_Slack__slack_create_canvas"
fi
if [ -s "$CANVAS_FILE_ZONE" ]; then
    CANVAS_STEPS="$CANVAS_STEPS Read '$CANVAS_FILE_ZONE' and create a Slack canvas in channel ID $CHANNEL titled 'DVS Zone Mismatches' using the slack_create_canvas tool with that content, exactly as-is."
    ALLOWED_TOOLS="$ALLOWED_TOOLS,mcp__claude_ai_Slack__slack_create_canvas"
fi
if [ -s "$CANVAS_FILE_LMD" ]; then
    CANVAS_STEPS="$CANVAS_STEPS Read '$CANVAS_FILE_LMD' and create a Slack canvas in channel ID $CHANNEL titled 'DVS LMD Partner Mismatches' using the slack_create_canvas tool with that content, exactly as-is."
    ALLOWED_TOOLS="$ALLOWED_TOOLS,mcp__claude_ai_Slack__slack_create_canvas"
fi

if [ -n "$CANVAS_STEPS" ]; then
    PROMPT="$CANVAS_STEPS Then read '$ALERT_FILE', append one line per canvas you just created with its link (e.g. 'Full ledger detail: <link>' / 'Full zone-mismatch detail: <link>' / 'Full LMD-mismatch detail: <link>'), and send the combined text as A SINGLE Slack message to channel ID $CHANNEL using the slack_send_message tool — send only ONE message total, do not rephrase, summarize, or otherwise change the alert text. Then reply with just: SENT"
else
    PROMPT="Read the file '$ALERT_FILE' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as A SINGLE Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT"
fi

CLAUDE_OUTPUT="$("$CLAUDE_BIN" -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" 2>&1)"
CLAUDE_EXIT=$?
echo "$CLAUDE_OUTPUT" >> "$LOG_FILE"

# Exit code alone is not proof of success (see coco_alerts precedent) — also
# require the literal "SENT" confirmation line before marking it sent.
if [ $CLAUDE_EXIT -eq 0 ] && printf '%s\n' "$CLAUDE_OUTPUT" | grep -qE '^SENT[[:space:]]*$'; then
    note "alert posted to $CHANNEL"
    echo "$TODAY" > "$STATE_FILE"
    exit 0
else
    note "claude -p post FAILED (exit=$CLAUDE_EXIT, no SENT confirmation) — will retry on next trigger"
    exit 1
fi
