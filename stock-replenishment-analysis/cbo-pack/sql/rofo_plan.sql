-- ROFO rolling sales plan, SKU x state x channel x month. Live window only (-1 = placeholder, filtered).
-- State codes remapped to standard (AD->AP, BH->BR, CT->CG, TG->TS, KR->KA per finance quirk).
-- NOTE: table is overwritten in place each refresh; no history retained.
SELECT UPPER(TRIM(item_sku_code)) item_no,
       CASE UPPER(TRIM(state))
         WHEN 'AD' THEN 'AP' WHEN 'BH' THEN 'BR' WHEN 'CT' THEN 'CG'
         WHEN 'TG' THEN 'TS' WHEN 'KR' THEN 'KA' ELSE UPPER(TRIM(state)) END state,
       UPPER(channel) channel, category_repo, rg_product_group,
       month, qty
FROM `agrostar-data.bizfin_team.rofo_fy26`
UNPIVOT (qty FOR month IN (
  apr_qty AS '2026-04-01', may_qty AS '2026-05-01', jun_qty AS '2026-06-01',
  jul_qty AS '2026-07-01', aug_qty AS '2026-08-01', sep_qty AS '2026-09-01',
  oct_qty AS '2026-10-01', nov_qty AS '2026-11-01', dec_qty AS '2026-12-01',
  jan_qty AS '2027-01-01', feb_qty AS '2027-02-01', mar_qty AS '2027-03-01'))
WHERE qty > 0
