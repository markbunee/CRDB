<template>
  <el-drawer
    :model-value="props.modelValue"
    :size="'96%'"
    direction="rtl"
    :title="null"
    @update:model-value="emit('update:modelValue', $event)"
    @opened="onOpened"
  >
    <template #header>
      <div class="dash-header">
        <span class="dash-title">数字看板</span>
        <span class="dash-sub">经营总览 · {{ dbLabel }}</span>
        <div class="unit-switch">
          <span class="unit-label">指标</span>
          <el-radio-group v-model="metric" size="small">
            <el-radio-button value="boxes">盒数</el-radio-button>
            <el-radio-button value="amount">金额</el-radio-button>
          </el-radio-group>
          <template v-if="metric === 'amount'">
            <span class="unit-label">单位</span>
            <el-radio-group v-model="amountUnit" size="small">
              <el-radio-button value="yuan">元</el-radio-button>
              <el-radio-button value="wan">万</el-radio-button>
            </el-radio-group>
          </template>
        </div>
      </div>
    </template>

    <div v-loading="loading" class="dash-body">
      <!-- ============ 筛选区：仅时间（全国全品类总览，地域/品种通过点击卡片下钻） ============ -->
      <el-card class="filter-card" shadow="never">
        <el-form :inline="true" class="filter-form">
          <el-form-item label="月份">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="至"
              start-placeholder="开始月"
              end-placeholder="结束月"
            />
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :icon="Search" :loading="loading" @click="loadAll(true)">查询</el-button>
            <el-button :icon="RefreshLeft" :disabled="loading" @click="resetFilters">重置</el-button>
          </el-form-item>

          <el-form-item class="filter-hint">
            <span class="hint-text">全国全品类总览 · 仅按时间筛选；点击省份/品种卡片可下钻到单个城市与品类</span>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- ============ KPI ============ -->
      <el-row :gutter="12" class="kpi-row">
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">盒数(总)</div>
            <div class="kpi-value">{{ fmt(kpi?.boxes) }}</div>
            <div class="kpi-yoy" :class="yoyClass(kpi?.yoy_pct)">
              同比 {{ kpi?.yoy_pct == null ? '/' : (kpi.yoy_pct >= 0 ? '+' : '') + kpi.yoy_pct + '%' }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">销售金额</div>
            <div class="kpi-value">{{ formatAmount(kpi?.amount, amountUnit) }}</div>
            <div class="kpi-yoy" :class="yoyClass(kpi?.yoy_amount_pct)">
              同比 {{ kpi?.yoy_amount_pct == null ? '/' : (kpi.yoy_amount_pct >= 0 ? '+' : '') + kpi.yoy_amount_pct + '%' }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">门店数</div>
            <div class="kpi-value">{{ fmt(kpi?.stores) }}</div>
            <div class="kpi-yoy muted">去重门店</div>
          </el-card>
        </el-col>
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">覆盖省份</div>
            <div class="kpi-value">{{ fmt(kpi?.provinces) }}</div>
            <div class="kpi-yoy muted">省级行政区</div>
          </el-card>
        </el-col>
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">覆盖城市</div>
            <div class="kpi-value">{{ fmt(kpi?.cities) }}</div>
            <div class="kpi-yoy muted">地级市</div>
          </el-card>
        </el-col>
        <el-col :span="4" :sm="8" :xs="12">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">去年同期盒数</div>
            <div class="kpi-value">{{ fmt(kpi?.yoy_boxes) }}</div>
            <div class="kpi-yoy muted">用于同比基准</div>
          </el-card>
        </el-col>
      </el-row>
      <div class="kpi-extra">
        <el-icon><Document /></el-icon>
        <span>销售记录 {{ fmt(kpi?.rows) }} 条 · 金额单位：{{ amountUnitLabel(amountUnit) }}</span>
      </div>

      <!-- ============ 板块二：品种表现 / 省市表现 ============ -->
      <el-card class="section" shadow="never">
        <template #header>
          <div class="tab-header">
            <el-radio-group v-model="activeTab" size="small">
              <el-radio-button value="product">品种表现</el-radio-button>
              <el-radio-button value="region">省市表现</el-radio-button>
            </el-radio-group>
            <span class="region-tip">
              <template v-if="activeTab === 'region'">
                点击省份卡片展开该省全部城市；点击「查看品种明细」查看月度维度
              </template>
              <template v-else>点击品种卡片查看该品种月度明细</template>
            </span>
          </div>
        </template>

        <!-- 品种表现：卡片网格 -->
        <div v-if="activeTab === 'product'" class="region-grid">
          <div
            v-for="p in visibleProducts"
            :key="p.key"
            class="region-card product-card"
            @click="onSelectProduct({ code: String(p.key), name: displayName(p.name) })"
          >
            <div class="region-name" :title="displayName(p.name)">{{ displayName(p.name) }}</div>
            <div class="region-metric">
              <div class="metric-value">{{ itemLabel(p) }}</div>
              <div class="metric-label">{{ metric === 'amount' ? '金额' : '销量' }}</div>
            </div>
            <div class="region-stores">门店数 {{ fmt(p.stores) }}</div>
            <div class="region-link">
              <el-icon><View /></el-icon> 点击查看月度明细
            </div>
          </div>
          <div v-if="sortedProducts.length > PRODUCT_INITIAL" class="expand-more">
            <el-button link type="primary" size="small" @click="showAllProducts = true">
              展开全部品种（共 {{ sortedProducts.length }}）
            </el-button>
          </div>
        </div>

        <!-- 省市表现：省份卡片 + 展开城市 -->
        <div v-else-if="activeTab === 'region'" class="region-grid">
          <div
            v-for="p in sortedProvinces"
            :key="p.key"
            class="region-card province-card"
          >
            <div class="region-card-header">
              <div class="region-name clickable" @click="toggleProvince(String(p.name))">
                {{ p.name }}
              </div>
              <el-button
                v-if="citiesByProvince[p.name]?.length"
                link
                type="primary"
                size="small"
                @click="toggleProvince(String(p.name))"
              >
                {{ splitProvinces[p.name] ? '收起城市' : '拆分城市' }}
              </el-button>
            </div>
            <div class="region-metric">
              <div class="metric-value">{{ itemLabel(p) }}</div>
              <div class="metric-label">{{ metric === 'amount' ? '金额' : '销量' }}</div>
            </div>
            <div class="region-stores">门店数 {{ fmt(p.stores) }}</div>
            <div class="region-link" @click="onViewProducts(p)">
              <el-icon><View /></el-icon> 点击查看品种明细
            </div>

            <!-- 城市展开网格 -->
            <div v-if="splitProvinces[p.name]" class="city-grid">
              <div
                v-for="c in visibleCities(String(p.name))"
                :key="c.key"
                class="region-card city-card"
              >
                <div class="region-name">{{ c.name }}</div>
                <div class="region-metric">
                  <div class="metric-value">{{ itemLabel(c) }}</div>
                  <div class="metric-label">{{ metric === 'amount' ? '金额' : '销量' }}</div>
                </div>
                <div class="region-stores">门店数 {{ fmt(c.stores) }}</div>
                <div class="region-link" @click="onViewProducts(c)">
                  <el-icon><View /></el-icon> 点击查看品种明细
                </div>
              </div>
              <div
                v-if="(citiesByProvince[p.name] || []).length > CITY_INITIAL && !expandedCities[p.name]"
                class="expand-more"
              >
                <el-button link type="primary" size="small" @click="expandedCities[p.name] = true">
                  展开该省全部城市（共 {{ (citiesByProvince[p.name] || []).length }}）
                </el-button>
              </div>
            </div>
          </div>
        </div>

      </el-card>

    </div>

    <!-- 地域品种明细弹窗 -->
    <DashboardRegionDialog
      v-model="regionDialog.visible"
      :db-key="dbKey"
      :title="regionDialog.title"
      :filters="dialogFilters"
      :province="regionDialog.province"
      :city="regionDialog.city"
      :metric="metric"
      :amount-unit="amountUnit"
      @select-product="onSelectProduct"
    />
    <!-- 品种月度明细弹窗 -->
    <DashboardProductDialog
      v-model="productDialog.visible"
      :db-key="dbKey"
      :code="productDialog.code"
      :product-name="productDialog.name"
      :filters="dialogFilters"
      :amount-unit="amountUnit"
    />
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Search, RefreshLeft, Document, View } from '@element-plus/icons-vue'
import {
  getDashboardSummaryCached,
  peekDashboardSummary,
  type DashboardKpi,
  type BreakdownItem,
  type DashboardFilters,
  type DashboardSummary,
} from '@/api/client'
import { formatAmount, amountUnitLabel, type AmountUnit } from '@/utils/amount'
import DashboardRegionDialog from '@/components/DashboardRegionDialog.vue'
import DashboardProductDialog from '@/components/DashboardProductDialog.vue'

const props = defineProps<{ modelValue: boolean; dbKey: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const dbLabel = computed(() => {
  const map: Record<string, string> = {
    dashenlin: '大参林',
    gaoji: '高济',
    haiwang: '海王',
  }
  return map[props.dbKey] || props.dbKey
})

const loading = ref(false)
const kpi = ref<DashboardKpi | null>(null)
const provinceItems = ref<BreakdownItem[]>([])
const cityItems = ref<BreakdownItem[]>([])
const productItems = ref<BreakdownItem[]>([])

// 看板固定为「全国全品类」总览，地域/品种通过点击卡片下钻，顶部仅保留时间筛选
const dateRange = ref<[string, string] | null>(null)

// 金额显示单位：元 / 万
const amountUnit = ref<AmountUnit>('wan')
// 主指标：盒数 / 金额
const metric = ref<'boxes' | 'amount'>('boxes')
// 表现 Tab：品种表现 / 省市表现
const activeTab = ref<'product' | 'region'>('region')
// 省份 → 是否展开城市维度
const splitProvinces = ref<Record<string, boolean>>({})
// 卡片网格惰性展开：品种/城市先只渲染 Top N，避免一次性把数百~数千张卡片铺满 DOM
// （前端未做虚拟滚动，全量渲染才是真正卡顿主因，远甚于图表重绘）。
const PRODUCT_INITIAL = 60
const CITY_INITIAL = 100
const showAllProducts = ref(false)
const expandedCities = ref<Record<string, boolean>>({})
const visibleProducts = computed(() =>
  showAllProducts.value ? sortedProducts.value : sortedProducts.value.slice(0, PRODUCT_INITIAL),
)
function visibleCities(name: string): BreakdownItem[] {
  const list = citiesByProvince.value[name] || []
  return expandedCities.value[name] ? list : list.slice(0, CITY_INITIAL)
}
// 地域品种明细弹窗
const regionDialog = ref({ visible: false, title: '', province: '', city: '' })
// 品种月度明细弹窗
const productDialog = ref({ visible: false, code: '', name: '' })

function itemValue(it: BreakdownItem): number {
  return metric.value === 'amount' ? it.amount || 0 : it.boxes || 0
}
function itemLabel(it: BreakdownItem): string {
  return metric.value === 'amount' ? formatAmount(it.amount, amountUnit.value) : fmt(it.boxes)
}
function displayName(name: string): string {
  const s = String(name || '')
  return s.endsWith(')') && s.includes(' (') ? s.slice(0, s.lastIndexOf(' (')) : s
}

// 地域 / 品种分布：按当前指标排序
const sortedProvinces = computed(() =>
  [...provinceItems.value].sort((a, b) => itemValue(b) - itemValue(a)),
)
const sortedProducts = computed(() =>
  [...productItems.value].sort((a, b) => itemValue(b) - itemValue(a)),
)
const citiesByProvince = computed(() => {
  const map: Record<string, BreakdownItem[]> = {}
  for (const c of cityItems.value) {
    const p = c.province || '其他'
    if (!map[p]) map[p] = []
    map[p].push(c)
  }
  for (const p in map) {
    map[p]?.sort((a, b) => itemValue(b) - itemValue(a))
  }
  return map
})

// 明细弹窗使用的筛选条件（跟随主看板当前筛选）
const dialogFilters = computed<DashboardFilters>(() => buildParams())

function fmt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function yoyClass(pct: number | null | undefined): string {
  if (pct == null) return ''
  return pct >= 0 ? 'up' : 'down'
}

function onViewProducts(item: BreakdownItem) {
  const isCity = item.province != null
  regionDialog.value = {
    visible: true,
    title: `${item.name} · 品种表现`,
    province: isCity ? String(item.province) : String(item.name),
    city: isCity ? String(item.name) : '',
  }
}

function onSelectProduct(payload: { code: string; name: string }) {
  productDialog.value = { visible: true, code: payload.code, name: payload.name }
}

function toggleProvince(name: string) {
  splitProvinces.value[name] = !splitProvinces.value[name]
}

function buildParams(): DashboardFilters {
  return {
    date_from: dateRange.value?.[0] || '',
    date_to: dateRange.value?.[1] || '',
    // 看板固定为「全国全品类」总览，地域/品种通过卡片下钻，不在此处筛选
    provinces: '',
    cities: '',
    products: '',
    store_type: 'all',
  }
}

// 首次打开看板时，watch(modelValue) 与抽屉 @opened 可能几乎同时触发加载，
// 用「在途 Promise」去重：同一时刻只发一次请求，避免重复扫描千万级大表
// （后端看板缓存是「请求返回后」才写入，并发期间两次都会未命中而真跑一遍）。
let loadPromise: Promise<void> | null = null
let loadSeq = 0

async function loadAll(force = false) {
  // 用户显式点「查询 / 重置」时 force=true，保证筛选变更一定生效；
  // 自动触发（打开抽屉）时复用正在进行的加载，去重并发重复请求。
  if (loadPromise && !force) return loadPromise
  const mySeq = ++loadSeq
  const p = buildParams()
  const sumParams = { ...p, include_stores: false }

  // 命中会话缓存：同一时间区间的结果直接复用，不再查询；
  // 看板窗格保持「静态固定」显示（数据变化由 import/clear/映射保存时主动失效缓存）。
  const cached = peekDashboardSummary(props.dbKey, sumParams)
  if (cached) {
    // 多数情况：抽屉未销毁、kpi 仍在，直接保持静态显示即可；
    // 少数情况：因路由切换组件被重建（kpi 已丢），需把缓存回填到视图。
    if (!kpi.value) {
      applySummary(cached)
    }
    loadPromise = null
    loading.value = false
    return
  }

  loading.value = true
  loadPromise = (async () => {
    try {
      const sum = await getDashboardSummaryCached(props.dbKey, sumParams)
      if (mySeq !== loadSeq) return // 已被更新的请求取代，丢弃过期结果
      applySummary(sum)
    } catch (e: any) {
      console.error('加载看板数据失败', e)
    } finally {
      // 仅当自己仍是最新一次加载时才复位，避免被后发请求「提前解锁」
      if (mySeq === loadSeq) {
        loading.value = false
        loadPromise = null
      }
    }
  })()
  return loadPromise
}

/** 把一份看板汇总结果回填到视图状态。 */
function applySummary(sum: DashboardSummary) {
  // 后端已按「今年 1 月 ~ 最新月」计算并回传 applied_range，直接回填，无需二次请求
  if (!dateRange.value && sum.applied_range) {
    dateRange.value = [sum.applied_range.date_from, sum.applied_range.date_to]
  }
  kpi.value = sum.kpi
  provinceItems.value = sum.breakdown.province
  cityItems.value = sum.breakdown.city
  productItems.value = sum.breakdown.product
  // 数据刷新（筛选变更）后重置惰性展开，避免残留上一批数据的展开态
  showAllProducts.value = false
  expandedCities.value = {}
}

function resetFilters() {
  // 置空后由后端按「今年 1 月 ~ 最新月」默认区间计算；
  // 地域/品种本就不参与顶部筛选，仅保留时间。
  dateRange.value = null
  loadAll(true)
}

/**
 * 抽屉展开完成后确保数据已就绪：el-drawer 内容是懒挂载的，
 * 若首屏请求仍在进行，先等它结束（复用同一请求，不再重复发起）。
 */
async function onOpened() {
  if (loadPromise) await loadPromise
  if (!kpi.value) await loadAll()
}

// immediate: 组件随抽屉挂载时 modelValue 往往已是 true，普通 watch 不会触发；
// 加 immediate 保证「一挂载就取数」，不依赖「watch 检测到变化」这条不确定的路径，
// 避免进场空白、只能靠硬刷新恢复。
watch(
  () => props.modelValue,
  async (v) => {
    if (v) await loadAll()
  },
  { immediate: true },
)
</script>

<style scoped>
.dash-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.dash-title {
  font-size: 18px;
  font-weight: 700;
}
.dash-sub {
  color: #909399;
  font-size: 13px;
}
.dash-body {
  padding: 4px;
}
.filter-card {
  margin-bottom: 12px;
}
.filter-form :deep(.el-form-item) {
  margin-bottom: 8px;
}
.filter-hint {
  margin-left: auto;
}
.hint-text {
  font-size: 12px;
  color: #909399;
}
.kpi-row {
  margin-bottom: 12px;
}
.kpi-card {
  text-align: center;
  padding: 8px 4px;
}
.kpi-label {
  color: #909399;
  font-size: 12px;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
  margin: 4px 0;
}
.kpi-yoy {
  font-size: 12px;
}
.kpi-yoy.up {
  color: #67c23a;
}
.kpi-yoy.down {
  color: #f56c6c;
}
.kpi-yoy.muted {
  color: #c0c4cc;
}
.section {
  margin-bottom: 12px;
}
.section-title {
  font-weight: 600;
}
/* ---------- 金额单位切换 ---------- */
.unit-switch {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.unit-label {
  font-size: 12px;
  color: #909399;
}

/* ---------- KPI 补充 ---------- */
.kpi-extra {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
  margin: -8px 0 12px 4px;
}

/* ---------- 地域分布卡片 ---------- */
.region-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.region-tip {
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}
.region-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.region-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  padding: 16px;
  transition: box-shadow 0.2s, transform 0.2s;
}
.region-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}
.province-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.region-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.region-name {
  font-size: 16px;
  font-weight: 700;
  color: #303133;
}
.region-rank {
  font-size: 12px;
  color: #909399;
  font-weight: 500;
  margin-left: 4px;
}
.region-metric {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.expand-more {
  grid-column: 1 / -1;
  text-align: center;
  padding: 4px 0 8px;
}
.metric-value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
}
.metric-label {
  font-size: 12px;
  color: #909399;
}
.region-stores {
  font-size: 13px;
  color: #606266;
}
.region-link {
  font-size: 12px;
  color: #3b6bd6;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.region-link:hover {
  color: #5b8def;
}
.city-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px dashed #e4e7ed;
}
.city-card {
  background: #f8fafc;
  padding: 12px;
  gap: 6px;
}
.city-card .metric-value {
  font-size: 22px;
}
.city-card .region-name {
  font-size: 14px;
}

/* ---------- Tab 头 ---------- */
.tab-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.region-name.clickable {
  cursor: pointer;
}
.region-name.clickable:hover {
  color: #3b6bd6;
}
.product-card {
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ============================================================
   手机端（≤768px）：单列纵向排布
   ============================================================ */
@media (max-width: 768px) {
  .dash-body {
    padding: 0;
  }

  /* 头部：标题与指标切换换行，切换组占满一行 */
  .dash-header {
    flex-wrap: wrap;
    gap: 8px;
  }

  .unit-switch {
    margin-left: 0;
    width: 100%;
    flex-wrap: wrap;
    gap: 6px;
  }

  /* 筛选区：下拉框撑满、按钮并排等宽 */
  .filter-form :deep(.el-select) {
    width: 100% !important;
  }

  .filter-form :deep(.el-form-item__content) {
    width: 100%;
  }

  .filter-form :deep(.el-button) {
    flex: 1;
  }

  .kpi-card {
    padding: 10px 6px;
    margin-bottom: 10px;
  }

  .kpi-value {
    font-size: 18px;
  }

  /* 地域卡片单列 */
  .region-grid {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .region-card {
    padding: 12px;
  }

  /* 门店排名表：降低高度与字号。
     注意：`:deep()` 不能裸写在最外层（含 @media 内），需挂在作用域根类下 */
  .dash-body :deep(.el-table) {
    font-size: 12px;
    max-height: 320px;
  }

  .dash-body :deep(.el-card__body) {
    padding: 12px;
  }

  .dash-body :deep(.el-card__header) {
    padding: 10px 12px;
  }
}
</style>
