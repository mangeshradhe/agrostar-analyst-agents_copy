# DVS DRISHTI — Schedule Configuration

## Daily Run: 9:00 AM IST

DRISHTI runs automatically every morning at 9:00 AM IST (3:30 AM UTC).

### What it does in each run:
1. Scores predictions filed 7+ days ago vs actuals (learning loop)
2. Runs 4 analysis modules: health scan, live pipeline, dead store classifier, LMD profiler
3. Files new predictions to memory.json with confidence + evidence
4. Posts routed Slack messages to 4 channels
5. Saves updated memory.json

### Slack Channels:
- #dvs-drishti → Daily summary (all teams)
- #dvs-ops → Store actions (TRULY_DEAD visits, STARVED routing config)
- #dvs-finance → OCP/MPD payment needed
- #dvs-lmd → LMD deprioritization escalations

### Manual Run:
```
cd "/Users/darpan/Documents/claude code/DVS Analysis"
python drishti/run.py
```

### Environment Variables Required:
- `SLACK_BOT_TOKEN` — Configured in ~/.claude/settings.json
- `GOOGLE_APPLICATION_CREDENTIALS` — GCP service account key path

### Cron Expression (9 AM IST = 3:30 AM UTC):
```
30 3 * * * cd "/path/to/dvs_analysis" && python drishti/run.py >> drishti/logs/drishti.log 2>&1
```

### Memory File:
`drishti/memory.json` — Updated after every run. Contains:
- Filed predictions (with PENDING/CORRECT/WRONG outcomes)
- Rule accuracy scores
- Regime boundaries
- Known LMD profiles
- Seasonal calendar

### Updating Baselines:
After each monthly review, update BASELINES in `drishti/config.py`
and update the `"updated_date"` in `memory.json` baselines section.

### Prediction Scoring:
Predictions filed today will be auto-scored in 7 days.
Overall accuracy tracked in `memory.json` rules.prediction_accuracy_overall.
Current accuracy (May 2026 backtesting): 60% (3/5 predictions correct).
