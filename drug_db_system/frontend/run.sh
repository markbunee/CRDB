#!/usr/bin/env bash
# 前端启动脚本：自动切换到 frontend 目录并启动 vite dev server
# 用法: bash run.sh
set -e
cd "$(dirname "$0")"
echo "启动前端，工作目录: $(pwd)"
echo "日志通过浏览器上报到后端，写入 frontend/log.txt"
exec npm run dev
