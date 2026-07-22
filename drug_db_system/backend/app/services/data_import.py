# -*- coding: utf-8 -*-
"""数据导入服务：用 PostgreSQL COPY 命令批量导入 Excel 数据。

相比旧版 execute_batch 的优化：
1. COPY 命令比逐条 INSERT 快 10~50 倍
2. 日期字段自动解析（支持 "2024-01"、"2024-01-15"、"20240115" 等格式）
3. NaN/空值正确转 NULL
4. 导入完成后自动建索引（先导入数据再建索引，比先建索引再导入快）

典型性能：
  - 100 万行：约 10 秒（COPY）vs 5 分钟（execute_batch）
  - 1000 万行：约 2 分钟（COPY）vs 1 小时+（execute_batch）
"""
import csv
import io
import math
import os
from datetime import datetime
from typing import Any, List

import numpy as np
import pandas as pd
from psycopg2 import sql

from ..config import EXCEL_DIR
from ..database import direct_conn, admin_conn
from ..models.schema_def import SCHEMAS, get_cfg, col_names, date_columns


def parse_dates_vectorized(s: pd.Series) -> pd.Series:
    """批量解析日期列，返回 datetime64[ns]（NaT 表示解析失败）。

    优先使用 pandas 向量化解析；仅对整数型 YYYYMMDD/YYYYMM 做逐行处理。
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors='coerce')

    # 数值型：可能是 20240115 / 202401 这种整数日期
    if pd.api.types.is_numeric_dtype(s):
        s_str = s.round().astype('Int64').astype('string')
        parsed: List[Any] = []
        for x in s_str:
            if pd.isna(x):
                parsed.append(pd.NaT)
                continue
            if len(x) == 6:
                parsed.append(pd.to_datetime(x, format='%Y%m', errors='coerce'))
            elif len(x) == 8:
                parsed.append(pd.to_datetime(x, format='%Y%m%d', errors='coerce'))
            else:
                parsed.append(pd.NaT)
        return pd.Series(parsed, index=s.index)

    # 字符串/object：pandas 向量化解析已能覆盖绝大多数格式
    return pd.to_datetime(s, errors='coerce', dayfirst=False)


def format_numeric_series(s: pd.Series) -> pd.Series:
    """把数值列转为字符串，去掉整数后面的 .0，便于 COPY 写入。"""
    if not pd.api.types.is_numeric_dtype(s):
        return s

    is_na = s.isna()
    is_int_like = (s == s.round()) & (~is_na)

    out = pd.Series(index=s.index, dtype=object)
    out[is_na] = np.nan
    # 对整数部分使用 nullable Int64 避免 .0
    int_part = s[is_int_like].round().astype('Int64').astype('string')
    out[is_int_like] = int_part.replace('<NA>', np.nan)
    out[~is_int_like & ~is_na] = s[~is_int_like & ~is_na].astype(str)
    return out


def prepare_for_copy(df: pd.DataFrame, db_key: str) -> pd.DataFrame:
    """把原始 DataFrame 处理成适合 PostgreSQL COPY 的 DataFrame。

    - 按数据库列顺序重排
    - 日期列批量解析并格式化为 YYYY-MM-DD
    - 数值列去掉 .0
    - 缺失值保持 NaN/NaT，由 to_csv(na_rep='') 输出为空字符串
    """
    cols = col_names(db_key)
    date_cols = set(date_columns(db_key))

    out = pd.DataFrame({c: df.get(c) for c in cols})

    for c in cols:
        if c in date_cols:
            out[c] = parse_dates_vectorized(out[c]).dt.strftime('%Y-%m-%d')
        elif pd.api.types.is_numeric_dtype(out[c]):
            out[c] = format_numeric_series(out[c])
        else:
            # 转为 object，避免 category 等非常规类型影响 to_csv
            out[c] = out[c].astype('object')

    return out


def parse_date(v):
    """尝试把各种日期格式解析成 datetime.date。

    支持:
      - "2024-01-15" / "2024/01/15"
      - "2024-01"（月度，补成 2024-01-01）
      - 20240115（整数 YYYYMMDD）
      - pandas Timestamp
      - datetime.date / datetime.datetime
    解析失败返回 None。
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass

    # 已经是 date/datetime
    if isinstance(v, datetime):
        return v.date()
    import datetime as _dt
    if isinstance(v, _dt.date):
        return v

    # pandas Timestamp
    if isinstance(v, pd.Timestamp):
        return v.date()

    # 字符串
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # 尝试常见格式
        formats = [
            "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
            "%Y-%m", "%Y/%m", "%Y.%m",
            "%Y%m%d", "%Y%m",
            "%Y年%m月%d日", "%Y年%m月",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        # 最后尝试 pandas 解析
        try:
            return pd.to_datetime(s).date()
        except Exception:
            return None

    # 整数：可能是 YYYYMMDD 或 YYYYMM
    if isinstance(v, (int, float)):
        try:
            if not math.isnan(v):
                s = str(int(v))
                if len(s) == 8:
                    return datetime.strptime(s, "%Y%m%d").date()
                if len(s) == 6:
                    return datetime.strptime(s, "%Y%m").date()
        except (ValueError, TypeError):
            return None

    return None


def create_database(admin_conn_obj, db_name: str):
    """创建数据库（若不存在）。"""
    with admin_conn_obj.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone():
            print(f"  [skip] 数据库 {db_name} 已存在")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(
                sql.Identifier(db_name)))
            print(f"  [ok]   创建数据库 {db_name}")


def create_table(conn, db_key: str):
    """建表（不带索引，索引在数据导入后单独建以加速导入）。"""
    cfg = get_cfg(db_key)
    table = cfg["table"]
    col_defs = [sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(typ))
                for name, typ in cfg["columns"]]
    stmt = sql.SQL(
        "CREATE TABLE IF NOT EXISTS {} (\n    id BIGSERIAL PRIMARY KEY,\n    {}\n)"
    ).format(
        sql.Identifier(table),
        sql.SQL(",\n    ").join(col_defs),
    )
    with conn.cursor() as cur:
        cur.execute(stmt)
    conn.commit()
    print(f"  [ok]   建表 {table}（{len(cfg['columns'])} 列）")


def drop_table_if_exists(conn, db_key: str):
    """若表已存在则删除（用于重新导入）。"""
    cfg = get_cfg(db_key)
    stmt = sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
        sql.Identifier(cfg["table"]))
    with conn.cursor() as cur:
        cur.execute(stmt)
    conn.commit()


def import_excel_copy(conn, db_key: str, replace: bool = True) -> int:
    """用 COPY 命令导入 Excel 数据，返回导入行数。

    replace=True 时先 DROP 再建表（避免重复导入）。
    使用向量化方式生成 CSV，避免 iterrows 逐行遍历的性能瓶颈。
    """
    cfg = get_cfg(db_key)
    xlsx_path = os.path.join(EXCEL_DIR, cfg["excel"])
    if not os.path.exists(xlsx_path):
        print(f"  [warn] 找不到 Excel 文件: {xlsx_path}，跳过导入")
        return 0

    if replace:
        drop_table_if_exists(conn, db_key)
    create_table(conn, db_key)

    print(f"  [info] 读取 Excel: {cfg['excel']}")
    try:
        df = pd.read_excel(xlsx_path, sheet_name=0, engine='calamine')
    except Exception:
        df = pd.read_excel(xlsx_path, sheet_name=0)

    cols = col_names(db_key)

    # 校验列
    excel_cols = [str(c) for c in df.columns]
    missing = [c for c in cols if c not in excel_cols]
    if missing:
        print(f"  [warn] Excel 中缺少字段 {missing}，将作为 NULL 写入")

    # 向量化生成 CSV 内存流
    print(f"  [info] 准备 COPY 数据（{len(df)} 行）...")
    copy_df = prepare_for_copy(df, db_key)
    buf = io.StringIO()
    copy_df.to_csv(
        buf,
        index=False,
        header=False,
        quoting=csv.QUOTE_MINIMAL,
        na_rep='',
        lineterminator='\n',
    )
    buf.seek(0)

    table = cfg["table"]
    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(
        sql.Identifier(table), cols_sql)

    with conn.cursor() as cur:
        cur.copy_expert(copy_stmt, buf)
    conn.commit()
    print(f"  [ok]   导入 {len(df)} 行 → {table}")
    return len(df)


def create_indexes(conn, db_key: str):
    """为指定库的表创建所有索引（在数据导入后调用）。

    indexes 格式: [(索引名, [列名...]), ...]
    fts_columns: 需要建 GIN 全文搜索索引的列名列表
    """
    cfg = get_cfg(db_key)
    table = cfg["table"]
    indexes = cfg.get("indexes", [])
    fts_cols = cfg.get("fts_columns", [])

    if not indexes and not fts_cols:
        return

    print(f"  [info] 为 {table} 创建索引（{len(indexes)} 个 B-tree + {len(fts_cols)} 个 GIN）...")
    with conn.cursor() as cur:
        for idx_name, idx_cols in indexes:
            # 列名列表 → ("col1", "col2") 形式，用 Identifier 包裹防注入
            cols_expr = sql.SQL(", ").join(sql.Identifier(c) for c in idx_cols)
            stmt = sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                sql.Identifier(idx_name),
                sql.Identifier(table),
                cols_expr,
            )
            cur.execute(stmt)
        # 全文搜索 GIN 索引
        for col in fts_cols:
            idx_name = f"idx_{db_key}_fts_{col}"
            stmt = sql.SQL(
                "CREATE INDEX IF NOT EXISTS {} ON {} USING GIN "
                "(to_tsvector('simple', coalesce({}, '')))"
            ).format(
                sql.Identifier(idx_name),
                sql.Identifier(table),
                sql.Identifier(col),
            )
            cur.execute(stmt)
    conn.commit()
    print(f"  [ok]   索引创建完成")


def analyze_table(conn, db_key: str):
    """更新表统计信息（让优化器选对执行计划）。"""
    cfg = get_cfg(db_key)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(cfg["table"])))
    conn.commit()


def import_all(replace: bool = True):
    """一键建库 + 建表 + 导入 + 建索引 + 分析。"""
    from ..config import PG_HOST, PG_PORT, PG_USER, DB_NAMES

    print("=" * 60)
    print("开始初始化数据库")
    print(f"  主机: {PG_HOST}:{PG_PORT}  账号: {PG_USER}")
    print("=" * 60)

    try:
        with admin_conn() as admin:
            for key, cfg in SCHEMAS.items():
                db_name = DB_NAMES[key]
                print(f"\n--- {cfg['label']}（库: {db_name}）---")
                create_database(admin, db_name)

                with direct_conn(db_name) as conn:
                    import_excel_copy(conn, key, replace=replace)
                    create_indexes(conn, key)
                    analyze_table(conn, key)
        print("\n[完成] 全部初始化成功！")
    except Exception as e:
        print(f"\n[错误] {e}")
        raise
