# HANDOFF · AI 问数（精简版）

> 交接给下一个人的最小上下文。完整背景见 `drug_db_system/修改日志.md` 的 2026-10-14 条目。

## 1. 一句话

用户用中文问数 → **纯规则**提取成 8 个槽位 → 缺哪个就把整张「确认卡」列出来让人补 → 齐了才查 → 回答必带口径说明。默认路径**不调用 LLM**（0 token）。

## 2. 代码在哪

| 层 | 文件 | 职责 |
|---|---|---|
| 能力目录（**单一真源**） | `drug_db_system/backend/data/ai_capabilities.json` | 9 个能力 + 8 个槽位定义。Python 校验和 Node schema **都读它** |
| 提取 | `backend/app/services/ai_slots.py` | 时间/地域/品类/指标…纯规则提取，含数据可得性校准 |
| 确认卡 | `backend/app/services/ai_clarify.py` | 三态卡片生成 + 卡片答复 → plan |
| 执行调度 | `backend/app/services/ai_executor.py` | plan → 各能力分派，直接 import 既有统计函数（不走 HTTP） |
| SQL 聚合 | `backend/app/services/ai_agg.py` | **唯一拼 SQL 的地方**：多指标一次扫描、三窗 CASE WHEN、门店数去重 |
| 回答渲染 | `backend/app/services/ai_render.py` | 中文话术 + 口径说明 + 缺价告警（模板，0 token） |
| 埋点 | `backend/app/services/ai_analytics.py` | SQLite `backend/data/ai_analytics.db`（4 表 + 会话） |
| **模型配置** | `backend/app/services/ai_model_config.py` + `backend/data/ai_providers.json` | 用户自配多套 LLM、切换、密钥掩码、连通性验证 |
| 路由 | `backend/app/routers/ai.py` | `/api/{db}/ai/{ask,confirm,abandon,feedback}`、`/api/ai-admin/*` |
| LLM 兜底 | `drug_db_system/ai-bridge/`（与 `drug_db_system/pi/` 同级） | Node + pi-agent-core，**pi 源码零改动**，默认不启用 |
| 前端 | `frontend/src/components/AiAssistantPanel.vue`、`AiQueryCard.vue`、`AiModelDialog.vue` | 对话 + 确认卡 + 模型管理弹窗 |

## 3. 数据流

```
提问 → ai_slots.extract()  ──能力都没识别──→ ai-bridge(LLM) 补槽位 ─┐
                                                                    ├→ build_card()
                                                                    │    ├ ready=false → 返回卡片（问全）
                                                                    │    └ ready=true  → ai_executor.execute()
                                                                    │                      ├ 直接调 _run_box_count / _compute_yoy_mom /
                                                                    │                      │  _run_store_count / _run_store_ability / _run_store_trend
                                                                    │                      └ 多指标：一条 SQL 出 盒数+金额+门店数 × 三窗
                                                                    └→ 回答 + caveats（口径/缺价告警）
```

## 4. 常见改动

**加一个问法（业务同学最常做）**
→ 编辑 `ai_capabilities.json`，往对应能力的 `aliases` 里加词。**不用改代码**，热生效（每次请求现读）。

**加一个新能力**
→ `ai_capabilities.json` 加一项（指定 `backend`）→ `ai_executor.execute()` 加一个分支。

**改某个槽位的取值**
→ 只改 `ai_capabilities.json` 的 `slots.*.values`。Python 校验、Node schema、前端选项**三处自动同步**。

**提取错了（比如某句话地域没识别出来）**
→ 改 `ai_slots.py` 对应 matcher。它是纯函数，**可以直接写单测**。

## 5. 三个必须记住的坑

1. **日期必须按 `MAX(日期)` 裁剪**。用户问"1-9月"、库里只到 8 月，不裁剪就会拿 8 个月跟去年同期 9 个月比 → **假性大跌**。逻辑在 `ai_slots.parse_time()` 末尾，别删。
2. **闸门只拦 GET**，`/ai/confirm` 是 POST，所以 `routers/ai.py` 的 `_run_guarded()` 必须保留，否则 AI 是绕开限流的后门。
3. **金额会静默偏低**：未配开票价的品类按 0 计入。`_caveats()` 会自动告警，别删。

## 6. 埋点怎么用（这是后期优化的地基）

后台接口（仅管理员）：`GET /api/ai-admin/{overview,questions,unresolved,slot-choices}`

| 想优化什么 | 看哪个字段 | 动作 |
|---|---|---|
| 0 token 覆盖率 | `overview.zero_token_rate` | 低 → 说明模板/别名不够 |
| 哪些词没解析出来 | `unresolved` 词频 | → 加进 `aliases` |
| 反问太烦 | `slot_choices` 分布 | 高频选项 → 变成默认值（仍显示可改） |
| 提取器哪里错了 | `ai_execution.edited_from` | 用户手动改了哪个字段 → 改对应 matcher |
| 用户跑了 | `status=abandoned` + `clarify_count` | 反问太多/问错了 |

## 7. 性能（真实库实测，dashenlin 861 万行）

| 场景 | 耗时 |
|---|---|
| 只要盒数 + 金额 | **1.1s** |
| 含动销门店数（要 `COUNT(DISTINCT)`） | 5.3s |
| 单城市 | 0.65s |
| 全国 + 同比（最坏） | 6.2s |
| 缓存命中（`stats_cache`，10 分钟） | ~0s |

**瓶颈是 `COUNT(DISTINCT)`**：同条件 `SUM` 0.61s vs `COUNT(DISTINCT)×2` 3.10s。
所以主 SQL **刻意不算门店数**，单独一条查，且只在 `metric` 含门店数时才查。

**还能再快，但要建索引（未擅自做）**：库里缺 `(省份,日期)` 复合索引，
且 `schema_def.py` 声明的 `idx_dashenlin_date` / `city_date` / `city_prod_month_store`
**在实际库中不存在**（数据直接导入、没跑 `create_indexes.py`），省份查询目前走 `Parallel Seq Scan`。
补 `(省份,日期)` 与 `(省份,月度,门店编码)` 预计能把 5~6s 降到 1~2s。

## 8. 已知缺口 / 注意事项

- `ai-bridge` 默认不启动（`profiles: ["ai"]`）。启用需先在宿主机打 pi 产物，见 `drug_db_system/ai-bridge/README.md`。
  **不启动也不影响 AI 功能**（规则路径覆盖高频问法）；且它**不接收任何密钥**
  （Key 由用户在页面里配、随请求透传），所以 `.env` 里不需要配 `AI_API_KEY`。
- **销售表与库存表地域命名不一致**：销售「广东省」、库存「广东」。动销率要查两表，已用 `_expand_region_terms()` 双写法化解；将来若改数据务必同步这里。
- **开票价配置会扭曲金额**：实测 2025 年同期 63.7% 的盒数未配价（按 0 计入），2026 年只有 12.9%，
  于是出现「盒数 -35%、金额 +47%」的背离。代码已主动告警，但**根因是数据配置**，要让业务补品类映射表的开票价。
- 确认卡的地域快选项取"近 180 天销量 Top12"，冷启动有一次聚合（10 分钟缓存）。

## 9. 模型配置（用户自配 LLM）

**用户视角**：AI 助手左上角「模型 → 设置」→ 新增配置 → 选供应商（Base URL 自动带出）→ 选/填模型 →
填自己的 API Key → **点「测试连接」**（强烈建议，能提前发现地址或密钥问题）→ 保存 → 点「启用」。

**关键行为**（都有实测覆盖）

- **按用户隔离**：一切查询带 `user_id`，看不到别人的配置。
- **密钥不回显**：接口只回 `sk-****abcd`；编辑时 **API Key 留空 = 保留原密钥**。
- **多套 + 切换**：同一用户同时只有一套 `is_active`；切换时先全置 0 再置 1。
- **第一套自动启用**；**删除启用项会自动顺延**到最后新建的一套（避免"配了却用不了"）。
- **验证逻辑**：`GET {base}/models` 带 Bearer，**多路径依次探测**（先试 `{base}/models`，
  再试 `{base}/v1/models`），401/403/429 立即返回（说明路径对、问题在密钥或额度），
  只有 404 才试下一个。所以**新增厂商只需改 `ai_providers.json`**，不用动后端分支。
  - `base_url` 留空 → 用目录里的默认地址；自定义网关必须自己填。

**供应商目录 `ai_providers.json` 的三个字段（容易搞混）**

| 字段 | 作用 | 注意 |
|---|---|---|
| `base_url` | 请求地址 | 各家写法不一：`api.deepseek.com`、`api.moonshot.cn/v1`、`open.bigmodel.cn/api/paas/v4` |
| `api` | **请求协议** | `openai-completions`（绝大多数）/ `openai-responses`（仅 OpenAI）/ `anthropic-messages`（仅 Claude） |
| `pi` | **模型定义模板来源** | 借同协议的内置 provider 抄 `Model` 的必填字段；**不是**请求走的实现 |

**为什么 `api` 必须单独标**：pi-ai 的 provider 各走不同协议，
OpenAI 官方走 `/responses`，而智谱 / 月之暗面 / 通义 / OneAPI / vLLM / Ollama
**都只支持 `/chat/completions`**。早先统一借 `openai` 实现 → 智谱请求被发到
`/responses` → 404。现在 `ai-bridge/src/provider.ts` 用 `createProvider` **自建 provider**：
协议按请求指定、地址用用户的、模型列表只放用户填的那一个
（这样**任意模型名都能用** —— `models.getModel` 只在已注册列表里 `find`，
不自建的话 `glm-4-flash` 这种不在内置清单里的名字会取不到模型）。

**智谱的两条地址（填错必 404）**

| 套餐 | Base URL |
|---|---|
| Coding 套餐（包月） | `https://open.bigmodel.cn/api/coding/paas/v4` |
| 通用 API（按量计费） | `https://open.bigmodel.cn/api/paas/v4` |

智谱的 Key 形如 `xxxxxxxx.xxxxxxxx`（**中间有一个点号**）。

**调用链（重点）**

```
用户提问 → 后端 _bridge_fill() 取该用户 is_active 的配置
        → 随 POST /fill-slots 的 body.llm 透传给 ai-bridge
        → ai-bridge 用 Agent.getApiKey 注入密钥（不写 process.env，避免并发串号）
```

**ai-bridge 里没有任何密钥配置项**，容器被拿到也拿不到 Key。用户没配模型时 `_bridge_fill` 返回 `None`，
静默走规则路径（不报错）——所以**没配模型系统照样能用**，只是长尾问法要人工补槽。

**改配置后不生效？** 检查：① 该套是不是「使用中」；② 后端 `AI_BRIDGE_ENABLED=true`；
③ ai-bridge 是否起了（`docker compose --profile ai up -d ai`）。

## 10. 怎么验证

**本机连真实库的方式**：postgres 跑在 docker 容器 `pg-medicine`，宿主机端口 **5433**，
不是 `.env` 默认的 5432。用环境变量覆盖即可：

```bash
export PG_HOST=127.0.0.1 PG_PORT=5433 PG_USER=postgres PG_PASSWORD=postgres PGCLIENTENCODING=UTF8
```

**两个必须知道的本机环境事实（踩过坑）**

1. **后端不能用 8000**。8000 被 **`ticketsys-api` 容器**占着（另一个项目，docker 容器，`kill` 不掉）。
   若服务端绑定 8000 失败，而前端仍代理到 8000，**所有 `/api` 请求会静默打到 ticketsys 上**，
   现象是「加载数据失败: Not Found」——看起来像代码坏了，其实是打错了服务。
   现在 `start.sh` 与 `vite.config.ts` 统一走 **18000**（`BACKEND_PORT` / `VITE_BACKEND_URL` 联动可改），
   且 `start.sh` 的 `free_port()` 检测到"端口被占但拿不到 PID"（=docker 容器）会**直接报错退出**，
   不再静默继续。
2. **`start.sh` 的 `PROJECT_ROOT` 必须动态解析**。本项目从 CRDB 复制而来，
   原脚本里硬编码了 `/mnt/d/Desktop/Project_Group/CRDB/drug_db_system`，
   结果在 CRDB_AI 目录里跑 `./start.sh`，启动的却是 **CRDB 的代码** ——
   改了代码完全不生效、新接口全 404。现已改为
   `PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`，复制到任何目录都对。

```bash
# 1) 语法
cd drug_db_system/backend && python3 -m py_compile app/services/ai_*.py app/routers/ai.py

# 2) 模块能导入（会连带校验路由注册）
python3 -c "import importlib; \
[importlib.import_module(m) for m in ['app.services.ai_agg','app.services.ai_render',\
'app.services.ai_executor','app.services.ai_clarify','app.routers.ai','app.main']]; print('ok')"

# 3) 端到端跑一句（会用真实库）
python3 -c "
from app.services.ai_slots import extract
from app.services.ai_clarify import build_card, to_plan
from app.services import ai_capabilities as caps
from app.services.ai_executor import execute
q='广东省2026年1-9月的实销情况和同比情况'
rs=extract(q,'dashenlin'); card=build_card(rs,'s')
plan=to_plan(card,{k:v['value'] for k,v in rs.slots.items() if v.get('value') not in (None,'',[])})
plan['capability']=rs.capability
print(caps.validate_plan(rs.capability,caps.apply_defaults(rs.capability,plan),'dashenlin') or '校验通过')
print(execute(plan,'dashenlin')['answer'])
"
# 期望：校验通过；回答含 盒数/金额/门店数 与同比

# 4) 前端
cd drug_db_system/frontend && npx vite build
```
