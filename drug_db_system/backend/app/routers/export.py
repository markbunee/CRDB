# -*- coding: utf-8 -*-
"""数据导出接口：/api/{db_key}/export。

注意：千万级数据全量导出会占用大量内存。
推荐使用 /export/stream 流式下载 CSV。
"""
import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from psycopg2 import sql

from ..config import DB_NAMES
from ..dependencies import validate_db_key
from ..models.schema_def import SCHEMAS, col_names
from ..database import get_conn
from ..utils.serialization import jsonable
from ..services.query_builder import export_rows

router = APIRouter(prefix="/api/{db_key}", tags=["export"])


@router.get("/export")
def export_all(db_key: str = validate_db_key):
    """导出全部数据为 JSON（小数据量用）。

    ⚠️ 千万级数据请改用 /export/stream。
    """
    try:
        return export_rows(db_key)
    except Exception as e:
        raise HTTPException(500, f"导出失败: {e}")


@router.get("/export/stream")
def export_stream(db_key: str = validate_db_key):
    """流式导出 CSV（大数据量推荐）。

    使用服务端游标分批读取，内存占用恒定。
    """
    cfg = SCHEMAS[db_key]
    table = cfg["table"]
    cols = ["id"] + col_names(db_key)
    select_cols = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    stmt = sql.SQL("SELECT {} FROM {} ORDER BY id").format(
        select_cols, sql.Identifier(table))

    db_name = DB_NAMES[db_key]

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        # 这里需要独立连接（流式响应生命周期超出请求）
        import psycopg2
        from ..config import conn_params
        conn = psycopg2.connect(**conn_params(db_name))
        # 服务端命名游标
        with conn.cursor("stream_export") as cur:
            cur.execute(stmt)
            while True:
                batch = cur.fetchmany(2000)
                if not batch:
                    break
                for r in batch:
                    writer.writerow(["" if v is None else str(jsonable(v)) for v in r])
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
        conn.close()

    filename = f"{cfg['label']}_导出.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
