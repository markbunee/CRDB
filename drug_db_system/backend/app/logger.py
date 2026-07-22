# -*- coding: utf-8 -*-
"""统一日志配置：同时输出到文件和控制台。

日志文件位置：backend/log.txt（自动创建）。
"""
import logging
import os
from logging.handlers import RotatingFileHandler

# backend/app/logger.py -> backend/app/ -> backend/
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BACKEND_DIR, "log.txt")

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志配置（幂等，重复调用安全）。"""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 文件输出（滚动，单文件 5MB，保留 3 份）
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 控制台输出
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # 降低第三方库噪声
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger，自动确保已初始化。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
