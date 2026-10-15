# CRDB（医药销售数据库）部署到阿里云 ECS 指南

- 服务器：`root@8.166.120.28`，项目目录 `/opt/CRDB`
- 域名：`crdb.neftoo.xyz`
- 方案：全 Docker（PostgreSQL 16 + FastAPI + Nginx），数据由本地 `pg-medicine` 用 `pg_dump` 迁移

> ⚠️ **端口策略：一律不抢别人的端口**
> 这台机器上 Ticketsys 和宝塔已经占用了 `80/443/5432/8000/8080` 等端口，所以 CRDB：
> - `postgres` / `api`：**不映射任何宿主机端口**（只在容器网络内，用 `docker exec` / `docker cp` 运维）
> - `web`：只映射一个冷门端口 **`18080`**（在 `.env` 的 `WEB_PORT` 改；冲突就换一个）
> - 公网访问复用 **Ticketsys 的 nginx**：把 `crdb-web` 接进它的 Docker 网络，
>   再往 `ticketsys-web:/etc/nginx/conf.d/` 放一个反代 server block（脚本自动完成）

---

## 一、阿里云控制台准备

### 1. 域名解析
`crdb.neftoo.xyz` 的 **A 记录**指向 `8.166.120.28`。

### 2. 安全组
入方向只需放行（Ticketsys 已开）：

| 协议 | 端口 | 说明 |
|---|---|---|
| TCP | 80 | HTTP / ACME 验证（Ticketsys nginx 在用） |
| TCP | 443 | HTTPS（Ticketsys nginx 在用） |
| TCP | 22 | SSH |

> 不要放行 5432 / 8000 / 18080。CRDB 的 postgres 和 api 本来就不映射宿主机端口，
> `18080` 只是调试用的直连入口，公网统一走 443（Ticketsys 反代）。

---

## 二、上传代码

```bash
# 本地 WSL
rm -rf CRDB/drug_db_system/frontend/node_modules CRDB/drug_db_system/frontend/dist
scp -r CRDB root@8.166.120.28:/opt/
```

服务器上应为：

```
/opt/CRDB/
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env                       ← 需要自己创建
├── deploy/
│   ├── export_db.sh           本地导出
│   ├── import_db.sh           服务器导入
│   ├── setup_nginx_vhost.sh   域名 HTTPS
│   └── pgdump/                备份包放这里
└── drug_db_system/
    ├── backend/
    └── frontend/
```

---

## 三、填写生产配置 `.env`

```bash
cd /opt/CRDB
cp .env.example .env
vim .env
```

必改：

```bash
DOMAIN=crdb.neftoo.xyz
POSTGRES_PASSWORD=<强密码>      # 别用默认
```

数据相关保持默认即可：

```bash
IMPORT_EXCEL=false   # 数据走 pg_dump 迁移，不从 xlsx 导入
INIT_DB=true         # 幂等建库/建表/建索引，不会覆盖已有数据
WEB_PORT=18080       # 避开 Ticketsys/宝塔占用的端口；仍冲突就换一个
```

> 密码必须在**首次启动 postgres 之前**定好，`initdb` 只跑一次，之后改 `.env` 不生效。

---

## 四、迁移本地数据（pg-medicine → 服务器）

### 1) 本地导出（WSL 执行）

```bash
cd <本地 CRDB 目录>
docker start pg-medicine          # 确保源库在跑
# 建议先停掉本地 start.sh，避免迁移期间有写入
bash deploy/export_db.sh
```

千万级数据用**目录格式 + 4 并发 + gzip 压缩**，产物：

```
deploy/pgdump/pgdump_<时间戳>.tgz
```

### 2) 上传到服务器（会提示输入密码）

```bash
scp deploy/pgdump/pgdump_*.tgz root@8.166.120.28:/opt/CRDB/deploy/pgdump/
```

### 3) 服务器导入

```bash
cd /opt/CRDB
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres
bash deploy/import_db.sh          # 自动取最新备份包
```

导入是**覆盖式**（`--clean --if-exists`），重复执行安全；结束后自动 `ANALYZE`。
千万行数据通常需要若干分钟，`-j 4` 并行恢复。

<details>
<summary>可选的导入加速（导入前跑，导入后还原）</summary>

```bash
docker exec crdb-postgres psql -U postgres -c "
  ALTER SYSTEM SET maintenance_work_mem = '2GB';
  ALTER SYSTEM SET max_wal_size = '8GB';
  ALTER SYSTEM SET synchronous_commit = off;
  ALTER SYSTEM SET checkpoint_timeout = '30min';"
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart postgres

# ---- 导入完成后 ----
docker exec crdb-postgres psql -U postgres -c "
  ALTER SYSTEM RESET maintenance_work_mem;
  ALTER SYSTEM RESET max_wal_size;
  ALTER SYSTEM RESET synchronous_commit;
  ALTER SYSTEM RESET checkpoint_timeout;"
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart postgres
```
</details>

---

## 五、启动应用

```bash
cd /opt/CRDB
bash deploy/deploy.sh
```

脚本会：

1. 构建并启动 `postgres` + `api` + `web`
2. `api` 容器内自动跑 `init_db.py`（幂等：建库 → 建表 IF NOT EXISTS → 建索引 → ANALYZE，**表非空不会导入 xlsx**）
3. 等健康检查通过
4. 自动调用 `setup_nginx_vhost.sh` 配域名 HTTPS

自检：

```bash
curl http://127.0.0.1:18080/health   # 应返回 {"status":"ok"}
```

完成后访问：https://crdb.neftoo.xyz/

### 域名 HTTPS 做了什么

`deploy/setup_nginx_vhost.sh`：

1. 找到 `ticketsys-web` 所在的 Docker 网络
2. `docker network connect <网络> crdb-web`
3. 用 Ticketsys 的 webroot（`/opt/Ticketsys/certbot/www`）申请 `crdb.neftoo.xyz` 证书
4. 生成 `deploy/crdb-vhost.conf` 并拷进 `ticketsys-web:/etc/nginx/conf.d/crdb.conf`
5. `nginx -t` + `nginx -s reload`

**不修改 Ticketsys 任何现有文件**。证书续期沿用 Ticketsys 已有的 `certbot renew` cron（同一个 `/etc/letsencrypt`，会一起续）。

> 若 `ticketsys-web` 容器被重建，这个 conf 会丢（未做挂载），重跑一次脚本即可。
> 想永久化：在 Ticketsys 的 `docker-compose.prod.yml` 的 `web.volumes` 里加一行
> `- /opt/CRDB/deploy/crdb-vhost.conf:/etc/nginx/conf.d/crdb.conf:ro`

---

## 六、日常运维

> 生产环境所有命令都要带两个 `-f`。可设别名：
> ```bash
> echo "alias crdb='docker compose -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc
> ```

| 操作 | 命令 |
|---|---|
| 重启全部 | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` |
| 更新代码后重建 | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` |
| 看后端日志 | `docker compose logs -f api` |
| 看前端日志 | `docker compose logs -f web` |
| 重新导入数据 | `bash deploy/import_db.sh` |
| 重配域名/续期后生效 | `bash deploy/setup_nginx_vhost.sh` |
| 手动续期证书 | `sudo certbot renew` |
| 数据库备份 | `docker exec crdb-postgres pg_dump -U postgres -Fc dashenlin > dashenlin.dump` |
| 停止 | `docker compose -f docker-compose.yml -f docker-compose.prod.yml down` |

持久化位置：

- PostgreSQL 数据：Docker 卷 `crdb_pgdata`
- 商品编码映射：`drug_db_system/backend/data/product_map.json`（挂载到宿主）
- 前端/后端代码：容器镜像内，更新代码需 `--build`

---

## 七、导出/下载已关闭

按钮保留、点击无反应：

| 位置 | 改动 |
|---|---|
| 前端 `src/api/client.ts` | `DOWNLOAD_DISABLED`（读 `VITE_DISABLE_DOWNLOAD`），`downloadBlob()` 直接 return |
| 前端 3 个 view + 3 个统计组件 | `handleExport()` 开头 `if (DOWNLOAD_DISABLED) return` |
| 后端 `app/main.py` | `block_export` 中间件，`DISABLE_EXPORT=true` 时含 `/export` 的接口返回 403 |

恢复：`VITE_DISABLE_DOWNLOAD=false` + `DISABLE_EXPORT=false`，然后
`docker compose build web && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d web`。

---

## 八、排错

- **`port is already allocated`**：`WEB_PORT`（默认 18080）被占用。
  `ss -tlnp | grep 18080` 看是谁，然后在 `.env` 里换一个 `WEB_PORT` 再 `up -d`。
  ⚠️ 别在 `docker-compose.prod.yml` 里写 `ports` 想覆盖——Compose 合并是**追加**，只会重复绑定。
- **502 Bad Gateway**：`docker logs crdb-api`（可能还在 `init_db.py`）；`docker logs crdb-web`。
- **证书申请失败**：确认 `crdb.neftoo.xyz` 已解析到 `8.166.120.28`，且 Ticketsys 的 80 端口在跑（ACME 走它的 webroot）。
- **反代 502 / `host not found in upstream "crdb-web"`**：`crdb-web` 没接进 Ticketsys 网络，重跑 `setup_nginx_vhost.sh`。
- **页面空白**：`docker compose build web` 看构建是否成功。
- **数据没进来**：`docker exec crdb-postgres psql -U postgres -d dashenlin -c "SELECT COUNT(*) FROM sales;"`

---

## 九、安全提醒

- 改掉 `.env` 的默认 `POSTGRES_PASSWORD`，别把 `.env` 提交到 Git。
- `18080` 只是调试直连入口，安全组不要放行；公网入口只有 Ticketsys 的 443。
- 已关闭导出下载；如需更严格，可在 `crdb-vhost.conf` 里对 `/api/*/export*` 加 `deny all;`。
