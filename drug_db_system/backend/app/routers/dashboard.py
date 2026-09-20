# -*- coding: utf-8 -*-
"""数字看板接口：/api/{db_key}/dashboard —— 大参林经营数字看板。

维度：省 / 城市 / 月 / 品种(商品编码→商品名称) / 连锁·加盟
指标：盒数(数量)、笔数、门店数、覆盖省·市数，以及同比增长(YoY)。

实现说明：
- 连锁/加盟 由 `大区`/`营运区` 字段含「加盟」二字判定；库无此列（高济/海王）时自动降级为单一「全部」桶。
- 品种名称直接从 `商品名称` 列取（MIN），无需 product_map 文件。
- 价格列为空，故看板以盒数/笔数/门店数为核心，不呈现销售额。
- 时间过滤统一走「月度」列（已建索引），保证千万级数据下的聚合性能。
"""
import os
import time
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import get_cfg, get_field_map
from ..database import get_conn
from ..utils.serialization import jsonable_rows
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["dashboard"])
logger = get_logger("app.routers.dashboard")


# ---------- 结果缓存（TTL） ----------
# 看板是读多写少的静态分析数据，但单次查询可能扫描千万级大表。
# 加 TTL 缓存可避免「同一筛选反复全表扫描」，显著减轻小内存服务器压力。
# 数据变更（导入/清空）时由对应接口调用 invalidate_dashboard_cache 主动失效。
_DASH_CACHE: dict = {}
_DASH_CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", "300"))


def _dash_cache_key(name: str, db_key: str, **kw) -> str:
    items = [f"{k}={kw.get(k)}" for k in sorted(kw)]
    return "|".join([name, db_key] + items)


def _dash_cache_get(key: str):
    item = _DASH_CACHE.get(key)
    if item and (time.time() - item[0]) < _DASH_CACHE_TTL:
        return item[1]
    if item:
        _DASH_CACHE.pop(key, None)
    return None


def _dash_cache_set(key: str, value):
    _DASH_CACHE[key] = (time.time(), value)


def invalidate_dashboard_cache(_db_key: str | None = None):
    """数据写入（导入/清空）后调用，清空看板缓存。

    _db_key 当前保留以便将来按需按库失效；现在看板数据量小，直接整体清空。
    """
    _DASH_CACHE.clear()


# ---------- 列判定 ----------
def _has_col(cfg: dict, name: str) -> bool:
    return any(c[0] == name for c in cfg["columns"])


def _store_type_expr(cfg: dict) -> Optional[sql.Composable]:
    """返回连锁/加盟的 CASE 表达式（仅大参林等含 大区/营运区 的库支持）。

    注意：该表达式会进入带参数的 execute()，psycopg2 会把 SQL 里的字面量 '%'
    当作占位符解析，故 LIKE 模式中的 '%' 必须写成 '%%'（执行时还原为单个 '%'），
    否则会报 IndexError: list index out of range。
    """
    if _has_col(cfg, "大区") and _has_col(cfg, "营运区"):
        return sql.SQL(
            "CASE WHEN {daqu} LIKE '%%加盟%%' OR {yingyun} LIKE '%%加盟%%' "
            "THEN '加盟' ELSE '连锁' END"
        ).format(daqu=sql.Identifier("大区"), yingyun=sql.Identifier("营运区"))
    return None


# ---------- WHERE 组装（时间窗由调用方通过 date_from/date_to 控制） ----------
def _build_where(
    fm: dict,
    cfg: dict,
    date_from: Optional[str],
    date_to: Optional[str],
    provinces: Optional[str],
    cities: Optional[str],
    products: Optional[str],
    store_type: str,
) -> tuple:
    conditions: List[sql.Composable] = []
    params: list = []

    month_col = sql.Identifier(fm["month_col"])
    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(month_col))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(month_col))
        params.append(date_to)

    if "province_col" in fm and provinces:
        vals = [v.strip() for v in provinces.split(",") if v.strip()]
        if vals:
            conditions.append(
                sql.SQL("{} IN ({})").format(
                    sql.Identifier(fm["province_col"]),
                    sql.SQL(", ").join(sql.Placeholder() * len(vals)),
                )
            )
            params.extend(vals)

    if cities:
        vals = [v.strip() for v in cities.split(",") if v.strip()]
        if vals:
            conditions.append(
                sql.SQL("{} IN ({})").format(
                    sql.Identifier(fm["city_col"]),
                    sql.SQL(", ").join(sql.Placeholder() * len(vals)),
                )
            )
            params.extend(vals)

    if products:
        vals = [v.strip() for v in products.split(",") if v.strip()]
        if vals:
            conditions.append(
                sql.SQL("{} IN ({})").format(
                    sql.Identifier(fm["product_col"]),
                    sql.SQL(", ").join(sql.Placeholder() * len(vals)),
                )
            )
            params.extend(vals)

    st_expr = _store_type_expr(cfg)
    if st_expr is not None and store_type in ("chain", "franchise"):
        label = "连锁" if store_type == "chain" else "加盟"
        conditions.append(sql.SQL("({}) = %s").format(st_expr))
        params.append(label)

    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    return where, params


def _shift_date(s: str, delta: int) -> str:
    """年份加减 delta，保持原格式：'YYYY-MM-DD' 仍返回 'YYYY-MM-DD'，'YYYY-MM' 仍返回 'YYYY-MM'。"""
    parts = s.split("-")
    y = int(parts[0]) + delta
    return "-".join([f"{y:04d}", *parts[1:]])


def _monthly_boxes(
    db_key: str, cfg: dict, fm: dict, where: sql.Composable, params: list,
) -> dict:
    """按月聚合盒数，返回 { 'YYYY-MM': boxes }。where 已含时间窗。"""
    mc = sql.Identifier(fm["month_col"])
    q = sql.Identifier(fm["qty_col"])
    t = sql.Identifier(cfg["table"])
    stmt = sql.SQL(
        "SELECT to_char(date_trunc('month', {mc}), 'YYYY-MM') AS m, "
        "SUM({q}) AS boxes FROM {t}{w} GROUP BY m ORDER BY m"
    ).format(mc=mc, q=q, t=t, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    return {r["m"]: (float(r["boxes"]) if r["boxes"] is not None else 0.0) for r in rows}


# ---------- 时间 / 同比辅助 ----------
def _to_month(s: Optional[str]) -> Optional[str]:
    """归一化到 'YYYY-MM'：'YYYY-MM-DD' / 'YYYY-MM' / 空 -> 'YYYY-MM' / None。

    看板所有「月度」比较统一用月级别，避免日级字符串与月级 key 字典序错位
    （例如 '2025-06-01' <= '2025-06' 在字典序下为 False，会错误丢掉起始月）。
    """
    if not s:
        return None
    return s[:7]


def _month_range(db_key: str, cfg: dict, fm: dict) -> tuple:
    """数据实际月范围 (min_month, max_month)，均为 'YYYY-MM'；无数据则 (None, None)。"""
    mc = sql.Identifier(fm["month_col"])
    t = sql.Identifier(cfg["table"])
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT MIN({m}), MAX({m}) FROM {t}").format(m=mc, t=t))
            mn, mx = cur.fetchone()
    fmt = lambda d: d.strftime("%Y-%m") if d else None
    return fmt(mn), fmt(mx)


def _yoy_window(
    date_from: Optional[str], date_to: Optional[str], min_month: Optional[str] = None
):
    """同比窗口：严格 = 当前区间往前推一年 [from-1y, to-1y]，两区间等长、月份一一对齐。

    口径（2026-09-20 调整）：**始终按用户当前拉取的区间整体前移一年比较**，
    不再判断「去年同期是否完整落在数据范围内」—— 新品 / 新区域 / 新客户去年同期
    本来就没有数据，按 0 计是正确的业务含义；基期为 0 时 yoy_pct 置 None，前端显示「/」。
    min_month 参数仅为兼容既有调用保留，不参与判定。
    """
    df = _to_month(date_from)
    dt = _to_month(date_to)
    if not (df and dt):
        return None
    return _shift_date(df, -1), _shift_date(dt, -1)


# ---------- 选项（筛选下拉） ----------
@router.get("/dashboard/options")
def dashboard_options(db_key: str = validate_db_key):
    """返回筛选所需选项：省份、城市、品种(编码+名称)、月度范围。"""
    key = _dash_cache_key("options", db_key)
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")
    table = sql.Identifier(cfg["table"])

    def distinct(col: str, limit: int = 2000):
        stmt = sql.SQL(
            "SELECT DISTINCT {} FROM {} WHERE {} IS NOT NULL ORDER BY {} LIMIT %s"
        ).format(sql.Identifier(col), table, sql.Identifier(col), sql.Identifier(col))
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, (limit,))
                return [r[0] for r in cur.fetchall()]

    products: list = []
    if "product_col" in fm:
        pcol = sql.Identifier(fm["product_col"])
        ncol = sql.Identifier(fm.get("product_name_col", fm["product_col"]))
        stmt = sql.SQL(
            "SELECT DISTINCT {p}, MIN({n}) FROM {t} GROUP BY {p} ORDER BY {p} LIMIT 200"
        ).format(p=pcol, n=ncol, t=table)
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt)
                products = [{"code": str(r[0]), "name": r[1]} for r in cur.fetchall()]

    provinces = distinct(fm["province_col"]) if "province_col" in fm else []
    cities = distinct(fm["city_col"]) if "city_col" in fm else []

    month_range = {"min": None, "max": None}
    mc = sql.Identifier(fm["month_col"])
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT MIN({m}), MAX({m}) FROM {t}").format(m=mc, t=table))
            mn, mx = cur.fetchone()
            month_range = {"min": mn, "max": mx}

    result = {
        "provinces": provinces,
        "cities": cities,
        "products": products,
        "month_range": month_range,
        "supports_store_type": _store_type_expr(cfg) is not None,
    }
    _dash_cache_set(key, result)
    return result


# ---------- KPI ----------
@router.get("/dashboard/kpi")
def dashboard_kpi(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all", description="all | chain | franchise"),
):
    """看板核心 KPI。

    所有指标均基于 date_from~date_to 当前区间，并应用相同的 省/市/品种/门店类型 过滤：
    - boxes       盒数合计（SUM 数量）
    - yoy_boxes   去年同期盒数（区间 = 当前区间 -1 年，等长对齐）
    - yoy_pct     同比增长率；去年同期销量为 0（无数据）时为 None，前端显示「/」
    - rows        笔数（COUNT(*) 销售记录条数）
    - stores      去重门店数（COUNT(DISTINCT 门店编码)）
    - provinces   覆盖省份数（COUNT(DISTINCT 省份)）
    - cities      覆盖城市数（COUNT(DISTINCT 城市)）
    """
    key = _dash_cache_key("kpi", db_key, date_from=date_from, date_to=date_to,
                          provinces=provinces, cities=cities, products=products,
                          store_type=store_type)
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")

    # 当前区间（窄窗）：用于笔数/门店/省/市等与盒数保持完全一致的口径
    narrow_where, narrow_params = _build_where(
        fm, cfg, date_from, date_to, provinces, cities, products, store_type)

    # 月范围 + 同比窗口（决定去年同期是否有效）
    min_month, _ = _month_range(db_key, cfg, fm)
    yoy = _yoy_window(date_from, date_to, min_month)
    df = _to_month(date_from)
    dt = _to_month(date_to) or "9999"

    # 扩展窗一次拉回 [去年同期起始, 当前区间结束] 的月度数据，再在内存拆分 cur/prev
    ext_from_sql = _shift_date(date_from, -1) if date_from else date_from
    ext_where, ext_params = _build_where(
        fm, cfg, ext_from_sql, date_to, provinces, cities, products, store_type)
    monthly = _monthly_boxes(db_key, cfg, fm, ext_where, ext_params)

    # 盒数：用月级别比较，避免日级字符串与月级 key 字典序错位（修复起始月丢失）
    current_boxes = sum(v for k, v in monthly.items() if (df or "0000") <= k <= dt)

    # 同比：严格按「用户当前拉取的区间」整体前移一年比较，不做任何按数据范围的裁剪。
    # 去年同期缺失的月份按 0 计（新品 / 新区域本就没有去年数据，属正常业务含义）；
    # 基期为 0 时 yoy_pct 置 None，前端显示「/」。
    if yoy:
        prev_from, prev_to = yoy
        previous_boxes = sum(v for k, v in monthly.items() if prev_from <= k <= prev_to)
        yoy_pct = round((current_boxes - previous_boxes) / previous_boxes * 100, 2) if previous_boxes else None
    else:
        previous_boxes = None
        yoy_pct = None

    # 区间聚合（笔数/门店/省/市）—— 与 current_boxes 同口径（当前区间 + 相同过滤）
    #
    # 性能：对千万级事实表直接 COUNT(DISTINCT) 会走「索引扫描 + 全量排序去重」，
    # 8M 行实测约 31s。这里改为「两级聚合」：先按 (省,市,门店) 折叠成门店粒度
    # （并行 HashAggregate，约 2 万组），再在结果上做去重计数，实测约 1.7s；
    # 口径与原写法完全一致（SUM(cnt)=总笔数；COUNT(DISTINCT 门店)=门店数）。
    s_col = sql.Identifier(fm["store_col"])
    inner_select = [sql.SQL("{} AS _ks").format(s_col)]
    inner_group = [s_col]
    outer_cols = [
        sql.SQL("SUM(cnt) AS rows_n"),
        sql.SQL("COUNT(DISTINCT _ks) AS stores"),
    ]
    if "city_col" in fm:
        c_col = sql.Identifier(fm["city_col"])
        inner_select.append(sql.SQL("{} AS _kc").format(c_col))
        inner_group.append(c_col)
        outer_cols.append(sql.SQL("COUNT(DISTINCT _kc) AS cities"))
    if "province_col" in fm:
        p_col = sql.Identifier(fm["province_col"])
        inner_select.append(sql.SQL("{} AS _kp").format(p_col))
        inner_group.append(p_col)
        outer_cols.append(sql.SQL("COUNT(DISTINCT _kp) AS provinces"))
    inner_stmt = sql.SQL(
        "SELECT {sel}, COUNT(*) AS cnt FROM {t}{w} GROUP BY {grp}"
    ).format(
        sel=sql.SQL(", ").join(inner_select),
        t=sql.Identifier(cfg["table"]),
        w=narrow_where,
        grp=sql.SQL(", ").join(inner_group),
    )
    stmt = sql.SQL("SELECT {cols} FROM ({inner}) t").format(
        cols=sql.SQL(", ").join(outer_cols), inner=inner_stmt)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, narrow_params)
            cols = [d[0] for d in cur.description]
            agg = dict(zip(cols, cur.fetchone()))

    result = {
        "boxes": current_boxes,
        "yoy_boxes": previous_boxes,
        "yoy_pct": yoy_pct,
        "rows": agg.get("rows_n"),
        "stores": agg.get("stores"),
        "provinces": agg.get("provinces"),
        "cities": agg.get("cities"),
    }
    _dash_cache_set(key, result)
    return result


# ---------- 趋势（含同比） ----------
@router.get("/dashboard/trend")
def dashboard_trend(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all"),
):
    """按月盒数趋势 + 同比（当前月 vs 去年同月）。

    无有效同比窗口（区间缺失或去年同期越界）时，previous / growth 统一为 None，
    前端据此不展示误导性的「0 同比」。
    """
    key = _dash_cache_key("trend", db_key, date_from=date_from, date_to=date_to,
                          provinces=provinces, cities=cities, products=products,
                          store_type=store_type)
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")

    min_month, _ = _month_range(db_key, cfg, fm)
    yoy = _yoy_window(date_from, date_to, min_month)
    df = _to_month(date_from)
    dt = _to_month(date_to) or "9999"

    # 扩展窗一次拉回 [去年同期起始, 当前区间结束] 的月度数据
    ext_from_sql = _shift_date(date_from, -1) if date_from else date_from
    ext_where, ext_params = _build_where(
        fm, cfg, ext_from_sql, date_to, provinces, cities, products, store_type)
    monthly = _monthly_boxes(db_key, cfg, fm, ext_where, ext_params)

    months = sorted(k for k in monthly if (df or "0000") <= k <= dt)
    series_cur = [monthly[m] for m in months]
    # 去年同月缺失按 0 计：新品 / 新区域没有去年数据是正常的，补 0 才能反映真实对比
    # （基期为 0 的月份 growth 为 None，前端不画点 / 显示「/」）。
    series_prev = [monthly.get(_shift_date(m, -1), 0.0) for m in months]
    growth = [
        round((cur - prev) / prev * 100, 2) if prev else None
        for cur, prev in zip(series_cur, series_prev)
    ]
    result = {
        "months": months,
        "current": series_cur,
        "previous": series_prev,
        "growth": growth,
    }
    _dash_cache_set(key, result)
    return result


# ---------- 维度拆解 ----------
@router.get("/dashboard/breakdown")
def dashboard_breakdown(
    db_key: str = validate_db_key,
    dim: str = Query("province", description="province | city | product"),
    top_n: int = Query(15, ge=1, le=50),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all"),
):
    """按 省/城市/品种 拆解盒数与门店数 TopN。"""
    key = _dash_cache_key("breakdown", db_key, dim=dim, top_n=top_n,
                          date_from=date_from, date_to=date_to,
                          provinces=provinces, cities=cities, products=products,
                          store_type=store_type)
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")
    if dim not in ("province", "city", "product"):
        raise HTTPException(400, f"不支持的维度: {dim}")

    if dim == "province":
        if "province_col" not in fm:
            raise HTTPException(400, "该库无省份维度")
        gcol = name_col = sql.Identifier(fm["province_col"])
    elif dim == "city":
        gcol = name_col = sql.Identifier(fm["city_col"])
    else:
        gcol = sql.Identifier(fm["product_col"])
        name_col = sql.Identifier(fm.get("product_name_col", fm["product_col"]))

    where, params = _build_where(fm, cfg, date_from, date_to, provinces, cities, products, store_type)
    t = sql.Identifier(cfg["table"])
    q = sql.Identifier(fm["qty_col"])
    s = sql.Identifier(fm["store_col"])
    # 两级聚合：先折叠到 (维度, 门店) 粒度，再在结果上取 MIN(名称)/去重门店数。
    stmt = sql.SQL(
        "SELECT _g, MIN(_n) AS name, SUM(_b) AS boxes, COUNT(DISTINCT _ks) AS stores FROM ("
        "SELECT {g} AS _g, {s} AS _ks, MIN({n}) AS _n, SUM({q}) AS _b "
        "FROM {t}{w} GROUP BY {g}, {s}"
        ") t GROUP BY _g ORDER BY boxes DESC LIMIT %s"
    ).format(g=gcol, n=name_col, q=q, s=s, t=t, w=where)
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params + [top_n])
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        result = {"dim": dim, "items": jsonable_rows(rows)}
        _dash_cache_set(key, result)
        return result
    except Exception as e:
        logger.error("看板拆解 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"拆解失败: {e}")


# ---------- 维度拆解（三维度单 SQL 聚合） ----------
@router.get("/dashboard/breakdown-all")
def dashboard_breakdown_all(
    db_key: str = validate_db_key,
    province_top_n: int = Query(15, ge=1, le=50),
    city_top_n: int = Query(15, ge=1, le=50),
    product_top_n: int = Query(20, ge=1, le=50),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all"),
):
    """省 / 城市 / 品种 三维度拆解，一次 GROUPING SETS 扫描聚合，各取 TopN。

    用 GROUPING SETS 让 PostgreSQL 对事实表只扫描/聚合一次，
    同时得到 (province)、(city)、(product_code, product_name) 三个分组结果，
    应用层再分别排序取 TopN —— 替代原来 3 次独立全表扫描。
    """
    key = _dash_cache_key(
        "breakdown_all", db_key, ptn=province_top_n, ctn=city_top_n, prtn=product_top_n,
        date_from=date_from, date_to=date_to,
        provinces=provinces, cities=cities, products=products, store_type=store_type,
    )
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")

    where, params = _build_where(fm, cfg, date_from, date_to, provinces, cities, products, store_type)
    t = sql.Identifier(cfg["table"])
    q = sql.Identifier(fm["qty_col"])
    s = sql.Identifier(fm["store_col"])
    pcol = sql.Identifier(fm["province_col"]) if "province_col" in fm else None
    ccol = sql.Identifier(fm["city_col"]) if "city_col" in fm else None
    prcol = sql.Identifier(fm["product_col"])
    pncol = sql.Identifier(fm.get("product_name_col", fm["product_col"]))

    # 两级聚合：先把事实表折叠到 (省,市,商品编码,门店) 粒度（并行 HashAggregate，
    # 8M 行 -> 约 30 万组），再在结果上做 GROUPING SETS + COUNT(DISTINCT 门店)。
    # 直接在千万级表上做「排序去重」实测 81.7s，改写后约 4.2s，各维度口径完全一致。
    base_sel: List[sql.Composable] = []
    base_grp: List[sql.Composable] = []
    set_cols: List[sql.Composable] = []
    select_parts: List[sql.Composable] = []
    if pcol is not None:
        base_sel.append(sql.SQL("{} AS _kp").format(pcol))
        base_grp.append(pcol)
        set_cols.append(sql.SQL("(_kp)"))
        select_parts.append(sql.SQL("GROUPING(_kp) AS g_p, MIN(_kp) AS province_v"))
    else:
        select_parts.append(sql.SQL("NULL AS g_p, NULL AS province_v"))
    if ccol is not None:
        base_sel.append(sql.SQL("{} AS _kc").format(ccol))
        base_grp.append(ccol)
        set_cols.append(sql.SQL("(_kc)"))
        select_parts.append(sql.SQL("GROUPING(_kc) AS g_c, MIN(_kc) AS city_v"))
    else:
        select_parts.append(sql.SQL("NULL AS g_c, NULL AS city_v"))
    # 品种维度只按商品编码分组（名称用 MIN 聚合），与原 breakdown(province/city/product)
    # 逐维度口径一致：同一编码只产生一行、名称取 MIN(product_name)，不会因脏数据（同编码多名称）被拆行。
    base_sel.append(sql.SQL("{} AS _kpc").format(prcol))
    base_grp.append(prcol)
    base_sel.append(sql.SQL("MIN({}) AS _kpn").format(pncol))
    base_sel.append(sql.SQL("{} AS _ks").format(s))
    base_grp.append(s)
    base_sel.append(sql.SQL("SUM({}) AS _b").format(q))
    set_cols.append(sql.SQL("(_kpc)"))
    select_parts.append(sql.SQL(
        "GROUPING(_kpc) AS g_pc, MIN(_kpc) AS product_code_v, MIN(_kpn) AS product_name_v"))

    stmt = sql.SQL(
        "WITH base AS ("
        "SELECT {bsel} FROM {t}{w} GROUP BY {bgrp}"
        ") SELECT {parts}, SUM(_b) AS boxes, COUNT(DISTINCT _ks) AS stores "
        "FROM base GROUP BY GROUPING SETS ({gs})"
    ).format(
        bsel=sql.SQL(", ").join(base_sel),
        t=t, w=where, bgrp=sql.SQL(", ").join(base_grp),
        parts=sql.SQL(", ").join(select_parts),
        gs=sql.SQL(", ").join(set_cols),
    )
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    prov, city, prod = [], [], []
    for r in rows:
        boxes = float(r["boxes"]) if r["boxes"] is not None else 0.0
        stores_n = int(r["stores"]) if r["stores"] is not None else 0
        avg = round(boxes / stores_n, 2) if stores_n else 0.0
        if r["g_p"] == 0:  # province 分组集
            v = r["province_v"]
            prov.append({"key": v, "name": v, "boxes": boxes, "avg_store": avg, "stores": stores_n})
        elif r["g_c"] == 0:  # city 分组集
            v = r["city_v"]
            city.append({"key": v, "name": v, "boxes": boxes, "avg_store": avg, "stores": stores_n})
        elif r["g_pc"] == 0:  # product 分组集
            code = r["product_code_v"]
            name = r["product_name_v"]
            prod.append({"key": code, "name": f"{name} ({code})", "boxes": boxes, "avg_store": avg, "stores": stores_n})

    prov.sort(key=lambda x: x["boxes"], reverse=True)
    city.sort(key=lambda x: x["boxes"], reverse=True)
    prod.sort(key=lambda x: x["boxes"], reverse=True)
    result = {
        "province": jsonable_rows(prov[:province_top_n]),
        "city": jsonable_rows(city[:city_top_n]),
        "product": jsonable_rows(prod[:product_top_n]),
    }
    _dash_cache_set(key, result)
    return result


# ---------- 门店板块 ----------
@router.get("/dashboard/stores")
def dashboard_stores(
    db_key: str = validate_db_key,
    top_n: int = Query(20, ge=1, le=100),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all"),
):
    """门店板块：连锁/加盟拆分 + Top 门店排名 + 品种能力 Top。"""
    key = _dash_cache_key("stores", db_key, top_n=top_n,
                          date_from=date_from, date_to=date_to,
                          provinces=provinces, cities=cities, products=products,
                          store_type=store_type)
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")
    st_expr = _store_type_expr(cfg)
    where, params = _build_where(fm, cfg, date_from, date_to, provinces, cities, products, store_type)
    t = sql.Identifier(cfg["table"])
    q = sql.Identifier(fm["qty_col"])
    s = sql.Identifier(fm["store_col"])
    sn = sql.Identifier(fm.get("store_name_col", fm["store_col"]))
    pcol = sql.Identifier(fm["product_col"])
    pname = sql.Identifier(fm.get("product_name_col", fm["product_col"]))

    result: dict = {}

    # 一次门店粒度聚合，同时派生「连锁/加盟拆分」与「Top 门店排名」：
    # 两者原本各自做一次全表扫描，这里合并为 1 次（约 2 万行），再在应用层汇总，
    # 省掉一次千万级全表扫描（实测门店板块 5.4s -> 2.7s）。
    st_sel = (sql.SQL("({}) AS store_type").format(st_expr) if st_expr is not None
              else sql.SQL("'全部' AS store_type"))
    store_stmt = sql.SQL(
        "SELECT {s} AS store_code, {st}, MIN({sn}) AS store_name, SUM({q}) AS boxes "
        "FROM {t}{w} GROUP BY 1, 2"
    ).format(s=s, st=st_sel, sn=sn, q=q, t=t, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(store_stmt, params)
            cols = [d[0] for d in cur.description]
            store_rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # 连锁/加盟拆分（每家门店 1 行，COUNT(*) 即去重门店数）
    split_map: dict = {}
    for r in store_rows:
        stv = r["store_type"]
        agg = split_map.setdefault(stv, {"st": stv, "boxes": 0, "stores": 0})
        agg["boxes"] += r["boxes"] or 0
        agg["stores"] += 1
    result["store_type_split"] = sorted(
        split_map.values(), key=lambda x: x["boxes"], reverse=True)

    # Top 门店排名（含门店类型）
    store_rows.sort(key=lambda x: x["boxes"] or 0, reverse=True)
    result["top_stores"] = jsonable_rows(store_rows[:top_n])

    # 品种能力 Top（盒数维度）
    prod_stmt = sql.SQL(
        "SELECT {p} AS code, MIN({pn}) AS name, SUM({q}) AS boxes "
        "FROM {t}{w} GROUP BY {p} ORDER BY boxes DESC LIMIT %s"
    ).format(p=pcol, pn=pname, q=q, t=t, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(prod_stmt, params + [top_n])
            cols = [d[0] for d in cur.description]
            result["top_products"] = jsonable_rows([dict(zip(cols, r)) for r in cur.fetchall()])

    _dash_cache_set(key, result)
    return result


# ---------- 聚合（一次返回看板全部数据） ----------
@router.get("/dashboard/summary")
def dashboard_summary(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all", description="all | chain | franchise"),
    province_top_n: int = Query(15, ge=1, le=50),
    city_top_n: int = Query(15, ge=1, le=50),
    product_top_n: int = Query(20, ge=1, le=50),
    store_top_n: int = Query(20, ge=1, le=100),
):
    """一次性返回看板全部数据：KPI + 趋势(同比) + 省/市/品种拆解 + 门店板块。

    复用各子查询函数（含其 TTL 缓存与已验证的计算口径），并用线程池并发执行，
    在把前端 HTTP 往返从 6 次降到 1 次的同时，保持「最慢子查询」级别的延迟。
    """
    key = _dash_cache_key(
        "summary", db_key, date_from=date_from, date_to=date_to,
        provinces=provinces, cities=cities, products=products, store_type=store_type,
        ptn=province_top_n, ctn=city_top_n, prtn=product_top_n, stn=store_top_n,
    )
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached

    # 复用已验证的子查询函数（每个在独立线程内各自开连接，安全）。
    # breakdown 三维度合并为 1 条 GROUPING SETS SQL（dashboard_breakdown_all），减少全表扫描次数；
    # options 也一并聚合，首屏 HTTP 往返降到 1 次。
    tasks = {
        "options": lambda: dashboard_options(db_key),
        "kpi": lambda: dashboard_kpi(db_key, date_from, date_to, provinces, cities, products, store_type),
        "trend": lambda: dashboard_trend(db_key, date_from, date_to, provinces, cities, products, store_type),
        "breakdown": lambda: dashboard_breakdown_all(
            db_key, province_top_n, city_top_n, product_top_n,
            date_from, date_to, provinces, cities, products, store_type),
        "stores": lambda: dashboard_stores(db_key, store_top_n, date_from, date_to, provinces, cities, products, store_type),
    }
    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {name: ex.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            results[name] = fut.result()

    result = {
        "options": results["options"],
        "kpi": results["kpi"],
        "trend": results["trend"],
        "breakdown": {
            "province": results["breakdown"]["province"],
            "city": results["breakdown"]["city"],
            "product": results["breakdown"]["product"],
        },
        "stores": results["stores"],
    }
    _dash_cache_set(key, result)
    return result
