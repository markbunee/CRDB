# -*- coding: utf-8 -*-
"""FastAPI 应用入口：创建 app、注册路由、托管前端。

启动方式（在 backend/ 目录下）：
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    或
    python main.py   # 入口脚本会调用 uvicorn
"""
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR
from .logger import get_logger, setup_logging
from .routers import (meta, rows, export, stats, frontend_log, import_excel,
                      excel_export, stats_store, stats_box, stats_store_ability)

# 启动即初始化日志（写入 backend/log.txt）
setup_logging()
logger = get_logger("app.main")

app = FastAPI(
    title="医药销售数据库管理",
    version="2.0",
    description="大参林 / 高济 / 海王 三库数据查询与维护（千万级数据优化版）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    logger.info("后端服务启动完成，日志文件: backend/log.txt")


# ---------- 请求日志中间件 ----------
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    cost = (time.time() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        cost,
    )
    return response


# ---------- 注册路由 ----------
app.include_router(meta.router)
app.include_router(rows.router)
app.include_router(export.router)
app.include_router(stats.router)
app.include_router(frontend_log.router)
app.include_router(import_excel.router)
app.include_router(excel_export.router)
app.include_router(stats_store.router)
app.include_router(stats_box.router)
app.include_router(stats_store_ability.router)


# ---------- 健康检查 ----------
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------- 前端托管（仅生产模式） ----------
_FRONTEND_DIST = os.path.join(FRONTEND_DIR, "dist")
_HAS_BUILD = os.path.isfile(os.path.join(_FRONTEND_DIST, "index.html"))


@app.get("/")
def index():
    """返回前端页面或 API 信息页。"""
    if _HAS_BUILD:
        with open(os.path.join(_FRONTEND_DIST, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse(
        "<h2>医药销售数据库 API v2.0</h2>"
        "<p>API 服务正常运行。开发模式下请通过 Vite 开发服务器访问前端："
        "<a href='http://localhost:5173'>http://localhost:5173</a></p>",
    )


if _HAS_BUILD:
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")), name="assets")
