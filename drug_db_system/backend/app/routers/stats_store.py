# -*- coding: utf-8 -*-
"""门店数统计接口：/api/{db_key}/stats/store_city

按维度统计实销门店数（DISTINCT 门店编码 非重复计数）。
独立模块，不与 stats.py 混合，便于后续扩展更多统计维度。

支持的统计模式：
  - merge_months=false（默认）：每个月独立统计门店数
  - merge_months=true：整个日期范围合并统计（跨月去重）
  - merge_products=false：每个商品编码单独一张表
  - merge_products=true：所有商品编码合并为一张表
"""
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..models.schema_def import get_cfg
from ..database import get_conn
from ..logger import get_logger

router = APIRouter(prefix="/api/{db_key}", tags=["stats-store"])
logger = get_logger("app.routers.stats_store")

# 大参林字段映射（城市维度统计仅适用于含"城市"列的库）
# 后续其他库如有城市列，在此扩展即可
_FIELD_MAP: Dict[str, Dict[str, str]] = {
    "dashenlin": {
        "date_col": "日期",
        "month_col": "月度",
        "store_col": "门店编码",
        "city_col": "城市",
        "product_col": "商品编码",
    },
}


def _parse_list(val: Optional[str]) -> List[str]:
    """把英文逗号分隔的字符串拆成非空列表。"""
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip() != ""]


def _fmt_month(val: Any) -> str:
    """把月度 DATE 值格式化为 YYYY-MM 字符串。"""
    if val is None:
        return "未知"
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m")
    s = str(val)
    return s[:7] if len(s) >= 7 else s


def _build_where(
    fm: Dict[str, str],
    date_from: Optional[str],
    date_to: Optional[str],
    product_list: List[str],
    city_list: List[str],
) -> Tuple[sql.Composable, list]:
    """组装 WHERE 子句及参数。"""
    conditions: List[sql.Composable] = []
    params: list = []

    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_to)

    if product_list:
        placeholders = sql.SQL(", ").join(sql.Placeholder() * len(product_list))
        conditions.append(
            sql.SQL("{} IN ({})").format(sql.Identifier(fm["product_col"]), placeholders)
        )
        params.extend(product_list)

    if city_list:
        placeholders = sql.SQL(", ").join(sql.Placeholder() * len(city_list))
        conditions.append(
            sql.SQL("{} IN ({})").format(sql.Identifier(fm["city_col"]), placeholders)
        )
        params.extend(city_list)

    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    return where, params


@router.get("/stats/store_city")
def store_count_by_city(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None, description="日期起 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="日期止 YYYY-MM-DD"),
    merge_months: bool = Query(
        False, description="合并月范围一起查询（true=整个区间合计去重，false=每月独立）"
    ),
    cities: Optional[str] = Query(None, description="城市，英文逗号分隔，如 广州市,佛山市"),
    product_codes: Optional[str] = Query(
        None, description="商品编码，英文逗号分隔，如 1058746,1086127"
    ),
    merge_products: bool = Query(False, description="多个商品编码合并为一张表统计"),
):
    """实销门店数统计 — 城市维度。

    返回结构:
    ```
    {
      "tables": [
        {
          "title": "商品编码: 1058746",
          "product_code": "1058746",       # 合并时为 null
          "time_columns": ["2024-01", ...], # 合并月时为 ["2024-01-01 ~ 2024-03-31"]
          "cities": ["广州市", "佛山市"],
          "rows": [
            {"city": "广州市", "counts": {"2024-01": 15, "2024-02": 18}},
            ...
          ]
        }
      ],
      "merge_months": false,
      "merge_products": false,
      "total_raw_rows": 120
    }
    ```
    """
    if db_key not in _FIELD_MAP:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}（需含城市列）")

    fm = _FIELD_MAP[db_key]
    cfg = get_cfg(db_key)
    table = cfg["table"]

    city_list = _parse_list(cities)
    product_list = _parse_list(product_codes)

    # 无商品编码输入 → 不拆分，视为合并
    split_products = bool(product_list) and not merge_products

    # ---------- WHERE ----------
    where, params = _build_where(fm, date_from, date_to, product_list, city_list)

    # ---------- SELECT / GROUP BY / ORDER BY ----------
    select_parts: List[sql.Composable] = [sql.Identifier(fm["city_col"])]
    group_cols: List[sql.Identifier] = [sql.Identifier(fm["city_col"])]
    order_parts: List[sql.Identifier] = [sql.Identifier(fm["city_col"])]

    if not merge_months:
        select_parts.append(sql.Identifier(fm["month_col"]))
        group_cols.append(sql.Identifier(fm["month_col"]))
        order_parts.append(sql.Identifier(fm["month_col"]))

    if split_products:
        select_parts.append(sql.Identifier(fm["product_col"]))
        group_cols.append(sql.Identifier(fm["product_col"]))
        order_parts.append(sql.Identifier(fm["product_col"]))

    select_parts.append(
        sql.SQL("COUNT(DISTINCT {}) AS store_count").format(
            sql.Identifier(fm["store_col"])
        )
    )

    stmt = sql.SQL(
        "SELECT {fields} FROM {table}{where} "
        "GROUP BY {group} ORDER BY {order}"
    ).format(
        fields=sql.SQL(", ").join(select_parts),
        table=sql.Identifier(table),
        where=where,
        group=sql.SQL(", ").join(group_cols),
        order=sql.SQL(", ").join(order_parts),
    )

    # ---------- 执行查询 ----------
    try:
        with get_conn(db_key) as conn:
            with conn.cursor() as cur:
                cur.execute(stmt, params)
                colnames = [d[0] for d in cur.description]
                raw_rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("门店数统计 %s 失败: %s", db_key, e)
        raise HTTPException(500, f"统计失败: {e}")

    # ---------- 透视成多张表 ----------
    tables = _pivot_to_tables(
        raw_rows, fm, merge_months, split_products,
        product_list, date_from, date_to,
    )

    logger.info(
        "门店数统计 %s: date=[%s,%s] merge_months=%s codes=%s merge_products=%s "
        "cities=%s -> %d 表, %d 原始行",
        db_key, date_from or "-", date_to or "-",
        merge_months, product_codes or "-", merge_products,
        cities or "-", len(tables), len(raw_rows),
    )

    return {
        "tables": tables,
        "merge_months": merge_months,
        "merge_products": merge_products,
        "total_raw_rows": len(raw_rows),
    }


def _pivot_to_tables(
    raw_rows: List[dict],
    fm: Dict[str, str],
    merge_months: bool,
    split_products: bool,
    product_list: List[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> List[dict]:
    """把扁平查询结果透视为多张表（行=城市，列=时间）。"""

    # ---------- 收集维度成员 ----------
    city_set: set = set()
    time_vals: list = []  # 保持插入顺序
    time_seen: set = set()
    prod_vals: list = []
    prod_seen: set = set()

    for r in raw_rows:
        city = r.get(fm["city_col"])
        if city is not None:
            city_set.add(city)

        if not merge_months:
            mv = r.get(fm["month_col"])
            if mv is not None and mv not in time_seen:
                time_seen.add(mv)
                time_vals.append(mv)

        if split_products:
            pv = r.get(fm["product_col"])
            if pv is not None and pv not in prod_seen:
                prod_seen.add(pv)
                prod_vals.append(pv)

    cities_sorted = sorted(city_set)
    times_sorted = sorted(time_vals)

    # ---------- 时间列标签 ----------
    if merge_months:
        if date_from and date_to:
            time_labels = [f"{date_from} ~ {date_to}"]
        elif date_from:
            time_labels = [f"{date_from} ~ 至今"]
        elif date_to:
            time_labels = [f"截至 {date_to}"]
        else:
            time_labels = ["全部日期"]
    else:
        time_labels = [_fmt_month(t) for t in times_sorted]

    # ---------- 建索引 (city, time_label, prod_str) -> count ----------
    index: Dict[Tuple, int] = {}
    for r in raw_rows:
        city = r.get(fm["city_col"])
        if city is None:
            continue
        if merge_months:
            t_label = time_labels[0]
        else:
            t_label = _fmt_month(r.get(fm["month_col"]))
        prod_str = str(r.get(fm["product_col"])) if split_products else None
        index[(city, t_label, prod_str)] = r.get("store_count", 0)

    # ---------- 生成表 ----------
    def _build_rows(cities: list, labels: list, prod_str: Optional[str]) -> list:
        rows = []
        for city in cities:
            counts = {}
            for t in labels:
                counts[t] = index.get((city, t, prod_str), 0)
            rows.append({"city": city, "counts": counts})
        return rows

    tables: List[dict] = []

    if split_products:
        for prod in sorted(prod_vals):
            prod_s = str(prod)
            tables.append(
                {
                    "title": f"商品编码: {prod_s}",
                    "product_code": prod_s,
                    "time_columns": time_labels,
                    "cities": cities_sorted,
                    "rows": _build_rows(cities_sorted, time_labels, prod_s),
                }
            )
    else:
        title = "门店数统计"
        if product_list:
            title = f"商品编码: {', '.join(product_list)}（合并）"
        tables.append(
            {
                "title": title,
                "product_code": None,
                "time_columns": time_labels,
                "cities": cities_sorted,
                "rows": _build_rows(cities_sorted, time_labels, None),
            }
        )

    return tables
