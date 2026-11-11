#!/bin/bash
# Status Mismatch Alert — daily sender. Triggered by launchd at login/restart and
# at 10:00 AM (a missed 10 AM fires on lid-open). Sends at most once per day.
#
# run.py writes the alert as one or more parts into logs/parts/. Each part is
# posted as its own Slack message through the user's own claude.ai Slack
# connector via `claude -p` — no bot token involved. Parts already posted today
# are remembered in logs/.sent_parts, so a retry after a partial failure only
# posts the missing parts (no double-posting), and BigQuery is not re-run mid-day.
#
# Test without touching the team channel:
#   STATUS_ALERT_CHANNEL=U5AR2LLBZ bash send.sh   # DM to Darpan

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL="${STATUS_ALERT_CHANNEL:-C0CG98Z96J3}"   # #status-mismatches-crm-wms-agroex (private)
PARTS_DIR="$DIR/logs/parts"
STATE_FILE="$DIR/logs/.last_sent_date"
COMPUTED_FILE="$DIR/logs/.computed_date"
SENT_PARTS="$DIR/logs/.sent_parts"
LOG_FILE="$DIR/logs/status_mismatch_alerts.log"
CLAUDE_BIN="$HOME/.local/bin/claude"
TODAY="$(date +%Y-%m-%d)"

mkdir -p "$DIR/logs"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') send.sh $1" >> "$LOG_FILE"; }

# Only the real channel run is deduped by date; a test run (env override) always sends.
if [ -z "${STATUS_ALERT_CHANNEL:-}" ] && [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$TODAY" ]; then
    note "already sent today — skipping"
    exit 0
fi

# Compute once per day. Retry run.py in a FRESH process: a run frozen by laptop
# sleep dies right after wake, and the missed 10 AM trigger is already used up
# by then (incident 2026-11-09 on the DVS/COCO alerts).
if [ ! -f "$COMPUTED_FILE" ] || [ "$(cat "$COMPUTED_FILE")" != "$TODAY" ] || [ -n "${STATUS_ALERT_CHANNEL:-}" ]; then
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
    echo "$TODAY" > "$COMPUTED_FILE"
fi

shopt -s nullglob
PARTS=("$PARTS_DIR"/part_*.md)
if [ ${#PARTS[@]} -eq 0 ]; then
    note "no parts computed — will retry on next trigger"
    exit 1
fi

ALL_OK=0
for PART in "${PARTS[@]}"; do
    KEY="$TODAY $CHANNEL $(basename "$PART")"
    if [ -f "$SENT_PARTS" ] && grep -qxF "$KEY" "$SENT_PARTS"; then
        continue
    fi
    # Exit code alone is not proof of success: if the Slack MCP tool is
    # unavailable in this headless session, `claude -p` still exits 0 after
    # explaining it couldn't send. Require the literal "SENT" line too.
    CLAUDE_OUTPUT="$("$CLAUDE_BIN" -p "Read the file '$PART' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as a single Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT" \
        --allowedTools "Read,mcp__claude_ai_Slack__slack_send_message" \
        2>&1)"
    CLAUDE_EXIT=$?
    echo "$CLAUDE_OUTPUT" >> "$LOG_FILE"

    if [ $CLAUDE_EXIT -eq 0 ] && printf '%s\n' "$CLAUDE_OUTPUT" | grep -qE '^SENT[[:space:]]*$'; then
        note "$(basename "$PART") posted to $CHANNEL"
        echo "$KEY" >> "$SENT_PARTS"
    else
        # Stop here: later parts must not go out ahead of a missing earlier one.
        note "$(basename "$PART") post FAILED (exit=$CLAUDE_EXIT, no SENT confirmation) — will retry on next trigger"
        ALL_OK=1
        break
    fi
done

if [ $ALL_OK -eq 0 ]; then
    note "all ${#PARTS[@]} part(s) posted to $CHANNEL"
    [ -z "${STATUS_ALERT_CHANNEL:-}" ] && echo "$TODAY" > "$STATE_FILE"
fi
exit $ALL_OK
