# -*- coding: utf-8 -*-
"""实销盒数统计接口：/api/{db_key}/stats/box_count(+ /export)

逻辑完全复用门店数统计（stats_store.py），仅把聚合指标从
  COUNT(DISTINCT 门店编码)
改为
  SUM(数量)
维度/地域级别/合并开关/导出与门店数统计保持一致。

导出：GET /stats/box_count/export 返回 xlsx（每个 table 一个 sheet），英文文件名。
"""
import calendar
import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import get_cfg, get_field_map, get_region_levels, get_region_label
from ..services.product_map import get_product_map
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-box"])
logger = get_logger("app.routers.stats_box")

DIMENSIONS = ("city", "product", "time")

# ---------- Excel 样式 ----------
HEADER_FILL = PatternFill(start_color="3B6BD6", end_color="3B6BD6", fill_type="solid")
HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
CELL_FONT = Font(name="Microsoft YaHei", size=10)
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
THIN_BORDER = Border(
    left=Side(style="thin", color="D0D5DD"),
    right=Side(style="thin", color="D0D5DD"),
    top=Side(style="thin", color="D0D5DD"),
    bottom=Side(style="thin", color="D0D5DD"),
)


def _parse_list(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip() != ""]


def _fmt_month(val: Any) -> str:
    if val is None:
        return "未知"
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m")
    s = str(val)
    return s[:7] if len(s) >= 7 else s


def _val_to_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def _fmt_val(fm: Dict[str, str], col_name: Optional[str], val: Any,
             pmap: Optional[Dict[str, str]] = None) -> Optional[str]:
    if col_name is None:
        return None
    if val is None:
        return "未知"
    if col_name == fm["month_col"]:
        return _fmt_month(val)
    s = _val_to_str(val)
    # 品类编码 → 品类中文名（仅 map_names 开启时 pmap 非空；无映射的编码保持原样）
    if pmap and col_name == fm["product_col"]:
        return pmap.get(s, s)
    return s


def _range_label(date_from: Optional[str], date_to: Optional[str]) -> str:
    if date_from and date_to:
        return f"{date_from} ~ {date_to}"
    if date_from:
        return f"{date_from} ~ 至今"
    if date_to:
        return f"截至 {date_to}"
    return "全部日期"


def _safe_sort(vals: list) -> list:
    def key(v):
        if v is None:
            return (1, 0, "")
        return (0, 0, v)
    try:
        return sorted(vals, key=key)
    except TypeError:
        return sorted(vals, key=lambda v: (v is None, str(v)))


def _build_where(
    fm: Dict[str, str],
    date_from: Optional[str],
    date_to: Optional[str],
    product_list: List[str],
    region_col: str,
    region_list: List[str],
):
    conditions: List[sql.Composable] = []
    params: list = []

    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_to)

    if product_list:
        placeholders = sql.SQL(", ").join(sql.Placeholder() * len(product_list))
        conditions.append(
            sql.SQL("{} IN ({})").format(sql.Identifier(fm["product_col"]), placeholders)
        )
        params.extend(product_list)

    if region_list:
        placeholders = sql.SQL(", ").join(sql.Placeholder() * len(region_list))
        conditions.append(
            sql.SQL("{} IN ({})").format(sql.Identifier(region_col), placeholders)
        )
        params.extend(region_list)

    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    return where, params


def _make_title(
    dimension: str,
    split_label: Optional[str],
    merge_split: bool,
    date_from: Optional[str],
    date_to: Optional[str],
    split_name: str,
    region_name: str,
) -> str:
    if merge_split or split_label is None:
        if dimension == "city":
            return f"全部{region_name}（合并）"
        if dimension == "product":
            return "全部品类（合并）"
        return f"全部月份（合并）· {_range_label(date_from, date_to)}"
    return f"{split_name}: {split_label}"


def _run_box_count(
    db_key: str,
    dimension: str,
    region_level: str,
    date_from: Optional[str],
    date_to: Optional[str],
    merge_months: bool,
    cities: Optional[str],
    provinces: Optional[str],
    products: Optional[str],
    merge_cities: bool,
    merge_products: bool,
    map_names: bool = False,
) -> dict:
    """盒数统计核心逻辑：查询 + 透视，返回结果 dict（JSON 端点与导出共用）。

    map_names=True 时把品类编码翻译成映射表中文名（无映射保持编码），
    JSON 与导出共用本函数，因此两者的口径天然一致。
    """
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")
    if dimension not in DIMENSIONS:
        raise HTTPException(400, f"不支持的维度: {dimension}，可选: {DIMENSIONS}")

    allowed_regions = get_region_levels(db_key)
    if region_level not in allowed_regions:
        raise HTTPException(400, f"不支持的地域级别: {region_level}，可选: {allowed_regions}")

    region_label = get_region_label(db_key)
    cfg = get_cfg(db_key)
    table = cfg["table"]

    if region_level == "province" and "province_col" in fm:
        region_col = fm["province_col"]
        region_name = "省份"
        region_list = _parse_list(provinces)
    else:
        region_col = fm["city_col"]
        region_name = region_label
        region_list = _parse_list(cities)

    product_list = _parse_list(products)

    if dimension == "city":
        split_col = region_col
        row_col = fm["product_col"]
        col_col = fm["month_col"]
        merge_split = merge_cities
        split_name, row_name, col_name = region_name, "品类", "月份"
    elif dimension == "product":
        split_col = fm["product_col"]
        row_col = region_col
        col_col = fm["month_col"]
        merge_split = merge_products
        split_name, row_name, col_name = "品类", region_name, "月份"
    else:
        split_col = fm["month_col"]
        row_col = region_col
        col_col = fm["product_col"]
        merge_split = merge_months
        split_name, row_name, col_name = "月份", region_name, "品类"

    month_col = fm["month_col"]

    # 根据当前维度，确定 split/row/col 分别由哪个 merge 参数控制
    if dimension == "city":
        merge_for: Dict[str, bool] = {
            split_col: merge_cities,
            row_col: merge_products,
            col_col: merge_months,
        }
    elif dimension == "product":
        merge_for = {
            split_col: merge_products,
            row_col: merge_cities,
            col_col: merge_months,
        }
    else:
        merge_for = {
            split_col: merge_months,
            row_col: merge_cities,
            col_col: merge_products,
        }

    group_cols: List[sql.Identifier] = []
    for col, merge_flag in merge_for.items():
        if col and not merge_flag:
            group_cols.append(sql.Identifier(col))

    where, params = _build_where(fm, date_from, date_to, product_list, region_col, region_list)

    fields = list(group_cols) + [
        sql.SQL("COALESCE(SUM({}), 0) AS box_count").format(
            sql.Identifier(fm["qty_col"])
        )
    ]
    if group_cols:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{where} GROUP BY {group} ORDER BY {order}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(table),
            where=where,
            group=sql.SQL(", ").join(group_cols),
            order=sql.SQL(", ").join(group_cols),
        )
    else:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{where}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(table),
            where=where,
        )

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params)
                colnames = [d[0] for d in cur.description]
                raw_rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("盒数统计 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")

    # 品类编码映射：仅在开关打开时读盘（文件很小，读一次足够）
    pmap: Optional[Dict[str, str]] = get_product_map(db_key) if map_names else None

    tables = _pivot(
        raw_rows, fm, dimension, merge_for,
        date_from, date_to,
        split_col, row_col, col_col,
        split_name, row_name, col_name, region_name,
        pmap,
    )

    logger.info(
        "盒数统计 %s dim=%s region=%s date=[%s,%s] merge_months=%s regions=%s products=%s "
        "merge_cities=%s merge_products=%s -> %d 表, %d 原始行",
        db_key, dimension, region_level, date_from or "-", date_to or "-",
        merge_months, cities or "-" if region_level == "city" else provinces or "-",
        products or "-", merge_cities, merge_products, len(tables), len(raw_rows),
    )

    return {
        "tables": tables,
        "dimension": dimension,
        "region_level": region_level,
        "merge_months": merge_months,
        "merge_cities": merge_cities,
        "merge_products": merge_products,
        "total_raw_rows": len(raw_rows),
    }


# ---------- 同比环比 ----------

def _shift_year(date_str: str) -> str:
    """将日期字符串平移一年（去年同期）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    try:
        return d.replace(year=d.year - 1).strftime("%Y-%m-%d")
    except ValueError:  # 2/29 -> 2/28
        return d.replace(year=d.year - 1, day=28).strftime("%Y-%m-%d")


def _shift_n_months(date_str: str, n: int) -> str:
    """将日期字符串平移 N 个月（正数往后，负数往前）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    total_months = d.year * 12 + d.month - 1 + n
    new_year = total_months // 12
    new_month = total_months % 12 + 1
    last_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day)
    return f"{new_year}-{new_month:02d}-{new_day:02d}"


def _month_count(date_from: str, date_to: str) -> int:
    """计算日期区间跨越的月份数（含首尾，至少 1 个月）。"""
    d1 = datetime.strptime(date_from, "%Y-%m-%d")
    d2 = datetime.strptime(date_to, "%Y-%m-%d")
    months = (d2.year - d1.year) * 12 + (d2.month - d1.month)
    return max(1, months + 1)


def _shift_month_label(month_label: str, months: int) -> str:
    """将 YYYY-MM 月份标签平移 N 个月（正数往后，负数往前）。"""
    try:
        y, m = month_label.split("-")
        y, m = int(y), int(m)
        step = 1 if months > 0 else -1
        for _ in range(abs(months)):
            m += step
            if m > 12:
                m = 1
                y += 1
            elif m < 1:
                m = 12
                y -= 1
        return f"{y}-{m:02d}"
    except Exception:
        return month_label


def _row_total(cells: Dict[str, Any]) -> int:
    """计算一行所有列的合计。"""
    return sum(v for v in cells.values() if isinstance(v, (int, float)))


def _calc_pct(curr: float, prev: float) -> Optional[float]:
    """计算同比/环比百分比，基期为 0 时返回 None。"""
    if prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _compute_yoy_mom(
    db_key: str,
    dimension: str,
    region_level: str,
    date_from: Optional[str],
    date_to: Optional[str],
    merge_months: bool,
    cities: Optional[str],
    provinces: Optional[str],
    products: Optional[str],
    merge_cities: bool,
    merge_products: bool,
    map_names: bool = False,
) -> dict:
    """计算当前期 + 同比(去年同期) + 环比(上个月)，合并到结果中。"""
    if not date_from or not date_to:
        raise HTTPException(400, "同比环比计算需要同时指定开始日期和结束日期")

    month_span = _month_count(date_from, date_to)
    yoy_from, yoy_to = _shift_year(date_from), _shift_year(date_to)

    is_time_unmerged = (dimension == "time" and not merge_months)
    if is_time_unmerged:
        # time 维度·未合并月：逐月比较，取上个月为环比
        mom_from, mom_to = _shift_n_months(date_from, 1), _shift_n_months(date_to, 1)
    else:
        # 其他维度 or 合并月：整段区间比较，取前 N 个月为环比（N = 区间月数）
        mom_from, mom_to = _shift_n_months(date_from, -month_span), _shift_n_months(date_to, -month_span)

    # 三期都要传 map_names：编码->中文名在各期保持一致，同比环比的行/列匹配才不会错位
    curr = _run_box_count(
        db_key, dimension, region_level, date_from, date_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, map_names,
    )
    yoy = _run_box_count(
        db_key, dimension, region_level, yoy_from, yoy_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, map_names,
    )
    mom = _run_box_count(
        db_key, dimension, region_level, mom_from, mom_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, map_names,
    )

    # 构建查找索引: split_value -> row_key -> total
    # 时间维度下 split_value 是月份，需要把同期/上月结果平移到当前期月份才能匹配
    def _index_totals(
        result: dict, shift_months: int = 0, single_key: Optional[str] = None
    ) -> Dict[Optional[str], Dict[str, int]]:
        idx: Dict[Optional[str], Dict[str, int]] = {}
        tables = result.get("tables", [])
        if single_key and len(tables) == 1:
            # 合并月模式下，每个结果只有一张表，直接用固定 key 匹配
            row_map: Dict[str, int] = {}
            for row in tables[0].get("rows", []):
                row_map[row.get("row_key", "")] = _row_total(row.get("cells", {}))
            idx[single_key] = row_map
            return idx
        for t in tables:
            sv = t.get("split_value")
            if shift_months and sv:
                sv = _shift_month_label(sv, shift_months)
            row_map = {}
            for row in t.get("rows", []):
                row_map[row.get("row_key", "")] = _row_total(row.get("cells", {}))
            idx[sv] = row_map
        return idx

    # 时间维度·未合并月时按月份平移匹配（逐月比较）；合并月或非时间维按单表匹配（整段区间比较）
    is_time_merge = dimension == "time" and merge_months
    single_key = "__merged__" if is_time_merge else None
    yoy_shift = 12 if is_time_unmerged else 0
    mom_shift = 1 if is_time_unmerged else 0

    yoy_idx = _index_totals(yoy, yoy_shift, single_key)
    mom_idx = _index_totals(mom, mom_shift, single_key)

    # 合并到当前期结果
    for t in curr["tables"]:
        sv = t.get("split_value")
        lookup_key = single_key if is_time_merge else sv
        yoy_rows = yoy_idx.get(lookup_key, {})
        mom_rows = mom_idx.get(lookup_key, {})
        for row in t.get("rows", []):
            rk = row.get("row_key", "")
            curr_total = _row_total(row.get("cells", {}))
            yoy_total = yoy_rows.get(rk, 0)
            mom_total = mom_rows.get(rk, 0)
            row["total"] = curr_total
            row["yoy_total"] = yoy_total
            row["yoy_pct"] = _calc_pct(curr_total, yoy_total)
            row["mom_total"] = mom_total
            row["mom_pct"] = _calc_pct(curr_total, mom_total)

    methodology = "逐月比较" if is_time_unmerged else "整段区间比较"
    curr["calc_yoy_mom"] = True
    curr["yoy_range"] = {"date_from": yoy_from, "date_to": yoy_to}
    curr["mom_range"] = {"date_from": mom_from, "date_to": mom_to}
    curr["口径说明"] = (
        f"同比({yoy_from}~{yoy_to})、环比({mom_from}~{mom_to})·{methodology}"
    )
    return curr


def _pivot(
    raw_rows: List[dict],
    fm: Dict[str, str],
    dimension: str,
    merge_for: Dict[str, bool],
    date_from: Optional[str],
    date_to: Optional[str],
    split_col: Optional[str],
    row_col: Optional[str],
    col_col: Optional[str],
    split_name: str,
    row_name: str,
    col_name: str,
    region_name: str,
    pmap: Optional[Dict[str, str]] = None,
) -> List[dict]:
    month_col = fm["month_col"]
    merge_split = merge_for.get(split_col, False) if split_col else False
    merge_row = merge_for.get(row_col, False) if row_col else False
    merge_col = merge_for.get(col_col, False) if col_col else False
    col_is_month_merged = (col_col == month_col and merge_col)

    split_raw: list = []
    split_seen: set = set()
    row_raw: list = []
    row_seen: set = set()
    col_raw: list = []
    col_seen: set = set()

    for r in raw_rows:
        if split_col and not merge_split:
            v = r.get(split_col)
            k = _val_to_str(v)
            if k not in split_seen:
                split_seen.add(k)
                split_raw.append(v)
        if row_col and not merge_row:
            v = r.get(row_col)
            k = _val_to_str(v)
            if k not in row_seen:
                row_seen.add(k)
                row_raw.append(v)
        if col_col and not merge_col:
            v = r.get(col_col)
            k = _val_to_str(v)
            if k not in col_seen:
                col_seen.add(k)
                col_raw.append(v)

    if merge_split:
        split_labels: List[Optional[str]] = [None]
    else:
        split_labels = [_fmt_val(fm, split_col, v, pmap) for v in _safe_sort(split_raw)]

    if merge_row:
        row_keys = [f"全部{row_name}（合并）"]
    else:
        row_keys = [_fmt_val(fm, row_col, v, pmap) for v in _safe_sort(row_raw)] if row_col else []

    if col_col:
        if col_is_month_merged:
            col_labels = [_range_label(date_from, date_to)]
        elif merge_col:
            col_labels = [f"全部{col_name}（合并）"]
        else:
            col_labels = [_fmt_val(fm, col_col, v, pmap) for v in _safe_sort(col_raw)]
    else:
        col_labels = []

    index: Dict[tuple, int] = {}
    for r in raw_rows:
        count = r.get("box_count", 0)
        if merge_split:
            sl: Optional[str] = None
        else:
            sl = _fmt_val(fm, split_col, r.get(split_col), pmap)
        if merge_row:
            rk = f"全部{row_name}（合并）"
        else:
            rk = _fmt_val(fm, row_col, r.get(row_col), pmap) if row_col else ""
        if col_col:
            if col_is_month_merged:
                cl = _range_label(date_from, date_to)
            elif merge_col:
                cl = f"全部{col_name}（合并）"
            else:
                cl = _fmt_val(fm, col_col, r.get(col_col), pmap)
        else:
            cl = ""
        index[(sl, rk, cl)] = count

    tables: List[dict] = []
    for sl in split_labels:
        rows = []
        for rk in row_keys:
            cells = {cl: index.get((sl, rk, cl), 0) for cl in col_labels}
            rows.append({"row_key": rk, "cells": cells})
        tables.append(
            {
                "title": _make_title(dimension, sl, merge_split, date_from, date_to, split_name, region_name),
                "split_value": sl,
                "row_header": row_name,
                "col_header": col_name,
                "row_keys": row_keys,
                "col_keys": col_labels,
                "rows": rows,
            }
        )

    return tables


# ---------- Excel 导出 ----------

def _safe_sheet_name(title: str, idx: int) -> str:
    name = re.sub(r'[:\\/\?\*\[\]]', "_", str(title)).strip()
    if not name:
        name = f"sheet{idx + 1}"
    if len(name) > 31:
        name = name[:31]
    return name


def _tables_to_xlsx(tables: List[dict], calc_yoy_mom: bool = False) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    for i, t in enumerate(tables):
        ws = wb.create_sheet(title=_safe_sheet_name(t.get("title") or f"sheet{i+1}", i))
        col_keys = list(t.get("col_keys", []))
        row_keys = list(t.get("row_keys", []))
        rows = t.get("rows", [])

        header = [t.get("row_header", "")] + col_keys
        if calc_yoy_mom:
            header += ["合计", "同期合计", "同比(%)", "上月合计", "环比(%)"]
        ws.append(header)
        for c in range(1, len(header) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = HEADER_ALIGN
            cell.border = THIN_BORDER
        ws.freeze_panes = "A2"

        for row in rows:
            line = [row.get("row_key", "")]
            cells = row.get("cells", {})
            for ck in col_keys:
                v = cells.get(ck, 0)
                line.append(v if v is not None else 0)
            if calc_yoy_mom:
                line.append(row.get("total", 0))
                line.append(row.get("yoy_total", 0))
                yoy_pct = row.get("yoy_pct")
                line.append(yoy_pct if yoy_pct is not None else "—")
                line.append(row.get("mom_total", 0))
                mom_pct = row.get("mom_pct")
                line.append(mom_pct if mom_pct is not None else "—")
            ws.append(line)
            r = ws.max_row
            for c in range(1, len(line) + 1):
                ws.cell(row=r, column=c).font = CELL_FONT

        ws.column_dimensions["A"].width = 18
        for j in range(len(col_keys)):
            ws.column_dimensions[get_column_letter(j + 2)].width = 16
        if calc_yoy_mom:
            for j in range(5):
                ws.column_dimensions[get_column_letter(len(col_keys) + 2 + j)].width = 14

    if not wb.worksheets:
        ws = wb.create_sheet(title="empty")
        ws.append(["无数据"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@router.get("/stats/box_count")
def box_count(
    db_key: str = validate_db_key,
    dimension: str = Query("city", description="统计维度: city | product | time"),
    region_level: str = Query("city", description="地域级别: city | province"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    merge_months: bool = Query(False),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    merge_cities: bool = Query(False, description="合并地域维度为单表"),
    merge_products: bool = Query(False, description="合并品类维度为单表"),
    calc_yoy_mom: bool = Query(False, description="计算同比(去年同期)与环比(上个月)"),
    map_names: bool = Query(False, description="品类编码映射为中文名（无映射保持编码）"),
):
    """实销盒数统计 — 通用三维度 + 地域级别切换。"""
    if calc_yoy_mom:
        return _compute_yoy_mom(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, map_names,
        )
    return _run_box_count(
        db_key, dimension, region_level, date_from, date_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, map_names,
    )


@router.get("/stats/box_count/export")
def export_box_count(
    db_key: str = validate_db_key,
    dimension: str = Query("city", description="统计维度: city | product | time"),
    region_level: str = Query("city", description="地域级别: city | province"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    merge_months: bool = Query(False),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    merge_cities: bool = Query(False, description="合并地域维度为单表"),
    merge_products: bool = Query(False, description="合并品类维度为单表"),
    calc_yoy_mom: bool = Query(False, description="计算同比(去年同期)与环比(上个月)"),
    map_names: bool = Query(False, description="品类编码映射为中文名（与页面口径一致）"),
):
    """导出盒数统计结果为 xlsx（每个 table 一个 sheet，英文文件名）。"""
    if calc_yoy_mom:
        result = _compute_yoy_mom(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, map_names,
        )
    else:
        result = _run_box_count(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, map_names,
        )
    tables = result["tables"]
    buf = _tables_to_xlsx(tables, calc_yoy_mom=calc_yoy_mom)

    suffix = "_yoy_mom" if calc_yoy_mom else ""
    filename = f"box_count_{dimension}_{region_level}{suffix}.xlsx"
    encoded = quote(filename)
    logger.info(
        "盒数导出 %s dim=%s region=%s yoy_mom=%s -> %d 表",
        db_key, dimension, region_level, calc_yoy_mom, len(tables),
    )
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
