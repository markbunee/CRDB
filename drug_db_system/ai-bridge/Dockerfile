# ============================================================
# ai-bridge —— CRDB AI 问数的 LLM 桥梁
#
# 构建上下文 = drug_db_system/（ai-bridge/ 与 pi/ 的同级目录）：
#   docker build -f drug_db_system/ai-bridge/Dockerfile -t crdb/ai-bridge drug_db_system
# 或（推荐，compose 已默认启动本服务）：
#   docker compose up -d --build ai
#
# 两种模式，**都一定能构建成功**：
#
#  A. 已生成 pi 打包产物（drug_db_system/pi/.artifacts/pi-packages）
#     → 镜像带完整 L2（LLM 兜底）。产物在宿主机上按 ai-bridge/README.md 生成：
#       cd drug_db_system/pi
#       npm install --ignore-scripts
#       npm run pack:packages -- --out .artifacts/pi-packages
#       node scripts/use-local-packages.mjs \
#         --manifest .artifacts/pi-packages/manifest.json \
#         --consumer ../ai-bridge \
#         --package @earendil-works/pi-agent-core --package @earendil-works/pi-ai
#     （最后一步会把 ai-bridge/package.json 的依赖改写成
#       file:./../pi/.artifacts/pi-packages/tarballs/*.tgz）
#
#  B. 没有产物 → 自动剥掉 package.json 里的 pi 依赖，构建「纯规则」镜像：
#     容器照常启动，/fill-slots 返回 503，Python 侧静默退回规则路径（L0/L1），
#     系统功能完全不受影响（/health 的 pi_ready 字段会如实报告 false）。
#     之后补上产物再 docker compose up -d --build ai 即可无缝升级到完整模式。
# ============================================================
FROM node:22.19-alpine AS build
WORKDIR /src

COPY ai-bridge/package.json ./ai-bridge/package.json
COPY ai-bridge/tsconfig.json ./ai-bridge/tsconfig.json
COPY ai-bridge/src ./ai-bridge/src
# pi 源码整体进构建上下文（其中 .artifacts/pi-packages 是可选产物）。
# 为什么不直接 COPY pi/.artifacts/pi-packages：COPY 一个不存在的路径会让
# docker build 立刻失败——那样「没打产物」就等于「整个 compose 起不来」。
# 拷 pi/ 整体后由下面的 shell 判断决定走 A 还是 B。
COPY pi/ ./pi/

WORKDIR /src/ai-bridge
RUN set -eux; \
    if [ -f /src/pi/.artifacts/pi-packages/manifest.json ]; then \
        echo "[ai-bridge] 检测到 pi 打包产物 → 完整模式（L2 可用）"; \
        npm install --ignore-scripts --no-audit --no-fund; \
    else \
        echo "[ai-bridge] 未检测到 pi 打包产物 → 纯规则模式（/fill-slots 将返回 503）"; \
        node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync('package.json','utf8'));const drop=(o)=>{if(o)for(const k of Object.keys(o))if(k.startsWith('@earendil-works/'))delete o[k];};drop(p.dependencies);drop(p.optionalDependencies);drop(p.devDependencies);drop(p.overrides);delete p.overrides;fs.writeFileSync('package.json',JSON.stringify(p,null,2));"; \
        npm install --ignore-scripts --no-audit --no-fund; \
    fi
RUN npm run build

# ---------- 运行阶段：只带 node_modules + dist ----------
FROM node:22.19-alpine
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000
COPY --from=build /src/ai-bridge/node_modules ./node_modules
COPY --from=build /src/ai-bridge/dist ./dist
# 能力目录与 Python 侧共用（容器内路径由 CAPABILITIES_PATH 指向）
COPY backend/data/ai_capabilities.json /app/data/ai_capabilities.json
EXPOSE 3000
CMD ["node", "dist/server.js"]
