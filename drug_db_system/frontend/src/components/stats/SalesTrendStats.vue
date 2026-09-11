<template>
  <div class="sales-trend-stats">
    <!-- 维度切换 -->
    <div class="dimension-tabs">
      <div
        v-for="d in dimensions"
        :key="d.key"
        class="dim-tab"
        :class="{ active: dimension === d.key }"
        @click="switchDimension(d.key)"
      >
        <el-icon class="dim-icon"><component :is="d.icon" /></el-icon>
        <div class="dim-text">
          <div class="dim-name">{{ d.name }}</div>
          <div class="dim-desc">{{ d.desc }}</div>
        </div>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-section">
      <div class="filter-grid">
        <div class="filter-group">
          <label class="filter-label">日期范围</label>
          <div class="filter-inline">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              size="default"
              style="width: 280px"
            />
            <el-checkbox v-model="mergeMonths" class="merge-checkbox" @change="onMergeChange">
              合并为单表
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">地域（{{ hasProvince ? '城市/省份 二选一' : (dbConfig?.region_label || '城市') }}）</label>
          <div class="filter-inline">
            <el-radio-group
              v-model="regionLevel"
              size="small"
              class="region-switch"
              @change="onRegionChange"
            >
              <el-radio-button label="city">{{ dbConfig?.region_label || '城市' }}</el-radio-button>
              <el-radio-button v-if="hasProvince" label="province">省份</el-radio-button>
            </el-radio-group>
            <el-input
              v-if="regionLevel === 'city'"
              v-model="cities"
              placeholder="广州市,佛山市,汕头市"
              clearable
              size="default"
              style="width: 220px"
              @keyup.enter="handleQuery"
            />
            <el-input
              v-else
              v-model="provinces"
              placeholder="广东省,广西壮族自治区,湖南省"
              clearable
              size="default"
              style="width: 220px"
              @keyup.enter="handleQuery"
            />
            <el-checkbox v-model="mergeCities" class="merge-checkbox" @change="onMergeChange">
              合并城市
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">品类（商品编码）</label>
          <div class="filter-inline">
            <el-input
              v-model="products"
              placeholder="1058746,1086127,1091138"
              clearable
              size="default"
              style="width: 280px"
              @keyup.enter="handleQuery"
            />
            <el-checkbox v-model="mergeProducts" class="merge-checkbox" @change="onMergeChange">
              合并品类
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group filter-actions">
          <div class="map-toggle">
            <el-switch v-model="mapNames" size="small" />
            <span class="map-toggle-label">品类映射</span>
            <el-button link type="primary" size="small" @click="mapManagerVisible = true">
              管理
            </el-button>
          </div>
          <el-button type="primary" :loading="loading" @click="handleQuery">
            <el-icon><Search /></el-icon> 生成图表
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>{{ hintText }}</span>
      </div>
    </div>

    <!-- 图表结果区 -->
    <div class="results-section" v-loading="loading" element-loading-text="正在生成趋势图表...">
      <template v-if="chartDataList.length > 0">
        <div class="results-summary">
          <span class="summary-badge">
            <el-icon><TrendCharts /></el-icon>
            共 {{ chartDataList.length }} 张图表 · 单位：盒
          </span>
        </div>
        <div class="charts-scroll-area">
          <div
            v-for="(chart, idx) in chartDataList"
            :key="idx"
            class="chart-card"
            :style="{ animationDelay: idx * 0.12 + 's' }"
          >
            <div class="chart-header">
              <span class="chart-title">
                <el-icon class="chart-title-icon"><DataLine /></el-icon>
                {{ chart.title }}
              </span>
              <span class="chart-meta">{{ chart.meta }}</span>
            </div>
            <div class="chart-body">
              <div :ref="el => setChartRef(el, idx)" class="chart-canvas"></div>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="!loading" class="empty-state">
        <div class="empty-icon-wrapper">
          <el-icon class="empty-icon"><TrendCharts /></el-icon>
        </div>
        <p class="empty-title">销售趋势可视化</p>
        <p class="empty-desc">选择维度和筛选条件，点击「生成图表」查看趋势分析</p>
      </div>
    </div>

    <!-- 品类映射管理弹窗 -->
    <ProductMapManager v-model="mapManagerVisible" :db-key="dbKey" @saved="onMapSaved" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  RefreshRight,
  InfoFilled,
  TrendCharts,
  DataLine,
  Location,
  Goods,
  Calendar,
} from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import {
  fetchBoxCount,
  type BoxCountTable,
  type StoreCountDimension,
  type StoreCountRegion,
  type DbMeta,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'
import { getDbConfig } from '@/utils/dbConfig'
import { loadMapPref, saveMapPref } from '@/utils/mapPref'
import ProductMapManager from './ProductMapManager.vue'

const props = defineProps<{
  dbKey: string
}>()

const { start: defaultStart, end: defaultEnd } = getCurrentMonthRange()

const dimensions = [
  { key: 'city' as StoreCountDimension, name: '城市维度', desc: '折线图 · 品类趋势按月', icon: Location },
  { key: 'product' as StoreCountDimension, name: '品类维度', desc: '折线图 · 地域趋势按月', icon: Goods },
  { key: 'time' as StoreCountDimension, name: '时间维度', desc: '柱状图 · 月度销售对比', icon: Calendar },
]

const dimension = ref<StoreCountDimension>('city')
const regionLevel = ref<StoreCountRegion>('city')
const dateRange = ref<[string, string]>([defaultStart, defaultEnd])
const cities = ref('')
const provinces = ref('')
const products = ref('')
const mergeMonths = ref(false)
const mergeCities = ref(false)
const mergeProducts = ref(false)

const loading = ref(false)
const tables = ref<BoxCountTable[]>([])

// ---------- 品类映射（四个统计页共用同一份开关偏好） ----------
const mapNames = ref(loadMapPref())
const mapManagerVisible = ref(false)

// 切换开关：持久化偏好；已有图表时自动按新口径重查
watch(mapNames, (v) => {
  saveMapPref(v)
  if (chartDataList.value.length > 0) handleQuery()
})

function onMapSaved() {
  if (chartDataList.value.length > 0) handleQuery()
}

// ---------- 图表实例管理 ----------
const chartInstances: echarts.ECharts[] = []
const chartRefs: (HTMLElement | null)[] = []

function setChartRef(el: any, idx: number) {
  chartRefs[idx] = el as HTMLElement | null
}

function disposeAllCharts() {
  chartInstances.forEach(c => {
    try { c.dispose() } catch (_) { /* noop */ }
  })
  chartInstances.length = 0
}

onUnmounted(() => {
  disposeAllCharts()
  window.removeEventListener('resize', handleResize)
})

function handleResize() {
  chartInstances.forEach(c => {
    try { c.resize() } catch (_) { /* noop */ }
  })
}
window.addEventListener('resize', handleResize)

// ---------- 图表数据结构 ----------
interface ChartData {
  title: string
  meta: string
  type: 'line' | 'bar'
  categories: string[]
  series: { name: string; data: number[] }[]
}

const chartDataList = ref<ChartData[]>([])

// ---------- 计算属性 ----------
const regionLabel = computed(() =>
  regionLevel.value === 'province' && hasProvince.value ? '省份' : (dbConfig.value?.region_label || '城市'),
)

const dbConfig = ref<DbMeta | null>(null)
const hasProvince = computed(() => dbConfig.value?.region_levels?.includes('province') ?? true)

onMounted(async () => {
  dbConfig.value = await getDbConfig(props.dbKey)
})

const hintText = computed(() => {
  const d = dimension.value
  const mergeHints: string[] = []
  if (mergeMonths.value) mergeHints.push('时间')
  if (mergeCities.value) mergeHints.push(regionLabel.value)
  if (mergeProducts.value) mergeHints.push('品类')
  const mergeStr = mergeHints.length ? `（已合并：${mergeHints.join('、')}）` : ''
  if (d === 'city') {
    return `城市维度：折线图展示各品类销售盒数按月变化趋势${mergeStr}`
  }
  if (d === 'product') {
    return `品类维度：折线图展示各${regionLabel.value}销售盒数按月变化趋势${mergeStr}`
  }
  return `时间维度：柱状图展示${mergeMonths.value ? '合计' : '各月'}销售盒数，按地域分组对比${mergeStr}`
})

// ---------- 数据转换 ----------
function tablesToLineCharts(tables: BoxCountTable[]): ChartData[] {
  return tables.map(t => {
    const categories = t.col_keys
    const series = t.row_keys.map(rk => {
      const row = t.rows.find(r => r.row_key === rk)
      return {
        name: rk,
        data: t.col_keys.map(ck => row?.cells[ck] ?? 0),
      }
    })
    return {
      title: t.title,
      meta: `${t.row_keys.length} ${t.row_header} × ${t.col_keys.length} ${t.col_header}`,
      type: 'line' as const,
      categories,
      series,
    }
  })
}

function tablesToBarCharts(tables: BoxCountTable[]): ChartData[] {
  // 时间维度：每个 table 是一个月（split_value=月份），row=地域，col=品类
  if (tables.length === 0) return []

  const firstTable = tables[0]!

  // 合并月时：单表，row=地域，col=品类，展示 地域×品类 分组柱状图
  if (mergeMonths.value) {
    const t = firstTable
    const categories = t.row_keys
    const series = t.col_keys.map(ck => ({
      name: ck,
      data: t.row_keys.map(rk => {
        const row = t.rows.find(r => r.row_key === rk)
        return row?.cells[ck] ?? 0
      }),
    }))
    return [{
      title: t.title,
      meta: `${t.row_keys.length} ${t.row_header} × ${t.col_keys.length} ${t.col_header}`,
      type: 'bar' as const,
      categories,
      series,
    }]
  }

  // 多月模式：x=月份，每个地域一条柱（每月按地域汇总各品类合计）
  const months = tables.map(t => t.split_value || t.title)
  const regions = firstTable.row_keys

  const series = regions.map(region => {
    return {
      name: region,
      data: tables.map(t => {
        const row = t.rows.find(r => r.row_key === region)
        return t.col_keys.reduce((sum, ck) => sum + (row?.cells[ck] ?? 0), 0)
      }),
    }
  })

  return [{
    title: '月度销售盒数趋势',
    meta: `${months.length} 个月 × ${regions.length} ${firstTable.row_header}`,
    type: 'bar' as const,
    categories: months,
    series,
  }]
}

// ---------- 渲染图表 ----------
function renderCharts() {
  disposeAllCharts()
  chartRefs.length = chartDataList.value.length

  nextTick(() => {
    chartDataList.value.forEach((cd, idx) => {
      const el = chartRefs[idx]
      if (!el) return
      const chart = echarts.init(el)
      chartInstances[idx] = chart

      const isLine = cd.type === 'line'
      const colors = ['#3b6bd6', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#9b59b6', '#1abc9c', '#34495e']

      const option: echarts.EChartsOption = {
        color: colors,
        animation: true,
        animationDuration: 1200,
        animationDurationUpdate: 600,
        animationEasing: 'cubicOut',
        animationDelay: (idx: number) => idx * 80,
        tooltip: {
          trigger: isLine ? 'axis' : 'item',
          axisPointer: { type: isLine ? 'cross' : 'shadow' },
          backgroundColor: 'rgba(255,255,255,0.96)',
          borderColor: '#e4e7ed',
          borderWidth: 1,
          textStyle: { color: '#303133', fontSize: 13 },
          extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 8px;',
          formatter: (params: any) => {
            if (isLine) {
              let html = `<div style="font-weight:600;margin-bottom:6px">${params[0].axisValue}</div>`
              params.forEach((p: any) => {
                html += `<div style="display:flex;align-items:center;gap:6px;line-height:1.8">
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color}"></span>
                  <span>${p.seriesName}</span>
                  <span style="font-weight:600;margin-left:auto">${p.value?.toLocaleString()} 盒</span>
                </div>`
              })
              return html
            }
            const p = Array.isArray(params) ? params[0] : params
            return `<div style="font-weight:600">${p.name}</div>
              <div style="margin-top:4px">${p.seriesName}：<b>${p.value?.toLocaleString()}</b> 盒</div>`
          },
        },
        legend: {
          show: cd.series.length > 1,
          top: 6,
          type: 'scroll',
          textStyle: { fontSize: 12, color: '#606266' },
          itemWidth: 14,
          itemHeight: 10,
          itemGap: 16,
        },
        grid: {
          left: 60,
          right: 28,
          bottom: 50,
          top: cd.series.length > 1 ? 46 : 24,
          containLabel: true,
        },
        xAxis: {
          type: 'category',
          data: cd.categories,
          axisLine: { lineStyle: { color: '#dcdfe6' } },
          axisTick: { show: false },
          axisLabel: { color: '#909399', fontSize: 11, rotate: cd.categories.length > 8 ? 35 : 0 },
        },
        yAxis: {
          type: 'value',
          name: '盒数',
          nameTextStyle: { color: '#909399', fontSize: 11, padding: [0, 0, 4, -20] },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: '#f0f2f5', type: 'dashed' } },
          axisLabel: {
            color: '#909399',
            fontSize: 11,
            formatter: (v: number) => v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v),
          },
        },
        series: cd.series.map((s, i) => {
          if (isLine) {
            return {
              name: s.name,
              type: 'line',
              data: s.data,
              smooth: true,
              symbol: 'circle',
              symbolSize: 7,
              lineStyle: { width: 2.5 },
              itemStyle: { borderWidth: 2 },
              emphasis: { focus: 'series', scale: 1.4 },
              label: {
                show: true,
                position: 'top',
                fontSize: 10,
                color: '#606266',
                fontWeight: 600,
                formatter: (p: any) => p.value > 0 ? p.value.toLocaleString() : '',
              },
              animationDelay: i * 100,
            }
          }
          return {
            name: s.name,
            type: 'bar',
            data: s.data,
            barMaxWidth: 36,
            barCategoryGap: '30%',
            itemStyle: {
              borderRadius: [4, 4, 0, 0],
            },
            emphasis: { focus: 'series' },
            label: {
              show: true,
              position: 'top',
              fontSize: 10,
              color: '#606266',
              fontWeight: 600,
              formatter: (p: any) => p.value > 0 ? p.value.toLocaleString() : '',
            },
            animationDelay: i * 80,
          }
        }),
      }

      chart.setOption(option)
    })
  })
}

// ---------- 事件处理 ----------
function switchDimension(d: StoreCountDimension) {
  if (dimension.value === d) return
  dimension.value = d
  // 切换维度时清理不再适用的合并选项
  if (d !== 'city') mergeCities.value = false
  if (d !== 'product') mergeProducts.value = false
  if (d !== 'time') mergeMonths.value = false
  tables.value = []
  chartDataList.value = []
  disposeAllCharts()
}

function onRegionChange() {
  if (regionLevel.value === 'city') {
    provinces.value = ''
  } else {
    cities.value = ''
  }
  tables.value = []
  chartDataList.value = []
  disposeAllCharts()
}

function onMergeChange() {
  tables.value = []
  chartDataList.value = []
  disposeAllCharts()
}

async function handleQuery() {
  loading.value = true
  try {
    const res = await fetchBoxCount(props.dbKey, {
      dimension: dimension.value,
      region_level: regionLevel.value,
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      merge_months: mergeMonths.value,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
      products: products.value || undefined,
      merge_cities: mergeCities.value,
      merge_products: mergeProducts.value,
      map_names: mapNames.value,
    })
    tables.value = res.tables
    if (res.tables.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
      chartDataList.value = []
      return
    }

    // 转换数据
    if (dimension.value === 'time') {
      chartDataList.value = tablesToBarCharts(res.tables)
    } else {
      chartDataList.value = tablesToLineCharts(res.tables)
    }

    await nextTick()
    renderCharts()
  } catch (e: any) {
    ElMessage.error('图表生成失败: ' + e.message)
    logger.error('销售趋势分析失败: ' + e.message, 'SalesTrendStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dimension.value = 'city'
  regionLevel.value = 'city'
  dateRange.value = [defaultStart, defaultEnd]
  mergeMonths.value = false
  mergeCities.value = false
  mergeProducts.value = false
  cities.value = ''
  provinces.value = ''
  products.value = ''
  tables.value = []
  chartDataList.value = []
  disposeAllCharts()
}
</script>

<style scoped>
.sales-trend-stats {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

/* ---------- 维度切换 ---------- */
.dimension-tabs {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.dim-tab {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.dim-tab:hover {
  border-color: #c0c4cc;
}

.dim-tab.active {
  border-color: #3b6bd6;
  box-shadow: 0 0 0 1px #3b6bd6 inset;
  background: linear-gradient(135deg, #f0f5ff 0%, #ffffff 100%);
}

.dim-icon {
  font-size: 22px;
  color: #909399;
  flex-shrink: 0;
}

.dim-tab.active .dim-icon {
  color: #3b6bd6;
}

.dim-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.dim-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.dim-tab.active .dim-name {
  color: #3b6bd6;
}

.dim-desc {
  font-size: 11px;
  color: #909399;
  line-height: 1.3;
}

/* ---------- 筛选区 ---------- */
.filter-section {
  background: #fff;
  border-radius: 8px;
  padding: 18px 22px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  flex-shrink: 0;
}

.filter-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 24px;
  align-items: flex-end;
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-label {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  letter-spacing: 0.3px;
}

.filter-inline {
  display: flex;
  align-items: center;
  gap: 12px;
}

.merge-checkbox {
  white-space: nowrap;
  font-size: 12px;
  color: #606266;
}

.region-switch {
  flex-shrink: 0;
}

.filter-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 8px;
  margin-left: auto;
}

.hint-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 12px;
  background: linear-gradient(90deg, #f0f9ff 0%, #f5f7fa 100%);
  border-radius: 6px;
  font-size: 12px;
  color: #3b6bd6;
}

.map-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 2px;
  white-space: nowrap;
}

.map-toggle-label {
  font-size: 12px;
  color: #606266;
}

/* ---------- 结果区 ---------- */
.results-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.results-summary {
  margin-bottom: 10px;
  flex-shrink: 0;
}

.summary-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 14px;
  background: linear-gradient(135deg, #3b6bd6 0%, #5b8def 100%);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  border-radius: 20px;
  box-shadow: 0 2px 8px rgba(59, 107, 214, 0.3);
}

.charts-scroll-area {
  flex: 1;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
}

/* ---------- 图表卡片（带入场动画） ---------- */
.chart-card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  overflow: hidden;
  flex-shrink: 0;
  animation: chartFadeIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
  border: 1px solid #f0f2f5;
}

@keyframes chartFadeIn {
  from {
    opacity: 0;
    transform: translateY(24px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f0fe 100%);
  border-bottom: 1px solid #e4e7ed;
}

.chart-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.chart-title-icon {
  font-size: 16px;
  color: #3b6bd6;
}

.chart-meta {
  font-size: 12px;
  color: #909399;
  background: rgba(255, 255, 255, 0.7);
  padding: 2px 10px;
  border-radius: 12px;
}

.chart-body {
  padding: 16px 12px 8px;
}

.chart-canvas {
  width: 100%;
  height: 340px;
}

/* ---------- 空状态 ---------- */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  gap: 8px;
}

.empty-icon-wrapper {
  width: 88px;
  height: 88px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f0fe 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
  animation: emptyPulse 2s ease-in-out infinite;
}

@keyframes emptyPulse {
  0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(59, 107, 214, 0.15); }
  50% { transform: scale(1.05); box-shadow: 0 0 0 12px rgba(59, 107, 214, 0); }
}

.empty-icon {
  font-size: 40px;
  color: #3b6bd6;
}

.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: #606266;
  margin: 0;
}

.empty-desc {
  font-size: 13px;
  color: #c0c4cc;
  margin: 0;
}
</style>
