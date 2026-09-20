# -*- coding: utf-8 -*-
"""门店库存接口：/api/{db_key}/inventory

库存是「当天快照」：每次更新都是一份**完整的最新文件**，导入即全量覆盖（先 DELETE 再 COPY）。

- GET  /inventory/latest     库存概况：最新截至日期、可选日期列表、行数 / 门店数 / 品类数
- GET  /inventory/query      库存情况查询（按门店）：库存数量 / 品类数量 / 效期货数量 / 已过期数量
- GET  /inventory/turnover   动销率 = 实销门店数（非重复）÷ 库存门店数（非重复）
- POST /inventory/import     全量覆盖导入（管理员）

关键口径：
- **动销率**：分子 = 销售表在所选区间的 `COUNT(DISTINCT 门店编码)`；
  分母 = 库存表在所选截至日期的 `COUNT(DISTINCT 门店编码)`；两边套用相同的地域 / 品类过滤。
- **效期货**：`有效期至 <= 截至日期 + N 个月`（N 由 `expiry_months` 控制，默认 6 个月）。
  `有效期至` 以 ISO 文本存储，字符串比较即等价于日期比较，且不怕 '2028-02-30' 这类脏数据。
- **已过期**：`有效期至 < 截至日期`。
"""
import calendar
import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from psycopg2 import sql

from ..dependencies import get_current_user, validate_db_key
from ..database import get_conn
from ..logger import get_logger
from ..services.export_gate import ExportRejected, export_slot
from ..models.schema_def import (
    INVENTORY_COLUMNS,
    INVENTORY_INDEXES,
    get_cfg,
    get_field_map,
    get_inventory_table,
    inventory_columns,
    supports_inventory,
    supports_store_type,
)

router = APIRouter(prefix="/api/{db_key}", tags=["inventory"])
logger = get_logger("app.routers.inventory")

# 库存表里写死的列名（与 Excel 表头、INVENTORY_COLUMNS 一致）
COL_DATE = "日期"
COL_QTY = "数量"
COL_STORE = "门店编码"
COL_STORE_NAME = "门店名称"
COL_PRODUCT = "商品编码"
COL_CITY = "城市"
COL_PROVINCE = "省份"
COL_EXPIRY = "有效期至"

DATE_COLS = [c for c, t in INVENTORY_COLUMNS if t == "DATE"]


# ---------- 通用工具 ----------
def _check_support(db_key: str) -> None:
    if not supports_inventory(db_key):
        raise HTTPException(400, f"该库暂未开放库存功能: {db_key}")


def _parse_list(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip() != ""]


def _add_months(date_str: str, months: int) -> str:
    """在 YYYY-MM-DD 上加 N 个月（日期溢出时取当月最后一天）。"""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    total = d.year * 12 + d.month - 1 + months
    y, m = total // 12, total % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return f"{y:04d}-{m:02d}-{day:02d}"


def _ensure_table(db_key: str) -> str:
    """库存表不存在则建表 + 建索引（幂等，任何读接口前调用）。"""
    table = get_inventory_table()
    col_defs = [
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(typ))
        for name, typ in INVENTORY_COLUMNS
    ]
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} (\n    id BIGSERIAL PRIMARY KEY,\n    {}\n)"
                ).format(
                    sql.Identifier(table),
                    sql.SQL(",\n    ").join(col_defs),
                )
            )
            for idx_name, idx_cols in INVENTORY_INDEXES:
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                        sql.Identifier(idx_name),
                        sql.Identifier(table),
                        sql.SQL(", ").join(sql.Identifier(c) for c in idx_cols),
                    )
                )
        conn.commit()
    return table


def _store_type_expr() -> sql.Composable:
    """连锁/加盟 CASE 表达式（库存表与销售表均含 大区/营运区 列，大参林满足）。

    大区或营运区字段含「加盟」二字即加盟，否则连锁；与看板/统计口径一致。
    """
    return sql.SQL(
        "CASE WHEN {daqu} LIKE '%%加盟%%' OR {yingyun} LIKE '%%加盟%%' "
        "THEN '加盟' ELSE '连锁' END"
    ).format(daqu=sql.Identifier("大区"), yingyun=sql.Identifier("营运区"))


def _inv_where(
    inv_date: Optional[str],
    products: List[str],
    cities: List[str],
    provinces: List[str],
    stores: List[str],
    store_type: str = "all",
) -> Tuple[sql.Composable, list]:
    conditions: List[sql.Composable] = []
    params: list = []
    if inv_date:
        conditions.append(sql.SQL("{} = %s").format(sql.Identifier(COL_DATE)))
        params.append(inv_date)
    if products:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(COL_PRODUCT),
                sql.SQL(", ").join(sql.Placeholder() * len(products)),
            )
        )
        params.extend(products)
    if cities:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(COL_CITY),
                sql.SQL(", ").join(sql.Placeholder() * len(cities)),
            )
        )
        params.extend(cities)
    if provinces:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(COL_PROVINCE),
                sql.SQL(", ").join(sql.Placeholder() * len(provinces)),
            )
        )
        params.extend(provinces)
    if stores:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(COL_STORE_NAME),
                sql.SQL(", ").join(sql.Placeholder() * len(stores)),
            )
        )
        params.extend(stores)
    if store_type in ("chain", "franchise"):
        label = "连锁" if store_type == "chain" else "加盟"
        conditions.append(sql.SQL("({}) = %s").format(_store_type_expr()))
        params.append(label)
    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    return where, params


def _sales_where(
    fm: Dict[str, str],
    date_from: Optional[str],
    date_to: Optional[str],
    products: List[str],
    cities: List[str],
    provinces: List[str],
    db_key: str = "",
    store_type: str = "all",
) -> Tuple[sql.Composable, list]:
    """销售表过滤（与统计功能同口径：日期列 + 品类 + 地域）。"""
    conditions: List[sql.Composable] = []
    params: list = []
    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(sql.Identifier(fm["date_col"])))
        params.append(date_to)
    if products:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(fm["product_col"]),
                sql.SQL(", ").join(sql.Placeholder() * len(products)),
            )
        )
        params.extend(products)
    if cities:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(fm["city_col"]),
                sql.SQL(", ").join(sql.Placeholder() * len(cities)),
            )
        )
        params.extend(cities)
    if provinces and "province_col" in fm:
        conditions.append(
            sql.SQL("{} IN ({})").format(
                sql.Identifier(fm["province_col"]),
                sql.SQL(", ").join(sql.Placeholder() * len(provinces)),
            )
        )
        params.extend(provinces)
    if store_type in ("chain", "franchise") and supports_store_type(db_key):
        label = "连锁" if store_type == "chain" else "加盟"
        conditions.append(sql.SQL("({}) = %s").format(_store_type_expr()))
        params.append(label)
    where = sql.SQL("")
    if conditions:
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    return where, params


def _rows(cur) -> List[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _latest_date(db_key: str) -> Optional[str]:
    table = get_inventory_table()
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT MAX({}) FROM {}").format(
                sql.Identifier(COL_DATE), sql.Identifier(table)))
            mx = cur.fetchone()[0]
    return mx.strftime("%Y-%m-%d") if mx else None


# ---------- 库存概况 ----------
@router.get("/inventory/latest")
def inventory_latest(db_key: str = validate_db_key):
    """库存概况：最新截至日期 + 可选日期列表 + 行数 / 门店数 / 品类数。"""
    _check_support(db_key)
    table = _ensure_table(db_key)
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT MAX({d}) AS max_date, COUNT(*) AS rows, "
                    "COUNT(DISTINCT {s}) AS stores, COUNT(DISTINCT {p}) AS cats FROM {t}"
                ).format(
                    d=sql.Identifier(COL_DATE),
                    s=sql.Identifier(COL_STORE),
                    p=sql.Identifier(COL_PRODUCT),
                    t=sql.Identifier(table),
                )
            )
            stat = _rows(cur)[0]
            cur.execute(
                sql.SQL(
                    "SELECT DISTINCT {d} FROM {t} WHERE {d} IS NOT NULL "
                    "ORDER BY {d} DESC LIMIT 30"
                ).format(d=sql.Identifier(COL_DATE), t=sql.Identifier(table))
            )
            dates = [r.strftime("%Y-%m-%d") for r, in cur.fetchall()]
    mx = stat["max_date"]
    return {
        "date": mx.strftime("%Y-%m-%d") if mx else None,
        "dates": dates,
        "rows": int(stat["rows"] or 0),
        "stores": int(stat["stores"] or 0),
        "cats": int(stat["cats"] or 0),
    }


# ---------- 库存情况查询 ----------
@router.get("/inventory/query")
def inventory_query(
    db_key: str = validate_db_key,
    date: Optional[str] = Query(None, description="库存截至日期 YYYY-MM-DD，留空取最新"),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    stores: Optional[str] = Query(None),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
    expiry_months: int = Query(6, ge=0, le=60, description="效期货：有效期至 ≤ 截至日期 + N 个月"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("qty", description="qty | cat_cnt | expiry_qty | store_code"),
    sort_dir: str = Query("desc"),
):
    """按门店查询库存：库存数量 / 品类数量 / 效期货数量 / 已过期数量。"""
    _check_support(db_key)
    table = _ensure_table(db_key)

    base_date = date or _latest_date(db_key)
    threshold = _add_months(base_date, expiry_months) if base_date else None

    where, wparams = _inv_where(
        base_date,
        _parse_list(products),
        _parse_list(cities),
        _parse_list(provinces),
        _parse_list(stores),
        store_type,
    )

    exp_id = sql.Identifier(COL_EXPIRY)
    qty_id = sql.Identifier(COL_QTY)
    # 效期 / 过期判定：有效期至为 ISO 文本，字符串比较等价于日期比较
    exp_cond = sql.SQL("({e} IS NOT NULL AND {e} <> '' AND {e} <= %s)").format(e=exp_id)
    expd_cond = sql.SQL("({e} IS NOT NULL AND {e} <> '' AND {e} < %s)").format(e=exp_id)

    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            # 汇总
            cur.execute(
                sql.SQL(
                    "SELECT COUNT(DISTINCT {s}) AS stores, COUNT(DISTINCT {p}) AS cats, "
                    "COALESCE(SUM({q}), 0) AS qty, "
                    "COALESCE(SUM(CASE WHEN {exp} THEN {q} ELSE 0 END), 0) AS expiry_qty, "
                    "COALESCE(SUM(CASE WHEN {expd} THEN {q} ELSE 0 END), 0) AS expired_qty "
                    "FROM {t}{w}"
                ).format(
                    s=sql.Identifier(COL_STORE),
                    p=sql.Identifier(COL_PRODUCT),
                    q=qty_id,
                    exp=exp_cond,
                    expd=expd_cond,
                    t=sql.Identifier(table),
                    w=where,
                ),
                [threshold, base_date] + wparams,
            )
            summary = _rows(cur)[0]

            # 门店总数（分页用）
            cur.execute(
                sql.SQL("SELECT COUNT(*) AS n FROM (SELECT {s} FROM {t}{w} GROUP BY {s}) g").format(
                    s=sql.Identifier(COL_STORE), t=sql.Identifier(table), w=where
                ),
                wparams,
            )
            total = int(_rows(cur)[0]["n"] or 0)

            # 明细
            order_map = {
                "qty": "qty",
                "cat_cnt": "cat_cnt",
                "expiry_qty": "expiry_qty",
                "store_code": "store_code",
            }
            ob = sql.Identifier(order_map.get(sort_by, "qty"))
            od = sql.SQL("ASC") if sort_dir.lower() == "asc" else sql.SQL("DESC")
            cur.execute(
                sql.SQL(
                    "SELECT {s} AS store_code, MIN({sn}) AS store_name, MIN({c}) AS city, "
                    "COALESCE(SUM({q}), 0) AS qty, COUNT(DISTINCT {p}) AS cat_cnt, "
                    "COALESCE(SUM(CASE WHEN {exp} THEN {q} ELSE 0 END), 0) AS expiry_qty, "
                    "COALESCE(SUM(CASE WHEN {expd} THEN {q} ELSE 0 END), 0) AS expired_qty "
                    "FROM {t}{w} GROUP BY {s} ORDER BY {ob} {od} LIMIT %s OFFSET %s"
                ).format(
                    s=sql.Identifier(COL_STORE),
                    sn=sql.Identifier(COL_STORE_NAME),
                    c=sql.Identifier(COL_CITY),
                    q=qty_id,
                    p=sql.Identifier(COL_PRODUCT),
                    exp=exp_cond,
                    expd=expd_cond,
                    t=sql.Identifier(table),
                    w=where,
                    ob=ob,
                    od=od,
                ),
                [threshold, base_date] + wparams + [page_size, (page - 1) * page_size],
            )
            rows = _rows(cur)

    return {
        "date": base_date,
        "expiry_months": expiry_months,
        "expiry_threshold": threshold,
        "summary": {
            "stores": int(summary["stores"] or 0),
            "cats": int(summary["cats"] or 0),
            "qty": float(summary["qty"] or 0),
            "expiry_qty": float(summary["expiry_qty"] or 0),
            "expired_qty": float(summary["expired_qty"] or 0),
        },
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": rows,
    }


# ---------- 动销率 ----------
@router.get("/inventory/turnover")
def inventory_turnover(
    db_key: str = validate_db_key,
    date_from: Optional[str] = Query(None, description="销售区间开始"),
    date_to: Optional[str] = Query(None, description="销售区间结束"),
    inv_date: Optional[str] = Query(None, description="库存截至日期，留空取最新"),
    cities: Optional[str] = Query(None),
    provinces: Optional[str] = Query(None),
    products: Optional[str] = Query(None),
    store_type: str = Query("all", description="门店类型：all=全部, chain=连锁(直营), franchise=加盟"),
):
    """动销率 = 实销门店数（非重复计数）÷ 库存门店数（非重复计数）。

    同时按城市给出明细（销售门店数 / 库存门店数 / 动销率），便于定位低动销区域。
    """
    _check_support(db_key)
    fm = get_field_map(db_key)
    cfg = get_cfg(db_key)
    if not fm:
        raise HTTPException(400, f"该库不支持统计: {db_key}")
    inv_table = _ensure_table(db_key)

    plist = _parse_list(products)
    clist = _parse_list(cities)
    pvlist = _parse_list(provinces)
    base_date = inv_date or _latest_date(db_key)

    s_where, s_params = _sales_where(fm, date_from, date_to, plist, clist, pvlist, db_key, store_type)
    i_where, i_params = _inv_where(base_date, plist, clist, pvlist, [], store_type)

    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT COUNT(DISTINCT {s}) AS n FROM {t}{w}").format(
                    s=sql.Identifier(fm["store_col"]),
                    t=sql.Identifier(cfg["table"]),
                    w=s_where,
                ),
                s_params,
            )
            sales_stores = int(_rows(cur)[0]["n"] or 0)

            cur.execute(
                sql.SQL("SELECT COUNT(DISTINCT {s}) AS n FROM {t}{w}").format(
                    s=sql.Identifier(COL_STORE), t=sql.Identifier(inv_table), w=i_where
                ),
                i_params,
            )
            inv_stores = int(_rows(cur)[0]["n"] or 0)

            # 按城市明细
            cur.execute(
                sql.SQL(
                    "SELECT {c} AS city, COUNT(DISTINCT {s}) AS n FROM {t}{w} GROUP BY {c}"
                ).format(
                    c=sql.Identifier(fm["city_col"]),
                    s=sql.Identifier(fm["store_col"]),
                    t=sql.Identifier(cfg["table"]),
                    w=s_where,
                ),
                s_params,
            )
            sales_by_city = {r["city"]: int(r["n"] or 0) for r in _rows(cur)}

            cur.execute(
                sql.SQL(
                    "SELECT {c} AS city, COUNT(DISTINCT {s}) AS n FROM {t}{w} GROUP BY {c}"
                ).format(
                    c=sql.Identifier(COL_CITY),
                    s=sql.Identifier(COL_STORE),
                    t=sql.Identifier(inv_table),
                    w=i_where,
                ),
                i_params,
            )
            inv_by_city = {r["city"]: int(r["n"] or 0) for r in _rows(cur)}

    by_city = []
    for city in sorted(set(sales_by_city) | set(inv_by_city)):
        s_n = sales_by_city.get(city, 0)
        i_n = inv_by_city.get(city, 0)
        by_city.append(
            {
                "city": city or "未知",
                "sales_stores": s_n,
                "inventory_stores": i_n,
                "turnover_rate": round(s_n / i_n * 100, 2) if i_n else None,
            }
        )
    by_city.sort(key=lambda x: (x["turnover_rate"] is None, -(x["turnover_rate"] or 0)))

    return {
        "date_from": date_from,
        "date_to": date_to,
        "inv_date": base_date,
        "sales_stores": sales_stores,
        "inventory_stores": inv_stores,
        "turnover_rate": round(sales_stores / inv_stores * 100, 2) if inv_stores else None,
        "by_city": by_city,
    }


# ---------- 全量覆盖导入 ----------
def _clean_series(s: pd.Series) -> pd.Series:
    """列规整：数值去 .0、去空白、空值转 None（COPY 时写 NULL）。"""
    if pd.api.types.is_numeric_dtype(s):
        s = s.round().astype("Int64").astype("string")
    out = s.astype("object")
    return out.map(
        lambda v: None
        if (v is None or (not isinstance(v, (list, tuple, dict)) and pd.isna(v)))
        else str(v).strip()
    )


@router.post("/inventory/import")
async def inventory_import(
    db_key: str = validate_db_key,
    user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
):
    """导入库存文件（**全量覆盖**：先清空库存表再整表写入）。

    支持 .xlsx / .xls / .csv。库存是当天快照，每次更新都是一份完整的最新文件，
    因此不做增量合并；导入与导出共用「重负载闸门」，并发满时直接 429。
    """
    _check_support(db_key)
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "请上传 .xlsx / .xls / .csv 格式的库存文件")

    try:
        # wait=0：async 路由里不能阻塞事件循环
        with export_slot(user.get("id"), f"{db_key}/inventory_import", wait=0):
            return await _inventory_import_inner(db_key, file)
    except ExportRejected as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})


async def _inventory_import_inner(db_key: str, file: UploadFile) -> dict:
    """库存导入主体（在闸门内执行）。"""
    name = (file.filename or "").lower()
    content = await file.read()
    if name.endswith(".csv"):
        df = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                # dtype=str：库存里「门店编码/商品编码」等编号必须原样保留前导零
                df = pd.read_csv(
                    io.BytesIO(content), dtype=str, keep_default_na=False,
                    encoding=encoding, low_memory=False,
                )
                break
            except UnicodeDecodeError:
                continue
        if df is None:
            raise HTTPException(400, "无法解析 CSV（编码需为 UTF-8 或 GBK）")
    else:
        try:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, engine="calamine")
        except Exception:
            try:
                df = pd.read_excel(io.BytesIO(content), sheet_name=0)
            except Exception as e:
                raise HTTPException(400, f"无法解析 Excel: {e}")

    cols = inventory_columns()
    excel_cols = {str(c).strip(): c for c in df.columns}
    required = [COL_DATE, COL_STORE, COL_PRODUCT, COL_QTY]
    missing_required = [c for c in required if c not in excel_cols]
    if missing_required:
        raise HTTPException(400, f"库存文件缺少必要列: {missing_required}")

    # 缺失列补空（不能传 None 标量，否则 DataFrame 构造会报 "must pass an index"）
    out = pd.DataFrame(
        {
            c: (
                df[excel_cols[c]]
                if c in excel_cols
                else pd.Series([None] * len(df), index=df.index)
            )
            for c in cols
        }
    )

    # 日期列
    for c in DATE_COLS:
        out[c] = pd.to_datetime(out[c], errors="coerce").dt.strftime("%Y-%m-%d")
    # 数量：非数字按 0
    out[COL_QTY] = (
        pd.to_numeric(out[COL_QTY], errors="coerce")
        .fillna(0)
        .round()
        .astype("Int64")
        .astype("string")
    )
    # 其余列统一文本化
    for c in cols:
        if c not in DATE_COLS and c != COL_QTY:
            out[c] = _clean_series(out[c])

    table = _ensure_table(db_key)
    buf = io.StringIO()
    out.to_csv(
        buf,
        index=False,
        header=False,
        quoting=csv.QUOTE_MINIMAL,
        na_rep="",
        lineterminator="\n",
    )
    buf.seek(0)

    copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
    )

    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
            cur.copy_expert(copy_stmt, buf)
            cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
        conn.commit()

    logger.info("库存导入 %s：全量覆盖 %d 行", db_key, len(out))
    return {
        "db_key": db_key,
        "inserted": len(out),
        "date": _latest_date(db_key),
        "message": f"已全量覆盖 {len(out)} 行库存数据",
    }
