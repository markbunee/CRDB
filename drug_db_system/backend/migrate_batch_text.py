# -*- coding: utf-8 -*-
"""一次性迁移：把批号/生产批号列从 BIGINT 改为 TEXT。"""
import psycopg2
from app.config import conn_params

TARGETS = [
    ("dashenlin", "批号"),
    ("gaoji", "生产批号"),
]

for db_key, col in TARGETS:
    conn = psycopg2.connect(**conn_params(db_key))   # 先连接
    conn.autocommit = True                            # 再设自动提交
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='sales' AND column_name=%s",
            (col,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"[{db_key}] 列 {col} 不存在，跳过")
        elif row[0] == "text":
            print(f"[{db_key}] 列 {col} 已是 TEXT，跳过")
        else:
            cur.execute(f'ALTER TABLE sales ALTER COLUMN "{col}" TYPE TEXT;')
            print(f"[{db_key}] 列 {col} 由 {row[0]} 改为 TEXT，成功")
        cur.close()
    finally:
        conn.close()
print("迁移完成")
