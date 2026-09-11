<template>
  <el-dialog
    v-model="visible"
    :title="`${storeName} · 每日产出趋势`"
    width="860px"
    align-center
    destroy-on-close
  >
    <div v-loading="loading" element-loading-text="加载每日数据..." class="trend-body">
      <template v-if="days.length > 0">
        <!-- 区间摘要 -->
        <div class="trend-meta">
          <span class="meta-range">{{ dateFrom }} 至 {{ dateTo }}</span>
          <span class="meta-total">区间合计：<b>{{ fmt(totalQty) }}</b> 盒</span>
          <span class="meta-days">共 {{ days.length }} 天</span>
          <span class="meta-avg">日均 {{ fmt(avgQty) }} 盒</span>
        </div>

        <!-- 细柱状图：柱子很细，整体形成趋势轮廓；鼠标悬停看数值 -->
        <div ref="chartEl" class="trend-chart"></div>

        <!-- 每日明细 -->
        <el-table :data="days" border stripe size="small" height="240">
          <el-table-column prop="date" label="日期" width="130" align="center" />
          <el-table-column label="实销盒数" align="right">
            <template #default="{ row }">
              <span class="qty-strong">{{ fmt(row.qty) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <div v-else-if="!loading" class="trend-empty">
        <el-icon class="empty-icon"><DataAnalysis /></el-icon>
        <p>该时间范围内没有此门店的销量数据</p>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { DataAnalysis } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { fetchStoreAbilityTrend, type StoreAbilityTrendPoint } from '@/api/client'
import { logger } from '@/utils/logger'

const props = defineProps<{
  modelValue: boolean
  dbKey: string
  storeName: string
  dateFrom: string
  dateTo: string
  /** 品类筛选（与主表一致），传空表示全部品类 */
  products?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const days = ref<StoreAbilityTrendPoint[]>([])
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const totalQty = computed(() => days.value.reduce((s, d) => s + d.qty, 0))
const avgQty = computed(() =>
  days.value.length ? Math.round(totalQty.value / days.value.length) : 0,
)

function fmt(v: number): string {
  return v.toLocaleString('zh-CN')
}

async function load() {
  if (!props.storeName || !props.dateFrom || !props.dateTo) return
  loading.value = true
  days.value = []
  try {
    const res = await fetchStoreAbilityTrend(props.dbKey, {
      store_name: props.storeName,
      date_from: props.dateFrom,
      date_to: props.dateTo,
      products: props.products || undefined,
    })
    days.value = res.days
    await nextTick()
    renderChart()
  } catch (e: any) {
    ElMessage.error('加载每日趋势失败: ' + e.message)
    logger.error('门店每日趋势加载失败: ' + e.message, 'StoreTrendDialog')
  } finally {
    loading.value = false
  }
}

// ---------- 图表（与 SalesTrendStats 保持同一套 ECharts 用法） ----------
function disposeChart() {
  if (chart) {
    try { chart.dispose() } catch (_) { /* noop */ }
    chart = null
  }
}

function renderChart() {
  disposeChart()
  if (!chartEl.value || days.value.length === 0) return
  chart = echarts.init(chartEl.value)

  const categories = days.value.map((d) => d.date)

  const option: echarts.EChartsOption = {
    color: ['#3b6bd6'],
    animationDuration: 800,
    animationEasing: 'cubicOut',
    tooltip: {
      // axis 触发：柱子很细也能稳定命中，鼠标移到该列任意位置即显示
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#e4e7ed',
      borderWidth: 1,
      textStyle: { color: '#303133', fontSize: 13 },
      extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 8px;',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `<div style="font-weight:600">${p.name}</div>
          <div style="margin-top:4px">实销盒数：<b>${(p.value ?? 0).toLocaleString()}</b></div>`
      },
    },
    grid: { left: 60, right: 24, bottom: 46, top: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#909399',
        fontSize: 11,
        // 天数多时自动抽稀 + 旋转，避免标签重叠
        interval: 'auto',
        hideOverlap: true,
        rotate: categories.length > 20 ? 45 : 0,
      },
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
        formatter: (v: number) => (v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)),
      },
    },
    series: [
      {
        name: '实销盒数',
        type: 'bar',
        data: days.value.map((d) => d.qty),
        // 核心：柱子做细，点多了整体就是一条趋势轮廓
        barMinWidth: 1,
        barMaxWidth: 8,
        barCategoryGap: '20%',
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        emphasis: { focus: 'series' },
        // 不显示常驻数值标签：靠 tooltip 看值，避免天数多时糊成一片
      },
    ],
  }

  chart.setOption(option)
}

function handleResize() {
  try { chart?.resize() } catch (_) { /* noop */ }
}

watch(visible, (v) => {
  if (v) {
    load()
    window.addEventListener('resize', handleResize)
  } else {
    window.removeEventListener('resize', handleResize)
    disposeChart()
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  disposeChart()
})
</script>

<style scoped>
.trend-body {
  min-height: 200px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.trend-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: #606266;
}

.meta-total b {
  color: #303133;
  font-variant-numeric: tabular-nums;
}

.meta-days,
.meta-avg {
  font-variant-numeric: tabular-nums;
}

.trend-chart {
  width: 100%;
  height: 300px;
  flex-shrink: 0;
}

.qty-strong {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.trend-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  gap: 10px;
  padding: 40px 0;
}

.empty-icon {
  font-size: 44px;
}

.trend-empty p {
  font-size: 14px;
  margin: 0;
}
</style>
