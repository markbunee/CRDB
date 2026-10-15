# -*- coding: utf-8 -*-
"""AI 回答渲染：把结构化结果拼成中文回答 + 口径说明（**模板生成，0 token**）。

单独成文件的原因：话术是**最高频被调整**的部分（业务同学天天改措辞），
而它又完全不涉及 SQL 与查询逻辑。隔开之后改话术不会碰坏查数代码。

为什么坚持模板而不是让 LLM 写总结：
  1. 数字已经在 data 里，让模型复述一遍只是花钱买幻觉风险；
  2. 口径说明必须**逐字可控**（范围/时间/门店类型/缺价告警），模型会漏。
"""
import os
from typing import List, Optional

from ..logger import get_logger
from ..services import stats_cache
from ..services.ttl_cache import TTLCache
from . import ai_capabilities as caps

logger = get_logger("app.services.ai_render")


def _caveats(db_key: str, plan: dict, data: dict) -> List[str]:
    """口径说明与告警 —— 回答必须带，否则用户无法判断数字对不对。"""
    out: List[str] = []
    rg = plan.get("region") or {}
    level = rg.get("level") or "nation"
    scope = "全国" if level == "nation" else "、".join(rg.get("values") or [])
    t = plan.get("time") or {}
    out.append(f"统计范围：{scope}；时间：{t.get('date_from')} ~ {t.get('date_to')}")

    st = plan.get("store_type") or "all"
    out.append(f"门店类型：{caps.label_of('store_type', st)}；"
               f"品类：{plan.get('product', {}).get('names') and '、'.join(plan['product']['names']) or '全部品类'}")

    # 金额缺价告警：未配开票价的品类按 0 计入，会让金额静默偏低
    if (data.get("kind") == "overview" and float(data["current"].get("amount") or 0) > 0) \
            or (data.get("kind") == "tables" and data.get("metric") == "amount"):
        cov = _amount_coverage_cached(db_key, t.get("date_from"), t.get("date_to"))
        if cov and cov.get("unpriced_share", 0) >= 1:
            out.append(
                f"⚠️ 金额口径：有 {cov['unpriced_share']}% 的盒数未配置开票价、按 0 计入，"
                f"实际金额可能偏低（请在「品类映射表」补价）"
            )

    # 同比背离提示：盒数降、金额升（或反之）几乎不可能是真实经营变化，
    # 而是「开票价在两个区间的配置不一致」——未配价的品类按 0 计入金额，
    # 去年配得少、今年配得多，金额就会被抬上去。不点破的话用户会当成真结论。
    if data.get("kind") == "overview" and data.get("yoy"):
        cur, base = data["current"], data["yoy"]
        pb, pa = _pct(cur.get("boxes"), base.get("boxes")), _pct(cur.get("amount"), base.get("amount"))
        try:
            if pb and pa and float(pb.rstrip("%")) * float(pa.rstrip("%")) < 0:
                out.append(
                    f"⚠️ 盒数同比 {pb} 与金额同比 {pa} 方向相反：通常是两个区间的「开票价配置」"
                    f"不一致（未配价品类按 0 计入金额），对比金额前请先核对品类映射表"
                )
        except ValueError:
            pass
    return out


_COVERAGE_CACHE = TTLCache(
    ttl=int(os.environ.get("AI_COVERAGE_CACHE_TTL", "900")),
    max_entries=32,
    name="ai_coverage",
)


def _amount_coverage_cached(db_key: str, date_from: Optional[str], date_to: Optional[str]):
    """缺价口径检查（带缓存）。

    为什么必须缓存：amount_coverage 本身是一次 GROUP BY 全表扫描（千万级），
    而它只是给回答加一句告警 —— 每次提问都跑一遍等于把主查询的成本又付了一次。
    缺价情况只在「品类映射表被改动」时才变化，缓存 15 分钟完全够用。
    """
    ck = stats_cache.cache_key("ai_coverage", db_key, date_from=date_from, date_to=date_to)
    hit = _COVERAGE_CACHE.get(ck)
    if hit is not None:
        return hit
    try:
        from ..routers.stats_box import amount_coverage
        cov = amount_coverage(db_key, date_from, date_to)
    except Exception as e:
        logger.warning("缺价口径检查失败（不影响主结果）%s: %s", db_key, e)
        return None
    _COVERAGE_CACHE.set(ck, cov)
    return cov


# ---------- 回答渲染（模板，0 token） ----------

def _fmt_amount(v) -> str:
    try:
        return f"{float(v or 0) / 10000:,.1f} 万元"
    except (TypeError, ValueError):
        return "-"


def _fmt_int(v) -> str:
    try:
        return f"{int(float(v or 0)):,}"
    except (TypeError, ValueError):
        return "-"


def _pct(cur, base) -> Optional[str]:
    try:
        cur, base = float(cur or 0), float(base or 0)
    except (TypeError, ValueError):
        return None
    if base == 0:
        return None
    d = (cur - base) / base * 100
    return f"{'+' if d >= 0 else ''}{d:.1f}%"


def render_answer(cap_key: str, plan: dict, data: dict, db_key: str) -> str:
    """把结构化结果拼成一段中文回答（模板生成，不调 LLM）。"""
    kind = data.get("kind")

    if kind == "overview":
        c = data["current"]
        lines = [
            f"实销盒数 **{_fmt_int(c['boxes'])} 盒**",
            f"实销金额 **{_fmt_amount(c['amount'])}**",
            f"动销门店 **{_fmt_int(c['stores'])} 家**",
        ]
        extra: List[str] = []
        for tag, key in (("同比", "yoy"), ("环比", "mom")):
            base = data.get(key)
            if not base:
                continue
            seg: List[str] = []
            for label, fld in (("盒数", "boxes"), ("金额", "amount")):
                p = _pct(c.get(fld), base.get(fld))
                if p:
                    seg.append(f"{label} {p}")
            if seg:
                extra.append(f"{tag}：" + "、".join(seg))
        return "；".join(lines) + ("。" + "；".join(extra) if extra else "")

    if kind == "tables":
        parts = []
        for t in data.get("tables") or []:
            gt = t.get("grand_total")
            head = f"{t.get('title') or ''}："
            if data.get("metric") == "amount":
                head += _fmt_amount(gt)
            elif gt is not None:
                head += f"{_fmt_int(gt)} 盒"
            top = t.get("rows") or []
            if top:
                # 门店数不产出 total，用执行器算好的 value 兜底
                def _rv(r):
                    v = r.get("total")
                    if not isinstance(v, (int, float)):
                        v = r.get("value")
                    return float(v) if isinstance(v, (int, float)) else None
                scored = [(r, _rv(r)) for r in top]
                ranked = [(r, v) for r, v in scored if v is not None]
                ranked.sort(key=lambda x: -x[1])
                if ranked:
                    head += "，前几名：" + "、".join(
                        f"{r['row_key']} {_fmt_int(v)}" for r, v in ranked[:5]
                    )
            parts.append(head)
        return "；".join(parts) or "未查到数据"

    if kind == "store_rank":
        stores = data.get("stores") or []
        if not stores:
            return "该范围内没有查到门店销售数据。"
        lines = []
        for s in stores[:10]:
            tops = s.get("top_categories") or []
            best = f"，最佳品类 {tops[0]['product_name']}（{_fmt_int(tops[0]['qty'])} 盒）" if tops else ""
            lines.append(f"{s['rank']}. {s['store_name']} · {_fmt_int(s['qty'])} 盒{best}")
        return "；".join(lines)

    if kind == "store_trend":
        return f"{data.get('store_name')} 在区间内合计 {_fmt_int(data.get('total'))} 盒，共 {data.get('day_count')} 天。"

    if kind == "turnover":
        rate = data.get("turnover_rate")
        rate_s = f"{rate:.1f}%" if isinstance(rate, (int, float)) else "—"
        lines = [
            f"动销率 **{rate_s}**"
            f"（实销 {_fmt_int(data.get('sales_stores'))} 家 ÷ 库存 {_fmt_int(data.get('inventory_stores'))} 家）",
        ]
        lowest = data.get("lowest_cities") or []
        if lowest:
            lines.append("动销率最低的几个城市：" + "、".join(
                f"{c.get('city')} {c.get('turnover_rate'):.1f}%" for c in lowest[:5]
            ))
        return "；".join(lines)

    if kind == "dashboard":
        kpi = data.get("kpi") or {}
        seg = [f"实销盒数 **{_fmt_int(kpi.get('boxes'))} 盒**"]
        if kpi.get("yoy_pct") is not None:
            seg.append(f"同比 {kpi['yoy_pct']:+.1f}%")
        if kpi.get("amount") is not None:
            seg.append(f"金额 **{_fmt_amount(kpi.get('amount'))}**")
        if kpi.get("stores"):
            seg.append(f"动销门店 {_fmt_int(kpi.get('stores'))} 家")
        out = "，".join(seg)
        tops = data.get("top_cities") or []
        if tops:
            out += "；城市前三：" + "、".join(
                f"{c.get('name')} {_fmt_int(c.get('boxes'))}" for c in tops[:3]
            )
        return out

    return "查询完成。"

