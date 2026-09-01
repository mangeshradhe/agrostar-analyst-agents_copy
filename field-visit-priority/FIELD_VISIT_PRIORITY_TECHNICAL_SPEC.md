# Field Visit Priority — Technical Specification
**For:** Engineering / Data Engineering
**Purpose:** Reverse pipeline to production app — daily recommendation engine for SM/TM field visits
**Status:** Model validated on real BigQuery data (2026-08-27/28)

---

## 1. What This Builds

A daily-refresh BigQuery query that outputs, per SM and TM:
- **4 visit recommendations** — geo-clustered, priority-ranked
- **2 call recommendations** — score-ranked, geography-independent

The app reads this output table and surfaces it to each rep's daily dashboard.

---

## 2. Data Sources

| Signal | Table | Join Key | Notes |
|---|---|---|---|
| Partner master | `offline_team.okr_data_live` | `farmer_id` | Filter `status='ACTIVE'` |
| Coordinates / credit | `replica_galaxy_views.institution` | `reference_customer_id = farmer_id` | Filter `archive=FALSE AND status='ACTIVE' AND business_type IN ('Proprietorship','Partnership')` + `QUALIFY ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) = 1` |
| OCP / overdue | `prod_db_views.wallet_creditwallettransaction` | `wallet_user_id → csr_farmer.user_id → farmer_id` | `transaction_type=0, cancelled=0, reason_id NOT IN (2)`, netted against `wallet_creditwallettransactionreconciliation` |
| Payment behaviour | `prod_db_views.wallet_creditwallettransaction` | Same as above | `transaction_type=1, cancelled=0` |
| B2B revenue / orders | `optimized_reports_data.sale_return_b2c_b2b` | `SAFE_CAST(owner_id AS INT64) = farmer_id` | `Channel_Name='B2B', unicommerce_status NOT LIKE '%RETURN%'` |
| Visit history | `offline_team.store_visits_v2` | `SAFE_CAST(store_id AS INT64) = farmer_id` | Always `UPPER(TRIM(email))` on both sides |
| AOP targets | `optimized_reports_data.aop_offline_online_fy27` | `UPPER(TRIM(revised_territory)) = partner.territory` | Multiply all gross_* columns by 100,000 (stored in lacs) |

---

## 3. Suppression Rules (applied before scoring)

| Role | Window | Rule |
|---|---|---|
| SM | 15 days | `days_since_visit > 15` — strict greater-than |
| TM | 45 days | `days_since_visit > 45` — strict greater-than |

Partners visited within these windows are excluded from all pools for that day.

---

## 4. Signal Architecture

### 4.1 Four Bucket Scores (each 0–100 via PERCENT_RANK fleet-wide)

#### Collection Bucket (weight: 45%)
Sub-signals blended — **only active when OCP > ₹5,000:**

| Sub-signal | Raw value | Weight within bucket |
|---|---|---|
| M — Monetary | OCP overdue amount (netted, due_date < today) | 40% |
| DPD — Age | Max DPD days | 25% |
| F — Payment Frequency | Distinct months with ≥1 payment in last 6 months | 20% |
| R — Payment Recency | Days since last payment received | 15% |

```sql
IF(ocp <= 5000, 0,
   0.40 * PERCENT_RANK() OVER (ORDER BY ocp ASC) * 100
 + 0.25 * PERCENT_RANK() OVER (ORDER BY max_dpd ASC) * 100
 + 0.20 * PERCENT_RANK() OVER (ORDER BY pay_months ASC) * 100
 + 0.15 * PERCENT_RANK() OVER (ORDER BY days_last_pay ASC) * 100
) AS collection_pctile
```

#### Revenue Bucket (weight: 25%)
Sub-signals blended — **zeroed out when OCP > ₹5,000 (credit blocked):**

| Sub-signal | Raw value | Weight within bucket |
|---|---|---|
| M — YoY Degrowth | (rev Apr–Aug 2025 − rev Apr–Aug 2026) / rev Apr–Aug 2025, floored at 0 | 40% |
| F — Order Frequency | Count of months Apr–Aug 2026 where order count < prior month | 30% |
| R — Order Recency | Days since last B2B order | 20% |
| Credit — Headroom | Available credit limit (SAFE_CAST(totalCreditLimit AS FLOAT64)) | 10% |

```sql
IF(ocp > 5000, 0,
   0.40 * PERCENT_RANK() OVER (ORDER BY yoy_decline ASC) * 100
 + 0.30 * PERCENT_RANK() OVER (ORDER BY ord_months ASC) * 100
 + 0.20 * PERCENT_RANK() OVER (ORDER BY days_since_ord ASC) * 100
 + 0.10 * PERCENT_RANK() OVER (ORDER BY credit_limit ASC) * 100
) AS revenue_pctile
```

#### Relationship Bucket (weight: 15%)
```sql
PERCENT_RANK() OVER (ORDER BY GREATEST(days_since_visit - 15, 0) ASC) * 100
  AS relationship_pctile
-- SM cadence = 15 days, TM cadence = 45 days (use 45 in GREATEST for TM rows)
```

#### Targets Bucket (weight: 15%)
Territory AOP shortfall × partner category revenue mix:
```sql
-- shortfall_pct = MAX(1 - mtd_actual / month_target, 0) per territory
-- target_exposure = SUM(shortfall_pct × partner_category_revenue) over last 180d
PERCENT_RANK() OVER (ORDER BY target_exposure ASC) * 100 AS target_pctile
```

### 4.2 Final Active Score
```sql
ROUND(
  (45 * collection_pctile + 25 * revenue_pctile
 + 15 * relationship_pctile + 15 * target_pctile) / 100
, 2) AS active_score
```

### 4.3 Primary Reason (highest raw percentile wins — NOT weighted)
The reason shown to the rep is driven by whichever of the 4 bucket percentiles is highest for that specific partner. **Do not use weighted contribution for reason selection** — this was tested and caused Collection to dominate 71% of reasons purely from weight, even when another signal was more extreme for the partner.

```sql
CASE
  WHEN collection_pctile >= revenue_pctile
    AND collection_pctile >= relationship_pctile
    AND collection_pctile >= target_pctile
    THEN CONCAT('₹', CAST(ROUND(ocp) AS STRING), ' overdue — ', CAST(max_dpd AS STRING), ' DPD')
  WHEN revenue_pctile >= relationship_pctile AND revenue_pctile >= target_pctile
    THEN CONCAT('Revenue down ', CAST(ROUND(yoy_decline*100) AS STRING), '% YoY — worth checking in')
  WHEN relationship_pctile >= target_pctile
    THEN IF(days_since_visit = 9999, 'Never visited — No recent orders.',
            CONCAT('Not visited in ', CAST(days_since_visit AS STRING), ' days'))
  ELSE 'Territory behind on monthly target'
END AS primary_reason
```

---

## 5. Tags (displayed on recommendation card)

### Collection Tag
```sql
CASE
  WHEN PERCENT_RANK() OVER (ORDER BY collection_pctile) >= 0.667 THEN 'High'
  WHEN PERCENT_RANK() OVER (ORDER BY collection_pctile) >= 0.333 THEN 'Medium'
  ELSE 'Low'
END AS collection_tag
```

### Sales Tag
```sql
CASE
  WHEN ocp > 5000 THEN 'Blocked'   -- credit blocked, no sales possible
  WHEN PERCENT_RANK() OVER (ORDER BY revenue_pctile) >= 0.667 THEN 'High'
  WHEN PERCENT_RANK() OVER (ORDER BY revenue_pctile) >= 0.333 THEN 'Medium'
  ELSE 'Low'
END AS sales_tag
```

Both tags always shown on the card. Visual state (filled = High/Medium, outlined = Low, greyed = Blocked) communicates urgency without hiding the signal.

---

## 6. EV Routing (Visit vs Call pool split)

Applied before geo-clustering. Separates partners into two pools:

```sql
CASE
  WHEN ocp > 5000                          THEN 'VISIT'  -- meaningful overdue, always physical
  WHEN ord_months = 0                      THEN 'VISIT'  -- dormant, needs reactivation
  WHEN visit_proximate_order_rate > 0      THEN 'VISIT'  -- only orders when visited, visit-dependent
  -- CALL: all three conditions must hold
  -- 1. collection_tag = 'Low' (clean OCP)
  -- 2. sales_tag IN ('Medium','High') (healthy revenue behaviour)
  -- 3. visit_proximate_order_rate = 0 (orders independently of visits)
  WHEN collection_tag = 'Low'
    AND sales_tag IN ('Medium','High')
    AND visit_proximate_order_rate = 0     THEN 'CALL'   -- healthy, self-sufficient, stay connected
  ELSE                                          'VISIT'  -- anything else defaults to visit
END AS ev_channel
```

**Call is not a fallback — it is a positive classification.** A partner must prove healthy collection, healthy sales, and visit-independent behaviour to qualify for a call. All other partners default to VISIT.

**VISIT pool** → enters geo-clustering
**CALL pool** → top 2 by active_score, geography-independent

---

## 7. Geo-Clustering (VISIT pool only)

### Geo Key Assignment
```sql
CASE
  WHEN lat IS NOT NULL AND lat != 0 AND lng IS NOT NULL AND lng != 0
    THEN ST_GEOHASH(ST_GEOGPOINT(lng, lat), 4)          -- ~39km cell
  WHEN addr_pincode IS NOT NULL AND addr_pincode != ''
    THEN CONCAT('ADDR_',
           LOWER(TRIM(addr_state)), '_',
           LOWER(TRIM(addr_district)), '_',
           LOWER(TRIM(addr_pincode)))                    -- address fallback
  ELSE CONCAT('DIST_', LOWER(TRIM(addr_state)), '_', LOWER(TRIM(addr_district)))
END AS geo_key
```

**Source:** `replica_galaxy_views.institution.latitude` / `.longitude` / `.address_*`
**Coverage:** 94.5% of active partners have valid GPS coordinates. 5.5% use address fallback — 100% have pincode coverage.

### Cluster Formation
```sql
CAST(CEIL(ROW_NUMBER() OVER (PARTITION BY sm ORDER BY geo_key) / 4.0) AS INT64) AS cluster_id
```
Geohash precision 4 follows a space-filling curve — sorting by geo_key naturally groups geographically proximate partners. Each chunk of 4 consecutive partners in the sorted order = one geographic cluster.

### Best Cluster Selection
```sql
-- Score each cluster as a unit
SELECT sm, cluster_id, SUM(active_score) AS cluster_score, MAX(active_score) AS max_score
FROM geo_clustered GROUP BY sm, cluster_id

-- Best = highest aggregate score, tiebreak: highest individual partner score
QUALIFY ROW_NUMBER() OVER (PARTITION BY sm ORDER BY cluster_score DESC, max_score DESC) = 1
```

### Sparse Fill (if best cluster < 4 partners)
Pull from adjacent clusters (cluster_id ± 1) within 15km of best cluster centroid:
```sql
WHERE ABS(g.cluster_id - bc.cluster_id) = 1
  AND ST_DISTANCE(ST_GEOGPOINT(g.lng, g.lat),
                  ST_GEOGPOINT(bc.c_lng, bc.c_lat)) / 1000 < 15
  AND g.cluster_id != bc.cluster_id
QUALIFY ROW_NUMBER() OVER (PARTITION BY g.sm
  ORDER BY ST_DISTANCE(...)) <= (4 - bc.cluster_size)
```
If no adjacent cluster exists or no partner qualifies within 15km — accept fewer than 4 for that day.

---

## 8. Output Schema

One row per partner per rep per day.

| Column | Type | Description |
|---|---|---|
| `list_date` | DATE | Run date (CURRENT_DATE()) |
| `rep_email` | STRING | SM or TM email — filter on this for one rep's list |
| `role` | STRING | 'SM' or 'TM' |
| `farmer_id` | INT64 | Partner ID — join key to app partner table |
| `name` | STRING | Partner display name |
| `channel` | STRING | 'VISIT' or 'CALL' |
| `priority` | INT64 | 1–4 for visits (score order within day's group), 1–2 for calls |
| `active_score` | FLOAT | Final blended score (0–100) |
| `collection_pctile` | FLOAT | Collection bucket score (0–100) |
| `revenue_pctile` | FLOAT | Revenue bucket score (0–100) |
| `relationship_pctile` | FLOAT | Relationship bucket score (0–100) |
| `target_pctile` | FLOAT | Targets bucket score (0–100) |
| `ocp_rs` | FLOAT | Raw OCP overdue amount in ₹ |
| `max_dpd` | INT64 | Max days past due |
| `days_since_visit` | INT64 | Days since last SM/TM visit (9999 = never visited) |
| `collection_tag` | STRING | 'High' / 'Medium' / 'Low' |
| `sales_tag` | STRING | 'High' / 'Medium' / 'Low' / 'Blocked' |
| `zone` | STRING | Geohash-4 code or ADDR_ fallback — partners in same zone = same day cluster |
| `cluster_source` | STRING | 'original' (in best cluster) or 'filled' (pulled from adjacent) |
| `primary_reason` | STRING | Plain-English reason string shown to rep |

---

## 9. Refresh Cadence

Run daily (scheduled BigQuery query or Cloud Composer). `CURRENT_DATE()` drives:
- Suppression window (days_since_visit vs threshold)
- MTD targets (AOP vs actual for current month)
- YoY comparison (same calendar period last FY)
- Relationship bucket (cadence overdue from today)

---

## 10. Known Limitations & Open Items

| Item | Status |
|---|---|
| Targets bucket uses territory-level AOP — no partner-level target | v1 accepted |
| Credit limit (10% of Revenue bucket) uses totalCreditLimit from institution — coverage unknown | Verify join match rate |
| Onboarding track (zoho_leads stage 8x / Closed Won) not yet integrated | Design ready, not built |
| Sparse fill falls back gracefully to <4 when no adjacent cluster within 15km | Working, accepted |
| Day-of-week reweighting (Wed collections, Thu onboarding, Fri revenue) from existing model not yet ported | Carry forward from existing prototype.sql |
| SM/TM same-person visit dedup across roles | Design decision pending |
| Onboarding leads who exist in institution as ACTIVE may double-count | Exclude from active pool if zoho_leads row exists for same rep |

---

## 11. Critical Join Notes (bugs caught during QA)

1. **Institution dedup:** Always filter `status='ACTIVE' AND business_type IN ('Proprietorship','Partnership')` + `QUALIFY ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) = 1`. Without this, 15% of partners have 2–3 duplicate rows causing fan-out.

2. **OCP join path:** `wallet_creditwallettransaction.wallet_user_id` → `csr_farmer.user_id` → `csr_farmer.farmer_id`. Do NOT join directly on `reference_customer_id` — different ID namespace.

3. **Revenue join:** `sale_return_b2c_b2b.owner_id = institution.reference_customer_id` (not `agroex_franchise_id` — matches only 6% of fleet).

4. **Email case:** Always `UPPER(TRIM(email))` on both sides when joining `store_visits_v2` to `okr_data_live`.

5. **Address fallback case:** Wrap all address fields in `LOWER(TRIM(...))` to prevent case-mismatch creating duplicate ADDR_ cluster keys.

---

## 12. Comparison with Existing Model (field_visit_priority_prototype.sql)

| Dimension | Existing Model | New Model |
|---|---|---|
| Collection signal | OCP + POG blended, single percentile | 4 RFM sub-signals (OCP, DPD, payment regularity, recency) |
| Revenue signal | 30-day ₹ decline (weak backtest) | YoY degrowth + order frequency trend + recency + credit headroom |
| OCP blocking | Not considered | OCP > ₹5k → Sales tag Blocked, Revenue bucket = 0 |
| Dormant partners | Scored same as active | Zero orders → forced VISIT (reactivation) |
| Geography | Not considered | Geo-cluster best group of 4 by geohash-4 |
| Channel | Single ranked list | VISIT (geo-clustered, 4/day) + CALL (score-ranked, 2/day) |
| Churned track | Separate track (inactive + OCP) | Absorbed into Collection bucket (OCP > ₹5k → VISIT) |
| Onboarding | Separate track | Separate pool (to be integrated) |
| Reason selection | Highest raw percentile (existing) | Same — highest raw percentile, not weighted contribution |
| Score formula | `(45×C + 25×R + 15×V + 15×T) / 100` | Same formula, richer signals feeding each bucket |
