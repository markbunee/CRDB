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
from ..services.product_map import get_product_map, get_priced_codes
from ..services.csv_export import csv_response, tables_to_csv_text
from ..services.export_gate import ExportRejected, export_slot
from ..services import stats_cache
# 立方体布局（拆表轴/行/列 + 合并开关）唯一定义在 services/cube_layout.py，
# 盒数与门店数统计共用，避免两处重复演化出不一致的口径。
from ..services.cube_layout import Layout as _Layout, compute_layout as _compute_layout
# 透视与值格式化唯一定义在 services/pivot.py（盒数与门店数共用同一份实现）
from ..services.pivot import (
    fmt_month as _fmt_month,
    fmt_val as _fmt_val,
    is_number,
    make_title as _make_title,
    pivot_tables,
    range_label as _range_label,
    safe_sort as _safe_sort,
    val_to_str as _val_to_str,
)
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-box"])
logger = get_logger("app.routers.stats_box")


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


def _agg_expr_and_join(db_key: str, layout: _Layout, metric: str):
    """聚合表达式 + 金额口径所需的开票价 JOIN，返回 (join_sql, agg_expr, extra_params)。"""
    agg_expr = sql.SQL("COALESCE(SUM({}), 0) AS box_count").format(
        sql.Identifier(layout.fm["qty_col"])
    )
    join_sql = sql.SQL("")
    extra_params: list = []
    if metric == "amount":
        # 只取「价格 > 0」的编码：价格缺失 / 0 / 负数都算未配价（对金额的贡献本来就是 0），
        # 与「缺价告警」共用同一判定，避免两者口径漂移。
        pvals = get_priced_codes(db_key)
        if not pvals:
            raise HTTPException(
                400, "当前库尚未配置品类开票价，无法统计实销金额（请在「品类映射表」中维护）"
            )
        join_sql = sql.SQL(
            "LEFT JOIN (VALUES {}) AS p(code, price) ON CAST({}.{} AS TEXT) = p.code"
        ).format(
            sql.SQL(", ").join(sql.SQL("(%s::TEXT, %s::NUMERIC)") for _ in pvals),
            sql.Identifier(layout.table),
            sql.Identifier(layout.fm["product_col"]),
        )
        extra_params = [x for pair in pvals for x in pair]
        agg_expr = sql.SQL("COALESCE(SUM({} * COALESCE(p.price, 0)), 0) AS box_count").format(
            sql.Identifier(layout.fm["qty_col"])
        )
    return join_sql, agg_expr, extra_params


def _fetch_raw(
    db_key: str,
    layout: _Layout,
    date_from: Optional[str],
    date_to: Optional[str],
    store_type: str,
    metric: str,
) -> List[dict]:
    """单窗口聚合，返回原始分组行（交给 _pivot 透视）。"""
    where, params = _build_where(
        layout.fm, layout.cfg, date_from, date_to,
        layout.product_list, layout.region_col, layout.region_list, store_type,
    )
    join_sql, agg_expr, extra_params = _agg_expr_and_join(db_key, layout, metric)
    fields = list(layout.group_cols) + [agg_expr]
    if layout.group_cols:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{join}{where} GROUP BY {group} ORDER BY {order}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(layout.table),
            join=join_sql,
            where=where,
            group=sql.SQL(", ").join(layout.group_cols),
            order=sql.SQL(", ").join(layout.group_cols),
        )
    else:
        stmt = sql.SQL("SELECT {fields} FROM {table}{join}{where}").format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(layout.table),
            join=join_sql,
            where=where,
        )
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, extra_params + list(params))
                colnames = [d[0] for d in cur.description]
                return [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("盒数统计 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")


def _fetch_raw_windows(
    db_key: str,
    layout: _Layout,
    windows: List[tuple],
    store_type: str,
    metric: str,
) -> List[List[dict]]:
    """**一次扫描**同时算出多个时间窗的聚合结果（同比 / 环比专用）。

    原实现对本期 / 去年同期 / 上期各跑一次全量聚合（扫表 3 次）。这里改为：
    WHERE 覆盖三窗**并集**，用 ``SUM(CASE WHEN 日期 BETWEEN ... THEN 值 ELSE 0 END)``
    一次算出三列，再按窗口拆成三份原始行。
    扫描量从「三窗之和」降到「三窗并集」，并省掉两次查询往返与两次 JIT/规划开销。
    """
    lo = min(w[0] for w in windows)
    hi = max(w[1] for w in windows)
    where, params = _build_where(
        layout.fm, layout.cfg, lo, hi,
        layout.product_list, layout.region_col, layout.region_list, store_type,
    )
    join_sql, _, extra_params = _agg_expr_and_join(db_key, layout, metric)

    qty = sql.Identifier(layout.fm["qty_col"])
    val = sql.SQL("{} * COALESCE(p.price, 0)").format(qty) if metric == "amount" else qty
    date_id = sql.Identifier(layout.fm["date_col"])

    win_fields = []
    cnt_fields = []
    win_params: list = []
    for i, (df_, dt_) in enumerate(windows):
        cond = sql.SQL("{d} >= %s AND {d} <= %s").format(d=date_id)
        win_fields.append(
            sql.SQL(
                "COALESCE(SUM(CASE WHEN {c} THEN {v} ELSE 0 END), 0) AS {a}"
            ).format(c=cond, v=val, a=sql.SQL("w%d" % i))
        )
        # 该窗口内的原始行数：用于剔除「本窗口没有数据、只是恰好落在并集区间里」的分组，
        # 保证每个窗口的结果集与「单独查该窗口 GROUP BY」完全一致（否则会多出 0 行、合计被算大）
        cnt_fields.append(
            sql.SQL("COUNT(CASE WHEN {c} THEN 1 END) AS {a}").format(
                c=cond, a=sql.SQL("n%d" % i)
            )
        )
        win_params.extend([df_, dt_])

    fields = list(layout.group_cols) + win_fields + cnt_fields
    if layout.group_cols:
        stmt = sql.SQL(
            "SELECT {fields} FROM {table}{join}{where} GROUP BY {group} ORDER BY {order}"
        ).format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(layout.table),
            join=join_sql,
            where=where,
            group=sql.SQL(", ").join(layout.group_cols),
            order=sql.SQL(", ").join(layout.group_cols),
        )
    else:
        stmt = sql.SQL("SELECT {fields} FROM {table}{join}{where}").format(
            fields=sql.SQL(", ").join(fields),
            table=sql.Identifier(layout.table),
            join=join_sql,
            where=where,
        )

    # 参数顺序必须与 SQL 文本中占位符出现顺序一致：SELECT(求和) → SELECT(计数) → JOIN(开票价) → WHERE
    exec_params = win_params + win_params + extra_params + list(params)
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, exec_params)
                colnames = [d[0] for d in cur.description]
                raw = [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("盒数统计(多窗口) %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")

    wkeys = ["w%d" % i for i in range(len(windows))]
    nkeys = ["n%d" % i for i in range(len(windows))]
    out: List[List[dict]] = []
    for i, wk in enumerate(wkeys):
        nk = nkeys[i]
        rows: List[dict] = []
        for r in raw:
            if not r.get(nk):
                continue  # 该窗口内没有任何原始行，不属于本窗口的结果集
            rr = {k: v for k, v in r.items() if k not in wkeys and k not in nkeys}
            rr["box_count"] = r[wk]
            rows.append(rr)
        out.append(rows)
    return out


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
    layout = _compute_layout(
        db_key, dimension, region_level, cities, provinces, products,
        merge_months, merge_cities, merge_products, merge_province_cities,
    )
    raw_rows = _fetch_raw(db_key, layout, date_from, date_to, store_type, metric)

    # 品类编码映射：仅在开关打开时读盘（文件很小，读一次足够）
    pmap: Optional[Dict[str, str]] = get_product_map(db_key) if map_names else None

    tables = _pivot(
        raw_rows, layout.fm, dimension, layout.merge_for,
        date_from, date_to,
        layout.split_col, layout.row_col, layout.col_col,
        layout.split_name, layout.row_name, layout.col_name, layout.region_name,
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
        "province_expanded": layout.province_expanded,
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


def _row_total(cells: Dict[str, Any]) -> Any:
    """计算一行所有列的合计（int 盒数 / Decimal 金额都要算进去）。"""
    return sum(v for v in cells.values() if is_number(v))


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

    # 布局只算一次；本期 / 同期 / 上期改为**一次扫描**同时算出（原实现各跑一次 = 扫表 3 次）
    layout = _compute_layout(
        db_key, dimension, region_level, cities, provinces, products,
        merge_months, merge_cities, merge_products, merge_province_cities,
    )
    r_curr, r_yoy, r_mom = _fetch_raw_windows(
        db_key, layout,
        [(date_from, date_to), (yoy_from, yoy_to), (mom_from, mom_to)],
        store_type, metric,
    )
    # 三期用同一份品类映射：编码->中文名在各期保持一致，同比环比的行/列匹配才不会错位
    pmap: Optional[Dict[str, str]] = get_product_map(db_key) if map_names else None

    def _tables_of(rows: List[dict], df_: Optional[str], dt_: Optional[str]) -> List[dict]:
        return _pivot(
            rows, layout.fm, dimension, layout.merge_for, df_, dt_,
            layout.split_col, layout.row_col, layout.col_col,
            layout.split_name, layout.row_name, layout.col_name, layout.region_name,
            pmap,
        )

    # curr 的返回结构与 _run_box_count 完全一致（端点直接把它当响应体）
    curr = {
        "tables": _tables_of(r_curr, date_from, date_to),
        "metric": metric,
        "province_expanded": layout.province_expanded,
        "dimension": dimension,
        "region_level": region_level,
        "merge_months": merge_months,
        "merge_cities": merge_cities,
        "merge_products": merge_products,
        "merge_province_cities": merge_province_cities,
        "total_raw_rows": len(r_curr),
    }
    yoy = {"tables": _tables_of(r_yoy, yoy_from, yoy_to)}
    mom = {"tables": _tables_of(r_mom, mom_from, mom_to)}

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
        yoy_sum = 0
        mom_sum = 0
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
            yoy_sum += yoy_total or 0
            mom_sum += mom_total or 0
        # 「合计」行的同期/上月列：由各行再汇总，保证表尾合计与该表数据自洽
        # （合计行的同比 = (总计 - 同期总计) / 同期总计）
        footer = t.get("footer")
        if isinstance(footer, dict):
            footer["yoy_total"] = yoy_sum
            footer["yoy_pct"] = _calc_pct(footer.get("total") or 0, yoy_sum)
            footer["mom_total"] = mom_sum
            footer["mom_pct"] = _calc_pct(footer.get("total") or 0, mom_sum)

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
    """盒数透视：与门店数统计共用 services/pivot.pivot_tables（取值字段 box_count）。"""
    return pivot_tables(
        raw_rows, fm, dimension, merge_for, date_from, date_to,
        split_col, row_col, col_col, split_name, row_name, col_name, region_name,
        pmap, value_key="box_count",
    )


# ---------- CSV 导出 ----------
# 多张子表拼成一个 CSV：首列「分组」= 原子表标题（原 xlsx 的 sheet 名）；
# 开启同比环比时表尾追加 合计/同期合计/同比(%)/上月合计/环比(%)。实现见 services/csv_export.py。


@router.get("/stats/amount_coverage")
def amount_coverage(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """实销金额口径完整性：所选区间内「有销量、却没配开票价」的品类。

    为什么需要它
    ------------
    「实销金额」= SUM(数量 × 开票价)，而**未配置开票价的品类按 0 计入**，
    于是金额会「静默偏低」：数字看起来正常，实际漏掉了一整块销量。
    实测某库曾有 1 个编码占当期 18% 的盒数没配价 —— 金额直接少了约 18%。

    本接口把元凶按盒数排序列出（含占当期总量的比例），供前端在金额视图明确告警。
    实现上只扫一次事实表：CTE 物化一次分组结果，再从中取当期总量算占比。
    """
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not cfg or not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")

    # 只取「价格 > 0」的编码：价格缺失 / 0 / 负数一律算未配价。
    # 与「实销金额」共用同一判定（services/product_map.get_priced_codes），避免口径漂移。
    pvals = get_priced_codes(db_key)

    table = sql.Identifier(cfg["table"])
    pcol = sql.Identifier(fm["product_col"])
    ncol = sql.Identifier(fm.get("product_name_col", fm["product_col"]))
    qcol = sql.Identifier(fm["qty_col"])
    dcol = sql.Identifier(fm["date_col"])

    conds: List[sql.Composable] = []
    params: List[Any] = []
    if date_from:
        conds.append(sql.SQL("{} >= %s").format(dcol))
        params.append(date_from)
    if date_to:
        conds.append(sql.SQL("{} <= %s").format(dcol))
        params.append(date_to)
    where = (sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds)) if conds else sql.SQL("")

    join_sql = sql.SQL("")
    extra_params: List[Any] = []
    if pvals:
        join_sql = sql.SQL(
            " LEFT JOIN (VALUES {}) AS p(code, price) ON CAST({} AS TEXT) = p.code"
        ).format(
            sql.SQL(", ").join(sql.SQL("(%s::TEXT, %s::NUMERIC)") for _ in pvals),
            pcol,
        )
        extra_params = [x for pair in pvals for x in pair]

        # WITH ... AS MATERIALIZED：保证 g 只算一次（PG12+ 默认内联 CTE，
        # 否则外层再取一次 SUM(qty) 会把事实表扫第二遍）
        stmt = sql.SQL(
            "WITH g AS MATERIALIZED ("
            " SELECT CAST({p} AS TEXT) AS code, MIN({n}) AS name,"
            "        COALESCE(SUM({q}), 0) AS qty,"
            "        COALESCE(SUM(CASE WHEN p.price IS NULL THEN {q} ELSE 0 END), 0) AS unpriced_qty"
            " FROM {t}{j}{w} GROUP BY 1"
            ") SELECT code, name, unpriced_qty,"
            " (SELECT COALESCE(SUM(qty), 0) FROM g) AS total_qty"
            " FROM g WHERE unpriced_qty > 0 ORDER BY unpriced_qty DESC LIMIT 200"
        ).format(p=pcol, n=ncol, q=qcol, t=table, j=join_sql, w=where)
        exec_params = extra_params + list(params)
    else:
        # 一个开票价都没配：全部销量都没计入金额
        stmt = sql.SQL(
            "SELECT CAST({p} AS TEXT) AS code, MIN({n}) AS name,"
            " COALESCE(SUM({q}), 0) AS unpriced_qty"
            " FROM {t}{w} GROUP BY 1 HAVING COALESCE(SUM({q}), 0) > 0"
            " ORDER BY 3 DESC LIMIT 200"
        ).format(p=pcol, n=ncol, q=qcol, t=table, w=where)
        exec_params = list(params)

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, exec_params)
                rows = cur.fetchall()
    except Exception as e:
        logger.error("金额口径检查 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"金额口径检查失败: {e}")

    items = [
        {"code": r[0], "name": (r[1] or "").strip(), "qty": float(r[2] or 0)}
        for r in rows
    ]
    unpriced_qty = sum(i["qty"] for i in items)
    total_qty = float(rows[0][3] or 0) if (pvals and rows) else unpriced_qty

    return {
        "db_key": db_key,
        "date_from": date_from,
        "date_to": date_to,
        "priced_codes": len(pvals),
        "total_qty": total_qty,
        "unpriced_qty": unpriced_qty,
        "unpriced_share": round(unpriced_qty / total_qty * 100, 2) if total_qty else 0.0,
        "items": items,
    }


@router.get("/stats/box_count")
def box_count(
    db_key: str = validate_db_key,
    dimension: str = Query("city", description="统计维度: city | product | time | province"),
    region_level: str = Query("city", description="地域级别: city | province"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    merge_months: bool = Query(False),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    merge_cities: bool = Query(False, description="合并地域维度为单表"),
    merge_products: bool = Query(False, description="合并品类维度为单表"),
    merge_province_cities: bool = Query(False, description="已废弃（兼容保留）：省份模式不再展开省内城市，该参数无效"),
    calc_yoy_mom: bool = Query(False, description="计算同比(去年同期)与环比(上个月)"),
    map_names: bool = Query(False, description="品类编码映射为中文名（无映射保持编码）"),
    metric: str = Query("boxes", description="boxes=盒数 | amount=实销金额(元)"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """实销盒数 / 实销金额统计 — 通用三维度 + 地域级别切换。"""
    if metric not in ("boxes", "amount"):
        raise HTTPException(400, f"不支持的指标: {metric}，可选: boxes | amount")
    # 结果缓存：相同筛选的重复请求（反复点统计 / 来回切维度）直接命中，不再全量扫表
    ck = stats_cache.cache_key(
        "box_count", db_key, dimension=dimension, region_level=region_level,
        date_from=date_from, date_to=date_to, merge_months=merge_months,
        cities=cities, provinces=provinces, products=products,
        merge_cities=merge_cities, merge_products=merge_products,
        merge_province_cities=merge_province_cities, calc_yoy_mom=calc_yoy_mom,
        map_names=map_names, metric=metric, store_type=store_type,
    )
    hit = stats_cache.cache_get(ck)
    if hit is not None:
        return hit
    if calc_yoy_mom:
        result = _compute_yoy_mom(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
        )
    else:
        result = _run_box_count(
            db_key, dimension, region_level, date_from, date_to, merge_months,
            cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type, metric,
        )
    stats_cache.cache_set(ck, result)
    return result


@router.get("/stats/box_count/export")
def export_box_count(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
    dimension: str = Query("city", description="统计维度: city | product | time | province"),
    region_level: str = Query("city", description="地域级别: city | province"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    merge_months: bool = Query(False),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    merge_cities: bool = Query(False, description="合并地域维度为单表"),
    merge_products: bool = Query(False, description="合并品类维度为单表"),
    merge_province_cities: bool = Query(False, description="已废弃（兼容保留）：省份模式不再展开省内城市，该参数无效"),
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
