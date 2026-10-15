# -*- coding: utf-8 -*-
"""商品编码映射管理接口：/api/product-map/{db_key}

- GET    /api/product-map/{db_key}          查看当前映射
- POST   /api/product-map/{db_key}          整体保存（body: {"items":[{"code","name"}]}）
- DELETE /api/product-map/{db_key}/{code}   删除单条

数据落在 backend/data/product_map.json，保存后立即生效（统计 / 导出按 map_names 开关使用）。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import DB_NAMES
from ..dependencies import require_admin
from ..routers.dashboard import invalidate_dashboard_cache
from ..services.product_map import (
    delete_product_code,
    get_product_map,
    get_product_map_full,
    replace_product_map,
)

router = APIRouter(tags=["product-map"])


def _check_db(db_key: str) -> None:
    if db_key not in DB_NAMES:
        raise HTTPException(404, f"未知数据库: {db_key}")


def _to_items(mapping: dict) -> List[dict]:
    """{code: {name, price}} -> [{code, name, price}]（按编码排序）。"""
    return [
        {"code": c, "name": v.get("name"), "price": v.get("price")}
        for c, v in sorted(mapping.items(), key=lambda kv: kv[0])
    ]


class ProductMapItem(BaseModel):
    code: str = Field(..., min_length=1, description="商品编码")
    name: str = Field(..., min_length=1, description="品类中文名")
    price: Optional[float] = Field(None, ge=0, description="开票价（元），用于实销金额统计")


class ProductMapSaveBody(BaseModel):
    items: List[ProductMapItem] = Field(default_factory=list)


@router.get("/api/product-map/{db_key}")
def get_map(db_key: str):
    """当前库的编码映射列表（按编码排序），含开票价。"""
    _check_db(db_key)
    items = _to_items(get_product_map_full(db_key))
    return {"db_key": db_key, "count": len(items), "items": items}


@router.post("/api/product-map/{db_key}")
def save_map(db_key: str, body: ProductMapSaveBody, _admin: dict = Depends(require_admin)):
    """整体保存映射（全量替换，前端管理页每次提交完整列表）。"""
    _check_db(db_key)
    mapping = replace_product_map(
        db_key,
        {i.code: {"name": i.name, "price": i.price} for i in body.items},
    )
    items = _to_items(mapping)
    # 改价 / 改品类名会影响看板「实销金额」等聚合口径，主动失效看板缓存，
    # 避免改价后在 TTL 内仍看到旧金额（准确性优先）。
    invalidate_dashboard_cache(db_key)
    return {"db_key": db_key, "count": len(items), "items": items}


@router.delete("/api/product-map/{db_key}/{code}")
def remove_code(db_key: str, code: str, _admin: dict = Depends(require_admin)):
    """删除单条映射。"""
    _check_db(db_key)
    if not delete_product_code(db_key, code):
        raise HTTPException(404, f"映射不存在: {code}")
    # 删除映射同样影响金额口径，失效看板缓存
    invalidate_dashboard_cache(db_key)
    return {"db_key": db_key, "deleted": code}
