<template>
  <div class="store-city-stats">
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
            <el-checkbox v-model="mergeMonths" class="merge-checkbox">
              合并月范围
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">城市</label>
          <el-input
            v-model="cities"
            placeholder="广州市,佛山市,汕头市"
            clearable
            size="default"
            style="width: 280px"
            @keyup.enter="handleQuery"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">商品编码</label>
          <div class="filter-inline">
            <el-input
              v-model="productCodes"
              placeholder="1058746,1086127,1091138"
              clearable
              size="default"
              style="width: 280px"
              @keyup.enter="handleQuery"
            />
            <el-checkbox
              v-if="hasMultipleProducts"
              v-model="mergeProducts"
              class="merge-checkbox"
            >
              合并统计
            </el-checkbox>
          </div>
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

      <!-- 提示信息 -->
      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span v-if="mergeMonths">当前模式：整个日期范围合并统计，跨月门店去重计数</span>
        <span v-else>当前模式：每月独立统计门店数，同一门店在不同月分别计数</span>
      </div>
    </div>

    <!-- 结果区 -->
    <div class="results-section" v-loading="loading" element-loading-text="统计中...">
      <template v-if="tables.length > 0">
        <div class="results-summary">
          共 {{ tables.length }} 张表 · {{ totalCities }} 个城市 · {{ totalTimeColumns }} 个时间段
        </div>
        <div class="tables-scroll-area">
          <div
            v-for="(table, idx) in tables"
            :key="idx"
            class="stat-table-card"
          >
            <div class="table-header">
              <span class="table-title">{{ table.title }}</span>
              <span class="table-meta">
                {{ table.cities.length }} 城市 × {{ table.time_columns.length }} 时段
              </span>
            </div>
            <div class="table-body">
              <table class="pivot-table">
                <thead>
                  <tr>
                    <th class="corner-header">城市</th>
                    <th
                      v-for="t in table.time_columns"
                      :key="t"
                      class="time-header"
                    >
                      {{ t }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in table.rows" :key="row.city">
                    <td class="city-cell">{{ row.city }}</td>
                    <td
                      v-for="t in table.time_columns"
                      :key="t"
                      class="count-cell"
                      :class="countClass(row.counts[t])"
                    >
                      {{ row.counts[t] ?? 0 }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><DataAnalysis /></el-icon>
        <p>点击「统计」按钮查看门店数统计结果</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, RefreshRight, InfoFilled, DataAnalysis } from '@element-plus/icons-vue'
import { fetchStoreCityStats, type StoreCountTable } from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'

const props = defineProps<{
  dbKey: string
}>()

const { start: defaultStart, end: defaultEnd } = getCurrentMonthRange()

// 筛选字段
const dateRange = ref<[string, string]>([defaultStart, defaultEnd])
const mergeMonths = ref(false)
const cities = ref('')
const productCodes = ref('')
const mergeProducts = ref(false)

// 结果
const tables = ref<StoreCountTable[]>([])
const loading = ref(false)

const hasMultipleProducts = computed(() => {
  const codes = productCodes.value.split(',').map((s) => s.trim()).filter(Boolean)
  return codes.length > 1
})

const totalCities = computed(() =>
  tables.value.reduce((sum, t) => sum + t.cities.length, 0),
)
const totalTimeColumns = computed(() =>
  tables.value.reduce((sum, t) => sum + t.time_columns.length, 0),
)

async function handleQuery() {
  loading.value = true
  try {
    const res = await fetchStoreCityStats(props.dbKey, {
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      merge_months: mergeMonths.value,
      cities: cities.value || undefined,
      product_codes: productCodes.value || undefined,
      merge_products: mergeProducts.value,
    })
    tables.value = res.tables
    if (res.tables.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
    }
  } catch (e: any) {
    ElMessage.error('统计失败: ' + e.message)
    logger.error('门店数统计失败: ' + e.message, 'StoreCityStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dateRange.value = [defaultStart, defaultEnd]
  mergeMonths.value = false
  cities.value = ''
  productCodes.value = ''
  mergeProducts.value = false
  tables.value = []
}

function countClass(val: number | undefined): string {
  if (!val || val === 0) return 'count-zero'
  if (val < 10) return 'count-low'
  if (val < 50) return 'count-mid'
  return 'count-high'
}
</script>

<style scoped>
.store-city-stats {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
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
  font-size: 13px;
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
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 12px;
  color: #3b6bd6;
}

/* ---------- 结果区 ---------- */
.results-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.results-summary {
  font-size: 13px;
  color: #909399;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.tables-scroll-area {
  flex: 1;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
}

.stat-table-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  flex-shrink: 0;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f0fe 100%);
  border-bottom: 1px solid #e4e7ed;
}

.table-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.table-meta {
  font-size: 12px;
  color: #909399;
}

.table-body {
  overflow: auto;
  max-height: 420px;
}

.pivot-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.pivot-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.pivot-table th {
  background: #f5f7fa;
  color: #303133;
  font-weight: 600;
  padding: 10px 16px;
  border-bottom: 2px solid #e4e7ed;
  white-space: nowrap;
  text-align: center;
}

.corner-header {
  text-align: left !important;
  min-width: 100px;
}

.time-header {
  min-width: 90px;
}

.pivot-table td {
  padding: 9px 16px;
  border-bottom: 1px solid #ebeef5;
  text-align: center;
}

.pivot-table tbody tr:hover td {
  background: #f0f5ff;
}

.city-cell {
  text-align: left !important;
  font-weight: 500;
  color: #303133;
  position: sticky;
  left: 0;
  background: #fff;
  z-index: 0;
}

.pivot-table tbody tr:hover .city-cell {
  background: #f0f5ff;
}

.count-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.count-zero {
  color: #c0c4cc;
}

.count-low {
  color: #e6a23c;
}

.count-mid {
  color: #409eff;
}

.count-high {
  color: #67c23a;
  font-weight: 700;
}

/* ---------- 空状态 ---------- */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  gap: 12px;
}

.empty-icon {
  font-size: 48px;
}

.empty-state p {
  font-size: 14px;
}
</style>
