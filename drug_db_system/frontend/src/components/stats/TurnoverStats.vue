<template>
  <div class="turnover-stats">
    <div class="filter-section">
      <div class="filter-grid">
        <div class="filter-group">
          <label class="filter-label">销售区间（实销门店数口径）</label>
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
        </div>

        <div class="filter-group">
          <label class="filter-label">库存截至日期</label>
          <el-date-picker
            v-model="invDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="留空=最新日期"
            size="default"
            style="width: 190px"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">地域</label>
          <div class="filter-inline">
            <el-radio-group
              v-model="regionLevel"
              size="small"
              @change="onRegionChange"
            >
              <el-radio-button label="city">城市</el-radio-button>
              <el-radio-button label="province">省份</el-radio-button>
            </el-radio-group>
            <KeywordSelect
              v-if="regionLevel === 'city'"
              v-model="cities"
              :db-key="dbKey"
              field="city"
              placeholder="输入城市关键词"
              width="220px"
            />
            <KeywordSelect
              v-else
              v-model="provinces"
              :db-key="dbKey"
              field="province"
              placeholder="输入省份关键词"
              width="220px"
            />
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">品类（商品编码）</label>
          <KeywordSelect
            v-model="products"
            :db-key="dbKey"
            field="product"
            placeholder="输入编码或名称关键词"
            width="280px"
          />
        </div>

        <div class="filter-group filter-actions">
          <el-button type="primary" :loading="loading" @click="handleQuery">
            <el-icon><Search /></el-icon> 统计
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>
          动销率 = 实销门店数（非重复计数）÷ 库存门店数（非重复计数）× 100% ·
          分子取销售表在所选区间的去重门店数，分母取库存表在所选截至日期的去重门店数 ·
          库存门店数为 0 时显示「/」
        </span>
      </div>
    </div>

    <div class="results-section" v-loading="loading" element-loading-text="统计中...">
      <template v-if="result">
        <div class="summary-cards">
          <div class="sum-card">
            <div class="sum-label">实销门店数</div>
            <div class="sum-value">{{ fmt(result.sales_stores) }}</div>
            <div class="sum-sub">区间内非重复门店</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">库存门店数</div>
            <div class="sum-value">{{ fmt(result.inventory_stores) }}</div>
            <div class="sum-sub">截至 {{ result.inv_date || '—' }} 非重复门店</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">动销率</div>
            <div class="sum-value" :class="rateClass(result.turnover_rate)">
              {{ fmtRate(result.turnover_rate) }}
            </div>
            <div class="sum-sub">实销门店数 ÷ 库存门店数</div>
          </div>
        </div>

        <div class="detail-title">分城市明细（按动销率降序）</div>
        <el-table :data="result.by_city" border size="small" max-height="420">
          <el-table-column prop="city" label="城市" min-width="140" show-overflow-tooltip />
          <el-table-column prop="sales_stores" label="实销门店数" width="130" align="right" sortable />
          <el-table-column prop="inventory_stores" label="库存门店数" width="130" align="right" sortable />
          <el-table-column label="动销率" width="130" align="right">
            <template #default="{ row }">
              <span :class="rateClass(row.turnover_rate)">{{ fmtRate(row.turnover_rate) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><DataAnalysis /></el-icon>
        <p>点击「统计」按钮查看动销率</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 动销率：实销门店数（非重复计数）÷ 库存门店数（非重复计数）。
 * 分子来自销售表（所选销售区间），分母来自库存表（所选库存截至日期）。
 */
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  RefreshRight,
  InfoFilled,
  DataAnalysis,
} from '@element-plus/icons-vue'
import {
  fetchInventoryTurnover,
  fetchStatsLatestRange,
  fetchInventoryLatest,
  type TurnoverResponse,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'
import KeywordSelect from '../KeywordSelect.vue'

const props = defineProps<{ dbKey: string; storeType?: 'all' | 'chain' | 'franchise' }>()

const { start: monthStart, end: monthEnd } = getCurrentMonthRange()
const defaultStart = ref(monthStart)
const defaultEnd = ref(monthEnd)

const dateRange = ref<[string, string]>([defaultStart.value, defaultEnd.value])
const invDate = ref('')
const regionLevel = ref<'city' | 'province'>('city')
const cities = ref('')
const provinces = ref('')
const products = ref('')

const loading = ref(false)
const result = ref<TurnoverResponse | null>(null)

function onRegionChange() {
  if (regionLevel.value === 'city') provinces.value = ''
  else cities.value = ''
}

async function handleQuery() {
  loading.value = true
  try {
    result.value = await fetchInventoryTurnover(props.dbKey, {
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      inv_date: invDate.value || undefined,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
      products: products.value || undefined,
      store_type: props.storeType || 'all',
    })
  } catch (e: any) {
    ElMessage.error('动销率统计失败: ' + e.message)
    logger.error('动销率统计失败: ' + e.message, 'TurnoverStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dateRange.value = [defaultStart.value, defaultEnd.value]
  invDate.value = ''
  regionLevel.value = 'city'
  cities.value = ''
  provinces.value = ''
  products.value = ''
  result.value = null
}

/** 默认区间对齐「数据最新月份」（数据常滞后于系统当月），库存日期取库存最新日期 */
async function applyDefaults() {
  try {
    const r = await fetchStatsLatestRange(props.dbKey)
    if (r?.date_from && r?.date_to) {
      defaultStart.value = r.date_from
      defaultEnd.value = r.date_to
      dateRange.value = [r.date_from, r.date_to]
    }
  } catch (e: any) {
    logger.warn('取数据最新月份失败，回退系统当月: ' + e.message, 'TurnoverStats')
  }
  try {
    const inv = await fetchInventoryLatest(props.dbKey)
    if (inv?.date) invDate.value = inv.date
  } catch (e: any) {
    logger.warn('取库存最新日期失败: ' + e.message, 'TurnoverStats')
  }
}

function fmt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString('zh-CN')
}

/** 基期为 0 时显示斜杠 */
function fmtRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '/'
  return rate.toFixed(2) + '%'
}

function rateClass(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return 'rate-na'
  if (rate >= 80) return 'rate-good'
  if (rate >= 50) return 'rate-mid'
  return 'rate-low'
}

onMounted(applyDefaults)
watch(() => props.dbKey, applyDefaults)
watch(
  () => props.storeType,
  () => {
    if (result.value) handleQuery()
  },
)

/** 供父组件（动销率库存情况合并视图）在库存导入后刷新 */
defineExpose({ refresh: applyDefaults })
</script>

<style scoped>
.turnover-stats {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}
.filter-section {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fafbfc;
}
.filter-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 14px 20px;
}
.filter-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.filter-inline {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 12px;
  color: #909399;
}
.filter-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 8px;
}
.hint-bar {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.results-section {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.summary-cards {
  display: flex;
  gap: 14px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.sum-card {
  flex: 1;
  min-width: 180px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fff;
}
.sum-label {
  font-size: 12px;
  color: #909399;
}
.sum-value {
  font-size: 24px;
  font-weight: 700;
  margin: 6px 0 4px;
}
.sum-sub {
  font-size: 12px;
  color: #c0c4cc;
}
.detail-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.empty-state {
  text-align: center;
  color: #c0c4cc;
  padding: 48px 0;
}
.empty-icon {
  font-size: 40px;
}
.rate-good {
  color: #67c23a;
}
.rate-mid {
  color: #e6a23c;
}
.rate-low {
  color: #f56c6c;
}
.rate-na {
  color: #c0c4cc;
}
</style>
