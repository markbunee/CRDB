# -*- coding: utf-8 -*-
"""带容量上限的进程内 TTL 缓存。

为什么需要容量上限
------------------
看板 / 列表 / 候选值都按「库 + 全部筛选参数」做缓存 key，筛选组合空间很大。
若只有 TTL 而没有容量上限，长时间运行 + 大量不同筛选会把缓存越堆越大，
在小内存服务器（2~4GB）上反而成为新的内存风险 —— 这与「防止把服务器挤爆」的目标相悖。

本实现同时受两道约束：
- 时间：条目超过 ttl 秒即视为过期（读取时惰性清除）；
- 容量：条目数超过 max_entries 时，按「最久未使用」淘汰到上限。

命中/未命中语义与普通 dict 缓存完全一致：淘汰只会带来一次重算，
**不会返回旧数据，也不改变任何统计口径**（准确性不受影响）。

线程安全：数字看板用 ThreadPoolExecutor 并发跑子查询，会并发读写同一缓存，
故所有读写都在锁内完成。锁内只做 dict 操作，不会长时间持锁。
"""
import threading
import time
from typing import Any, Optional


class TTLCache:
    """线程安全、带容量上限的 TTL 缓存。"""

    def __init__(self, ttl: int, max_entries: int, name: str = "cache"):
        # ttl <= 0 表示不过期（仅靠容量与主动失效控制）；默认按秒。
        self.ttl = int(ttl)
        self.max_entries = max(1, int(max_entries))
        self.name = name
        self._data: dict = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key) -> Optional[Any]:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            ts, value = item
            if self.ttl > 0 and (now - ts) >= self.ttl:
                self._data.pop(key, None)
                self._misses += 1
                return None
            # 命中即移动到末尾，配合超容量时从头淘汰，近似 LRU
            self._data.pop(key, None)
            self._data[key] = (ts, value)
            self._hits += 1
            return value

    def set(self, key, value) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._data[key] = (time.time(), value)
            # 超容量：从最久未使用的一端淘汰
            while len(self._data) > self.max_entries:
                self._data.pop(next(iter(self._data)))
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
