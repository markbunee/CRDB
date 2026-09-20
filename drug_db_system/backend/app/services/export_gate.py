# -*- coding: utf-8 -*-
"""导出闸门（export gate）：把「导出」这类重负载操作限制在服务器扛得住的范围内。

为什么需要这道闸门
------------------
导出是典型的「一个请求占用大量 CPU / IO / 数据库连接数分钟」的操作。2~4GB 小机上，
只要 2~3 个人同时点导出，PostgreSQL 的排序/聚合（work_mem）就会把内存吃满，
API 容器被 OOM 杀掉，**所有人都用不了** —— 这正是要防的「把服务器挤崩」。

四道阀（全部可用环境变量调整）
------------------------------
1) 并发阀 ``EXPORT_MAX_CONCURRENT``（默认 2）
   进程内同时最多 N 个导出在跑；超出先短暂等待（``EXPORT_QUEUE_WAIT_SEC``，默认 3 秒），
   仍然排不上就直接 429 + Retry-After，让用户稍后重试。
   —— 刻意「快速失败」而不是无限排队：排队会占着内存与连接，反而更容易把机器拖死。
2) 频率阀 ``EXPORT_MIN_INTERVAL_SEC``（默认 5）
   同一用户两次导出之间的最小间隔，挡住连点 / 脚本狂刷。
3) 规模阀 ``EXPORT_MAX_ROWS``（默认 3,000,000）
   单次导出最多 N 行，到量即停，并在响应头 ``X-Export-Truncated: 1`` 标记，
   避免「一个导出跑半小时、连接一直挂着」。
4) 超时阀 ``EXPORT_STATEMENT_TIMEOUT_MS``（默认 600000，即 10 分钟）
   在导出专用连接上 ``SET LOCAL statement_timeout``，SQL 跑飞时由 PostgreSQL 主动杀查询，
   连接与快照都不会无限期占用（这是 CSV 流式导出最容易被忽略的坑）。

说明
----
``uvicorn`` 多 worker 时闸门是「每进程」的，实际并发上限 = EXPORT_MAX_CONCURRENT × workers。
小机建议 ``API_WORKERS=1``（见 .env），此时闸门即为全局上限。
"""
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from ..logger import get_logger

logger = get_logger("app.services.export_gate")


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# 并发上限：至少 1，最多 8（再多就等于没有闸门了）
MAX_CONCURRENT = max(1, min(8, _env_int("EXPORT_MAX_CONCURRENT", 2)))
# 同一用户最小导出间隔（秒）
MIN_INTERVAL_SEC = max(0.0, _env_float("EXPORT_MIN_INTERVAL_SEC", 5.0))
# 排不上队时最多等待多久（秒），超时即 429
QUEUE_WAIT_SEC = max(0.0, min(30.0, _env_float("EXPORT_QUEUE_WAIT_SEC", 3.0)))
# 单次导出行数上限（0 表示不限）
MAX_ROWS = max(0, _env_int("EXPORT_MAX_ROWS", 3_000_000))
# 导出连接上的语句超时（毫秒），0 表示不设置
STATEMENT_TIMEOUT_MS = max(0, _env_int("EXPORT_STATEMENT_TIMEOUT_MS", 600_000))


class ExportRejected(Exception):
    """导出被闸门拒绝。路由层捕获后转成 HTTP 429（附 Retry-After）。"""

    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.message = message
        self.retry_after = max(1, int(retry_after))


@dataclass
class ExportSlot:
    """一次导出占用的配额，流式响应结束后必须 release()。"""

    token: str
    kind: str
    user_id: Optional[int]
    started_at: float

    def release(self) -> None:
        _release(self)


_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_lock = threading.Lock()
_seq = 0
_last_start: Dict[str, float] = {}          # user_key -> 上次开始时间
_active: Dict[str, ExportSlot] = {}          # token -> slot
_counters = {"started": 0, "busy": 0, "frequent": 0}


def limits() -> dict:
    """当前生效的闸门参数（供状态接口 / 文档展示）。"""
    return {
        "max_concurrent": MAX_CONCURRENT,
        "min_interval_sec": MIN_INTERVAL_SEC,
        "queue_wait_sec": QUEUE_WAIT_SEC,
        "max_rows": MAX_ROWS,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
    }


def status() -> dict:
    """实时状态：正在跑的导出、剩余并发额度、累计拒绝次数。"""
    with _lock:
        active = [
            {
                "kind": s.kind,
                "user_id": s.user_id,
                "running_sec": round(time.time() - s.started_at, 1),
            }
            for s in _active.values()
        ]
        counters = dict(_counters)
    return {
        "limits": limits(),
        "active": len(active),
        "active_detail": active,
        "counters": counters,
    }


def acquire(user_id: Optional[int], kind: str, wait: Optional[float] = None) -> ExportSlot:
    """申请一个导出名额；被拒时抛 ExportRejected（路由层转 429）。

    顺序：先查频率（便宜、无需争用信号量），再争并发名额。
    ``wait`` 可覆盖默认排队等待时长；async 路由请传 0，避免阻塞事件循环。
    """
    global _seq
    user_key = str(user_id) if user_id is not None else "-"
    now = time.time()
    wait_sec = QUEUE_WAIT_SEC if wait is None else max(0.0, min(30.0, wait))

    if MIN_INTERVAL_SEC > 0:
        with _lock:
            last = _last_start.get(user_key, 0.0)
            wait = MIN_INTERVAL_SEC - (now - last)
            if wait > 0:
                _counters["frequent"] += 1
                raise ExportRejected(
                    f"导出过于频繁，请 {math.ceil(wait)} 秒后再试", retry_after=math.ceil(wait)
                )

    got = _sem.acquire(timeout=wait_sec) if wait_sec > 0 else _sem.acquire(blocking=False)
    if not got:
        with _lock:
            _counters["busy"] += 1
            active = len(_active)
        logger.warning(
            "导出闸门拒绝（并发达上限 %d，当前在跑 %d）：user=%s kind=%s",
            MAX_CONCURRENT, active, user_key, kind,
        )
        raise ExportRejected(
            f"当前有 {MAX_CONCURRENT} 个导出任务正在运行，请稍后再试（避免服务器被挤崩）",
            retry_after=10,
        )

    with _lock:
        _seq += 1
        token = f"{os.getpid()}-{_seq}"
        slot = ExportSlot(token=token, kind=kind, user_id=user_id, started_at=now)
        _active[token] = slot
        _last_start[user_key] = now
        _counters["started"] += 1
        active = len(_active)
    logger.info(
        "导出开始（并发 %d/%d）：user=%s kind=%s",
        active, MAX_CONCURRENT, user_key, kind,
    )
    return slot


def _release(slot: ExportSlot) -> None:
    with _lock:
        if _active.pop(slot.token, None) is None:
            return  # 重复释放（例如客户端断连后生成器二次清理）直接忽略
        active = len(_active)
    try:
        _sem.release()
    except ValueError:  # 信号量被超额释放，说明计数错乱，记日志即可
        logger.warning("导出闸门信号量释放异常：%s", slot.kind)
    logger.info(
        "导出结束（耗时 %.1fs，剩余并发 %d/%d）：user=%s kind=%s",
        time.time() - slot.started_at, active, MAX_CONCURRENT, slot.user_id, slot.kind,
    )


class _SlotCtx:
    """with export_slot(...) 用法；异常时同样保证归还名额。"""

    def __init__(self, user_id: Optional[int], kind: str, wait: Optional[float] = None):
        self._user_id = user_id
        self._kind = kind
        self._wait = wait
        self._slot: Optional[ExportSlot] = None

    def __enter__(self) -> ExportSlot:
        self._slot = acquire(self._user_id, self._kind, wait=self._wait)
        return self._slot

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._slot is not None:
            self._slot.release()
        return False


def export_slot(user_id: Optional[int], kind: str, wait: Optional[float] = None) -> _SlotCtx:
    """非流式重负载用：``with export_slot(uid, "stats"):``，退出即归还名额。

    ``wait=0`` 适用于 async 路由（不阻塞事件循环）。
    """
    return _SlotCtx(user_id, kind, wait=wait)
