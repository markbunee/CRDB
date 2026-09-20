#!/bin/sh
# 后端容器入口：等数据库 → 按需初始化（建库/建表/导入 Excel/建索引）→ 启动 uvicorn
set -e

echo "==> 等待数据库 ${PG_HOST:-postgres}:${PG_PORT:-5432} ..."
python - <<'PY'
import os
import sys
import time

import psycopg2

last = None
for i in range(60):
    try:
        conn = psycopg2.connect(
            host=os.environ.get("PG_HOST", "postgres"),
            port=os.environ.get("PG_PORT", "5432"),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", "postgres"),
            dbname="postgres",
            connect_timeout=3,
        )
        conn.close()
        print("==> 数据库已就绪")
        break
    except Exception as e:  # noqa: BLE001
        last = e
        time.sleep(2)
else:
    print("!! 数据库在 120s 内未就绪:", last)
    sys.exit(1)
PY

if [ "${INIT_DB:-true}" = "true" ]; then
    echo "==> 初始化数据库（幂等）..."
    python init_db.py
else
    echo "==> INIT_DB=false，跳过数据库初始化"
fi

echo "==> 启动 API (workers=${API_WORKERS:-1})"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${API_WORKERS:-1}"
