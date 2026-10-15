# -*- coding: utf-8 -*-
"""查询确认卡生成 —— 把 RawSlots 渲染成前端可直接渲染的卡片（模板生成，0 token）。

设计原则：**问全，但不是轮流追问**。
机器人一句句追问会让用户烦、易放弃；这里一次把所有槽位渲染到一张卡上：
  - 已识别的预填打勾（绿）；
  - 有歧义的高亮待确认（黄）；
  - 必填且没提取到的标红必选（红）；
  - advanced 组带显式默认值，看得见、可展开改，但不强制打断。
「查询」按钮仅在 ready=true 时可点 —— 从机制上杜绝盲目执行。
"""
import os
from typing import Any, Dict, List, Optional

from psycopg2 import sql as pg_sql

from .ai_slots import RawSlots, get_data_range
from ..database import get_conn
from ..logger import get_logger
from ..models.schema_def import get_cfg, get_field_map, get_region_label, get_region_levels
from ..routers.filter_options import get_candidates
from ..services.ttl_cache import TTLCache

logger = get_logger("app.services.ai_clarify")

_HOT_CACHE = TTLCache(
    ttl=int(os.environ.get("AI_HOT_CACHE_TTL", "600")), max_entries=32, name="ai_hot"
)

# 时间快选：以「数据最新月」为锚点，避免用系统当月（数据常滞后）
TIME_PRESETS = [
    ("数据最新月", "last_month"),
    ("近 3 个月", "last_3m"),
    ("近 6 个月", "last_6m"),
    ("近 12 个月", "last_12m"),
    ("今年至今", "ytd"),
]

CORE_SLOTS = ["region", "time", "product", "metric"]
ADV_SLOTS = ["store_type", "compare", "split", "top_n"]


def hot_regions(db_key: str, level: str, limit: int = 12) -> List[str]:
    """按近期销量取「热点地域」作为快选项（不是全量几千个候选）。

    为什么按销量排序：用户 90% 的场景就是那几个主力省/市，
    按字母序给一堆没人问的地区反而增加选择成本。
    """
    if level == "nation":
        return []
    ck = f"hot|{db_key}|{level}"
    hit = _HOT_CACHE.get(ck)
    if hit is not None:
        return hit[:limit]
    fm = get_field_map(db_key)
    if not fm:
        return []
    col = fm.get("province_col") if level == "province" else fm.get("city_col")
    if not col:
        return []
    cfg = get_cfg(db_key)
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                # 只看近 180 天：走 (地域, 日期) 复合索引，避免全表扫
                # 注意：必须用 pg_sql.SQL(...).format() 而非 str.format()，
                # 否则 sql.Identifier 会被渲染成 Identifier('城市') 导致语法错误。
                cur.execute(
                    pg_sql.SQL(
                        "SELECT {c}, SUM({q}) AS s FROM {t} "
                        "WHERE {d} >= (SELECT MAX({d}) FROM {t}) - INTERVAL '180 days' "
                        "GROUP BY {c} ORDER BY s DESC LIMIT %s"
                    ).format(
                        c=pg_sql.Identifier(col), q=pg_sql.Identifier(fm["qty_col"]),
                        t=pg_sql.Identifier(cfg["table"]), d=pg_sql.Identifier(fm["date_col"]),
                    ),
                    (limit,),
                )
                rows = [str(r[0]) for r in cur.fetchall() if r[0]]
    except Exception as e:
        logger.error("取热点地域失败 %s/%s: %s", db_key, level, e)
        rows = []
    if not rows:  # 降级：用候选全集前 N 个
        rows = [str(it["value"]) for it in get_candidates(db_key, level)][:limit]
    _HOT_CACHE.set(ck, rows)
    return rows[:limit]


def _time_options(max_date) -> List[Dict[str, Any]]:
    return [{"v": key, "label": label} for label, key in TIME_PRESETS]


def build_card(rs: RawSlots, session_id: str) -> Dict[str, Any]:
    """RawSlots → 确认卡。"""
    from . import ai_capabilities as caps

    db_key = rs.db_key
    cap = caps.get_capability(rs.capability) or {}
    max_date_s = rs.data_range.get("max_date")
    max_date = _parse_d(max_date_s)

    card: Dict[str, Any] = {
        "session_id": session_id,
        "question": rs.text,
        "capability": rs.capability,
        "capability_label": cap.get("label"),
        "db_key": db_key,
        "data_range": rs.data_range,
        "confidence": rs.confidence,
        "core": [],
        "advanced": [],
        "missing": [],
        "ready": False,
    }

    for name in CORE_SLOTS:
        item = _build_slot(name, rs, db_key, cap, max_date)
        if item:
            card["core"].append(item)
    for name in ADV_SLOTS:
        item = _build_slot(name, rs, db_key, cap, max_date)
        if item:
            card["advanced"].append(item)

    # 门店名（仅门店趋势能力需要）
    if rs.hints.get("store_name") and rs.capability == "q_store_trend":
        card["core"].append({
            "key": "store_name", "label": "门店", "state": "resolved",
            "value": rs.hints["store_name"], "display": rs.hints["store_name"], "options": [],
        })

    required = list(cap.get("required") or [])
    card["missing"] = [
        caps.label_slot(n) for n in required
        if rs.slots.get(n, {}).get("state") == "missing"
        or rs.slots.get(n, {}).get("value") in (None, [], {})
    ]
    card["ready"] = not card["missing"]
    return card


def _build_slot(name: str, rs: RawSlots, db_key: str, cap: dict, max_date) -> Optional[Dict[str, Any]]:
    from . import ai_capabilities as caps

    supports = set(cap.get("supports") or []) | set(cap.get("required") or []) | set((cap.get("fixed") or {}).keys())
    if name not in supports:
        return None
    s = rs.slots.get(name)
    if not s:
        return None

    d = caps.slot_def(name) or {}
    item: Dict[str, Any] = {
        "key": name,
        "label": d.get("label", name),
        "state": s.get("state", "missing"),
        "value": s.get("value"),
        "note": s.get("note"),
        "options": [],
        "multi": bool(d.get("multi")),
    }

    if name == "region":
        item["display"] = (s.get("value") or {}).get("label") if s.get("value") else None
        # 已确定才不取候选：热点地域需要聚合近 180 天销量（冷启动一次大表扫描），
        # 已确定的槽位再去查一遍纯属浪费。
        if s.get("state") != "resolved":
            item["options"] = _region_options(db_key)
        if s.get("value"):
            item["sub"] = [{
                "key": "expand_province",
                "label": "省拆成城市呈现",
                "value": bool(s["value"].get("expand_province")),
                "type": "bool",
            }]
    elif name == "time":
        v = s.get("value") or {}
        if v:
            item["display"] = f"{v.get('date_from')} ~ {v.get('date_to')}"
        item["options"] = _time_options(max_date)
        if s.get("value"):
            item["sub"] = [{
                "key": "granularity",
                "label": "粒度",
                "value": v.get("granularity") or "merged",
                "type": "enum",
                "options": [{"v": "merged", "label": "区间合计"}, {"v": "monthly", "label": "逐月"}],
            }]
    elif name == "product":
        item["display"] = s.get("label") or ("全部品类" if not s.get("value") else None)
        if s.get("state") != "resolved":
            item["options"] = _product_options(db_key, s)
    else:
        vals = d.get("values")
        if vals:
            item["options"] = [{"v": v, "label": d.get("value_labels", {}).get(v, v)} for v in vals]
        item["display"] = caps.label_of(name, s.get("value")) if s.get("value") is not None else None

    return item


def _parse_d(s: Optional[str]):
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _region_options(db_key: str) -> List[Dict[str, Any]]:
    levels = get_region_levels(db_key)
    label = get_region_label(db_key)
    opts: List[Dict[str, Any]] = [{"v": "nation", "label": "全国", "level": "nation", "hot": True}]
    if "province" in levels:
        for v in hot_regions(db_key, "province"):
            opts.append({"v": v, "label": v, "level": "province", "hot": True})
    for v in hot_regions(db_key, "city"):
        opts.append({"v": v, "label": v, "level": "city", "hot": True})
    return opts


def _product_options(db_key: str, s: dict) -> List[Dict[str, Any]]:
    v = s.get("value") or {}
    names = v.get("names") or []
    opts = [{"v": n, "label": n} for n in names]
    if len(opts) < 10:
        from ..services.product_map import get_product_map
        pmap = get_product_map(db_key) or {}
        for code, name in list(pmap.items())[: 20 - len(opts)]:
            nm = str(name or code)
            if nm not in names:
                opts.append({"v": nm, "label": nm})
    return opts


# ---------- 卡片提交 → plan ----------

def to_plan(card: Dict[str, Any], answers: Dict[str, Any]) -> Dict[str, Any]:
    """把卡片 + 用户答复合并成可执行 plan。

    answers 形如 {"region":{"level":"province","values":["广东"],"expand_province":false},
                  "time":{"preset":"last_3m"} 或 {"date_from":..,"date_to":..},
                  "product":{"names":["维生素D"]}, "metric":"boxes", ...}
    """
    from datetime import date
    from .ai_slots import _month_end, _month_start, _shift_months, get_data_range

    db_key = card["db_key"]
    plan: Dict[str, Any] = {
        "db_key": db_key,
        "capability": card.get("capability"),
        "session_id": card.get("session_id"),
    }

    # ---- 地域 ----
    rg = answers.get("region")
    if isinstance(rg, str):
        rg = _guess_region_level(db_key, rg)
    if isinstance(rg, dict) and rg.get("level"):
        plan["region"] = {
            "level": rg["level"],
            "values": rg.get("values") or ([rg["value"]] if rg.get("value") else []),
            "expand_province": bool(rg.get("expand_province")),
        }

    # ---- 时间（支持快选 preset 或显式区间） ----
    tm = answers.get("time") or {}
    df, dt = tm.get("date_from"), tm.get("date_to")
    if not df or not dt:
        preset = tm.get("preset") or "last_month"
        min_d, max_d = get_data_range(db_key)
        anchor = max_d or date.today()
        if preset == "last_month":
            df, dt = _month_start(anchor.year, anchor.month), _month_end(anchor.year, anchor.month)
        elif preset == "last_3m":
            st = _shift_months(anchor, -2)
            df, dt = _month_start(st.year, st.month), _month_end(anchor.year, anchor.month)
        elif preset == "last_6m":
            st = _shift_months(anchor, -5)
            df, dt = _month_start(st.year, st.month), _month_end(anchor.year, anchor.month)
        elif preset == "last_12m":
            st = _shift_months(anchor, -11)
            df, dt = _month_start(st.year, st.month), _month_end(anchor.year, anchor.month)
        else:  # ytd
            df, dt = _month_start(anchor.year, 1), _month_end(anchor.year, anchor.month)
    plan["time"] = {
        "date_from": df, "date_to": dt,
        "granularity": tm.get("granularity") or "merged",
    }

    for k in ("metric", "store_type", "compare", "split", "top_n"):
        if k in answers:
            plan[k] = answers[k]
    if "product" in answers:
        pv = answers["product"]
        if isinstance(pv, dict):
            plan["product"] = pv
        elif isinstance(pv, str) and pv:
            plan["product"] = {"names": [pv]}
    if "store_name" in answers:
        plan["store_name"] = answers["store_name"]
    return plan


def _guess_region_level(db_key: str, value: str) -> dict:
    """前端只回了一个地域字符串时，判断它是省还是市。"""
    levels = get_region_levels(db_key)
    if "province" in levels:
        for it in get_candidates(db_key, "province"):
            if str(it["value"]) in (value, value.rstrip("省"), value + "省"):
                return {"level": "province", "values": [str(it["value"])]}
    for it in get_candidates(db_key, "city"):
        if str(it["value"]) in (value, value.rstrip("市"), value + "市"):
            return {"level": "city", "values": [str(it["value"])]}
    return {"level": "city", "values": [value]}
