#!/bin/bash
# COCO Alerts — daily sender. Triggered by launchd at login/restart and at
# 10:00 AM (a missed 10 AM fires on lid-open). Runs the checks at most once per
# calendar day, per channel — Slack and Google Chat are dedup'd independently so
# one lagging behind (e.g. a retry) never blocks or re-fires the other.
#
# What gets posted, per channel (user, 2026-11-12):
#   alert today            -> the alert
#   clean day              -> NOTHING (day is still marked done)
#   Friday, no alert there -> weekly recap + fresh encouraging line (slogan.sh)
#
# Two independent sends, either can fail without blocking the other:
#   Slack (Tech)             — other stuck orders (not DISPATCHED) + QR guardrail,
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
WEEKLY_FILE="$DIR/logs/weekly_recap.md"
STATE_FILE_GCHAT="$DIR/logs/.last_sent_date_gchat"
ALERT_FILE_GCHAT="$DIR/logs/latest_alert_gchat.md"
WEEKLY_FILE_GCHAT="$DIR/logs/weekly_recap_gchat.md"
LOG_FILE="$DIR/logs/coco_alerts.log"
CLAUDE_BIN="$HOME/.local/bin/claude"
TODAY="$(date +%Y-%m-%d)"
SLOGAN_TEAM="AgroStar COCO stores team (company-owned agri-input shops; daily automated checks catch stuck orders and QR payments that didn't reach the store ledger)"

mkdir -p "$DIR/logs"
note() { echo "$(date '+%Y-%m-%d %H:%M:%S') send.sh $1" >> "$LOG_FILE"; }
sent_today() { [ -f "$1" ] && [ "$(cat "$1")" = "$TODAY" ]; }
. "$DIR/slogan.sh"

# GCHAT_WEBHOOK_URL is a secret — kept out of the repo, only in the deployed
# copy's config.env (gitignored, see config.env.example for the format).
[ -f "$DIR/config.env" ] && . "$DIR/config.env"

if sent_today "$STATE_FILE" && sent_today "$STATE_FILE_GCHAT"; then
    note "already done today (both channels) — skipping"
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

# Picks what a channel posts today. Prints the file to send, or nothing if the
# day is clean. $1 = alert file, $2 = weekly recap file, $3 = filled-recap path.
pick_message() {
    if [ -s "$1" ]; then
        printf '%s' "$1"
    elif [ -s "$2" ]; then
        fill_slogan "$2" "$3"
        printf '%s' "$3"
    fi
}

# ── Slack (Tech) ─────────────────────────────────────────────────────────────
SLACK_OK=0
if sent_today "$STATE_FILE"; then
    note "slack: already done today — skipping"
else
    SEND_FILE="$(pick_message "$ALERT_FILE" "$WEEKLY_FILE" "$DIR/logs/weekly_recap_filled.md")"
    if [ -z "$SEND_FILE" ]; then
        note "slack: clean day — nothing to post"
        echo "$TODAY" > "$STATE_FILE"
    else
        # Exit code alone is not proof of success: if the Slack MCP tool is
        # unavailable in this headless session (it's tied to an interactive
        # claude.ai login and can drop out under launchd), `claude -p` still
        # exits 0 after just explaining that it couldn't send anything. So we
        # also require the literal "SENT" confirmation line before marking sent.
        CLAUDE_OUTPUT="$("$CLAUDE_BIN" -p "Read the file '$SEND_FILE' and send its contents EXACTLY as-is (do not rephrase, summarize, or add anything) as a Slack message to channel ID $CHANNEL using the slack_send_message tool. Then reply with just: SENT" \
            --allowedTools "Read,mcp__claude_ai_Slack__slack_send_message" \
            2>&1)"
        CLAUDE_EXIT=$?
        echo "$CLAUDE_OUTPUT" >> "$LOG_FILE"

        if [ $CLAUDE_EXIT -eq 0 ] && printf '%s\n' "$CLAUDE_OUTPUT" | grep -qE '^SENT[[:space:]]*$'; then
            note "posted $(basename "$SEND_FILE") to $CHANNEL"
            echo "$TODAY" > "$STATE_FILE"
        else
            note "claude -p post FAILED (exit=$CLAUDE_EXIT, no SENT confirmation) — will retry on next trigger"
            SLACK_OK=1
        fi
    fi
fi

# ── Google Chat (Sales Ops) — CREATED stuck orders ──────────────────────────
GCHAT_OK=0
if sent_today "$STATE_FILE_GCHAT"; then
    note "gchat: already done today — skipping"
elif [ -z "${GCHAT_WEBHOOK_URL:-}" ]; then
    note "GCHAT_WEBHOOK_URL not set — skipping Google Chat send (see coco_alerts/config.env)"
else
    SEND_FILE="$(pick_message "$ALERT_FILE_GCHAT" "$WEEKLY_FILE_GCHAT" "$DIR/logs/weekly_recap_gchat_filled.md")"
    if [ -z "$SEND_FILE" ]; then
        note "gchat: clean day — nothing to post"
        echo "$TODAY" > "$STATE_FILE_GCHAT"
    else
        GCHAT_PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))' < "$SEND_FILE")"
        GCHAT_HTTP_CODE="$(curl -s -o "$DIR/logs/.gchat_resp.json" -w '%{http_code}' \
            -X POST -H 'Content-Type: application/json; charset=UTF-8' \
            -d "$GCHAT_PAYLOAD" "$GCHAT_WEBHOOK_URL")"
        if [ "$GCHAT_HTTP_CODE" = "200" ]; then
            note "posted $(basename "$SEND_FILE") to Google Chat (Sales Ops)"
            echo "$TODAY" > "$STATE_FILE_GCHAT"
        else
            note "Google Chat post FAILED (http=$GCHAT_HTTP_CODE): $(cat "$DIR/logs/.gchat_resp.json" 2>/dev/null) — will retry on next trigger"
            GCHAT_OK=1
        fi
    fi
fi

[ "$SLACK_OK" -eq 0 ] && [ "$GCHAT_OK" -eq 0 ]
