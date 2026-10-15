# -*- coding: utf-8 -*-
"""查询闸门（query gate）：把「重负载只读查询」限制在服务器扛得住的范围内。

为什么需要这道闸门
------------------
``/rows``（实销查看主表）、``/stats/*``、``/dashboard/*``、``/inventory/*`` 这类
只读接口会对千万级大表做扫描/聚合。2~4GB 小机上，只要几个用户同时拉「全量/宽区间」
查询，PostgreSQL 的排序/聚合（work_mem）就会把内存吃满，API 容器被 OOM 杀掉，
**所有人都用不了** —— 这正是要防的「把服务器挤崩」。

这与 ``export_gate`` 同源（导出也是重负载），但两者职责不同：
- 导出是「长时间占连接 + 流式吐数据」，用 export_gate（含行数上限、流式释放）。
- 普通查询是「短请求、JSON 返回」，用本闸门（并发上限 + 每用户频率 + 语句超时兜底）。

四道阀（全部可用环境变量调整）
------------------------------
1) 并发阀 ``QUERY_MAX_CONCURRENT``（默认 4）
   进程内同时最多 N 个重查询在跑；超出先短暂等待（``QUERY_QUEUE_WAIT_SEC``，默认 1.5 秒），
   仍然排不上就直接 429 + Retry-After，让用户稍后重试。
   —— 刻意「快速失败」而不是无限排队：排队会占着连接与内存，反而更容易把机器拖死。
2) 频率阀 ``QUERY_MIN_INTERVAL_SEC``（默认 0.5）
   同一用户对「同一接口 + 同一筛选条件」两次重查询之间的最小间隔，挡住连点 / 脚本狂刷；
   按「请求路径 + 查询串」隔离，**不同筛选条件的正常查询互不误伤**。
   若同一筛选条件已有请求在跑（前端重复触发造成的并发重复），不按频率拒绝，
   而是让它去排队争并发名额 —— 直接 429 会让用户看到无谓报错。
3) 超时阀 ``QUERY_STATEMENT_TIMEOUT_MS``（默认 30000，即 30 秒）
   仅作为兜底说明：实际语句超时在 ``database.get_conn(readonly=True)`` 上 ``SET``，
   让跑飞的 SQL 由 PostgreSQL 主动杀掉，连接与快照都不会无限期占用。
4) 等待阀 ``QUERY_QUEUE_WAIT_SEC``（默认 1.5）
   排不上队时最多等待多久，超时即 429。

说明
----
``uvicorn`` 多 worker 时闸门是「每进程」的，实际并发上限 = QUERY_MAX_CONCURRENT × workers。
小机建议 ``API_WORKERS=1``（见 .env），此时闸门即为全局上限。
本闸门以 **中间件** 形式覆盖所有 ``GET /api/`` 读接口（导出类接口已自带 export_gate，
且带 ``/export`` 路径，会被本中间件豁免，避免双重闸门）。
"""
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from ..logger import get_logger

logger = get_logger("app.services.query_gate")


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


# 并发上限：至少 1，最多 16（再多就等于没有闸门了）
MAX_CONCURRENT = max(1, min(16, _env_int("QUERY_MAX_CONCURRENT", 4)))
# 同一用户对「同一接口」的最小查询间隔（秒）；按接口隔离，
# 避免一次页面加载里 /dbs、/rows 等多个请求互相误伤。
MIN_INTERVAL_SEC = max(0.0, _env_float("QUERY_MIN_INTERVAL_SEC", 0.5))
# 排不上队时最多等待多久（秒），超时即 429
QUEUE_WAIT_SEC = max(0.0, min(10.0, _env_float("QUERY_QUEUE_WAIT_SEC", 2.0)))
# 只读连接上的语句超时（毫秒），0 表示不设置（由 database.py 读取同一变量）
STATEMENT_TIMEOUT_MS = max(0, _env_int("QUERY_STATEMENT_TIMEOUT_MS", 30000))


class QueryRejected(Exception):
    """查询被闸门拒绝。路由/中间件捕获后转成 HTTP 429（附 Retry-After）。"""

    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.message = message
        self.retry_after = max(1, int(retry_after))


@dataclass
class QuerySlot:
    """一次重查询占用的配额，请求结束后必须 release()。"""

    token: str
    user_id: Optional[int]
    started_at: float
    freq_key: str = ""

    def release(self) -> None:
        _release(self)


_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_lock = threading.Lock()
_seq = 0
_last_start: Dict[str, float] = {}           # freq_key -> 上次开始时间
_active: Dict[str, QuerySlot] = {}           # token -> slot
_active_by_key: Dict[str, int] = {}          # freq_key -> 正在跑的请求数
_counters = {"started": 0, "busy": 0, "frequent": 0}


def limits() -> dict:
    """当前生效的闸门参数（供状态接口 / 文档展示）。"""
    return {
        "max_concurrent": MAX_CONCURRENT,
        "min_interval_sec": MIN_INTERVAL_SEC,
        "queue_wait_sec": QUEUE_WAIT_SEC,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
    }


def status() -> dict:
    """实时状态：正在跑的查询、剩余并发额度、累计拒绝次数。"""
    with _lock:
        active = [
            {
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


def acquire(
    user_id: Optional[int],
    path: Optional[str] = None,
    wait: Optional[float] = None,
    freq_key: Optional[str] = None,
) -> QuerySlot:
    """申请一个重查询名额；被拒时抛 QueryRejected（中间件转 429）。

    顺序：先查频率（便宜、无需争用信号量），再争并发名额。
    频率隔离粒度默认为接口路径；中间件会传入「路径 + 查询串」，
    因此**不同筛选条件的正常查询互不影响**，只有「同一用户、同一筛选」的
    重复提交才会被频率阀拦下。若同一筛选已有请求在跑（并发重复触发），
    则跳过频率校验、让它排队争并发名额，避免用户看到无谓的 429。
    """
    global _seq
    user_key = str(user_id) if user_id is not None else "-"
    scope = freq_key if freq_key is not None else path
    key = f"{user_key}:{scope}" if scope else user_key
    now = time.time()
    wait_sec = QUEUE_WAIT_SEC if wait is None else max(0.0, min(10.0, wait))

    if MIN_INTERVAL_SEC > 0:
        with _lock:
            already_running = _active_by_key.get(key, 0) > 0
            gap = MIN_INTERVAL_SEC - (now - _last_start.get(key, 0.0))
        if gap > 0 and not already_running:
            with _lock:
                _counters["frequent"] += 1
            raise QueryRejected(
                f"查询过于频繁，请 {math.ceil(gap)} 秒后再试", retry_after=math.ceil(gap)
            )

    got = _sem.acquire(timeout=wait_sec) if wait_sec > 0 else _sem.acquire(blocking=False)
    if not got:
        with _lock:
            _counters["busy"] += 1
            active = len(_active)
        logger.warning(
            "查询闸门拒绝（并发达上限 %d，当前在跑 %d）：user=%s path=%s",
            MAX_CONCURRENT, active, user_key, path,
        )
        raise QueryRejected(
            f"当前有 {MAX_CONCURRENT} 个查询任务正在运行，请稍后再试（避免服务器被挤崩）",
            retry_after=10,
        )

    with _lock:
        _seq += 1
        token = f"{os.getpid()}-{_seq}"
        slot = QuerySlot(token=token, user_id=user_id, started_at=now, freq_key=key)
        _active[token] = slot
        _active_by_key[key] = _active_by_key.get(key, 0) + 1
        _last_start[key] = now
        _counters["started"] += 1
        active = len(_active)
    logger.info("查询开始（并发 %d/%d）：user=%s path=%s", active, MAX_CONCURRENT, user_key, path)
    return slot


def _release(slot: QuerySlot) -> None:
    with _lock:
        if _active.pop(slot.token, None) is None:
            return  # 重复释放忽略
        active = len(_active)
        if slot.freq_key:
            left = _active_by_key.get(slot.freq_key, 0) - 1
            if left > 0:
                _active_by_key[slot.freq_key] = left
            else:
                _active_by_key.pop(slot.freq_key, None)
    try:
        _sem.release()
    except ValueError:
        logger.warning("查询闸门信号量释放异常")
    logger.info(
        "查询结束（耗时 %.1fs，剩余并发 %d/%d）：user=%s",
        time.time() - slot.started_at, active, MAX_CONCURRENT, slot.user_id,
    )

