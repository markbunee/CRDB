# -*- coding: utf-8 -*-
"""AI 聚合层：多指标一次扫描的 SQL 构造（供 ai_executor 调用）。

单独成文件的原因：这段是整套 AI 里**唯一直接拼 SQL**的地方，
也是最需要被仔细审查/调优的部分（扫描量、参数顺序、去重语义）。
把它与「能力分派」「回答渲染」隔开，改 SQL 时不会碰到话术，改话术时不会碰 SQL。

核心优化见 _fetch_multi 的注释：盒数 / 金额 / 门店数 × N 个时间窗 = 一条 SQL。
"""
from typing import List, Optional, Tuple

from psycopg2 import sql

from ..database import get_conn
from ..logger import get_logger
from ..services.product_map import get_priced_codes

logger = get_logger("app.services.ai_agg")


# ---------- 多指标一次扫描（核心优化） ----------

def _build_where(layout, windows: List[Tuple[str, str]], store_type: str):
    """并集时间区间 + 品类 / 地域 / 门店类型 的 WHERE（两个聚合查询共用，保证同口径）。

    用「各窗并集」而不是逐窗查询：扫描量从「各窗之和」降到「并集」，
    窗口之间的区分交给 SELECT 里的 CASE WHEN（见 _fetch_multi）。
    """
    fm = layout.fm
    date_id = sql.Identifier(fm["date_col"])
    lo = min(w[0] for w in windows)
    hi = max(w[1] for w in windows)
    conditions = [
        sql.SQL("{} >= %s").format(date_id),
        sql.SQL("{} <= %s").format(date_id),
    ]
    params: list = [lo, hi]
    if layout.product_list:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(fm["product_col"]),
                sql.SQL(", ").join(sql.Placeholder() * len(layout.product_list)),
            )
        )
        params.extend(layout.product_list)
    if layout.region_list:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(layout.region_col),
                sql.SQL(", ").join(sql.Placeholder() * len(layout.region_list)),
            )
        )
        params.extend(layout.region_list)
    st_expr = _store_type_expr(layout.cfg)
    if st_expr is not None and store_type in ("chain", "franchise"):
        conditions.append(sql.SQL("({}) = %s").format(st_expr))
        params.append("连锁" if store_type == "chain" else "加盟")
    return conditions, params


def _fetch_multi(
    db_key: str,
    layout,
    windows: List[Tuple[str, str]],
    store_type: str,
) -> List[dict]:
    """一条 SQL 算出 盒数 / 金额 / 动销门店数 × N 个时间窗。

    与 stats_box._fetch_raw_windows 同思路（三窗并集一次扫描），
    差别在于每个窗口同时产 3 个指标列 + 1 个原始行计数列。

    注意：分组内的门店数**不可跨组相加**（同一门店会落进多个分组被重复计），
    拆表模式下由 _distinct_stores_multi() 单独去重。
    """
    fm = layout.fm
    qty = sql.Identifier(fm["qty_col"])
    date_id = sql.Identifier(fm["date_col"])
    pcol = sql.Identifier(fm["product_col"])

    conditions, params = _build_where(layout, windows, store_type)
    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)

    # ---- 开票价 JOIN（金额口径）----
    pvals = get_priced_codes(db_key)
    join_sql = sql.SQL("")
    extra_params: list = []
    if pvals:
        join_sql = sql.SQL(
            "LEFT JOIN (VALUES {}) AS p(code, price) ON CAST({}.{} AS TEXT) = p.code"
        ).format(
            sql.SQL(", ").join(sql.SQL("(%s::TEXT, %s::NUMERIC)") for _ in pvals),
            sql.Identifier(layout.table),
            pcol,
        )
        extra_params = [x for pair in pvals for x in pair]

    val_boxes = qty
    val_amount = sql.SQL("{} * COALESCE(p.price, 0)").format(qty)

    fields = list(layout.group_cols)
    win_params: list = []
    for i, (df_, dt_) in enumerate(windows):
        cond = sql.SQL("{d} >= %s AND {d} <= %s").format(d=date_id)
        fields += [
            sql.SQL("COALESCE(SUM(CASE WHEN {c} THEN {v} ELSE 0 END), 0) AS {a}").format(
                c=cond, v=val_boxes, a=sql.SQL("w%d_boxes" % i)),
            sql.SQL("COALESCE(SUM(CASE WHEN {c} THEN {v} ELSE 0 END), 0) AS {a}").format(
                c=cond, v=val_amount, a=sql.SQL("w%d_amount" % i)),
            # 门店数**刻意不在这里算**：COUNT(DISTINCT) 要比 SUM 贵一个量级
            #（实测同条件 SUM 0.61s vs COUNT(DISTINCT)×2 3.10s），
            # 放进主 SQL 会把「只要盒数/金额」的查询也一起拖慢。
            # 拆表模式下它本来就不可跨组相加，统一交给 _distinct_stores_multi()。
            sql.SQL("COUNT(CASE WHEN {c} THEN 1 END) AS {a}").format(
                c=cond, a=sql.SQL("n%d" % i)),
        ]
        win_params.extend([df_, dt_] * 3)  # 每个窗口 3 个字段各用一份 (from,to)

    group_sql = (
        sql.SQL(" GROUP BY ") + sql.SQL(", ").join(layout.group_cols)
        if layout.group_cols else sql.SQL("")
    )
    stmt = sql.SQL("SELECT {fields} FROM {table}{join}{where}{group}").format(
        fields=sql.SQL(", ").join(fields),
        table=sql.Identifier(layout.table),
        join=join_sql,
        where=where,
        group=group_sql,
    )

    exec_params = win_params + extra_params + params
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, exec_params)
                colnames = [d[0] for d in cur.description]
                return [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("AI 多指标聚合失败 %s: %s", db_key, e)
        raise


def _store_type_expr(cfg: dict) -> Optional[sql.Composable]:
    if any(c[0] == "大区" for c in cfg["columns"]) and any(c[0] == "营运区" for c in cfg["columns"]):
        return sql.SQL(
            "CASE WHEN {daqu} LIKE '%%加盟%%' OR {yingyun} LIKE '%%加盟%%' "
            "THEN '加盟' ELSE '连锁' END"
        ).format(daqu=sql.Identifier("大区"), yingyun=sql.Identifier("营运区"))
    return None


def _distinct_stores_multi(
    db_key: str, layout, windows: List[Tuple[str, str]], store_type: str
) -> List[int]:
    """一次查询算出**每个时间窗**的去重门店数。

    为什么必须单独查：门店数是 COUNT(DISTINCT)，拆表后同一门店会落进多个分组，
    把各分组的门店数相加会严重重复计（例如按品类拆表，一家店卖 50 个品类就被算 50 次）。

    为什么一次查完所有窗口：同比/环比需要 3 个窗口，逐个查就是 3 次全表扫描；
    用 COUNT(DISTINCT CASE WHEN 窗口条件 THEN 门店 END) 可以在同一次扫描里出 3 列。
    """
    fm = layout.fm
    date_id = sql.Identifier(fm["date_col"])
    store_id = sql.Identifier(fm["store_col"])

    fields, win_params = [], []
    for i, (df_, dt_) in enumerate(windows):
        cond = sql.SQL("{d} >= %s AND {d} <= %s").format(d=date_id)
        fields.append(
            sql.SQL("COUNT(DISTINCT CASE WHEN {c} THEN {s} END) AS {a}").format(
                c=cond, s=store_id, a=sql.SQL("s%d" % i)
            )
        )
        win_params.extend([df_, dt_])

    conditions, params = _build_where(layout, windows, store_type)
    stmt = sql.SQL("SELECT {f} FROM {t} WHERE {w}").format(
        f=sql.SQL(", ").join(fields),
        t=sql.Identifier(layout.table),
        w=sql.SQL(" AND ").join(conditions),
    )
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, win_params + params)
                row = cur.fetchone()
                return [int(row[i] or 0) for i in range(len(windows))]
    except Exception as e:
        logger.error("AI 门店数去重失败 %s: %s", db_key, e)
        return [0] * len(windows)


def _group_row(r: dict, layout) -> dict:
    """读取该行的分组键（Identifier.strings 里是原始列名）。"""
    g = {}
    for c in layout.group_cols:
        name = c.strings[0] if getattr(c, "strings", None) else str(c)
        g[name] = r.get(name)
    return g

