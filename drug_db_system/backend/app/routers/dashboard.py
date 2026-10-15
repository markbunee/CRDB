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
from datetime import datetime
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import get_cfg, get_field_map, supports_inventory
from ..services import stats_cache
from ..database import get_conn
from ..services.product_map import get_product_map_full
from ..services.ttl_cache import TTLCache
from ..utils.serialization import jsonable_rows
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["dashboard"])
logger = get_logger("app.routers.dashboard")


# ---------- 结果缓存（TTL + 容量上限） ----------
# 看板是读多写少的静态分析数据，但单次查询可能扫描千万级大表。
# 加 TTL 缓存可避免「同一筛选反复全表扫描」，显著减轻小内存服务器压力。
# 数据变更（导入/清空/映射改价）时由对应接口调用 invalidate_dashboard_cache 主动失效。
# 数据导入/清空会主动失效，故 TTL 可放长，避免用户隔几分钟再打开看板时缓存已过期；
# 同时用 max_entries 兜住「筛选组合过多导致缓存膨胀」的内存风险。
_DASH_CACHE = TTLCache(
    ttl=int(os.environ.get("DASHBOARD_CACHE_TTL", "3600")),
    max_entries=int(os.environ.get("DASHBOARD_CACHE_MAX_ENTRIES", "150")),
    name="dashboard",
)


def _dash_cache_key(name: str, db_key: str, **kw) -> str:
    items = [f"{k}={kw.get(k)}" for k in sorted(kw)]
    return "|".join([name, db_key] + items)


def _dash_cache_get(key: str):
    return _DASH_CACHE.get(key)


def _dash_cache_set(key: str, value):
    _DASH_CACHE.set(key, value)


def invalidate_dashboard_cache(_db_key: str | None = None):
    """数据写入（导入/清空/映射改价）后调用，清空看板缓存。

    _db_key 当前保留以便将来按需按库失效；现在看板数据量小，直接整体清空。

    这里顺带清空**统计结果缓存**：所有数据写入点都已调用本函数，在此统一失效
    可以保证不会漏掉某个入口导致统计返回旧数据（符合「数据一变、派生缓存全失效」）。
    """
    _DASH_CACHE.clear()
    stats_cache.invalidate_stats_cache(_db_key)


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


# ---------- 金额口径（数量 × 品类开票价） ----------
def _amount_sql(db_key: str, cfg: dict, fm: dict):
    """返回 (join_sql, amount_expr, extra_params)。

    无开票价映射时返回 None，此时 dashboard 金额字段统一为 0/None。
    实现与 stats_box.py 一致：用 VALUES 常量集 LEFT JOIN，未配置价格的品类按 0 计。
    """
    full_map = get_product_map_full(db_key)
    pvals = [
        (code, float(v["price"]))
        for code, v in full_map.items()
        if v.get("price") is not None
    ]
    if not pvals:
        return None
    join_sql = sql.SQL(
        " LEFT JOIN (VALUES {}) AS p(code, price) ON CAST({t}.{pc} AS TEXT) = p.code"
    ).format(
        sql.SQL(", ").join(sql.SQL("(%s::text, %s::numeric)") for _ in pvals),
        t=sql.Identifier(cfg["table"]),
        pc=sql.Identifier(fm["product_col"]),
    )
    amount_expr = sql.SQL("SUM({q} * COALESCE(p.price, 0))").format(
        q=sql.Identifier(fm["qty_col"])
    )
    extra_params = [x for pair in pvals for x in pair]
    return join_sql, amount_expr, extra_params


def _monthly_amounts(
    db_key: str, cfg: dict, fm: dict, where: sql.Composable, params: list,
) -> dict:
    """按月聚合金额（元），返回 { 'YYYY-MM': amount }。where 已含时间窗与 JOIN 参数。"""
    amount_sql = _amount_sql(db_key, cfg, fm)
    if amount_sql is None:
        return {}
    join_sql, amount_expr, extra_params = amount_sql
    mc = sql.Identifier(fm["month_col"])
    t = sql.Identifier(cfg["table"])
    stmt = sql.SQL(
        "SELECT to_char(date_trunc('month', {mc}), 'YYYY-MM') AS m, "
        "{amt} AS amount FROM {t}{join}{w} GROUP BY m ORDER BY m"
    ).format(mc=mc, amt=amount_expr, t=t, join=join_sql, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, extra_params + params)
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    return {r["m"]: (float(r["amount"]) if r["amount"] is not None else 0.0) for r in rows}


def _monthly_boxes_amounts(
    db_key: str, cfg: dict, fm: dict, where: sql.Composable, params: list,
) -> tuple:
    """一次扫描同时得到盒数/金额的月度字典，返回 (boxes, amounts)。

    把原先 `_monthly_boxes` + `_monthly_amounts` 两次千万级扫描合并为一次（KPI 同比用）。
    """
    mc = sql.Identifier(fm["month_col"])
    q = sql.Identifier(fm["qty_col"])
    t = sql.Identifier(cfg["table"])
    amount_sql = _amount_sql(db_key, cfg, fm)
    if amount_sql is not None:
        join_sql, amount_expr, extra_params = amount_sql
        amount_sel = sql.SQL(", {} AS amount").format(amount_expr)
    else:
        join_sql = sql.SQL("")
        extra_params = []
        amount_sel = sql.SQL(", NULL AS amount")
    stmt = sql.SQL(
        "SELECT to_char(date_trunc('month', {mc}), 'YYYY-MM') AS m, "
        "SUM({q}) AS boxes{amt} FROM {t}{join}{w} GROUP BY m ORDER BY m"
    ).format(mc=mc, q=q, amt=amount_sel, t=t, join=join_sql, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, extra_params + params)
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    boxes = {r["m"]: (float(r["boxes"]) if r["boxes"] is not None else 0.0) for r in rows}
    amounts = {r["m"]: (float(r["amount"]) if r["amount"] is not None else 0.0) for r in rows}
    return boxes, amounts, (amount_sql is not None)


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


def _default_range(db_key: str, cfg: dict, fm: dict) -> tuple:
    """默认区间：今年 1 月 1 日 ~ 数据最新月（首个自然日）。

    首屏请求不带日期时由后端直接落到该区间，避免前端「先全量再按默认区间重查」
    造成的两次千万级聚合（这是看板打开慢的主因）。
    数据起始月晚于今年 1 月时，从数据起始月开始。
    返回 ('YYYY-MM-DD', 'YYYY-MM-DD')；无数据返回 (None, None)。
    """
    mn, mx = _month_range(db_key, cfg, fm)
    if not mx:
        return None, None
    year_start = f"{datetime.now().year:04d}-01"
    from_ym = mn if (mn and mn > year_start) else year_start
    if from_ym > mx:
        from_ym = mx
    return f"{from_ym}-01", f"{mx}-01"


def _month_list(df: str, dt: str) -> list:
    """生成闭区间 ['YYYY-MM', ...]，用于补齐无数据的月份。"""
    out = []
    if not (df and dt):
        return out
    y, m = int(df[:4]), int(df[5:7])
    ey, em = int(dt[:4]), int(dt[5:7])
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 1
    if m < 1:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _monthly_stores(
    db_key: str, cfg: dict, fm: dict, where: sql.Composable, params: list,
) -> dict:
    """按月聚合去重门店数，返回 { 'YYYY-MM': stores }。where 已含时间窗。"""
    mc = sql.Identifier(fm["month_col"])
    s = sql.Identifier(fm["store_col"])
    t = sql.Identifier(cfg["table"])
    stmt = sql.SQL(
        "SELECT to_char(date_trunc('month', {mc}), 'YYYY-MM') AS m, "
        "COUNT(DISTINCT {s}) AS stores FROM {t}{w} GROUP BY m ORDER BY m"
    ).format(mc=mc, s=s, t=t, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    return {r["m"]: int(r["stores"]) if r["stores"] is not None else 0 for r in rows}


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
        "supports_inventory": supports_inventory(db_key),
    }
    _dash_cache_set(key, result)
    return result


def _options_from_breakdown(db_key: str, cfg: dict, fm: dict, breakdown: dict) -> dict:
    """用已算好的三维度拆解结果拼出筛选选项，避免再跑 3 次 DISTINCT 全表扫描。

    仅在「无任何筛选」的首屏使用（此时 breakdown 即全量维度），语义与 dashboard_options 一致。
    """
    mn, mx = _month_range(db_key, cfg, fm)
    products = []
    for p in breakdown.get("product", []):
        name = str(p.get("name") or "")
        # breakdown 的产品名格式为「商品名 (编码)」，去掉尾部编码还原纯名称
        if name.endswith(")") and " (" in name:
            name = name.rsplit(" (", 1)[0]
        products.append({"code": str(p.get("key")), "name": name})
    return {
        "provinces": [p.get("name") for p in breakdown.get("province", []) if p.get("name")],
        "cities": [c.get("name") for c in breakdown.get("city", []) if c.get("name")],
        "products": products,
        "month_range": {
            "min": f"{mn}-01" if mn else None,
            "max": f"{mx}-01" if mx else None,
        },
        "supports_store_type": _store_type_expr(cfg) is not None,
        "supports_inventory": supports_inventory(db_key),
    }


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
    - yoy_pct     盒数同比增长率；去年同期销量为 0（无数据）时为 None，前端显示「/」
    - amount      金额合计（SUM 数量 × 开票价），未配置开票价映射的库为 None
    - yoy_amount  去年同期金额
    - yoy_amount_pct 金额同比增长率
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
    monthly, monthly_amount, has_amount = _monthly_boxes_amounts(
        db_key, cfg, fm, ext_where, ext_params)

    # 盒数：用月级别比较，避免日级字符串与月级 key 字典序错位（修复起始月丢失）
    current_boxes = sum(v for k, v in monthly.items() if (df or "0000") <= k <= dt)

    # 金额：与盒数同窗口
    current_amount = sum(v for k, v in monthly_amount.items() if (df or "0000") <= k <= dt)

    # 同比：严格按「用户当前拉取的区间」整体前移一年比较，不做任何按数据范围的裁剪。
    # 去年同期缺失的月份按 0 计（新品 / 新区域本就没有去年数据，属正常业务含义）；
    # 基期为 0 时 yoy_pct 置 None，前端显示「/」。
    if yoy:
        prev_from, prev_to = yoy
        previous_boxes = sum(v for k, v in monthly.items() if prev_from <= k <= prev_to)
        yoy_pct = round((current_boxes - previous_boxes) / previous_boxes * 100, 2) if previous_boxes else None
        previous_amount = sum(v for k, v in monthly_amount.items() if prev_from <= k <= prev_to)
        yoy_amount_pct = round((current_amount - previous_amount) / previous_amount * 100, 2) if previous_amount else None
    else:
        previous_boxes = None
        yoy_pct = None
        previous_amount = None
        yoy_amount_pct = None

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
        "amount": current_amount if has_amount else None,
        "yoy_amount": previous_amount if has_amount else None,
        "yoy_amount_pct": yoy_amount_pct if has_amount else None,
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
    # 金额口径（数量 × 开票价）：与盒数一并算出，前端按「指标」切换时无需重新请求。
    amounts = _monthly_amounts(db_key, cfg, fm, ext_where, ext_params)

    months = sorted(k for k in monthly if (df or "0000") <= k <= dt)

    # 去年同月缺失按 0 计：新品 / 新区域没有去年数据是正常的，补 0 才能反映真实对比
    # （基期为 0 的月份 growth 为 None，前端不画点 / 显示「/」）。
    def _build_series(mdict):
        cur = [mdict.get(m, 0.0) for m in months]
        prev = [mdict.get(_shift_date(m, -1), 0.0) for m in months]
        growth = [
            round((c - p) / p * 100, 2) if p else None
            for c, p in zip(cur, prev)
        ]
        return cur, prev, growth

    cur_b, prev_b, gr_b = _build_series(monthly)
    if amounts:
        cur_a, prev_a, gr_a = _build_series(amounts)
    else:
        # 无开票价映射：金额口径整体不可用，金额序列置空，避免前端切到金额时画出「0」误导。
        cur_a = prev_a = [None] * len(months)
        gr_a = [None] * len(months)
    result = {
        "months": months,
        "current": cur_b,
        "previous": prev_b,
        "growth": gr_b,
        "current_amount": cur_a,
        "previous_amount": prev_a,
        "growth_amount": gr_a,
    }
    _dash_cache_set(key, result)
    return result


# ---------- 维度拆解 ----------
@router.get("/dashboard/breakdown")
def dashboard_breakdown(
    db_key: str = validate_db_key,
    dim: str = Query("province", description="province | city | product"),
    top_n: int = Query(15, ge=1, le=2000),
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
    # 金额口径（数量 × 开票价），无开票价映射时返回 NULL
    amount_sql = _amount_sql(db_key, cfg, fm)
    if amount_sql is not None:
        join_sql, amount_expr, extra_params = amount_sql
        inner_amount = sql.SQL(", {} AS _a").format(amount_expr)
        outer_amount = sql.SQL(", SUM(_a) AS amount")
    else:
        join_sql = sql.SQL("")
        extra_params = []
        inner_amount = sql.SQL("")
        outer_amount = sql.SQL(", NULL AS amount")
    # 两级聚合：先折叠到 (维度, 门店) 粒度，再在结果上取 MIN(名称)/去重门店数。
    stmt = sql.SQL(
        "SELECT _g, MIN(_n) AS name, SUM(_b) AS boxes, COUNT(DISTINCT _ks) AS stores{oa} FROM ("
        "SELECT {g} AS _g, {s} AS _ks, MIN({n}) AS _n, SUM({q}) AS _b{ia} "
        "FROM {t}{join}{w} GROUP BY {g}, {s}"
        ") t GROUP BY _g ORDER BY boxes DESC LIMIT %s"
    ).format(g=gcol, n=name_col, q=q, s=s, ia=inner_amount, oa=outer_amount,
             t=t, join=join_sql, w=where)
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, extra_params + params + [top_n])
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        result = {"dim": dim, "items": jsonable_rows(rows)}
        _dash_cache_set(key, result)
        return result
    except Exception as e:
        logger.error("看板拆解 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"拆解失败: {e}")


# ---------- 品种逐月明细（销量 / 金额 / 门店数 / 同比 / 环比） ----------
@router.get("/dashboard/product-detail")
def dashboard_product_detail(
    db_key: str = validate_db_key,
    code: str = Query(..., description="商品编码"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    store_type: str = Query("all"),
):
    """单个品种的逐月明细：销量(盒)、金额、门店数、同比、环比。

    一次拉回「当前区间 + 去年同期 + 上一个月」的月度聚合，再在内存拆分，
    避免为同比/环比各开一次查询。
    """
    key = _dash_cache_key(
        "product_detail", db_key, code=code, date_from=date_from, date_to=date_to,
        provinces=provinces, cities=cities, store_type=store_type,
    )
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")

    df = _to_month(date_from)
    dt = _to_month(date_to)
    if not (df and dt):
        d_from, d_to = _default_range(db_key, cfg, fm)
        df, dt = _to_month(d_from), _to_month(d_to)
    empty = {
        "code": code, "name": None, "months": [], "boxes": [], "amount": [],
        "stores": [], "yoy_pct": [], "mom_pct": [],
        "total_boxes": 0, "total_amount": 0, "total_stores": 0,
    }
    if not (df and dt):
        _dash_cache_set(key, empty)
        return empty

    months = _month_list(df, dt)
    # 扩展窗：往前推一年，覆盖同比；环比（上月）自然落在窗口内
    ext_from = f"{_shift_date(df, -1)}-01"
    ext_to = f"{dt}-01"
    ext_where, ext_params = _build_where(
        fm, cfg, ext_from, ext_to, provinces, cities, code, store_type)
    boxes = _monthly_boxes(db_key, cfg, fm, ext_where, ext_params)
    amounts = _monthly_amounts(db_key, cfg, fm, ext_where, ext_params)
    stores = _monthly_stores(db_key, cfg, fm, ext_where, ext_params)

    # 品种名称（区间内取 MIN）
    t = sql.Identifier(cfg["table"])
    pncol = sql.Identifier(fm.get("product_name_col", fm["product_col"]))
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT MIN({n}) FROM {t}{w}").format(n=pncol, t=t, w=ext_where),
                ext_params,
            )
            name_row = cur.fetchone()
    name = name_row[0] if name_row else None

    # 当前窗口去重门店数（不能按月度相加）
    cur_where, cur_params = _build_where(
        fm, cfg, f"{df}-01", f"{dt}-01", provinces, cities, code, store_type)
    s_col = sql.Identifier(fm["store_col"])
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT COUNT(DISTINCT {s}) FROM {t}{w}").format(
                    s=s_col, t=t, w=cur_where),
                cur_params,
            )
            total_stores = int(cur.fetchone()[0] or 0)

    def pct(cur_v: float, base_v: float):
        if base_v:
            return round((cur_v - base_v) / base_v * 100, 2)
        return None

    bx, am, st, yoy, mom = [], [], [], [], []
    for m in months:
        v = boxes.get(m, 0.0)
        bx.append(v)
        am.append(amounts.get(m, 0.0))
        st.append(stores.get(m, 0))
        yoy.append(pct(v, boxes.get(_shift_date(m, -1), 0.0)))
        mom.append(pct(v, boxes.get(_prev_month(m), 0.0)))

    result = {
        "code": code,
        "name": name,
        "months": months,
        "boxes": bx,
        "amount": am,
        "stores": st,
        "yoy_pct": yoy,
        "mom_pct": mom,
        "total_boxes": round(sum(bx), 2),
        "total_amount": round(sum(am), 2),
        "total_stores": total_stores,
    }
    _dash_cache_set(key, result)
    return result


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
    amount_sql = _amount_sql(db_key, cfg, fm)
    if amount_sql is not None:
        join_sql, amount_expr, extra_params = amount_sql
    else:
        join_sql = sql.SQL("")
        extra_params = []
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
        # 城市分组集也保留 province_v（MIN 聚合），供前端「拆分城市维度」时知道属哪个省
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
    if amount_sql is not None:
        base_sel.append(sql.SQL("{} AS _a").format(amount_expr))
    set_cols.append(sql.SQL("(_kpc)"))
    select_parts.append(sql.SQL(
        "GROUPING(_kpc) AS g_pc, MIN(_kpc) AS product_code_v, MIN(_kpn) AS product_name_v"))

    stmt = sql.SQL(
        "WITH base AS ("
        "SELECT {bsel} FROM {t}{join}{w} GROUP BY {bgrp}"
        ") SELECT {parts}, SUM(_b) AS boxes, COUNT(DISTINCT _ks) AS stores "
        "{amount_sel} FROM base GROUP BY GROUPING SETS ({gs})"
    ).format(
        bsel=sql.SQL(", ").join(base_sel),
        t=t, join=join_sql, w=where, bgrp=sql.SQL(", ").join(base_grp),
        parts=sql.SQL(", ").join(select_parts),
        amount_sel=sql.SQL(", SUM(_a) AS amount") if amount_sql is not None else sql.SQL(", NULL AS amount"),
        gs=sql.SQL(", ").join(set_cols),
    )
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, extra_params + params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    prov, city, prod = [], [], []
    for r in rows:
        boxes = float(r["boxes"]) if r["boxes"] is not None else 0.0
        amount = float(r["amount"]) if r["amount"] is not None else 0.0
        stores_n = int(r["stores"]) if r["stores"] is not None else 0
        avg = round(boxes / stores_n, 2) if stores_n else 0.0
        if r["g_p"] == 0:  # province 分组集
            v = r["province_v"]
            prov.append({"key": v, "name": v, "boxes": boxes, "amount": amount, "avg_store": avg, "stores": stores_n})
        elif r["g_c"] == 0:  # city 分组集
            v = r["city_v"]
            province_v = r["province_v"]
            city.append({"key": v, "name": v, "province": province_v, "boxes": boxes, "amount": amount, "avg_store": avg, "stores": stores_n})
        elif r["g_pc"] == 0:  # product 分组集
            code = r["product_code_v"]
            name = r["product_name_v"]
            prod.append({"key": code, "name": f"{name} ({code})", "boxes": boxes, "amount": amount, "avg_store": avg, "stores": stores_n})

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
    amount_sql = _amount_sql(db_key, cfg, fm)
    if amount_sql is not None:
        join_sql, amount_expr, extra_params = amount_sql
        amount_sel = sql.SQL(", {} AS amount").format(amount_expr)
    else:
        join_sql = sql.SQL("")
        extra_params = []
        amount_sel = sql.SQL(", NULL AS amount")
    st_sel = (sql.SQL("({}) AS store_type").format(st_expr) if st_expr is not None
              else sql.SQL("'全部' AS store_type"))
    store_stmt = sql.SQL(
        "SELECT {s} AS store_code, {st}, MIN({sn}) AS store_name, SUM({q}) AS boxes {amt} "
        "FROM {t}{join}{w} GROUP BY 1, 2"
    ).format(s=s, st=st_sel, sn=sn, q=q, amt=amount_sel, t=t, join=join_sql, w=where)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(store_stmt, extra_params + params)
            cols = [d[0] for d in cur.description]
            store_rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # 连锁/加盟拆分（每家门店 1 行，COUNT(*) 即去重门店数）
    split_map: dict = {}
    for r in store_rows:
        stv = r["store_type"]
        agg = split_map.setdefault(stv, {"st": stv, "boxes": 0, "amount": 0, "stores": 0})
        agg["boxes"] += r["boxes"] or 0
        agg["amount"] += float(r["amount"]) if r["amount"] is not None else 0
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
    province_top_n: int = Query(100, ge=1, le=2000),
    city_top_n: int = Query(500, ge=1, le=2000),
    product_top_n: int = Query(500, ge=1, le=2000),
    store_top_n: int = Query(20, ge=1, le=100),
    include_stores: bool = Query(True, description="是否计算门店板块（首屏可先跳过，异步补齐）"),
):
    """一次性返回看板全部数据：KPI + 趋势(同比) + 省/市/品种拆解 + 门店板块。

    复用各子查询函数（含其 TTL 缓存与已验证的计算口径），并用线程池并发执行，
    在把前端 HTTP 往返从 6 次降到 1 次的同时，保持「最慢子查询」级别的延迟。
    """
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持看板: {db_key}")
    # 首屏未传日期时直接落到「今年 1 月 ~ 最新月」，避免前端二次全量重查
    applied_range = None
    if not (date_from and date_to):
        d_from, d_to = _default_range(db_key, cfg, fm)
        if d_from and d_to:
            date_from, date_to = d_from, d_to
            applied_range = {"date_from": d_from, "date_to": d_to}
    key = _dash_cache_key(
        "summary", db_key, date_from=date_from, date_to=date_to,
        provinces=provinces, cities=cities, products=products, store_type=store_type,
        ptn=province_top_n, ctn=city_top_n, prtn=product_top_n, stn=store_top_n,
        inc_stores=include_stores,
    )
    cached = _dash_cache_get(key)
    if cached is not None:
        return cached

    # 复用已验证的子查询函数（每个在独立线程内各自开连接，安全）。
    # breakdown 三维度合并为 1 条 GROUPING SETS SQL（dashboard_breakdown_all），减少全表扫描次数；
    # options 也一并聚合，首屏 HTTP 往返降到 1 次。
    # 门店板块最重（单表门店粒度聚合），首屏可用 include_stores=false 跳过、随后单独补齐，
    # 避免它与其它查询争抢数据库资源拖慢首屏。
    tasks = {
        "kpi": lambda: dashboard_kpi(db_key, date_from, date_to, provinces, cities, products, store_type),
        "trend": lambda: dashboard_trend(db_key, date_from, date_to, provinces, cities, products, store_type),
        "breakdown": lambda: dashboard_breakdown_all(
            db_key, province_top_n, city_top_n, product_top_n,
            date_from, date_to, provinces, cities, products, store_type),
    }
    if include_stores:
        tasks["stores"] = lambda: dashboard_stores(
            db_key, store_top_n, date_from, date_to, provinces, cities, products, store_type)
    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {name: ex.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            results[name] = fut.result()

    # 选项：优先命中缓存；无筛选时直接用 breakdown 结果拼（省掉 3 次 DISTINCT 扫描），
    # 有筛选且未命中缓存时退回 dashboard_options（极少发生，选项通常在首屏已缓存）。
    opt_key = _dash_cache_key("options", db_key)
    options = _dash_cache_get(opt_key)
    if options is None:
        if not (provinces or cities or products):
            options = _options_from_breakdown(db_key, cfg, fm, results["breakdown"])
            _dash_cache_set(opt_key, options)
        else:
            options = dashboard_options(db_key)

    result = {
        "options": options,
        "kpi": results["kpi"],
        "trend": results["trend"],
        "breakdown": {
            "province": results["breakdown"]["province"],
            "city": results["breakdown"]["city"],
            "product": results["breakdown"]["product"],
        },
        "stores": results.get("stores"),
        "applied_range": applied_range,
    }
    _dash_cache_set(key, result)
    return result
