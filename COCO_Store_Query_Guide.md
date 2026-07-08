# COCO Store — Data & Query Reference Guide

> **What is COCO?** Company Owned Company Operated retail stores. Agrostar's own physical outlets — not franchise. The POS (Point of Sale) system handles in-store transactions.
>
> As of June 2026: **9 stores live**, all in **Pune district, Maharashtra**.

---

## Table of Contents

1. [Data Architecture](#1-data-architecture)
2. [Canonical Patterns — Always Start Here](#2-canonical-patterns)
3. [Table Schemas](#3-table-schemas)
4. [Query Patterns](#4-query-patterns)
5. [Data Gotchas](#5-data-gotchas)
6. [Store Reference](#6-store-reference)

---

## 1. Data Architecture

```
order_management_order           ← All COCO orders (filter: order_type LIKE 'COCO%')
  │  retail_store_code           ← Store identifier
  │  sales_order_id              ← Joins to orderitem
  │  unicommerce_id              ← Joins to delivery_shippingpackage
  │  owner_id                    ← The FARMER (customer), NOT the store
  │
  ├──→ pristine_wms_views.location_mst        ← Store name
  │      retail_store_code = location_id
  │      → location_name
  │
  ├──→ order_management_orderitem             ← Line items (SKU, qty, price)
  │      sales_order_id = order_id
  │
  └──→ delivery_shippingpackage               ← Fulfillment + reconciliation
         CAST(unicommerce_id AS STRING) = order_id
         → reconciliation_status
         → delivery_status
```

**Key rule:** `owner_id` on COCO orders = the **farmer/customer**, NOT the store. Store identity always comes from `retail_store_code`.

---

## 2. Canonical Patterns

### Base Filter — Apply to Every COCO Query

```sql
WHERE o.order_type LIKE 'COCO%'
  AND DATE(o.created_on) BETWEEN '2026-05-15' AND '2026-06-15'
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
  AND o.owner_id != 11377446
```

- `LIKE 'COCO%'` — not `= 'COCO'`, variants exist in the data
- `owner_id != 11377446` — excludes system/head-office account, always apply

---

### Store Name — Always Join `location_mst`

```sql
LEFT JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON o.retail_store_code = lm.location_id
-- lm.location_name = store display name
```

---

### `order_net_amount` CTE — Net Payable Per Order

Pre-aggregate before joining to avoid row fan-out (one order = multiple item rows):

```sql
WITH order_net_amount AS (
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

---

## 3. Table Schemas

### `order_management_order`

| Column | Notes |
|--------|-------|
| `sales_order_id` | PK — joins to `orderitem.order_id` |
| `unicommerce_id` | Join key for `delivery_shippingpackage` — CAST to STRING |
| `retail_store_code` | **Store identifier** — joins to `location_mst.location_id` |
| `owner_id` | Farmer/customer ID — NOT the store. Exclude `11377446` |
| `order_type` | `'COCO'` or variants — filter with `LIKE 'COCO%'` |
| `created_on` | Order creation time |
| `grand_total` | What the farmer paid after discounts — use for GMV |
| `status` | Order status |
| `unicommerce_status` | Fulfillment status |
| `cash_on_delivery` | `1` = COD, `0` = prepaid |
| `online_paid_amount` | Amount paid online |

---

### `pristine_wms_views.location_mst` — Store Name Lookup

| Column | Notes |
|--------|-------|
| `location_id` | Joins to `order_management_order.retail_store_code` |
| `location_name` | Store display name (e.g. "COCO Narayangaon") |
| `location_code` | WMS location code |

---

### `order_management_orderitem` — Line Items

| Column | Notes |
|--------|-------|
| `order_id` | FK → `order_management_order.sales_order_id` |
| `item_sku` | SKU code |
| `item_name` | Product name |
| `quantity` | Units ordered |
| `selling_price` | Per-unit price |
| `discount` | Per-unit line discount |
| `adj_discount` | Per-unit adjusted/promo discount — column name is `adj_discount` |
| `total_price` | Pre-computed line total |

**Net payable per order:**
```sql
SUM((selling_price * quantity) - (discount * quantity) - (adj_discount * quantity))
```

---

### `delivery_shippingpackage` — Fulfillment & Reconciliation

| Column | Notes |
|--------|-------|
| `code` | Package ID → joins to `delivery_shippingpackagestatushistory.package_id` |
| `order_id` | = `CAST(unicommerce_id AS STRING)` |
| `delivery_status` | Current delivery status |
| `reconciliation_status` | Store dues settlement status — STRING field |
| `attempt` | Delivery attempt count |

**Always LEFT JOIN** — not every COCO order generates a package record.

---

## 4. Query Patterns

### A. Order-Level Net Payable + Reconciliation (Canonical Query)

```sql
WITH order_net_amount AS (
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
  o.retail_store_code           AS store_id,
  lm.location_name              AS store_name,
  o.owner_id                    AS farmer_id,
  DATE(o.created_on)            AS order_date,
  o.status,
  o.unicommerce_status,
  oa.gross_amount,
  oa.total_line_discount,
  oa.total_adjusted_discount,
  oa.net_payable_amount,
  sp.reconciliation_status,
  sp.delivery_status
FROM `agrostar-data.prod_db_views.order_management_order` o
LEFT JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON o.retail_store_code = lm.location_id
LEFT JOIN order_net_amount oa
  ON oa.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
WHERE o.order_type LIKE 'COCO%'
  AND DATE(o.created_on) > '2026-05-14'
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
  AND o.owner_id != 11377446
ORDER BY o.sales_order_id
```

---

### B. GMV and Orders per Store

```sql
SELECT
  o.retail_store_code                   AS store_id,
  lm.location_name                      AS store_name,
  DATE_TRUNC(DATE(o.created_on), MONTH) AS month,
  COUNT(DISTINCT o.sales_order_id)      AS total_orders,
  COUNT(DISTINCT o.owner_id)            AS unique_farmers,
  ROUND(SUM(o.grand_total), 2)          AS total_gmv,
  ROUND(AVG(o.grand_total), 2)          AS avg_basket_size
FROM `agrostar-data.prod_db_views.order_management_order` o
LEFT JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON o.retail_store_code = lm.location_id
WHERE o.order_type LIKE 'COCO%'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
  AND o.owner_id != 11377446
GROUP BY 1, 2, 3
ORDER BY month, total_gmv DESC
```

---

### C. Reconciliation Summary by Store

```sql
WITH order_net_amount AS (
  SELECT
    order_id,
    SUM((selling_price * quantity) - (discount * quantity) - (adj_discount * quantity)) AS net_payable_amount
  FROM `agrostar-data.prod_db_views.order_management_orderitem`
  GROUP BY order_id
)

SELECT
  lm.location_name                                                    AS store_name,
  COUNT(DISTINCT o.sales_order_id)                                    AS total_orders,
  COUNT(DISTINCT CASE WHEN sp.reconciliation_status IS NULL
                       OR sp.reconciliation_status != 'RECONCILED'
                      THEN o.sales_order_id END)                      AS unreconciled_orders,
  ROUND(SUM(CASE WHEN sp.reconciliation_status IS NULL
                  OR sp.reconciliation_status != 'RECONCILED'
                 THEN oa.net_payable_amount ELSE 0 END), 2)           AS unreconciled_amount
FROM `agrostar-data.prod_db_views.order_management_order` o
LEFT JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON o.retail_store_code = lm.location_id
LEFT JOIN order_net_amount oa ON oa.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` sp
  ON sp.order_id = CAST(o.unicommerce_id AS STRING)
WHERE o.order_type LIKE 'COCO%'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
  AND o.owner_id != 11377446
GROUP BY 1
ORDER BY unreconciled_amount DESC
```

> Before using `'RECONCILED'` as a filter, confirm the exact value:
> ```sql
> SELECT DISTINCT reconciliation_status
> FROM `agrostar-data.prod_db_views.delivery_shippingpackage` LIMIT 20
> ```

---

### D. Product Mix — Top SKUs and Categories

```sql
SELECT
  lm.location_name              AS store_name,
  im.category_name,
  oi.item_sku,
  oi.item_name,
  SUM(oi.quantity)              AS units_sold,
  ROUND(SUM(oi.total_price), 2) AS revenue
FROM `agrostar-data.prod_db_views.order_management_order` o
LEFT JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON o.retail_store_code = lm.location_id
JOIN `agrostar-data.prod_db_views.order_management_orderitem` oi
  ON oi.order_id = o.sales_order_id
LEFT JOIN `agrostar-data.prod_db_views.item_master` im
  ON im.product_code = oi.item_sku
WHERE o.order_type LIKE 'COCO%'
  AND DATE(o.created_on) BETWEEN @start_date AND @end_date
  AND o.status NOT IN ('CANCELLED')
  AND LOWER(COALESCE(o.unicommerce_status, '')) NOT IN ('cancelled', 'future order', 'disputed_address', 'error')
  AND o.owner_id != 11377446
GROUP BY 1, 2, 3, 4
ORDER BY revenue DESC
```

---

### E. Inventory at Each Store

```sql
SELECT
  lm.location_name          AS store_name,
  ii.item_code              AS sku,
  im.name                   AS item_name,
  im.category_code          AS category,
  SUM(ii.qty_available)     AS qty_available,
  SUM(ii.qty_reserved)      AS qty_reserved,
  SUM(ii.qty_onhand)        AS qty_onhand
FROM `agrostar-data.pristine_wms_views.item_inventory` ii
JOIN `agrostar-data.pristine_wms_views.location_mst` lm
  ON lm.location_id = ii.location_id
LEFT JOIN `agrostar-data.pristine_wms_views.item_mst` im
  ON im.item_code = ii.item_code
WHERE lm.location_id IN (
  SELECT DISTINCT retail_store_code
  FROM `agrostar-data.prod_db_views.order_management_order`
  WHERE order_type LIKE 'COCO%'
    AND owner_id != 11377446
)
GROUP BY 1, 2, 3, 4
ORDER BY store_name, qty_onhand DESC
```

---

### F. Net Outstanding per Store — Liable vs Settled (validated Jul 7, 2026)

**What it answers:** how much money is unaccounted for per store overall — liability (COD + online collected at store) minus everything settled (QR + cash/van/manual).

**How it differs from query C:** query C measures *cash the store is holding* (packages still `to_be_paid`, QR excluded). This one nets QR settlements against liability, so it's the total-reconciliation view. Small residuals (₹14–₹50) are timing/rounding, not real dues.

Key logic:
- **Store master** = `galaxy_views.institution` filtered `LOWER(ancestor_institutions_name) LIKE '% ebo %'` (covers all 23 EBO/COCO stores across MH/MP/UP)
- **Liability** = `cod_amount + online_paid_amount` on `DELIVERED/DISPATCHED` COCO orders that have a shipping package; `SELECT DISTINCT` at order level first so multi-package orders don't double-count
- **Payments** = `delivery_payment` → `delivery_lppostpaidtransaction.amount_settled`; store link via `CAST(franchise_id AS STRING) = institution.agroex_franchise_id`
- **QR detection** = `by_user = 'system' AND creation_type = 'manual'`; everything else is cash (van / manual deposit)

```sql
WITH stores AS (
  SELECT
    CAST(reference_customer_id AS STRING) AS store_id,
    ANY_VALUE(name)                       AS store_name,
    ANY_VALUE(agroex_franchise_id)        AS agroex_franchise_id
  FROM `agrostar-data.galaxy_views.institution`
  WHERE LOWER(ancestor_institutions_name) LIKE '% ebo %'
    AND reference_customer_id IS NOT NULL
  GROUP BY 1
),

coco_orders AS (
  -- Delivered/dispatched COCO orders; liability = cash + online collected at store
  SELECT
    sales_order_id,
    CAST(unicommerce_id AS STRING)  AS uni_id,
    retail_store_code,
    cod_amount + online_paid_amount AS collect_amount
  FROM `agrostar-data.prod_db_views.order_management_order`
  WHERE order_type = 'COCO'
    AND status IN ('DELIVERED','DISPATCHED')
    AND created_on > '2026-05-01'
),

-- Liability: only orders that have a shipping package;
-- dedup to order level first so multi-package orders don't double-count
liability AS (
  SELECT
    retail_store_code AS store_id,
    COUNT(*)                      AS liable_orders,
    ROUND(SUM(collect_amount), 2) AS amount_liable
  FROM (
    SELECT DISTINCT o.sales_order_id, o.retail_store_code, o.collect_amount
    FROM coco_orders o
    JOIN `agrostar-data.prod_db_views.delivery_shippingpackage` dsp
      ON dsp.order_id = o.uni_id
     AND dsp.order_placed_date > '2026-05-01'
  )
  GROUP BY 1
),

-- Payments: settled amounts per store, split QR vs cash (van/manual)
payments AS (
  SELECT
    s.store_id,
    COUNT(delp.id)                      AS payment_count,
    ROUND(SUM(delph.amount_settled), 2) AS amount_settled,
    ROUND(SUM(CASE WHEN delp.by_user = 'system' AND delp.creation_type = 'manual'
                   THEN delph.amount_settled ELSE 0 END), 2) AS qr_settled,
    ROUND(SUM(CASE WHEN delp.by_user = 'system' AND delp.creation_type = 'manual'
                   THEN 0 ELSE delph.amount_settled END), 2) AS cash_settled
  FROM `agrostar-data.prod_db_views.delivery_payment` delp
  LEFT JOIN `agrostar-data.prod_db_views.delivery_lppostpaidtransaction` delph
    ON delp.lp_postpaid_transaction_id = delph.id
  JOIN stores s
    ON CAST(delp.franchise_id AS STRING) = s.agroex_franchise_id
  WHERE delp.created_on > '2026-05-01'
  GROUP BY 1
)

SELECT
  COALESCE(l.store_id, p.store_id)  AS retail_store_code,
  s.store_name,
  IFNULL(l.liable_orders, 0)        AS liable_orders,
  IFNULL(l.amount_liable, 0)        AS amount_liable,
  IFNULL(p.qr_settled, 0)           AS qr_settled,
  IFNULL(p.cash_settled, 0)         AS cash_settled,
  IFNULL(p.amount_settled, 0)       AS total_settled,
  ROUND(IFNULL(l.amount_liable, 0) - IFNULL(p.amount_settled, 0), 2) AS outstanding
FROM liability l
FULL OUTER JOIN payments p ON l.store_id = p.store_id
LEFT JOIN stores s ON COALESCE(l.store_id, p.store_id) = s.store_id
ORDER BY outstanding DESC
```

**Jul 7, 2026 snapshot (orders since May 1):** 23 stores · 553 liable orders · ₹9,66,239 liable · ₹8,42,828 settled (₹3,58,924 QR + ₹4,83,904 cash) · **₹1,23,411 net outstanding — 87.2% settlement rate.**

Top 3 = 64% of dues: Sangavi ₹29,030 (only 33% settled — red flag) · Khalwa ₹26,296 · Kalamb ₹24,171. Pattern: MP stores settle via cash/van, Pune stores are QR-dominant; new UP stores have low absolute dues but 12–30% settlement ratios. Six stores settled to the rupee.

---

## 5. Data Gotchas

| # | Gotcha | Rule |
|---|--------|------|
| 1 | **Store name source** | `retail_store_code` → `location_mst.location_id` → `location_name`. Do NOT use `galaxy_views.institution`. |
| 2 | **`owner_id` ≠ store** | `owner_id` = farmer/customer on COCO orders. Store = `retail_store_code`. |
| 3 | **System account** | Always exclude `owner_id != 11377446` — this is a system account, not a real farmer. |
| 4 | **`order_type` filter** | `LIKE 'COCO%'` not `= 'COCO'`. Variants exist in the data. |
| 5 | **Shipping package join** | Always `LEFT JOIN` — COCO walk-in POS sales may not generate a package record. INNER JOIN returns 0 rows. |
| 6 | **Shipping package join key** | `sp.order_id = CAST(o.unicommerce_id AS STRING)` — not `sales_order_id`. |
| 7 | **Reconciliation field** | `reconciliation_status` (STRING). Confirm exact value before filtering. |
| 8 | **`adj_discount` column** | Column is `adj_discount`. `adjusted_discount` does not exist. |
| 9 | **orderitem fan-out** | Pre-aggregate in a CTE before joining — one order has multiple item rows. |
| 10 | **Status history duplicates** | `delivery_shippingpackagestatushistory` has duplicate rows — always `DISTINCT`. |
| 11 | **GMV** | Use `grand_total` (what farmer paid). Not MRP × quantity. |

---

## 6. Store Reference

**As of July 2026 the network is 23 stores across Maharashtra, Madhya Pradesh, and Uttar Pradesh** (see query F output for the full list — new stores include Khalwa, Khar Kalan, Bediya, Mandleshwar, Barwaha in MP and Allapur Ranimau, Kothi, Noorpur, Ram Sanehi Ghat, Chapra Nawabganj, Jalalpur, Machhali Shahar in UP). The full store master comes from `galaxy_views.institution` with `LOWER(ancestor_institutions_name) LIKE '% ebo %'`.

Original 9 Pune stores (June 2026):

| retail_store_code | Store Name | District | State |
|-------------------|-----------|----------|-------|
| 12468760 | COCO Pargaon Tarf Ale | Pune | Maharashtra |
| 12468751 | COCO Narayangaon | Pune | Maharashtra |
| 12468757 | COCO Manchar | Pune | Maharashtra |
| 12468761 | COCO Kalamb | Pune | Maharashtra |
| 12456494 | COCO Malegaon | Pune | Maharashtra |
| 12450785 | COCO Sansar | Pune | Maharashtra |
| 12458582 | COCO Sangavi | Pune | Maharashtra |
| 12496906 | COCO Wadgaon Rasai | Pune | Maharashtra |
| 12496872 | COCO Nhavara | Pune | Maharashtra |

`retail_store_code` = `location_id` in `pristine_wms_views.location_mst`
