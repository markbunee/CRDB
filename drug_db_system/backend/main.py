# -*- coding: utf-8 -*-
"""启动入口脚本（在 backend/ 目录下运行，或任意目录运行本脚本均可）。

用法:
    python main.py
    或
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

然后浏览器访问 http://localhost:8000
"""
import os
import sys

# backend/ 目录（本脚本所在目录）
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# 确保 backend/ 在 sys.path 中，以便 import app
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 让 uvicorn reload 子进程也能找到 app 包（子进程继承环境变量）
env_path = os.environ.get("PYTHONPATH", "")
if BACKEND_DIR not in env_path.split(os.pathsep):
    os.environ["PYTHONPATH"] = (
        BACKEND_DIR + (os.pathsep + env_path if env_path else "")
    )

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[BACKEND_DIR],
        app_dir=BACKEND_DIR,
    )
