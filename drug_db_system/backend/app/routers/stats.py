# -*- coding: utf-8 -*-
"""统计聚合接口：/api/{db_key}/stats —— 按维度汇总，用于报表。

后续扩展查询功能时，在这里新增路由即可，不影响 rows.py。
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import SCHEMAS, col_names
from ..database import get_conn
from ..utils.serialization import jsonable_rows
from ..services.query_builder import build_filter_conditions, build_date_range_condition
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats"])
logger = get_logger("app.routers.stats")


def _build_click_where(
    db_key: str,
    date_from: Optional[str],
    date_to: Optional[str],
    product_codes: Optional[str],
    cities: Optional[str],
    provinces: Optional[str],
):
    """组装点击统计的 WHERE 片段与参数。"""
    conditions = []
    params = []

    date_cond, date_params = build_date_range_condition(db_key, date_from, date_to)
    if date_cond is not None:
        conditions.append(date_cond)
        params.extend(date_params)

    filters = {}
    if db_key == "dashenlin":
        if product_codes:
            filters["商品编码"] = product_codes
        if cities:
            filters["城市"] = cities
        if provinces:
            filters["省份"] = provinces

    filter_conds, filter_params = build_filter_conditions(db_key, filters)
    conditions.extend(filter_conds)
    params.extend(filter_params)
    return conditions, params


@router.get("/stats/summary")
def summary(
    db_key: str = validate_db_key,
    group_by: Optional[str] = Query(None, description="分组列名"),
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD"),
    product_codes: Optional[str] = Query(None, description="商品编码，英文逗号分隔"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔"),
):
    """汇总统计：仅返回记录数，可按某列分组。

    示例:
      /api/dashenlin/stats/summary
      /api/dashenlin/stats/summary?group_by=省份
      /api/dashenlin/stats/summary?group_by=商品名称&date_from=2024-01-01&date_to=2024-03-31
    """
    cfg = SCHEMAS[db_key]
    table = cfg["table"]
    cols = set(col_names(db_key))

    if group_by and group_by not in cols:
        raise HTTPException(400, f"无效的分组列: {group_by}")

    conditions, params = _build_click_where(
        db_key, date_from, date_to, product_codes, cities, provinces)
    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)

    select_parts = [sql.SQL("COUNT(*) AS row_count")]
    if group_by:
        select_parts.insert(0, sql.Identifier(group_by))

    stmt = sql.SQL("SELECT {} FROM {}{}").format(
        sql.SQL(", ").join(select_parts),
        sql.Identifier(table),
        where,
    )
    if group_by:
        stmt = sql.SQL("{} GROUP BY {} ORDER BY row_count DESC LIMIT 1000").format(
            stmt, sql.Identifier(group_by))

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params)
                colnames = [d[0] for d in cur.description]
                rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
        return jsonable_rows(rows)
    except Exception as e:
        raise HTTPException(500, f"统计失败: {e}")


@router.get("/stats/click")
def click_stats(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD"),
    product_codes: Optional[str] = Query(None, description="商品编码，英文逗号分隔"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔"),
    group_by_date: bool = Query(True, description="是否按日期分组返回趋势"),
):
    """点击项统计：根据日期范围、商品编码、城市、省份筛选后返回记录数。

    默认返回总记录数，以及按日期分组的数量（用于趋势展示）。
    """
    cfg = SCHEMAS[db_key]
    table = cfg["table"]
    date_cols = [c for c, t in cfg["columns"] if t == "DATE"]
    date_col = date_cols[0] if date_cols else None

    conditions, params = _build_click_where(
        db_key, date_from, date_to, product_codes, cities, provinces)
    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                # 总记录数
                cur.execute(
                    sql.SQL("SELECT COUNT(*) AS total FROM {}{}").format(
                        sql.Identifier(table), where),
                    params,
                )
                total = cur.fetchone()[0]

                # 日期范围
                date_range = {"min": None, "max": None}
                if date_col:
                    cur.execute(
                        sql.SQL("SELECT MIN({date}), MAX({date}) FROM {table}{where}").format(
                            date=sql.Identifier(date_col),
                            table=sql.Identifier(table),
                            where=where,
                        ),
                        params,
                    )
                    min_date, max_date = cur.fetchone()
                    date_range = {"min": min_date, "max": max_date}

                # 按日期分组趋势
                daily = []
                if group_by_date and date_col:
                    cur.execute(
                        sql.SQL(
                            "SELECT {date} AS day, COUNT(*) AS count "
                            "FROM {table}{where} "
                            "GROUP BY {date} ORDER BY {date} LIMIT 1000"
                        ).format(
                            date=sql.Identifier(date_col),
                            table=sql.Identifier(table),
                            where=where,
                        ),
                        params,
                    )
                    colnames = [d[0] for d in cur.description]
                    daily = [dict(zip(colnames, r)) for r in cur.fetchall()]

        logger.info(
            "点击统计 %s: date=[%s,%s] codes=%s cities=%s provinces=%s -> total=%d",
            db_key, date_from, date_to,
            product_codes or "-", cities or "-", provinces or "-", total,
        )
        return {
            "total": total,
            "date_range": date_range,
            "daily": jsonable_rows(daily),
        }
    except Exception as e:
        logger.error("点击统计 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")


@router.get("/stats/distinct")
def distinct_values(
    db_key: str = validate_db_key,
    column: str = Query(..., description="要取去重值的列名"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """获取某列的去重值（用于筛选下拉框）。

    示例: /api/dashenlin/stats/distinct?column=省份
    """
    cfg = SCHEMAS[db_key]
    cols = set(col_names(db_key))
    if column not in cols:
        raise HTTPException(400, f"无效列名: {column}")

    stmt = sql.SQL(
        "SELECT DISTINCT {} FROM {} WHERE {} IS NOT NULL ORDER BY {} LIMIT %s"
    ).format(
        sql.Identifier(column),
        sql.Identifier(cfg["table"]),
        sql.Identifier(column),
        sql.Identifier(column),
    )
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, (limit,))
                values = [r[0] for r in cur.fetchall()]
        return {"column": column, "values": values}
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}")
