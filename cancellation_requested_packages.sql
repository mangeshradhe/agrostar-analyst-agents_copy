-- Packages in 'cancellation_requested' status (last 30 days), one row per order.
-- Columns: farmer name, LMD partner, order value (cod_amount), preferred language, products ordered.
-- Notes:
--   * Orders are joined on dsp.order_id = CAST(omo.unicommerce_id AS STRING) (not sales_order_id).
--   * products_ordered = distinct sub_sub_product_group of ordered SKUs, MKT excluded.
--   * Orders with no products_ordered and Gujarat sources (CSRGJ, APPGJ) are dropped.
--   * lmd_partner: to_franchise_id -> delivery_franchise.id -> user_info_id -> delivery_userinformation.username.
--   * An order can have several packages; QUALIFY keeps one (lowest code).
select code as package_id,order_id unicommerce_id,delivery_status,user_name,lmd_partner,omo.sales_order_id as order_id,omo.confirmed_on as order_date,omo.initiating_source, omo.owner_id,mobile_1,mobile_2,mobile_3,
omo.cod_amount as order_value,
case
when initiating_source in ('CSRRJ','APPRJ','CSRUP','APPMP','CSRMP','APPMP') then "hindi"
when initiating_source in ('CSRGJ','APPGJ') then "gujarati"
when initiating_source in ('CSRMH','APPMH') then "marathi"
else "hindi" end preferred_language,
omoi.products_ordered
from `prod_db_views.delivery_shippingpackage`dsp
left join
(
  select unicommerce_id,sales_order_id,initiating_source,owner_id,confirmed_on,cod_amount from `prod_db_views.order_management_order` where date(created_on) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
) omo on
order_id = cast(omo.unicommerce_id as string)
left join
(
  select farmer_id,mobile_1,mobile_2,mobile_3,
         trim(concat(coalesce(first_name,''),' ',coalesce(last_name,''))) as user_name
  from `prod_db_views.csr_farmer`
) on omo.owner_id = farmer_id
left join
(
  select df.id as franchise_id,
         trim(concat(coalesce(dui.first_name,''),' ',coalesce(dui.last_name,''))) as lmd_partner
  from `prod_db_views.delivery_franchise` df
  left join `prod_db_views.delivery_userinformation` dui
    on dui.username = df.user_info_id
) lmd on dsp.to_franchise_id = lmd.franchise_id
left join
(
  -- one row per order: comma separated list of sub_sub_product_group of the ordered SKUs (MKT excluded)
  select oi.order_id as oi_order_id,
         string_agg(distinct im.sub_sub_product_group, ', ' order by im.sub_sub_product_group) as products_ordered
  from `prod_db_views.order_management_orderitem` oi
  left join `pristine_wms_prod_db.item_mst` im
    on oi.item_sku = im.item_code
  where date(oi.created_on) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    and (im.sub_sub_product_group is null or im.sub_sub_product_group != 'MKT')
  group by oi.order_id
) omoi on omo.sales_order_id = omoi.oi_order_id
where delivery_status in ("cancellation_requested")
and order_placed_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
and omoi.products_ordered is not null
and omo.initiating_source not in ('CSRGJ','APPGJ')
qualify row_number() over (partition by omo.sales_order_id order by code) = 1
