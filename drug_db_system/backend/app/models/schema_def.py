# -*- coding: utf-8 -*-
"""三个数据库的表结构定义 + 索引/全文搜索元数据。

相比旧版 schema_def.py 的优化：
1. 日期类字段从 TEXT 改为 DATE（支持范围查询、索引）
2. 新增 indexes 字段：声明每张表要建的索引
3. 新增 fts_columns 字段：声明需要建 GIN 全文搜索索引的列
4. 新增 search_columns 字段：限定搜索范围（避免对所有列 ILIKE）

注意：Excel 中日期可能是字符串（如 "2024-01" 或 "2024-01-15"），
导入时 data_import.py 会尝试解析；解析失败则存 NULL。
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
            # (中文字段名, PostgreSQL 类型)
            ("日期",         "DATE"),   # 原 TEXT → DATE
            ("月度",         "DATE"),   # "2024-01" → "2024-01-01"
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
            ("批号",         "BIGINT"),
            ("批准文号",     "TEXT"),
            ("有效期至",     "DATE"),   # 原 TEXT → DATE
            ("营运区",       "TEXT"),
            ("大区",         "TEXT"),
            ("医院名字",     "TEXT"),
            ("适应症",       "TEXT"),
            ("销售价格",     "NUMERIC(14,2)"),
            ("生产厂家",     "TEXT"),
        ],
        # 常用过滤字段 → B-tree 索引（每项: (索引名, [列名...])）
        "indexes": [
            ("idx_dashenlin_date",        ["日期"]),
            ("idx_dashenlin_month",       ["月度"]),
            ("idx_dashenlin_store_code",  ["门店编码"]),
            ("idx_dashenlin_prod_code",   ["商品编码"]),
            ("idx_dashenlin_province",    ["省份"]),
            ("idx_dashenlin_manufacturer",["生产厂家"]),
            # 复合索引：商品编码 + 日期（高频查询模式）
            ("idx_dashenlin_prod_date",   ["商品编码", "日期"]),
            ("idx_dashenlin_store_date",  ["门店编码", "日期"]),
        ],
        # 需要建 GIN 全文搜索索引的列
        "fts_columns": ["商品名称", "门店名称", "生产厂家"],
        # 关键字搜索时扫描的列（避免对低频长文本列 ILIKE）
        "search_columns": ["公司", "门店名称", "商品名称", "生产厂家", "省份", "城市"],
    },
    "gaoji": {
        "label": "高济",
        "table": "sales",
        "excel": "高济.xlsx",
        "columns": [
            ("业务日期",     "DATE"),   # 原 TEXT → DATE
            ("月度",         "DATE"),   # 原 TEXT → DATE
            ("企业名称",     "TEXT"),
            ("门店编码",     "TEXT"),
            ("门店名称",     "TEXT"),
            ("商品编码",     "BIGINT"),
            ("商品名称",     "TEXT"),
            ("规格",         "TEXT"),
            ("厂家名称",     "TEXT"),
            ("单位",         "TEXT"),
            ("生产批号",     "BIGINT"),
            ("生产日期",     "BIGINT"),  # Excel 中是 YYYYMMDD 数字，保留 BIGINT
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
        ],
        "fts_columns": ["商品名称", "门店名称", "厂家名称"],
        "search_columns": ["企业名称", "门店名称", "商品名称", "厂家名称", "供应商名称"],
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
            ("过账日期",     "DATE"),   # 原 TEXT → DATE
            ("月度",         "DATE"),   # 原 TEXT → DATE
            ("合同价",       "NUMERIC(14,2)"),
            ("事业部名称",   "TEXT"),
            ("事业部",       "TEXT"),
        ],
        "indexes": [
            ("idx_haiwang_date",        ["过账日期"]),
            ("idx_haiwang_month",       ["月度"]),
            ("idx_haiwang_store",       ["店号/区域ID"]),
            ("idx_haiwang_prod_code",   ["商品SAP编码"]),
            ("idx_haiwang_dept",        ["事业部"]),
            ("idx_haiwang_prod_date",   ["商品SAP编码", "过账日期"]),
        ],
        "fts_columns": ["商品名称", "店名/区域"],
        "search_columns": ["商品名称", "店名/区域", "事业部名称", "事业部"],
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
