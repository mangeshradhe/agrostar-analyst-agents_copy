# COCO Store — Data & Query Reference Guide

> **What is COCO?** Company Owned Company Operated retail stores. Agrostar's own physical outlets — not franchise. The POS (Point of Sale) system handles in-store transactions.
>
> As of June 2026: **9 stores live**, all in **Pune district, Maharashtra**.

---

## Table of Contents

1. [Data Architecture — The Full Picture](#1-data-architecture)
2. [Canonical CTEs — Always Start Here](#2-canonical-ctes)
3. [Table Schemas](#3-table-schemas)
4. [Query Patterns](#4-query-patterns)
5. [Data Gotchas](#5-data-gotchas)

---

## 1. Data Architecture

COCO store data is spread across four systems. Here is how they connect:

```
galaxy_views.institution                   ← Store master (name, location, status)
  │  reference_customer_id
  │
  ├──→ order_management_order              ← All orders placed at the store
  │      owner_id = reference_customer_id
  │      │  sales_order_id
  │      │
  │      ├──→ order_management_orderitem   ← Line items per order (SKU, qty, price)
  │      │      order_id = sales_order_id
  │      │
  │      └──→ delivery_shippingpackage     ← Dispatch/fulfillment record per order
  │             order_id = CAST(unicommerce_id AS STRING)
  │             │  code (= package_id)
  │             │
  │             └──→ delivery_shippingpackagestatushistory  ← Status timeline
  │                    package_id = delivery_shippingpackage.code
  │
  └──→ pristine_wms_views.location_mst    ← WMS physical location for the store
         reference_customer_id = reference_customer_id
         │  location_id
         │
         └──→ pristine_wms_views.item_inventory  ← Current stock at the store
                location_id = location_id
```

---

## 2. Canonical CTEs

These two CTEs are the foundation of every COCO query. Copy them as-is.

### `coco_stores` — Store Master

```sql
WITH coco_stores AS (
  SELECT
    reference_customer_id AS store_id,
    name                  AS store_name,
    address_state,
    address_district,
    address_taluka,
    status                AS store_status
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) AS rn
    FROM `agrostar-data.galaxy_views.institution`
    WHERE UPPER(ancestor_institutions_name) LIKE '%AGROSTAR EBO%'
      AND archive = FALSE
  )
  WHERE rn = 1
)
```

**Why `ROW_NUMBER()`?** `galaxy_views.institution` can have multiple rows per store — always deduplicate to one row per `reference_customer_id`.

---

### `order_net_amount` — Net Payable Per Order

```sql
order_net_amount AS (
  SELECT
    order_id,
    SUM(selling_price * quantity)                                     AS gross_amount,
    SUM(discount * quantity)                                          AS total_line_discount,
    SUM(adj_discount * quantity)                                      AS total_adjusted_discount,
    SUM((selling_price * quantity)
        - (discount * quantity)
        - (adj_discount * quantity))                                  AS net_payable_amount
  FROM `agrostar-data.prod_db_views.order_management_orderitem`
  GROUP BY order_id
)
```

**Pre-aggregate before joining** — one order has multiple line-item rows (one per SKU). Always aggregate in a CTE first to avoid row fan-out in the main query.

---

## 3. Table Schemas

### `galaxy_views.institution` — Store Master

| Column | Notes |
|--------|-------|
| `reference_customer_id` | Canonical store ID — the key that links everything |
| `name` | Store name (e.g. "COCO Narayangaon") |
| `ancestor_institutions_name` | Filter: `LIKE '%AGROSTAR EBO%'` to get COCO stores |
| `address_state` | State |
| `address_district` | District |
| `address_taluka` | Taluka |
| `status` | Store status (`ACTIVE` / `INACTIVE`) |
| `archive` | Always filter `archive = FALSE` |
| `created_on` | Used for deduplication — take latest row per store |

---

### `order_management_order` — Orders

| Column | Type | Notes |
|--------|------|-------|
| `sales_order_id` | INTEGER | Primary key — joins to `orderitem.order_id` |
| `unicommerce_id` | INTEGER | **Join key for `delivery_shippingpackage`** — must `CAST AS STRING` |
| `owner_id` | INTEGER | = `institution.reference_customer_id` (the store) |
| `order_type` | STRING | `'COCO'` for all COCO store orders |
| `created_on` | TIMESTAMP | Order creation time — always filter on `DATE(created_on)` |
| `grand_total` | FLOAT | What the farmer paid (after discounts) — use for GMV |
| `total_discount` | FLOAT | Total discount applied |
| `status` | STRING | Order status |
| `unicommerce_status` | STRING | Fulfillment status |
| `farmer_id` | INTEGER | Customer (farmer) ID |
| `cash_on_delivery` | INTEGER | `1` = COD, `0` = prepaid |
| `online_paid_amount` | FLOAT | Amount paid online |

**Standard filter for COCO orders:**
```sql
WHERE order_type = 'COCO'
  AND DATE(created_on) BETWEEN '2026-05-15' AND '2026-06-15'
  AND status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
```

---

### `order_management_orderitem` — Line Items

| Column | Notes |
|--------|-------|
| `order_id` | FK → `order_management_order.sales_order_id` |
| `item_sku` | SKU code — joins to `item_master.product_code` |
| `item_name` | Product name |
| `quantity` | Units ordered |
| `selling_price` | Per-unit selling price |
| `discount` | Per-unit line discount |
| `adj_discount` | Per-unit adjusted/promo discount |
| `total_price` | Pre-computed line total |
| `status_code` | Item status |

**Net payable per line:**
```
(selling_price - discount - adj_discount) × quantity
```

---

### `delivery_shippingpackage` — Fulfillment / Dispatch

| Column | Notes |
|--------|-------|
| `code` | Package ID — joins to `delivery_shippingpackagestatushistory.package_id` |
| `order_id` | = `CAST(order_management_order.unicommerce_id AS STRING)` |
| `delivery_status` | Current status of the package |
| `reconciliation_status` | Whether the store manager has settled dues (STRING field) |
| `order_placed_date` | When the order was placed |
| `scheduled_date` | Scheduled delivery date |
| `attempt` | Number of delivery attempts |

**Join pattern:**
```sql
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
```

⚠️ **Always `LEFT JOIN`** — not every COCO order has a package record (walk-in POS sales may not trigger dispatch). An `INNER JOIN` returns 0 rows.

---

### `delivery_shippingpackagestatushistory` — Status Timeline

| Column | Notes |
|--------|-------|
| `package_id` | = `delivery_shippingpackage.code` |
| `delivery_status` | Status at that point in time |
| `created_on` | When this status was logged |
| `by_user` | Who logged it |
| `comment` | Free-text note |

⚠️ **Has duplicate rows** — always `SELECT DISTINCT` or aggregate carefully. Raw row counts inflate ~5×.

---

### `pristine_wms_views.location_mst` — WMS Store Location

| Column | Notes |
|--------|-------|
| `location_id` | WMS location ID — joins to `item_inventory.location_id` |
| `location_name` | Store name in WMS |
| `reference_customer_id` | Links to `galaxy_views.institution.reference_customer_id` |

---

### `pristine_wms_views.item_inventory` — Stock at Store

| Column | Notes |
|--------|-------|
| `location_id` | WMS location = store |
| `item_code` | SKU — joins to `item_mst.item_code` |
| `qty_available` | Available (not reserved) stock |
| `qty_reserved` | Reserved for pending orders |
| `qty_onhand` | Total physical stock (available + reserved) |
| `channel` | `B2B` or `B2C` — stock is ring-fenced per channel |

---

## 4. Query Patterns

### A. GMV and Orders per Store

```sql
WITH coco_stores AS ( /* canonical CTE above */ )

SELECT
  s.store_name,
  s.address_state,
  s.address_district,
  DATE_TRUNC(DATE(o.created_on), MONTH)         AS month,
  COUNT(DISTINCT o.sales_order_id)              AS total_orders,
  COUNT(DISTINCT o.farmer_id)                   AS unique_farmers,
  ROUND(SUM(o.grand_total), 2)                  AS total_gmv,
  ROUND(AVG(o.grand_total), 2)                  AS avg_basket_size
FROM `agrostar-data.prod_db_views.order_management_order` o
JOIN coco_stores s ON s.store_id = o.owner_id
WHERE o.order_type = 'COCO'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
GROUP BY 1, 2, 3, 4
ORDER BY month, total_gmv DESC
```

---

### B. Order-Level Net Payable Amount + Reconciliation Status

```sql
WITH coco_stores AS (
  SELECT
    reference_customer_id AS store_id,
    name                  AS store_name,
    address_state,
    address_district,
    address_taluka,
    status                AS store_status
  FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY reference_customer_id ORDER BY created_on DESC) AS rn
    FROM `agrostar-data.galaxy_views.institution`
    WHERE UPPER(ancestor_institutions_name) LIKE '%AGROSTAR EBO%'
      AND archive = FALSE
  )
  WHERE rn = 1
),

order_net_amount AS (
  SELECT
    order_id,
    SUM(selling_price * quantity)                                     AS gross_amount,
    SUM(discount * quantity)                                          AS total_line_discount,
    SUM(adj_discount * quantity)                                      AS total_adjusted_discount,
    SUM((selling_price * quantity)
        - (discount * quantity)
        - (adj_discount * quantity))                                  AS net_payable_amount
  FROM `agrostar-data.prod_db_views.order_management_orderitem`
  GROUP BY order_id
)

SELECT
  o.sales_order_id,
  o.unicommerce_id,
  s.store_id,
  s.store_name,
  s.address_state,
  s.address_district,
  s.address_taluka,
  s.store_status,
  DATE(o.created_on)            AS order_date,
  oa.gross_amount,
  oa.total_line_discount,
  oa.total_adjusted_discount,
  oa.net_payable_amount,
  sp.reconciliation_status,
  sp.delivery_status
FROM `agrostar-data.prod_db_views.order_management_order` o
JOIN coco_stores s
  ON s.store_id = o.owner_id
JOIN order_net_amount oa
  ON oa.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
WHERE o.order_type = 'COCO'
  AND DATE(o.created_on) > '2026-05-14'
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
ORDER BY o.sales_order_id
```

---

### C. Reconciliation Summary by Store

```sql
WITH coco_stores AS ( /* canonical CTE */ ),
order_net_amount AS ( /* canonical CTE */ )

SELECT
  s.store_name,
  s.address_state,
  s.address_district,
  COUNT(DISTINCT o.sales_order_id)                                   AS total_orders,
  COUNT(DISTINCT CASE WHEN sp.reconciliation_status IS NULL
                       OR sp.reconciliation_status != 'RECONCILED'
                      THEN o.sales_order_id END)                     AS unreconciled_orders,
  ROUND(SUM(CASE WHEN sp.reconciliation_status IS NULL
                  OR sp.reconciliation_status != 'RECONCILED'
                 THEN oa.net_payable_amount ELSE 0 END), 2)          AS unreconciled_amount
FROM `agrostar-data.prod_db_views.order_management_order` o
JOIN coco_stores s ON s.store_id = o.owner_id
JOIN order_net_amount oa ON oa.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
WHERE o.order_type = 'COCO'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
GROUP BY 1, 2, 3
ORDER BY unreconciled_amount DESC
```

> **Before using `'RECONCILED'` as a filter value**, confirm the exact string used in production:
> ```sql
> SELECT DISTINCT reconciliation_status
> FROM `agrostar-data.prod_db_views.delivery_shippingpackage`
> LIMIT 20
> ```

---

### D. Product Mix — Top SKUs and Categories

```sql
WITH coco_stores AS ( /* canonical CTE */ )

SELECT
  s.store_name,
  im.category_name,
  oi.item_sku                      AS sku_code,
  oi.item_name,
  SUM(oi.quantity)                 AS units_sold,
  ROUND(SUM(oi.total_price), 2)    AS revenue
FROM `agrostar-data.prod_db_views.order_management_order` o
JOIN coco_stores s ON s.store_id = o.owner_id
JOIN `agrostar-data.prod_db_views.order_management_orderitem` oi
  ON oi.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.item_master` im
  ON im.product_code = oi.item_sku
WHERE o.order_type = 'COCO'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
GROUP BY 1, 2, 3, 4
ORDER BY revenue DESC
```

---

### E. Inventory at Each Store

```sql
WITH coco_location_ids AS (
  SELECT lm.location_id, lm.location_name
  FROM `agrostar-data.pristine_wms_views.location_mst` lm
  WHERE EXISTS (
    SELECT 1
    FROM `agrostar-data.galaxy_views.institution` inst
    WHERE UPPER(inst.ancestor_institutions_name) LIKE '%AGROSTAR EBO%'
      AND inst.archive = FALSE
      AND CAST(inst.reference_customer_id AS STRING) = lm.reference_customer_id
  )
)
SELECT
  lm.location_name          AS store_name,
  ii.item_code              AS sku,
  im.name                   AS item_name,
  im.category_code          AS category,
  SUM(ii.qty_available)     AS qty_available,
  SUM(ii.qty_reserved)      AS qty_reserved,
  SUM(ii.qty_onhand)        AS qty_onhand
FROM `agrostar-data.pristine_wms_views.item_inventory` ii
JOIN coco_location_ids lm ON lm.location_id = ii.location_id
LEFT JOIN `agrostar-data.pristine_wms_views.item_mst` im
  ON im.item_code = ii.item_code
GROUP BY 1, 2, 3, 4
ORDER BY store_name, qty_onhand DESC
```

---

### F. New vs Repeat Farmers per Store

```sql
WITH coco_stores AS ( /* canonical CTE */ ),

first_coco_order AS (
  SELECT farmer_id, MIN(DATE(created_on)) AS first_order_date
  FROM `agrostar-data.prod_db_views.order_management_order`
  WHERE order_type = 'COCO'
    AND status NOT IN ('CANCELLED')
  GROUP BY 1
)

SELECT
  s.store_name,
  COUNT(DISTINCT o.farmer_id)                                                   AS total_farmers,
  COUNT(DISTINCT CASE WHEN fo.first_order_date BETWEEN @start_date AND @end_date
                      THEN o.farmer_id END)                                     AS new_farmers,
  COUNT(DISTINCT CASE WHEN fo.first_order_date < @start_date
                      THEN o.farmer_id END)                                     AS repeat_farmers
FROM `agrostar-data.prod_db_views.order_management_order` o
JOIN coco_stores s ON s.store_id = o.owner_id
JOIN first_coco_order fo ON fo.farmer_id = o.farmer_id
WHERE o.order_type = 'COCO'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
GROUP BY 1
ORDER BY total_farmers DESC
```

---

## 5. Data Gotchas

| # | Gotcha | Rule |
|---|--------|------|
| 1 | **Order identification** | Use `order_type = 'COCO'` only. Never `LIKE '%offline%'` — that catches 1,400+ Saathi franchise orders. |
| 2 | **Shipping package join** | Always `LEFT JOIN delivery_shippingpackage` — COCO walk-in POS sales may not generate a package record. `INNER JOIN` returns 0 rows. |
| 3 | **Shipping package join key** | Join on `CAST(o.unicommerce_id AS STRING)`, not `sales_order_id`. |
| 4 | **Reconciliation field** | Use `reconciliation_status` (STRING column). `reconciliation_done` (BOOLEAN) is not the right field for COCO. |
| 5 | **Adjusted discount column** | Column is `adj_discount` in `order_management_orderitem`. `adjusted_discount` does not exist. |
| 6 | **Net payable formula** | `SUM((selling_price * quantity) - (discount * quantity) - (adj_discount * quantity))` |
| 7 | **orderitem fan-out** | One order = multiple rows (one per SKU). Always pre-aggregate in a CTE before joining to avoid row multiplication. |
| 8 | **Status history duplicates** | `delivery_shippingpackagestatushistory` has duplicate rows. Always `DISTINCT` when counting events. |
| 9 | **Store master dedup** | `galaxy_views.institution` has multiple rows per store. Always take `rn = 1` ordered by `created_on DESC`. |
| 10 | **GMV definition** | `grand_total` = what the farmer paid after discounts. Not MRP × quantity. |

---

## Store Reference (as of June 2026)

| Store Code | Store Name | District | State |
|------------|-----------|----------|-------|
| 12468760 | COCO Pargaon Tarf Ale | Pune | Maharashtra |
| 12468751 | COCO Narayangaon | Pune | Maharashtra |
| 12468757 | COCO Manchar | Pune | Maharashtra |
| 12468761 | COCO Kalamb | Pune | Maharashtra |
| 12456494 | COCO Malegaon | Pune | Maharashtra |
| 12450785 | COCO Sansar | Pune | Maharashtra |
| 12458582 | COCO Sangavi | Pune | Maharashtra |
| 12496906 | COCO Wadgaon Rasai | Pune | Maharashtra |
| 12496872 | COCO Nhavara | Pune | Maharashtra |

> Store codes = `reference_customer_id` in `galaxy_views.institution` = `owner_id` in `order_management_order`.
