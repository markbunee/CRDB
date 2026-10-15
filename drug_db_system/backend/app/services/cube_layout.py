# -*- coding: utf-8 -*-
"""统计立方体布局：地域 × 品类 × 时间 的「拆表轴 / 行 / 列」计算（唯一定义处）。

为什么单独抽出来
----------------
盒数统计与门店数统计是**同一套布局、只换聚合指标**（SUM(数量) vs COUNT(DISTINCT 门店)）。
原实现在两个路由里各写了一份近 80 行的布局代码，于是同一个「合并」开关的含义
会各自演化 —— 这正是语义漂移的根源。收敛到这里后两边共用一份，改口径只需改一处。

统一口径：地域级别只替换「地域轴」，角色分配与城市级别完全一致
------------------------------------------------------------
地域轴（城市 / 省份，二选一）+ 品类轴 + 月份轴，共 3 根，角色由 dimension 决定：
  - dimension=city      地域拆表，行=品类，列=月份
  - dimension=product   品类拆表，行=地域，列=月份
  - dimension=time      月份拆表，行=地域，列=品类
  - dimension=province  省份拆表，行=省内城市，列=品类（省份展开视图，地域吃两根轴）

历史坑（已修正）
----------------
早期省份模式把「地域」拆成 省 + 市 两级（4 根轴），并**对所有维度**都套这套 4 轴布局、
同时写死月份合并，于是：
  - 「省份 + 时间维度」静默退化成一张 省份×城市 的表，月份彻底从表里消失；
  - 「省份 + 品类/时间维度」的列轴是城市、行轴是省份，与城市级别对不上
    （同一份数据在两个地域级别下表格结构不一致）。
现在「地域级别」只替换地域轴，城市级别与省份级别的表格结构完全一致；
那套「省 → 市 展开」的布局被**单拎出来**做成独立的 dimension=province（省份维度），
不再与地域级别开关耦合。

另外两条约定
------------
1) ``merge_*`` 一律按「轴」生效（地域 / 品类 / 月份），与「哪根轴负责拆表」完全解耦，
   因此其含义**不随 dimension 变化**（切维度不再清空勾选框）。
2) GROUP BY / ORDER BY 顺序刻意沿用「拆表轴 → 行轴 → 列轴 → 月份」，
   保证透视结果的表/行顺序与既有实现一致，前端与导出不受影响。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from fastapi import HTTPException
from psycopg2 import sql

from ..models.schema_def import get_cfg, get_field_map, get_region_label, get_region_levels

DIMENSIONS = ("city", "product", "time", "province")


def parse_list(val: Optional[str]) -> List[str]:
    """把「英文逗号分隔」的筛选串解析成列表（去空白、丢空项）。"""
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip() != ""]


@dataclass
class Layout:
    """「地域 × 品类 × 时间」立方体的布局（拆表轴 / 行 / 列 + 合并开关）。

    ``province_mode`` / ``province_expanded``：仅 dimension=province（省份维度）为 True，
    表示「每个省份一张子表，表内 行=省内城市、列=品类」的省份展开视图。
    """

    fm: Dict[str, str]
    cfg: dict
    table: str
    split_col: Optional[str]
    row_col: Optional[str]
    col_col: Optional[str]
    merge_for: Dict[str, bool]
    group_cols: List[sql.Identifier]
    split_name: str
    row_name: str
    col_name: str
    region_name: str
    region_col: str
    region_list: List[str]
    product_list: List[str]
    province_mode: bool
    province_expanded: bool


def compute_layout(
    db_key: str,
    dimension: str,
    region_level: str,
    cities: Optional[str],
    provinces: Optional[str],
    products: Optional[str],
    merge_months: bool,
    merge_cities: bool,
    merge_products: bool,
    merge_province_cities: bool,
) -> Layout:
    """计算立方体布局（盒数 / 门店数统计共用的唯一入口）。

    三种维度本质只是「哪根轴当拆表轴」不同，地域级别只决定「哪根轴当地域轴」；
    把布局收敛到一处后，「单窗口查询」「同比环比多窗口查询」「门店数查询」共用同一套布局。

    ``merge_province_cities`` 只在 dimension=province（省份维度）下生效：用于把行轴上的
    「省内城市」折叠成整省合计（一省一行）。其余维度的地域只有一根轴，该参数不参与布局。
    """
    fm = get_field_map(db_key)
    if not fm:
        raise HTTPException(400, f"该统计功能暂不支持数据库: {db_key}")
    if dimension not in DIMENSIONS:
        raise HTTPException(400, f"不支持的维度: {dimension}，可选: {DIMENSIONS}")
    if dimension == "province" and "province_col" not in fm:
        raise HTTPException(400, f"该统计功能暂不支持省份维度: {db_key}")

    allowed_regions = get_region_levels(db_key)
    if region_level not in allowed_regions:
        raise HTTPException(400, f"不支持的地域级别: {region_level}，可选: {allowed_regions}")

    region_label = get_region_label(db_key)
    cfg = get_cfg(db_key)
    table = cfg["table"]

    # 地域轴：省份维度恒用省份（同时把城市用作行轴）；其余维度由地域级别二选一
    if "province_col" in fm and (region_level == "province" or dimension == "province"):
        region_col = fm["province_col"]
        region_name = "省份"
        region_list = parse_list(provinces)
    else:
        region_col = fm["city_col"]
        region_name = region_label
        region_list = parse_list(cities)

    product_list = parse_list(products)

    month_col = fm["month_col"]

    if dimension == "province":
        # 省份维度 = 旧省份模式那套「省份展开」布局，原样保留为一个独立维度：
        # 省份拆表、行=省内城市、列=品类。地域吃掉 省 + 市 两根轴后月份不再是轴，
        # 必须折叠 —— 否则月份的第四个分组键会在透视时互相覆盖（最后一个月顶掉其余月）。
        axes = [fm["province_col"], fm["city_col"], fm["product_col"], month_col]
        fold = {
            fm["province_col"]: merge_cities,
            fm["city_col"]: merge_province_cities,
            fm["product_col"]: merge_products,
            month_col: True,
        }
        name_of = {
            fm["province_col"]: "省份",
            fm["city_col"]: region_label,
            fm["product_col"]: "品类",
            month_col: "月份",
        }
        split_col, row_col, col_col = fm["province_col"], fm["city_col"], fm["product_col"]
    else:
        # 合并开关按「轴」记录：merge_* 表示「把这根轴折叠成合计」，与它当前扮演什么角色无关
        axes = [region_col, fm["product_col"], month_col]
        fold = {
            region_col: merge_cities,
            fm["product_col"]: merge_products,
            month_col: merge_months,
        }
        # 角色分配（纯展示层）：dimension 决定「哪根轴拆表」，其余轴按语义排在 行 / 列。
        # 地域级别不参与角色分配 —— 省份级别只是把 region_col 换成省份列，结构与城市级别一致。
        name_of = {region_col: region_name, fm["product_col"]: "品类", month_col: "月份"}
        if dimension == "city":
            split_col, row_col, col_col = region_col, fm["product_col"], month_col
        elif dimension == "product":
            split_col, row_col, col_col = fm["product_col"], region_col, month_col
        else:  # time
            split_col, row_col, col_col = month_col, region_col, fm["product_col"]

    split_name, row_name, col_name = name_of[split_col], name_of[row_col], name_of[col_col]

    merge_for: Dict[str, bool] = {c: fold.get(c, False) for c in axes}

    group_cols: List[sql.Identifier] = []
    seen_cols: set = set()
    for c in (split_col, row_col, col_col, month_col):
        if c and c not in seen_cols and c in merge_for and not merge_for[c]:
            seen_cols.add(c)
            group_cols.append(sql.Identifier(c))

    return Layout(
        fm=fm, cfg=cfg, table=table,
        split_col=split_col, row_col=row_col, col_col=col_col,
        merge_for=merge_for, group_cols=group_cols,
        split_name=split_name, row_name=row_name, col_name=col_name,
        region_name=region_name, region_col=region_col,
        region_list=region_list, product_list=product_list,
        # 只有「省份维度」才是省份展开视图；地域级别开关不再触发展开
        province_mode=dimension == "province", province_expanded=dimension == "province",
    )
