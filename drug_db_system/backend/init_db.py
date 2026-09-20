# -*- coding: utf-8 -*-
"""幂等初始化数据库：建库 → 建表 →（仅表为空时）导入 Excel → 建索引 → ANALYZE。

与 setup_db.py 的区别：setup_db.py 会 DROP 表重建（丢数据），本脚本不会，
因此可以在每次容器启动时安全执行（由 docker-entrypoint.sh 的 INIT_DB 控制）。

环境变量：
  IMPORT_EXCEL=true|false   是否导入 Excel（默认 true；仅当表为空时才真正导入）
  EXCEL_DIR                 Excel 源数据目录（容器里为 /data/excel）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from psycopg2 import sql  # noqa: E402

from app.config import DB_NAMES, EXCEL_DIR  # noqa: E402
from app.database import admin_conn, direct_conn  # noqa: E402
from app.models.schema_def import SCHEMAS  # noqa: E402
from app.services.data_import import (  # noqa: E402
    analyze_table,
    create_database,
    create_indexes,
    create_table,
    import_excel_copy,
)


def _table_has_rows(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(
                sql.Identifier(table)
            )
        )
        return bool(cur.fetchone()[0])


def main() -> None:
    import_data = os.environ.get("IMPORT_EXCEL", "true").lower() == "true"

    print("=" * 60)
    print("初始化数据库")
    print(f"  Excel 目录: {EXCEL_DIR}（导入={'开' if import_data else '关'}）")
    print("=" * 60)

    with admin_conn() as admin:
        for key, cfg in SCHEMAS.items():
            db_name = DB_NAMES[key]
            print(f"\n--- {cfg['label']}（库: {db_name}）---")
            create_database(admin, db_name)

            with direct_conn(db_name) as conn:
                create_table(conn, key)

                if import_data and not _table_has_rows(conn, cfg["table"]):
                    import_excel_copy(conn, key, replace=False)
                else:
                    print("  [skip] 表已有数据（或已关闭导入），跳过 Excel 导入")

                create_indexes(conn, key)
                analyze_table(conn, key)

    print("\n[完成] 数据库初始化结束")


if __name__ == "__main__":
    main()
