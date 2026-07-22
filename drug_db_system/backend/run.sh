#!/usr/bin/env bash
# 后端启动脚本：自动切换到 backend 目录并启动 uvicorn
# 用法: bash run.sh
set -e
cd "$(dirname "$0")"
echo "启动后端，工作目录: $(pwd)"
echo "日志文件: $(pwd)/log.txt"
exec python main.py
