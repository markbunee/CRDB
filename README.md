# CRDB — 医药销售数据库管理系统

针对 **大参林 / 高济 / 海王** 三家连锁药店的销售数据，提供查询、维护、统计与导出能力；
后端针对 **千万级数据** 做了性能优化（连接池、COPY 导入、索引、全文搜索、游标分页等）。

## 三库说明

| db_key | 数据库名 | 说明 |
|--------|----------|------|
| `dashenlin` | `dashenlin` | 大参林医药集团 |
| `gaoji` | `gaoji` | 高济 |
| `haiwang` | `haiwang` | 海王（当前仅录入广州、深圳分部） |

源数据来自 CRDB 根目录下的三个 Excel，由 `setup_db.py` 通过 `COPY` 命令导入 PostgreSQL：

- `大参林医药集团数据.xlsx`
- `高济.xlsx`
- `海王数据.xlsx`

## 目录结构

```
CRDB/
├── 大参林医药集团数据.xlsx
├── 高济.xlsx
├── 海王数据.xlsx
└── drug_db_system/
    ├── backend/                         ← Python / FastAPI 后端
    │   ├── app/
    │   │   ├── main.py                  # FastAPI 入口（注册路由、托管前端）
    │   │   ├── config.py                # 配置（DB 参数、路径、DB_NAMES）
    │   │   ├── database.py              # 连接池 + get_conn
    │   │   ├── schemas.py               # Pydantic 请求/响应模型
    │   │   ├── dependencies.py          # db_key 校验等公共依赖
    │   │   ├── logger.py                # 日志（写入 backend/log.txt）
    │   │   ├── models/schema_def.py     # 三库表结构 + 索引/FTS 元数据
    │   │   ├── routers/                 # 路由层（按功能拆分，便于扩展）
    │   │   ├── services/                # 业务逻辑层
    │   │   │   ├── query_builder.py     #   查询构建（全文搜索 / 游标分页）
    │   │   │   └── data_import.py       #   Excel 导入（COPY）/ 建索引
    │   │   └── utils/serialization.py   #   JSON 序列化工具
    │   ├── main.py                      # 启动入口脚本（调用 uvicorn）
    │   ├── setup_db.py                  # 一键建库 + 建表 + 导入 + 建索引
    │   ├── create_indexes.py            # 单独补建索引
    │   ├── migrate_batch_text.py        # 批次文本迁移脚本
    │   ├── run.sh
    │   └── requirements.txt
    ├── frontend/                        ← Vue 3 + Vite 前端
    │   ├── src/
    │   │   ├── views/                   # 三库视图：Dashenlin / Gaoji / Haiwang View.vue
    │   │   ├── components/              # DataTable / FilterPanel / StatisticsPanel / stats/
    │   │   ├── api/  router/  utils/  assets/
    │   │   ├── App.vue  main.ts
    │   ├── package.json  vite.config.ts  tsconfig*.json
    │   └── run.sh
    ├── sql/                             ← schema.sql / indexes.sql 参考 SQL
    ├── start.sh                         ← 一键启动（WSL2：conda + nvm）
    └── 开发日志.md                       ← 数据预处理 / 运维记录
```

> 新增查询功能：在 `backend/app/routers/` 下新增路由文件，并在 `app/main.py` 注册即可。

## 技术栈

- **后端**：Python 3.12 · FastAPI · Uvicorn · psycopg2 · pandas · openpyxl · python-calamine
- **数据库**：PostgreSQL 16
- **前端**：Vue 3 · TypeScript · Vite · Element Plus · ECharts · vue-router

## 快速开始

### 方式一：一键启动（WSL2）

前提：PostgreSQL 已在 `localhost:5433` 运行；conda（`py312torch222`）与 nvm（Node `v22.23.1`）已安装。

```bash
cd /mnt/d/Desktop/Project_Group/CRDB/drug_db_system
./start.sh
```

启动后：

- 前端 → http://localhost:5173
- 后端 → http://localhost:8000 （API 文档 http://localhost:8000/docs ）
- `Ctrl + C` 停止全部服务

### 方式二：手动启动

1. 启动 PostgreSQL（Docker）

   ```bash
   docker run -d --name pg-medicine \
     -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \
     -p 5433:5432 -v pg-medicine-data:/var/lib/postgresql/data \
     postgres:16
   ```

2. 后端

   ```bash
   cd drug_db_system/backend
   pip install -r requirements.txt
   export PG_PORT=5433
   python setup_db.py        # 建库 + 建表 + COPY 导入 + 建索引（默认覆盖旧数据）
   python main.py            # 或 uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. 前端

   ```bash
   cd drug_db_system/frontend
   npm install
   npm run dev               # http://localhost:5173
   ```

   Vite 已配置 `/api` 代理到后端 `8000`，开发模式无需额外 CORS 配置。
   生产构建：`npm run build`，构建产物 `dist/` 由后端 `/` 直接托管。

## 配置（环境变量）

数据库连接与路径可通过环境变量覆盖：

```bash
export PG_HOST=localhost
export PG_PORT=5433                 # 默认 5432，本项目用 5433
export PG_USER=postgres
export PG_PASSWORD=postgres
export PG_ADMIN_DB=postgres
export PG_POOL_MIN=2                # 连接池下限
export PG_POOL_MAX=10               # 连接池上限
export EXCEL_DIR=/path/to/CRDB      # Excel 源数据目录（默认项目根 CRDB/）
```

## 前端功能

- **三库切换**：大参林 / 高济 / 海王 独立视图
- **数据表格**：分页 / 游标分页、全文搜索、按日期 / 商品编码 / 城市 / 省份筛选与排序
- **编辑维护**：单行查看、新增、编辑、删除；按日期覆盖导入（`upsert_by_date`）
- **统计面板**：汇总统计、点击统计（趋势）、门店数统计、盒数统计、
  门店能力分析（本期仅大参林·广州）
- **图表可视化**：基于 ECharts 的趋势与分布图
- **商品映射维护**：商品编码 → 品类名映射（`product-map`）

## API 一览

所有数据接口前缀 `/api/{db_key}`，`{db_key}` ∈ `{dashenlin, gaoji, haiwang}`。
交互式文档见 http://localhost:8000/docs （FastAPI Swagger）。

### 元数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dbs` | 三库字段、行数、统计 / 筛选字段映射 |
| GET | `/api/health` | 健康检查 |

### 数据 CRUD（`/api/{db_key}`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/rows` | 分页查询（`page`/`page_size`/`cursor`/`search`/`sort_by`/`sort_dir`/`date_from`/`date_to`/`product_codes`/`cities`/`provinces`） |
| GET | `/rows/{id}` | 获取单行（编辑预填） |
| POST | `/rows` | 新增 |
| PUT | `/rows/{id}` | 编辑 |
| DELETE | `/rows/{id}` | 删除 |
| POST | `/rows/upsert_by_date` | 按日期覆盖新增（先删同日期旧记录） |
| POST | `/rows/clear` | 按月份范围删除，或 `confirm=true` 清空全部 |

### 导入 / 导出（`/api/{db_key}`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/rows/import_excel` | 导入单个 Excel |
| POST | `/rows/import_excel_batch` | 批量导入 |
| GET | `/export` | JSON 导出（小数据量） |
| GET | `/export/stream` | CSV 流式导出（大数据量推荐，内存恒定） |
| GET | `/export/excel` | Excel 导出 |

### 统计（`/api/{db_key}`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stats/summary` | 汇总（`group_by` 按列分组） |
| GET | `/stats/click` | 点击统计（总记录数 + 按日期趋势） |
| GET | `/stats/distinct` | 某列去重值（筛选下拉用） |
| GET | `/stats/store_count` | 门店数统计 |
| GET | `/stats/store_count/export` | 门店数统计导出 |
| GET | `/stats/box_count` | 盒数统计 |
| GET | `/stats/box_count/export` | 盒数统计导出 |
| GET | `/stats/store_ability` | 门店能力分析（大参林·广州） |
| GET | `/stats/store_ability/export` | 门店能力分析导出 |
| GET | `/stats/store_ability/latest_range` | 门店能力分析最新区间 |
| GET | `/stats/store_ability/store_trend` | 门店能力趋势 |

### 其它

| 方法 | 路径 | 说明 |
|------|------|------|
| GET / POST / DELETE | `/api/product-map/{db_key}` | 商品编码 → 品类名映射（POST 新增 / DELETE 删单条） |
| POST | `/log/frontend` | 前端日志上报 |
| GET | `/log/frontend` | 前端日志查询 |

## 性能优化（千万级数据）

| 优化项 | 说明 |
|--------|------|
| 连接池 | 复用连接，减少每次新建开销 |
| COPY 导入 | 替代逐条 INSERT，导入速度提升 10~50 倍 |
| B-tree / 复合索引 | 日期、门店编码、商品编码、公司等高频过滤字段 |
| GIN 全文搜索 | 替代 `ILIKE '%...%'`，商品 / 门店名称搜索毫秒级 |
| 日期类型化 | TEXT → DATE，支持范围查询与索引 |
| 行数估算 | 用 `pg_class.reltuples` 替代 `COUNT(*)`，秒级返回 |
| 单行查询 | 编辑表单用 `/rows/{id}` 单独取，不再拉整页 |
| 游标分页 | `cursor` 替代深 OFFSET，翻页速度恒定 |
| 流式导出 | 服务端游标分批读，内存恒定 |

## 运维命令

```bash
cd drug_db_system/backend

# 一键初始化（建库 + 建表 + 导入 + 索引，覆盖旧数据）
python setup_db.py

# 只建库建表（不导入）
python setup_db.py --no-import

# 只补建索引（查询变慢时运行）
python setup_db.py --indexes
python create_indexes.py

# PostgreSQL 容器
docker start pg-medicine
docker stop  pg-medicine
```

## 数据预处理说明

数据导入前需在 Excel 中完成以下处理（详见 `drug_db_system/开发日志.md`）：

- 高济：去掉企业编码，销售数量转数值，新增月度列
- 大参林 / 高济：日期转文本型数字，新增月度列
- 海王：销量文本转数值（否则求和不一致），新增月度列，删除最后一行合计；当前仅录入广州、深圳分部
- 三家销售数量统一转为数值型，日期统一转为文本型数字

> 拉取原始数据时注意核对行数与总数，避免月份 / 总日期缺失。
