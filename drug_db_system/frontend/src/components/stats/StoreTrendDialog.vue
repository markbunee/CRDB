<template>
  <el-dialog
    v-model="visible"
    :title="`${storeName} · 月度产出趋势`"
    width="780px"
    align-center
    destroy-on-close
  >
    <div v-loading="loading" element-loading-text="加载月度数据..." class="trend-body">
      <template v-if="months.length > 0">
        <!-- 区间摘要 -->
        <div class="trend-meta">
          <span class="meta-range">{{ dateFrom }} 至 {{ dateTo }}</span>
          <span class="meta-total">区间合计：<b>{{ fmt(totalQty) }}</b> 盒</span>
          <el-tag v-if="incompleteCount > 0" type="warning" size="small">
            {{ incompleteCount }} 个非完整月
          </el-tag>
        </div>

        <!-- 折线图 -->
        <div ref="chartEl" class="trend-chart"></div>

        <!-- 月度明细 -->
        <el-table :data="months" border stripe size="small" max-height="240">
          <el-table-column prop="month" label="月份" width="110" align="center" />
          <el-table-column label="实销盒数" align="right">
            <template #default="{ row }">
              <span class="qty-strong">{{ fmt(row.qty) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="完整性" width="110" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.complete" type="success" size="small">完整月</el-tag>
              <el-tag v-else type="warning" size="small">非完整月</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="incompleteCount > 0" class="incomplete-tip">
          <el-icon><InfoFilled /></el-icon>
          <span>
            带 * 的月份为「非完整月」：查询区间的{{ incompleteHint }}未覆盖该自然月整月，
            其盒数仅反映区间内的部分数据，对比时请留意。
          </span>
        </div>
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
import { InfoFilled, DataAnalysis } from '@element-plus/icons-vue'
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
const months = ref<StoreAbilityTrendPoint[]>([])
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const totalQty = computed(() => months.value.reduce((s, m) => s + m.qty, 0))
const incompleteCount = computed(() => months.value.filter((m) => !m.complete).length)

/** 非完整月的具体提示：只有首/尾可能不完整，按位置给出人话描述 */
const incompleteHint = computed(() => {
  if (!incompleteCount.value) return '起止日期'
  const first = months.value[0]
  const last = months.value[months.value.length - 1]
  const parts: string[] = []
  if (first && !first.complete) parts.push('开始日期')
  if (last && !last.complete) parts.push('结束日期')
  return parts.join('或')
})

function fmt(v: number): string {
  return v.toLocaleString('zh-CN')
}

async function load() {
  if (!props.storeName || !props.dateFrom || !props.dateTo) return
  loading.value = true
  months.value = []
  try {
    const res = await fetchStoreAbilityTrend(props.dbKey, {
      store_name: props.storeName,
      date_from: props.dateFrom,
      date_to: props.dateTo,
      products: props.products || undefined,
    })
    months.value = res.months
    await nextTick()
    renderChart()
  } catch (e: any) {
    ElMessage.error('加载月度趋势失败: ' + e.message)
    logger.error('门店月度趋势加载失败: ' + e.message, 'StoreTrendDialog')
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
  if (!chartEl.value || months.value.length === 0) return
  chart = echarts.init(chartEl.value)

  // 非完整月在横轴标签上追加 *，与下方提示呼应
  const categories = months.value.map((m) => (m.complete ? m.month : `${m.month} *`))
  const completeFlags = months.value.map((m) => m.complete)

  const option: echarts.EChartsOption = {
    color: ['#3b6bd6'],
    animationDuration: 800,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#e4e7ed',
      borderWidth: 1,
      textStyle: { color: '#303133', fontSize: 13 },
      extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 8px;',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        const idx = p.dataIndex as number
        const flag = completeFlags[idx]
          ? '<span style="color:#67c23a">完整月</span>'
          : '<span style="color:#e6a23c">非完整月（区间未覆盖整月）</span>'
        return `<div style="font-weight:600">${p.name.replace(' *', '')}</div>
          <div style="margin-top:4px">实销盒数：<b>${(p.value ?? 0).toLocaleString()}</b></div>
          <div style="margin-top:2px;font-size:12px">${flag}</div>`
      },
    },
    grid: { left: 60, right: 28, bottom: 40, top: 30, containLabel: true },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#909399',
        fontSize: 11,
        rotate: categories.length > 8 ? 35 : 0,
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
        type: 'line',
        data: months.value.map((m) => m.qty),
        smooth: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { width: 2.5 },
        itemStyle: { borderWidth: 2 },
        emphasis: { focus: 'series', scale: 1.4 },
        label: {
          show: true,
          position: 'top',
          fontSize: 10,
          color: '#606266',
          fontWeight: 600,
          formatter: (p: any) => (p.value > 0 ? p.value.toLocaleString() : ''),
        },
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
  gap: 16px;
  font-size: 13px;
  color: #606266;
}

.meta-total b {
  color: #303133;
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

.incomplete-tip {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px;
  background: #fff7ed;
  border-radius: 6px;
  font-size: 12px;
  color: #b45309;
  line-height: 1.5;
}

.incomplete-tip .el-icon {
  margin-top: 2px;
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
