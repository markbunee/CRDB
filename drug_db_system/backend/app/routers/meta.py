# -*- coding: utf-8 -*-
"""元数据接口：/api/dbs —— 返回三库的表结构、字段类型、行数。"""
from fastapi import APIRouter

from ..config import DB_NAMES
from ..models.schema_def import SCHEMAS, numeric_columns, date_columns
from ..services.query_builder import count_rows

router = APIRouter()


@router.get("/api/dbs")
def list_dbs():
    """返回三个数据库及其字段元数据、行数。"""
    result = []
    for key, cfg in SCHEMAS.items():
        result.append({
            "key": key,
            "label": cfg["label"],
            "db_name": DB_NAMES[key],
            "table": cfg["table"],
            "columns": [{"name": "id", "type": "INTEGER"}] + [{"name": c, "type": t} for c, t in cfg["columns"]],
            "numeric_columns": numeric_columns(key),
            "date_columns": date_columns(key),
            "search_columns": cfg.get("search_columns", []),
            "row_count": count_rows(key),
        })
    return result
