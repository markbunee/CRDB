# -*- coding: utf-8 -*-
"""AI 执行层：plan → 直接调用既有统计函数（**不走 HTTP**），再裁剪 + 生成回答。

为什么直接 import 而不是 requests 打自己的接口
----------------------------------------------
1. 省一次网络往返与 JSON 序列化，天然复用 stats_cache / 鉴权 / 内存护栏；
2. 不会绕过 query_gate（走 HTTP 反而要重新实现一遍闸门）；
3. 口径唯一：与统计页面调用的是同一份函数，不存在「AI 一个数、页面另一个数」。

关键优化：多指标一次扫描
------------------------
「实销情况」这种模糊问法要同时给盒数 + 金额 + 动销门店数。
三者可以在**同一条 SQL** 里并列聚合（GROUP BY 键完全相同）：
    SUM(qty) / SUM(qty × 开票价) / COUNT(DISTINCT 门店)
再套上三窗 CASE WHEN，本期 + 去年同期 + 上期一次扫完。
即：**用户问得越模糊，我们一次给得越全，但扫描量不变** —— 多两个聚合列的边际成本近乎为零。

本文件只做「能力分派」；SQL 在 ai_agg.py，话术在 ai_render.py。
"""
import calendar
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..routers.filter_options import get_candidates
from ..routers.stats_box import _run_box_count, _compute_yoy_mom
from ..routers.stats_store import _run_store_count
from ..routers.stats_store_ability import _run_store_ability, _run_store_trend
from ..services import stats_cache
from ..services.cube_layout import compute_layout
from ..services.product_map import get_product_map
from ..logger import get_logger
from . import ai_capabilities as caps
from .ai_agg import _distinct_stores_multi, _fetch_multi, _group_row
from .ai_render import _caveats, render_answer

logger = get_logger("app.services.ai_executor")

DEFAULT_TOP_N = 10
MAX_ANSWER_ROWS = 20


# ---------- plan → 后端参数 ----------

def _split_to_layout_args(split: str, region_level: str = "city") -> Tuple[str, bool, bool, bool]:
    """split（呈现方式）→ (dimension, merge_cities, merge_products, merge_months)。

    规则：split 指定哪根轴拆表，其余轴一律折叠成合计。
    split=none 时三轴全折叠 → 无 GROUP BY → 单行总计（模糊问法的默认形态，最快也最好读）。

    特例：**筛选了某个省、又要看「各城市」** —— 此时若仍用 dimension=city，
    地域轴会被 region_level=province 换成「省份」列，结果只有一个省、一行，
    用户想看的省内城市分布彻底消失（实测「广东省各城市」返回「广东省：603,302 盒」一行）。
    改用 dimension=province（省份拆表、行=省内城市）才是他要的视图。
    """
    if split == "city" and region_level == "province":
        return "province", False, True, True
    dim = split if split in ("city", "product", "time", "province") else "city"
    return (
        dim,
        split != "city",
        split != "product",
        split != "time",
    )


def _region_to_filters(region: Optional[dict], db_key: str) -> Tuple[str, str, str]:
    """region 槽 → (region_level, cities, provinces)"""
    if not region:
        return "city", None, None
    level = region.get("level") or "nation"
    values = region.get("values") or []
    if level == "nation":
        return "city", None, None
    joined = ",".join(str(v) for v in values) or None
    if level == "province":
        return "province", None, joined
    return "city", joined, None


def _resolve_product_codes(db_key: str, product: Optional[dict]) -> Optional[str]:
    """品类槽（中文名 / 编码）→ 商品编码逗号串。

    用户从确认卡上选的是**中文名**，后端要的是编码，这里做一次确定性的反查。
    """
    if not product:
        return None
    codes = product.get("codes")
    if codes:
        return ",".join(str(c) for c in codes)
    names = product.get("names") or []
    if not names:
        return None
    pmap = get_product_map(db_key) or {}
    out: List[str] = []
    for n in names:
        ns = str(n).strip().lower()
        hit = None
        for code, name in pmap.items():
            if str(name or "").strip().lower() == ns:
                hit = str(code)
                break
        if hit is None:
            for it in get_candidates(db_key, "product"):
                if str(it.get("name") or "").strip().lower() == ns:
                    hit = str(it["value"])
                    break
        if hit:
            out.append(hit)
    return ",".join(out) or None


def _time_shift(date_str: str, years: int = 0, months: int = 0) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if years:
        try:
            d = d.replace(year=d.year - years)
        except ValueError:  # 2/29 -> 2/28
            d = d.replace(year=d.year - years, day=28)
    if months:
        total = d.year * 12 + d.month - 1 - months
        y, m = total // 12, total % 12 + 1
        d = d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))
    return d.strftime("%Y-%m-%d")


def _span_months(df: str, dt: str) -> int:
    d1 = datetime.strptime(df, "%Y-%m-%d")
    d2 = datetime.strptime(dt, "%Y-%m-%d")
    return max(1, (d2.year - d1.year) * 12 + (d2.month - d1.month) + 1)



def _exec_overview(plan: dict, db_key: str) -> dict:
    """实销总览：盒数 + 金额 + 动销门店数（± 同比/环比），一次扫描。"""
    region = plan.get("region")
    region_level, cities, provinces = _region_to_filters(region, db_key)
    products = _resolve_product_codes(db_key, plan.get("product"))
    split = plan.get("split") or "none"
    dimension, mc, mp, mm = _split_to_layout_args(split, region_level)
    store_type = plan.get("store_type") or "all"
    compare = plan.get("compare") or "none"
    metric = plan.get("metric") or "both"
    df = plan["time"]["date_from"]
    dt = plan["time"]["date_to"]

    layout = compute_layout(db_key, dimension, region_level, cities, provinces,
                            products, mm, mc, mp, False)

    if compare == "none":
        windows = [(df, dt)]
    elif compare == "yoy":
        windows = [(df, dt), (_time_shift(df, 1), _time_shift(dt, 1))]
    elif compare == "mom":
        n = _span_months(df, dt)
        windows = [(df, dt), (_time_shift(df, 0, n), _time_shift(dt, 0, n))]
    else:  # both
        n = _span_months(df, dt)
        windows = [
            (df, dt),
            (_time_shift(df, 1), _time_shift(dt, 1)),
            (_time_shift(df, 0, n), _time_shift(dt, 0, n)),
        ]

    ck = stats_cache.cache_key(
        "ai_overview", db_key, df=df, dt=dt, region_level=region_level,
        cities=cities, provinces=provinces, products=products,
        dimension=dimension, mc=mc, mp=mp, mm=mm,
        # metric 必须进 key：不同 metric 的 SQL 不同（要不要额外去查门店数），
        # 漏掉会让「只要盒数」命中「含门店数」的结果，既串味又白白付了去重成本。
        metric=metric, st=store_type, cmp=compare,
    )
    hit = stats_cache.cache_get(ck)
    if hit is not None:
        return hit

    rows = _fetch_multi(db_key, layout, windows, store_type)

    # 每个窗口**各自**剔除「该窗口内没有原始行」的分组。
    # 不能只按窗口 0（本期）过滤后给所有窗口复用：某分组很可能本期有数据、去年同期没有
    # （或反之，比如去年卖过今年停销的品类）。若统一按本期过滤，基期里真实存在的销量
    # 会被一起剔掉，基期偏小 → **同比虚高**。原实现 _fetch_raw_windows 也是逐窗过滤的。
    clean_by_win: List[List[dict]] = [
        [r for r in rows if r.get("n%d" % i)]
        for i in range(len(windows))
    ]

    # 门店数单独一条 SQL：拆表模式下它不可跨组相加，且 COUNT(DISTINCT) 是全场最贵的聚合，
    # 剥离出来后「只要盒数/金额」的场景就不必为它付费。
    need_stores = metric in ("both", "stores")
    store_counts: Optional[List[int]] = None
    if need_stores:
        store_counts = _distinct_stores_multi(db_key, layout, windows, store_type)

    def _pick(i: int) -> dict:
        def _f(v):
            return float(v or 0)
        clean = clean_by_win[i]
        stores = store_counts[i] if store_counts is not None else 0
        return {
            "boxes": sum(_f(r.get("w%d_boxes" % i)) for r in clean),
            "amount": sum(_f(r.get("w%d_amount" % i)) for r in clean),
            "stores": stores,
        }

    cur = _pick(0)
    yoy = _pick(1) if len(windows) > 1 and compare in ("yoy", "both") else None
    mom = _pick(len(windows) - 1) if compare in ("mom", "both") else None

    cur_rows = clean_by_win[0]
    result = {
        "kind": "overview",
        "current": cur,
        "yoy": yoy,
        "mom": mom,
        "groups": [_group_row(r, layout) for r in cur_rows[:MAX_ANSWER_ROWS]] if layout.group_cols else [],
        "group_count": len(cur_rows),
        "truncated": len(cur_rows) > MAX_ANSWER_ROWS,
    }
    stats_cache.cache_set(ck, result)
    return result


# ---------- 其他能力：复用既有函数 ----------

def _exec_box_count(plan: dict, db_key: str, metric: str) -> dict:
    region_level, cities, provinces = _region_to_filters(plan.get("region"), db_key)
    products = _resolve_product_codes(db_key, plan.get("product"))
    split = plan.get("split") or "none"
    dimension, mc, mp, mm = _split_to_layout_args(split, region_level)
    calc = (plan.get("compare") or "none") != "none"
    df, dt = plan["time"]["date_from"], plan["time"]["date_to"]

    fn = _compute_yoy_mom if calc else _run_box_count
    res = fn(
        db_key, dimension, region_level, df, dt, mm,
        cities, provinces, products, mc, mp, False, True,
        plan.get("store_type") or "all", metric,
    )
    tables = res.get("tables") or []
    clipped = []
    for t in tables[:3]:
        rows = t.get("rows") or []
        clipped.append({
            "title": t.get("title"),
            "rows": [{"row_key": r.get("row_key"), "total": r.get("total"),
                      "yoy_pct": r.get("yoy_pct"), "mom_pct": r.get("mom_pct")}
                     for r in rows[:MAX_ANSWER_ROWS]],
            "grand_total": t.get("grand_total"),
            "truncated": len(rows) > MAX_ANSWER_ROWS,
            "row_count": len(rows),
        })
    return {"kind": "tables", "metric": metric, "tables": clipped,
            "calc_yoy_mom": calc, "range_label": res.get("口径说明")}


def _exec_store_count(plan: dict, db_key: str) -> dict:
    region_level, cities, provinces = _region_to_filters(plan.get("region"), db_key)
    products = _resolve_product_codes(db_key, plan.get("product"))
    split = plan.get("split") or "none"
    dimension, mc, mp, mm = _split_to_layout_args(split, region_level)
    res = _run_store_count(
        db_key, dimension, region_level,
        plan["time"]["date_from"], plan["time"]["date_to"], mm,
        cities, provinces, products, mc, mp, False, True,
        plan.get("store_type") or "all",
    )
    tables = res.get("tables") or []
    clipped = []
    for t in tables[:3]:
        rows = t.get("rows") or []
        items = []
        for r in rows[:MAX_ANSWER_ROWS]:
            # 门店数是去重计数，**不产出 total/footer**（跨月相加会重复计），
            # 因此这里取该行的最大单元格作为展示值，否则回答会是空的。
            cells = r.get("cells") or {}
            vals = [v for v in cells.values() if isinstance(v, (int, float))]
            total = r.get("total")
            items.append({
                "row_key": r.get("row_key"),
                "value": total if isinstance(total, (int, float)) else (max(vals) if vals else None),
            })
        clipped.append({
            "title": t.get("title"),
            "rows": items,
            "row_count": len(rows),
        })
    return {"kind": "tables", "metric": "stores", "tables": clipped}


def _exec_store_rank(plan: dict, db_key: str) -> dict:
    rg = plan.get("region") or {}
    level = rg.get("level") or "nation"
    cities = ",".join(rg.get("values") or []) if level == "city" else None
    provinces = ",".join(rg.get("values") or []) if level == "province" else None
    products = _resolve_product_codes(db_key, plan.get("product"))
    top_n = int(plan.get("top_n") or DEFAULT_TOP_N)
    res = _run_store_ability(
        db_key, plan["time"]["date_from"], plan["time"]["date_to"],
        products, min(top_n, 100), stores=None, map_names=True,
        store_type=plan.get("store_type") or "all",
        cities=cities, provinces=provinces,
    )
    stores = (res.get("stores") or [])[:MAX_ANSWER_ROWS]
    return {
        "kind": "store_rank",
        "summary": res.get("summary"),
        "stores": [{
            "rank": s.get("rank"),
            "store_name": s.get("store_name"),
            "qty": s.get("qty"),
            "cat_cnt": s.get("cat_cnt"),
            "top_categories": (s.get("top_categories") or [])[:3],
        } for s in stores],
        "returned": len(stores),
    }


def _exec_store_trend(plan: dict, db_key: str) -> dict:
    res = _run_store_trend(
        db_key, plan.get("store_name") or "", 
        plan["time"]["date_from"], plan["time"]["date_to"],
        _resolve_product_codes(db_key, plan.get("product")),
    )
    days = res.get("days") or []
    total = sum(d.get("qty") or 0 for d in days)
    return {
        "kind": "store_trend",
        "store_name": res.get("store_name"),
        "total": total,
        "days": days[:MAX_ANSWER_ROWS * 5],
        "day_count": len(days),
    }


def _expand_region_terms(values) -> List[str]:
    """地域值补出去后缀的写法：「广东省」→「广东省,广东」。

    为什么要补：**销售表与库存表的地域命名不一致**——
    实测销售表省份写作「广东省」，库存表写作「广东」（城市同理「广州市」/「广州」）。
    动销率要同时查这两张表，只传一种写法必有一边匹配 0 行，
    表现为「实销 8000 家 ÷ 库存 0 家」= 动销率算不出来。
    """
    out: List[str] = []
    seen = set()
    for v in values or []:
        s = str(v).strip()
        if not s:
            continue
        base = s[:-1] if s.endswith(("省", "市")) else s
        for cand in (s, base):
            if cand and cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _exec_turnover(plan: dict, db_key: str) -> dict:
    """动销率 = 实销门店数 ÷ 库存门店数（仅大参林，库存表存在才可用）。"""
    # 显式传全部参数即可直调 router 函数（Query(...) 默认值只在缺省时才生效）
    from ..routers.inventory import inventory_turnover

    _, cities, provinces = _region_to_filters(plan.get("region"), db_key)
    res = inventory_turnover(
        db_key=db_key,
        date_from=plan["time"]["date_from"],
        date_to=plan["time"]["date_to"],
        inv_date=None,
        cities=",".join(_expand_region_terms((cities or "").split(",") if cities else [])),
        provinces=",".join(_expand_region_terms((provinces or "").split(",") if provinces else [])),
        products=_resolve_product_codes(db_key, plan.get("product")),
        store_type=plan.get("store_type") or "all",
    )
    by_city = res.get("by_city") or []
    # 动销率低的排前面：业务真正关心的是「哪里没动起来」
    ranked = sorted(
        [c for c in by_city if c.get("turnover_rate") is not None],
        key=lambda c: c["turnover_rate"],
    )
    return {
        "kind": "turnover",
        "sales_stores": res.get("sales_stores"),
        "inventory_stores": res.get("inventory_stores"),
        "turnover_rate": res.get("turnover_rate"),
        "inv_date": res.get("inv_date"),
        "lowest_cities": ranked[:MAX_ANSWER_ROWS],
        "city_count": len(by_city),
    }


def _exec_dashboard(plan: dict, db_key: str) -> dict:
    """经营大盘：KPI + 省/市/品种拆解。

    include_stores=False：门店板块要按门店再聚合一次（千万级），
    AI 只回答「大盘是多少」，不默认付这个成本。
    """
    from ..routers.dashboard import dashboard_summary

    _, cities, provinces = _region_to_filters(plan.get("region"), db_key)
    top_n = int(plan.get("top_n") or DEFAULT_TOP_N)
    res = dashboard_summary(
        db_key=db_key,
        date_from=plan["time"]["date_from"],
        date_to=plan["time"]["date_to"],
        provinces=provinces,
        cities=cities,
        products=_resolve_product_codes(db_key, plan.get("product")),
        store_type=plan.get("store_type") or "all",
        province_top_n=20,
        city_top_n=top_n,
        product_top_n=top_n,
        store_top_n=min(top_n, 20),
        include_stores=False,
    )
    bd = res.get("breakdown") or {}
    return {
        "kind": "dashboard",
        "kpi": res.get("kpi") or {},
        "applied_range": res.get("applied_range"),
        "top_cities": (bd.get("city") or [])[:MAX_ANSWER_ROWS],
        "top_products": (bd.get("product") or [])[:MAX_ANSWER_ROWS],
        "trend_months": (res.get("trend") or {}).get("months") or [],
    }


# ---------- 主入口 ----------

def execute(plan: dict, db_key: str) -> dict:
    """执行 plan，返回 {ok, capability, data, answer, caveats}。"""
    cap_key = plan.get("capability")
    cap = caps.get_capability(cap_key) or {}
    backend = cap.get("backend")
    metric = (cap.get("fixed") or {}).get("metric") or plan.get("metric") or "boxes"

    started = datetime.now()
    try:
        if backend == "box_count_multi" or metric == "both":
            data = _exec_overview(plan, db_key)
        elif backend == "box_count":
            data = _exec_box_count(plan, db_key, metric)
        elif backend == "store_count":
            data = _exec_store_count(plan, db_key)
        elif backend == "store_ability":
            data = _exec_store_rank(plan, db_key)
        elif backend == "store_ability_trend":
            data = _exec_store_trend(plan, db_key)
        elif backend == "inventory_turnover":
            data = _exec_turnover(plan, db_key)
        elif backend == "dashboard_summary":
            data = _exec_dashboard(plan, db_key)
        else:
            return {"ok": False, "error": f"能力「{cap.get('label')}」的执行器尚未接入"}
    except Exception as e:
        logger.error("AI 执行失败 %s/%s: %s", db_key, cap_key, e)
        return {"ok": False, "error": str(e)}

    cost_ms = int((datetime.now() - started).total_seconds() * 1000)
    caveats = _caveats(db_key, plan, data)
    answer = render_answer(cap_key, plan, data, db_key)
    return {
        "ok": True,
        "capability": cap_key,
        "capability_label": cap.get("label"),
        "plan": plan,
        "data": data,
        "answer": answer,
        "caveats": caveats,
        "cost_ms": cost_ms,
    }

