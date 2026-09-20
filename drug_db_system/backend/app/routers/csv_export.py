# -*- coding: utf-8 -*-
"""查询结果导出接口：按筛选条件把结果导出为 CSV（替代原来的 Excel 导出）。

为什么换 CSV
------------
原来是 xlsxwriter 逐行写 xlsx（constant_memory 模式）。内存虽然恒定，但 xlsx 的
zip/XML/共享字符串表开销使百万行导出要几十秒到几分钟、吃满一个核；同一时间两三个人
导出就会把 2~4GB 小机拖垮。CSV 是纯文本追加，速度快 5~20 倍、CPU 占用低得多，
而且可以「边查边写」，因此全站导出统一改为 CSV。

接口
----
- ``GET /api/{db_key}/export/csv``   主接口（推荐）
- ``GET /api/{db_key}/export/excel`` 兼容旧路径：**同样返回 CSV**（前端仍在用，无需同步发布）

防挤崩设计（详见 ``app/services/export_gate.py`` 与 ``app/services/csv_export.py``）
-------------------------------------------------------------------------------
- 导出前经「导出闸门」：并发达上限 → 429；同用户连点 → 429。
- 服务端游标分批取数，内存恒定；攒够 256KB 才输出一块，避免 chunk 风暴。
- 导出连接带语句超时（默认 10 分钟），SQL 跑飞由 PG 杀查询，不占死连接。
- 单次行数上限（默认 300 万行），触顶即停并在文件末尾追加提示行。
- 全量导出不再先跑 COUNT(*)：省掉一次全表/全索引扫描（这也是原来导出慢的原因之一）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key, require_admin, require_permission
from ..logger import get_logger
from ..models.schema_def import get_cfg, col_names, get_filter_map
from ..services.export_gate import ExportRejected, acquire
from ..services.csv_export import csv_stream_response, query_csv_chunks
from ..services.query_builder import (
    build_search_conditions, build_filter_conditions,
    build_date_range_condition, _assemble_where,
)

# 按筛选条件导出（数据外泄风险），整组接口仅管理员可用
router = APIRouter(
    prefix="/api/{db_key}", tags=["csv_export"], dependencies=[Depends(require_permission("export"))]
)
logger = get_logger("app.routers.csv_export")


def _build_export_query(db_key: str, search: str, date_from: Optional[str],
                        date_to: Optional[str], filters: dict,
                        sort_by: str, sort_dir: str):
    """复用 query_rows 的查询逻辑，构造完整的 SQL 用于导出（不分页）。"""
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


def _run_export(
    db_key: str,
    user: Optional[dict],
    search: str,
    sort_by: str,
    sort_dir: str,
    date_from: Optional[str],
    date_to: Optional[str],
    product_codes: Optional[str],
    cities: Optional[str],
    provinces: Optional[str],
):
    """筛选导出 CSV（``/export/csv`` 与 ``/export/excel`` 共用）。"""
    cfg = get_cfg(db_key)
    filters = {}
    filter_map = get_filter_map(db_key)
    if product_codes and "product_codes" in filter_map:
        filters[filter_map["product_codes"]] = product_codes
    if cities and "cities" in filter_map:
        filters[filter_map["cities"]] = cities
    if provinces and "provinces" in filter_map:
        filters[filter_map["provinces"]] = provinces

    stmt, params, all_cols = _build_export_query(
        db_key, search, date_from, date_to, filters, sort_by, sort_dir,
    )

    # 名额在路由阶段申请（并发满/连点可在「还没开始查库」时就 429 掉，避免白跑重查询），
    # 释放放在流式生成器的 finally 里（数据写完才真正结束）。
    try:
        slot = acquire((user or {}).get("id"), f"{db_key}/export/csv")
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})

    stats: dict = {"rows": 0, "truncated": False}
    chunks = query_csv_chunks(db_key, stmt, params, all_cols, stats)
    filename = f"{cfg['label']}_导出.csv"
    logger.info(
        "导出 %s CSV：search=%s date=[%s,%s] filters=%s sort=%s/%s",
        db_key, search or "-", date_from or "-", date_to or "-",
        filters, sort_by or "-", sort_dir,
    )
    return csv_stream_response(
        filename, chunks, stats=stats, slot=slot,
        log_ctx=f"{db_key} 筛选导出",
    )


@router.get("/export/csv", summary="按筛选条件导出 CSV")
def export_csv(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
    search: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    product_codes: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
):
    """把当前筛选/查询结果导出为 CSV（流式写出，内存恒定，支持千万级）。

    接受与 ``/rows`` 相同的查询参数；导出所有满足条件的行（不分页）。
    超过单次导出行数上限时自动截断，并在文件末尾追加提示行。
    """
    return _run_export(
        db_key, user, search, sort_by, sort_dir,
        date_from, date_to, product_codes, cities, provinces,
    )


@router.get("/export/excel", summary="【兼容】旧 Excel 导出路径，现返回 CSV")
def export_excel_compat(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
    search: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    product_codes: Optional[str] = Query(None),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
):
    """历史路径保留：返回值已从 xlsx 改为 CSV，避免前端与外部脚本一起改版。"""
    return _run_export(
        db_key, user, search, sort_by, sort_dir,
        date_from, date_to, product_codes, cities, provinces,
    )
