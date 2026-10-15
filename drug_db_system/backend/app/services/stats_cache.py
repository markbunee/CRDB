# -*- coding: utf-8 -*-
"""统计结果缓存（TTL + 容量上限）。

为什么加
--------
统计（盒数 / 门店数 / 门店能力）是典型的「读多写少 + 单次可能扫千万级大表」：
- 用户反复点「统计」、来回切维度与合并开关，经常得到相同筛选的重复结果；
- 门店数的 COUNT(DISTINCT) 在大参林（841 万行）上单次要数秒。
命中缓存可把重复请求降到毫秒级，大幅降低 DB 压力（与 export_gate 的限流互为补充）。

一致性
------
纯读缓存，口径零影响：
- 写入数据（导入 / 清空 / 品类映射改价 / 库存导入）时由对应接口调用
  ``invalidate_stats_cache()`` 主动失效；
- TTL 兜底（默认 10 分钟）；
- 容量上限避免「筛选组合过多」把缓存撑爆内存（见 ttl_cache 的说明）。
"""
import os
from typing import Any, Optional

from .ttl_cache import TTLCache

# 容量上限为什么是 40：实测单个结果约 0.2MB（单月）~1.5MB（16 个月全展开），
# 40 条最坏约 60MB，在 2~4GB 小机上是可接受的上限；条目再多就有内存风险。
_CACHE = TTLCache(
    ttl=int(os.environ.get("STATS_CACHE_TTL", "600")),
    max_entries=int(os.environ.get("STATS_CACHE_MAX_ENTRIES", "40")),
    name="stats",
)


def cache_key(name: str, db_key: str, **kw) -> str:
    """按「接口名 + 库 + 全部筛选参数」生成 key（参数排序保证幂等）。"""
    items = [f"{k}={kw.get(k)}" for k in sorted(kw)]
    return "|".join([name, db_key] + items)


def cache_get(key: str) -> Optional[Any]:
    return _CACHE.get(key)


def cache_set(key: str, value: Any) -> None:
    _CACHE.set(key, value)


def invalidate_stats_cache(_db_key: Optional[str] = None) -> None:
    """数据写入后调用，清空统计缓存。

    当前统计结果体量小、组合多，整体清空比按库/按参数精细失效更简单且不会错漏；
    ``_db_key`` 保留以备将来需要按库失效。
    """
    _CACHE.clear()
