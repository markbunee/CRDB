# -*- coding: utf-8 -*-
"""SQL 查询构建器：把分页/搜索/排序/过滤参数编译成安全的 SQL。

优化点：
1. 搜索使用 GIN 全文搜索索引（@@）+ ILIKE 回退，避免全表扫描
2. 排序字段白名单校验，防止 SQL 注入
3. 支持 Keyset 分页（基于 id 游标），大数据量下性能恒定
4. 支持任意列等值过滤（filters 参数）

WHERE 条件采用「条件片段列表 + 参数列表」的方式统一组装，
确保占位符顺序与参数顺序严格一致。
"""
from typing import Any, Dict, List, Optional, Tuple

from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from ..database import get_conn
from ..models.schema_def import (
    get_cfg, col_names, search_columns, numeric_columns, date_columns,
    get_field_map,
)
from ..utils.serialization import jsonable_rows


def _sortable_cols(db_key: str) -> set:
    """允许排序的列白名单（id + 业务列）。"""
    return set(["id"] + col_names(db_key))


def build_search_conditions(db_key: str, search: str) -> Tuple[List[sql.Composable], list]:
    """构建搜索条件片段（不含 WHERE/OR，由调用方组装）。

    返回 (conditions, params)：
      - conditions: 多个 OR 条件片段列表
      - params: 对应参数列表

    优先使用全文搜索（@@ to_tsvector），对未建 FTS 索引的列回退 ILIKE。
    """
    if not search:
        return [], []

    cfg = get_cfg(db_key)
    fts_cols = cfg.get("fts_columns", [])
    like_cols = search_columns(db_key)

    conditions = []
    params = []

    # 1. 全文搜索（命中 GIN 索引，毫秒级）
    if fts_cols:
        words = search.strip().split()
        tsq = " & ".join(f"{w}:*" for w in words if w)
        if tsq:
            for c in fts_cols:
                conditions.append(
                    sql.SQL("to_tsvector('simple', coalesce({}, '')) @@ to_tsquery('simple', %s)").format(
                        sql.Identifier(c)))
                params.append(tsq)

    # 2. ILIKE 回退（覆盖非 FTS 的搜索列，如公司、省份）
    for c in like_cols:
        if c in fts_cols:
            continue
        conditions.append(sql.SQL("{}::text ILIKE %s").format(sql.Identifier(c)))
        params.append(f"%{search}%")

    return conditions, params


def _normalize_list(val: Any) -> List[Any]:
    """把字符串按逗号拆分，或把标量/列表统一成非空列表。"""
    if val is None:
        return []
    if isinstance(val, str):
        parts = [v.strip() for v in val.split(",") if v.strip() != ""]
        return parts
    if isinstance(val, (list, tuple)):
        return [v for v in val if v is not None and v != ""]
    return [val]


def build_filter_conditions(db_key: str, filters: Dict[str, Any]) -> Tuple[List[sql.Composable], list]:
    """构建过滤条件片段（不含 WHERE/AND，由调用方组装）。

    filters: {"省份": "广东", "商品编码": [1058746, 1086127]}
    字符串值会按英文逗号拆分，支持多选 IN 查询。
    """
    if not filters:
        return [], []

    valid_cols = set(col_names(db_key))
    conditions = []
    params = []
    for col, val in filters.items():
        if col not in valid_cols or val is None or val == "":
            continue
        vals = _normalize_list(val)
        if not vals:
            continue
        if len(vals) == 1:
            conditions.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
            params.append(vals[0])
        else:
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(vals))
            conditions.append(sql.SQL("{} IN ({})").format(sql.Identifier(col), placeholders))
            params.extend(vals)
    return conditions, params


def build_date_range_condition(
    db_key: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[Optional[sql.Composable], list]:
    """构建日期范围条件，自动选择第一张表的第一个 DATE 列。

    返回 (condition, params)。没有日期列或没有日期参数时返回 (None, [])。
    """
    cfg = get_cfg(db_key)
    date_cols = date_columns(db_key)
    if not date_cols:
        return None, []
    date_col = date_cols[0]

    conditions = []
    params = []
    if date_from:
        conditions.append(sql.SQL("{} >= %s").format(sql.Identifier(date_col)))
        params.append(date_from)
    if date_to:
        conditions.append(sql.SQL("{} <= %s").format(sql.Identifier(date_col)))
        params.append(date_to)
    if not conditions:
        return None, []
    return sql.SQL(" AND ").join(conditions), params


def _assemble_where(conditions: List[sql.Composable]) -> sql.Composable:
    """把条件片段列表组装成 WHERE 子句（用 AND 连接所有条件）。

    若 conditions 为空，返回空 SQL（不含 WHERE 关键字）。
    注意：搜索的 OR 条件需在外层包一层括号后作为单个条件传入。
    """
    if not conditions:
        return sql.SQL("")
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)


def query_rows(
    db_key: str,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    cursor: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """分页查询数据。

    返回: {"total", "page", "page_size", "rows", "next_cursor"}

    分页模式：
      - 传统分页：传 page/page_size（OFFSET 方式）
      - 游标分页：传 cursor（上一页最后一条 id），适合千万级数据无限滚动
    """
    cfg = get_cfg(db_key)
    table = cfg["table"]
    cols = col_names(db_key)

    # SELECT 列：id + 业务列
    select_cols = sql.SQL(", ").join(
        [sql.Identifier("id")] + [sql.Identifier(c) for c in cols]
    )

    # ---------- 组装 WHERE 条件 ----------
    # 收集所有条件片段，保证参数顺序与占位符顺序一致
    all_conditions: List[sql.Composable] = []
    all_params: list = []

    # 游标条件（放最前，配合 ORDER BY id DESC 用 id < %s）
    if cursor is not None:
        all_conditions.append(sql.SQL("id < %s"))
        all_params.append(cursor)

    # 日期范围条件
    date_cond, date_params = build_date_range_condition(db_key, date_from, date_to)
    if date_cond is not None:
        all_conditions.append(date_cond)
        all_params.extend(date_params)

    # 搜索条件（用 OR 连接后包一层括号，再 AND 进总条件）
    search_conds, search_params = build_search_conditions(db_key, search)
    if search_conds:
        or_group = sql.SQL("({})").format(sql.SQL(" OR ").join(search_conds))
        all_conditions.append(or_group)
        all_params.extend(search_params)

    # 过滤条件（支持等值与多选 IN）
    filter_conds, filter_params = build_filter_conditions(db_key, filters or {})
    all_conditions.extend(filter_conds)
    all_params.extend(filter_params)

    where = _assemble_where(all_conditions)

    # ---------- ORDER BY（白名单校验）----------
    sortable = _sortable_cols(db_key)
    if sort_by and sort_by in sortable:
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
        order = sql.SQL(" ORDER BY {} {}").format(
            sql.Identifier(sort_by), sql.SQL(direction))
    else:
        # 默认按 id 倒序（配合游标分页）
        order = sql.SQL(" ORDER BY id DESC")

    # ---------- COUNT（不带 ORDER BY/LIMIT）----------
    count_sql = sql.SQL("SELECT COUNT(*) FROM {}{}").format(
        sql.Identifier(table), where)

    # ---------- 分页数据 ----------
    limit = page_size
    if cursor is not None:
        # 游标分页：不需要 OFFSET
        page_sql = sql.SQL("SELECT {} FROM {}{}{} LIMIT %s").format(
            select_cols, sql.Identifier(table), where, order)
        page_params = all_params + [limit]
    else:
        # 传统 OFFSET 分页
        offset = (page - 1) * page_size
        page_sql = sql.SQL("SELECT {} FROM {}{}{} LIMIT %s OFFSET %s").format(
            select_cols, sql.Identifier(table), where, order)
        page_params = all_params + [limit, offset]

    with get_conn(db_key) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(count_sql, all_params)
            total = cur.fetchone()["count"]
            cur.execute(page_sql, page_params)
            rows = [dict(r) for r in cur.fetchall()]

    # 计算下一页游标
    next_cursor = None
    if cursor is not None and len(rows) == page_size and rows:
        next_cursor = rows[-1]["id"]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": jsonable_rows(rows),
        "next_cursor": next_cursor,
    }


def get_row_by_id(db_key: str, row_id: int) -> Optional[Dict[str, Any]]:
    """根据 id 获取单行（用于编辑表单预填，避免拉取整页数据）。"""
    cfg = get_cfg(db_key)
    cols = col_names(db_key)
    select_cols = sql.SQL(", ").join(
        [sql.Identifier("id")] + [sql.Identifier(c) for c in cols]
    )
    stmt = sql.SQL("SELECT {} FROM {} WHERE id = %s").format(
        select_cols, sql.Identifier(cfg["table"]))
    with get_conn(db_key) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(stmt, (row_id,))
            row = cur.fetchone()
    return jsonable_rows([dict(row)])[0] if row else None


def create_row(db_key: str, data: Dict[str, Any]) -> int:
    """新增一行，返回新 id。"""
    cfg = get_cfg(db_key)
    valid = set(col_names(db_key))
    # 按列顺序过滤出本次提供的字段
    provided = [c for c in col_names(db_key) if c in data and data[c] not in (None, "")]
    if not provided:
        raise ValueError("没有可插入的字段")
    vals = [data[c] for c in provided]
    stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(cfg["table"]),
        sql.SQL(", ").join(sql.Identifier(c) for c in provided),
        sql.SQL(", ").join(sql.Placeholder() * len(provided)),
    )
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, vals)
            new_id = cur.fetchone()[0]
    return new_id


def update_row(db_key: str, row_id: int, data: Dict[str, Any]) -> bool:
    """更新一行，返回是否命中。"""
    cfg = get_cfg(db_key)
    valid = set(col_names(db_key))
    provided = [c for c in valid if c in data]
    if not provided:
        raise ValueError("没有可更新的字段")
    sets = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(c)) for c in provided)
    vals = [data[c] for c in provided] + [row_id]
    stmt = sql.SQL("UPDATE {} SET {} WHERE id = %s").format(
        sql.Identifier(cfg["table"]), sets)
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, vals)
            affected = cur.rowcount
    return affected > 0


def delete_row(db_key: str, row_id: int) -> bool:
    """删除一行，返回是否命中。"""
    cfg = get_cfg(db_key)
    stmt = sql.SQL("DELETE FROM {} WHERE id = %s").format(
        sql.Identifier(cfg["table"]))
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (row_id,))
            affected = cur.rowcount
    return affected > 0


def delete_rows_by_date(db_key: str, date_value: str) -> int:
    """按日期列删除记录，返回被删除的行数。

    使用第一个 date_column 匹配。不同库的日期列名不同：
    - 大参林: 日期
    - 高济: 业务日期
    - 海王: 过账日期
    """
    cfg = get_cfg(db_key)
    date_cols = date_columns(db_key)
    if not date_cols:
        raise ValueError(f"库 {db_key} 没有日期列，无法按日期覆盖")
    date_col = date_cols[0]

    stmt = sql.SQL("DELETE FROM {} WHERE {} = %s").format(
        sql.Identifier(cfg["table"]),
        sql.Identifier(date_col),
    )
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (date_value,))
            affected = cur.rowcount
    return affected


def delete_rows_by_month_range(db_key: str, month_from: str, month_to: str) -> int:
    """按月度列删除指定月份范围的数据，返回被删除行数。

    使用 schema_def 中的 month_col 匹配（大参林=月度，海王=月度）。
    月份参数格式建议为 YYYY-MM-DD（月度列通常存每月首日）。
    """
    cfg = get_cfg(db_key)
    fm = get_field_map(db_key)
    month_col = fm.get("month_col")
    if not month_col:
        # 兼容没有 field_map 的库：使用第一个日期列（退化为按日期范围删）
        date_cols = date_columns(db_key)
        if not date_cols:
            raise ValueError(f"库 {db_key} 没有月度/日期列，无法按月份范围删除")
        month_col = date_cols[0]

    stmt = sql.SQL("DELETE FROM {} WHERE {} BETWEEN %s AND %s").format(
        sql.Identifier(cfg["table"]),
        sql.Identifier(month_col),
    )
    with get_conn(db_key, readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (month_from, month_to))
            affected = cur.rowcount
    return affected


def count_rows(db_key: str) -> int:
    """统计总行数。

    优先用 pg_class.reltuples 快速估算（建过索引/ANALYZE 后较准），
    避免千万级数据全表 COUNT 耗时。
    """
    cfg = get_cfg(db_key)
    stmt = sql.SQL(
        "SELECT reltuples::bigint FROM pg_class WHERE relname = %s"
    )
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(stmt, (cfg["table"],))
            row = cur.fetchone()
    if row and row[0] > 0:
        return int(row[0])
    # 回退到精确 COUNT（表很小时 reltuples 可能为 0）
    with get_conn(db_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(
                sql.Identifier(cfg["table"])))
            return cur.fetchone()[0]


def export_rows(db_key: str, batch_size: int = 5000) -> Dict[str, Any]:
    """导出全部数据（服务端游标分批读取，避免内存爆炸）。

    返回: {"columns": ["id", ...], "rows": [...]}
    注意：千万级数据请改用 /export/stream 流式 CSV 下载。
    """
    cfg = get_cfg(db_key)
    cols = col_names(db_key)
    all_cols = ["id"] + cols
    select_cols = sql.SQL(", ").join(sql.Identifier(c) for c in all_cols)
    stmt = sql.SQL("SELECT {} FROM {} ORDER BY id").format(
        select_cols, sql.Identifier(cfg["table"]))

    # 命名服务端游标，分批 fetch
    with get_conn(db_key) as conn:
        with conn.cursor("export_cursor", cursor_factory=RealDictCursor) as cur:
            cur.execute(stmt)
            rows = []
            while True:
                batch = cur.fetchmany(batch_size)
                if not batch:
                    break
                rows.extend(dict(r) for r in batch)
    return {"columns": all_cols, "rows": jsonable_rows(rows)}
