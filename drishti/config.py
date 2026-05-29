# DVS DRISHTI — Configuration
# Update Slack channel IDs before first run

import os

# BigQuery
PROJECT = "agrostar-data"

# Slack — all output goes to Darpan Pathar DM (U5AR2LLBZ) during pilot
# To route to team channels later, replace D0B7Y65ENRW with real channel IDs
_DM_DARPAN = "D0B7Y65ENRW"  # Darpan Pathar (darpan.pathar@agrostar.in)

CHANNELS = {
    "summary": os.environ.get("DRISHTI_CHANNEL_SUMMARY", _DM_DARPAN),
    "ops":     os.environ.get("DRISHTI_CHANNEL_OPS",     _DM_DARPAN),
    "lmd":     os.environ.get("DRISHTI_CHANNEL_LMD",     _DM_DARPAN),
    "finance": os.environ.get("DRISHTI_CHANNEL_FINANCE", _DM_DARPAN),
    "cc":      os.environ.get("DRISHTI_CHANNEL_CC",      _DM_DARPAN),
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
# PAT-002 validated 2026-05-30: use decline RATE not volume
# formula: priority_score = prev_orders × decline_rate × urgency
#          decline_rate = 1 - (current_orders / prev_orders)
SCORING = {
    "avg_order_value": 850,           # ₹ per DVS order (approximate)
    "urgency_truly_dead": 3,
    "urgency_declining": 2,
    "urgency_starved": 1,
    "use_rate_based_decline": True,   # PAT-002: rate not volume
}

# PAT-001 validated 2026-05-30: seasonal dead persistence prior
# Dead stores reactivate in May-Jun due to Kharif demand
DEAD_PERSISTENCE_BY_MONTH = {
    "may": 0.35, "jun": 0.35,        # Kharif season — stores come back
    "default": 0.55,                  # Rest of year
}

# Prediction settings
PREDICTION = {
    "score_after_days": 7,          # Score predictions filed 7+ days ago
    "dead_persistence_prior": 0.55, # Updated from May 2026 backtesting (was 0.70)
    "max_predictions_per_run": 15,  # Don't file more than this per day
}

# Memory file location
MEMORY_FILE = "drishti/memory.json"
