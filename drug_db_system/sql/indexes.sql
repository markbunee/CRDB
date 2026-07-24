-- ============================================================
-- 索引定义（优化千万级数据查询）
--
-- 策略：
--   1. 常用过滤字段 → B-tree 索引
--   2. 高频复合查询模式 → 复合索引
--   3. 模糊搜索列 → GIN 全文搜索索引（替代 ILIKE '%...%'）
--
-- 重要：建议在数据导入完成后再建索引（CREATE INDEX 会全表扫描）
--       python setup_db.py 已自动按此顺序执行
-- ============================================================

-- ============================================================
-- 大参林
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_dashenlin_date         ON dashenlin.sales ("日期");
CREATE INDEX IF NOT EXISTS idx_dashenlin_month        ON dashenlin.sales ("月度");
CREATE INDEX IF NOT EXISTS idx_dashenlin_store_code   ON dashenlin.sales ("门店编码");
CREATE INDEX IF NOT EXISTS idx_dashenlin_prod_code    ON dashenlin.sales ("商品编码");
CREATE INDEX IF NOT EXISTS idx_dashenlin_province     ON dashenlin.sales ("省份");
CREATE INDEX IF NOT EXISTS idx_dashenlin_manufacturer ON dashenlin.sales ("生产厂家");
-- 复合索引
CREATE INDEX IF NOT EXISTS idx_dashenlin_prod_date    ON dashenlin.sales ("商品编码", "日期");
CREATE INDEX IF NOT EXISTS idx_dashenlin_store_date   ON dashenlin.sales ("门店编码", "日期");
-- 全文搜索（GIN）
CREATE INDEX IF NOT EXISTS idx_dashenlin_fts_商品名称 ON dashenlin.sales
    USING GIN (to_tsvector('simple', coalesce("商品名称", '')));
CREATE INDEX IF NOT EXISTS idx_dashenlin_fts_门店名称 ON dashenlin.sales
    USING GIN (to_tsvector('simple', coalesce("门店名称", '')));
CREATE INDEX IF NOT EXISTS idx_dashenlin_fts_生产厂家 ON dashenlin.sales
    USING GIN (to_tsvector('simple', coalesce("生产厂家", '')));

-- ============================================================
-- 高济
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_gaoji_date         ON gaoji.sales ("业务日期");
CREATE INDEX IF NOT EXISTS idx_gaoji_month        ON gaoji.sales ("月度");
CREATE INDEX IF NOT EXISTS idx_gaoji_store_code   ON gaoji.sales ("门店编码");
CREATE INDEX IF NOT EXISTS idx_gaoji_prod_code    ON gaoji.sales ("商品编码");
CREATE INDEX IF NOT EXISTS idx_gaoji_manufacturer ON gaoji.sales ("厂家名称");
CREATE INDEX IF NOT EXISTS idx_gaoji_prod_date    ON gaoji.sales ("商品编码", "业务日期");
CREATE INDEX IF NOT EXISTS idx_gaoji_city_date    ON gaoji.sales ("城市", "业务日期");
CREATE INDEX IF NOT EXISTS idx_gaoji_month_city   ON gaoji.sales ("月度", "城市");
-- 全文搜索
CREATE INDEX IF NOT EXISTS idx_gaoji_fts_商品名称 ON gaoji.sales
    USING GIN (to_tsvector('simple', coalesce("商品名称", '')));
CREATE INDEX IF NOT EXISTS idx_gaoji_fts_门店名称 ON gaoji.sales
    USING GIN (to_tsvector('simple', coalesce("门店名称", '')));
CREATE INDEX IF NOT EXISTS idx_gaoji_fts_厂家名称 ON gaoji.sales
    USING GIN (to_tsvector('simple', coalesce("厂家名称", '')));

-- ============================================================
-- 海王
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_haiwang_date       ON haiwang.sales ("过账日期");
CREATE INDEX IF NOT EXISTS idx_haiwang_month      ON haiwang.sales ("月度");
CREATE INDEX IF NOT EXISTS idx_haiwang_store      ON haiwang.sales ("店号/区域ID");
CREATE INDEX IF NOT EXISTS idx_haiwang_prod_code  ON haiwang.sales ("商品SAP编码");
CREATE INDEX IF NOT EXISTS idx_haiwang_dept       ON haiwang.sales ("事业部");
CREATE INDEX IF NOT EXISTS idx_haiwang_prod_date  ON haiwang.sales ("商品SAP编码", "过账日期");
-- 全文搜索
CREATE INDEX IF NOT EXISTS idx_haiwang_fts_商品名称 ON haiwang.sales
    USING GIN (to_tsvector('simple', coalesce("商品名称", '')));
CREATE INDEX IF NOT EXISTS idx_haiwang_fts_店名区域 ON haiwang.sales
    USING GIN (to_tsvector('simple', coalesce("店名/区域", '')));

-- ============================================================
-- 更新统计信息（让查询优化器选对执行计划）
-- ============================================================
ANALYZE dashenlin.sales;
ANALYZE gaoji.sales;
ANALYZE haiwang.sales;
