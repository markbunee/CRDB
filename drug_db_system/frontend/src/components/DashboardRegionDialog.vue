<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="900px"
    append-to-body
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    @open="load"
  >
    <div v-loading="loading" class="region-dialog">
      <div class="rd-summary">
        <div class="rd-metric">
          <div class="rd-label">{{ metric === 'amount' ? '合计金额' : '合计销量' }}</div>
          <div class="rd-value">{{ metric === 'amount' ? formatAmount(total, amountUnit) : fmt(total) }}</div>
          <div class="rd-sub">单位：{{ metric === 'amount' ? amountUnitLabel(amountUnit) : '盒' }}</div>
        </div>
        <div class="rd-metric">
          <div class="rd-label">覆盖门店</div>
          <div class="rd-value">{{ fmt(stores) }}</div>
          <div class="rd-sub">去重门店</div>
        </div>
        <div class="rd-metric">
          <div class="rd-label">品种数</div>
          <div class="rd-value">{{ fmt(items.length) }}</div>
          <div class="rd-sub">有销量品种</div>
        </div>
      </div>

      <div class="rd-section-title">品种排行</div>
      <div ref="chartEl" class="rd-chart"></div>

      <div class="rd-section-title">品种明细（点击查看月度维度）</div>
      <div class="rd-list">
        <div
          v-for="(p, idx) in items"
          :key="p.key"
          class="rd-item"
          @click="emit('select-product', { code: String(p.key), name: displayName(p.name) })"
        >
          <div class="rd-rank">{{ idx + 1 }}</div>
          <div class="rd-name" :title="displayName(p.name)">{{ displayName(p.name) }}</div>
          <div class="rd-bar-wrap">
            <div class="rd-bar" :style="{ width: barWidth(p) }"></div>
          </div>
          <div class="rd-num">{{ metric === 'amount' ? formatAmount(p.amount, amountUnit) : fmt(p.boxes) }}</div>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import {
  getDashboardBreakdown,
  getDashboardKpi,
  type BreakdownItem,
  type DashboardFilters,
} from '@/api/client'
import { formatAmount, amountUnitLabel, type AmountUnit } from '@/utils/amount'

const props = defineProps<{
  modelValue: boolean
  dbKey: string
  title: string
  filters: DashboardFilters
  province?: string
  city?: string
  metric: 'boxes' | 'amount'
  amountUnit: AmountUnit
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  'select-product': [payload: { code: string; name: string }]
}>()

const loading = ref(false)
const items = ref<BreakdownItem[]>([])
const stores = ref(0)
const total = ref(0)
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

function fmt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function displayName(name: string): string {
  const s = String(name || '')
  return s.endsWith(')') && s.includes(' (') ? s.slice(0, s.lastIndexOf(' (')) : s
}

function regionParams(): DashboardFilters {
  const p: DashboardFilters = { ...props.filters }
  if (props.city) {
    p.cities = props.city
    if (props.province) p.provinces = props.province
  } else if (props.province) {
    p.provinces = props.province
    p.cities = ''
  }
  return p
}

function barWidth(p: BreakdownItem): string {
  const max = metricMax()
  const v = props.metric === 'amount' ? p.amount || 0 : p.boxes || 0
  if (!max) return '0%'
  return `${Math.max(2, (v / max) * 100)}%`
}

function metricMax(): number {
  return items.value.reduce(
    (m, p) => Math.max(m, props.metric === 'amount' ? p.amount || 0 : p.boxes || 0),
    0,
  )
}

async function load() {
  // destroy-on-close 会重建 DOM，重新打开时先销毁旧图表实例，避免绑定到已卸载节点
  chart?.dispose()
  chart = null
  loading.value = true
  try {
    const params = regionParams()
    const [res, kpi] = await Promise.all([
      getDashboardBreakdown(props.dbKey, 'product', 2000, params),
      getDashboardKpi(props.dbKey, params),
    ])
    items.value = res.items || []
    stores.value = Number(kpi.stores || 0)
    total.value = props.metric === 'amount'
      ? items.value.reduce((s, p) => s + (p.amount || 0), 0)
      : items.value.reduce((s, p) => s + (p.boxes || 0), 0)
    setTimeout(renderChart, 0)
  } catch (e) {
    console.error('加载地域品种明细失败', e)
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const top = [...items.value]
    .sort((a, b) => (props.metric === 'amount' ? (b.amount || 0) - (a.amount || 0) : (b.boxes || 0) - (a.boxes || 0)))
    .slice(0, 12)
    .reverse()
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 40, top: 10, bottom: 8, containLabel: true },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: top.map((p) => displayName(p.name)),
      axisLabel: { width: 150, overflow: 'truncate' },
    },
    series: [
      {
        type: 'bar',
        data: top.map((p) => (props.metric === 'amount' ? p.amount || 0 : p.boxes || 0)),
        itemStyle: { color: '#5b8def' },
      },
    ],
  })
}

onBeforeUnmount(() => {
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.region-dialog {
  min-height: 200px;
}
.rd-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.rd-metric {
  flex: 1;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px 16px;
}
.rd-label {
  font-size: 12px;
  color: #909399;
}
.rd-value {
  font-size: 26px;
  font-weight: 700;
  margin: 4px 0;
}
.rd-sub {
  font-size: 12px;
  color: #c0c4cc;
}
.rd-section-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin: 12px 0 8px;
}
.rd-chart {
  width: 100%;
  height: 320px;
}
.rd-list {
  max-height: 360px;
  overflow: auto;
}
.rd-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px solid #f2f3f5;
  cursor: pointer;
}
.rd-item:hover {
  background: #f8fafc;
}
.rd-rank {
  width: 24px;
  text-align: center;
  font-weight: 700;
  color: #e6a23c;
}
.rd-name {
  width: 240px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rd-bar-wrap {
  flex: 1;
  height: 10px;
  background: #f2f3f5;
  border-radius: 5px;
  overflow: hidden;
}
.rd-bar {
  height: 100%;
  background: linear-gradient(90deg, #8fb4f7, #5b8def);
  border-radius: 5px;
}
.rd-num {
  width: 100px;
  text-align: right;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
</style>
