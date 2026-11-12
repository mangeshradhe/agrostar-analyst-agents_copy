"""
Quiet-days + Friday weekly recap — shared by coco_alerts and dvs-alerts.
(Identical copy lives in both folders; each is deployed on its own.)

Rule (user, 2026-11-12):
  - Alert day  -> post the alert.
  - Clean day  -> post nothing.
  - Friday     -> if that channel has NO alert today, post a weekly recap so the
                  team knows the schedule is still running. If Friday has an
                  alert, post the alert only (no recap).
The recap ends with a {{SLOGAN}} placeholder; send.sh fills it with a fresh
encouraging line from Claude (never repeated — see slogan.sh).

History: one JSON line per real daily run (run_date = local date of the run),
    {"run_date": "2026-11-12", "data_day": "2026-11-11",
     "channels": {"slack": {"alert": false, "note": ""}, ...}}
Re-runs on the same day replace that day's row.
"""

import json
import os
from datetime import date, timedelta

SLOGAN_PLACEHOLDER = "{{SLOGAN}}"
RECAP_WEEKDAY = 4  # Friday
KEEP_DAYS = 60


def load_history(path: str) -> list:
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def save_run(path: str, run_date: date, data_day: str, channels: dict) -> None:
    """channels = {"slack": {"alert": bool, "note": str}, ...}"""
    rows = [r for r in load_history(path) if r["run_date"] != run_date.isoformat()]
    rows.append({"run_date": run_date.isoformat(), "data_day": data_day, "channels": channels})
    rows = sorted(rows, key=lambda r: r["run_date"])[-KEEP_DAYS:]
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def is_recap_day(d: date) -> bool:
    return d.weekday() == RECAP_WEEKDAY


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def format_weekly(title: str, channel: str, history: list, today: date, gchat: bool = False) -> str:
    """Recap of the last 7 run days (today included) for one channel.
    Slack text is standard markdown (**bold**, :shortcodes:) — the claude.ai
    Slack tool converts it. Google Chat uses *bold* and unicode emoji."""
    b = "*" if gchat else "**"
    cal, ok, warn, seed = ("📅", "✅", "⚠️", "🌱") if gchat else (
        ":calendar:", ":white_check_mark:", ":warning:", ":seedling:")

    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_date = {r["run_date"]: r for r in history}
    calm, missed, alerts = 0, 0, []
    for d in days:
        row = by_date.get(d.isoformat())
        ch = (row or {}).get("channels", {}).get(channel)
        if ch is None:
            missed += 1
        elif ch.get("alert"):
            alerts.append((d, ch.get("note", "")))
        else:
            calm += 1

    start, end = days[0], days[-1]
    rng = (f"{start:%d}–{end:%d %b %Y}" if start.month == end.month
           else f"{start:%d %b}–{end:%d %b %Y}")

    week_parts = [_plural(calm, "calm day")]
    if alerts:
        week_parts.append(_plural(len(alerts), "day") + " with alerts")
    if missed:
        week_parts.append(_plural(missed, "day") + " the check didn't run")

    lines = [
        f"{cal} {b}{title} — Weekly check-in ({rng}){b}",
        f"{ok} All clear today. The checks run every day — we post here only when "
        f"something needs fixing, plus this note every Friday.",
        "",
        "This week: " + " · ".join(week_parts) + ".",
    ]
    if alerts:
        for d, note in alerts:
            lines.append(f"{warn} {d:%a %d %b} — {note}")
    elif not missed:
        lines.append("Every single day was calm. 🎉")
    lines += ["", f"{seed} {SLOGAN_PLACEHOLDER}"]
    return "\n".join(lines)


def write_or_clear(path: str, text: str) -> None:
    """Write text, or leave an EMPTY file (= nothing to post) when text is ''."""
    with open(path, "w") as f:
        f.write(text)
