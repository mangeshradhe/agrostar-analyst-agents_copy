#!/bin/bash
# COCO Alerts — daily sender. Triggered by launchd at login/restart and at
# 9:00 AM (a missed 9 AM fires on lid-open). Sends at most once per calendar
# day, per channel — Slack and Google Chat are dedup'd independently so one
# lagging behind (e.g. a retry) never blocks or re-fires the other.
#
# Two independent sends, either can fail without blocking the other:
#   Slack (Tech)             — DISPATCHED/other stuck orders + QR guardrail,
#                               via the user's own claude.ai Slack connector
#                               (`claude -p`, no bot token involved)
#   Google Chat (Sales Ops)  — CREATED stuck orders, via a plain incoming
#                               webhook (GCHAT_WEBHOOK_URL, see config.env)
#
# Test without touching the team channel/space:
#   COCO_ALERT_CHANNEL=U5AR2LLBZ bash coco_alerts/send.sh   # Slack DM to Darpan

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL="${COCO_ALERT_CHANNEL:-C0AFTP6QASH}"   # #agrostar-pos
STATE_FILE="$DIR/logs/.last_sent_date"
ALERT_FILE="$DIR/logs/latest_alert.md"
STATE_FILE_GCHAT="$DIR/logs/.last_sent_date_gchat"
ALERT_FILE_GCHAT="$DIR/logs/latest_alert_gchat.md"
LOG_FILE="$DIR/logs/coco_alerts.log"
CLAUDE_BIN="$HOME/.local/bin/claude"
TODAY="$(date +%Y-%m-%d)"

mkdir -p "$DIR/logs"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') send.sh $1" >> "$LOG_FILE"; }
sent_today() { [ -f "$1" ] && [ "$(cat "$1")" = "$TODAY" ]; }

# GCHAT_WEBHOOK_URL is a secret — kept out of the repo, only in the deployed
# copy's config.env (gitignored, see config.env.example for the format).
[ -f "$DIR/config.env" ] && . "$DIR/config.env"

if sent_today "$STATE_FILE" && sent_today "$STATE_FILE_GCHAT"; then
    note "already sent today (both channels) — skipping"
    exit 0
fi

if ! /usr/bin/python3 "$DIR/run.py" >> "$LOG_FILE" 2>&1; then
    note "run.py FAILED — will retry on next trigger"
    exit 1
fi

# ── Slack (Tech) ─────────────────────────────────────────────────────────────
SLACK_OK=0
if sent_today "$STATE_FILE"; then
    note "slack: already sent today — skipping"
elif [ ! -s "$ALERT_FILE" ]; then
    note "slack: nothing computed — will retry on next trigger"
    SLACK_OK=1
else
    # Exit code alone is not proof of success: if the Slack MCP tool is
    # unavailable in this headless session (it's tied to an interactive
    # claude.ai login and can drop out under launchd), `claude -p` still
    # exits 0 after just explaining that it couldn't send anything. So we
    # also require the literal "SENT" confirmation line before marking sent.
    CLAUDE_OUTPUT="$("$CLAUDE_BIN" -p "Read the file '$ALERT_FILE' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as a Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT" \
        --allowedTools "Read,mcp__claude_ai_Slack__slack_send_message" \
        2>&1)"
    CLAUDE_EXIT=$?
    echo "$CLAUDE_OUTPUT" >> "$LOG_FILE"

    if [ $CLAUDE_EXIT -eq 0 ] && printf '%s\n' "$CLAUDE_OUTPUT" | grep -qE '^SENT[[:space:]]*$'; then
        note "alert posted to $CHANNEL"
        echo "$TODAY" > "$STATE_FILE"
    else
        note "claude -p post FAILED (exit=$CLAUDE_EXIT, no SENT confirmation) — will retry on next trigger"
        SLACK_OK=1
    fi
fi

# ── Google Chat (Sales Ops) — CREATED stuck orders ──────────────────────────
GCHAT_OK=0
if sent_today "$STATE_FILE_GCHAT"; then
    note "gchat: already sent today — skipping"
elif [ -z "${GCHAT_WEBHOOK_URL:-}" ]; then
    note "GCHAT_WEBHOOK_URL not set — skipping Google Chat send (see coco_alerts/config.env)"
elif [ ! -s "$ALERT_FILE_GCHAT" ]; then
    note "gchat: nothing computed — will retry on next trigger"
    GCHAT_OK=1
else
    GCHAT_PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))' < "$ALERT_FILE_GCHAT")"
    GCHAT_HTTP_CODE="$(curl -s -o "$DIR/logs/.gchat_resp.json" -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json; charset=UTF-8' \
        -d "$GCHAT_PAYLOAD" "$GCHAT_WEBHOOK_URL")"
    if [ "$GCHAT_HTTP_CODE" = "200" ]; then
        note "alert posted to Google Chat (Sales Ops)"
        echo "$TODAY" > "$STATE_FILE_GCHAT"
    else
        note "Google Chat post FAILED (http=$GCHAT_HTTP_CODE): $(cat "$DIR/logs/.gchat_resp.json" 2>/dev/null) — will retry on next trigger"
        GCHAT_OK=1
    fi
fi

[ "$SLACK_OK" -eq 0 ] && [ "$GCHAT_OK" -eq 0 ]
