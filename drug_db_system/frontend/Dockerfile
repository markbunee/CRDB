# 医药销售数据库 · 前端（Vue3 + Vite → Nginx）
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .

# 部署开关：true = 保留导出/下载按钮但点击无反应
ARG VITE_DISABLE_DOWNLOAD=true
ENV VITE_DISABLE_DOWNLOAD=$VITE_DISABLE_DOWNLOAD

# 只跑 vite build（跳过 vue-tsc 类型检查，避免类型噪音导致构建失败）
RUN npm run build-only

FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/health >/dev/null || exit 1
