#!/usr/bin/env bash
# ============================================================
# 医药销售数据库 一键启动脚本（WSL2）
#   启动：./start.sh
#   停止：Ctrl + C
# 前提：PostgreSQL 已自行启动（默认 localhost:5433，可用环境变量覆盖）
# ============================================================
set -uo pipefail

# ---------------- 配置区（按需修改）----------------
CONDA_HOME="${HOME}/miniforge3"        # conda 安装目录
CONDA_ENV="py312torch222"              # conda 环境名（与 Mawork 后端一致）
NVM_DIR="${HOME}/.nvm"                 # nvm 目录
NODE_VERSION="v22.23.1"                # Node 版本

# 项目根 = 本脚本所在目录（drug_db_system/）。
# 刻意**不写死绝对路径**：本项目是从 CRDB 复制出来的，硬编码会把服务启动到另一个项目
# 的代码上（现象：改了代码却完全不生效、新接口 404），排查代价极高。
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 后端端口默认 18000：本机 8000 已被 ticketsys-api 容器占用（docker 容器，
# 脚本的 free_port 杀不掉），8080/80/443/5432 也被其它服务占着。
# 需要改动就 `BACKEND_PORT=xxxx ./start.sh`，前端 proxy 会自动跟随。
BACKEND_PORT="${BACKEND_PORT:-18000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
# 后端热重载：/mnt（WSL2 DrvFS）挂载或远程机上 watchfiles 递归监视
# 会因 inotify 限制/内存不足而崩（Cannot allocate memory / os error 12），
# 直接拖死后端进程。服务端长跑请保持 false。
# 本地开发确需热重载时，改用 --reload 并限定目录：
#   uvicorn app.main:app --reload --reload-dir backend/app
ENABLE_RELOAD=false
# --------------------------------------------------

# 数据库连接（可用环境变量覆盖）
export PG_HOST="${PG_HOST:-localhost}"
export PG_PORT="${PG_PORT:-5433}"
export PG_USER="${PG_USER:-postgres}"
export PG_PASSWORD="${PG_PASSWORD:-postgres}"
export PG_ADMIN_DB="${PG_ADMIN_DB:-postgres}"

# 前端 vite 的 /api 代理目标：与后端端口联动，避免两处各写一个数字对不上
export VITE_BACKEND_URL="${VITE_BACKEND_URL:-http://127.0.0.1:${BACKEND_PORT}}"

# Excel 源数据目录（仅「导入 Excel」功能用到，日常查询不需要）。
# 默认取项目根的上一级；若那里没有 xlsx，给出提示引导用环境变量指定。
export EXCEL_DIR="${EXCEL_DIR:-$(dirname "$PROJECT_ROOT")}"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${GREEN}[CRDB]${NC} $1"; }
warn() { echo -e "${YELLOW}[警告]${NC} $1"; }
err()  { echo -e "${RED}[错误]${NC} $1"; exit 1; }

# ---------- 1. 加载 conda（脚本非交互，必须手动 source）----------
[ -f "$CONDA_HOME/etc/profile.d/conda.sh" ] || err "找不到 conda：$CONDA_HOME"
source "$CONDA_HOME/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV" || err "conda 环境不存在：$CONDA_ENV"
log "conda 已激活 → ${BOLD}$CONDA_ENV${NC} ($(python -V 2>&1 | awk '{print $2}'))"

# ---------- 2. 加载 Node（nvm 同样需手动 source）----------
export NVM_DIR
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" >/dev/null 2>&1
nvm use "$NODE_VERSION" >/dev/null 2>&1
# 兜底：nvm 未生效则直接用绝对路径
command -v npm >/dev/null 2>&1 || export PATH="$NVM_DIR/versions/node/$NODE_VERSION/bin:$PATH"
command -v npm >/dev/null 2>&1 || err "找不到 npm，请检查 nvm 安装"
log "Node 已就绪 → ${BOLD}$(node -v)${NC} / npm $(npm -v)"

# ---------- 3. 释放被占用的端口 ----------
free_port() {
    local port=$1 pids
    pids=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
    if [ -n "$pids" ]; then
        warn "端口 $port 被占用，正在释放..."
        for p in $pids; do kill -9 "$p" 2>/dev/null; done
        sleep 1
        return
    fi
    # 端口被占但拿不到 PID：几乎一定是 docker 容器（进程属 root）。
    # 此时 kill 是无效的，必须提前报错 —— 否则后面只看到一句
    # "address already in use"，前端所有 /api 请求会静默打到别的服务上（表现为 404）。
    if ss -tln 2>/dev/null | grep -q ":$port "; then
        err "端口 $port 被占用且无法自动释放（多为 docker 容器）。
     查看占用者：docker ps --format '{{.Names}}\t{{.Ports}}'
     换端口启动：BACKEND_PORT=18001 ./start.sh"
    fi
}
free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# ---------- 4. 依赖与目录检查 ----------
cd "$PROJECT_ROOT" || err "项目目录不存在：$PROJECT_ROOT"
python -c "import fastapi, uvicorn, psycopg2, pandas, openpyxl, python_calamine, xlsxwriter" >/dev/null 2>&1 || {
    warn "缺少后端依赖，正在安装 backend/requirements.txt ..."
    pip install -r backend/requirements.txt || err "依赖安装失败"
}
[ -d frontend/node_modules ] || warn "未检测到 frontend/node_modules，前端可能启动失败"

# Excel 只在「导入数据」时用到，日常查询不需要；找不到就说清楚，别让人以为系统坏了
if ! compgen -G "$EXCEL_DIR/*.xlsx" >/dev/null 2>&1 \
   && ! compgen -G "$EXCEL_DIR/*.csv" >/dev/null 2>&1; then
    warn "Excel 源数据目录（$EXCEL_DIR）下没有 xlsx/csv —— 日常查询不受影响，但「导入数据」会失败。
     数据在别处时：EXCEL_DIR=/数据目录 ./start.sh"
fi

# ---------- 5. 启动后端 ----------
log "启动后端 FastAPI → 端口 $BACKEND_PORT"
RELOAD_FLAG=""; $ENABLE_RELOAD && RELOAD_FLAG="--reload"
cd "$PROJECT_ROOT/backend"
uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" $RELOAD_FLAG \
    > >(sed "s/^/${CYAN}[后端]${NC} /") 2>&1 &
BACKEND_PID=$!

# 等待后端健康检查通过
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
        log "后端就绪 ✓"; break
    fi
    [ "$i" -eq 30 ] && warn "后端健康检查超时，请查看上方日志"
    sleep 1
done

# ---------- 6. 启动前端 ----------
log "启动前端 Vite → 端口 $FRONTEND_PORT"
cd "$PROJECT_ROOT/frontend" || err "前端目录不存在"
[ -d node_modules ] || {
    warn "首次运行，安装前端依赖（可能需要几分钟）..."
    npm install || err "前端依赖安装失败"
}
npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" \
    > >(sed "s/^/${GREEN}[前端]${NC} /") 2>&1 &
FRONTEND_PID=$!

# ---------- 7. 退出清理 ----------
cleanup() {
    echo ""
    log "正在停止服务..."
    pkill -P "$BACKEND_PID"  2>/dev/null   # 先杀 --reload 派生的子进程
    pkill -P "$FRONTEND_PID" 2>/dev/null
    kill "$BACKEND_PID"  "$FRONTEND_PID" 2>/dev/null
    sleep 1
    log "已停止 ✓"
    exit 0
}
trap cleanup INT TERM

# ---------- 8. 启动完成 ----------
sleep 3
echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  医药销售数据库 已启动${NC}"
echo -e "  项目  →  $PROJECT_ROOT"
echo -e "  前端  →  http://localhost:$FRONTEND_PORT"
echo -e "  后端  →  http://localhost:$BACKEND_PORT   (API 文档 /docs)"
echo -e "  代理  →  前端 /api  →  $VITE_BACKEND_URL"
echo -e "  数据库→  ${PG_USER}@${PG_HOST}:${PG_PORT}（请确保已启动）"
echo -e "  Excel →  $EXCEL_DIR"
echo -e "  停止  →  ${YELLOW}Ctrl + C${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""

wait
