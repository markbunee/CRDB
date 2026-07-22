# -*- coding: utf-8 -*-
"""全局配置：数据库连接参数、路径等。

所有参数均可由环境变量覆盖：
  PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_ADMIN_DB
  EXCEL_DIR  （Excel 源数据所在目录，默认为项目上两级）
"""
import os

# Windows/中文系统下让 libpq 用 UTF-8 客户端编码，避免错误消息被 GBK 本地化
os.environ.setdefault("PGCLIENTENCODING", "UTF8")

# ---------- PostgreSQL 连接参数 ----------
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "postgres")
PG_ADMIN_DB = os.environ.get("PG_ADMIN_DB", "postgres")

# ---------- 连接池参数 ----------
POOL_MIN = int(os.environ.get("PG_POOL_MIN", "2"))
POOL_MAX = int(os.environ.get("PG_POOL_MAX", "10"))

# ---------- 数据库 Key -> 实际数据库名 ----------
DB_NAMES = {
    "dashenlin": "dashenlin",
    "gaoji": "gaoji",
    "haiwang": "haiwang",
}

# ---------- 路径 ----------
# backend/app/config.py -> backend/app/ -> backend/ -> drug_db_system/ -> 项目根 CRDB/
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
PROJECT_DIR = os.path.dirname(BACKEND_DIR)                                  # drug_db_system/
ROOT_DIR = os.path.dirname(PROJECT_DIR)                                     # CRDB/（Excel 所在）
FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")

# Excel 源数据目录：默认项目根 CRDB/（与 drug_db_system 同级），可用环境变量覆盖
EXCEL_DIR = os.environ.get("EXCEL_DIR", ROOT_DIR)


def conn_params(db_name: str) -> dict:
    """返回指定数据库的连接参数 dict。"""
    return {
        "host": PG_HOST,
        "port": PG_PORT,
        "user": PG_USER,
        "password": PG_PASSWORD,
        "dbname": db_name,
    }
