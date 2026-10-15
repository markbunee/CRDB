# -*- coding: utf-8 -*-
"""确定性槽位提取器：把一句中文问话拆成结构化槽位（**不调用 LLM**）。

为什么不让 LLM 做这一步
------------------------
1. 幻觉风险最高的恰恰是时间：「2026年1-9月」被解析成 ~09-30 / ~今天 / ~09-31 都有可能，
   而日期错了后面全错，且错得极其隐蔽（数字看着完全正常）。
2. 0 token、<1ms、可攒历史问法做回归测试；LLM 每问都要付一次成本且不可回归。
3. LLM 真正不可替代的是「没见过的说法」——那是 L2 兜底的事，不该是主路径。

输出三态
--------
每个槽位标记 resolved / ambiguous / missing，驱动前端确认卡的配色与「查询」按钮是否可点：
  - resolved  绿：已确定，回显即可
  - ambiguous 黄：有候选但要人确认（如「维生素D」匹配到 3 个规格）
  - missing   红：必填且没提取到 → 必须让人补，**从机制上杜绝盲目执行**
"""
import calendar
import os
import re
import threading
from dataclasses import dataclass, field as dc_field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from psycopg2 import sql as _pgsql

from ..database import get_conn
from ..logger import get_logger
from ..models.schema_def import get_cfg, get_field_map, get_region_levels, get_region_label
from ..routers.filter_options import get_candidates
from ..services.product_map import get_product_map
from ..services.ttl_cache import TTLCache

logger = get_logger("app.services.ai_slots")

# ---------- 数据时间边界缓存（MAX/MIN 日期，用于可得性校准） ----------
_RANGE_CACHE = TTLCache(
    ttl=int(os.environ.get("AI_RANGE_CACHE_TTL", "600")),
    max_entries=16,
    name="ai_range",
)
_RANGE_LOCK = threading.Lock()


def get_data_range(db_key: str) -> Tuple[Optional[date], Optional[date]]:
    """返回库里销售数据的 [min_date, max_date]（带缓存）。

    这是「数据可得性校准」的基准：用户问 2026年1-9月，库里只到 8 月，
    就必须把 date_to 裁到 8 月底并在回答里说明，否则：
      - 本期 9 个月 vs 基期 9 个月，但最后一个月是空的 → 同比假性大跌；
      - 或直接回答「0 盒」，用户还以为真没销量。
    """
    ck = f"range|{db_key}"
    hit = _RANGE_CACHE.get(ck)
    if hit is not None:
        return hit
    fm = get_field_map(db_key)
    if not fm:
        return (None, None)
    cfg = get_cfg(db_key)
    try:
        with _RANGE_LOCK:
            hit = _RANGE_CACHE.get(ck)
            if hit is not None:
                return hit
            with get_conn(db_key) as conn:
                with conn.cursor() as cur:
                    # 必须用 sql.SQL(...).format()：sql.Identifier 只有被 sql.SQL 包裹时
                    # 才会渲染成带引号的标识符，用普通 str.format() 会变成 Identifier('日期') 而报错。
                    cur.execute(
                        _pgsql.SQL("SELECT MIN({d}), MAX({d}) FROM {t}").format(
                            d=_pgsql.Identifier(fm["date_col"]),
                            t=_pgsql.Identifier(cfg["table"]),
                        )
                    )
                    row = cur.fetchone()
            res = (row[0], row[1]) if row and row[0] else (None, None)
            _RANGE_CACHE.set(ck, res)
            return res
    except Exception as e:
        logger.error("取数据时间边界失败 %s: %s", db_key, e)
        return (None, None)


# ---------- 时间解析 ----------

_RE_YEAR = re.compile(r"(20\d{2})\s*年")
# 同时覆盖「1-9月」「1~9月」「1月至9月」「1月到9月」四种写法
_RE_MONTH_RANGE = re.compile(r"(\d{1,2})\s*月?\s*[-~—至到]\s*(\d{1,2})\s*月?")
# 负向后顾：避免把「1-9月」里的「9月」当成单月命中
_RE_SINGLE_MONTH = re.compile(r"(?<![-\d])(\d{1,2})\s*月")
_RE_QUARTER = re.compile(r"([1-4一二三四])\s*季度|Q([1-4])", re.I)
_RE_RECENT_N = re.compile(r"(?:近|最近|过去)\s*(\d{1,2})\s*(?:个)?\s*月")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}


def _month_end(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def _month_start(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}-01"


def parse_time(text: str, max_date: Optional[date]) -> Optional[Dict[str, Any]]:
    """解析时间槽 → {'date_from','date_to','granularity','raw','clamped','note'}

    解析结果一律再做「数据可得性校准」：date_to 不超过 max_date。
    """
    t = text.strip()
    if not t:
        return None

    anchor = max_date or date.today()
    res: Optional[Dict[str, Any]] = None
    gran = "merged"

    # ① 年 + 月范围：2026年1-9月 / 1~9月 / 1月至9月
    m = _RE_MONTH_RANGE.search(t)
    if m:
        y = _year_of(t, anchor)
        m1, m2 = int(m.group(1)), int(m.group(2))
        if 1 <= m1 <= 12 and 1 <= m2 <= 12 and m1 <= m2:
            res = {"date_from": _month_start(y, m1), "date_to": _month_end(y, m2)}
    # ② 年 + 单月：2026年1月
    if res is None:
        m = _RE_SINGLE_MONTH.search(t)
        if m:
            y = _year_of(t, anchor)
            mm = int(m.group(1))
            if 1 <= mm <= 12:
                res = {"date_from": _month_start(y, mm), "date_to": _month_end(y, mm)}
    # ③ 季度：Q1 / 一季度
    if res is None:
        m = _RE_QUARTER.search(t)
        if m:
            q = m.group(1) or m.group(2)
            q = _CN_NUM.get(q, q)
            try:
                q = int(q)
            except (TypeError, ValueError):
                q = None
            if q and 1 <= q <= 4:
                y = _year_of(t, anchor)
                m1 = (q - 1) * 3 + 1
                res = {"date_from": _month_start(y, m1), "date_to": _month_end(y, m1 + 2)}
    # ④ 近 N 个月
    if res is None:
        m = _RE_RECENT_N.search(t)
        if m:
            n = int(m.group(1))
            end = anchor
            start = _shift_months(anchor, -(n - 1))
            res = {"date_from": _month_start(start.year, start.month), "date_to": _month_end(end.year, end.month)}
            gran = "monthly"
    # ⑤ 相对词：上月 / 本月 / 今年 / 去年 / 上半年 / 下半年
    if res is None:
        for kw, build in (
            ("上月", lambda: _prev_month(anchor)),
            ("上个月", lambda: _prev_month(anchor)),
            ("本月", lambda: _cur_month(anchor)),
            ("这个月", lambda: _cur_month(anchor)),
            ("当月", lambda: _cur_month(anchor)),
            ("去年", lambda: _year_span(anchor.year - 1)),
            ("前年", lambda: _year_span(anchor.year - 2)),
            ("今年", lambda: _year_span(anchor.year)),
            ("本年", lambda: _year_span(anchor.year)),
            ("上半年", lambda: {"date_from": _month_start(anchor.year, 1), "date_to": _month_end(anchor.year, 6)}),
            ("下半年", lambda: {"date_from": _month_start(anchor.year, 7), "date_to": _month_end(anchor.year, 12)}),
        ):
            if kw in t:
                res = build()
                break
    # ⑥ 只有年：2026年
    if res is None:
        m = _RE_YEAR.search(t)
        if m:
            y = int(m.group(1))
            res = {"date_from": _month_start(y, 1), "date_to": _month_end(y, 12)}
            gran = "monthly"

    if res is None:
        return None

    # 分支自己指定了粒度（长区间如「今年」「近12个月」默认逐月）时以分支为准，
    # 否则用默认的「区间合计」。短区间（如「1-9月」「上月」）给合计更符合问数直觉。
    res["granularity"] = res.get("granularity") or gran
    res["raw"] = _time_raw(t)
    res["clamped"] = False
    res["note"] = None

    # ------- 数据可得性校准（必做） -------
    if max_date:
        md = max_date.strftime("%Y-%m-%d")
        if res["date_to"] > md:
            res["date_to"] = md
            res["clamped"] = True
            res["note"] = f"数据截至 {md}，已按此日期统计"
        # 逐月粒度下，起点早于数据起点也做收敛（避免整段空窗）
    return res


def _year_of(t: str, anchor: date) -> int:
    m = _RE_YEAR.search(t)
    return int(m.group(1)) if m else anchor.year


def _time_raw(t: str) -> str:
    m = _RE_MONTH_RANGE.search(t) or _RE_SINGLE_MONTH.search(t) or _RE_YEAR.search(t)
    return m.group(0) if m else ""


def _shift_months(d: date, n: int) -> date:
    total = d.year * 12 + d.month - 1 + n
    y, m = total // 12, total % 12 + 1
    return date(y, m, 1)


def _prev_month(anchor: date) -> dict:
    p = _shift_months(anchor, -1)
    return {"date_from": _month_start(p.year, p.month), "date_to": _month_end(p.year, p.month)}


def _cur_month(anchor: date) -> dict:
    return {"date_from": _month_start(anchor.year, anchor.month), "date_to": _month_end(anchor.year, anchor.month)}


def _year_span(y: int) -> dict:
    return {"date_from": _month_start(y, 1), "date_to": _month_end(y, 12), "granularity": "monthly"}


# ---------- 实体消歧 ----------

def _norm_variants(word: str) -> List[str]:
    """生成容错写法：广东 ↔ 广东省、广州 ↔ 广州市。"""
    out, seen = [], set()
    for v in (word, word + "省", word + "市", word.rstrip("省市")):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def match_region(text: str, db_key: str) -> Optional[Dict[str, Any]]:
    """地域消歧 → {'level':'nation'|'province'|'city','values':[...],'expand_province':bool}

    命中库里真实存在的值（不是用户输入原样），保证后续 IN 查询必中。
    """
    t = text.strip()
    levels = get_region_levels(db_key)
    label = get_region_label(db_key)

    for kw in ("全国", "全部", "所有城市", "所有地区", "整体", "全国范围"):
        if kw in t:
            return {"level": "nation", "values": [], "expand_province": False, "label": "全国"}

    cands: List[Tuple[int, str, str]] = []  # (长度, level, 真实值)
    if "province" in levels:
        for it in get_candidates(db_key, "province"):
            v = str(it["value"])
            for variant in _norm_variants(v):
                if variant and variant in t:
                    cands.append((len(variant), "province", v))
                    break
    for it in get_candidates(db_key, "city"):
        v = str(it["value"])
        for variant in _norm_variants(v):
            if variant and variant in t:
                cands.append((len(variant), "city", v))
                break

    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], x[1]))
    top_len, level, value = cands[0]
    # 同长度下省份优先（"广东" 比 "广州" 更长匹配到时用长度说话，这里是兜底）
    values = [value]
    # 允许同时命中多个同级地域：「广州和深圳」
    for ln, lv, v in cands:
        if lv == level and v not in values:
            values.append(v)

    expand = any(k in t for k in ("各省城市", "省内城市", "拆成城市", "分城市", "各城市", "每个城市"))
    return {
        "level": level,
        "values": values,
        "expand_province": bool(expand),
        "label": "、".join(values) if values else "全国",
        "region_label": label,
    }


def match_products(text: str, db_key: str) -> Optional[Dict[str, Any]]:
    """品类消歧 → {'codes':[...], 'names':[...], 'ambiguous':bool}

    **优先用品类映射的中文名**（用户说人话，不会报编码），其次库内商品名，最后编码本身。
    命中多个 → ambiguous=True，交确认卡让人选，绝不替他猜。
    """
    t = text.strip().lower()
    pmap = get_product_map(db_key) or {}
    hits: Dict[str, str] = {}  # code -> 显示名

    # ① 映射中文名（长词优先，避免「维生素D」被「维生素」抢先）
    for code, name in sorted(pmap.items(), key=lambda kv: -len(str(kv[1] or ""))):
        nm = str(name or "").strip()
        if nm and nm.lower() in t:
            hits[str(code)] = nm
    # ② 库内商品名称
    for it in get_candidates(db_key, "product"):
        nm = str(it.get("name") or "").strip()
        if nm and nm.lower() in t and str(it["value"]) not in hits:
            hits[str(it["value"])] = nm
    # ③ 编码直接命中
    for it in get_candidates(db_key, "product"):
        code = str(it["value"])
        if len(code) >= 4 and code in t and code not in hits:
            hits[code] = str(it.get("name") or code)

    if not hits:
        return None
    names = [hits[c] for c in hits]
    # 去掉被更长词包含的短命中（"维生素D" 命中后，"维生素" 应剔除）
    names = _drop_subsumed(names)
    codes = [c for c, n in hits.items() if n in names]
    return {
        "codes": codes,
        "names": names,
        "ambiguous": len(codes) > 1,
        "label": "、".join(names),
    }


def _drop_subsumed(names: List[str]) -> List[str]:
    out = []
    for n in names:
        if any(n != m and n in m for m in names):
            continue
        out.append(n)
    return out


def match_stores(text: str, db_key: str) -> List[str]:
    """门店名消歧（store_ability / store_trend 用）。"""
    t = text.strip()
    hits: List[Tuple[int, str]] = []
    for it in get_candidates(db_key, "store"):
        v = str(it["value"]).strip()
        if len(v) >= 3 and v in t:
            hits.append((len(v), v))
    hits.sort(reverse=True)
    seen, out = set(), []
    for _, v in hits:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out[:5]


# ---------- 语义标签 ----------

def _detect_metric(t: str) -> Optional[str]:
    has_box = any(k in t for k in ("盒数", "数量", "销量", "多少盒", "多少盒数"))
    has_amt = any(k in t for k in ("金额", "销售额", "多少钱", "营收"))
    has_store = any(k in t for k in ("门店数", "动销门店", "覆盖门店", "多少家店", "几家店"))
    if has_box and has_amt:
        return "both"
    if has_box:
        return "boxes"
    if has_amt:
        return "amount"
    if has_store:
        return "stores"
    return None


def _detect_compare(t: str) -> str:
    yoy = any(k in t for k in ("同比", "去年同期", "相比去年", "较去年", "yoy"))
    mom = any(k in t for k in ("环比", "上月", "较上月", "相比上月", "mom"))
    if yoy and mom:
        return "both"
    if yoy:
        return "yoy"
    if mom:
        return "mom"
    return "none"


def _detect_top_n(t: str) -> Optional[int]:
    m = re.search(r"(?:前|top|TOP)\s*(\d{1,3})", t)
    if m:
        return max(1, min(500, int(m.group(1))))
    if any(k in t for k in ("排行", "排名", "第一", "最高", "最多", "最好", "top", "TOP")):
        return 1 if "第一" in t or "最高" in t or "最多" in t else 10
    return None


def _detect_split(t: str) -> str:
    if any(k in t for k in ("各城市", "每个城市", "城市对比", "哪些城市", "城市分布", "分城市")):
        return "city"
    if any(k in t for k in ("各品类", "各品种", "每个品类", "品类对比", "哪些品种", "品类结构")):
        return "product"
    if any(k in t for k in ("逐月", "每月", "月度", "各月", "趋势", "走势")):
        return "time"
    if any(k in t for k in ("各省", "每个省", "省份对比", "各省份")):
        return "province"
    return "none"


# ---------- 主入口 ----------

@dataclass
class RawSlots:
    """提取结果。slots 里每个值形如 {'state':..., 'value':..., 'options':...}"""
    text: str
    db_key: str
    capability: Optional[str] = None
    slots: Dict[str, dict] = dc_field(default_factory=dict)
    unresolved: List[str] = dc_field(default_factory=list)
    confidence: float = 0.0
    data_range: Dict[str, Optional[str]] = dc_field(default_factory=dict)
    hints: Dict[str, Any] = dc_field(default_factory=dict)


def extract(text: str, db_key: str) -> RawSlots:
    """确定性提取主入口（纯规则，0 token、可单测）。"""
    from . import ai_capabilities as caps

    t = (text or "").strip()
    rs = RawSlots(text=t, db_key=db_key)
    if not t:
        return rs

    min_d, max_d = get_data_range(db_key)
    rs.data_range = {
        "min_date": min_d.strftime("%Y-%m-%d") if min_d else None,
        "max_date": max_d.strftime("%Y-%m-%d") if max_d else None,
    }

    # ① 能力（别名命中，长词优先）
    cap = caps.match_alias(t, db_key)
    rs.capability = cap

    # ② 时间
    tp = parse_time(t, max_d)
    if tp:
        rs.slots["time"] = {
            "state": "ambiguous" if tp.get("clamped") else "resolved",
            "value": {"date_from": tp["date_from"], "date_to": tp["date_to"], "granularity": tp["granularity"]},
            "note": tp.get("note"),
            "raw": tp.get("raw"),
        }
        rs.hints["granularity"] = tp["granularity"]
    else:
        rs.slots["time"] = {"state": "missing", "value": None}

    # ③ 地域
    rg = match_region(t, db_key)
    if rg:
        st = "resolved"
        if rg["level"] != "nation" and len(rg["values"]) > 1:
            st = "ambiguous"
        rs.slots["region"] = {"state": st, "value": rg}
    else:
        rs.slots["region"] = {"state": "missing", "value": None}

    # ④ 品类
    pr = match_products(t, db_key)
    if pr:
        rs.slots["product"] = {
            "state": "ambiguous" if pr["ambiguous"] else "resolved",
            "value": {"codes": pr["codes"], "names": pr["names"]},
            "label": pr["label"],
        }
    else:
        # 用户没提任何品类词 → 视为「全部品类」，不是缺失
        rs.slots["product"] = {"state": "resolved", "value": None, "label": "全部品类"}

    # ⑤ 指标
    cap_fixed = (caps.get_capability(cap) or {}).get("fixed") or {}
    metric = cap_fixed.get("metric") or _detect_metric(t)
    if metric:
        rs.slots["metric"] = {"state": "resolved", "value": metric}
    else:
        rs.slots["metric"] = {"state": "missing", "value": None}

    # ⑥ 可选槽（有显式默认值，仍回显在确认卡上）
    stype = "all"
    if any(k in t for k in ("加盟", "加盟商", "加盟店")):
        stype = "franchise"
    elif any(k in t for k in ("连锁", "直营")):
        stype = "chain"
    rs.slots["store_type"] = {"state": "resolved", "value": stype}

    rs.slots["compare"] = {"state": "resolved", "value": _detect_compare(t)}

    splt = cap_fixed.get("split") or _detect_split(t)
    rs.slots["split"] = {"state": "resolved", "value": splt}

    tn = _detect_top_n(t)
    rs.slots["top_n"] = {"state": "resolved", "value": tn}

    # ⑦ 门店名（仅当问题确实涉及门店时才匹配）
    # 门店候选常有几万条，逐条做 `v in t` 是纯 CPU 开销；绝大多数提问不带门店名，
    # 无条件跑一遍会白白拖慢每次提问，故用「店」字 + 能力类型做门控。
    if "店" in t or cap in ("q_store_rank", "q_store_trend"):
        stores = match_stores(t, db_key)
        if stores:
            rs.hints["store_name"] = stores[0]

    # ⑧ 置信度：必填槽缺失 = 明确要问；其余按已确定比例
    required = set((caps.get_capability(cap) or {}).get("required") or [])
    missing = [k for k in required if rs.slots.get(k, {}).get("state") == "missing"]
    rs.unresolved = [_label_of(k) for k in missing]
    resolved_cnt = sum(1 for v in rs.slots.values() if v.get("state") == "resolved")
    base = resolved_cnt / max(1, len(rs.slots))
    rs.confidence = round(base * (0.5 if missing else 1.0), 3)
    return rs


def _label_of(slot: str) -> str:
    from . import ai_capabilities as caps
    return caps.label_slot(slot)
