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
from typing import Dict, List, Set, Tuple

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from psycopg2 import sql

from ..dependencies import validate_db_key
from ..logger import get_logger
from ..models.schema_def import get_cfg, col_names, date_columns
from ..services.data_import import parse_dates_vectorized, prepare_for_copy
from ..database import get_conn

router = APIRouter(prefix="/api/{db_key}", tags=["import"])
logger = get_logger("app.routers.import_excel")


def _read_upload_file(file: UploadFile) -> pd.DataFrame:
    """读取上传的 Excel/CSV 文件为 DataFrame，优先使用 calamine 引擎。"""
    try:
        content = file.file.read()
        try:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, engine='calamine')
        except Exception:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0)
    finally:
        file.file.close()
    return df


def _import_single_excel(
    db_key: str,
    file: UploadFile,
    overwrite_existing: bool = True,
) -> Dict[str, any]:
    """单个 Excel 导入逻辑，成功/失败均返回结构化结果。

    返回字段：
    - filename, inserted, deleted, dates, excel_rows, overwrite_existing, message
    - success, error（失败时 error 非空）
    """
    result: Dict[str, any] = {
        "filename": file.filename,
        "success": False,
        "inserted": 0,
        "deleted": 0,
        "dates": [],
        "excel_rows": 0,
        "overwrite_existing": overwrite_existing,
        "message": "",
        "error": None,
    }

    cfg = get_cfg(db_key)
    cols = col_names(db_key)
    date_cols = date_columns(db_key)
    if not date_cols:
        result["error"] = f"库 {db_key} 没有日期列，无法按日期覆盖"
        return result
    date_col = date_cols[0]

    # 1. 读取上传的 Excel（优先使用 calamine 引擎）
    try:
        df = _read_upload_file(file)
    except Exception as e:
        result["error"] = f"无法读取 Excel 文件: {e}"
        return result

    excel_count = len(df)
    result["excel_rows"] = excel_count
    if excel_count == 0:
        result["error"] = "Excel 文件为空，没有数据可导入"
        return result

    # 校验列：查看 Excel 中有哪些目标列
    excel_cols = set(str(c) for c in df.columns)
    matched_cols = [c for c in cols if c in excel_cols]
    if not matched_cols:
        result["error"] = (
            f"Excel 列头与数据库列不匹配。数据库列: {', '.join(cols[:8])}..."
        )
        return result
    missing = [c for c in cols if c not in excel_cols]
    if missing:
        logger.info("Excel 中缺少字段 %s，将作为 NULL 写入", missing)

    # 2. 提取唯一日期（若 Excel 中没有日期列，则提示用户）
    if date_col not in excel_cols:
        result["error"] = (
            f"Excel 中未找到日期列「{date_col}」，请确保 Excel 包含该列。"
            f"数据库日期列: {', '.join(date_cols)}"
        )
        return result

    # 获取所有非空、成功解析的唯一日期（向量化）
    date_series = parse_dates_vectorized(df[date_col])
    unique_dates: Set[str] = set(
        date_series.dropna().dt.strftime('%Y-%m-%d').unique()
    )

    if not unique_dates:
        result["error"] = f"Excel 中「{date_col}」列没有有效日期，请检查数据"
        return result

    table = cfg["table"]
    sorted_dates = sorted(unique_dates)
    result["dates"] = sorted_dates

    deleted = 0
    try:
        with get_conn(db_key, readonly=False) as conn:
            # 3. 若开启覆盖，删除数据库中这些日期的所有旧记录
            if overwrite_existing:
                with conn.cursor() as cur:
                    # 用 IN 批量删除
                    placeholders = sql.SQL(", ").join(
                        sql.Placeholder() * len(sorted_dates)
                    )
                    del_stmt = sql.SQL("DELETE FROM {} WHERE {} IN ({})").format(
                        sql.Identifier(table),
                        sql.Identifier(date_col),
                        placeholders,
                    )
                    cur.execute(del_stmt, sorted_dates)
                    deleted = cur.rowcount

                logger.info(
                    "导入 %s: 文件 %s 覆盖日期 %s → 删除 %d 条旧记录，准备写入 %d 条",
                    db_key, file.filename, sorted_dates, deleted, excel_count,
                )
            else:
                logger.info(
                    "导入 %s: 文件 %s 不覆盖旧数据，日期 %s，准备写入 %d 条",
                    db_key, file.filename, sorted_dates, excel_count,
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
            copy_stmt = sql.SQL(
                "COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')"
            ).format(sql.Identifier(table), cols_sql)

            with conn.cursor() as cur:
                cur.copy_expert(copy_stmt, buf)

        result["inserted"] = excel_count
        result["deleted"] = deleted
        result["success"] = True
        if overwrite_existing:
            result["message"] = (
                f"导入完成：读取 {excel_count} 行，"
                f"覆盖 {len(sorted_dates)} 个日期（{', '.join(sorted_dates[:5])}"
                + ("..." if len(sorted_dates) > 5 else "")
                + f"），删除旧记录 {deleted} 条，写入新记录 {excel_count} 条"
            )
        else:
            result["message"] = (
                f"导入完成：读取 {excel_count} 行，"
                f"涉及 {len(sorted_dates)} 个日期（{', '.join(sorted_dates[:5])}"
                + ("..." if len(sorted_dates) > 5 else "")
                + f"），未覆盖旧数据，直接写入新记录 {excel_count} 条"
            )
    except Exception as e:
        logger.exception("导入 %s 文件 %s 失败", db_key, file.filename)
        result["error"] = f"写入数据库失败: {e}"

    return result


@router.post("/rows/import_excel")
def import_excel(
    db_key: str = validate_db_key,
    file: UploadFile = File(...),
    overwrite_existing: bool = Form(True),
):
    """上传单个 Excel 文件，可选择按日期覆盖同日期旧数据并批量导入。"""
    result = _import_single_excel(db_key, file, overwrite_existing)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.post("/rows/import_excel_batch")
def import_excel_batch(
    db_key: str = validate_db_key,
    files: List[UploadFile] = File(..., description="多个 Excel 文件，后台会排队依次处理"),
    overwrite_existing: bool = Form(True, description="是否按日期覆盖旧数据"),
):
    """批量上传多个 Excel 文件，按顺序排队处理。

    说明：
    - 文件会按上传顺序逐个处理，每个文件独立事务
    - 若多文件包含相同日期且 overwrite_existing=True，后处理的文件会覆盖先处理的文件
    - 任意文件失败会记录错误，其余文件继续处理；接口返回每个文件的结果列表
    """
    if not files:
        raise HTTPException(400, "未上传任何文件")

    file_results: List[Dict[str, any]] = []
    total_inserted = 0
    total_deleted = 0
    success_count = 0
    failed_count = 0

    for idx, file in enumerate(files, start=1):
        logger.info("批量导入 %s: 开始处理第 %d/%d 个文件 %s", db_key, idx, len(files), file.filename)
        res = _import_single_excel(db_key, file, overwrite_existing)
        file_results.append(res)
        if res["success"]:
            total_inserted += res["inserted"]
            total_deleted += res["deleted"]
            success_count += 1
        else:
            failed_count += 1

    has_failure = failed_count > 0
    status_code = 207 if has_failure else 200
    msg = (
        f"批量导入完成：成功 {success_count} 个文件，失败 {failed_count} 个文件，"
        f"总写入 {total_inserted} 条，总删除 {total_deleted} 条"
    )

    return {
        "success": not has_failure,
        "total_files": len(files),
        "success_count": success_count,
        "failed_count": failed_count,
        "total_inserted": total_inserted,
        "total_deleted": total_deleted,
        "overwrite_existing": overwrite_existing,
        "message": msg,
        "file_results": file_results,
    }
