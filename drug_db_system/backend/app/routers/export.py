# -*- coding: utf-8 -*-
"""全量导出接口：``/api/{db_key}/export``（默认 CSV）与 ``/export/stream``（CSV）。

- ``/export``        ：**默认流式 CSV**（旧行为是 JSON，可用 ``?format=json`` 取回）。
                       默认改 CSV 是因为 JSON 逐行序列化在千万级下又慢又占带宽，
                       而 CSV 能被 Excel / pandas 直接打开。
- ``/export/stream`` ：CSV 流式导出（大数据量推荐），语义与 ``/export`` 一致，
                       作为显式入口保留，前端 / 脚本按老习惯调用不受影响。

两者都走同一套「导出闸门 + 服务端游标 + 分块输出」实现（见 services/csv_export.py）：
并发超限 429、同用户连点 429、语句超时自动杀、行数上限截断，避免把服务器挤崩。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from ..config import DB_NAMES
from ..dependencies import validate_db_key, require_admin, require_permission
from ..models.schema_def import SCHEMAS, col_names
from ..services.export_gate import ExportRejected, acquire
from ..services.csv_export import (
    csv_stream_response,
    export_cursor,
    query_csv_chunks,
)
from ..utils.serialization import jsonable_rows

# 全量导出涉及数据外泄，整组接口仅管理员可用
router = APIRouter(
    prefix="/api/{db_key}", tags=["export"], dependencies=[Depends(require_permission("export"))]
)


def _full_export_stmt(db_key: str):
    cfg = SCHEMAS[db_key]
    all_cols = ["id"] + col_names(db_key)
    select_cols = sql.SQL(", ").join(sql.Identifier(c) for c in all_cols)
    stmt = sql.SQL("SELECT {} FROM {} ORDER BY id").format(
        select_cols, sql.Identifier(cfg["table"]))
    return stmt, all_cols


def _begin_export(user_id, kind: str):
    try:
        return acquire(user_id, kind)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})


@router.get("/export")
def export_all(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
    format: str = Query("csv", description="csv(默认) | json(旧行为，逐行序列化)"),
):
    """全量导出。默认 CSV；``format=json`` 返回 {"columns", "rows"}（兼容旧调用）。"""
    if format not in ("csv", "json"):
        raise HTTPException(400, f"不支持的格式: {format}，可选: csv | json")

    cfg = SCHEMAS[db_key]
    stmt, all_cols = _full_export_stmt(db_key)

    if format == "csv":
        slot = _begin_export(user.get("id"), f"{db_key}/export/all.csv")
        stats: dict = {"rows": 0, "truncated": False}
        chunks = query_csv_chunks(db_key, stmt, None, all_cols, stats)
        return csv_stream_response(
            f"{cfg['label']}_全量导出.csv", chunks, stats=stats, slot=slot,
            log_ctx=f"{db_key} 全量导出",
        )

    # ---------- 旧 JSON 行为 ----------
    slot = _begin_export(user.get("id"), f"{db_key}/export/all.json")

    def generate():
        try:
            yield '{"columns":' + json.dumps(all_cols, ensure_ascii=False) + ',"rows":['
            first = True
            with export_cursor(db_key, stmt, None, cursor_factory=RealDictCursor) as cur:
                while True:
                    batch = cur.fetchmany(2000)
                    if not batch:
                        break
                    for r in batch:
                        obj = jsonable_rows([dict(r)])[0]
                        if not first:
                            yield ","
                        first = False
                        yield json.dumps(obj, ensure_ascii=False)
            yield "]}"
        finally:
            slot.release()

    return StreamingResponse(
        generate(),
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/export/stream")
def export_stream(db_key: str = validate_db_key, user: dict = Depends(require_permission("export"))):
    """流式导出 CSV（大数据量推荐，内存恒定）。

    与 ``/export`` 等价，保留该路径是为了兼容既有脚本 / 文档中的调用方式。
    """
    cfg = SCHEMAS[db_key]
    stmt, all_cols = _full_export_stmt(db_key)

    slot = _begin_export(user.get("id"), f"{db_key}/export/stream")
    stats: dict = {"rows": 0, "truncated": False}
    chunks = query_csv_chunks(db_key, stmt, None, all_cols, stats)
    return csv_stream_response(
        f"{cfg['label']}_导出.csv", chunks, stats=stats, slot=slot,
        log_ctx=f"{db_key} 全量流式导出",
    )
