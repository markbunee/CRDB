# -*- coding: utf-8 -*-
"""异步导出任务中心（服务端后台慢慢跑，前端弹提示到下载中心）。

为什么需要这层
--------------
原导出是「请求即生成、同步流式返回」：一个大导出会长时间占着 HTTP 连接与数据库连接，
用户一点击就得干等，且连接断开就前功尽弃。这里把导出拆成「提交 → 后台生成 → 落盘 →
前端轮询领取」，带来三个好处：
1) 点完立即返回，页面不卡；关掉页面/刷新后任务仍在（落盘文件 + 内存任务表）。
2) 服务端用现有「导出闸门」(export_gate) 当限流器：后台 worker 抢不到名额就退回排队重试，
   并发仍被 EXPORT_MAX_CONCURRENT 死死卡住，**不会挤崩**；同用户最小间隔也照常生效。
3) 统一管理「下载中心」，文件 1 小时后自动清理，不会无限堆积。

任务表为进程内存（单 worker 部署足够）；导出文件落在 backend/exports/ 目录（已 gitignore）。
"""
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..logger import get_logger
from ..models.schema_def import get_cfg, get_filter_map
from ..routers import stats_box, stats_store, stats_store_ability
from ..routers.csv_export import _build_export_query
from ..services.csv_export import query_csv_chunks, tables_to_csv_text
from ..services.export_gate import ExportRejected, acquire

logger = get_logger("app.services.export_jobs")


# ---------- 常量 ----------
# 导出文件存放目录（backend/exports/）
EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# 文件 / 任务记录存活时长：超过即清理（用户 1 小时内可随时回下载中心取）
TTL_SEC = 3600
# 同一用户最多同时存在多少个「排队中 + 生成中」任务，挡住狂点 / 脚本刷
MAX_ACTIVE_PER_USER = 10
# 支持的导出类型
EXPORT_TYPES = ("rows", "store_count", "box_count", "store_ability")


@dataclass
class ExportJob:
    job_id: str
    user_id: int
    db_key: str
    type: str
    name: str
    params: dict
    status: str = "queued"          # queued | running | done | failed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    filename: Optional[str] = None   # 完成后才有（下载时的 Content-Disposition 文件名）
    size: int = 0
    rows: int = 0
    error: Optional[str] = None
    file_path: Optional[str] = None


# ---------- 进程内任务注册表（线程安全）----------
JOBS: Dict[str, ExportJob] = {}
_LOCK = threading.Lock()
_STARTED = False


class ExportJobRejected(Exception):
    """提交被拒（如当前用户任务过多），路由层转 429。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _default_name(type_: str) -> str:
    return {
        "rows": "查询结果导出",
        "store_count": "门店数统计",
        "box_count": "实销盒数/金额统计",
        "store_ability": "门店能力分析",
    }.get(type_, "导出任务")


def submit_job(db_key: str, user_id: int, type_: str, params: dict, name: Optional[str] = None) -> ExportJob:
    """登记一个导出任务，返回任务对象（状态 queued）。不在此处抢闸门名额。"""
    if type_ not in EXPORT_TYPES:
        raise ExportJobRejected(f"不支持的导出类型: {type_}")
    with _LOCK:
        active = sum(
            1 for j in JOBS.values()
            if j.user_id == user_id and j.status in ("queued", "running")
        )
        if active >= MAX_ACTIVE_PER_USER:
            raise ExportJobRejected(
                f"你已有 {active} 个导出任务在排队或生成中，请稍后再提交（避免挤占服务器）"
            )
        job = ExportJob(
            job_id=uuid.uuid4().hex,
            user_id=user_id,
            db_key=db_key,
            type=type_,
            name=name or _default_name(type_),
            params=params or {},
        )
        JOBS[job.job_id] = job
    logger.info("异步导出任务已登记 job=%s user=%s db=%s type=%s", job.job_id, user_id, db_key, type_)
    return job


def get_user_jobs(user_id: int) -> List[dict]:
    with _LOCK:
        items = [j for j in JOBS.values() if j.user_id == user_id]
    items.sort(key=lambda x: x.created_at, reverse=True)
    return [_public(j) for j in items]


def get_job(job_id: str) -> Optional[ExportJob]:
    with _LOCK:
        return JOBS.get(job_id)


def _public(j: ExportJob) -> dict:
    return {
        "job_id": j.job_id,
        "db_key": j.db_key,
        "type": j.type,
        "name": j.name,
        "status": j.status,
        "filename": j.filename,
        "size": j.size,
        "rows": j.rows,
        "error": j.error,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


def retry_job(job_id: str, user: dict) -> ExportJob:
    """把失败任务重新置为排队（仅任务属主或管理员可操作）。"""
    with _LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise ExportJobRejected("任务不存在或已过期")
        if not _owns(j, user):
            raise ExportJobRejected("无权操作该任务")
        if j.status not in ("failed",):
            raise ExportJobRejected("仅失败任务可重试")
        j.status = "queued"
        j.error = None
        j.finished_at = None
        j.started_at = None
    return j


def delete_job(job_id: str, user: dict) -> None:
    """删除任务并清掉落盘文件（仅属主或管理员）。"""
    with _LOCK:
        j = JOBS.pop(job_id, None)
    if j is None:
        return
    if not _owns(j, user):
        # 非属主不得删，但记录已弹出：为安全起见重新放回（理论上不会发生，因路由已校验）
        with _LOCK:
            JOBS[job_id] = j
        raise ExportJobRejected("无权删除该任务")
    _remove_file(j)


def _owns(j: ExportJob, user: dict) -> bool:
    return j.user_id == user.get("id") or user.get("role") == "admin"


def _remove_file(j: ExportJob) -> None:
    """删除任务落盘文件并清空路径；删除失败不影响主流程（只记日志）。"""
    path = j.file_path
    j.file_path = None
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:  # noqa: BLE001
        logger.warning("删除导出文件失败: %s", path)


# ---------- 生成器：复用既有导出核心，写到文件而非流式返回 ----------

def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _gen_rows(job: ExportJob, path: str) -> None:
    """筛选/查询结果导出（对应 /export/csv）。"""
    p = job.params or {}
    db_key = job.db_key
    cfg = get_cfg(db_key)
    fm = get_filter_map(db_key)
    filters: dict = {}
    if p.get("product_codes") and "product_codes" in fm:
        filters[fm["product_codes"]] = p["product_codes"]
    if p.get("cities") and "cities" in fm:
        filters[fm["cities"]] = p["cities"]
    if p.get("provinces") and "provinces" in fm:
        filters[fm["provinces"]] = p["provinces"]

    stmt, params, cols = _build_export_query(
        db_key, p.get("search", ""), p.get("date_from"), p.get("date_to"),
        filters, p.get("sort_by", ""), p.get("sort_dir", "asc"),
    )
    stats: dict = {"rows": 0, "truncated": False}
    with open(path, "w", encoding="utf-8", newline="") as f:
        for chunk in query_csv_chunks(db_key, stmt, params, cols, stats):
            f.write(chunk)
    job.rows = stats["rows"]
    job.filename = f"{cfg['label']}_导出.csv"
    job.size = os.path.getsize(path)


def _gen_store_count(job: ExportJob, path: str) -> None:
    p = job.params or {}
    result = stats_store._run_store_count(
        job.db_key, p.get("dimension", "city"), p.get("region_level", "city"),
        p.get("date_from"), p.get("date_to"), bool(p.get("merge_months", False)),
        p.get("cities"), p.get("provinces"), p.get("products"),
        bool(p.get("merge_cities", False)), bool(p.get("merge_products", False)),
        bool(p.get("merge_province_cities", False)), bool(p.get("map_names", False)),
        p.get("store_type", "all"),
    )
    # additive=False：门店数是去重计数（COUNT DISTINCT 门店），单元格不可加，故不出合计
    _write_text(path, tables_to_csv_text(result["tables"], additive=False))
    job.filename = f"store_count_{p.get('dimension', 'city')}_{p.get('region_level', 'city')}.csv"
    job.size = os.path.getsize(path)


def _gen_box_count(job: ExportJob, path: str) -> None:
    p = job.params or {}
    calc = bool(p.get("calc_yoy_mom", False))
    if calc:
        result = stats_box._compute_yoy_mom(
            job.db_key, p.get("dimension", "city"), p.get("region_level", "city"),
            p.get("date_from"), p.get("date_to"), bool(p.get("merge_months", False)),
            p.get("cities"), p.get("provinces"), p.get("products"),
            bool(p.get("merge_cities", False)), bool(p.get("merge_products", False)),
            bool(p.get("merge_province_cities", False)), bool(p.get("map_names", False)),
            p.get("store_type", "all"), p.get("metric", "boxes"),
        )
    else:
        result = stats_box._run_box_count(
            job.db_key, p.get("dimension", "city"), p.get("region_level", "city"),
            p.get("date_from"), p.get("date_to"), bool(p.get("merge_months", False)),
            p.get("cities"), p.get("provinces"), p.get("products"),
            bool(p.get("merge_cities", False)), bool(p.get("merge_products", False)),
            bool(p.get("merge_province_cities", False)), bool(p.get("map_names", False)),
            p.get("store_type", "all"), p.get("metric", "boxes"),
        )
    _write_text(path, tables_to_csv_text(result["tables"], calc_yoy_mom=calc))
    suffix = "_yoy_mom" if calc else ""
    prefix = "sales_amount" if p.get("metric") == "amount" else "box_count"
    job.filename = f"{prefix}_{p.get('dimension', 'city')}_{p.get('region_level', 'city')}{suffix}.csv"
    job.size = os.path.getsize(path)


def _gen_store_ability(job: ExportJob, path: str) -> None:
    p = job.params or {}
    result = stats_store_ability._run_store_ability(
        job.db_key, p.get("date_from"), p.get("date_to"), p.get("products"),
        int(p.get("top_n", 100) or 100), stores=p.get("stores"),
        map_names=bool(p.get("map_names", False)), store_type=p.get("store_type", "all"),
        cities=p.get("cities"), provinces=p.get("provinces"),
    )
    _write_text(path, stats_store_ability._store_ability_to_csv(result))
    job.filename = "store_ability_guangzhou.csv"
    job.size = os.path.getsize(path)


def _generate(job: ExportJob) -> None:
    path = os.path.join(EXPORT_DIR, job.job_id + ".csv")
    # 先登记路径：生成中途失败时，半成品文件也能被 _execute / _cleanup 清掉，
    # 否则失败的（可能很大的）CSV 会永远留在导出目录里。
    job.file_path = path
    if job.type == "rows":
        _gen_rows(job, path)
    elif job.type == "store_count":
        _gen_store_count(job, path)
    elif job.type == "box_count":
        _gen_box_count(job, path)
    elif job.type == "store_ability":
        _gen_store_ability(job, path)
    else:
        raise ValueError(f"未知导出类型: {job.type}")


# ---------- 后台 worker：supervisor 抢闸门名额并派发，executor 真正生成 ----------

def _execute(job: ExportJob, slot) -> None:
    try:
        _generate(job)
        job.status = "done"
        job.error = None
    except Exception as e:  # noqa: BLE001
        logger.exception("异步导出失败 job=%s type=%s", job.job_id, job.type)
        job.status = "failed"
        job.error = str(e) or "导出失败"
        # 清掉写到一半的半成品文件，避免失败任务在磁盘上留下垃圾
        _remove_file(job)
    finally:
        job.finished_at = time.time()
        try:
            slot.release()
        except Exception:  # noqa: BLE001
            pass


def _supervisor() -> None:
    """单线程调度：扫描排队任务，抢到导出闸门名额就起 executor 线程生成，抢不到就跳过试下一个。

    闸门（export_gate）用 BoundedSemaphore 限制全局并发，因此这里即使连续派发，
    真正同时跑的生成也不会超过 EXPORT_MAX_CONCURRENT；同用户最小间隔也由闸门在 acquire 时校验。
    """
    while True:
        try:
            with _LOCK:
                queued = [j for j in JOBS.values() if j.status == "queued"]
            started_any = False
            for job in queued:
                try:
                    slot = acquire(job.user_id, f"{job.db_key}/async/{job.type}", wait=0)
                except ExportRejected:
                    # 该用户频率受限或全局并发满：跳过，尝试下一个用户的任务
                    continue
                job.status = "running"
                job.started_at = time.time()
                threading.Thread(target=_execute, args=(job, slot), daemon=True).start()
                started_any = True
            if not started_any:
                time.sleep(1)
        except Exception:  # noqa: BLE001
            logger.exception("异步导出 supervisor 异常")
            time.sleep(2)


def _cleanup() -> None:
    """定期清理超过 TTL 的已完成 / 失败任务及其文件（下载中心 1 小时后看不到）。"""
    while True:
        try:
            now = time.time()
            with _LOCK:
                expired = [
                    j.job_id for j in JOBS.values()
                    if j.status in ("done", "failed") and j.finished_at
                    and now - j.finished_at > TTL_SEC
                ]
            for jid in expired:
                with _LOCK:
                    j = JOBS.pop(jid, None)
                if j and j.file_path and os.path.exists(j.file_path):
                    try:
                        os.remove(j.file_path)
                    except Exception:  # noqa: BLE001
                        pass
                if j:
                    logger.info("异步导出任务已过期清理 job=%s", jid)
            _sweep_orphan_files(now)
        except Exception:  # noqa: BLE001
            logger.exception("异步导出清理异常")
        time.sleep(300)


def _sweep_orphan_files(now: float) -> None:
    """兜底清理「不在任务表里」的孤儿文件。

    典型场景：用户删掉一个**正在生成**的任务——任务记录已从 JOBS 弹出，
    但 executor 线程仍会继续把文件写完，这类文件没人引用，只能靠这里回收。
    """
    try:
        for name in os.listdir(EXPORT_DIR):
            fp = os.path.join(EXPORT_DIR, name)
            if not os.path.isfile(fp):
                continue
            try:
                if now - os.path.getmtime(fp) <= TTL_SEC:
                    continue
                os.remove(fp)
                logger.info("清理孤儿导出文件 %s", name)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        logger.warning("扫描导出目录失败: %s", EXPORT_DIR)


def ensure_workers() -> None:
    """启动后台线程（幂等）。在 main.py 的 startup 中调用。"""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_supervisor, daemon=True).start()
    threading.Thread(target=_cleanup, daemon=True).start()
    logger.info("异步导出 worker 已启动（TTL=%ds, 单用户最大活动任务=%d）", TTL_SEC, MAX_ACTIVE_PER_USER)
