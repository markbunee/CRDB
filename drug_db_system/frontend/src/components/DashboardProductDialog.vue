<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="860px"
    append-to-body
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    @open="load"
  >
    <div v-loading="loading" class="product-dialog">
      <div class="pd-summary">
        <div class="pd-metric">
          <div class="pd-label">合计销量</div>
          <div class="pd-value">{{ fmt(data?.total_boxes) }}</div>
          <div class="pd-sub">盒</div>
        </div>
        <div class="pd-metric">
          <div class="pd-label">销售金额</div>
          <div class="pd-value">{{ formatAmount(data?.total_amount, amountUnit) }}</div>
          <div class="pd-sub">单位：{{ amountUnitLabel(amountUnit) }}</div>
        </div>
        <div class="pd-metric">
          <div class="pd-label">覆盖门店</div>
          <div class="pd-value">{{ fmt(data?.total_stores) }}</div>
          <div class="pd-sub">去重门店</div>
        </div>
      </div>

      <div class="pd-section-title">月度趋势（销量 + 同比）</div>
      <div ref="chartEl" class="pd-chart"></div>

      <div class="pd-section-title">月度明细</div>
      <el-table :data="rows" size="small" max-height="320" border stripe>
        <el-table-column prop="month" label="月份" width="110" />
        <el-table-column label="销量(盒)" width="120" align="right">
          <template #default="{ row }">{{ fmt(row.boxes) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="140" align="right">
          <template #default="{ row }">{{ formatAmount(row.amount, amountUnit) }}</template>
        </el-table-column>
        <el-table-column label="同比" width="110" align="right">
          <template #default="{ row }">
            <span :class="pctClass(row.yoy)">{{ fmtPct(row.yoy) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="环比" width="110" align="right">
          <template #default="{ row }">
            <span :class="pctClass(row.mom)">{{ fmtPct(row.mom) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="门店数" min-width="100" align="right">
          <template #default="{ row }">{{ fmt(row.stores) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import {
  getDashboardProductDetail,
  type DashboardFilters,
  type DashboardProductDetail,
} from '@/api/client'
import { formatAmount, amountUnitLabel, type AmountUnit } from '@/utils/amount'

const props = defineProps<{
  modelValue: boolean
  dbKey: string
  code: string
  productName: string
  filters: DashboardFilters
  amountUnit: AmountUnit
}>()

const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const loading = ref(false)
const data = ref<DashboardProductDetail | null>(null)
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const title = computed(() => `${props.productName || props.code} · 月度明细`)

const rows = computed(() => {
  const d = data.value
  if (!d) return []
  return d.months.map((m, i) => ({
    month: m,
    boxes: d.boxes[i],
    amount: d.amount[i],
    yoy: d.yoy_pct[i],
    mom: d.mom_pct[i],
    stores: d.stores[i],
  }))
})

function fmt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function fmtPct(v: number | null): string {
  if (v == null) return '/'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

function pctClass(v: number | null): string {
  if (v == null) return 'pct-flat'
  return v >= 0 ? 'pct-up' : 'pct-down'
}

async function load() {
  // destroy-on-close 会重建 DOM，重新打开时先销毁旧图表实例
  chart?.dispose()
  chart = null
  loading.value = true
  try {
    data.value = await getDashboardProductDetail(props.dbKey, props.code, props.filters)
    setTimeout(renderChart, 0)
  } catch (e) {
    console.error('加载品种月度明细失败', e)
  } finally {
    loading.value = false
  }
}

function renderChart() {
  const d = data.value
  if (!chartEl.value || !d) return
  if (!chart) chart = echarts.init(chartEl.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['销量(盒)', '同比%'] },
    grid: { left: 8, right: 40, top: 30, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: d.months, axisLabel: { rotate: 45 } },
    yAxis: [
      { type: 'value', name: '盒' },
      { type: 'value', name: '同比%', axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      { name: '销量(盒)', type: 'bar', data: d.boxes, itemStyle: { color: '#5b8def' } },
      {
        name: '同比%',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: d.yoy_pct,
        itemStyle: { color: '#e6a23c' },
        connectNulls: true,
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
.product-dialog {
  min-height: 200px;
}
.pd-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.pd-metric {
  flex: 1;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px 16px;
}
.pd-label {
  font-size: 12px;
  color: #909399;
}
.pd-value {
  font-size: 26px;
  font-weight: 700;
  margin: 4px 0;
}
.pd-sub {
  font-size: 12px;
  color: #c0c4cc;
}
.pd-section-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin: 12px 0 8px;
}
.pd-chart {
  width: 100%;
  height: 300px;
}
.pct-up {
  color: #f56c6c;
}
.pct-down {
  color: #67c23a;
}
.pct-flat {
  color: #909399;
}
</style>
