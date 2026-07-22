# -*- coding: utf-8 -*-
"""前端日志收集接口：/api/log/frontend

前端把浏览器端日志 POST 到这里，后端写入 frontend/log.txt。
"""
import os
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import FRONTEND_DIR
from ..logger import get_logger

router = APIRouter(prefix="/api")
logger = get_logger("app.routers.frontend_log")

LOG_FILE = os.path.join(FRONTEND_DIR, "log.txt")
_write_lock = threading.Lock()


class FrontendLogEntry(BaseModel):
    level: str = "info"
    message: str
    context: Optional[str] = None


@router.post("/log/frontend")
def receive_frontend_log(entry: FrontendLogEntry):
    """接收前端日志并追加写入 frontend/log.txt。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level = entry.level.upper().ljust(7)
    ctx = f" | {entry.context}" if entry.context else ""
    line = f"{ts} | {level} | frontend{ctx} | {entry.message}\n"

    # 确保 frontend 目录存在
    os.makedirs(FRONTEND_DIR, exist_ok=True)

    try:
        with _write_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        logger.error("写入前端日志失败: %s", e)
        return {"status": "error", "detail": str(e)}

    return {"status": "ok"}


@router.get("/log/frontend")
def get_frontend_log(limit: int = 200):
    """读取最近的 frontend/log.txt（默认最后 200 行）。"""
    if not os.path.exists(LOG_FILE):
        return {"lines": []}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return {"lines": all_lines[-limit:]}
    except Exception as e:
        logger.error("读取前端日志失败: %s", e)
        return {"lines": []}
