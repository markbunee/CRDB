# -*- coding: utf-8 -*-
"""数据库连接管理：使用连接池替代每次新建连接。

每个业务数据库（dashenlin / gaoji / haiwang）维护一个独立的连接池，
进程级共享，请求结束后归还连接而非关闭。
"""
import contextlib
import threading
from typing import Dict

import psycopg2
from psycopg2 import pool

from .config import (
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD,
    POOL_MIN, POOL_MAX, DB_NAMES, conn_params,
)


class DatabasePools:
    """三个业务库的连接池管理器（线程安全懒加载）。"""

    def __init__(self):
        self._pools: Dict[str, pool.ThreadedConnectionPool] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, db_key: str) -> pool.ThreadedConnectionPool:
        if db_key not in DB_NAMES:
            raise ValueError(f"未知数据库: {db_key}")
        if db_key not in self._pools:
            with self._lock:
                # 双检锁
                if db_key not in self._pools:
                    params = conn_params(DB_NAMES[db_key])
                    self._pools[db_key] = pool.ThreadedConnectionPool(
                        minconn=POOL_MIN,
                        maxconn=POOL_MAX,
                        **params,
                    )
        return self._pools[db_key]

    @contextlib.contextmanager
    def get_conn(self, db_key: str, readonly: bool = True):
        """获取一个连接，readonly=True 时设为只读以提速。

        用法:
            with pools.get_conn("dashenlin") as conn:
                with conn.cursor() as cur:
                    cur.execute(...)
        """
        p = self._get_or_create(db_key)
        conn = p.getconn()
        try:
            conn.autocommit = readonly
            if readonly:
                conn.set_session(readonly=True)
            yield conn
            # 非只读连接：上下文正常退出时自动提交
            if not readonly and not conn.closed:
                conn.commit()
        except Exception:
            # 出现异常时回滚非只读事务
            if not readonly:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            # 归还前恢复默认会话设置
            try:
                conn.set_session(readonly=False)
            except Exception:
                pass
            p.putconn(conn)

    def closeall(self):
        """关闭所有连接池（进程退出时调用）。"""
        with self._lock:
            for key, p in self._pools.items():
                p.closeall()
            self._pools.clear()


# 进程级单例
pools = DatabasePools()


@contextlib.contextmanager
def get_conn(db_key: str, readonly: bool = True):
    """便捷函数：等价于 pools.get_conn(db_key, readonly)。"""
    with pools.get_conn(db_key, readonly=readonly) as conn:
        yield conn


@contextlib.contextmanager
def admin_conn(autocommit: bool = True):
    """连接到默认 postgres 库（用于建库等管理操作）。"""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER,
        password=PG_PASSWORD, dbname="postgres",
    )
    conn.autocommit = autocommit
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def direct_conn(db_name: str):
    """直接连接指定数据库（不走连接池，用于 setup_db 等一次性场景）。"""
    conn = psycopg2.connect(**conn_params(db_name))
    try:
        yield conn
    finally:
        conn.close()
