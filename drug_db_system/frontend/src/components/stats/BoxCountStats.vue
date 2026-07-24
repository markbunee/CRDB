<template>
  <div class="store-city-stats">
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
            <el-checkbox v-model="mergeMonths" class="merge-checkbox">
              {{ mergeMonthsLabel }}
            </el-checkbox>
            <el-checkbox v-model="calcYoyMom" class="merge-checkbox yoy-checkbox">
              求同比环比
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
            <el-checkbox
              v-model="mergeCities"
              class="merge-checkbox"
            >
              合并{{ regionLabel }}
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
            <el-checkbox
              v-model="mergeProducts"
              class="merge-checkbox"
            >
              合并品类
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
          <el-button type="success" :loading="exporting" @click="handleExport">
            <el-icon><Download /></el-icon> 导出Excel
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>{{ hintText }}</span>
      </div>

      <!-- 同比环比说明条 -->
      <div v-if="calcYoyMom && yoyRange" class="yoy-mom-info">
        <div class="yoy-mom-item">
          <span class="yoy-label">当前期</span>
          <span class="yoy-value">{{ dateRange?.[0] }} ~ {{ dateRange?.[1] }}</span>
        </div>
        <div class="yoy-mom-item">
          <span class="yoy-label yoy-color">同比期</span>
          <span class="yoy-value">{{ yoyRange.date_from }} ~ {{ yoyRange.date_to }}</span>
        </div>
        <div class="yoy-mom-item">
          <span class="yoy-label mom-color">环比期</span>
          <span class="yoy-value">{{ momRange!.date_from }} ~ {{ momRange!.date_to }}</span>
        </div>
      </div>
    </div>

    <!-- 结果区 -->
    <div class="results-section" v-loading="loading" element-loading-text="统计中...">
      <template v-if="tables.length > 0">
        <div class="results-summary">
          共 {{ tables.length }} 张表 · {{ totalRows }} 行
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
                {{ table.row_keys.length }} {{ table.row_header }} × {{ table.col_keys.length }} {{ table.col_header }}
              </span>
            </div>
            <div class="table-body">
              <table class="pivot-table">
                <thead>
                  <tr>
                    <th class="corner-header">{{ table.row_header }}</th>
                    <th
                      v-for="c in table.col_keys"
                      :key="c"
                      class="time-header"
                    >
                      {{ c }}
                    </th>
                    <template v-if="calcYoyMom">
                      <th class="total-header">合计(盒)</th>
                      <th class="yoy-header">同期合计(盒)</th>
                      <th class="yoy-header">同比(%)</th>
                      <th class="mom-header">上月合计(盒)</th>
                      <th class="mom-header">环比(%)</th>
                    </template>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in table.rows" :key="row.row_key">
                    <td class="row-cell">{{ row.row_key }}</td>
                    <td
                      v-for="c in table.col_keys"
                      :key="c"
                      class="count-cell"
                      :class="countClass(row.cells[c])"
                    >
                      {{ row.cells[c] ?? 0 }}
                    </td>
                    <template v-if="calcYoyMom">
                      <td class="total-cell">{{ row.total ?? 0 }}</td>
                      <td class="total-cell">{{ row.yoy_total ?? 0 }}</td>
                      <td class="pct-cell" :class="pctClass(row.yoy_pct)">{{ fmtPct(row.yoy_pct) }}</td>
                      <td class="total-cell">{{ row.mom_total ?? 0 }}</td>
                      <td class="pct-cell" :class="pctClass(row.mom_pct)">{{ fmtPct(row.mom_pct) }}</td>
                    </template>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><Box /></el-icon>
        <p>点击「统计」按钮查看实销盒数统计结果</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  RefreshRight,
  InfoFilled,
  Box,
  Location,
  Goods,
  Calendar,
  Download,
} from '@element-plus/icons-vue'
import {
  fetchBoxCount,
  exportBoxCount,
  downloadBlob,
  type BoxCountTable,
  type StoreCountDimension,
  type StoreCountRegion,
  type DbMeta,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'
import { getDbConfig } from '@/utils/dbConfig'

const props = defineProps<{
  dbKey: string
}>()

const { start: defaultStart, end: defaultEnd } = getCurrentMonthRange()

const dimensions = [
  { key: 'city' as StoreCountDimension, name: '城市维度', desc: '按城市/省份拆分 · 品类×月份', icon: Location },
  { key: 'product' as StoreCountDimension, name: '品类维度', desc: '按品类拆分 · 地域×月份', icon: Goods },
  { key: 'time' as StoreCountDimension, name: '时间维度', desc: '按月份拆分 · 地域×品类', icon: Calendar },
]

const dimension = ref<StoreCountDimension>('city')
const regionLevel = ref<StoreCountRegion>('city')
const dateRange = ref<[string, string]>([defaultStart, defaultEnd])
const mergeMonths = ref(false)
const cities = ref('')
const provinces = ref('')
const products = ref('')
const mergeCities = ref(false)
const mergeProducts = ref(false)

const tables = ref<BoxCountTable[]>([])
const loading = ref(false)
const exporting = ref(false)
const calcYoyMom = ref(false)
const yoyRange = ref<{ date_from: string; date_to: string } | null>(null)
const momRange = ref<{ date_from: string; date_to: string } | null>(null)

const regionLabel = computed(() =>
  regionLevel.value === 'province' && hasProvince.value ? '省份' : (dbConfig.value?.region_label || '城市'),
)

const dbConfig = ref<DbMeta | null>(null)
const hasProvince = computed(() => dbConfig.value?.region_levels?.includes('province') ?? true)

onMounted(async () => {
  dbConfig.value = await getDbConfig(props.dbKey)
})

const mergeMonthsLabel = computed(() =>
  dimension.value === 'time' ? '合并为单表' : '合并月范围',
)

const hintText = computed(() => {
  const d = dimension.value
  const parts: string[] = []
  if (d === 'city') {
    parts.push(mergeCities.value ? `全部${regionLabel.value}合并为一张表` : `每个${regionLabel.value}单独一张表`)
  } else if (d === 'product') {
    parts.push(mergeProducts.value ? '全部品类合并为一张表' : '每个品类单独一张表')
  } else {
    parts.push(mergeMonths.value ? '全部月份合并为一张表' : '每月单独一张表')
  }
  if (d === 'time') {
    parts.push(`行=${regionLabel.value}，列=品类`)
  } else if (d === 'city') {
    parts.push(`行=品类，列=${mergeMonths.value ? '月份区间' : '月份'}`)
  } else {
    parts.push(`行=${regionLabel.value}，列=${mergeMonths.value ? '月份区间' : '月份'}`)
  }
  const mergeHints: string[] = []
  if (mergeMonths.value) mergeHints.push('时间')
  if (mergeCities.value) mergeHints.push(regionLabel.value)
  if (mergeProducts.value) mergeHints.push('品类')
  if (mergeHints.length) {
    parts.push(`已合并：${mergeHints.join('、')}`)
  }
  return '当前模式：' + parts.join('，')
})

const totalRows = computed(() =>
  tables.value.reduce((sum, t) => sum + t.row_keys.length, 0),
)

function switchDimension(d: StoreCountDimension) {
  if (dimension.value === d) return
  dimension.value = d
  mergeCities.value = false
  mergeProducts.value = false
  mergeMonths.value = false
  tables.value = []
}

function onRegionChange() {
  if (regionLevel.value === 'city') {
    provinces.value = ''
  } else {
    cities.value = ''
  }
  tables.value = []
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
      calc_yoy_mom: calcYoyMom.value,
    })
    tables.value = res.tables
    yoyRange.value = res.yoy_range || null
    momRange.value = res.mom_range || null
    if (res.tables.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
    }
  } catch (e: any) {
    ElMessage.error('统计失败: ' + e.message)
    logger.error('实销盒数统计失败: ' + e.message, 'BoxCountStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dimension.value = 'city'
  regionLevel.value = 'city'
  dateRange.value = [defaultStart, defaultEnd]
  mergeMonths.value = false
  cities.value = ''
  provinces.value = ''
  products.value = ''
  mergeCities.value = false
  mergeProducts.value = false
  calcYoyMom.value = false
  yoyRange.value = null
  momRange.value = null
  tables.value = []
}

async function handleExport() {
  if (tables.value.length === 0) {
    ElMessage.warning('请先执行统计后再导出')
    return
  }
  exporting.value = true
  try {
    const blob = await exportBoxCount(props.dbKey, {
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
      calc_yoy_mom: calcYoyMom.value,
    })
    const suffix = calcYoyMom.value ? '_yoy_mom' : ''
    const filename = `box_count_${dimension.value}_${regionLevel.value}${suffix}.xlsx`
    downloadBlob(blob, filename)
    ElMessage.success('导出完成')
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e.message)
    logger.error('盒数导出失败: ' + e.message, 'BoxCountStats')
  } finally {
    exporting.value = false
  }
}

function countClass(val: number | undefined): string {
  if (!val || val === 0) return 'count-zero'
  if (val < 100) return 'count-low'
  if (val < 1000) return 'count-mid'
  return 'count-high'
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return '—'
  const sign = val > 0 ? '+' : ''
  return sign + val.toFixed(2) + '%'
}

function pctClass(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'pct-na'
  if (val > 0) return 'pct-up'
  if (val < 0) return 'pct-down'
  return 'pct-flat'
}
</script>

<style scoped>
.store-city-stats {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

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

.region-switch {
  flex-shrink: 0;
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

.row-cell {
  text-align: left !important;
  font-weight: 500;
  color: #303133;
  position: sticky;
  left: 0;
  background: #fff;
  z-index: 0;
}

.pivot-table tbody tr:hover .row-cell {
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

/* ---------- 同比环比 ---------- */
.yoy-checkbox {
  color: #e6a23c;
  font-weight: 600;
}

.yoy-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: #e6a23c;
  border-color: #e6a23c;
}

.yoy-checkbox :deep(.el-checkbox__input.is-checked + .el-checkbox__label) {
  color: #e6a23c;
}

.yoy-mom-info {
  display: flex;
  gap: 20px;
  margin-top: 10px;
  padding: 10px 16px;
  background: linear-gradient(90deg, #fdf6ec 0%, #f0f9ff 50%, #fef0f0 100%);
  border-radius: 6px;
  flex-wrap: wrap;
}

.yoy-mom-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.yoy-label {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: #3b6bd6;
  color: #fff;
}

.yoy-label.yoy-color {
  background: #e6a23c;
}

.yoy-label.mom-color {
  background: #f56c6c;
}

.yoy-value {
  color: #606266;
  font-variant-numeric: tabular-nums;
}

.total-header {
  background: #ecf5ff !important;
  color: #3b6bd6 !important;
  border-left: 2px solid #d0e3ff;
}

.yoy-header {
  background: #fdf6ec !important;
  color: #e6a23c !important;
  border-left: 1px solid #faecd8;
}

.mom-header {
  background: #fef0f0 !important;
  color: #f56c6c !important;
  border-left: 1px solid #fde2e2;
}

.total-cell {
  font-weight: 600;
  color: #3b6bd6;
  background: #f8fbff;
  font-variant-numeric: tabular-nums;
}

.pct-cell {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
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

.pct-na {
  color: #c0c4cc;
}
</style>
