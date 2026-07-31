-- Item master + COGS (secondary valuation; units are primary per Darpan).
SELECT UPPER(TRIM(im.item_code)) item_no,
       ANY_VALUE(im.name) item_name,
       ANY_VALUE(im.category_code) category,
       ANY_VALUE(im.sub_sub_product_group) product_group,
       MAX(c.cogs) cogs
FROM `agrostar-data.pristine_wms_prod_db.item_mst` im
LEFT JOIN (SELECT UPPER(TRIM(sku_code)) sku, MAX(running_cogs) cogs
           FROM `agrostar-data.static_tables_views.average_weighted_cogs` GROUP BY 1) c
  ON UPPER(TRIM(im.item_code)) = c.sku
GROUP BY 1
