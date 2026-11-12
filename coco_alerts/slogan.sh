# slogan.sh — sourced by send.sh. Shared by coco_alerts and dvs-alerts
# (identical copy in both folders; each is deployed on its own).
#
# Friday weekly recap ends with a fresh encouraging line. Claude writes a new
# one each week; every line used is kept in logs/slogans_used.tsv and passed
# back as "don't repeat these". Same line is reused for every channel and
# every retry on the same day. If Claude's output is unusable, falls back to
# the first unused line from FALLBACK_SLOGANS below.
#
# Needs from send.sh: DIR, TODAY, CLAUDE_BIN, LOG_FILE, note(), SLOGAN_TEAM.

SLOGAN_FILE="$DIR/logs/slogans_used.tsv"

FALLBACK_SLOGANS=(
    "Quiet weeks don't happen by luck — they happen because of you. Keep it going! 💪"
    "Small fixes, done on time, make big weeks. Thank you, team!"
    "Calm is the result of good work. Same energy next week! 🚀"
    "Every clean day is a farmer served right. Proud of this team."
    "Good systems + good people = calm weeks. Let's keep both strong."
    "No noise is the best news. Keep up the great work! 🌾"
    "Steady hands, steady stores. Have a great weekend, team!"
    "What gets watched gets fixed — and you fixed it all. Well done!"
    "Another week, another step forward. Keep it rolling! 🙌"
    "The best alert is the one we never need to send. Keep it up!"
)

used_slogans() { cut -f2- "$SLOGAN_FILE" 2>/dev/null; }

# $1 = one-line week summary (e.g. "This week: 7 calm days."). Prints the line.
get_slogan() {
    local summary="$1" existing past out line s
    touch "$SLOGAN_FILE"

    existing="$(awk -F'\t' -v d="$TODAY" '$1 == d { print $2; exit }' "$SLOGAN_FILE")"
    if [ -n "$existing" ]; then
        printf '%s' "$existing"
        return
    fi

    past="$(used_slogans | tail -100)"
    [ -z "$past" ] && past="(none yet)"
    out="$("$CLAUDE_BIN" -p "Write ONE short encouraging line (max 20 words) to close this week's Friday check-in message for the $SLOGAN_TEAM. ${summary} Use simple, warm, plain English. It can be a slogan, a thank-you, or a push to keep going. At most one emoji. It must be clearly different in words and idea from every one of these past lines:
$past

Reply with only the line itself — no quotes, no preamble." 2>>"$LOG_FILE")"
    line="$(printf '%s\n' "$out" | sed '/^[[:space:]]*$/d' | tail -1 | tr -d '\t' \
        | sed -E 's/^[[:space:]"“]+//; s/[[:space:]"”]+$//')"

    if [ ${#line} -lt 10 ] || [ ${#line} -gt 220 ] || used_slogans | grep -qxF "$line"; then
        note "slogan: Claude output unusable ('$line') — using fallback list"
        line=""
        for s in "${FALLBACK_SLOGANS[@]}"; do
            if ! used_slogans | grep -qxF "$s"; then line="$s"; break; fi
        done
        [ -z "$line" ] && line="${FALLBACK_SLOGANS[0]}"
    fi

    printf '%s\t%s\n' "$TODAY" "$line" >> "$SLOGAN_FILE"
    note "slogan for $TODAY: $line"
    printf '%s' "$line"
}

# $1 = recap file with {{SLOGAN}}, $2 = output file. Fills in today's slogan.
fill_slogan() {
    local summary slogan
    summary="$(grep -m1 '^This week:' "$1")"
    slogan="$(get_slogan "$summary")"
    SLOGAN="$slogan" /usr/bin/python3 -c '
import os, sys
text = open(sys.argv[1]).read().replace("{{SLOGAN}}", os.environ["SLOGAN"])
open(sys.argv[2], "w").write(text)' "$1" "$2"
}
