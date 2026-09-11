# -*- coding: utf-8 -*-
"""商品编码映射管理接口：/api/product-map/{db_key}

- GET    /api/product-map/{db_key}          查看当前映射
- POST   /api/product-map/{db_key}          整体保存（body: {"items":[{"code","name"}]}）
- DELETE /api/product-map/{db_key}/{code}   删除单条

数据落在 backend/data/product_map.json，保存后立即生效（统计 / 导出按 map_names 开关使用）。
"""
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import DB_NAMES
from ..services.product_map import (
    delete_product_code,
    get_product_map,
    replace_product_map,
)

router = APIRouter(tags=["product-map"])


def _check_db(db_key: str) -> None:
    if db_key not in DB_NAMES:
        raise HTTPException(404, f"未知数据库: {db_key}")


def _to_items(mapping: dict) -> List[dict]:
    return [{"code": c, "name": n} for c, n in sorted(mapping.items(), key=lambda kv: kv[0])]


class ProductMapItem(BaseModel):
    code: str = Field(..., min_length=1, description="商品编码")
    name: str = Field(..., min_length=1, description="品类中文名")


class ProductMapSaveBody(BaseModel):
    items: List[ProductMapItem] = Field(default_factory=list)


@router.get("/api/product-map/{db_key}")
def get_map(db_key: str):
    """当前库的编码映射列表（按编码排序）。"""
    _check_db(db_key)
    items = _to_items(get_product_map(db_key))
    return {"db_key": db_key, "count": len(items), "items": items}


@router.post("/api/product-map/{db_key}")
def save_map(db_key: str, body: ProductMapSaveBody):
    """整体保存映射（全量替换，前端管理弹窗每次提交完整列表）。"""
    _check_db(db_key)
    mapping = replace_product_map(db_key, {i.code: i.name for i in body.items})
    items = _to_items(mapping)
    return {"db_key": db_key, "count": len(items), "items": items}


@router.delete("/api/product-map/{db_key}/{code}")
def remove_code(db_key: str, code: str):
    """删除单条映射。"""
    _check_db(db_key)
    if not delete_product_code(db_key, code):
        raise HTTPException(404, f"映射不存在: {code}")
    return {"db_key": db_key, "deleted": code}
