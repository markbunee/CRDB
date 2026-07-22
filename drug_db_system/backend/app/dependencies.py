# -*- coding: utf-8 -*-
"""公共依赖：校验 db_key、提供 cfg 等。"""
from fastapi import HTTPException, Path

from .models.schema_def import SCHEMAS, get_cfg


def validate_db_key(db_key: str = Path(..., description="数据库 key")):
    """路径参数校验：db_key 必须是已知的三库之一。"""
    if db_key not in SCHEMAS:
        raise HTTPException(404, f"未知数据库: {db_key}")
    return db_key


def get_db_cfg(db_key: str):
    """获取数据库配置 dict。"""
    return get_cfg(db_key)
