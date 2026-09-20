# -*- coding: utf-8 -*-
"""门店能力分析接口：/api/{db_key}/stats/store_ability（+ /export）

本期范围（业务指定）：
  - 仅大参林（dashenlin），其它库返回 400；前端按 supports_store_ability 控制菜单显隐
  - 地域可筛选：城市 / 省份 由前端传入（英文逗号分隔，留空=全部），兼容「广州/广州市」「广东/广东省」两种写法
  - 门店身份按「门店名称」分组（不用门店编码）
  - 产出口径为实销盒数 SUM(数量)，不涉及金额

输出内容（一张表，默认前 100 家）：
  1. 门店总产出排行：按实销盒数降序
  2. 品类能力：每个门店的销量 Top3 品类 + 末位品类（倒数第一）
       - 门店品类数不足 3 时，多余的 Top 列留空
       - 末位列始终输出，即使与 Top3 中某一项重复（品类数 <= 3 时必然重复）
       - 占比 = 该品类盒数 / 该门店实销总数（分母与表中「实销总数」列一致）

筛选条件（均可选，留空 = 不限）：
  - stores  ：门店名称关键词，英文逗号分隔，模糊「包含」匹配（ILIKE '%词%'），多词之间 OR
  - products：品类（商品编码），英文逗号分隔，精确匹配，多词之间 IN
  - 两者留空即为「全部门店 × 全部品类」

日期范围必填（date_from / date_to 至少其一）：缺省会退化为对全量销售明细的全表扫描，
千万级数据下不可接受，因此接口直接拒绝；前端默认已带区间。首尾相同即单日查询，
用 >= 与 <= 自然命中。

GET /stats/store_ability/latest_range 返回广州「数据最新月份」的起止日期，
供前端默认填充时间范围（数据常滞后于当前自然月，不能用系统月份）。
"""
import calendar
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key, require_admin, require_permission
from ..models.schema_def import get_cfg, get_field_map, supports_store_ability
from ..services.product_map import get_product_map
from ..services.csv_export import csv_response, rows_to_csv_text
from ..services.export_gate import ExportRejected, export_slot
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-store-ability"])
logger = get_logger("app.routers.stats_store_ability")

# ---------- 地域筛选 ----------
# 门店能力分析地域由前端传入（城市 / 省份，英文逗号分隔，留空=全部），不再固定广州。
# 库里「广州」/「广州市」、「广东」/「广东省」两种写法都可能存在，用 _expand_region 兼容
# （IN 精确枚举，可命中 (城市, 日期) / (省份, 日期) 复合索引，避免全表扫描）。
DEFAULT_CITY = "广州"  # 仅作前端默认回填参考，后端不强制
DEFAULT_TOP_N = 100
MAX_TOP_N = 500
TOP_CATEGORY_COUNT = 3  # 取销量前 3 的品类
# 门店名称关键词个数上限：每个词生成一个 ILIKE，过多会拖慢查询
MAX_STORE_TERMS = 20
# 门店趋势按天展示的柱子上限（约 10 年），防止误选超长区间导致柱子密到无法阅读
MAX_TREND_DAYS = 3660

def _parse_list(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip() != ""]


def _escape_like(val: str) -> str:
    """转义 ILIKE 通配符，避免用户输入的 % / _ 被当作通配符。

    PG 的 LIKE/ILIKE 默认转义符就是反斜杠，故无需额外写 ESCAPE 子句。
    """
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _day_sequence(d_from: date, d_to: date) -> List[str]:
    """生成 [d_from, d_to] 区间内的每一天（升序），返回 YYYY-MM-DD 字符串列表。

    用于补齐没有销量的日期（qty 补 0），保证柱状图横轴连续、柱子疏密一致。
    """
    days: List[str] = []
    cur = d_from
    step = timedelta(days=1)
    while cur <= d_to:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += step
    return days


def _store_type_expr(cfg: dict) -> Optional[sql.Composable]:
    """返回连锁/加盟的 CASE 表达式（仅含 大区/营运区 的库支持，大参林满足）。

    与看板 dashboard._store_type_expr 同口径：大区或营运区字段含「加盟」二字即加盟，
    否则连锁（直营）。门店能力分析本期仅大参林开放，大参林具备这两列，故一定生效。

    注意：该表达式会进入带参数的 execute()，psycopg2 会把 SQL 里的字面量 '%'
    当作占位符解析，故 LIKE 模式中的 '%' 必须写成 '%%'（执行时还原为单个 '%'），
    否则会报 IndexError: list index out of range。
    """
    if any(c[0] == "大区" for c in cfg["columns"]) and any(c[0] == "营运区" for c in cfg["columns"]):
        return sql.SQL(
            "CASE WHEN {daqu} LIKE '%%加盟%%' OR {yingyun} LIKE '%%加盟%%' "
            "THEN '加盟' ELSE '连锁' END"
        ).format(daqu=sql.Identifier("大区"), yingyun=sql.Identifier("营运区"))
    return None


def _expand_region(terms: List[str], suffix: str) -> List[str]:
    """兼容「广州」/「广州市」、「广东」/「广东省」两种写法。

    对每个输入词取其去后缀的基名，再生成 {基名, 基名+suffix} 去重集合用于 IN 精确匹配，
    避免只输「广州」却漏掉库里「广州市」的行（省份同理）。
    """
    seen: set = set()
    out: List[str] = []
    for t in terms:
        base = t[: -len(suffix)] if t.endswith(suffix) else t
        for v in (base, base + suffix):
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _run_store_ability(
    db_key: str,
    date_from: Optional[str],
    date_to: Optional[str],
    products: Optional[str],
    top_n: int,
    stores: Optional[str] = None,
    map_names: bool = False,
    store_type: str = "all",
    cities: Optional[str] = None,
    provinces: Optional[str] = None,
) -> dict:
    """门店能力分析核心逻辑：查询 + 组装，JSON 端点与导出共用。

    stores / products 均可选，留空表示不限（全部门店 / 全部品类）。
    map_names=True 时品类显示名优先用映射表中文名，无映射回落到库内商品名称。
    """
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")
    if not supports_store_ability(db_key):
        raise HTTPException(400, f"门店能力分析暂不支持数据库: {db_key}（本期仅大参林开放）")

    # 必须指定日期范围：否则 WHERE 不含日期条件，会退化为对全量销售明细的全表扫描，
    # 千万级数据下不可接受。前端默认已带当月区间，此校验主要堵住手动调用 /docs 的口子。
    if not date_from and not date_to:
        raise HTTPException(
            400,
            "门店能力分析必须指定日期范围（date_from 或 date_to 至少其一），"
            "以避免对全量销售明细做全表扫描。",
        )

    # 品类编码映射：仅在开关打开时读盘（文件很小，读一次足够）
    pmap: Dict[str, str] = get_product_map(db_key) if map_names else {}

    store_col = fm["store_name_col"]
    product_col = fm["product_col"]
    product_name_col = fm.get("product_name_col", "商品名称")
    qty_col = fm["qty_col"]
    city_col = fm["city_col"]
    province_col = fm.get("province_col")
    date_col = fm["date_col"]
    cfg = get_cfg(db_key)
    table = cfg["table"]

    # ---------- WHERE 条件（参数顺序必须与占位符顺序一致） ----------
    conditions: List[sql.Composable] = [
        sql.SQL("{} IS NOT NULL").format(sql.Identifier(store_col)),
        sql.SQL("btrim({}) <> ''").format(sql.Identifier(store_col)),
        sql.SQL("{} IS NOT NULL").format(sql.Identifier(date_col)),
    ]
    params: list = []

    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(date_col)))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(sql.Identifier(date_col)))
        params.append(date_to)

    # 地域筛选：城市 / 省份 英文逗号分隔，留空=全部；兼容「市 / 省市」两种写法（IN 可命中索引）
    city_terms = _parse_list(cities)
    if city_terms:
        expanded = _expand_region(city_terms, "市")
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(city_col),
                sql.SQL(", ").join(sql.Placeholder() * len(expanded)),
            )
        )
        params.extend(expanded)
    province_terms = _parse_list(provinces)
    if province_terms and province_col:
        expanded_p = _expand_region(province_terms, "省")
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(province_col),
                sql.SQL(", ").join(sql.Placeholder() * len(expanded_p)),
            )
        )
        params.extend(expanded_p)

    # 门店名称筛选：多个关键词之间 OR，模糊「包含」匹配（ILIKE '%词%'），
    # 留空则不限门店。ILIKE 本身命中不了索引，但前面的 城市 IN + 日期区间 已通过
    # (城市, 日期) 复合索引把结果集收敛到「当月的广州数据」，在此基础上做字符串
    # 匹配代价可接受；若后续门店量过大，可改用 GIN 全文索引（schema 里 门店名称
    # 已登记在 fts_columns）。
    store_terms = _parse_list(stores)
    if store_terms:
        if len(store_terms) > MAX_STORE_TERMS:
            raise HTTPException(
                400,
                f"门店名称关键词最多 {MAX_STORE_TERMS} 个，当前 {len(store_terms)} 个",
            )
        term_conds = sql.SQL(" OR ").join(
            sql.SQL("{} ILIKE %s").format(sql.Identifier(store_col))
            for _ in store_terms
        )
        conditions.append(sql.SQL("({})").format(term_conds))
        params.extend([f"%{_escape_like(t)}%" for t in store_terms])

    product_list = _parse_list(products)
    if product_list:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(product_col),
                sql.SQL(", ").join(sql.Placeholder() * len(product_list)),
            )
        )
        params.extend(product_list)

    # 门店类型（连锁/直营 vs 加盟）：仅支持库生效；store_type=all 不限制。
    # 判定口径与看板一致：大区或营运区含「加盟」二字即加盟，否则连锁。
    st_expr = _store_type_expr(cfg)
    if st_expr is not None and store_type in ("chain", "franchise"):
        label = "连锁" if store_type == "chain" else "加盟"
        conditions.append(sql.SQL("({}) = %s").format(st_expr))
        params.append(label)

    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)

    # sp         : 门店 × 品类 盒数聚合
    # st         : 门店总盒数 + 品类数，窗口函数带出全城总量与门店总数
    # top_stores : 按总盒数降序取前 N 家并生成排名
    # sp_rank    : 对前 N 家的每个品类算正序 / 倒序行号
    # 最终只取 rn_desc <= 3 或 rn_asc = 1 的行（Top3 + 末位）
    stmt = sql.SQL(
        """
        WITH sp AS (
            SELECT
                btrim({store})          AS store_name,
                {prod}                  AS product_code,
                MIN({prod_name})        AS product_name,
                SUM(COALESCE({qty}, 0)) AS qty
            FROM {table}{where}
            GROUP BY btrim({store}), {prod}
        ),
        st AS (
            SELECT
                store_name,
                SUM(qty)              AS store_qty,
                COUNT(*)              AS cat_cnt,
                SUM(SUM(qty)) OVER () AS total_qty,
                COUNT(*)      OVER () AS store_count
            FROM sp
            GROUP BY store_name
        ),
        top_stores AS (
            SELECT
                store_name, store_qty, cat_cnt, total_qty, store_count,
                ROW_NUMBER() OVER (ORDER BY store_qty DESC, store_name) AS rank
            FROM st
            ORDER BY store_qty DESC, store_name
            LIMIT %s
        ),
        sp_rank AS (
            SELECT
                sp.store_name, sp.product_code, sp.product_name, sp.qty,
                t.rank, t.store_qty, t.cat_cnt, t.total_qty, t.store_count,
                ROW_NUMBER() OVER (
                    PARTITION BY sp.store_name ORDER BY sp.qty DESC, sp.product_code
                ) AS rn_desc,
                ROW_NUMBER() OVER (
                    PARTITION BY sp.store_name ORDER BY sp.qty ASC, sp.product_code
                ) AS rn_asc
            FROM sp
            JOIN top_stores t ON t.store_name = sp.store_name
        )
        SELECT *
        FROM sp_rank
        WHERE rn_desc <= {top_cat} OR rn_asc = 1
        ORDER BY rank, rn_desc
        """
    ).format(
        store=sql.Identifier(store_col),
        prod=sql.Identifier(product_col),
        prod_name=sql.Identifier(product_name_col),
        qty=sql.Identifier(qty_col),
        table=sql.Identifier(table),
        where=where,
        top_cat=sql.Literal(TOP_CATEGORY_COUNT),
    )

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params + [top_n])
                colnames = [d[0] for d in cur.description]
                raw_rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("门店能力分析 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")

    # ---------- 组装结果 ----------
    # 注意：PG 的 SUM/COUNT 经 psycopg2 返回 Decimal / int，且这些值嵌套在 stores 列表的子 dict
    # 中（dict -> list -> dict -> list -> dict）。项目 utils/serialization.jsonable 只递归单值、
    # 不展开嵌套结构；若直接 return，FastAPI 会因 Decimal 不可序列化而 500。故在此统一转
    # float/int（与 jsonable 口径一致），share 因分子分母已是 float 自然也是 float。
    stores: Dict[str, dict] = {}
    total_qty = 0
    store_count = 0

    for r in raw_rows:
        name = r["store_name"]
        store_qty = float(r["store_qty"] or 0)
        qty = float(r["qty"] or 0)
        total_qty = float(r["total_qty"] or 0)
        store_count = int(r["store_count"] or 0)

        s = stores.get(name)
        if s is None:
            s = {
                "rank": int(r["rank"]),
                "store_name": name,
                "qty": store_qty,
                "cat_cnt": int(r["cat_cnt"] or 0),
                "top_categories": [],
                "last_category": None,
            }
            stores[name] = s

        # map_names 开启时：映射表中文名优先，无映射回落到库内商品名称
        code_str = str(r["product_code"]) if r["product_code"] is not None else ""
        display_name = pmap.get(code_str) or r["product_name"] if pmap else r["product_name"]
        item = {
            "product_code": r["product_code"],
            "product_name": display_name,
            "qty": qty,
            "share": (qty / store_qty) if store_qty else None,
        }
        # ORDER BY rank, rn_desc 保证按 1、2、3 顺序追加
        if r["rn_desc"] <= TOP_CATEGORY_COUNT:
            s["top_categories"].append(item)
        if r["rn_asc"] == 1:
            s["last_category"] = item

    store_list = sorted(stores.values(), key=lambda x: x["rank"])

    logger.info(
        "门店能力分析 %s date=[%s,%s] stores=%s products=%s top_n=%s -> 返回 %d 家 / 共 %d 家, 原始行 %d",
        db_key, date_from or "-", date_to or "-", stores or "-", products or "-",
        top_n, len(store_list), store_count, len(raw_rows),
    )

    region_desc = cities or provinces or "全部"
    return {
        "db_key": db_key,
        "city": region_desc,
        "date_from": date_from,
        "date_to": date_to,
        "summary": {
            "store_count": store_count,   # 广州范围内动销门店总数
            "total_qty": total_qty,       # 广州范围内实销总盒数
            "returned": len(store_list),  # 实际返回门店数（<= top_n）
            "top_n": top_n,
        },
        "stores": store_list,
    }


# ---------- CSV 导出 ----------

def _store_to_row(s: dict) -> list:
    """把单个门店拍平成一行（品类列固定 3 组 Top + 1 组末位）。"""
    line: list = [s.get("rank"), s.get("store_name"), s.get("qty"), s.get("cat_cnt")]
    tops = list(s.get("top_categories") or [])
    for i in range(TOP_CATEGORY_COUNT):
        item = tops[i] if i < len(tops) else None
        line.extend([
            item.get("product_name") if item else "",
            item.get("qty") if item else "",
            item.get("share") if item else "",
        ])
    last = s.get("last_category")
    line.extend([
        last.get("product_name") if last else "",
        last.get("qty") if last else "",
        last.get("share") if last else "",
    ])
    return line


def _store_ability_to_csv(result: dict) -> str:
    """把门店能力分析结果写成 CSV 文本（单表，列结构固定）。"""
    header = ["排名", "门店名称", "实销总数", "品类数"]
    for i in range(1, TOP_CATEGORY_COUNT + 1):
        header.extend([f"最佳品类{i}", f"品类{i}盒数", f"品类{i}占比"])
    header.extend(["末位品类", "末位品类盒数", "末位品类占比"])

    rows = (_store_to_row(s) for s in result.get("stores", []))
    return rows_to_csv_text(header, rows)


@router.get("/stats/store_ability")
def store_ability(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD（必填；可与 date_to 相同表示单日）"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD（与 date_from 至少填其一）"),
    products: Optional[str] = Query(None, description="品类(商品编码)，英文逗号分隔；留空=全部品类"),
    stores: Optional[str] = Query(None, description="门店名称关键词，英文逗号分隔，模糊匹配；留空=全部门店"),
    top_n: int = Query(DEFAULT_TOP_N, ge=1, le=MAX_TOP_N, description="返回前 N 家门店"),
    map_names: bool = Query(False, description="品类显示映射表中文名（无映射回落商品名称）"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔；留空=全部（兼容 市/省市 两种写法）"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔；留空=全部"),
):
    """门店能力分析（大参林）：总产出排行 + 品类 Top3 与末位（地域可筛选）。"""
    return _run_store_ability(
        db_key, date_from, date_to, products, top_n, stores=stores, map_names=map_names,
        store_type=store_type, cities=cities, provinces=provinces,
    )


@router.get("/stats/store_ability/export")
def export_store_ability(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("export")),
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD（必填）"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD（与 date_from 至少填其一）"),
    products: Optional[str] = Query(None, description="品类(商品编码)，英文逗号分隔；留空=全部品类"),
    stores: Optional[str] = Query(None, description="门店名称关键词，英文逗号分隔，模糊匹配；留空=全部门店"),
    top_n: int = Query(DEFAULT_TOP_N, ge=1, le=MAX_TOP_N),
    map_names: bool = Query(False, description="品类显示映射表中文名（与页面口径一致）"),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔；留空=全部（兼容 市/省市 两种写法）"),
    provinces: Optional[str] = Query(None, description="省份，英文逗号分隔；留空=全部"),
):
    """导出门店能力分析结果为 CSV（走导出闸门，避免并发重查询拖垮服务器）。"""
    try:
        with export_slot(user.get("id"), f"{db_key}/stats/store_ability.csv"):
            result = _run_store_ability(
                db_key, date_from, date_to, products, top_n, stores=stores,
                map_names=map_names, store_type=store_type, cities=cities,
                provinces=provinces,
            )
            text = _store_ability_to_csv(result)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})

    filename = "store_ability_guangzhou.csv"
    logger.info(
        "门店能力分析导出（CSV）%s date=[%s,%s] stores=%s -> %d 家",
        db_key, date_from or "-", date_to or "-", stores or "-",
        len(result.get("stores", [])),
    )
    return csv_response(filename, text)


@router.get("/stats/store_ability/latest_range")
def store_ability_latest_range(db_key: str = validate_db_key):
    """返回全库「数据最新月份」的日期区间，供前端默认填充时间范围。

    销售数据通常滞后于当前自然月（例如现在是 9 月但数据只到 8 月），因此默认区间
    应以库里 MAX(日期) 所在月份为准，而不是当前系统月份，否则默认查出来是空的。
    门店能力分析现已支持地域筛选，这里返回全局最新月份，前端再按所选地域查询。
    返回该月的完整首末日；无数据时三个字段均为 null，前端按当月兜底。
    """
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")
    if not supports_store_ability(db_key):
        raise HTTPException(400, f"门店能力分析暂不支持数据库: {db_key}（本期仅大参林开放）")

    date_col = fm["date_col"]
    table = get_cfg(db_key)["table"]

    # 返回全库 MAX(日期) 所在月份（不限地域），门店能力分析现已支持地域筛选，
    # 默认时间范围用全局最新月份更通用；MAX 定位索引末尾代价极低。
    stmt = sql.SQL(
        "SELECT MAX({date}) FROM {table}"
    ).format(
        date=sql.Identifier(date_col),
        table=sql.Identifier(table),
    )

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt)
                row = cur.fetchone()
    except Exception as e:
        logger.error("门店能力分析 取最新月份失败 %s: %s", db_key, e)
        raise HTTPException(500, f"获取最新月份失败: {e}")

    max_date = row[0] if row else None
    if max_date is None:
        return {"date_from": None, "date_to": None, "max_date": None}

    first = max_date.replace(day=1)
    last_day = calendar.monthrange(max_date.year, max_date.month)[1]
    last = max_date.replace(day=last_day)
    return {
        "date_from": first.isoformat(),
        "date_to": last.isoformat(),
        "max_date": max_date.isoformat(),
    }


@router.get("/stats/store_ability/store_trend")
def store_ability_trend(
    db_key: str = validate_db_key,
    store_name: str = Query(..., min_length=1, description="门店名称（表格中的完整名称，精确匹配）"),
    date_from: str = Query(..., description="日期起 YYYY-MM-DD（必填）"),
    date_to: str = Query(..., description="日期止 YYYY-MM-DD（必填）"),
    products: Optional[str] = Query(None, description="品类(商品编码)，英文逗号分隔；留空=全部"),
):
    """单个门店在日期区间内的「按天产出」数据。

    - 按天聚合实销盒数；区间内没有销量的日期补 0，保证柱状图横轴连续。
    - 不再区分「完整月 / 非完整月」：按天呈现时每一天本身就是完整的，
      首尾日期不会被截断，因此不存在部分月份的口径问题。
    - products 与主表共用同一筛选口径，保证「表格里的实销总数 = 图表各日合计」。
    """
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")
    if not supports_store_ability(db_key):
        raise HTTPException(400, f"门店能力分析暂不支持数据库: {db_key}（本期仅大参林开放）")

    try:
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    if d_from > d_to:
        raise HTTPException(400, "date_from 不能晚于 date_to")

    # 按天聚合的柱子上限：防止误选超长区间（如十年）导致柱子密到无法阅读
    day_count = (d_to - d_from).days + 1
    if day_count > MAX_TREND_DAYS:
        raise HTTPException(
            400,
            f"日期区间过长（{day_count} 天），按天展示上限为 {MAX_TREND_DAYS} 天，请缩小范围",
        )

    store_col = fm["store_name_col"]
    product_col = fm["product_col"]
    qty_col = fm["qty_col"]
    date_col = fm["date_col"]
    table = get_cfg(db_key)["table"]

    # 条件顺序必须与 params 追加顺序一致。
    # 门店趋势按门店名称（btrim 后）精确匹配，不限定城市，因此点全国任意城市的店都能查到；
    # 与门店能力分析「按门店名称分组」口径一致（同名店跨城市会被合并）。
    conditions: List[sql.Composable] = [
        # 表格里的 store_name 已是 btrim 后的值，这里精确匹配即可（非模糊）
        sql.SQL("btrim({}) = %s").format(sql.Identifier(store_col)),
        sql.SQL("{} >= %s").format(sql.Identifier(date_col)),
        sql.SQL("{} <= %s").format(sql.Identifier(date_col)),
    ]
    params: list = [store_name.strip(), date_from, date_to]

    product_list = _parse_list(products)
    if product_list:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(product_col),
                sql.SQL(", ").join(sql.Placeholder() * len(product_list)),
            )
        )
        params.extend(product_list)

    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    stmt = sql.SQL(
        "SELECT to_char({date}, 'YYYY-MM-DD') AS day, "
        "SUM(COALESCE({qty}, 0)) AS qty "
        "FROM {table}{where} "
        "GROUP BY 1 ORDER BY 1"
    ).format(
        date=sql.Identifier(date_col),
        qty=sql.Identifier(qty_col),
        table=sql.Identifier(table),
        where=where,
    )

    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params)
                colnames = [d[0] for d in cur.description]
                raw_rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("门店月度趋势 %s/%s 失败: %s", db_key, store_name, e)
        raise HTTPException(500, f"统计失败: {e}")

    # Decimal -> float；再用日期序列补齐区间内没有销量的日期（qty=0）
    qty_by_day = {r["day"]: float(r["qty"] or 0) for r in raw_rows}
    days = [
        {"date": d, "qty": qty_by_day.get(d, 0)}
        for d in _day_sequence(d_from, d_to)
    ]

    logger.info(
        "门店每日趋势 %s store=%s date=[%s,%s] products=%s -> %d 天",
        db_key, store_name, date_from, date_to, products or "-", len(days),
    )

    return {
        "db_key": db_key,
        "store_name": store_name.strip(),
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
    }
