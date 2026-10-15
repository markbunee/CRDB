#!/usr/bin/env bash
# ============================================================
# 服务器执行：给 CRDB 配域名 HTTPS
#
# 背景：80/443 已被 Ticketsys 的 nginx 占用，CRDB 不能再绑。
# 做法：复用 Ticketsys 的 nginx —— 把 crdb-web 接入它的 Docker 网络，
#       再往 ticketsys-web 的 /etc/nginx/conf.d/ 放一个 server block 反代过去。
#       不修改 Ticketsys 任何现有文件。
#
#   bash deploy/setup_nginx_vhost.sh
#
# 可选环境变量：
#   TICKETSYS_DIR=/opt/Ticketsys   Ticketsys 项目目录
#   TICKETSYS_WEB=ticketsys-web    Ticketsys 的 nginx 容器名
#   CERTBOT_EMAIL=admin@neftoo.xyz
#
# 注意：ticketsys-web 若被重建（如 docker compose up -d 重建容器），
#       这个 conf 会丢失，重跑本脚本即可；要永久化可在 Ticketsys 的
#       docker-compose.prod.yml 里加一条 volume 挂载。
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

TICKETSYS_DIR="${TICKETSYS_DIR:-/opt/Ticketsys}"
TICKETSYS_WEB="${TICKETSYS_WEB:-ticketsys-web}"
CRDB_WEB="${CRDB_WEB:-crdb-web}"

# ---------- 0) 读取配置 ----------
if [ ! -f .env ]; then
  echo "❌ 未找到 .env，请先 cp .env.example .env"
  exit 1
fi

_env() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' | sed 's/^"//;s/"$//'; }
DOMAIN="${DOMAIN:-$(_env DOMAIN)}"
EMAIL="${CERTBOT_EMAIL:-$(_env CERTBOT_EMAIL)}"

if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "crdb.example.com" ]; then
  echo "❌ 请先在 .env 里设置真实域名 DOMAIN=xxx（当前：${DOMAIN:-空}）"
  exit 1
fi
[ -n "$EMAIL" ] || EMAIL="admin@${DOMAIN#*.}"

echo "==> 域名: $DOMAIN"

# ---------- 1) 检查容器 ----------
for c in "$CRDB_WEB" "$TICKETSYS_WEB"; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "❌ 容器 $c 未运行"
    exit 1
  fi
done

# ---------- 2) 找出 Ticketsys 所在的 Docker 网络 ----------
NET="$(
  docker inspect "$TICKETSYS_WEB" --format \
    '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}
{{end}}' | grep -vE '^(bridge|host|none)$' | head -1
)"

if [ -z "$NET" ]; then
  echo "❌ 未能识别 $TICKETSYS_WEB 所在的自定义网络"
  docker inspect "$TICKETSYS_WEB" --format '{{json .NetworkSettings.Networks}}'
  exit 1
fi
echo "==> Ticketsys 网络: $NET"

# ---------- 3) 把 crdb-web 接入该网络（已接入则忽略）----------
if docker inspect "$CRDB_WEB" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}
{{end}}' | grep -qx "$NET"; then
  echo "==> $CRDB_WEB 已在网络 $NET 中"
else
  echo "==> 将 $CRDB_WEB 接入 $NET ..."
  docker network connect "$NET" "$CRDB_WEB"
fi

# ---------- 4) 申请证书（webroot 复用 Ticketsys nginx 的 80 端口）----------
mkdir -p "$TICKETSYS_DIR/certbot/www"

if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo "==> 证书已存在，跳过申请"
else
  echo "==> 申请 Let's Encrypt 证书 ..."
  if ! command -v certbot >/dev/null; then
    $SUDO apt-get update -y && $SUDO apt-get install -y certbot
  fi
  $SUDO certbot certonly --webroot -w "$TICKETSYS_DIR/certbot/www" -d "$DOMAIN" \
    --non-interactive --agree-tos -m "$EMAIL" --expand
fi

# ---------- 5) 生成 vhost 并放进 ticketsys-web ----------
mkdir -p "$ROOT/deploy"
VHOST="$ROOT/deploy/crdb-vhost.conf"
sed "s/\${DOMAIN}/$DOMAIN/g" "$ROOT/deploy/nginx-vhost.conf.template" > "$VHOST"
echo "==> 已生成 $VHOST"

docker cp "$VHOST" "$TICKETSYS_WEB:/etc/nginx/conf.d/crdb.conf"
docker exec "$TICKETSYS_WEB" nginx -t
docker exec "$TICKETSYS_WEB" nginx -s reload

# ---------- 6) 证书自动续期（Ticketsys 已有的 cron 会一起续，没有才补）----------
if ! crontab -l 2>/dev/null | grep -q 'certbot renew'; then
  echo "==> 补充 certbot 自动续期 cron ..."
  CRON_LINE="0 3 * * * certbot renew --quiet --deploy-hook 'docker exec $TICKETSYS_WEB nginx -s reload'"
  ( crontab -l 2>/dev/null ; echo "$CRON_LINE" ) | crontab -
else
  echo "==> 已存在 certbot renew 的 cron，本证书会被一并续期"
fi

echo ""
echo "✅ 完成！访问：https://$DOMAIN/"
echo "   健康检查：https://$DOMAIN/health"
