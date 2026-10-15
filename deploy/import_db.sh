#!/usr/bin/env bash
# ============================================================
# 服务器执行：把 deploy/pgdump 里的备份导入 crdb-postgres
#
#   bash deploy/import_db.sh                    # 自动取最新一个备份包
#   bash deploy/import_db.sh /path/pgdump_x.tgz # 指定备份包
#   STAMP=20260916_1200 bash deploy/import_db.sh # 用已解压过的目录
#
# 前提：数据库容器已启动
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

DUMP_DIR="$ROOT/deploy/pgdump"
mkdir -p "$DUMP_DIR"
CONTAINER="${CONTAINER:-crdb-postgres}"
PG_USER="${PG_USER:-postgres}"
DBS="${DBS:-dashenlin gaoji haiwang}"
JOBS="${JOBS:-4}"
CLEAN="${CLEAN:-1}"   # 1 = 覆盖式导入（--clean --if-exists），重复执行安全

if [ -n "${STAMP:-}" ]; then
  TARBALL=""
elif [ -n "${1:-}" ]; then
  TARBALL="$1"
else
  TARBALL="$(ls -t "$DUMP_DIR"/pgdump_*.tgz 2>/dev/null | head -1 || true)"
fi

if [ -z "${STAMP:-}" ]; then
  if [ -z "$TARBALL" ] || [ ! -f "$TARBALL" ]; then
    echo "❌ 没找到备份包。"
    echo "   请把本地导出的 pgdump_*.tgz 放到 $DUMP_DIR/ 下，或用："
    echo "   bash deploy/import_db.sh /path/to/pgdump_xxx.tgz"
    exit 1
  fi
  STAMP="$(basename "$TARBALL" .tgz | sed 's/^pgdump_//')"
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "❌ 容器 $CONTAINER 未运行，先执行："
  echo "   cd $ROOT && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres"
  exit 1
fi

if [ -n "$TARBALL" ]; then
  echo "==> 解包 $TARBALL"
  tar xzf "$TARBALL" -C "$DUMP_DIR"
fi

echo "==> 目标容器: $CONTAINER   并发: $JOBS   批次: $STAMP"
echo ""

for db in $DBS; do
  src="$DUMP_DIR/$STAMP/$db"
  if [ ! -d "$src" ]; then
    echo "⚠️  跳过 $db（备份中没有该库）"
    continue
  fi

  echo "---- 恢复 $db ----"

  # 建库（已存在则跳过）
  docker exec "$CONTAINER" psql -U "$PG_USER" -tAc \
      "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || docker exec "$CONTAINER" createdb -U "$PG_USER" "$db"

  docker exec "$CONTAINER" rm -rf "/tmp/$db"
  docker cp "$src" "$CONTAINER:/tmp/$db" >/dev/null

  opts=(-j "$JOBS" --no-owner --no-privileges)
  if [ "$CLEAN" = "1" ]; then
    opts+=(--clean --if-exists)
  fi

  # --clean 对尚不存在的对象会报 ERROR，属正常告警，不中断
  docker exec "$CONTAINER" pg_restore -U "$PG_USER" -d "$db" "${opts[@]}" "/tmp/$db" \
    || echo "⚠️  $db 恢复有告警（多为 --clean 删除不存在的对象，可忽略）"

  docker exec "$CONTAINER" rm -rf "/tmp/$db"
  docker exec "$CONTAINER" psql -U "$PG_USER" -d "$db" -c "ANALYZE;" >/dev/null
  echo "    ✅ $db 完成"
done

echo ""
echo "✅ 数据导入完成"
echo "   下一步：bash deploy/deploy.sh"
