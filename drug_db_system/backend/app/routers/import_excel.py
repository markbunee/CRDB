# -*- coding: utf-8 -*-
"""Excel 导入接口：上传 Excel，按日期覆盖同日期旧数据，然后 COPY 批量写入。

性能优化要点：
1. 优先使用 calamine 引擎读取 xlsx，比 openpyxl 快 5~20 倍
2. 日期/数值列使用向量化处理，避免 iterrows 逐行遍历
3. 使用 DataFrame.to_csv() 生成 COPY 数据，比 Python csv.writer 快得多
4. 删除旧数据后一次性 COPY，导入完成后不再重新连接 COUNT
"""
import csv
import io
from typing import Set

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, File
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..logger import get_logger
from ..models.schema_def import get_cfg, col_names, date_columns
from ..services.data_import import parse_dates_vectorized, prepare_for_copy
from ..database import get_conn

router = APIRouter(prefix="/api/{db_key}", tags=["import"])
logger = get_logger("app.routers.import_excel")


@router.post("/rows/import_excel")
def import_excel(
    db_key: str = validate_db_key,
    file: UploadFile = File(...),
):
    """上传 Excel 文件，按日期覆盖同日期旧数据并批量导入。

    流程：
    1. 读取上传的 Excel 文件
    2. 提取第一日期列的所有唯一日期
    3. 删除数据库中这些日期的所有旧记录
    4. 用 COPY 命令批量写入新数据
    """
    cfg = get_cfg(db_key)
    cols = col_names(db_key)
    date_cols = date_columns(db_key)
    if not date_cols:
        raise HTTPException(400, f"库 {db_key} 没有日期列，无法按日期覆盖")
    date_col = date_cols[0]

    # 1. 读取上传的 Excel（优先使用 calamine 引擎）
    try:
        content = file.file.read()
        try:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, engine='calamine')
        except Exception:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0)
    except Exception as e:
        raise HTTPException(400, f"无法读取 Excel 文件: {e}")
    finally:
        file.file.close()

    excel_count = len(df)
    if excel_count == 0:
        raise HTTPException(400, "Excel 文件为空，没有数据可导入")

    # 校验列：查看 Excel 中有哪些目标列
    excel_cols = set(str(c) for c in df.columns)
    matched_cols = [c for c in cols if c in excel_cols]
    if not matched_cols:
        raise HTTPException(
            400, f"Excel 列头与数据库列不匹配。数据库列: {', '.join(cols[:8])}..."
        )
    missing = [c for c in cols if c not in excel_cols]
    if missing:
        logger.info("Excel 中缺少字段 %s，将作为 NULL 写入", missing)

    # 2. 提取唯一日期（若 Excel 中没有日期列，则提示用户）
    if date_col not in excel_cols:
        raise HTTPException(
            400,
            f"Excel 中未找到日期列「{date_col}」，请确保 Excel 包含该列。"
            f"数据库日期列: {', '.join(date_cols)}",
        )

    # 获取所有非空、成功解析的唯一日期（向量化）
    date_series = parse_dates_vectorized(df[date_col])
    unique_dates: Set[str] = set(
        date_series.dropna().dt.strftime('%Y-%m-%d').unique()
    )

    if not unique_dates:
        raise HTTPException(400, f"Excel 中「{date_col}」列没有有效日期，请检查数据")

    table = cfg["table"]
    sorted_dates = sorted(unique_dates)

    # 3. 删除数据库中这些日期的所有旧记录
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            # 用 IN 批量删除
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(sorted_dates))
            del_stmt = sql.SQL("DELETE FROM {} WHERE {} IN ({})").format(
                sql.Identifier(table),
                sql.Identifier(date_col),
                placeholders,
            )
            cur.execute(del_stmt, sorted_dates)
            deleted = cur.rowcount

        logger.info(
            "导入 %s: 覆盖日期 %s → 删除 %d 条旧记录，准备写入 %d 条",
            db_key, sorted_dates, deleted, excel_count,
        )

        # 4. 用 COPY 批量写入（向量化生成 CSV）
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

        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
        copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(
            sql.Identifier(table), cols_sql)

        with conn.cursor() as cur:
            cur.copy_expert(copy_stmt, buf)

    msg = (
        f"导入完成：从 Excel 读取 {excel_count} 行，"
        f"覆盖 {len(sorted_dates)} 个日期（{', '.join(sorted_dates[:5])}"
        + ("..." if len(sorted_dates) > 5 else "")
        + f"），删除旧记录 {deleted} 条，写入新记录 {excel_count} 条"
    )

    return {
        "inserted": excel_count,
        "deleted": deleted,
        "dates": sorted_dates,
        "excel_rows": excel_count,
        "message": msg,
    }
