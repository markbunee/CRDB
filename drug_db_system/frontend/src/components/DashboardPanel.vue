<template>
  <el-drawer
    :model-value="props.visible"
    :size="'96%'"
    direction="rtl"
    :title="null"
    @update:model-value="emit('update:visible', $event)"
    @opened="onOpened"
  >
    <template #header>
      <div class="dash-header">
        <span class="dash-title">数字看板</span>
        <span class="dash-sub">经营总览 · {{ dbLabel }}</span>
        <el-tag v-if="options?.supports_store_type" type="info" size="small">含连锁/加盟</el-tag>
      </div>
    </template>

    <div v-loading="loading" class="dash-body">
      <!-- ============ 筛选区 ============ -->
      <el-card class="filter-card" shadow="never">
        <el-form :inline="true" class="filter-form">
          <el-form-item label="省份">
            <el-select
              v-model="provinces"
              multiple
              filterable
              clearable
              collapse-tags
              placeholder="全部省份"
              style="width: 240px"
            >
              <el-option v-for="p in options?.provinces || []" :key="p" :label="p" :value="p" />
            </el-select>
          </el-form-item>

          <el-form-item label="城市">
            <el-select
              v-model="cities"
              multiple
              filterable
              clearable
              collapse-tags
              placeholder="全部城市"
              style="width: 240px"
            >
              <el-option v-for="c in options?.cities || []" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>

          <el-form-item label="品种">
            <el-select
              v-model="products"
              multiple
              filterable
              clearable
              collapse-tags
              placeholder="全部品种"
              style="width: 320px"
            >
              <el-option
                v-for="p in options?.products || []"
                :key="p.code"
                :label="`${p.name} (${p.code})`"
                :value="p.code"
              />
            </el-select>
          </el-form-item>

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

          <el-form-item v-if="options?.supports_store_type" label="门店类型">
            <el-radio-group v-model="storeType">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="chain">连锁</el-radio-button>
              <el-radio-button value="franchise">加盟</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :icon="Search" @click="loadAll">查询</el-button>
            <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- ============ KPI ============ -->
      <el-row :gutter="12" class="kpi-row">
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">盒数(总)</div>
            <div class="kpi-value">{{ fmt(kpi?.boxes) }}</div>
            <div class="kpi-yoy" :class="yoyClass(kpi?.yoy_pct)">
              同比 {{ kpi?.yoy_pct == null ? '/' : (kpi.yoy_pct >= 0 ? '+' : '') + kpi.yoy_pct + '%' }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">环比笔数</div>
            <div class="kpi-value">{{ fmt(kpi?.rows) }}</div>
            <div class="kpi-yoy muted">销售记录条数</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">门店数</div>
            <div class="kpi-value">{{ fmt(kpi?.stores) }}</div>
            <div class="kpi-yoy muted">去重门店</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">覆盖省份</div>
            <div class="kpi-value">{{ fmt(kpi?.provinces) }}</div>
            <div class="kpi-yoy muted">省级行政区</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">覆盖城市</div>
            <div class="kpi-value">{{ fmt(kpi?.cities) }}</div>
            <div class="kpi-yoy muted">地级市</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover" class="kpi-card">
            <div class="kpi-label">去年同期盒数</div>
            <div class="kpi-value">{{ fmt(kpi?.yoy_boxes) }}</div>
            <div class="kpi-yoy muted">用于同比基准</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- ============ 板块一：销售趋势（同比） ============ -->
      <el-card class="section" shadow="never">
        <template #header><span class="section-title">销售趋势 · 同比增长</span></template>
        <div ref="trendChart" class="chart chart-lg"></div>
        <div v-if="!yoyAvailable && kpi" class="yoy-tip">
          去年同期该区间无销量（或数据未覆盖，数据自 {{ dataStartYear }} 年起），同比显示「/」
        </div>
      </el-card>

      <!-- ============ 板块二：地域分布 ============ -->
      <el-card class="section" shadow="never">
        <template #header><span class="section-title">地域分布（按盒数 Top15）</span></template>
        <el-row :gutter="16">
          <el-col :span="12">
            <div class="chart-subtitle">省份</div>
            <div ref="provinceChart" class="chart chart-md"></div>
          </el-col>
          <el-col :span="12">
            <div class="chart-subtitle">城市</div>
            <div ref="cityChart" class="chart chart-md"></div>
          </el-col>
        </el-row>
      </el-card>

      <!-- ============ 板块三：品种分布 ============ -->
      <el-card class="section" shadow="never">
        <template #header><span class="section-title">品种分布（商品编码 → 商品名，Top20）</span></template>
        <div ref="productChart" class="chart chart-lg"></div>
      </el-card>

      <!-- ============ 板块四：连锁 vs 加盟 ============ -->
      <el-card v-if="options?.supports_store_type" class="section" shadow="never">
        <template #header><span class="section-title">连锁 vs 加盟</span></template>
        <el-row :gutter="16">
          <el-col :span="14">
            <div class="split-cards">
              <div v-for="s in stores?.store_type_split || []" :key="s.st" class="split-card">
                <div class="split-tag" :class="{ franchise: s.st === '加盟' }">{{ s.st }}</div>
                <div class="split-metric">
                  <div class="m-label">盒数</div>
                  <div class="m-value">{{ fmt(s.boxes) }}</div>
                </div>
                <div class="split-metric">
                  <div class="m-label">门店数</div>
                  <div class="m-value">{{ fmt(s.stores) }}</div>
                </div>
              </div>
            </div>
          </el-col>
          <el-col :span="10">
            <div ref="storeTypeChart" class="chart chart-md"></div>
          </el-col>
        </el-row>
      </el-card>

      <!-- ============ 板块五：门店板块 ============ -->
      <el-card class="section" shadow="never">
        <template #header><span class="section-title">门店板块 · 排名 / 盒数 / 品种能力</span></template>
        <el-row :gutter="16">
          <el-col :span="14">
            <div class="chart-subtitle">门店排名 Top20（按盒数）</div>
            <el-table :data="topStores" size="small" max-height="420" border stripe>
              <el-table-column type="index" label="排名" width="60" align="center" />
              <el-table-column prop="store_name" label="门店名称" min-width="180" show-overflow-tooltip />
              <el-table-column label="类型" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.store_type === '加盟' ? 'warning' : 'success'">
                    {{ row.store_type }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="boxes" label="盒数" width="120" align="right" sortable>
                <template #default="{ row }">{{ fmt(row.boxes) }}</template>
              </el-table-column>
              <el-table-column label="占比" width="120" align="right">
                <template #default="{ row }">
                  {{ topStoreTotal ? (row.boxes / topStoreTotal * 100).toFixed(2) + '%' : '—' }}
                </template>
              </el-table-column>
            </el-table>
          </el-col>
          <el-col :span="10">
            <div class="chart-subtitle">品种能力 Top15（门店最畅销品种）</div>
            <div ref="storeProductChart" class="chart chart-md"></div>
          </el-col>
        </el-row>
      </el-card>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { Search, RefreshLeft } from '@element-plus/icons-vue'
import {
  getDashboardSummary,
  type DashboardOption,
  type DashboardKpi,
  type DashboardTrend,
  type BreakdownItem,
  type DashboardStores,
  type DashboardFilters,
} from '@/api/client'

const props = defineProps<{ visible: boolean; dbKey: string }>()
const emit = defineEmits<{ 'update:visible': [boolean] }>()

const dbLabel = computed(() => {
  const map: Record<string, string> = {
    dashenlin: '大参林',
    gaoji: '高济',
    haiwang: '海王',
  }
  return map[props.dbKey] || props.dbKey
})

const loading = ref(false)
const options = ref<DashboardOption | null>(null)
const kpi = ref<DashboardKpi | null>(null)
const trend = ref<DashboardTrend | null>(null)
const provinceItems = ref<BreakdownItem[]>([])
const cityItems = ref<BreakdownItem[]>([])
const productItems = ref<BreakdownItem[]>([])
const stores = ref<DashboardStores | null>(null)

const provinces = ref<string[]>([])
const cities = ref<string[]>([])
const products = ref<string[]>([])
const dateRange = ref<[string, string] | null>(null)
const storeType = ref<'all' | 'chain' | 'franchise'>('all')

const topStores = computed(() => stores.value?.top_stores || [])
// 占比分母：优先用 KPI 的门店总盒数（与 Top 门店同口径：相同日期/地域/品种/门店类型过滤），
// 更真实反映「单店贡献度」；KPI 未加载时退化为 Top20 之和。
const topStoreTotal = computed(() => {
  const total = kpi.value?.boxes
  if (total != null) return Number(total)
  return topStores.value.reduce((s, r) => s + (Number(r.boxes) || 0), 0)
})

// 同比是否有值：基期（去年同期）为 0 时后端返回 null，前端显示「/」
const yoyAvailable = computed(() => kpi.value?.yoy_pct != null)
// 数据起始年份（用于提示文案），取自 options 的月范围下限
const dataStartYear = computed(() => {
  const m = options.value?.month_range?.min
  return m ? String(m).slice(0, 4) : ''
})

// ---------- echarts ----------
const trendChart = ref<HTMLElement | null>(null)
const provinceChart = ref<HTMLElement | null>(null)
const cityChart = ref<HTMLElement | null>(null)
const productChart = ref<HTMLElement | null>(null)
const storeTypeChart = ref<HTMLElement | null>(null)
const storeProductChart = ref<HTMLElement | null>(null)
const charts: echarts.ECharts[] = []

function makeChart(refEl: typeof ref<HTMLElement | null>) {
  const el = refEl.value
  if (!el) return null
  const c = echarts.init(el)
  charts.push(c)
  return c
}

function disposeCharts() {
  charts.forEach((c) => c.dispose())
  charts.length = 0
}

function fmt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function yoyClass(pct: number | null): string {
  if (pct == null) return ''
  return pct >= 0 ? 'up' : 'down'
}

function buildParams(): DashboardFilters {
  return {
    date_from: dateRange.value?.[0] || '',
    date_to: dateRange.value?.[1] || '',
    provinces: provinces.value.join(','),
    cities: cities.value.join(','),
    products: products.value.join(','),
    store_type: storeType.value,
  }
}

/**
 * 默认日期区间：今年 1 月 1 日 ~ 数据最新月份。
 * 若数据起始月份晚于今年 1 月（如数据只到去年），退化为数据起始月，避免首屏区间为空。
 */
function defaultRange(
  mr?: { min: string | null; max: string | null } | null,
): [string, string] | null {
  const max = mr?.max || ''
  if (!max) return null
  const min = mr?.min || ''
  const from = `${new Date().getFullYear()}-01-01`
  const lower = min && min.slice(0, 7) > from.slice(0, 7) ? min : from
  return [lower, max]
}

async function loadAll() {
  loading.value = true
  try {
    const p = buildParams()
    // 一次请求拿回全部看板数据（含 options）：
    // 后端 GROUPING SETS 单 SQL 聚合三维度 + 线程池并发各板块，HTTP 往返从 2 次降到 1 次。
    const sum = await getDashboardSummary(props.dbKey, p)
    options.value = sum.options
    // 首屏未指定区间时：默认「今年 1 月 ~ 最新月」，并用该区间重取一次，
    // 保证顶部区间与首屏数字口径一致（后端带 TTL 缓存，第二次请求几乎无成本）
    let result = sum
    const mr = sum.options?.month_range
    if (!dateRange.value && mr) {
      const def = defaultRange(mr)
      if (def) {
        dateRange.value = def
        result = await getDashboardSummary(props.dbKey, buildParams())
        options.value = result.options
      }
    }
    kpi.value = result.kpi
    trend.value = result.trend
    provinceItems.value = result.breakdown.province
    cityItems.value = result.breakdown.city
    productItems.value = result.breakdown.product
    stores.value = result.stores
    await nextTick()
    renderCharts()
  } catch (e: any) {
    console.error('加载看板数据失败', e)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  provinces.value = []
  cities.value = []
  products.value = []
  storeType.value = 'all'
  // 重置回到默认口径：今年 1 月 ~ 数据最新月
  dateRange.value = defaultRange(options.value?.month_range)
  loadAll()
}

/**
 * 抽屉展开完成后再补一次渲染：el-drawer 的内容是懒挂载的，
 * 仅靠 visible 变化时的 nextTick 可能拿不到图表容器，导致首屏图表空白。
 */
async function onOpened() {
  if (!kpi.value) await loadAll()
  else renderCharts()
}

// ---------- 图表渲染 ----------
function baseGrid() {
  return { left: 8, right: 16, top: 30, bottom: 8, containLabel: true }
}

function renderTrend() {
  const c = makeChart(trendChart)
  if (!c || !trend.value) return
  const t = trend.value
  c.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['今年', '去年同期', '同比增长%'] },
    grid: { ...baseGrid(), bottom: 24 },
    xAxis: { type: 'category', data: t.months, axisLabel: { rotate: 45 } },
    yAxis: [
      { type: 'value', name: '盒数' },
      { type: 'value', name: '同比%', axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      { name: '今年', type: 'line', smooth: true, data: t.current, itemStyle: { color: '#409EFF' } },
      { name: '去年同期', type: 'line', smooth: true, data: t.previous, itemStyle: { color: '#C0C4CC' }, lineStyle: { type: 'dashed' } },
      {
        name: '同比增长%',
        type: 'bar',
        yAxisIndex: 1,
        data: t.growth,
        itemStyle: { color: '#67C23A' },
      },
    ],
  })
}

function renderHorizontalBar(elRef: typeof ref<HTMLElement | null>, items: BreakdownItem[], color: string) {
  const c = makeChart(elRef)
  if (!c || !items.length) return
  const sorted = [...items].sort((a, b) => a.boxes - b.boxes)
  c.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: baseGrid(),
    xAxis: { type: 'value', name: '盒数' },
    yAxis: {
      type: 'category',
      data: sorted.map((i) => i.name),
      axisLabel: { width: 160, overflow: 'truncate' },
    },
    series: [
      { type: 'bar', data: sorted.map((i) => i.boxes), itemStyle: { color }, label: { show: true, position: 'right', formatter: (p: any) => Number(p.value).toLocaleString('zh-CN') } },
    ],
  })
}

function renderStoreTypePie() {
  const c = makeChart(storeTypeChart)
  if (!c || !stores.value) return
  const data = (stores.value.store_type_split || []).map((s) => ({
    name: s.st,
    value: Number(s.boxes),
  }))
  c.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0 },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        data,
        label: { formatter: '{b}\n{d}%' },
      },
    ],
  })
}

function renderCharts() {
  disposeCharts()
  renderTrend()
  renderHorizontalBar(provinceChart, provinceItems.value, '#409EFF')
  renderHorizontalBar(cityChart, cityItems.value, '#E6A23C')
  renderHorizontalBar(productChart, productItems.value, '#909399')
  renderStoreTypePie()
  renderHorizontalBar(storeProductChart, (stores.value?.top_products || []).map((p) => ({
    key: p.code,
    name: p.name,
    boxes: p.boxes,
    avg_store: 0,
    stores: 0,
  })), '#67C23A')
}

function onResize() {
  charts.forEach((c) => c.resize())
}

onMounted(() => {
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  disposeCharts()
})

watch(
  () => props.visible,
  async (v) => {
    if (v) await loadAll()
  },
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
.chart {
  width: 100%;
}
.chart-lg {
  height: 340px;
}
.yoy-tip {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  background: #f4f4f5;
  border-radius: 4px;
  padding: 6px 10px;
}
.chart-md {
  height: 360px;
}
.chart-subtitle {
  font-size: 13px;
  color: #606266;
  margin-bottom: 6px;
}
.split-cards {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.split-card {
  flex: 1;
  min-width: 200px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 20px;
}
.split-tag {
  font-size: 16px;
  font-weight: 700;
  color: #409eff;
  padding: 6px 12px;
  border-radius: 6px;
  background: #ecf5ff;
}
.split-tag.franchise {
  color: #e6a23c;
  background: #fdf6ec;
}
.split-metric .m-label {
  font-size: 12px;
  color: #909399;
}
.split-metric .m-value {
  font-size: 20px;
  font-weight: 700;
}
</style>
