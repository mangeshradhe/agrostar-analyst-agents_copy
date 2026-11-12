#!/bin/bash
# DVS Alerts — daily sender. Triggered by launchd at login/restart and at
# 10:00 AM (a missed 10 AM fires on lid-open). Sends at most once per calendar day.
#
# What gets posted (user, 2026-11-12):
#   alert today          -> the alert
#   clean day            -> NOTHING (day is still marked done)
#   Friday, no alert     -> weekly recap + fresh encouraging line (slogan.sh)
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
WEEKLY_FILE="$DIR/logs/weekly_recap.md"
WEEKLY_FILLED="$DIR/logs/weekly_recap_filled.md"
LOG_FILE="$DIR/logs/dvs_alerts.log"
CLAUDE_BIN="$HOME/.local/bin/claude"
TODAY="$(date +%Y-%m-%d)"
SLOGAN_TEAM="AgroStar DVS team (village Saathi stores that fulfil farmer orders; daily automated checks catch orders whose store ledger entries didn't post after reconciliation)"

mkdir -p "$DIR/logs"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') send.sh $1" >> "$LOG_FILE"; }
. "$DIR/slogan.sh"

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$TODAY" ]; then
    note "already sent today — skipping"
    exit 0
fi

# Retry run.py in a FRESH process: a run frozen by laptop sleep dies right
# after wake, and the missed 10 AM trigger is already used up by then — so
# without this, nothing re-runs until the next day (incident 2026-11-09).
RUN_OK=1
for ATTEMPT in 1 2 3; do
    if /usr/bin/python3 "$DIR/run.py" >> "$LOG_FILE" 2>&1; then
        RUN_OK=0; break
    fi
    note "run.py attempt $ATTEMPT FAILED"
    [ $ATTEMPT -lt 3 ] && sleep 60
done
if [ $RUN_OK -ne 0 ]; then
    note "run.py FAILED after 3 attempts — will retry on next trigger"
    exit 1
fi

if [ -s "$ALERT_FILE" ]; then
    # run.py writes exactly ONE alert (ledger reconciliation only — guardrails
    # 2/3 and the Slack Canvas overflow were removed 2026-10-31) to ALERT_FILE.
    SEND_FILE="$ALERT_FILE"
elif [ -s "$WEEKLY_FILE" ]; then
    fill_slogan "$WEEKLY_FILE" "$WEEKLY_FILLED"
    SEND_FILE="$WEEKLY_FILLED"
else
    note "clean day — nothing to post"
    echo "$TODAY" > "$STATE_FILE"
    exit 0
fi

ALLOWED_TOOLS="Read,mcp__claude_ai_Slack__slack_send_message"
PROMPT="Read the file '$SEND_FILE' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as A SINGLE Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT"

CLAUDE_OUTPUT="$("$CLAUDE_BIN" -p "$PROMPT" --allowedTools "$ALLOWED_TOOLS" 2>&1)"
CLAUDE_EXIT=$?
echo "$CLAUDE_OUTPUT" >> "$LOG_FILE"

# Exit code alone is not proof of success (see coco_alerts precedent) — also
# require the literal "SENT" confirmation line before marking it sent.
if [ $CLAUDE_EXIT -eq 0 ] && printf '%s\n' "$CLAUDE_OUTPUT" | grep -qE '^SENT[[:space:]]*$'; then
    note "posted $(basename "$SEND_FILE") to $CHANNEL"
    echo "$TODAY" > "$STATE_FILE"
    exit 0
else
    note "claude -p post FAILED (exit=$CLAUDE_EXIT, no SENT confirmation) — will retry on next trigger"
    exit 1
fi
