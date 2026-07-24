-- ============================================================
-- 三个数据库的建表 SQL（优化版：日期类型化）
--
-- 注意：实际建库建表导入由 backend/setup_db.py 自动完成
-- 此文件供参考、手动维护、DBA 审阅使用。
--
-- 优化点：
--   1. 日期字段从 TEXT 改为 DATE（支持范围查询、索引）
--   2. 主键用 BIGSERIAL（千万级数据不会溢出）
--   3. 索引见 indexes.sql
-- ============================================================

-- 建库（先连到默认 postgres 库）
CREATE DATABASE dashenlin ENCODING 'UTF8';
CREATE DATABASE gaoji     ENCODING 'UTF8';
CREATE DATABASE haiwang   ENCODING 'UTF8';

-- ------------------------------------------------------------
-- 1) 大参林 —— 连接到 dashenlin 库后执行
-- ------------------------------------------------------------
CREATE TABLE sales (
    id              BIGSERIAL PRIMARY KEY,
    "日期"         DATE,
    "月度"         DATE,
    "公司"         TEXT,
    "来源公司"     TEXT,
    "门店编码"     BIGINT,
    "门店名称"     TEXT,
    "门店详细名称" TEXT,
    "省份"         TEXT,
    "城市"         TEXT,
    "商品编码"     BIGINT,
    "商品名称"     TEXT,
    "规格"         TEXT,
    "单位"         TEXT,
    "数量"         INTEGER,
    "批号"         TEXT,
    "批准文号"     TEXT,
    "有效期至"     DATE,
    "营运区"       TEXT,
    "大区"         TEXT,
    "医院名字"     TEXT,
    "适应症"       TEXT,
    "销售价格"     NUMERIC(14,2),
    "生产厂家"     TEXT
);

-- ------------------------------------------------------------
-- 2) 高济 —— 连接到 gaoji 库后执行
-- ------------------------------------------------------------
CREATE TABLE sales (
    id              BIGSERIAL PRIMARY KEY,
    "业务日期"     DATE,
    "月度"         DATE,
    "企业名称"     TEXT,
    "城市"         TEXT,
    "门店编码"     TEXT,
    "门店名称"     TEXT,
    "商品编码"     BIGINT,
    "商品名称"     TEXT,
    "规格"         TEXT,
    "厂家名称"     TEXT,
    "单位"         TEXT,
    "生产批号"     TEXT,
    "生产日期"     BIGINT,
    "销售数量"     INTEGER,
    "供应商名称"   TEXT
);

-- ------------------------------------------------------------
-- 3) 海王 —— 连接到 haiwang 库后执行
-- ------------------------------------------------------------
CREATE TABLE sales (
    id              BIGSERIAL PRIMARY KEY,
    "类别"         BIGINT,
    "商品SAP编码"  BIGINT,
    "商品名称"     TEXT,
    "规格"         TEXT,
    "单位"         TEXT,
    "店号/区域ID"  TEXT,
    "店名/区域"    TEXT,
    "销量"         INTEGER,
    "过账日期"     DATE,
    "月度"         DATE,
    "合同价"       NUMERIC(14,2),
    "事业部名称"   TEXT,
    "事业部"       TEXT
);
