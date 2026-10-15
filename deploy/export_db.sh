#!/usr/bin/env bash
# ============================================================
# 本地执行：从已运行的 Postgres 容器导出三个业务库并打包
#
#   bash deploy/export_db.sh
#   SOURCE_CONTAINER=pg-medicine PG_USER=postgres JOBS=4 bash deploy/export_db.sh
#
# 可选环境变量：
#   SOURCE_CONTAINER  源容器名（默认 pg-medicine）
#   PG_USER           数据库用户（默认 postgres）
#   DBS               要导出的库（默认 "dashenlin gaoji haiwang"）
#   JOBS              并行度（默认 4；仅目录格式 -Fd 支持）
#   OUT               备份根目录（默认 ./deploy/pgdump）
#   STAMP             备份批次名（默认当前时间）
#
# 千万级数据说明：
#   使用 -Fd（目录格式）+ -j 并行 + -Z 6 压缩，导出与后续恢复都远快于单文件 -Fc。
# ============================================================
set -euo pipefail

SOURCE_CONTAINER="${SOURCE_CONTAINER:-pg-medicine}"
PG_USER="${PG_USER:-postgres}"
DBS="${DBS:-dashenlin gaoji haiwang}"
JOBS="${JOBS:-4}"
OUT="${OUT:-$(pwd)/deploy/pgdump}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
DIR="$OUT/$STAMP"

if ! command -v docker >/dev/null; then
  echo "❌ 未检测到 docker"; exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$SOURCE_CONTAINER"; then
  echo "❌ 找不到正在运行的容器：$SOURCE_CONTAINER"
  echo "   当前运行中的容器："
  docker ps --format '   - {{.Names}}  ({{.Image}})'
  echo "   请用 SOURCE_CONTAINER=<容器名> $0 指定"
  exit 1
fi

mkdir -p "$DIR"
echo "==> 源容器 : $SOURCE_CONTAINER"
echo "==> 用户   : $PG_USER"
echo "==> 并发   : $JOBS"
echo "==> 输出   : $DIR"
echo ""

for db in $DBS; do
  echo "---- 导出 $db ----"
  docker exec "$SOURCE_CONTAINER" rm -rf "/tmp/$db"
  docker exec "$SOURCE_CONTAINER" pg_dump \
    -U "$PG_USER" -Fd -j "$JOBS" -Z 6 -d "$db" -f "/tmp/$db"
  docker cp "$SOURCE_CONTAINER:/tmp/$db" "$DIR/$db" >/dev/null
  docker exec "$SOURCE_CONTAINER" rm -rf "/tmp/$db"
done

TARBALL="$OUT/pgdump_${STAMP}.tgz"
tar czf "$TARBALL" -C "$OUT" "$STAMP"
rm -rf "$DIR"

echo ""
echo "✅ 导出完成"
echo "   备份包: $TARBALL"
echo "   大小  : $(du -sh "$TARBALL" | cut -f1)"
echo ""
echo "下一步（会提示输入服务器密码）："
echo "   scp $TARBALL root@8.166.120.28:/opt/CRDB/deploy/pgdump/"
