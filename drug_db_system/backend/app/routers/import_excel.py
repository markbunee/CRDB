# -*- coding: utf-8 -*-
"""数据导入接口：上传 Excel / CSV，按日期覆盖同日期旧数据，然后 COPY 批量写入。

支持格式：``.xlsx`` / ``.xls`` / ``.csv``（推荐 CSV——解析比 xlsx 快得多、内存也更省，
本系统导出的 CSV 可以直接原样再导回来）。

性能优化要点：
1. CSV 用 pandas C 引擎解析（``dtype=str`` 保留「00123」这类前导零编码）；
   xlsx 优先 calamine 引擎，比 openpyxl 快 5~20 倍
2. 日期/数值列使用向量化处理，避免 iterrows 逐行遍历
3. 使用 DataFrame.to_csv() 生成 COPY 数据，比 Python csv.writer 快得多
4. 删除旧数据后一次性 COPY，导入完成后不再重新连接 COUNT
"""
import csv
import io
from typing import Dict, List, Set, Tuple

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from psycopg2 import sql

from ..dependencies import validate_db_key, require_admin, require_permission
from ..logger import get_logger
from ..memory_guard import assert_memory_available, MemoryPressureError
from ..services.export_gate import ExportRejected, export_slot
from ..models.schema_def import get_cfg, col_names, date_columns
from ..services.data_import import parse_dates_vectorized, prepare_for_copy
from ..database import get_conn
from ..routers.dashboard import invalidate_dashboard_cache
from ..routers.filter_options import invalidate_filter_options_cache

# 导入会覆盖/写入业务数据，整组接口仅管理员可用
router = APIRouter(
    prefix="/api/{db_key}", tags=["import"], dependencies=[Depends(require_permission("import"))]
)
logger = get_logger("app.routers.import_excel")


def _read_csv_bytes(content: bytes) -> pd.DataFrame:
    """读取 CSV 字节流：先按 UTF-8（兼容 BOM）再退回 GB18030（Windows Excel 另存为）。

    ``dtype=str`` 是关键：CSV 没有类型信息，若交给 pandas 推断，
    「商品编码 00123」会被吃成 123、长条码会被转成科学计数法。
    全部按字符串读入，后续由 ``prepare_for_copy`` 统一做日期/数值归一。
    """
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return pd.read_csv(
                io.BytesIO(content),
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
                low_memory=False,
            )
        except UnicodeDecodeError as e:  # 编码不对，换下一个
            last_error = e
    raise last_error if last_error else ValueError("CSV 解析失败")


def _read_upload_file(file: UploadFile) -> pd.DataFrame:
    """读取上传的 Excel / CSV 文件为 DataFrame。

    - ``.csv`` / ``.txt``：走 CSV 分支（快、省内存，推荐）
    - 其它（``.xlsx`` / ``.xls``）：优先 calamine 引擎，失败再退回默认引擎
    """
    name = (file.filename or "").lower()
    try:
        content = file.file.read()
        if name.endswith((".csv", ".txt")):
            return _read_csv_bytes(content)
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

    # 【内存安全护栏·事中】Excel 已整表读入内存，若此时系统已触顶则中止，
    # 避免继续 prepare_for_copy / COPY 把内存撑爆（2 核 2G 设备尤其关键）。
    try:
        assert_memory_available("Excel导入")
    except MemoryPressureError as e:
        result["success"] = False
        result["error"] = f"导入被内存安全护栏拦截：{e}"
        logger.warning("导入 %s 因内存护栏中止: %s", db_key, e)
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
    user: dict = Depends(require_permission("import")),
    file: UploadFile = File(...),
    overwrite_existing: bool = Form(True),
):
    """上传单个 Excel / CSV 文件，可选择按日期覆盖同日期旧数据并批量导入。"""
    try:
        with export_slot(user.get("id"), f"{db_key}/import"):
            result = _import_single_excel(db_key, file, overwrite_existing)
    except ExportRejected as e:
        # 导入与导出同属重负载操作，共用闸门：并发满时直接 429，避免把数据库打满
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})
    if not result["success"]:
        raise HTTPException(400, result["error"])
    invalidate_dashboard_cache(db_key)
    invalidate_filter_options_cache(db_key)
    return result


@router.post("/rows/import_excel_batch")
def import_excel_batch(
    db_key: str = validate_db_key,
    user: dict = Depends(require_permission("import")),
    files: List[UploadFile] = File(..., description="多个 Excel / CSV 文件，后台会排队依次处理"),
    overwrite_existing: bool = Form(True, description="是否按日期覆盖旧数据"),
):
    """批量上传多个 Excel / CSV 文件，按顺序排队处理。

    说明：
    - 文件会按上传顺序逐个处理，每个文件独立事务
    - 若多文件包含相同日期且 overwrite_existing=True，后处理的文件会覆盖先处理的文件
    - 任意文件失败会记录错误，其余文件继续处理；接口返回每个文件的结果列表
    - 整体占用一个「重负载名额」：文件越多耗时越长，注意 Nginx 读超时（默认 600s）
    """
    if not files:
        raise HTTPException(400, "未上传任何文件")

    try:
        with export_slot(user.get("id"), f"{db_key}/import_batch"):
            return _import_batch_inner(db_key, files, overwrite_existing)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})


def _import_batch_inner(
    db_key: str,
    files: List[UploadFile],
    overwrite_existing: bool,
) -> Dict[str, any]:
    """批量导入主体（放在闸门内执行）。"""
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

    if success_count > 0:
        invalidate_dashboard_cache(db_key)
        invalidate_filter_options_cache(db_key)

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
