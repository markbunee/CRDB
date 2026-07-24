# -*- coding: utf-8 -*-
"""三个数据库的表结构定义 + 索引/全文搜索元数据 + 统计字段映射。

设计原则（便于后期维护）：
1. 每个库的「表结构」「索引」「搜索列」「统计字段映射」「查询过滤映射」
   全部集中在 SCHEMAS dict 中，新增库只需加一个 key。
2. field_map：统计功能（门店数/盒数/趋势）共用的字段映射，
   包含 date_col / month_col / store_col / qty_col / city_col / product_col，
   province_col 可选（海王无省份）。
3. filter_map：行查询/导出时前端参数 → 实际列名的映射。
4. region_levels：该库支持的地域级别（海王只有 city）。
5. region_label：地域维度的显示名称（大参林=城市，海王=事业部）。

性能优化（千万级数据）：
- B-tree 索引：单列 + 复合索引覆盖高频查询模式
- BRIN 索引：大表日期列用 BRIN 替代 B-tree，体积小 1000 倍
- GIN 全文搜索：商品名称/门店名称模糊搜索毫秒级
- 复合索引：(地域, 日期) / (品类, 日期) / (门店, 日期) 覆盖统计查询
"""

# 数值类型集合
NUMERIC_TYPES = {"BIGINT", "INTEGER", "SMALLINT", "NUMERIC", "REAL", "DOUBLE PRECISION"}
# 日期类型集合
DATE_TYPES = {"DATE"}

SCHEMAS = {
    "dashenlin": {
        "label": "大参林医药集团",
        "table": "sales",
        "excel": "大参林医药集团数据.xlsx",
        "columns": [
            ("日期",         "DATE"),
            ("月度",         "DATE"),
            ("公司",         "TEXT"),
            ("来源公司",     "TEXT"),
            ("门店编码",     "BIGINT"),
            ("门店名称",     "TEXT"),
            ("门店详细名称", "TEXT"),
            ("省份",         "TEXT"),
            ("城市",         "TEXT"),
            ("商品编码",     "BIGINT"),
            ("商品名称",     "TEXT"),
            ("规格",         "TEXT"),
            ("单位",         "TEXT"),
            ("数量",         "INTEGER"),
            ("批号",         "TEXT"),
            ("批准文号",     "TEXT"),
            ("有效期至",     "DATE"),
            ("营运区",       "TEXT"),
            ("大区",         "TEXT"),
            ("医院名字",     "TEXT"),
            ("适应症",       "TEXT"),
            ("销售价格",     "NUMERIC(14,2)"),
            ("生产厂家",     "TEXT"),
        ],
        "indexes": [
            ("idx_dashenlin_date",        ["日期"]),
            ("idx_dashenlin_month",       ["月度"]),
            ("idx_dashenlin_store_code",  ["门店编码"]),
            ("idx_dashenlin_prod_code",   ["商品编码"]),
            ("idx_dashenlin_province",    ["省份"]),
            ("idx_dashenlin_manufacturer",["生产厂家"]),
            ("idx_dashenlin_prod_date",   ["商品编码", "日期"]),
            ("idx_dashenlin_store_date",  ["门店编码", "日期"]),
            ("idx_dashenlin_city_date",   ["城市", "日期"]),
            ("idx_dashenlin_month_city",  ["月度", "城市"]),
        ],
        "brin_columns": ["日期", "月度"],
        "fts_columns": ["商品名称", "门店名称", "生产厂家"],
        "search_columns": ["公司", "门店名称", "商品名称", "生产厂家", "省份", "城市"],
        # 统计功能字段映射
        "field_map": {
            "date_col": "日期",
            "month_col": "月度",
            "store_col": "门店编码",
            "qty_col": "数量",
            "city_col": "城市",
            "province_col": "省份",
            "product_col": "商品编码",
        },
        # 行查询/导出过滤映射（前端参数名 → 实际列名）
        "filter_map": {
            "product_codes": "商品编码",
            "cities": "城市",
            "provinces": "省份",
        },
        "region_levels": ("city", "province"),
        "region_label": "城市",
    },
    "gaoji": {
        "label": "高济",
        "table": "sales",
        "excel": "高济.xlsx",
        "columns": [
            ("业务日期",     "DATE"),
            ("月度",         "DATE"),
            ("企业名称",     "TEXT"),
            ("城市",         "TEXT"),
            ("门店编码",     "TEXT"),
            ("门店名称",     "TEXT"),
            ("商品编码",     "BIGINT"),
            ("商品名称",     "TEXT"),
            ("规格",         "TEXT"),
            ("厂家名称",     "TEXT"),
            ("单位",         "TEXT"),
            ("生产批号",     "TEXT"),
            ("生产日期",     "BIGINT"),
            ("销售数量",     "INTEGER"),
            ("供应商名称",   "TEXT"),
        ],
        "indexes": [
            ("idx_gaoji_date",        ["业务日期"]),
            ("idx_gaoji_month",       ["月度"]),
            ("idx_gaoji_store_code",  ["门店编码"]),
            ("idx_gaoji_prod_code",   ["商品编码"]),
            ("idx_gaoji_manufacturer",["厂家名称"]),
            ("idx_gaoji_prod_date",   ["商品编码", "业务日期"]),
            ("idx_gaoji_city_date",   ["城市", "业务日期"]),
            ("idx_gaoji_month_city",  ["月度", "城市"]),
        ],
        "brin_columns": ["业务日期", "月度"],
        "fts_columns": ["商品名称", "门店名称", "厂家名称"],
        "search_columns": ["企业名称", "城市", "门店名称", "商品名称", "厂家名称", "供应商名称"],
        # 统计功能字段映射
        "field_map": {
            "date_col": "业务日期",
            "month_col": "月度",
            "store_col": "门店编码",
            "qty_col": "销售数量",
            "city_col": "城市",
            "product_col": "商品编码",
        },
        # 行查询/导出过滤映射
        "filter_map": {
            "product_codes": "商品编码",
            "cities": "城市",
        },
        "region_levels": ("city",),
        "region_label": "城市",
    },
    "haiwang": {
        "label": "海王",
        "table": "sales",
        "excel": "海王数据.xlsx",
        "columns": [
            ("类别",         "BIGINT"),
            ("商品SAP编码",  "BIGINT"),
            ("商品名称",     "TEXT"),
            ("规格",         "TEXT"),
            ("单位",         "TEXT"),
            ("店号/区域ID",  "TEXT"),
            ("店名/区域",    "TEXT"),
            ("销量",         "INTEGER"),
            ("过账日期",     "DATE"),
            ("月度",         "DATE"),
            ("合同价",       "NUMERIC(14,2)"),
            ("事业部名称",   "TEXT"),
            ("事业部",       "TEXT"),
        ],
        "indexes": [
            ("idx_haiwang_date",        ["过账日期"]),
            ("idx_haiwang_month",       ["月度"]),
            ("idx_haiwang_store",       ["店名/区域"]),
            ("idx_haiwang_prod_code",   ["商品SAP编码"]),
            ("idx_haiwang_dept",        ["事业部名称"]),
            ("idx_haiwang_prod_date",   ["商品SAP编码", "过账日期"]),
            ("idx_haiwang_store_date",  ["店名/区域", "过账日期"]),
            ("idx_haiwang_dept_date",   ["事业部名称", "过账日期"]),
            ("idx_haiwang_month_dept",  ["月度", "事业部名称"]),
        ],
        # BRIN 索引：千万级大表日期列，体积仅为 B-tree 的 1/1000
        "brin_columns": ["过账日期", "月度"],
        "fts_columns": ["商品名称", "店名/区域"],
        "search_columns": ["商品名称", "店名/区域", "事业部名称", "事业部"],
        # 统计功能字段映射
        "field_map": {
            "date_col": "过账日期",
            "month_col": "月度",
            "store_col": "店名/区域",
            "qty_col": "销量",
            "city_col": "事业部名称",
            "product_col": "商品SAP编码",
            # 无 province_col — 海王无省份区分
        },
        # 行查询/导出过滤映射
        "filter_map": {
            "product_codes": "商品SAP编码",
            "cities": "事业部名称",
        },
        "region_levels": ("city",),  # 仅城市(事业部)级别，无省份
        "region_label": "事业部",
    },
}


def get_cfg(db_key: str):
    """获取某个数据库的配置，不存在则抛 KeyError。"""
    return SCHEMAS[db_key]


def col_names(db_key: str):
    """返回某数据库所有列名列表。"""
    return [c for c, _ in SCHEMAS[db_key]["columns"]]


def numeric_columns(db_key: str):
    """返回数值类型的列名。"""
    return [name for name, typ in SCHEMAS[db_key]["columns"]
            if typ.split("(")[0] in NUMERIC_TYPES]


def date_columns(db_key: str):
    """返回日期类型的列名。"""
    return [name for name, typ in SCHEMAS[db_key]["columns"]
            if typ.split("(")[0] in DATE_TYPES]


def search_columns(db_key: str):
    """返回关键字搜索时扫描的列名。"""
    return SCHEMAS[db_key].get("search_columns", col_names(db_key))


def get_field_map(db_key: str) -> dict:
    """返回统计功能字段映射（date_col/month_col/store_col/qty_col/city_col/product_col，
    province_col 可选）。"""
    return SCHEMAS[db_key].get("field_map", {})


def get_filter_map(db_key: str) -> dict:
    """返回行查询/导出的前端参数 → 列名映射。"""
    return SCHEMAS[db_key].get("filter_map", {})


def get_region_levels(db_key: str) -> tuple:
    """返回该库支持的地域级别元组。"""
    return SCHEMAS[db_key].get("region_levels", ("city", "province"))


def get_region_label(db_key: str) -> str:
    """返回地域维度的显示名称。"""
    return SCHEMAS[db_key].get("region_label", "城市")


def supports_stats(db_key: str) -> bool:
    """该库是否支持统计功能（需有 field_map）。"""
    return bool(SCHEMAS[db_key].get("field_map"))
