# -*- coding: utf-8 -*-
"""元数据接口：/api/dbs —— 返回三库的表结构、字段类型、行数、统计字段映射。"""
from fastapi import APIRouter

from ..config import DB_NAMES
from ..models.schema_def import (
    SCHEMAS, numeric_columns, date_columns,
    get_field_map, get_filter_map, get_region_levels, get_region_label, supports_stats,
    supports_store_ability, get_store_ability_city,
)
from ..services.query_builder import count_rows

router = APIRouter()


@router.get("/api/dbs")
def list_dbs():
    """返回三个数据库及其字段元数据、行数、统计字段映射。"""
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
            # 统计功能相关
            "field_map": get_field_map(key),
            "filter_map": get_filter_map(key),
            "region_levels": list(get_region_levels(key)),
            "region_label": get_region_label(key),
            "supports_stats": supports_stats(key),
            # 门店能力分析是否对该库开放（本期仅大参林·广州）
            "supports_store_ability": supports_store_ability(key),
            "store_ability_city": get_store_ability_city(),
        })
    return result
