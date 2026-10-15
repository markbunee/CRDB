# ai-bridge —— CRDB AI 问数的 LLM 桥梁

> **定位：只补槽位，不查数。**
> 真正的查询永远在 Python 侧完成（复用既有统计函数、缓存、鉴权、查询闸门）。
> 本服务只在**规则路径失效**时被调用，是兜底，不是主路径。

## 为什么单独建这个目录（而不是改 pi）

`pi/` 是上游开源项目源码，**保持零改动**，方便后续 `git pull` 跟进。
所有 CRDB 的适配代码都写在这里：

```
CRDB_AI/
├── docker-compose.yml
└── drug_db_system/
    ├── pi/          ← 上游源码（只读，只用来构建产物）
    └── ai-bridge/   ← 本项目桥梁代码（全部业务适配都在这）
```

> `pi/` 与 `ai-bridge/` 是 `drug_db_system/` 下的同级目录，
> 所以下面所有相对路径（`../ai-bridge`、`../pi/.artifacts`）都成立。

## 在架构里的位置

```
用户提问（Python）
  ├─ L0 规则提取 + L1 别名命中 ── 命中 ──→ 直接出确认卡 / 执行   【不经过本服务】
  └─ 能力都没识别出来 ──────────────→ POST /fill-slots ──→ 本服务
                                        ↓
                                   pi Agent（单步）
                                        ↓
                              结构化槽位 → 仍回确认卡让用户确认
```

**即使本服务完全不可用，AI 功能照常工作**（高频问法由规则覆盖），
只是长尾问法需要用户自己点选槽位。这是有意的降级设计。

## 为什么是「单步」而不是多轮 ReAct

| 控制点 | 实现 |
|---|---|
| 一步即停 | 工具返回 `terminate: true` + `afterToolCall` 再置一次 + `finishTurn → {action:"end"}` |
| 输出结构化 | 用 typebox 参数 schema，模型必须调用 `resolve_query`，不会吐半截 JSON |
| 取值封闭 | 所有枚举来自 `ai_capabilities.json`，模型不可能产出后端不接受的值 |
| 省 token | `thinkingLevel: "off"` 关掉 reasoning；systemPrompt 前缀固定，命中 prompt cache |
| 不越权 | 只补槽位，**不执行查询**；输出回到 Python 侧仍要过确认卡 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CAPABILITIES_PATH` | `/app/data/ai_capabilities.json` | 与 Python 侧**同一份**能力目录 |
| `PORT` | `3000` | |
| `FILL_TIMEOUT_MS` | `15000` | 单次补槽超时 |

**本服务没有任何密钥配置项。** provider / model / api_key / base_url
由前端保存到后端数据库，随**每次请求**透传进来（`body.llm`），用完即弃。
这样多用户各用各的 Key，容器被拿到也拿不到密钥，且不存在并发写环境变量的串号问题。

## 本地开发

```bash
# 从仓库根 CRDB_AI/ 开始
# 1) 构建 pi 产物（只需一次，pi 有更新时重做）
cd drug_db_system/pi
npm install --ignore-scripts
npm run pack:packages -- --out .artifacts/pi-packages
node scripts/use-local-packages.mjs \
  --manifest .artifacts/pi-packages/manifest.json \
  --consumer ../ai-bridge \
  --package @earendil-works/pi-agent-core \
  --package @earendil-works/pi-ai

# 2) 装依赖 + 跑起来
cd ../ai-bridge
npm install --ignore-scripts
npm run dev     # 或 npm run build && npm start
```

自测（注意要带上 `llm`，否则 503）：

```bash
curl -s localhost:3000/health
curl -s -X POST localhost:3000/fill-slots -H 'Content-Type: application/json' \
  -d '{"text":"维生素d1-9月的情况","db_key":"dashenlin","slots":{},"capabilities":["q_boxes","q_overview"],"data_range":{"max_date":"2026-08-31"},"llm":{"provider":"deepseek","model":"deepseek-chat","api_key":"sk-xxx","base_url":""}}'
```

## 部署

`docker-compose.yml` 里的 `ai` 服务默认**不启动**（`profiles: ["ai"]`），
因此常规的 `docker compose up -d` 不受影响。需要 LLM 兜底时：

```bash
# 先按上面「构建 pi 产物」两步生成 .artifacts/pi-packages 并改写 ai-bridge/package.json
docker compose --profile ai up -d --build ai
```

再把后端环境变量 `AI_BRIDGE_ENABLED=true`。
**模型 Key 不需要放 .env**——由每个用户在页面「AI 助手 → 模型 → 设置」里自己配。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，返回 `{ok, llm}` |
| POST | `/fill-slots` | 补槽位。入参 `{text, db_key, slots, capability, capabilities, data_range}`；出参 `{ok, capability, slots, tokens}` |
