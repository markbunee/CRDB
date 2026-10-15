# -*- coding: utf-8 -*-
"""透视表构建：把「原始分组行」透成多张子表（盒数 / 门店数统计共用）。

为什么单独抽出来
----------------
盒数统计与门店数统计的透视逻辑**逐字节相同**，只有取值字段不同
（``box_count`` vs ``store_count``）。原实现两边各写一份约 110 行，
于是任何一处新增列（比如「行合计 / 列合计」）都要改两遍、极易漏改。
现在收敛为 ``pivot_tables(..., value_key=...)``，两边只传各自的取值字段。

合计口径
--------
- **行合计**：该行所有月份的求和，写在每行的 ``total``；
- **列合计**：该列（月份）所有行（城市/品类）的求和，写在表级 ``col_totals``；
- **总计**：``grand_total``；
- 列合计同时以 ``footer``（形状与数据行完全一致、``row_key="合计"``）给出，
  前端可以直接复用同一套单元格渲染，导出也能直接复用。
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional


def is_number(v: Any) -> bool:
    """数值判定：盒数是 int，金额（NUMERIC）是 Decimal，必须都算进去。

    历史坑：早期只用 ``isinstance(v, (int, float))`` 判断，导致金额口径
    （psycopg2 把 NUMERIC 取成 Decimal）的合计恒为 0。
    """
    return isinstance(v, (int, float, Decimal))

# ---------------------------------------------------------------------------
# 值格式化 / 排序：原分散在 stats_box 与 stats_store 两处，此处为唯一定义
# ---------------------------------------------------------------------------


def fmt_month(val: Any) -> str:
    if val is None:
        return "未知"
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m")
    s = str(val)
    return s[:7] if len(s) >= 7 else s


def val_to_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def fmt_val(
    fm: Dict[str, str],
    col_name: Optional[str],
    val: Any,
    pmap: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if col_name is None:
        return None
    if val is None:
        return "未知"
    if col_name == fm["month_col"]:
        return fmt_month(val)
    s = val_to_str(val)
    # 品类编码 → 品类中文名（仅 map_names 开启时 pmap 非空；无映射的编码保持原样）
    if pmap and col_name == fm["product_col"]:
        return pmap.get(s, s)
    return s


def range_label(date_from: Optional[str], date_to: Optional[str]) -> str:
    if date_from and date_to:
        return f"{date_from} ~ {date_to}"
    if date_from:
        return f"{date_from} ~ 至今"
    if date_to:
        return f"截至 {date_to}"
    return "全部日期"


def safe_sort(vals: list) -> list:
    """空值安全的排序：None 排在最后，且不与 str 比较导致 TypeError。"""

    def key(v):
        if v is None:
            return (1, 0, "")
        return (0, 0, v)

    try:
        return sorted(vals, key=key)
    except TypeError:
        return sorted(vals, key=lambda v: (v is None, str(v)))


def make_title(
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
        return f"全部月份（合并）· {range_label(date_from, date_to)}"
    if split_name == "省份":
        # 省份级别按省份拆表：子表名直接用省名（导出时即 CSV 分组名），不再加「省份: 」前缀
        return f"{split_label}"
    return f"{split_name}: {split_label}"


# ---------------------------------------------------------------------------
# 透视
# ---------------------------------------------------------------------------


def pivot_tables(
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
    value_key: str = "box_count",
    with_totals: bool = True,
) -> List[dict]:
    """把原始分组行透成多张子表（并带上行合计 / 列合计 / 总计）。

    ``value_key``：原始行里承载数值的字段名（盒数 ``box_count`` / 门店数 ``store_count``）。

    ``with_totals``：**仅当指标可加时才允许求和**。
    - 盒数 / 金额是累加量（SUM），行合计、列合计、总计都有实际意义 → ``True``；
    - 门店数是**去重计数**（COUNT(DISTINCT 门店编码)），**不可加**：同一门店卖多个品种、
      或同一品种跨多个月都会被重复计入，把单元格直接相加得到的「合计」偏大且无业务含义
      （正确的区间去重门店数必须另做一次 COUNT(DISTINCT)，不是单元格求和）。
      因此门店数统计传 ``False``，行/列/表尾都不产出任何合计字段，
      避免前端或导出"看起来有个合计"却算出错误数值。
    """
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
            k = val_to_str(v)
            if k not in split_seen:
                split_seen.add(k)
                split_raw.append(v)
        if row_col and not merge_row:
            v = r.get(row_col)
            k = val_to_str(v)
            if k not in row_seen:
                row_seen.add(k)
                row_raw.append(v)
        if col_col and not merge_col:
            v = r.get(col_col)
            k = val_to_str(v)
            if k not in col_seen:
                col_seen.add(k)
                col_raw.append(v)

    if merge_split:
        split_labels: List[Optional[str]] = [None]
    else:
        split_labels = [fmt_val(fm, split_col, v, pmap) for v in safe_sort(split_raw)]

    if merge_row:
        row_keys = [f"全部{row_name}（合并）"]
    else:
        row_keys = [fmt_val(fm, row_col, v, pmap) for v in safe_sort(row_raw)] if row_col else []

    if col_col:
        if col_is_month_merged:
            col_labels = [range_label(date_from, date_to)]
        elif merge_col:
            col_labels = [f"全部{col_name}（合并）"]
        else:
            col_labels = [fmt_val(fm, col_col, v, pmap) for v in safe_sort(col_raw)]
    else:
        col_labels = []

    # 行轴在层级上「从属」于拆表轴（省份 → 城市）：一个城市只属于唯一省份。
    # 若沿用全局行键，每张省份表都会列出全国所有城市（其余全填 0）——
    # 实测「省份维度」下 23 张省份表全部渲染 167 个城市、3664 个全零行。
    # 当前只有 dimension=province（省份拆表 + 城市行）会命中该分支，其余维度不会。
    row_nested_in_split = bool(
        split_col and row_col
        and fm.get("province_col")
        and split_col == fm["province_col"]
        and row_col == fm["city_col"]
    )

    index: Dict[tuple, int] = {}
    split_row_seen: Dict[Optional[str], set] = {}
    for r in raw_rows:
        count = r.get(value_key, 0)
        if merge_split:
            sl: Optional[str] = None
        else:
            sl = fmt_val(fm, split_col, r.get(split_col), pmap)
        if merge_row:
            rk = f"全部{row_name}（合并）"
        else:
            rk = fmt_val(fm, row_col, r.get(row_col), pmap) if row_col else ""
        if col_col:
            if col_is_month_merged:
                cl = range_label(date_from, date_to)
            elif merge_col:
                cl = f"全部{col_name}（合并）"
            else:
                cl = fmt_val(fm, col_col, r.get(col_col), pmap)
        else:
            cl = ""
        index[(sl, rk, cl)] = count
        if row_nested_in_split:
            split_row_seen.setdefault(sl, set()).add(rk)

    tables: List[dict] = []
    for sl in split_labels:
        # 本表的行键：从属关系下只保留「该拆表值下真实出现」的行（保持全局排序，稳定）
        if row_nested_in_split and not merge_row:
            present = split_row_seen.get(sl, set())
            table_row_keys = [rk for rk in row_keys if rk in present]
        else:
            table_row_keys = row_keys
        rows = []
        col_totals: Dict[str, Any] = {cl: 0 for cl in col_labels}
        grand_total: Any = 0
        for rk in table_row_keys:
            cells = {cl: index.get((sl, rk, cl), 0) for cl in col_labels}
            if not with_totals:
                rows.append({"row_key": rk, "cells": cells})
                continue
            row_total = sum(v for v in cells.values() if is_number(v))
            for cl in col_labels:
                v = cells.get(cl)
                if is_number(v):
                    col_totals[cl] += v
            grand_total += row_total
            rows.append({"row_key": rk, "cells": cells, "total": row_total})
        table = {
            "title": make_title(dimension, sl, merge_split, date_from, date_to, split_name, region_name),
            "split_value": sl,
            "row_header": row_name,
            "col_header": col_name,
            "row_keys": table_row_keys,
            "col_keys": col_labels,
            "rows": rows,
        }
        if with_totals:
            # 列合计 / 总计：前端与导出直接把 footer 当成一行「合计」渲染
            table["col_totals"] = col_totals
            table["grand_total"] = grand_total
            table["footer"] = {"row_key": "合计", "cells": col_totals, "total": grand_total}
        tables.append(table)

    return tables
