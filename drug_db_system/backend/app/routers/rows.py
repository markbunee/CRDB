# -*- coding: utf-8 -*-
"""数据行 CRUD 接口：/api/{db_key}/rows。"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from ..dependencies import validate_db_key
from ..logger import get_logger
from ..services.query_builder import (
    query_rows, get_row_by_id, create_row, update_row, delete_row,
    delete_rows_by_date,
)

router = APIRouter(prefix="/api/{db_key}", tags=["rows"])
logger = get_logger("app.routers.rows")


@router.get("/rows")
def list_rows(
    db_key: str = validate_db_key,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    search: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    cursor: Optional[int] = Query(None, description="游标分页：上一页最后一条 id"),
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD"),
    product_codes: Optional[str] = Query(None, description="商品编码，英文逗号分隔"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔"),
):
    """分页查询数据。

    - 传统分页：传 page/page_size（OFFSET 方式，深翻页慢）
    - 游标分页：传 cursor（上一页最后一条 id），适合千万级数据无限滚动
    - search：在 search_columns 声明的列上做全文搜索 + ILIKE 回退
    - product_codes / cities / provinces：支持英文逗号分隔多选，留空表示全选
    """
    filters = {}
    # 大参林表字段名映射；其他库可在此扩展
    if db_key == "dashenlin":
        if product_codes:
            filters["商品编码"] = product_codes
        if cities:
            filters["城市"] = cities
        if provinces:
            filters["省份"] = provinces

    try:
        result = query_rows(
            db_key=db_key,
            page=page, page_size=page_size,
            search=search,
            sort_by=sort_by, sort_dir=sort_dir,
            cursor=cursor,
            filters=filters,
            date_from=date_from,
            date_to=date_to,
        )
        logger.info(
            "查询 %s rows: page=%d size=%d date=[%s,%s] codes=%s cities=%s provinces=%s -> total=%d",
            db_key, page, page_size, date_from, date_to,
            product_codes or "-", cities or "-", provinces or "-",
            result.get("total", 0),
        )
        return result
    except Exception as e:
        logger.error("查询 %s rows 失败: %s", db_key, e)
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/rows/{row_id}")
def get_row(db_key: str = validate_db_key, row_id: int = ...):
    """获取单行数据（用于编辑表单预填）。"""
    row = get_row_by_id(db_key, row_id)
    if row is None:
        raise HTTPException(404, "记录不存在")
    return row


@router.post("/rows")
def create(db_key: str = validate_db_key, body: Dict[str, Any] = ...):
    """新增记录。"""
    try:
        new_id = create_row(db_key, body)
        return {"id": new_id, "message": "新增成功"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"新增失败: {e}")


@router.put("/rows/{row_id}")
def update(db_key: str = validate_db_key, row_id: int = ..., body: Dict[str, Any] = ...):
    """更新记录。"""
    try:
        if not update_row(db_key, row_id, body):
            raise HTTPException(404, "记录不存在")
        return {"message": "更新成功"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"更新失败: {e}")


@router.delete("/rows/{row_id}")
def delete(db_key: str = validate_db_key, row_id: int = ...):
    """删除记录。"""
    try:
        if not delete_row(db_key, row_id):
            raise HTTPException(404, "记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


@router.post("/rows/upsert_by_date")
def upsert_by_date(
    db_key: str = validate_db_key,
    body: Dict[str, Any] = ...,
):
    """按日期覆盖新增：先删除同日期所有旧记录，再插入新记录。

    body 格式: {"date": "2024-01-15", "data": {"门店名称": "xxx", ...}}

    适合场景：某天的数据需要重新导入/修正时，一键覆盖。
    """
    overwrite_date = body.get("date")
    data = body.get("data")
    if not overwrite_date:
        raise HTTPException(400, "缺少 date 字段，必须指定覆盖目标日期")
    if not data or not isinstance(data, dict):
        raise HTTPException(400, "缺少 data 字段（要新增的行数据）")
    try:
        deleted = delete_rows_by_date(db_key, overwrite_date)
        logger.info(
            "覆盖新增 %s: date=%s 删除了 %d 条旧记录，准备插入新记录",
            db_key, overwrite_date, deleted,
        )
        new_id = create_row(db_key, data)
        return {
            "id": new_id,
            "deleted": deleted,
            "message": f"已覆盖 {overwrite_date} 的数据（删除 {deleted} 条旧记录）",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("覆盖新增 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"覆盖新增失败: {e}")
