-- Fresh stock inflow at FCs, SKU x FC x month x door. FY starts @fy_start (default 2026-04-01).
-- Doors per fresh-stock-inflow-doors memory / TRANSFER_ANALYSIS_METHODOLOGY.md:
--   A = PO GRN at non-plant location (external vendors)
--   B = transfer-in at FC whose TO origin is a manufacturing plant (own production)
--   C = approved PA/PAWOB adjustment at FC (82% seeds; non-barcoded receiving route)
-- Excluded: FC<->FC transfer-ins (redistribution), plant intake (PO GRN / PA at plants), NA/STSNP/BAD-GOOD.
WITH plants AS (
  SELECT UPPER(TRIM(location_id)) loc
  FROM `agrostar-data.pristine_wms_prod_db.location_mst`
  WHERE location_type = 'Manufacturing'
),
door_a AS (
  SELECT UPPER(TRIM(gl.item_no)) item_no, UPPER(TRIM(gh.location_code)) fc,
         DATE_TRUNC(DATE(gh.completed_on, 'Asia/Kolkata'), MONTH) month, 'A_po_grn' door,
         SUM(IFNULL(gl.qty,0) + IFNULL(gl.bad_qty,0)) qty
  FROM `agrostar-data.pristine_wms_prod_db.grn_line_serial` gl
  JOIN `agrostar-data.pristine_wms_prod_db.grn_header` gh ON gl.grn_no = gh.grn_no
  WHERE DATE(gl.created_on) > DATE_SUB('{fy_start}', INTERVAL 31 DAY)
    AND DATE(gh.created_on) > DATE_SUB('{fy_start}', INTERVAL 31 DAY)
    AND DATE(gh.completed_on, 'Asia/Kolkata') >= '{fy_start}'
    AND UPPER(TRIM(gh.location_code)) NOT IN (SELECT loc FROM plants)
  GROUP BY 1,2,3
),
door_b AS (
  SELECT UPPER(TRIM(rgl.item_no)) item_no, UPPER(TRIM(rg.location_code)) fc,
         DATE_TRUNC(DATE(rg.completed_on, 'Asia/Kolkata'), MONTH) month, 'B_mfg_transfer_in' door,
         SUM(IFNULL(rgl.qty,0)) qty
  FROM `agrostar-data.pristine_wms_prod_db.return_grn_header` rg
  JOIN `agrostar-data.pristine_wms_prod_db.return_grn_line` rgl ON rg.grn_no = rgl.grn_no
  JOIN (SELECT DISTINCT transfer_no, from_location_code
        FROM `agrostar-data.pristine_wms_prod_db.transfer_header`
        WHERE DATE(created_on) > DATE_SUB('{fy_start}', INTERVAL 300 DAY)) th
    ON rg.document_no = th.transfer_no
  WHERE DATE(rg.created_on) > DATE_SUB('{fy_start}', INTERVAL 31 DAY)
    AND DATE(rg.completed_on, 'Asia/Kolkata') >= '{fy_start}'
    AND LOWER(rg.document_type) = 'transfer order'
    AND IFNULL(rg.is_cancel_mark, 0) = 0
    AND UPPER(TRIM(rg.location_code)) NOT IN (SELECT loc FROM plants)
    AND (UPPER(TRIM(th.from_location_code)) IN (SELECT loc FROM plants) OR th.transfer_no LIKE 'VTO%')
  GROUP BY 1,2,3
),
door_c AS (
  SELECT UPPER(TRIM(al.item_code)) item_no, UPPER(TRIM(ah.location_code)) fc,
         DATE_TRUNC(DATE(ah.completed_on, 'Asia/Kolkata'), MONTH) month, 'C_adjustment_in' door,
         SUM(al.quantity) qty
  FROM `agrostar-data.pristine_wms_prod_db.adjustment_header` ah
  JOIN `agrostar-data.pristine_wms_prod_db.adjustment_line` al ON ah.adjustment_no = al.adjustment_no
  WHERE DATE(ah.created_on) > DATE_SUB('{fy_start}', INTERVAL 31 DAY)
    AND DATE(ah.completed_on, 'Asia/Kolkata') >= '{fy_start}'
    AND ah.approve_status = 'APPROVED' AND ah.adj_type IN ('PA','PAWOB')
    AND UPPER(TRIM(ah.location_code)) NOT IN (SELECT loc FROM plants)
  GROUP BY 1,2,3
)
SELECT * FROM door_a
UNION ALL SELECT * FROM door_b
UNION ALL SELECT * FROM door_c
