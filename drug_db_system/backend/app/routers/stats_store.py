# -*- coding: utf-8 -*-
"""门店数统计接口：/api/{db_key}/stats/store_count(+ /export)

按维度统计实销门店数（DISTINCT 门店编码 非重复计数）。

统一为「拆分维度(多表) + 行维度 + 列维度」模型，支持四种维度：
  - dimension=city     地域维度：拆分=地域(可合并)，行=品类(商品编码)，列=月份(可合并)
  - dimension=product  品类维度：拆分=品类(可合并)，行=地域，列=月份(可合并)
  - dimension=time     时间维度：拆分=月份(可合并)，行=地域，列=品类
  - dimension=province 省份维度：拆分=省份(可合并)，行=省内城市(可合并)，列=品类，月份恒合并

地域级别 region_level 可在城市(city)/省份(province)间二选一切换（城市与省份互斥）。
地域级别只替换「地域轴」，拆表/行/列角色与城市级别完全一致（省 → 市 的展开视图
已单拎成独立的 dimension=province，不再与地域级别开关耦合）。

导出：GET /stats/store_count/export 返回 **CSV**（多张子表用首列「分组」区分），英文文件名。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key, require_admin, require_permission
from ..services.product_map import get_product_map
from ..services.csv_export import csv_response, tables_to_csv_text
from ..services.export_gate import ExportRejected, export_slot
from ..services import stats_cache
# 布局（拆表轴/行/列 + 合并开关）与盒数统计共用同一份实现，杜绝两处口径漂移
from ..services.cube_layout import compute_layout as _compute_layout
# 透视与值格式化唯一定义在 services/pivot.py（盒数与门店数共用同一份实现）
from ..services.pivot import (
    fmt_month as _fmt_month,
    fmt_val as _fmt_val,
    make_title as _make_title,
    pivot_tables,
    range_label as _range_label,
    safe_sort as _safe_sort,
    val_to_str as _val_to_str,
)
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-store"])
logger = get_logger("app.routers.stats_store")


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


def _run_store_count(
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
) -> dict:
    """门店数统计核心逻辑：查询 + 透视，返回结果 dict（JSON 端点与导出共用）。

    map_names=True 时把品类编码翻译成映射表中文名（无映射保持编码），
    JSON 与导出共用本函数，因此两者的口径天然一致。
    """
    layout = _compute_layout(
        db_key, dimension, region_level, cities, provinces, products,
        merge_months, merge_cities, merge_products, merge_province_cities,
    )
    fm, cfg, table = layout.fm, layout.cfg, layout.table
    group_cols = layout.group_cols
    province_expanded = layout.province_expanded

    where, params = _build_where(
        fm, cfg, date_from, date_to,
        layout.product_list, layout.region_col, layout.region_list, store_type,
    )

    fields = list(group_cols) + [
        sql.SQL("COUNT(DISTINCT {}) AS store_count").format(
            sql.Identifier(fm["store_col"])
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
        logger.error("门店数统计 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")

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
        "门店数统计 %s dim=%s region=%s date=[%s,%s] merge_months=%s regions=%s products=%s "
        "merge_cities=%s merge_products=%s -> %d 表, %d 原始行",
        db_key, dimension, region_level, date_from or "-", date_to or "-",
        merge_months, cities or "-" if region_level == "city" else provinces or "-",
        products or "-", merge_cities, merge_products, len(tables), len(raw_rows),
    )

    return {
        "tables": tables,
        "province_expanded": province_expanded,
        "dimension": dimension,
        "region_level": region_level,
        "merge_months": merge_months,
        "merge_cities": merge_cities,
        "merge_products": merge_products,
        "merge_province_cities": merge_province_cities,
        "total_raw_rows": len(raw_rows),
    }


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
    """门店数透视：与盒数统计共用 services/pivot.pivot_tables（取值字段 store_count）。

    ``with_totals=False``：门店数是**去重计数**（COUNT(DISTINCT 门店编码)），不可加 ——
    同一门店卖多个品种、或同一品种跨多个月都会被重复计入，单元格求和得到的「合计」偏大且无业务含义，
    故门店数统计不产出任何行/列/表尾合计。
    """
    return pivot_tables(
        raw_rows, fm, dimension, merge_for, date_from, date_to,
        split_col, row_col, col_col, split_name, row_name, col_name, region_name,
        pmap, value_key="store_count", with_totals=False,
    )


# ---------- CSV 导出 ----------
# 多张子表统一拼成一个 CSV：首列「分组」= 原子表标题（原 xlsx 的 sheet 名），
# 后面是行表头 + 各列；各子表列取并集，缺失补 0。实现见 services/csv_export.py。


@router.get("/stats/store_count")
def store_count(
    db_key: str = validate_db_key,
    dimension: str = Query("city", description="统计维度: city | product | time | province"),
    region_level: str = Query("city", description="地域级别: city | province"),
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD"),
    merge_months: bool = Query(False, description="合并月范围"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔（region_level=city 时生效）"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔（region_level=province 时生效）"),
    products: Optional[str] = Query(None, description="品类(商品编码)，英文逗号分隔"),
    merge_cities: bool = Query(False, description="合并地域维度为单表"),
    merge_products: bool = Query(False, description="合并品类维度为单表"),
    merge_province_cities: bool = Query(False, description="已废弃（兼容保留）：省份模式不再展开省内城市，该参数无效"),
    map_names: bool = Query(False, description="品类编码映射为中文名（无映射保持编码）"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """实销门店数统计 — 通用三维度 + 地域级别切换。"""
    # 结果缓存：COUNT(DISTINCT 门店) 是大表上最贵的聚合，重复请求直接命中缓存
    ck = stats_cache.cache_key(
        "store_count", db_key, dimension=dimension, region_level=region_level,
        date_from=date_from, date_to=date_to, merge_months=merge_months,
        cities=cities, provinces=provinces, products=products,
        merge_cities=merge_cities, merge_products=merge_products,
        merge_province_cities=merge_province_cities, map_names=map_names, store_type=store_type,
    )
    hit = stats_cache.cache_get(ck)
    if hit is not None:
        return hit
    result = _run_store_count(
        db_key, dimension, region_level, date_from, date_to, merge_months,
        cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type,
    )
    stats_cache.cache_set(ck, result)
    return result


@router.get("/stats/store_count/export")
def export_store_count(
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
    merge_cities: bool = Query(False),
    merge_products: bool = Query(False),
    merge_province_cities: bool = Query(False, description="已废弃（兼容保留）：省份模式不再展开省内城市，该参数无效"),
    map_names: bool = Query(False, description="品类编码映射为中文名（与页面口径一致）"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """导出门店数统计结果为 CSV（多张子表用首列「分组」区分，英文文件名）。

    查询走导出闸门：并发满 / 连点会 429，避免多人同时跑重聚合把服务器挤崩。
    """
    try:
        with export_slot(user.get("id"), f"{db_key}/stats/store_count.csv"):
            result = _run_store_count(
                db_key, dimension, region_level, date_from, date_to, merge_months,
                cities, provinces, products, merge_cities, merge_products, merge_province_cities, map_names, store_type,
            )
            tables = result["tables"]
            # additive=False：门店数是去重计数，不做合计
            text = tables_to_csv_text(tables, additive=False)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})

    filename = f"store_count_{dimension}_{region_level}.csv"
    logger.info(
        "门店数导出（CSV）%s dim=%s region=%s -> %d 表",
        db_key, dimension, region_level, len(tables),
    )
    return csv_response(filename, text)
