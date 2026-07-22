# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class DbMeta(BaseModel):
    """数据库元信息。"""
    key: str
    label: str
    db_name: str
    table: str
    columns: List[Dict[str, str]]
    numeric_columns: List[str]
    date_columns: List[str]
    row_count: int


class PageResult(BaseModel):
    """分页查询结果。"""
    total: int
    page: int
    page_size: int
    rows: List[Dict[str, Any]]
    next_cursor: Optional[int] = None


class RowCreate(BaseModel):
    """新增记录请求体（动态字段）。"""
    data: Dict[str, Any]


class RowUpdate(BaseModel):
    """更新记录请求体（动态字段）。"""
    data: Dict[str, Any]


class Message(BaseModel):
    message: str
