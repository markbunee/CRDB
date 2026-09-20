# -*- coding: utf-8 -*-
"""FastAPI 应用入口：创建 app、注册中间件与路由、托管前端。

启动方式（在 backend/ 目录下）：
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    或
    python main.py   # 入口脚本会调用 uvicorn

中间件执行顺序（由外到内）：CORSMiddleware → 内存护栏 → 鉴权 → 导出开关 → 请求日志。
CORS 必须放在最外层（即最后 add_middleware），否则浏览器发出的 OPTIONS 预检请求
会先被内层中间件以 401 拦截，导致跨域请求整体失败。
"""
import os
import time

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import decode_token, ensure_auth_tables, get_user_status
from .config import FRONTEND_DIR
from .dependencies import require_admin
from .logger import get_logger, setup_logging
from .memory_guard import (
    is_enabled as MEM_GUARD_ENABLED,
    is_memory_pressure,
    guard_status,
)
from .services.export_gate import status as export_gate_status
from .routers import (meta, rows, export, stats, frontend_log, import_excel,
                      csv_export, stats_store, stats_box, stats_store_ability,
                      product_map, dashboard, filter_options, inventory, auth)

# 启动即初始化日志（写入 backend/log.txt）
setup_logging()
logger = get_logger("app.main")

app = FastAPI(
    title="医药销售数据库管理",
    version="2.0",
    description="大参林 / 高济 / 海王 三库数据查询与维护（千万级数据优化版）",
)


@app.on_event("startup")
def on_startup():
    # 幂等：建用户表/申请表，并确保存在默认管理员
    try:
        ensure_auth_tables()
    except Exception as e:
        logger.exception("初始化认证表失败: %s", e)
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


# ---------- 导出开关（部署用，默认关闭此限制）----------
DISABLE_EXPORT = os.environ.get("DISABLE_EXPORT", "false").lower() == "true"


@app.middleware("http")
async def block_export(request: Request, call_next):
    """DISABLE_EXPORT=true 时，所有含 /export 的下载接口直接返回 403。

    前端按钮会先被 VITE_DISABLE_DOWNLOAD 拦掉（点击无反应），这里是纵深防御，
    防止有人直接拼 URL 下载数据。
    """
    if DISABLE_EXPORT and "/export" in request.url.path:
        return JSONResponse({"detail": "当前环境已关闭导出/下载功能"}, status_code=403)
    return await call_next(request)


# ---------- 鉴权中间件（登录后才可访问业务接口）----------
# 免登录路径：登录、新成员申请、存活探针。
AUTH_EXEMPT_PATHS = ("/api/auth/login", "/api/auth/apply", "/api/health")


def _extract_token(request: Request) -> str:
    """从 Authorization: Bearer 头取令牌。

    刻意不支持 URL 查询参数传令牌：避免令牌出现在访问日志、浏览器历史与 Referer 中。
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _unauth(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """校验 JWT：未登录 401、账号被禁用 403。

    校验通过后把用户信息写入 request.state.user，供 get_current_user /
    require_admin 依赖使用（导入/导出/删除等敏感操作再叠加管理员校验）。
    """
    path = request.url.path
    # OPTIONS 是 CORS 预检，不带令牌，直接放行交由外层 CORS 中间件处理
    if request.method == "OPTIONS" or not path.startswith("/api/") or path in AUTH_EXEMPT_PATHS:
        return await call_next(request)

    token = _extract_token(request)
    if not token:
        return _unauth("未登录或登录已失效")
    payload = decode_token(token)
    if not payload:
        return _unauth("登录已失效，请重新登录")

    try:
        uid = int(payload.get("sub"))
    except (TypeError, ValueError):
        return _unauth("登录已失效，请重新登录")

    # 账号被管理员禁用后，最多 60 秒内令牌即失效（无需等自然过期）
    status = get_user_status(uid, fallback="active")
    if status != "active":
        return JSONResponse({"detail": "账号已被禁用，请联系管理员"}, status_code=403)

    request.state.user = {
        "id": uid,
        "username": payload.get("username", ""),
        "role": payload.get("role", "member"),
        "name": payload.get("name", ""),
    }
    return await call_next(request)


# ---------- 内存安全护栏（始终开启的熔断器）----------
# 不受护栏限制的路径：存活探针 / 护栏状态自检 / 文档 / 静态资源，避免把监控也挡掉。
_MEMORY_GUARD_EXEMPT = ("/api/health", "/api/memory-guard", "/docs", "/openapi.json", "/")


@app.middleware("http")
async def memory_guard_middleware(request: Request, call_next):
    """内存安全护栏：请求进入时检测系统可用内存，过低则直接 503 拒绝。

    这是一道“始终开启”的熔断器——无论 2 核 2G 还是更大服务器都存在，
    仅在可用内存低于自适应阈值时才触发，平时零开销（带 0.5s 采样缓存）。
    阈值 = max(绝对下限, 总内存 * 相对下限)，随机器大小自适应。
    """
    path = request.url.path
    if not MEM_GUARD_ENABLED or path in _MEMORY_GUARD_EXEMPT or path.startswith("/assets"):
        return await call_next(request)
    low, avail, th = is_memory_pressure()
    if low:
        logger.warning(
            "内存护栏拒绝 %s %s：可用 %.0fMB < 阈值 %.0fMB",
            request.method, path, avail, th,
        )
        return JSONResponse(
            {
                "detail": "系统内存不足，已拒绝本次请求（内存安全护栏）",
                "available_mb": round(avail, 1),
                "threshold_mb": round(th, 1),
            },
            status_code=503,
        )
    return await call_next(request)


# ---------- 注册路由 ----------
app.include_router(auth.router)
app.include_router(meta.router)
app.include_router(rows.router)
app.include_router(export.router)
app.include_router(stats.router)
app.include_router(frontend_log.router)
app.include_router(import_excel.router)
app.include_router(csv_export.router)
app.include_router(stats_store.router)
app.include_router(stats_box.router)
app.include_router(stats_store_ability.router)
app.include_router(product_map.router)
app.include_router(dashboard.router)
app.include_router(filter_options.router)
app.include_router(inventory.router)


# ---------- 健康检查 ----------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/memory-guard")
def memory_guard_status(_admin: dict = Depends(require_admin)):
    """查看内存护栏实时状态（总内存 / 可用内存 / 阈值 / 是否触顶）。仅管理员。"""
    return guard_status()


@app.get("/api/export-gate")
def export_gate(request: Request, _admin: dict = Depends(require_admin)):
    """查看导出闸门实时状态（并发上限 / 正在跑的导出 / 累计拒绝次数）。仅管理员。

    导出把服务器挤崩时，第一件事就是看这里：
    若 active 长期等于 max_concurrent，说明并发阀在起作用（用户会收到 429），
    这时应该先让用户缩小筛选范围，而不是去调大 EXPORT_MAX_CONCURRENT。
    """
    return {**export_gate_status(), "memory": guard_status(), "path": str(request.url.path)}


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

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """SPA 路由回退：/login、/admin、/dashenlin 等前端路由直接刷新时返回 index.html。

        没有它，用户刷新 /login 或 /admin 会得到 404（后端只注册了 "/"）。
        注意：必须注册在所有 /api 路由之后，避免吞掉接口请求。
        """
        with open(os.path.join(_FRONTEND_DIST, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())


# ---------- CORS（必须最后注册，才能成为最外层中间件）----------
# 生产环境请用 CORS_ORIGINS 指定前端域名，切勿长期保持 "*"。
_CORS_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()
] or ["*"]
if "*" in _CORS_ORIGINS:
    logger.warning("CORS_ORIGINS 未限定来源（*），公网部署建议设置为前端域名")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    # 导出相关响应头需要显式暴露，浏览器端 JS 才能读到（用于提示「本次最多导出 N 行」）
    expose_headers=["X-Export-Max-Rows", "X-Export-Truncated", "X-Export-Rows", "Retry-After"],
)
