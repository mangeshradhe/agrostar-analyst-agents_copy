-- Current on-hand inventory SKU x FC (snapshot at run time).
SELECT UPPER(TRIM(item_no)) item_no, UPPER(TRIM(location_code)) fc,
       SUM(IFNULL(saleable_quantity, 0)) qty
FROM `agrostar-data.pristine_wms_prod_db.item_inventory`
GROUP BY 1,2
HAVING qty > 0
