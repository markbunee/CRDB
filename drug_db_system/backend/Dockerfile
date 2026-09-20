# 医药销售数据库 · 后端（FastAPI）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 所有依赖均有 manylinux wheel，无需编译工具链
COPY requirements.txt .
RUN pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com \
        -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh && mkdir -p data

EXPOSE 8000

CMD ["./docker-entrypoint.sh"]
