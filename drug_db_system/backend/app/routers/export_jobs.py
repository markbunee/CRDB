# -*- coding: utf-8 -*-
"""异步导出任务中心接口。

端点一览（均含 /export，受 DISABLE_EXPORT 开关与 VITE_DISABLE_DOWNLOAD 统一兜底）：
- ``POST /api/{db_key}/export/async``  提交导出任务，立即返回 202 + job_id（不阻塞）
- ``GET  /api/export/jobs``           当前用户的任务列表（下载中心轮询用）
- ``GET  /api/export/jobs/{job_id}``  单个任务详情
- ``GET  /api/export/jobs/{job_id}/download``  下载已生成好的文件（仅属主 / 管理员）
- ``POST /api/export/jobs/{job_id}/retry``     重试失败任务
- ``DELETE /api/export/jobs/{job_id}``         删除任务及文件

服务端用 export_gate 当限流器，后台 worker 抢不到名额就排队重试，绝不会挤崩。
"""
import os
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..dependencies import get_current_user, require_permission, validate_db_key
from ..logger import get_logger
from ..services import export_jobs as jobs

logger = get_logger("app.routers.export_jobs")

router = APIRouter(prefix="/api", tags=["export_jobs"])


class ExportSubmit(BaseModel):
    type: str
    params: Dict = Field(default_factory=dict)
    name: Optional[str] = None


@router.post("/{db_key}/export/async", status_code=202, summary="提交异步导出任务")
def submit_export(
    body: ExportSubmit,
    db_key: str = Depends(validate_db_key),
    user: dict = Depends(require_permission("export")),
):
    """提交一个导出任务，立即返回 job_id；真正的 CSV 生成在后台慢慢跑。

    并发 / 频率由 export_gate 在 worker 端兜底，这里只做「单用户活动任务数」轻量防护。
    """
    try:
        job = jobs.submit_job(db_key, user.get("id"), body.type, body.params, body.name)
    except jobs.ExportJobRejected as e:
        raise HTTPException(429, e.message)
    return {"job_id": job.job_id, "status": job.status, "name": job.name}


@router.get("/export/jobs", summary="当前用户的导出任务列表")
def list_jobs(user: dict = Depends(get_current_user)):
    return {"jobs": jobs.get_user_jobs(user.get("id"))}


@router.get("/export/jobs/{job_id}", summary="单个导出任务详情")
def job_detail(job_id: str, user: dict = Depends(get_current_user)):
    job = jobs.get_job(job_id)
    if job is None or job.user_id != user.get("id") and user.get("role") != "admin":
        raise HTTPException(404, "任务不存在或已过期")
    return jobs._public(job)


@router.get("/export/jobs/{job_id}/download", summary="下载已生成的导出文件")
def download_job(job_id: str, user: dict = Depends(get_current_user)):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    if job.user_id != user.get("id") and user.get("role") != "admin":
        raise HTTPException(403, "无权下载该任务")
    if job.status != "done" or not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(409, "文件尚未生成完成，请稍后再试")
    return FileResponse(
        job.file_path,
        media_type="text/csv; charset=utf-8",
        filename=job.filename or f"{job_id}.csv",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/export/jobs/{job_id}/retry", summary="重试失败的导出任务")
def retry_job(job_id: str, user: dict = Depends(get_current_user)):
    try:
        job = jobs.retry_job(job_id, user)
    except jobs.ExportJobRejected as e:
        # 404 / 403 / 非法状态都归到这个异常，按状态码区分更友好
        if "不存在" in e.message:
            raise HTTPException(404, e.message)
        if "无权" in e.message:
            raise HTTPException(403, e.message)
        raise HTTPException(400, e.message)
    return jobs._public(job)


@router.delete("/export/jobs/{job_id}", summary="删除导出任务及文件")
def delete_job(job_id: str, user: dict = Depends(get_current_user)):
    try:
        jobs.delete_job(job_id, user)
    except jobs.ExportJobRejected as e:
        if "无权" in e.message:
            raise HTTPException(403, e.message)
        raise HTTPException(400, e.message)
    return {"ok": True}
