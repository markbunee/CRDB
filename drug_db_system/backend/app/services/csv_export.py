# -*- coding: utf-8 -*-
"""CSV 导出公共设施：全站导出统一走这里（替代原来的 xlsx 生成）。

为什么要从 xlsx 换成 CSV
------------------------
- xlsx 本质是 zip + XML，写一个单元格要维护共享字符串表、样式、列宽，
  CPU 与内存开销都大；百万行导出往往几十秒到几分钟，期间吃满一个核。
- CSV 是纯文本追加，几乎零额外开销，可以「边查边写」，内存恒定，
  同等数据量通常快 5~20 倍，且不需要 openpyxl/xlsxwriter 这类重依赖。
- Excel 双击即可打开 CSV；本模块统一加 UTF-8 BOM，中文不乱码。

安全设计（不只求快，还要不把服务器挤崩）
----------------------------------------
- **分块输出**：攒够 ``EXPORT_CHUNK_BYTES``（默认 256KB）才 yield 一次。
  原 ``/export/stream`` 是「每行 yield 一次」，几十万行会产生几十万个小 chunk，
  框架调度 + 网络分包的开销比查库本身还大。
- **服务端游标**：结果集留在 PostgreSQL 侧，按 ``EXPORT_FETCH_SIZE``（默认 5000）
  分批取回，内存占用恒定，千万级也不会把 API 容器撑爆。
- **语句超时**：导出连接上 ``SET LOCAL statement_timeout``（见 export_gate），
  SQL 跑飞时由 PG 主动杀查询，不会一直占着连接与快照。
- **行数上限**：到量即停，并通过响应头 ``X-Export-Truncated: 1`` 告知前端。
- **CSV 注入防护**：字符串型单元格若以 ``= + - @ 制表符`` 开头，补一个前导单引号，
  防止用 Excel 打开时被当成公式执行（数字类型不受影响，负数、金额正常）。
"""
import contextlib
import csv
import io
import itertools
import os
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

from ..database import pools
from ..logger import get_logger
from ..utils.serialization import jsonable
from .export_gate import ExportSlot, limits

logger = get_logger("app.services.csv_export")

BOM = "\ufeff"
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
# 每块输出多少字节：太小（逐行）→ chunk 风暴；太大 → 首字节延迟高、内存占用上升
CHUNK_BYTES = max(8192, int(os.environ.get("EXPORT_CHUNK_BYTES", "262144") or 262144))
# 服务端游标每次 FETCH 的行数
FETCH_SIZE = max(100, int(os.environ.get("EXPORT_FETCH_SIZE", "5000") or 5000))

_cursor_seq = itertools.count(1)

# 触发行数上限时追加在文件末尾的提示行（流式响应无法在表头阶段预知总行数）
TRUNCATION_NOTICE = "【导出提示】数据量超过单次导出上限 {cap} 行，结果已截断，请缩小筛选范围后重试"

# 危险前缀：Excel/WPS 会把以这些字符开头的文本当公式执行
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """把单个值转成 CSV 单元格文本（含公式注入防护）。"""
    if value is None:
        return ""
    value = jsonable(value)
    text = str(value)
    # 只对「本来就是字符串」的值做前缀转义；数字（含负数、金额）绝不动，避免破坏数值含义
    if isinstance(value, str) and text[:1] in _FORMULA_PREFIX:
        return "'" + text
    return text


class _ChunkBuffer:
    """把 csv.writer 的输出攒成大块，减少 yield 次数。"""

    def __init__(self, chunk_bytes: int = CHUNK_BYTES):
        self._buf = io.StringIO()
        self._chunk_bytes = chunk_bytes
        self.writer = csv.writer(self._buf, lineterminator="\r\n")

    def should_flush(self) -> bool:
        return self._buf.tell() >= self._chunk_bytes

    def write_raw(self, text: str) -> None:
        """直接写入原始文本（例如 UTF-8 BOM，不能走 csv.writer）。"""
        self._buf.write(text)

    def flush(self) -> str:
        if self._buf.tell() == 0:
            return ""
        data = self._buf.getvalue()
        self._buf.seek(0)
        self._buf.truncate(0)
        return data


@contextlib.contextmanager
def export_cursor(
    db_key: str,
    stmt,
    params: Optional[Sequence] = None,
    fetch_size: int = FETCH_SIZE,
    cursor_factory=None,
):
    """打开一个「导出专用」游标：独立连接 + 命名服务端游标 + 语句超时。

    为什么要独立连接而不是用 ``get_conn``：
    流式响应（StreamingResponse）的生成器生命周期长于请求函数，
    连接必须活到生成器结束；同时命名游标必须在显式事务里才能跨 FETCH 存活，
    而连接池里 readonly 连接是 autocommit 的，所以这里单独控制事务。
    """
    conn = pools.acquire(db_key)
    cur = None
    try:
        conn.autocommit = False
        conn.set_session(readonly=True)
        timeout_ms = limits()["statement_timeout_ms"]
        if timeout_ms > 0:
            with conn.cursor() as setup:
                setup.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
                # 客户端异常断连时，事务不会无限期挂着
                setup.execute(
                    "SET LOCAL idle_in_transaction_session_timeout = %s",
                    (int(timeout_ms) + 60_000,),
                )
        cur = conn.cursor(name=f"crdb_exp_{os.getpid()}_{next(_cursor_seq)}",
                          cursor_factory=cursor_factory)
        cur.itersize = fetch_size
        cur.execute(stmt, params or ())
        yield cur
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
        pools.release(db_key, conn)


def query_csv_chunks(
    db_key: str,
    stmt,
    params: Optional[Sequence],
    columns: Sequence[str],
    stats: Dict,
    max_rows: Optional[int] = None,
    fetch_size: int = FETCH_SIZE,
) -> Iterable[str]:
    """生成器：把 SQL 查询结果按块产出 CSV 文本（首块含 UTF-8 BOM + 表头）。

    ``stats`` 由调用方传入并在结束时被写入 ``rows`` / ``truncated``，
    供日志与响应头使用（生成器内无法 return 结果）。
    """
    cap = limits()["max_rows"] if max_rows is None else max_rows
    buf = _ChunkBuffer()
    writer = buf.writer

    yield BOM
    writer.writerow([safe_cell(c) for c in columns])
    yield buf.flush()

    rows = 0
    truncated = False
    with export_cursor(db_key, stmt, params, fetch_size) as cur:
        while True:
            batch = cur.fetchmany(fetch_size)
            if not batch:
                break
            for row in batch:
                writer.writerow([safe_cell(v) for v in row])
                rows += 1
                if cap and rows >= cap:
                    truncated = True
                    break
            if buf.should_flush():
                yield buf.flush()
            if truncated:
                break

    if truncated:
        # 流式响应发出表头时还不知道总行数，无法用响应头告知「已截断」，
        # 因此在文件末尾显式追加一条提示行，避免用户以为拿到了全量数据。
        writer.writerow([
            TRUNCATION_NOTICE.format(cap=cap),
        ])
        logger.warning(
            "导出 %s 达到行数上限 %d，结果已截断（请让用户缩小筛选范围）", db_key, cap,
        )

    tail = buf.flush()
    if tail:
        yield tail
    stats["rows"] = rows
    stats["truncated"] = truncated


def rows_to_csv_text(
    header: Sequence, rows: Iterable[Sequence], bom: bool = True, chunk_bytes: int = CHUNK_BYTES
) -> str:
    """小数据量（已在内存中的统计结果）：一次性拼成 CSV 文本。"""
    buf = _ChunkBuffer(chunk_bytes)
    if bom:
        buf.write_raw(BOM)
    buf.writer.writerow([safe_cell(c) for c in header])
    for row in rows:
        buf.writer.writerow([safe_cell(v) for v in row])
    return buf.flush()


def tables_to_csv_text(tables: List[dict], calc_yoy_mom: bool = False) -> str:
    """把多张透视表拼成**一个 CSV**（原来是一个 xlsx 多 sheet）。

    多 sheet 的信息用首列「分组」保留：每行带上所属子表标题，
    这样导出的 CSV 既能直接看，也能被 pandas/Excel 直接透视分析。
    各子表的列取并集（省份展开等场景列集合可能不同），缺失填 0。
    """
    col_keys: List[str] = []
    seen = set()
    for t in tables:
        for k in t.get("col_keys", []):
            if k not in seen:
                seen.add(k)
                col_keys.append(k)

    extra = ["合计", "同期合计", "同比(%)", "上月合计", "环比(%)"] if calc_yoy_mom else []
    row_header = (tables[0].get("row_header", "") if tables else "") or "分组"
    header = ["分组", row_header] + col_keys + extra

    def _iter():
        for t in tables:
            title = t.get("title") or ""
            for row in t.get("rows", []):
                cells = row.get("cells", {}) or {}
                line: List = [title, row.get("row_key", "")]
                for k in col_keys:
                    v = cells.get(k, 0)
                    line.append(0 if v is None else v)
                if calc_yoy_mom:
                    line.append(row.get("total", 0))
                    line.append(row.get("yoy_total", 0))
                    yoy = row.get("yoy_pct")
                    line.append(yoy if yoy is not None else "/")
                    line.append(row.get("mom_total", 0))
                    mom = row.get("mom_pct")
                    line.append(mom if mom is not None else "/")
                yield line

    if not tables:
        return rows_to_csv_text(["无数据"], [])

    return rows_to_csv_text(header, _iter())


def content_disposition(filename: str) -> Dict[str, str]:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


def csv_response(filename: str, text: str, extra_headers: Optional[Dict[str, str]] = None) -> Response:
    """非流式 CSV 响应（统计类导出，结果已在内存中）。"""
    headers = content_disposition(filename)
    headers["Cache-Control"] = "no-store"
    if extra_headers:
        headers.update(extra_headers)
    return Response(content=text.encode("utf-8"), media_type=CSV_MEDIA_TYPE, headers=headers)


def csv_stream_response(
    filename: str,
    chunks: Iterable[str],
    stats: Optional[Dict] = None,
    slot: Optional[ExportSlot] = None,
    log_ctx: str = "",
) -> StreamingResponse:
    """流式 CSV 响应：在生成器 finally 里归还导出名额并记录实际行数。

    名额必须在生成器结束（而不是路由函数返回）时归还：
    路由返回时数据其实还没写完，提前归还会让并发上限形同虚设。
    """
    ctx = f"（{log_ctx}）" if log_ctx else ""

    def _wrapped():
        try:
            for chunk in chunks:
                yield chunk
        except GeneratorExit:
            # 用户取消下载 / 网络断开：Starlette 关闭生成器时会走到这里。
            # 名额一定要归还，否则几次中断就能把并发阀占死。
            logger.warning("导出%s 被客户端中断，已停止并释放名额", ctx)
            raise
        except Exception:
            logger.exception("导出%s 中途失败（已下载部分数据不完整）", ctx)
            raise
        finally:
            if slot is not None:
                slot.release()
            if stats is not None:
                logger.info(
                    "导出完成%s：%d 行%s",
                    ctx,
                    stats.get("rows", 0),
                    "，已截断" if stats.get("truncated") else "",
                )

    headers = content_disposition(filename)
    headers["Cache-Control"] = "no-store"
    # 该请求最多会导出多少行（前端可据此提示用户）；实际行数在流结束后才知道，
    # 因此不放在响应头里，而是触顶时在文件末尾追加提示行（见 query_csv_chunks）。
    max_rows = limits().get("max_rows") or 0
    if max_rows:
        headers["X-Export-Max-Rows"] = str(max_rows)
    return StreamingResponse(_wrapped(), media_type=CSV_MEDIA_TYPE, headers=headers)


__all__ = [
    "BOM",
    "CSV_MEDIA_TYPE",
    "CHUNK_BYTES",
    "FETCH_SIZE",
    "safe_cell",
    "export_cursor",
    "query_csv_chunks",
    "rows_to_csv_text",
    "tables_to_csv_text",
    "csv_response",
    "csv_stream_response",
    "content_disposition",
]
