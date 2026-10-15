# -*- coding: utf-8 -*-
"""启动入口脚本（在 backend/ 目录下运行，或任意目录运行本脚本均可）。

用法:
    python main.py
    或
    uvicorn app.main:app --host 0.0.0.0 --port 18000 --reload

端口为什么是 18000 而不是 8000：
本机 8000 已被 ticketsys-api 容器占用（docker 容器，kill 不掉），
沿用 8000 会导致本服务绑定失败，而前端 /api 请求会静默打到 ticketsys 上，
表现为「所有接口 404 / 加载数据失败」，排查起来非常隐蔽。
端口可用环境变量覆盖：BACKEND_PORT=18001 python main.py

然后浏览器访问 http://localhost:18000
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
    # 默认 18000，可用 BACKEND_PORT 覆盖（见文件头部说明）
    port = int(os.environ.get("BACKEND_PORT", "18000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_dirs=[BACKEND_DIR],
        app_dir=BACKEND_DIR,
    )
