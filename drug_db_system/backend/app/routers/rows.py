# -*- coding: utf-8 -*-
"""数据行 CRUD 接口：/api/{db_key}/rows。"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg2 import sql

from ..database import get_conn
from ..dependencies import validate_db_key, require_admin
from ..logger import get_logger
from ..models.schema_def import get_cfg, get_filter_map
from ..routers.dashboard import invalidate_dashboard_cache
from ..routers.filter_options import invalidate_filter_options_cache
from ..services.query_builder import (
    query_rows, get_row_by_id, create_row, update_row, delete_row,
    delete_rows_by_date, delete_rows_by_month_range,
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
    filter_map = get_filter_map(db_key)
    if product_codes and "product_codes" in filter_map:
        filters[filter_map["product_codes"]] = product_codes
    if cities and "cities" in filter_map:
        filters[filter_map["cities"]] = cities
    if provinces and "provinces" in filter_map:
        filters[filter_map["provinces"]] = provinces

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
def create(
    db_key: str = validate_db_key,
    body: Dict[str, Any] = ...,
    _admin: dict = Depends(require_admin),
):
    """新增记录。"""
    try:
        new_id = create_row(db_key, body)
        return {"id": new_id, "message": "新增成功"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"新增失败: {e}")


@router.put("/rows/{row_id}")
def update(
    db_key: str = validate_db_key,
    row_id: int = ...,
    body: Dict[str, Any] = ...,
    _admin: dict = Depends(require_admin),
):
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
def delete(
    db_key: str = validate_db_key,
    row_id: int = ...,
    _admin: dict = Depends(require_admin),
):
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
    _admin: dict = Depends(require_admin),
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


@router.post("/rows/clear")
def clear_all_rows(
    db_key: str = validate_db_key,
    _admin: dict = Depends(require_admin),
    month_from: Optional[str] = Body(None, embed=True, description="月份范围起 YYYY-MM-DD（月度列通常存每月首日）"),
    month_to: Optional[str] = Body(None, embed=True, description="月份范围止 YYYY-MM-DD"),
    confirm: bool = Body(False, embed=True, description="全部清空必须传 confirm=true 才会执行"),
):
    """清空数据：支持按月份范围删除，或确认后清空全部。

    - 传 month_from / month_to：按月度列删除该月份范围的数据
    - 不传月份范围且 confirm=true：TRUNCATE 清空全部（破坏性操作，谨慎使用）
    """
    cfg = get_cfg(db_key)
    table = cfg["table"]

    if month_from and month_to:
        try:
            deleted = delete_rows_by_month_range(db_key, month_from, month_to)
            logger.warning(
                "按月份范围删除 %s 表 %s: %s ~ %s, 删除 %d 条",
                db_key, table, month_from, month_to, deleted,
            )
            invalidate_dashboard_cache(db_key)
            invalidate_filter_options_cache(db_key)
            return {
                "message": f"已删除 {month_from} 至 {month_to} 共 {deleted} 条数据",
                "db_key": db_key,
                "month_from": month_from,
                "month_to": month_to,
                "deleted": deleted,
            }
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("按月份范围删除 %s 失败: %s", db_key, e)
            raise HTTPException(500, f"删除失败: {e}")

    if not confirm:
        raise HTTPException(
            400,
            "请提供 month_from/month_to 进行月份范围删除，或传 confirm=true 执行全部清空",
        )

    try:
        with get_conn(db_key, readonly=False) as conn:
            with conn.cursor() as cur:
                # 使用 TRUNCATE 快速清空并重置自增 ID
                cur.execute(
                    sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(
                        sql.Identifier(table)
                    )
                )
        logger.warning("清空数据库 %s 的表 %s 完成", db_key, table)
        invalidate_dashboard_cache(db_key)
        invalidate_filter_options_cache(db_key)
        return {"message": f"已清空数据库 {db_key} 的全部数据", "db_key": db_key}
    except Exception as e:
        logger.error("清空数据库 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"清空失败: {e}")
