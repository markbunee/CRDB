# -*- coding: utf-8 -*-
"""筛选候选值接口：/api/{db_key}/filter_options

统计功能各子页面（实销门店数 / 实销盒数 / 销售趋势 / 门店能力）此前只能手工输入
城市、省份、商品编码、门店名称 —— 编码记不住、名字容易打错。这里提供
「输入关键词 → 返回候选值」的模糊查询接口，前端用可搜索下拉承接，
交互方式与数字看板保持一致。

性能设计：
- 事实表是千万级，若每次按键都去 DISTINCT 扫描会拖垮数据库；
- 候选集合只在「导入 / 清空」时变化，故按 (库, 字段) 只做一次去重扫描并 TTL 缓存，
  之后的关键词匹配在内存里完成，单次请求降到毫秒级；
- 单字段候选值有上限（默认 5 万），避免门店名这类高基数字段把内存打爆。

品类（product）候选的展示与匹配优先用「品类映射」的中文名（backend/data/product_map.json），
没有映射时才回退到数据里的商品名称——与数字看板一致，让用户按「易善复」而不是编码找品类。
映射每次请求现读（文件只有几 KB），管理弹窗保存后立即生效，不受候选缓存影响。
"""
import os
import time
from typing import List

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import (
    get_cfg,
    get_field_map,
    get_inventory_table,
    supports_inventory,
    INVENTORY_FIELD_COL,
)
from ..database import get_conn
from ..services.product_map import get_product_map
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["filter_options"])
logger = get_logger("app.routers.filter_options")


# 逻辑字段 → field_map 中的列 key
FIELD_COL = {
    "city": "city_col",       # 海王为「事业部名称」
    "province": "province_col",  # 海王无此维度
    "product": "product_col",    # 商品编码（附带商品名称）
    "store": "store_name_col",   # 门店名称
}

# ---------- 候选值缓存（TTL） ----------
_OPT_CACHE: dict = {}
_OPT_CACHE_TTL = int(os.environ.get("FILTER_OPTIONS_CACHE_TTL", "600"))
_MAX_DISTINCT = int(os.environ.get("FILTER_OPTIONS_MAX", "50000"))


def invalidate_filter_options_cache(_db_key: str | None = None):
    """数据写入（导入 / 清空）后调用，清空候选值缓存。

    _db_key 保留以便将来按需按库失效；现在候选值数据量小，直接整体清空。
    """
    _OPT_CACHE.clear()


def _cache_get(key: str):
    item = _OPT_CACHE.get(key)
    if item and (time.time() - item[0]) < _OPT_CACHE_TTL:
        return item[1]
    if item:
        _OPT_CACHE.pop(key, None)
    return None


def _cache_set(key: str, value):
    _OPT_CACHE[key] = (time.time(), value)


def _load_distinct_inventory(db_key: str, field: str) -> List[dict]:
    """从库存表取候选值（库存是当天快照，门店 / 品类集合与销售表不完全一致）。"""
    table = sql.Identifier(get_inventory_table())
    col = INVENTORY_FIELD_COL[field]
    if field == "product":
        stmt = sql.SQL(
            "SELECT {p}, MIN({n}) FROM {t} GROUP BY {p} LIMIT %s"
        ).format(
            p=sql.Identifier(col),
            n=sql.Identifier("商品名称"),
            t=table,
        )
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, (_MAX_DISTINCT,))
                rows = cur.fetchall()
        items: List[dict] = []
        for code, name in rows:
            if code is None:
                continue
            code_s = str(code).strip()
            if not code_s:
                continue
            name_s = str(name).strip() if name is not None else ""
            items.append({
                "value": code_s,
                "name": name_s,
                "label": f"{name_s} ({code_s})" if name_s else code_s,
                "kw": f"{code_s} {name_s}".lower(),
            })
        items.sort(key=lambda x: x["value"])
        return items

    cid = sql.Identifier(col)
    stmt = sql.SQL(
        "SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT %s"
    ).format(c=cid, t=table)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (_MAX_DISTINCT,))
            rows = cur.fetchall()
    items = []
    for (v,) in rows:
        s = str(v).strip()
        if not s:
            continue
        items.append({"value": s, "label": s, "kw": s.lower()})
    items.sort(key=lambda x: x["value"])
    return items


def _load_distinct(db_key: str, field: str, source: str = "sales") -> List[dict]:
    """扫描一次事实表，取该字段候选值 [{value, label, kw}]（kw 供内存模糊匹配）。"""
    if source == "inventory":
        return _load_distinct_inventory(db_key, field)
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    table = sql.Identifier(cfg["table"])

    if field == "product":
        # 编码 → 名称：同一编码取 MIN(名称)，与看板 options 口径一致
        pcol = sql.Identifier(fm["product_col"])
        ncol = sql.Identifier(fm.get("product_name_col", fm["product_col"]))
        stmt = sql.SQL(
            "SELECT {p}, MIN({n}) FROM {t} GROUP BY {p} LIMIT %s"
        ).format(p=pcol, n=ncol, t=table)
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, (_MAX_DISTINCT,))
                rows = cur.fetchall()
        items: List[dict] = []
        for code, name in rows:
            if code is None:
                continue
            code_s = str(code).strip()
            if not code_s:
                continue
            name_s = str(name).strip() if name is not None else ""
            items.append({
                "value": code_s,
                # name = 数据里的商品名称（兜底）；展示名在接口层优先取品类映射的中文名
                "name": name_s,
                "label": f"{name_s} ({code_s})" if name_s else code_s,
                "kw": f"{code_s} {name_s}".lower(),
            })
        items.sort(key=lambda x: x["value"])
        return items

    col = sql.Identifier(fm[FIELD_COL[field]])
    stmt = sql.SQL(
        "SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT %s"
    ).format(c=col, t=table)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (_MAX_DISTINCT,))
            rows = cur.fetchall()
    items = []
    for (v,) in rows:
        s = str(v).strip()
        if not s:
            continue
        items.append({"value": s, "label": s, "kw": s.lower()})
    items.sort(key=lambda x: x["value"])
    return items


@router.get("/filter_options")
def filter_options(
    db_key: str = validate_db_key,
    field: str = Query("city", description="city | province | product | store"),
    keyword: str = Query("", description="关键词，模糊匹配候选值"),
    limit: int = Query(30, ge=1, le=200),
    source: str = Query("sales", description="sales=销售表 | inventory=库存表"),
):
    """按关键词返回候选值：输入关键词即弹出可选项。

    field：
      city     城市（海王为事业部）
      province 省份（海王无此维度，返回空）
      product  商品编码（附商品名称，编码/名称都能搜）
      store    门店名称

    返回 { field, total, items:[{value,label}] }；total 为命中总数，
    items 为截断到 limit 的候选列表。
    """
    if field not in FIELD_COL:
        raise HTTPException(400, f"不支持的候选字段: {field}")
    if source not in ("sales", "inventory"):
        raise HTTPException(400, f"不支持的候选来源: {source}")

    if source == "inventory":
        if not supports_inventory(db_key):
            return {"field": field, "total": 0, "items": []}
        # 库存表尚未导入时直接给空候选，避免接口报错
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (get_inventory_table(),))
                if cur.fetchone()[0] is None:
                    return {"field": field, "total": 0, "items": []}
        ck = f"inv|{db_key}|{field}"
    else:
        fm = get_field_map(db_key)
        if FIELD_COL[field] not in fm:
            # 该库没有这一维度（如海王无省份）：返回空候选，前端下拉自然为空
            return {"field": field, "total": 0, "items": []}
        ck = f"{db_key}|{field}"

    items = _cache_get(ck)
    if items is None:
        try:
            items = _load_distinct(db_key, field, source)
        except Exception as e:
            logger.error("加载候选值失败 %s/%s(%s): %s", db_key, field, source, e)
            raise HTTPException(500, f"加载候选值失败: {e}")
        _cache_set(ck, items)

    # 品类：映射名也参与匹配与展示（每次请求现读，保存映射后立刻生效）
    mapping = get_product_map(db_key) if field == "product" else {}
    kw = (keyword or "").strip().lower()

    def _match(it: dict) -> bool:
        if not kw:
            return True
        if kw in it["kw"]:
            return True
        if field == "product":
            mname = mapping.get(it["value"], "")
            return bool(mname) and kw in mname.lower()
        return False

    def _label(it: dict) -> str:
        if field == "product":
            shown = mapping.get(it["value"], "") or it.get("name", "")
            return f"{shown} ({it['value']})" if shown else it["value"]
        return it["label"]

    matched = [it for it in items if _match(it)]
    return {
        "field": field,
        "total": len(matched),
        "items": [{"value": it["value"], "label": _label(it)} for it in matched[:limit]],
    }
