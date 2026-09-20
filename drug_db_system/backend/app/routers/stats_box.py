# -*- coding: utf-8 -*-
"""实销盒数统计接口：/api/{db_key}/stats/box_count(+ /export)

逻辑完全复用门店数统计（stats_store.py），仅把聚合指标从
  COUNT(DISTINCT 门店编码)
改为
  SUM(数量)
维度/地域级别/合并开关/导出与门店数统计保持一致。

导出：GET /stats/box_count/export 返回 **CSV**（多张子表用首列「分组」区分），英文文件名。
"""
import calendar
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key, require_admin, require_permission
from ..models.schema_def import get_cfg, get_field_map, get_region_levels, get_region_label
from ..services.product_map import get_product_map, get_product_map_full
from ..services.csv_export import csv_response, tables_to_csv_text
from ..services.export_gate import ExportRejected, export_slot
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-box"])
logger = get_logger("app.routers.stats_box")

DIMENSIONS = ("city", "product", "time")


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


def _store_type_expr(cfg: dict) -> Optional[sql.Composable]:
    """返回连锁/加盟的 CASE 表达式（仅含 大区/营运区 的库支持，大参林满足）。

    与看板 dashboard._store_type_expr 同口径：大区或营运区字段含「加盟」二字即加盟，否则连锁。
    """
    if any(c[0] == "大区" for c in cfg["columns"]) and any(c[0] == "营运区" for c in cfg["columns"]):
        return sql.SQL(
            "CASE WHEN {daqu} LIKE '%%加盟%%' OR {yingyun} LIKE '%%加盟%%' "
            "THEN '加盟' ELSE '连锁' END"
        ).format(daqu=sql.Identifier("大区"), yingyun=sql.Identifier("营运区"))
    return None


def _build_where(
    fm: Dict[str, str],
    cfg: dict,
    date_from: Optional[str],
    date_to: Optional[str],
    product_list: List[str],
    region_col: str,
    region_list: List[str],
    store_type: str = "all",
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

    st_expr = _store_type_expr(cfg)
    if st_expr is not None and store_type in ("chain", "franchise"):
        label = "连锁" if store_type == "chain" else "加盟"
        conditions.append(sql.SQL("({}) = %s").format(st_expr))
        params.append(label)

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
        if split_name == "省份":
            return "全部省份（合并）"
        if dimension == "city":
            return f"全部{region_name}（合并）"
        if dimension == "product":
            return "全部品类（合并）"
        return f"全部月份（合并）· {_range_label(date_from, date_to)}"
    if split_name == "省份":
        # 省份展开：子表名直接用省名（导出时即 Excel sheet 名），不再加「省份: 」前缀
        return f"{split_label}"
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
    merge_province_cities: bool = False,
    map_names: bool = False,
    store_type: str = "all",
    metric: str = "boxes",
) -> dict:
    """盒数/金额统计核心逻辑：查询 + 透视，返回结果 dict（JSON 端点与导出共用）。

    map_names=True 时把品类编码翻译成映射表中文名（无映射保持编码），
    JSON 与导出共用本函数，因此两者的口径天然一致。

    metric="amount" 时为实销金额口径：SUM(数量 × 品类开票价)，
    开票价来自品类映射表（backend/data/product_map.json），未配置价格的品类按 0 计
    （即不计入金额）。SQL 里把映射做成 VALUES 常量集 JOIN，单次聚合完成；
    列名仍复用 box_count 别名，下游透视/导出零改动。
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

    # 省份模式：
    # - 默认（合并省份未勾选）按「省份展开」：每个省份一张子表，表内 行=城市、列=品类，月份按整段区间合并；
    # - 勾选「合并省份」(merge_cities)：跨省份合并为一张总表；
    # - 勾选「合并省份内的城市」(merge_province_cities)：每省内城市折叠成一个整省合计（一省一行）。
    province_mode = region_level == "province" and "province_col" in fm
    province_expanded = province_mode

    if province_mode:
        split_col = fm["province_col"]
        row_col = fm["city_col"]
        col_col = fm["product_col"]
        split_name, row_name, col_name = "省份", region_label, "品类"
        merge_split = merge_cities         # 合并省份：跨省份合并为单表
        merge_row = merge_province_cities  # 合并省份内的城市：折叠成整省合计
        merge_col = merge_products         # 合并品类
    elif dimension == "city":
        split_col = region_col
        row_col = fm["product_col"]
        col_col = fm["month_col"]
        merge_split = merge_cities
        merge_row = merge_products
        merge_col = merge_months
        split_name, row_name, col_name = region_name, "品类", "月份"
    elif dimension == "product":
        split_col = fm["product_col"]
        row_col = region_col
        col_col = fm["month_col"]
        merge_split = merge_products
        merge_row = merge_cities
        merge_col = merge_months
        split_name, row_name, col_name = "品类", region_name, "月份"
    else:
        split_col = fm["month_col"]
        row_col = region_col
        col_col = fm["product_col"]
        merge_split = merge_months
        merge_row = merge_cities
        merge_col = merge_products
        split_name, row_name, col_name = "月份", region_name, "品类"

    month_col = fm["month_col"]

    # 省份模式下月份恒合并（不进表）；其余维度月份按 merge_months 控制
    merge_for: Dict[str, bool] = {
        split_col: merge_split,
        row_col: merge_row,
        col_col: merge_col,
    }
    if not province_mode:
        merge_for[month_col] = merge_months

    group_cols: List[sql.Identifier] = []
    for col, merge_flag in merge_for.items():
        if col and not merge_flag:
            group_cols.append(sql.Identifier(col))

    where, params = _build_where(fm, cfg, date_from, date_to, product_list, region_col, region_list, store_type)

    # 聚合表达式 + 可选的「开票价」JOIN（仅金额口径需要）
    agg_expr = sql.SQL("COALESCE(SUM({}), 0) AS box_count").format(
        sql.Identifier(fm["qty_col"])
    )
    join_sql = sql.SQL("")
    exec_params: list = list(params)
    if metric == "amount":
        full_map = get_product_map_full(db_key)
        pvals = [
            (code, float(v["price"]))
            for code, v in full_map.items()
            if v.get("price") is not None
        ]
        if not pvals:
            raise HTTPException(
                400, "当前库尚未配置品类开票价，无法统计实销金额（请在「品类映射表」中维护）"
            )
        join_sql = sql.SQL(
            "LEFT JOIN (VALUES {}) AS p(code, price) ON CAST({}.{} AS TEXT) = p.code"
        ).format(
            sql.SQL(", ").join(
                sql.SQL("(%s::TEXT, %s::NUMERIC)") for _ in pvals
            ),
            sql.Identifier(table),
            sql.Identifier(fm["product_col"]),
        )
        # JOIN 条件里的占位符在 SQL 文本中先于 WHERE 出现，参数顺序必须与之对应
        exec_params = [x for pair in pvals for x in pair] + list(params)
        agg_expr = sql.SQL(
            "COALESCE(SUM({} * COALESCE(p.price, 0)), 0) AS box_count"
        ).format(sql.Identifier(fm["qty_col"]))

    fields = list(group_cols) + [agg_expr]
    if group_cols:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{join}{where} GROUP BY {group} ORDER BY {order}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(table),
            join=join_sql,
            where=where,
            group=sql.SQL(", ").join(group_cols),
            order=sql.SQL(", ").join(group_cols),
        )
    else:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{join}{where}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(table),
            join=join_sql,
            where=where,
        )

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, exec_params)
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
        "metric": metric,
        "province_expanded": province_expanded,
        "dimension": dimension,
        "region_level": region_level,
        "merge_months": merge_months,
        "merge_cities": merge_cities,
        "merge_products": merge_products,
        "merge_province_cities": merge_province_cities,
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
    merge_province_cities: bool = False,
    map_names: bool = False,
    store_type: str = "all",
    metric: str = "boxes",
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
        cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
    )
    yoy = _run_box_count(
        db_key, dimension, region_level, yoy_from, yoy_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
    )
    mom = _run_box_count(
        db_key, dimension, region_level, mom_from, mom_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
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


# ---------- CSV 导出 ----------
# 多张子表拼成一个 CSV：首列「分组」= 原子表标题（原 xlsx 的 sheet 名）；
# 开启同比环比时表尾追加 合计/同期合计/同比(%)/上月合计/环比(%)。实现见 services/csv_export.py。


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
    merge_province_cities: bool = Query(False, description="省份模式：合并省内城市为整省合计"),
    calc_yoy_mom: bool = Query(False, description="计算同比(去年同期)与环比(上个月)"),
    map_names: bool = Query(False, description="品类编码映射为中文名（无映射保持编码）"),
    metric: str = Query("boxes", description="boxes=盒数 | amount=实销金额(元)"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """实销盒数 / 实销金额统计 — 通用三维度 + 地域级别切换。"""
    if metric not in ("boxes", "amount"):
        raise HTTPException(400, f"不支持的指标: {metric}，可选: boxes | amount")
    if calc_yoy_mom:
        return _compute_yoy_mom(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
        )
    return _run_box_count(
        db_key, dimension, region_level, date_from, date_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
    )


@router.get("/stats/box_count/export")
def export_box_count(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
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
    merge_province_cities: bool = Query(False, description="省份模式：合并省内城市为整省合计"),
    calc_yoy_mom: bool = Query(False, description="计算同比(去年同期)与环比(上个月)"),
    map_names: bool = Query(False, description="品类编码映射为中文名（与页面口径一致）"),
    metric: str = Query("boxes", description="boxes=盒数 | amount=实销金额(元)"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """导出盒数 / 金额统计结果为 CSV（多张子表用首列「分组」区分，英文文件名）。

    同比环比口径下一共要跑 3 次重聚合（本期/同期/上期），因此更必须走导出闸门：
    并发满时直接 429，而不是让多个人一起把数据库打满。
    """
    if metric not in ("boxes", "amount"):
        raise HTTPException(400, f"不支持的指标: {metric}，可选: boxes | amount")
    try:
        with export_slot(user.get("id"), f"{db_key}/stats/box_count.csv"):
            if calc_yoy_mom:
                result = _compute_yoy_mom(
                    db_key, dimension, region_level, date_from, date_to, merge_months,
                    cities, provinces, products, merge_cities, merge_products,
                    merge_province_cities, map_names, store_type, metric,
                )
            else:
                result = _run_box_count(
                    db_key, dimension, region_level, date_from, date_to, merge_months,
                    cities, provinces, products, merge_cities, merge_products,
                    merge_province_cities, map_names, store_type, metric,
                )
            tables = result["tables"]
            text = tables_to_csv_text(tables, calc_yoy_mom=calc_yoy_mom)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})

    suffix = "_yoy_mom" if calc_yoy_mom else ""
    prefix = "sales_amount" if metric == "amount" else "box_count"
    filename = f"{prefix}_{dimension}_{region_level}{suffix}.csv"
    logger.info(
        "盒数导出（CSV）%s dim=%s region=%s yoy_mom=%s -> %d 表",
        db_key, dimension, region_level, calc_yoy_mom, len(tables),
    )
    return csv_response(filename, text)
