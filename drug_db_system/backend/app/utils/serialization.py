# -*- coding: utf-8 -*-
"""JSON 序列化工具：处理 Decimal / date / datetime 等 PG 特有类型。"""
from datetime import date, datetime
from decimal import Decimal


def jsonable(obj):
    """递归把单个值转成 JSON 可序列化类型。"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def jsonable_row(row: dict) -> dict:
    """转换一行 dict 的所有值为 JSON 可序列化类型。"""
    return {k: jsonable(v) for k, v in row.items()}


def jsonable_rows(rows: list) -> list:
    """批量转换多行。"""
    return [jsonable_row(r) for r in rows]
