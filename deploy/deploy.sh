#!/usr/bin/env bash
# ============================================================
# CRDB（医药销售数据库）部署脚本 · 阿里云 ECS · Ubuntu 22.04 · Docker
#
# 注意：本机 80/443 已被 Ticketsys 占用，本脚本不会去抢。
#   - 应用只绑一个冷门端口 18080 供本机自检（避开 Ticketsys / 宝塔）
#   - 公网域名访问用 deploy/setup_nginx_vhost.sh（复用 Ticketsys 的 nginx）
#
# 数据：默认不从 xlsx 导入，请用 deploy/export_db.sh + deploy/import_db.sh 迁移
#
# 用法：
#   bash deploy/deploy.sh            # 构建并启动（末尾自动尝试配域名 HTTPS）
#   bash deploy/deploy.sh --no-vhost # 只起服务，不配域名
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
NO_VHOST=0
[ "${1:-}" = "--no-vhost" ] && NO_VHOST=1

echo "==> 项目目录: $ROOT"

# ---------- 0) 环境预检 ----------
if [ ! -f .env ]; then
  echo "❌ 未找到 .env，请先执行：cp .env.example .env 并填写 POSTGRES_PASSWORD"
  exit 1
fi

if ! command -v docker >/dev/null; then
  echo "❌ 未检测到 docker"; exit 1
fi

# ---------- 1) 构建并启动 ----------
echo "==> 构建并启动服务 ..."
$COMPOSE up -d --build

# ---------- 2) 等待健康检查 ----------
echo "==> 等待服务就绪 ..."
OK=0
for i in $(seq 1 60); do
  if docker exec crdb-web wget -qO- http://127.0.0.1/health >/dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 2
done

if [ "$OK" -ne 1 ]; then
  echo "⚠️  健康检查超时，请查看日志："
  echo "   docker compose logs -f api"
  echo "   docker compose logs -f web"
fi

# ---------- 3) 域名 HTTPS（可选）----------
_env() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' | sed 's/^"//;s/"$//'; }
DOMAIN="${DOMAIN:-$(_env DOMAIN)}"

if [ "$NO_VHOST" = "1" ]; then
  echo "==> 已跳过域名配置（--no-vhost）"
elif [ -z "$DOMAIN" ] || [ "$DOMAIN" = "crdb.example.com" ]; then
  echo "==> .env 中未设置真实域名，跳过 HTTPS 配置"
else
  echo "==> 配置域名 HTTPS: $DOMAIN"
  bash "$ROOT/deploy/setup_nginx_vhost.sh" || echo "⚠️  域名配置未成功（不影响本机自检），可稍后手动跑：bash deploy/setup_nginx_vhost.sh"
fi

echo ""
echo "✅ 部署完成"
echo "   本机自检 : curl http://127.0.0.1:${WEB_PORT:-18080}/health"
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "crdb.example.com" ]; then
  echo "   公网访问 : https://$DOMAIN/"
fi
echo "   查看日志 : docker compose logs -f api"
echo ""
echo "⚠️  若还没导入数据：bash deploy/import_db.sh"
