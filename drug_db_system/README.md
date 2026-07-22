# 医药销售数据库管理系统

基于 PostgreSQL + FastAPI + 原生 HTML 的三库医药销售数据查询与维护系统，
针对**千万级数据**做了性能优化。

## 目录结构

```
drug_db_system/
├── backend/
│   ├── app/                          ← 应用主包（按职责分模块）
│   │   ├── __init__.py
│   │   ├── main.py                   ← FastAPI 应用入口（创建 app、注册路由）
│   │   ├── config.py                 ← 配置（DB 连接参数、路径）
│   │   ├── database.py               ← 连接池 + get_conn 依赖
│   │   ├── schemas.py                ← Pydantic 请求/响应模型
│   │   ├── dependencies.py           ← 公共依赖（db_key 校验等）
│   │   ├── models/
│   │   │   └── schema_def.py         ← 三库表结构 + 索引/FTS 元数据
│   │   ├── routers/                  ← 路由模块（按功能拆分，便于扩展）
│   │   │   ├── meta.py               ←   /api/dbs 元数据
│   │   │   ├── rows.py               ←   /api/{db}/rows CRUD
│   │   │   ├── export.py             ←   /api/{db}/export 导出
│   │   │   └── stats.py              ←   /api/{db}/stats 统计聚合
│   │   ├── services/                 ← 业务逻辑层
│   │   │   ├── query_builder.py      ←   查询构建（全文搜索/游标分页）
│   │   │   └── data_import.py        ←   Excel 导入（COPY 命令）
│   │   └── utils/
│   │       └── serialization.py      ←   JSON 序列化工具
│   ├── scripts（顶层脚本）
│   │   ├── setup_db.py               ← 一键建库建表导入
│   │   └── create_indexes.py         ← 单独建索引
│   ├── main.py                       ← 启动入口
│   └── requirements.txt
├── frontend/
│   └── index.html                    ← 单页前端
└── sql/
    ├── schema.sql                    ← 建表 SQL（参考）
    └── indexes.sql                   ← 索引 SQL（参考）
```

> 后续新增查询功能，只需在 `app/routers/` 下新增路由文件，在 `app/main.py` 注册即可。

## 性能优化（千万级数据）

| 优化项 | 说明 |
|--------|------|
| **连接池** | 替代每次新建连接，复用连接减少开销 |
| **COPY 导入** | 替代逐条 INSERT，速度提升 10~50 倍 |
| **B-tree 索引** | 日期、门店编码、商品编码等常用过滤字段 |
| **复合索引** | (商品编码, 日期) 等高频查询模式 |
| **GIN 全文搜索** | 替代 ILIKE '%...%'，商品名称/门店名称搜索毫秒级 |
| **日期类型化** | TEXT → DATE，支持范围查询和索引 |
| **行数估算** | 用 pg_class.reltuples 替代 COUNT(*)，秒级返回 |
| **单行查询** | 编辑表单用 /rows/{id} 单独取，不再拉整页 |
| **流式导出** | 服务端游标分批读，内存恒定 |

## 快速开始

### 1. 启动 PostgreSQL（Docker）

```bash
docker run -d --name pg-medicine \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -p 5432:5432 \
  -v pg-medicine-data:/var/lib/postgresql/data \
  postgres:16
```

> 若 5432 被占用，改用 5433，并设置 `export PG_PORT=5433`

### 2. 安装依赖 + 初始化数据

```bash
cd backend
pip install -r requirements.txt
python setup_db.py        # 建库 + 建表 + COPY 导入 + 建索引
```

### 3. 启动后端

```bash
python main.py
# 或
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. 访问前端

浏览器打开 http://localhost:8000

## 配置

数据库连接参数可通过环境变量覆盖：

```bash
export PG_HOST=localhost
export PG_PORT=5432          # 或 5433
export PG_USER=postgres
export PG_PASSWORD=postgres
export PG_POOL_MAX=10        # 连接池大小
export EXCEL_DIR=/path/to    # Excel 源数据目录
```

## API 一览

### 元数据
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dbs` | 三库元数据与行数 |
| GET | `/api/health` | 健康检查 |

### 数据 CRUD
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/{db}/rows?page=&page_size=&search=&sort_by=&sort_dir=&cursor=` | 分页查询（支持游标分页） |
| GET | `/api/{db}/rows/{id}` | 获取单行 |
| POST | `/api/{db}/rows` | 新增 |
| PUT | `/api/{db}/rows/{id}` | 编辑 |
| DELETE | `/api/{db}/rows/{id}` | 删除 |

### 导出
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/{db}/export` | 导出全部（JSON，小数据量） |
| GET | `/api/{db}/export/stream` | 流式导出 CSV（大数据量推荐） |

### 统计
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/{db}/stats/summary?group_by=&date_from=&date_to=` | 汇总统计 |
| GET | `/api/{db}/stats/distinct?column=` | 去重值（筛选用） |

`{db}` 取值：`dashenlin` / `gaoji` / `haiwang`

## 运维命令

```bash
# 仅为已有数据补建索引（查询慢时运行）
python create_indexes.py

# 只建库建表不导入数据
python setup_db.py --no-import

# 重启 PostgreSQL 容器
docker start pg-medicine

# 停止
docker stop pg-medicine
```
