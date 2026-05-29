# DVS DRISHTI — Configuration
# Update Slack channel IDs before first run

import os

# BigQuery
PROJECT = "agrostar-data"

# Slack channel IDs (update these with real channel IDs)
CHANNELS = {
    "summary":  os.environ.get("DRISHTI_CHANNEL_SUMMARY", "#dvs-drishti"),
    "ops":      os.environ.get("DRISHTI_CHANNEL_OPS", "#dvs-ops"),
    "lmd":      os.environ.get("DRISHTI_CHANNEL_LMD", "#dvs-lmd"),
    "finance":  os.environ.get("DRISHTI_CHANNEL_FINANCE", "#dvs-finance"),
    "cc":       os.environ.get("DRISHTI_CHANNEL_CC", "#dvs-cc"),
}

# SLA thresholds (hours)
SLA = {
    "dvs_first_action": 1,       # Order → PACKED/ON_HOLD target
    "dvs_packed_warning": 24,    # PACKED → warning (LMD overdue)
    "dvs_packed_critical": 48,   # PACKED → critical (3-day SLA impossible)
    "dvs_picked_warning": 24,    # PICKED_BY_LMD → warning
    "dvs_picked_critical": 48,   # PICKED_BY_LMD → critical
    "dvs_end_to_end": 72,        # Total DVS target (3 days)
    "fc_dispatch_warning": 72,   # Since dispatch → warning
    "fc_dispatch_critical": 120, # Since dispatch → 5-day SLA breached
}

# Baselines (updated from April 2026 analysis, update monthly)
BASELINES = {
    "routing_rate_pct": 48.5,
    "fulfillment_rate_pct": 43.7,
    "rto_rate_pct": 24.1,
    "dvs_3day_sla_pct": 38.4,   # PACKED → DELIVERED within 3 days
    "fc_5day_sla_pct": 28.7,    # DISPATCH → DELIVERED within 5 days
    "packed_to_picked_p50_hrs": 51,   # Current bottleneck
    "stage2_target_hrs": 24,          # Where we need to get to
}

# Alert thresholds (flag if metric drops this many pp below baseline)
ALERT_THRESHOLDS = {
    "fulfillment_rate_drop_pp": 3,
    "routing_rate_drop_pp": 3,
    "rto_spike_pp": 3,
    "lmd_selective_fc_pct": 80,    # FC delivery above this = selective if DVS HOLD also high
    "lmd_selective_hold_pct": 40,  # DVS HOLD above this = selective (given FC is high)
    "lmd_overload_fc_pct": 70,     # FC delivery below this = overloaded
    "lmd_min_packages": 10,        # Minimum packages to flag an LMD
    "store_min_prev_orders": 3,    # Minimum prev orders to flag as dead
}

# Leaky bucket scoring weights
SCORING = {
    "avg_order_value": 850,         # ₹ per DVS order (approximate)
    "urgency_truly_dead": 3,
    "urgency_declining": 2,
    "urgency_starved": 1,
}

# Prediction settings
PREDICTION = {
    "score_after_days": 7,          # Score predictions filed 7+ days ago
    "dead_persistence_prior": 0.55, # Updated from May 2026 backtesting (was 0.70)
    "max_predictions_per_run": 15,  # Don't file more than this per day
}

# Memory file location
MEMORY_FILE = "drishti/memory.json"
