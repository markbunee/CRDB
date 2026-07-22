# -*- coding: utf-8 -*-
"""一键建库 + 建表 + 导入 Excel + 建索引 + 分析统计。

用法（在 backend/ 目录下）:
    python setup_db.py            # 重新建表并导入（会清空旧数据）
    python setup_db.py --no-import # 只建库建表，不导入数据
    python setup_db.py --indexes   # 只补建索引（数据已存在时）

前提：本地 PostgreSQL 服务已启动，PG_PORT 等环境变量已正确设置。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.data_import import import_all, create_indexes, analyze_table
from app.services.data_import import import_excel_copy, create_table, drop_table_if_exists
from app.database import admin_conn, direct_conn
from app.config import DB_NAMES
from app.models.schema_def import SCHEMAS


def main():
    args = set(sys.argv[1:])

    if "--indexes" in args:
        # 只建索引
        print("=" * 60)
        print("仅为现有数据补建索引")
        print("=" * 60)
        for key, cfg in SCHEMAS.items():
            db_name = DB_NAMES[key]
            print(f"\n--- {cfg['label']}（库: {db_name}）---")
            with direct_conn(db_name) as conn:
                create_indexes(conn, key)
                analyze_table(conn, key)
        print("\n[完成] 索引创建完毕")
        return

    if "--no-import" in args:
        # 只建库建表
        print("=" * 60)
        print("仅建库建表（不导入数据）")
        print("=" * 60)
        from app.services.data_import import create_database
        with admin_conn() as admin:
            for key, cfg in SCHEMAS.items():
                db_name = DB_NAMES[key]
                print(f"\n--- {cfg['label']}（库: {db_name}）---")
                create_database(admin, db_name)
                with direct_conn(db_name) as conn:
                    create_table(conn, key)
        print("\n[完成] 建库建表完毕")
        return

    # 默认：完整流程
    import_all(replace=True)


if __name__ == "__main__":
    main()
