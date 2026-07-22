# -*- coding: utf-8 -*-
"""Excel 导出接口：根据查询条件导出筛选后的结果为格式化的 Excel。"""
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from ..dependencies import validate_db_key
from ..logger import get_logger
from ..models.schema_def import get_cfg, col_names
from ..services.query_builder import (
    query_rows, build_search_conditions, build_filter_conditions,
    build_date_range_condition, _assemble_where,
)
from ..database import get_conn
from ..utils.serialization import jsonable

from urllib.parse import quote

router = APIRouter(prefix="/api/{db_key}", tags=["excel_export"])
logger = get_logger("app.routers.excel_export")

# 样式常量
HEADER_FILL = PatternFill(start_color="3B6BD6", end_color="3B6BD6", fill_type="solid")
HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
CELL_FONT = Font(name="Microsoft YaHei", size=10)
CELL_FONT_BOLD = Font(name="Microsoft YaHei", size=10, bold=True)
THIN_BORDER = Border(
    left=Side(style="thin", color="D0D5DD"),
    right=Side(style="thin", color="D0D5DD"),
    top=Side(style="thin", color="D0D5DD"),
    bottom=Side(style="thin", color="D0D5DD"),
)
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
CELL_ALIGN = Alignment(vertical="center", wrap_text=False)
ALT_FILL = PatternFill(start_color="F7F8FA", end_color="F7F8FA", fill_type="solid")


def _build_export_query(db_key: str, search: str, date_from: Optional[str],
                        date_to: Optional[str], filters: dict,
                        sort_by: str, sort_dir: str):
    """复用 query_rows 的查询逻辑，构造完整的 SQL 用于导出（不分页）。"""
    from psycopg2 import sql
    cfg = get_cfg(db_key)
    table = cfg["table"]
    cols = col_names(db_key)
    select_cols = sql.SQL(", ").join(
        [sql.Identifier("id")] + [sql.Identifier(c) for c in cols]
    )

    all_conditions = []
    all_params = []

    # 日期范围
    date_cond, date_params = build_date_range_condition(db_key, date_from, date_to)
    if date_cond is not None:
        all_conditions.append(date_cond)
        all_params.extend(date_params)

    # 搜索
    search_conds, search_params = build_search_conditions(db_key, search)
    if search_conds:
        or_group = sql.SQL("({})").format(sql.SQL(" OR ").join(search_conds))
        all_conditions.append(or_group)
        all_params.extend(search_params)

    # 过滤
    filter_conds, filter_params = build_filter_conditions(db_key, filters)
    all_conditions.extend(filter_conds)
    all_params.extend(filter_params)

    where = _assemble_where(all_conditions)

    # ORDER BY
    if sort_by and sort_by in set(["id"] + cols):
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
        order = sql.SQL(" ORDER BY {} {}").format(
            sql.Identifier(sort_by), sql.SQL(direction))
    else:
        order = sql.SQL(" ORDER BY id DESC")

    stmt = sql.SQL("SELECT {} FROM {}{}{}").format(
        select_cols, sql.Identifier(table), where, order)

    return stmt, all_params, ["id"] + cols


@router.get("/export/excel")
def export_excel(
    db_key: str = validate_db_key,
    search: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    product_codes: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
):
    """导出当前筛选/查询结果为格式化的 Excel 文件。

    接受与 /rows 相同的查询参数，导出所有满足条件的行（不分页）。
    """
    cfg = get_cfg(db_key)
    filters = {}
    if db_key == "dashenlin":
        if product_codes:
            filters["商品编码"] = product_codes
        if cities:
            filters["城市"] = cities
        if provinces:
            filters["省份"] = provinces

    stmt, params, all_cols = _build_export_query(
        db_key, search, date_from, date_to, filters, sort_by, sort_dir,
    )

    # 查询全部匹配行（服务端游标分批读取）
    rows_data = []
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, params)
            while True:
                batch = cur.fetchmany(2000)
                if not batch:
                    break
                for r in batch:
                    # r 是普通 tuple
                    rows_data.append(tuple(jsonable(v) for v in r))

    # 用 openpyxl 生成 Excel
    wb = Workbook()
    ws = wb.active
    ws.title = cfg.get("label", db_key)

    # --- 表头 ---
    for col_idx, col_name in enumerate(all_cols, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        cell.border = THIN_BORDER

    # 冻结首行
    ws.freeze_panes = "A2"
    # 开启筛选
    ws.auto_filter.ref = f"A1:{get_column_letter(len(all_cols))}{len(rows_data) + 1}"

    # --- 数据行 ---
    for row_idx, row_data in enumerate(rows_data, 2):
        is_alt = row_idx % 2 == 0
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = CELL_FONT
            cell.alignment = CELL_ALIGN
            cell.border = THIN_BORDER
            if is_alt:
                cell.fill = ALT_FILL

    # --- 设置列宽 ---
    col_widths = {
        "id": 8, "日期": 14, "月度": 12, "公司": 22, "来源公司": 22,
        "门店编码": 14, "门店名称": 20, "门店详细名称": 26,
        "省份": 10, "城市": 12, "区县": 10, "地址": 28,
        "商品编码": 14, "商品名称": 26, "规格": 16, "生产厂家": 26,
        "批号": 14, "批准文号": 18, "有效期至": 14, "营运区": 10,
        "大区": 10, "医院名字": 16, "适应症": 18, "销售价格": 12,
        "单位": 8, "数量": 10,
        "业务日期": 14, "企业名称": 22, "厂家名称": 22, "生产批号": 14,
        "生产日期": 14, "销售数量": 12, "供应商名称": 22,
        "类别": 10, "商品SAP编码": 14, "店号/区域ID": 14,
        "店名/区域": 20, "销量": 10, "过账日期": 14, "合同价": 12,
        "事业部名称": 18, "事业部": 14,
    }
    for col_idx, col_name in enumerate(all_cols, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = col_widths.get(col_name, 16)

    # 写入 bytes 流
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{cfg['label']}_导出.xlsx"
    logger.info(
        "Excel 导出 %s: search=%s date=[%s,%s] filters=%s → %d 行",
        db_key, search or "-", date_from or "-", date_to or "-",
        filters, len(rows_data),
    )
    encoded_filename = quote(filename)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        },
    )
