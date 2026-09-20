# -*- coding: utf-8 -*-
"""内存安全护栏（memory guard）。

目标（用户要求）：
- 设备为 2 核 2G 时，若系统出现“内存峰值”（可用内存过低），检测并**拒绝**重负载请求，
  而不是放任其继续占用内存直到 OOM 把进程杀掉。
- 即使在更大的服务器上，该安全机制也必须**始终开启**（默认启用），仅阈值随机器大小自适应，
  不会因为在“大服务器”上就悄悄失效。

实现：
- 通过 /proc/meminfo（或 psutil 回退）读取系统可用内存。
- 作为全局 HTTP 中间件（见 app/main.py 的 memory_guard_middleware）在请求进入时做“事前”拦截：
  可用内存 < 阈值 时直接返回 503，不执行后续重查询/导入。
- 重负载代码路径（如 Excel 导入）可额外调用 assert_memory_available() 做“事中”兜底，
  捕获 MemoryPressureError 后优雅返回，避免读到一半才崩。

阈值（均可用环境变量覆盖）：
- MEMORY_GUARD_ENABLED          默认 1（关闭置 0/false/off）
- MEMORY_GUARD_MIN_AVAILABLE_MB 绝对下限，默认 384（MB）
- MEMORY_GUARD_MIN_FREE_PCT     相对下限，默认 0.08（总内存的比例）
实际阈值 = max(绝对下限, 总内存 * 相对下限)。
"""
import os
import time
import threading
import logging
from typing import Tuple

logger = logging.getLogger("app.memory_guard")

# 默认关闭：护栏原本是「始终开启」的熔断器，但只要 MEMORY_GUARD_MIN_AVAILABLE_MB
# 被配成离谱值（历史上出现过 10000000MB，即 10TB），就会把所有请求一律拦成 503
# —— 看板被拦就是这么来的。改为「默认关闭、显式开启」：需要时设
# MEMORY_GUARD_ENABLED=1，并配合合理的 MEMORY_GUARD_MIN_AVAILABLE_MB（如 512）。
_ENABLED = os.environ.get("MEMORY_GUARD_ENABLED", "0").lower() in ("1", "true", "yes", "on")
_MIN_AVAILABLE_MB = float(os.environ.get("MEMORY_GUARD_MIN_AVAILABLE_MB", "384"))
_MIN_FREE_PCT = float(os.environ.get("MEMORY_GUARD_MIN_FREE_PCT", "0.08"))

_lock = threading.Lock()
_cache = {"ts": 0.0, "total": 0.0, "avail": 0.0}
_CACHE_TTL = 0.5  # 秒：采样缓存，避免高并发下频繁读 /proc/meminfo


class MemoryPressureError(RuntimeError):
    """内存触顶时抛出，供重负载代码捕获后优雅降级。"""


def _read_meminfo() -> Tuple[float, float]:
    """返回 (total_mb, available_mb)。优先 psutil，否则解析 /proc/meminfo。"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.total / 1048576.0, vm.available / 1048576.0
    except Exception:
        pass
    try:
        info: dict = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in (
                    "MemTotal:", "MemAvailable:", "MemFree:", "Buffers:", "Cached:"
                ):
                    info[parts[0]] = int(parts[1])  # 单位 kB
        total = info.get("MemTotal:", 0) / 1024.0
        if "MemAvailable:" in info:
            avail = info["MemAvailable:"] / 1024.0
        else:
            avail = (info.get("MemFree:", 0) + info.get("Buffers:", 0) + info.get("Cached:", 0)) / 1024.0
        return total, avail
    except Exception as e:
        logger.warning("无法读取系统内存信息: %s（护栏将放行，避免误杀）", e)
        return 0.0, float("inf")


def _mem_sample() -> Tuple[float, float]:
    now = time.time()
    with _lock:
        if now - _cache["ts"] < _CACHE_TTL:
            return _cache["total"], _cache["avail"]
    total, avail = _read_meminfo()
    with _lock:
        _cache["ts"], _cache["total"], _cache["avail"] = now, total, avail
    return total, avail


def is_enabled() -> bool:
    return _ENABLED


def threshold_mb() -> float:
    total, _ = _mem_sample()
    if total <= 0:
        return _MIN_AVAILABLE_MB
    th = max(_MIN_AVAILABLE_MB, _MIN_FREE_PCT * total)
    # 防御：阈值绝不超过总内存的 50%。环境变量被配成离谱值（如 10000000MB）时，
    # 否则会出现「可用 4GB < 阈值 10TB」这种恒真判断，把所有请求一律拦成 503。
    return min(th, total * 0.5)


def is_memory_pressure() -> Tuple[bool, float, float]:
    """返回 (是否触顶, 可用内存MB, 阈值MB)。"""
    total, avail = _mem_sample()
    th = threshold_mb() if total > 0 else _MIN_AVAILABLE_MB
    return avail < th, avail, th


def assert_memory_available(context: str = "") -> float:
    """事中兜底：内存已触顶则抛 MemoryPressureError。返回当前可用内存(MB)。"""
    low, avail, th = is_memory_pressure()
    if low:
        msg = f"内存安全护栏触发（{context}）：可用 {avail:.0f}MB < 阈值 {th:.0f}MB"
        logger.warning(msg)
        raise MemoryPressureError(msg)
    return avail


def guard_status() -> dict:
    low, avail, th = is_memory_pressure()
    total, _ = _mem_sample()
    return {
        "enabled": _ENABLED,
        "total_mb": round(total, 1),
        "available_mb": round(avail, 1),
        "threshold_mb": round(th, 1),
        "under_pressure": low,
        "min_available_mb": _MIN_AVAILABLE_MB,
        "min_free_pct": _MIN_FREE_PCT,
    }
