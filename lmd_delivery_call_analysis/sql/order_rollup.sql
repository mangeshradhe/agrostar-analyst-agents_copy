-- Package-level calls rolled up to order level.
-- Grain of lmd_call_audits is CALL; one package can have multiple calls (re-attempts/follow-ups);
-- one order can have multiple packages. This query collapses to one row per order_id.
--
-- Join key: lmd_call_audits.package_id = delivery_shippingpackage.code (confirmed clean, 0 unmatched
-- as of 2026-09-26). delivery_shippingpackage cached metadata shows numRows=0 — that's stale, table
-- is live (7.5M+ rows) — ignore the metadata, trust this query.

SELECT
  p.order_id,
  COUNT(DISTINCT a.package_id)                                   AS packages_called,
  COUNT(*)                                                        AS total_calls,
  COUNTIF(a.ai_conflict_detected = 'YES')                        AS calls_with_conflict,
  COUNTIF(a.farmer_willing_to_accept_delivery = 'NO')            AS calls_farmer_declined,
  COUNTIF(a.amount_mismatch_reported_by_farmer = 'YES')          AS calls_amount_mismatch,
  ARRAY_AGG(DISTINCT UPPER(a.overall_call_sentiment) IGNORE NULLS) AS sentiments_seen,
  ARRAY_AGG(DISTINCT a.call_outcome IGNORE NULLS)                AS outcomes_seen,
  MIN(a.created_at)                                              AS first_call_at,
  MAX(a.created_at)                                              AS last_call_at
FROM `agrostar-data.llm_transcripts.lmd_call_audits` a
JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` p
  ON a.package_id = p.code
WHERE a.processing_status = 'success'
GROUP BY p.order_id
ORDER BY total_calls DESC
