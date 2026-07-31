-- Sales (consumption) SKU x FC x month, FY from @fy_start. Serial-level table covers ALL
-- categories incl. seeds (serials system-generated at invoicing; verified Jul 2026: 9.8L seed units present).
-- Excludes cancelled lines and returns per methodology.
SELECT UPPER(TRIM(ItemSKU)) item_no, UPPER(TRIM(Facilitycode)) fc,
       DATE_TRUNC(DATE(CreatedOn, 'Asia/Kolkata'), MONTH) month,
       SUM(IFNULL(good_qty, 1)) qty,
       ROUND(SUM(IFNULL(TotalPrice, 0)), 0) revenue
FROM `agrostar-data.pristine_wms_prod_db.invoiced_report`
WHERE DATE(CreatedOn) > DATE_SUB('{fy_start}', INTERVAL 1 DAY)
  AND line_status != 'CANCELLED' AND IFNULL(is_return, 0) = 0
GROUP BY 1,2,3
