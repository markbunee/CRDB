# -*- coding: utf-8 -*-
"""单独建索引脚本：对已有数据补建索引。

用法:
    python create_indexes.py            # 为所有库建索引
    python create_indexes.py dashenlin  # 只为大参林建索引

适用场景：数据已导入但没建索引，查询慢时运行此脚本。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import direct_conn
from app.config import DB_NAMES
from app.models.schema_def import SCHEMAS
from app.services.data_import import create_indexes, analyze_table


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(SCHEMAS.keys())
    for key in targets:
        if key not in SCHEMAS:
            print(f"[warn] 未知数据库: {key}，跳过")
            continue
        cfg = SCHEMAS[key]
        db_name = DB_NAMES[key]
        print(f"\n--- {cfg['label']}（库: {db_name}）---")
        with direct_conn(db_name) as conn:
            create_indexes(conn, key)
            analyze_table(conn, key)
    print("\n[完成] 索引创建完毕")


if __name__ == "__main__":
    main()
