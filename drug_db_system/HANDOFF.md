# HANDOFF · 数字看板 / 账号体系 / AI 问数

> 给接手者的上下文。子系统：CRDB/drug_db_system（经营数字看板 · 账号体系 · AI 问数）。
> **AI 问数的深度交接在另两份文档**：`CRDB_AI/HANDOFF-AI.md`（完整）+ `修改日志.md`（时间线）。

## 1. 用户原始诉求
看板"整个界面千万级重绘"卡顿，要求优化并审查是否陷入"无意义的性能问题"。

## 2. 根因（两层）
- **A. 图表重建（已修）**：每次开抽屉 / 点查询 / 切指标，全部 echarts 容器 `dispose + init`，重走初始化与首帧绘制，并丢失状态/闪烁。
- **B. DOM 膨胀（已修）**：品种卡片最多 500 张、城市卡片最多 2000 张被一次性铺进 DOM，前端**无虚拟滚动**。这是比图表重绘更伤的真实卡顿源。

> 结论：A 是"局部有效但不完整"的优化，B 才是整页变轻的主因。两层都处理后才算整体优化到位。

## 3. 关键设计点
- **图表实例持久化**（`DashboardPanel.vue`）：`chartMap: Map<HTMLElement, ECharts>`，`getChart()` 仅在元素有尺寸时 `init`；`onOpened` 兜底补渲染（修首屏 0×0 空白）。切指标/单位只 `setOption`。
- **后端已同时返回盒数与金额**：`dashboard_summary` 一次性给 `boxes` 与 `amount`，前端切口径不重新请求。
- **请求去重/缓存**：`summaryCache`（按筛选参数缓存，存活会话内）+ `loadPromise`（并发去重），重复打开不重扫千万级大表。
- **卡片惰性展开**：`PRODUCT_INITIAL=60` / `CITY_INITIAL=100`，超出显示"展开全部"按钮；`showAllProducts`、`expandedCities` 控制；数据重载重置。
- **后端默认上限**：`dashboard_summary` 现 `province_top_n=100`、`city_top_n=500`、`product_top_n=500`（`le` 上限 2000，可显式上调）。`dashboard_breakdown_all` 自身默认 15/15/20，但前端走 summary 时会被覆盖为上述值。

## 4. 如何验证
1. `cd frontend && npx vite build` 通过（无报错）。
2. 起前后端，打开看板抽屉：
   - 切「盒数/金额」：趋势图、连锁/加盟饼图、连锁/加盟卡片、KPI、卡片网格同步换口径，且无图表闪烁/重建。
   - 「品种表现」Tab：默认 ~60 张卡片，点"展开全部品种"见剩余。
   - 「省市表现」Tab：展开某省，城市先 ~100 张，点"展开该省全部城市"见剩余。
   - 改筛选点「查询」：展开态被重置。

## 5. 风险 / 未尽事项
- 卡片网格仍是**真·全量渲染**（仅用 Top N 截断），非虚拟滚动。若某省城市真实 >100 或品种 >60，需手动点"展开"。若未来数据量再膨胀，建议引入虚拟滚动（如 `vue-virtual-scroller`）。
- `city_top_n` 默认 500：极大量级连锁若总城市 >500 会被截断，可按需上调（接口 `le=2000`）。
- 后端看板结果仍有 TTL 缓存（`TTLCache`），调参后注意缓存命中。

## 6. 相关文件（看板部分）
- `frontend/src/components/DashboardPanel.vue`
- `backend/app/routers/dashboard.py`
- 配套文档：`修改日志.md`（同目录）

## 7. 账号体系（2026-09-20 改造）

**规则（已在代码中落地）**：
- 申请账号 = 姓名 + 手机号 + **用户自设密码**；密码只存 bcrypt 哈希（`crdb_applications.password_hash`）。
- 审批通过 → 直接沿用该哈希建账号（`create_user_with_hash`），**后端不生成、接口不回显任何明文密码**，管理员全程看不到密码。
- 管理员**没有**设定/重置密码的能力；忘记密码的唯一处理是 `DELETE /api/auth/members/{uid}` 删除账号 → 成员用手机号重新申请并重设密码。
- 登录 = **手机号 + 密码**；按手机号查不到时回退按账号名查一次（保证未登记手机号的管理员 `admin` 仍可登录）。
- 成员自己仍可用 `/api/auth/change-password`（需原密码）改密。

**迁移注意**：改造前提交的待审申请没有 `password_hash`，审批会被拒绝并提示"请让申请人重新提交申请"——这是预期行为（旧申请无法凭空得到密码）。

**关键文件**：`backend/app/auth.py`、`backend/app/routers/auth.py`、`frontend/src/api/auth.ts`、`frontend/src/views/LoginView.vue`、`frontend/src/views/AdminView.vue`。

## 8. AI 问数（2026-10-15 补充）

> 深度上下文见 **`CRDB_AI/HANDOFF-AI.md`**（数据流 / 三个必须记住的坑 / 性能实测 / 模型配置 / 本机验证命令）。

**一句话**：用户说人话 → **纯规则**提取 8 个槽位 → 缺哪个就把整张「确认卡」列出来让人补 → 齐了才查 → 回答必带口径说明。
默认路径**不调用 LLM（0 token）**；只有「连能力都没识别出来」且该用户配了自己的模型时，才让 `ai-bridge` 兜底补槽位。

### 8.1 位置与职责（本次调整后的目录）

| 层 | 位置 | 说明 |
|---|---|---|
| 后端 AI 服务 | `backend/app/services/ai_*.py` | 提取 / 确认卡 / 执行调度 / 聚合 SQL / 渲染 / 埋点 / 模型配置 |
| 后端 AI 路由 | `backend/app/routers/ai.py` | `/api/{db}/ai/*`、`/api/ai-admin/*`、`/api/ai-models/*` |
| 能力目录（**单一真源**） | `backend/data/ai_capabilities.json` | Python 校验与 Node schema 都读它 |
| LLM 兜底桥梁 | `ai-bridge/`（与 `pi/` 同级） | Node + pi-agent-core，只补槽位不查数 |
| 上游 pi 源码 | `pi/`（与 `ai-bridge/` 同级，**只读**） | 仅用于构建打包产物，业务代码零改动 |
| 前端 | `frontend/src/components/Ai*.vue` | 对话面板 / 确认卡 / 模型管理弹窗 |

> `pi/` 与 `ai-bridge/` 同在 `drug_db_system/` 下，所以 `../pi/...`、`../ai-bridge` 这类相对路径都成立。

### 8.2 ai 服务：已改为**默认启动**，且不会因缺 pi 产物挡住部署

- `docker-compose.yml` 的 `ai` 服务已去掉 `profiles: ["ai"]`：`docker compose up -d` 会一并启动；
  后端 `AI_BRIDGE_ENABLED` 默认 **true**（`.env` 可覆盖成 false 退回纯规则模式）。
- `ai-bridge/Dockerfile` 有两条构建分支，**都一定构建成功**：
  1. 宿主机已生成 pi 打包产物（`pi/.artifacts/pi-packages`）→ 镜像带完整 L2；
  2. 没有产物 → 自动剥掉 package.json 里的 pi 依赖，构建「纯规则」镜像：容器照常启动，
     `/fill-slots` 返回 503，后端静默退回规则路径，**系统功能完全不受影响**。
- 想让 L2 真正可用（只需一次，pi 有更新时重做）：
  ```bash
  cd drug_db_system/pi
  npm install --ignore-scripts
  npm run pack:packages -- --out .artifacts/pi-packages
  node scripts/use-local-packages.mjs \
    --manifest .artifacts/pi-packages/manifest.json \
    --consumer ../ai-bridge \
    --package @earendil-works/pi-agent-core --package @earendil-works/pi-ai
  cd ../..  &&  docker compose up -d --build ai
  ```
- **自检**：`curl localhost:3000/health` 的 `pi_ready` 字段 —— 为 false 就说明镜像里没带 pi 产物，
  L2 不会生效（compose 的 `healthcheck` 也用它判断存活）。

### 8.3 改 AI 之前必须知道的三条

1. **pi 在 ai-bridge 里是「可选运行时依赖」**：`agent.ts` / `provider.ts` 的动态 import 说明符
   **刻意写成变量而非字符串字面量**，否则没装 pi 时 `tsc` 报 TS2307 会直接中断镜像构建。
   想改回字面量，先想清楚这条。
2. **查询闸门只拦 GET**：AI 的执行入口是 POST（`/api/{db}/ai/confirm`），
   所以 `routers/ai.py` 的 `_run_guarded()` 必须保留，否则 AI 是绕开限流的后门。
   （`main.py` 的 `_QUERY_GATE_HEAVY` 里曾有一个永不命中的 `/ai/` 段，已删。）
3. **日期必须按库内 `MAX(日期)` 裁剪**；**金额会静默偏低**（未配开票价的品类按 0 计入，
   `ai_render._caveats()` 会自动告警）—— 这两处逻辑别删。

### 8.4 2026-10-15 死代码清理记录

| 删除项 | 位置 | 依据 |
|---|---|---|
| `query_slot()` / `_SlotCtx` | `backend/app/services/query_gate.py` | 全仓零调用（导出侧用的是 `export_gate.export_slot`，未动） |
| `default_of()` | `backend/app/services/ai_capabilities.py` | 全仓零引用（取值实际走 `slot_def` / `apply_defaults`） |
| `_qi()` | `backend/app/services/ai_clarify.py` | 全仓零引用（热点地域 SQL 直接写 `pg_sql.Identifier`） |
| `stats()` | `backend/app/services/{stats_cache,ttl_cache}.py` | 无调用者、无端点暴露（后者仅被前者调用，连锁死） |
| 重复的 `DIMENSIONS` | `backend/app/routers/{stats_box,stats_store}.py` | 两处只定义不使用；唯一定义处是 `services/cube_layout.py` |
| 未使用的 typing 导入 | `ai_render.py`（`Any,Dict`）、`ai_model_config.py`（`Any`） | 逐名 grep 确认零使用 |
| `createRow` / `upsertByDate` / `fetchClickStats` / `deleteProductCode` / `downloadBlob` / `exportStoreCount` / `exportBoxCount` / `getDashboardOptions` / `getDashboardTrend` / `getDashboardStores` / `getDashboardStoresCached` | `frontend/src/api/client.ts` | 全仓（含文件内）零引用；`ClickStatsResponse` 类型与 `_dashboardStoresCache` 随之删除 |
| `FilterPanel.vue`、`MobileRows.vue` | `frontend/src/components/` | 零 import；**已在 git 中，`git checkout` 可恢复** |
| `DashboardTurnoverPanel.vue` | `frontend/src/components/` | 零 import；动销率实际由 `stats/TurnoverStats.vue` + `stats/TurnoverInventory.vue` 承担。⚠️ 该文件**未纳入 git**，删除后不可恢复 |

同时修掉一个**既有的**类型错误：`MobileTable.vue` 的 `touchDist()` 未判空
（`noUncheckedIndexedAccess` 下报 TS18048），它让 `npm run build` 的 type-check 一直失败 —— 现已修复。

### 8.5 改动后怎么验证

```bash
# 1) 后端：语法 + 模块导入（后者会连带校验路由注册）
cd drug_db_system/backend && python3 -m py_compile app/main.py app/services/*.py app/routers/*.py

# 2) 前端：类型检查 + 构建（必须 exit 0）
cd drug_db_system/frontend && npm run build

# 3) 部署编排（检查 compose 是否合法）
cd CRDB_AI && docker compose config --quiet
```

AI 链路的端到端跑法（连真实库问一句话）见 `CRDB_AI/HANDOFF-AI.md` 第 10 节。
