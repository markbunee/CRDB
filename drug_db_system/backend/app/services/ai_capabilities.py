# -*- coding: utf-8 -*-
"""AI 能力目录加载与校验 —— 读 data/ai_capabilities.json（跨语言单一真源）。

为什么用 JSON 而不是直接在 Python 里写
--------------------------------------
同一份能力目录要被两端消费：
  1. Python（本文件）：槽位校验、确认卡生成、别名命中；
  2. Node（ai-bridge）：动态构造 typebox 参数 schema + 生成 systemPrompt。
两边各写一份必然漂移（典型症状：TS 侧允许多选、Python 侧只允许单选，
于是 LLM 输出的 plan 在 Python 侧被拒）。故统一落到 JSON，改口径只改一处。

加载策略
--------
文件很小（几 KB）且改动频繁（业务会往 aliases 里加词），**每次请求现读**，
与 product_map 一致：改完立即生效，不吃陈旧缓存。
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR
from ..logger import get_logger

logger = get_logger("app.services.ai_capabilities")

_FILE = os.path.join(DATA_DIR, "ai_capabilities.json")
_LOCK = threading.Lock()

# 进程内缓存：只在 mtime 变化时重读，避免每个请求都做一次磁盘 IO + json 解析
_cache: Optional[dict] = None
_cache_mtime: float = -1.0


def _load() -> dict:
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(_FILE)
    except OSError:
        logger.error("能力目录不存在: %s", _FILE)
        return {"version": 0, "slots": {}, "capabilities": {}}
    with _LOCK:
        if _cache is not None and abs(mtime - _cache_mtime) < 1e-6:
            return _cache
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("能力目录解析失败 %s: %s", _FILE, e)
            return _cache or {"version": 0, "slots": {}, "capabilities": {}}
        _cache, _cache_mtime = data, mtime
        return _cache


def slots() -> Dict[str, dict]:
    return _load().get("slots", {})


def capabilities() -> Dict[str, dict]:
    return _load().get("capabilities", {})


def get_capability(key: str) -> Optional[dict]:
    return capabilities().get(key)


def slot_def(name: str) -> Optional[dict]:
    return slots().get(name)


def label_of(slot_name: str, value: Any) -> str:
    """把枚举值翻成中文显示名（确认卡与回答口径说明共用，保证措辞一致）。"""
    d = slot_def(slot_name)
    if not d:
        return str(value)
    return d.get("value_labels", {}).get(value, str(value))


def available_capabilities(db_key: str) -> Dict[str, dict]:
    """按库过滤：门店能力/动销率本期仅大参林开放。"""
    out = {}
    for k, c in capabilities().items():
        keys = c.get("db_keys") or ["*"]
        if "*" in keys or db_key in keys:
            out[k] = c
    return out


def match_alias(text: str, db_key: str) -> Optional[str]:
    """别名命中 → 直接得到能力 key（L1 模板层，0 token）。

    长词优先：避免「实销盒数」被短的「实销」先抢走。
    """
    cands: List[tuple] = []
    for key, cap in available_capabilities(db_key).items():
        for a in cap.get("aliases", []):
            if a and a in text:
                cands.append((len(a), key))
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


# ---------- 校验 ----------

def validate_plan(cap_key: str, plan: Dict[str, Any], db_key: str) -> List[str]:
    """校验 plan，返回错误列表（空 = 通过）。

    刻意不抛异常：路由层要把「错误 + 已填值」一起回给前端渲染确认卡，
    让用户看着错在哪，而不是收到一句 400。
    """
    cap = get_capability(cap_key)
    if not cap:
        return [f"未知能力: {cap_key}"]
    keys = cap.get("db_keys") or ["*"]
    if "*" not in keys and db_key not in keys:
        return [f"「{cap.get('label', cap_key)}」暂不支持该数据库（本期仅 {('/'.join(keys))} 开放）"]

    errs: List[str] = []
    fixed = cap.get("fixed") or {}
    supports = set(cap.get("supports") or [])
    required = set(cap.get("required") or [])

    for name in required:
        if plan.get(name) in (None, "", [], {}):
            errs.append(f"缺少必填槽位: {label_slot(name)}")

    for name, value in plan.items():
        if name in ("db_key", "capability", "session_id", "store_name"):
            continue
        if value in (None, "", []):
            continue
        if name not in supports and name not in fixed and name not in required:
            # 值恰等于该槽位默认值的，视为「没填」——默认值是系统自己补的，
            # 不能因为能力没声明这个槽位就报错（否则「门店排行」这类能力永远校验不过：
            # 提取器总会给出 compare/split 的默认值）。
            d0 = slot_def(name)
            if d0 and value == d0.get("default"):
                continue
            errs.append(f"「{cap.get('label')}」不支持槽位: {name}")
            continue
        d = slot_def(name)
        if not d:
            continue
        t = d.get("type")
        if t == "enum" and value not in (d.get("values") or []):
            errs.append(f"{label_slot(name)}取值非法: {value}")
        elif t == "int":
            try:
                iv = int(value)
            except (TypeError, ValueError):
                errs.append(f"{label_slot(name)}必须为整数: {value}")
                continue
            lo, hi = d.get("min"), d.get("max")
            if (lo is not None and iv < lo) or (hi is not None and iv > hi):
                errs.append(f"{label_slot(name)}超出范围 [{lo}, {hi}]")

    # region 形状：{"level":..., "values":[...]}，且 level 与库支持的地域级别一致
    region = plan.get("region")
    if isinstance(region, dict):
        from ..models.schema_def import get_region_levels
        lv = region.get("level")
        if lv not in ("nation", "city", "province"):
            errs.append(f"统计范围粒度非法: {lv}")
        elif lv != "nation" and lv not in get_region_levels(db_key):
            errs.append(f"该库不支持地域级别: {lv}")

    return errs


def label_slot(name: str) -> str:
    d = slot_def(name)
    return d.get("label", name) if d else name


def apply_defaults(cap_key: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """补默认值：fixed 覆盖用户值，advanced 槽位缺失时填显式默认值。"""
    cap = get_capability(cap_key) or {}
    out = dict(plan)
    for k, v in (cap.get("fixed") or {}).items():
        out[k] = v
    allowed = set(cap.get("supports") or []) | set((cap.get("fixed") or {}).keys())
    for name, d in slots().items():
        # 该能力没声明的槽位不补默认值：补了也用不上，反而让 plan 看着像填了东西
        if name not in allowed:
            continue
        if out.get(name) in (None, "", []) and d.get("default") is not None:
            # region/time 是必填槽，缺了必须让人补，不偷偷填默认
            if name not in (cap.get("required") or []):
                out[name] = d["default"]
    return out
